"""
大模型服务 - 调用 LLM 进行实时策略分析
支持 OpenAI API 格式，可替换为任意兼容的模型服务

重构版（2026-04-17）：
  - 移除固定格式报告模板
  - 三个专家真正调用专业skill进行分析
  - 自由格式输出，不限制报告结构
  - 分析结果可下载保存
"""

import os
import json
import re
import uuid
import base64
from datetime import datetime
from typing import Dict, List, Any, Optional

# ── 委托给 agent_router（统一混合路由）─────────────────────────────────────
from services.agent_router import (
    router, call_glm_with_ds, check_router_status,
)
# 向后兼容别名
call_llm = call_glm_with_ds
from services.agent_skills import registry
from services.agent_orchestrator import run_agent_analysis

# ── 向后兼容：保留原配置变量 ────────────────────────────────────────────────
LLM_API_KEY = os.environ.get('LLM_API_KEY', '')
LLM_API_URL = os.environ.get('LLM_API_URL', 'https://api.openai.com/v1/chat/completions')
LLM_MODEL   = os.environ.get('LLM_MODEL', 'gpt-3.5-turbo')


# ════════════════════════════════════════════════════════════════════════════════
# 业务场景配置
# ════════════════════════════════════════════════════════════════════════════════

def _build_biz_context(biz_scenario: str, biz_country: str, biz_module: str) -> Dict[str, Any]:
    """
    构建业务场景上下文（用于传递给专家的Prompt）
    
    Args:
        biz_scenario: first_loan / repeat_loan
        biz_country: india / indonesia / philippines
        biz_module: model / rule
    """
    # 国家映射
    country_map = {
        'india': {'name': '印度', 'flag': '🇮🇳', 'apr': '800%+'},
        'indonesia': {'name': '印尼', 'flag': '🇮🇩', 'apr': '800%+'},
        'philippines': {'name': '菲律宾', 'flag': '🇵🇭', 'apr': '800%+'},
    }
    
    # 客群类型映射
    scenario_map = {
        'first_loan': {'name': '首贷（New Customer）', 'desc': '新客户首次借款'},
        'repeat_loan': {'name': '复贷（Repeat Customer）', 'desc': '老客户重复借款'},
    }
    
    # 模块映射
    module_map = {
        'model': {'name': '🤖 模型分析', 'desc': '风控模型评估与优化'},
        'rule': {'name': '📋 规则分析', 'desc': '风控规则设计与调整'},
    }
    
    # 业务基准线（按国家+客群）
    benchmarks = {
        'india': {
            'first_loan': {
                '通过率下限': '15%', '首逾上限': '45%', 
                '优秀通过率': '25%+', '优秀首逾': '40%-',
                '产品期限': '15天', '定价': '33%砍头息'
            },
            'repeat_loan': {
                '通过率下限': '96%', '首逾上限': '100%',
                '优秀通过率': '99%+', '优秀首逾': '25%-',
                '产品期限': '15天', '定价': '33%砍头息'
            }
        },
        'indonesia': {
            'first_loan': {
                '通过率下限': '10%', '首逾上限': '50%',
                '优秀通过率': '15%+', '优秀首逾': '42%-',
                '产品期限': '7天', '定价': '35%砍头息'
            },
            'repeat_loan': {
                '通过率下限': '96%', '首逾上限': '100%',
                '优秀通过率': '99%+', '优秀首逾': '25%-',
                '产品期限': '7天', '定价': '35%砍头息'
            }
        },
        'philippines': {
            'first_loan': {
                '通过率下限': '10%', '首逾上限': '50%',
                '优秀通过率': '15%+', '优秀首逾': '42%-',
                '产品期限': '7天', '定价': '35%砍头息'
            },
            'repeat_loan': {
                '通过率下限': '96%', '首逾上限': '100%',
                '优秀通过率': '99%+', '优秀首逾': '25%-',
                '产品期限': '7天', '定价': '35%砍头息'
            }
        }
    }
    
    # 模型评估标准
    model_standards = {
        'model': {
            'AUC合格线': '≥0.51',
            'KS合格线': '≥0.01',
            'PSI警戒线': '<0.01'
        }
    }
    
    # 策略专家基准
    strategy_standards = {
        '高收益覆盖高风险': 'APR 800%+',
        '快速迭代': '快速放款',
        '逾期容忍上限': '50%（超过则亏损）'
    }
    
    country_info = country_map.get(biz_country, None)
    scenario_info = scenario_map.get(biz_scenario, None)
    module_info = module_map.get(biz_module, None)
    
    # 获取具体基准
    benchmark = None
    if biz_country and biz_scenario:
        benchmark = benchmarks.get(biz_country, {}).get(biz_scenario, None)
    
    return {
        'country': biz_country,
        'country_name': country_info['name'] if country_info else None,
        'country_flag': country_info['flag'] if country_info else None,
        'country_apr': country_info['apr'] if country_info else '800%+',
        'scenario': biz_scenario,
        'scenario_name': scenario_info['name'] if scenario_info else None,
        'scenario_desc': scenario_info['desc'] if scenario_info else None,
        'module': biz_module,
        'module_name': module_info['name'] if module_info else None,
        'module_desc': module_info['desc'] if module_info else None,
        'benchmark': benchmark,
        'model_standards': model_standards if biz_module == 'model' else None,
        'strategy_standards': strategy_standards,
    }


# ════════════════════════════════════════════════════════════════════════════════
# 专业Skill调用器
# ════════════════════════════════════════════════════════════════════════════════

class SkillCaller:
    """
    专业Skill调用器 - 让AI能够真正执行分析任务
    """
    
    @staticmethod
    def load_data(file_path: str, file_type: str = "xlsx") -> Dict[str, Any]:
        """加载数据"""
        return registry.invoke("load_data", file_path=file_path, file_type=file_type)
    
    @staticmethod
    def overdue_analysis(file_path: str, target_col: str, score_col: str = "",
                        file_type: str = "xlsx", n_bins: int = 10) -> Dict[str, Any]:
        """逾期率分析"""
        return registry.invoke("overdue_analysis",
                               file_path=file_path,
                               target_col=target_col,
                               score_col=score_col,
                               file_type=file_type,
                               n_bins=n_bins)
    
    @staticmethod
    def model_correlation(file_path: str, target_col: str, 
                         score_cols: List[str] = None, 
                         file_type: str = "xlsx") -> Dict[str, Any]:
        """多模型相关性分析"""
        return registry.invoke("model_correlation",
                              file_path=file_path,
                              target_col=target_col,
                              score_cols=score_cols,
                              file_type=file_type)
    
    @staticmethod
    def feature_importance(file_path: str, target_col: str,
                          file_type: str = "xlsx", top_n: int = 15) -> Dict[str, Any]:
        """特征重要性分析"""
        return registry.invoke("feature_importance",
                              file_path=file_path,
                              target_col=target_col,
                              file_type=file_type,
                              top_n=top_n)
    
    @staticmethod
    def bin_optimize(file_path: str, target_col: str, feature_col: str,
                    max_bins: int = 10, file_type: str = "xlsx") -> Dict[str, Any]:
        """智能分箱"""
        return registry.invoke("bin_optimize",
                              file_path=file_path,
                              target_col=target_col,
                              feature_col=feature_col,
                              max_bins=max_bins,
                              file_type=file_type)
    
    @staticmethod
    def strategy_suggestion(metrics: Dict, bins: List, 
                           pass_rate: float = 0.8) -> Dict[str, Any]:
        """策略建议"""
        return registry.invoke("strategy_suggestion",
                              metrics=metrics,
                              bins=bins,
                              pass_rate=pass_rate)


# ════════════════════════════════════════════════════════════════════════════════
# 专业专家Agent - 真正调用Skill进行分析
# ════════════════════════════════════════════════════════════════════════════════

class DataAnalystAgent:
    """
    数据分析师 - 真正调用专业Skill
    """
    name = "📊 数据分析师"
    
    @classmethod
    def analyze(cls, file_path: str, file_name: str, target_col: str,
                score_cols: List[str], file_type: str, n_bins: int,
                user_note: str, analysis_tags: List[str],
                biz_context: Dict = None) -> Dict[str, Any]:
        """
        执行数据分析师的专业分析
        """
        biz_context = biz_context or {}
        analysis_results = {}
        
        # 1. 加载数据了解概况
        load_result = SkillCaller.load_data(file_path, file_type)
        analysis_results['data_loaded'] = load_result
        
        # 2. 逾期率分析
        overdue_result = SkillCaller.overdue_analysis(
            file_path, target_col, 
            score_cols[0] if score_cols else "",
            file_type, n_bins
        )
        analysis_results['overdue_analysis'] = overdue_result
        
        # 3. 特征重要性分析
        feature_result = SkillCaller.feature_importance(
            file_path, target_col, file_type, top_n=20
        )
        analysis_results['feature_importance'] = feature_result
        
        # 4. 收集所有分析数据
        summary = cls._build_data_summary(analysis_results)
        
        # 5. 调用LLM进行专业诊断
        diagnosis = cls._call_llm_diagnosis(
            summary=summary,
            user_note=user_note,
            analysis_tags=analysis_tags,
            file_name=file_name,
            biz_context=biz_context,
        )
        
        return {
            'expert': cls.name,
            'skill_results': analysis_results,
            'diagnosis': diagnosis,
            'summary': summary,
            # === 新增：结构化数据（供前端图表展示）===
            'metrics': cls._extract_metrics(analysis_results),
            'bins': cls._extract_bins(analysis_results),
            'feature_importance': cls._extract_feature_importance(analysis_results),
            'data_summary_struct': cls._extract_data_info(analysis_results),
        }
    
    @classmethod
    def _extract_metrics(cls, results: Dict) -> Dict[str, Any]:
        """提取核心指标"""
        overdue = results.get('overdue_analysis', {})
        if overdue.get('success'):
            r = overdue.get('result', {})
            return {
                'total_count': r.get('summary', {}).get('total_count', 0),
                'bad_count': r.get('summary', {}).get('bad_count', 0),
                'bad_rate': r.get('summary', {}).get('bad_rate', 0),
                'ks': r.get('metrics', {}).get('ks', 0),
                'auc': r.get('metrics', {}).get('auc', 0),
                'psi': r.get('metrics', {}).get('psi', 0),
            }
        return {}
    
    @classmethod
    def _extract_bins(cls, results: Dict) -> List[Dict]:
        """提取分箱数据"""
        overdue = results.get('overdue_analysis', {})
        if overdue.get('success'):
            return overdue.get('result', {}).get('bins', [])
        return []
    
    @classmethod
    def _extract_feature_importance(cls, results: Dict) -> List[Dict]:
        """提取特征重要性"""
        fi = results.get('feature_importance', {})
        if fi.get('success'):
            return fi.get('result', {}).get('top_features', [])[:15]
        return []
    
    @classmethod
    def _extract_data_info(cls, results: Dict) -> Dict[str, Any]:
        """提取数据概况"""
        loaded = results.get('data_loaded', {})
        if loaded.get('success'):
            data = loaded.get('result', {}).get('data', {})
            return {
                'rows': data.get('shape', [0])[0],
                'cols': data.get('shape', [0, 0])[1],
                'columns': data.get('columns', [])[:30],
            }
        return {}
    
    @classmethod
    def _build_data_summary(cls, results: Dict) -> str:
        """构建数据摘要"""
        lines = []
        
        # 数据概况
        if results.get('data_loaded', {}).get('success'):
            data = results['data_loaded'].get('result', {}).get('data', {})
            if data:
                lines.append(f"【数据规模】{data.get('shape', ['?'])[0]:,}行 × {data.get('shape', ['?', '?'])[1]}列")
                lines.append(f"【列名】{', '.join(data.get('columns', [])[:20])}")
        
        # 逾期分析
        if results.get('overdue_analysis', {}).get('success'):
            ov = results['overdue_analysis'].get('result', {})
            if ov:
                summary = ov.get('summary', {})
                metrics = ov.get('metrics', {})
                bins = ov.get('bins', [])
                
                lines.append(f"\n【逾期概况】")
                lines.append(f"  总样本: {summary.get('total_count', 0):,}条")
                lines.append(f"  坏样本: {summary.get('bad_count', 0):,}条")
                lines.append(f"  逾期率: {summary.get('bad_rate', 0):.2%}")
                lines.append(f"  KS值: {metrics.get('ks', 0):.4f}")
                lines.append(f"  AUC: {metrics.get('auc', 0):.4f}")
                
                if bins:
                    lines.append(f"\n【分箱明细】")
                    for i, b in enumerate(bins[:10]):
                        lines.append(
                            f"  分箱{i+1}: 样本{b.get('count',0):,} | "
                            f"逾期{b.get('bad_count',0):,} | 率{b.get('bad_rate',0):.2%}"
                        )
        
        # 特征重要性
        if results.get('feature_importance', {}).get('success'):
            fi = results['feature_importance'].get('result', {})
            if fi:
                top_features = fi.get('top_features', [])[:10]
                if top_features:
                    lines.append(f"\n【Top10重要特征】")
                    for f in top_features:
                        lines.append(f"  {f.get('feature', '?')}: 相关系数={f.get('spearman_corr', 0):.4f}")
        
        return "\n".join(lines) if lines else "（无数据）"
    
    @classmethod
    def _call_llm_diagnosis(cls, summary: str, user_note: str, 
                            analysis_tags: List[str], file_name: str,
                            biz_context: Dict = None) -> str:
        """调用LLM进行专业诊断 - 自由发挥版（支持业务场景）"""
        
        biz_context = biz_context or {}
        
        # 构建完整的业务目标基准
        benchmark = biz_context.get('benchmark', {})
        scenario = biz_context.get('scenario', 'first_loan')  # 默认首贷
        
        # 判断首贷/复贷
        is_first_loan = scenario == 'first_loan'
        loan_type_text = "首贷（新客户首次借款）" if is_first_loan else "复贷（老客户重复借款）"
        
        # 首贷vs复贷的差异化关注点
        first_vs_repeat_focus = """
【首贷 vs 复贷差异化分析】
"""
        if is_first_loan:
            first_vs_repeat_focus += """首贷客户（New Customer）：
- 关注：欺诈检测、薄征信处理、多头借贷
- 策略：高收益覆盖高风险，快速筛选优质新客
- 重点：识别潜在欺诈和骗贷特征
"""
        else:
            first_vs_repeat_focus += """复贷客户（Repeat Customer）：
- 关注：复借率、忠诚度、历史还款表现
- 策略：高通过率维系客户，首逾容忍度更低
- 重点：基于历史行为预测还款意愿
"""
        
        # 构建业务场景上下文
        biz_section = ""
        if biz_context.get('country_name') or biz_context.get('scenario_name'):
            biz_lines = ["【本次分析业务场景】"]
            if biz_context.get('country_flag'):
                biz_lines.append(f"🌍 国家：{biz_context['country_flag']} {biz_context.get('country_name', '')}")
            biz_lines.append(f"👥 客群：{loan_type_text}")
            if benchmark:
                biz_lines.append(f"📊 业务目标基准：")
                biz_lines.append(f"   - 通过率下限：{benchmark.get('通过率下限', '-')}")
                biz_lines.append(f"   - 首逾上限：{benchmark.get('首逾上限', '-')}")
                if benchmark.get('优秀通过率'):
                    biz_lines.append(f"   - 优秀目标：通过率{benchmark.get('优秀通过率')} & 首逾{benchmark.get('优秀首逾', '-')}")
            if biz_context.get('module_name'):
                biz_lines.append(f"🎯 分析模块：{biz_context['module_name']}")
            biz_section = "\n".join(biz_lines)
        
        # 构建分析方向提示
        focus_hints = ""
        if analysis_tags:
            focus_list = "、".join(analysis_tags)
            focus_hints = f"\n【本次分析重点】{focus_list}"
        
        prompt = f"""你是资深数据分析师，专注于海外现金贷业务数据诊断。

【业务目标基准 - 必须遵循】
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🌍 市场：印度、印尼、菲律宾
📅 产品期限：7天（35%砍头息）/ 15天（32%砍头息）
💰 定价：APR 800%+，高收益覆盖高风险
👥 客群：18-62岁
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

【客群分层基准】
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🔵 首贷（New Customer）：
   - 通过率下限：10%-15%
   - 首逾容忍上限：45%-50%
   - 优秀目标：通过率25%+ & 首逾<40%
   - 核心挑战：欺诈检测、薄征信处理

🔴 复贷（Repeat Customer）：
   - 通过率下限：96%+
   - 首逾容忍上限：25%
   - 优秀目标：通过率99%+ & 首逾<25%
   - 核心挑战：还款意愿预测、复借率提升
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

{biz_section}

{first_vs_repeat_focus}

【用户补充说明】
{user_note if user_note else "（无特殊说明，基于数据自由分析）"}

{focus_hints}

【数据概况】
{summary}

【任务】
作为数据分析师，深入分析这批数据，重点关注：

1️⃣ 数据质量问题
   - 缺失值分布、异常值检测、数据分布偏差
   - 样本是否具有代表性

2️⃣ 客户风险特征（针对当前客群类型）
   - 首贷客户：欺诈特征、多头借贷、薄征信表现
   - 复贷客户：历史还款表现、复借频率、忠诚度

3️⃣ 特征预测价值
   - 哪些特征对预测逾期最有区分度
   - 特征稳定性如何

4️⃣ 异常检测
   - 是否存在可疑的欺诈或数据造假迹象
   - 异常样本的占比和影响

5️⃣ 优化建议
   - 数据层面能给建模和策略什么改进建议

【重要】
- 不要被固定格式束缚，用你最专业的数据分析师视角自由分析
- 找出数据中隐藏的洞察，用数据说话
- 发现任何异常都要重点标注
- 关注点要与当前客群类型（首贷/复贷）相匹配

请开始你的分析："""

        try:
            response = call_glm_with_ds(
                prompt=prompt,
                system="你是资深数据分析师，专注海外现金贷业务数据。自由分析，发现洞察，不拘格式。",
                json_mode=False,
            )
            if response.get('success'):
                return response.get('content', '')
            return f"[分析失败] {response.get('error', '未知错误')}"
        except Exception as e:
            return f"[异常] {str(e)}"


class ModelEngineerAgent:
    """
    金融建模师 - 真正调用专业Skill
    """
    name = "🤖 金融建模师"
    
    @classmethod
    def analyze(cls, file_path: str, file_name: str, target_col: str,
                score_cols: List[str], file_type: str, n_bins: int,
                user_note: str, analysis_tags: List[str],
                data_summary: Dict = None,
                biz_context: Dict = None) -> Dict[str, Any]:
        """
        执行金融建模师的专业分析
        """
        biz_context = biz_context or {}
        analysis_results = {}
        
        # 1. 多模型相关性分析
        if len(score_cols) >= 2:
            corr_result = SkillCaller.model_correlation(
                file_path, target_col, score_cols, file_type
            )
            analysis_results['model_correlation'] = corr_result
        
        # 2. 对每个模型做分箱分析
        bin_results = []
        for score_col in score_cols[:50]:  # 最多分析50个模型
            bin_result = SkillCaller.bin_optimize(
                file_path, target_col, score_col, n_bins, file_type
            )
            bin_results.append({
                'model': score_col,
                'result': bin_result
            })
        analysis_results['bin_analysis'] = bin_results
        
        # 3. 收集模型分析数据
        summary = cls._build_model_summary(analysis_results, score_cols)
        
        # 4. 调用LLM进行专业评估
        evaluation = cls._call_llm_evaluation(
            summary=summary,
            user_note=user_note,
            analysis_tags=analysis_tags,
            file_name=file_name,
            data_summary=data_summary,
            biz_context=biz_context,
        )
        
        return {
            'expert': cls.name,
            'skill_results': analysis_results,
            'evaluation': evaluation,
            'summary': summary,
            # === 新增：结构化数据（供前端图表展示）===
            'model_performance': cls._extract_model_performance(analysis_results),
            'correlation_matrix': cls._extract_correlation_matrix(analysis_results),
            'bin_results': cls._extract_all_bin_results(analysis_results),
        }
    
    @classmethod
    def _extract_model_performance(cls, results: Dict) -> List[Dict]:
        """提取模型性能数据"""
        corr = results.get('model_correlation', {})
        if corr.get('success'):
            return corr.get('result', {}).get('models', [])
        return []
    
    @classmethod
    def _extract_correlation_matrix(cls, results: Dict) -> Dict:
        """提取相关性矩阵"""
        corr = results.get('model_correlation', {})
        if corr.get('success'):
            return corr.get('result', {}).get('cor_matrix', {})
        return {}
    
    @classmethod
    def _extract_all_bin_results(cls, results: Dict) -> List[Dict]:
        """提取所有分箱结果（按AUC排序，返回前6个）"""
        bins = results.get('bin_analysis', [])
        
        # 获取模型性能数据用于排序
        corr = results.get('model_correlation', {})
        model_auc = {}
        if corr.get('success'):
            for m in corr.get('result', {}).get('models', []):
                model_auc[m.get('model', '')] = m.get('auc', 0)
        
        # 提取分箱数据并添加AUC
        bin_data = []
        for b in bins:
            model_name = b.get('model', '')
            result_data = b.get('result', {})
            if result_data.get('success'):
                bin_data.append({
                    'model': model_name,
                    'bins': result_data.get('result', {}).get('bins', []),
                    'iv': result_data.get('result', {}).get('iv', 0),
                    'auc': model_auc.get(model_name, 0),  # 添加AUC用于排序
                })
            else:
                bin_data.append({
                    'model': model_name,
                    'bins': [],
                    'iv': 0,
                    'auc': model_auc.get(model_name, 0),
                })
        
        # 按AUC降序排序，取前6个
        bin_data.sort(key=lambda x: x['auc'], reverse=True)
        return bin_data[:6]
    
    @classmethod
    def _build_model_summary(cls, results: Dict, score_cols: List[str]) -> str:
        """构建模型分析摘要"""
        lines = []
        
        # 多模型相关性
        if results.get('model_correlation', {}).get('success'):
            corr = results['model_correlation'].get('result', {})
            if corr:
                models = corr.get('models', [])
                top3 = corr.get('top3', [])
                
                lines.append(f"【多模型分析】共{len(models)}个模型")
                lines.append(f"\nTop3模型性能：")
                for m in top3[:3]:
                    lines.append(
                        f"  {m.get('model', '?')}: "
                        f"KS={m.get('ks', 0):.4f}, AUC={m.get('auc', 0):.4f}, "
                        f"覆盖率={m.get('coverage', 0):.1%}"
                    )
                
                # 相关性矩阵摘要
                cor_matrix = corr.get('cor_matrix', {})
                if cor_matrix:
                    lines.append(f"\n模型相关性（部分）：")
                    for model, relations in list(cor_matrix.items())[:3]:
                        for other, corr_val in list(relations.items())[:3]:
                            if model != other and abs(corr_val) > 0.5:
                                lines.append(f"  {model} ↔ {other}: {corr_val:.3f}")
        
        # 分箱分析
        bin_results = results.get('bin_analysis', [])
        if bin_results:
            lines.append(f"\n【分箱分析】")
            for br in bin_results[:3]:
                result = br.get('result', {})
                if result.get('success'):
                    r = result.get('result', {})
                    lines.append(
                        f"  {br.get('model', '?')}: IV={r.get('iv', 0):.4f}, "
                        f"单调性={'✅' if r.get('is_monotonic') else '❌'}"
                    )
        
        return "\n".join(lines) if lines else "（无模型数据）"
    
    @classmethod
    def _call_llm_evaluation(cls, summary: str, user_note: str,
                            analysis_tags: List[str], file_name: str,
                            data_summary: Dict = None,
                            biz_context: Dict = None) -> str:
        """调用LLM进行专业评估 - 自由发挥版（支持业务场景）"""
        
        biz_context = biz_context or {}
        
        # 构建完整的业务目标基准
        scenario = biz_context.get('scenario', 'first_loan')
        is_first_loan = scenario == 'first_loan'
        
        # 模型评估标准（首贷/复贷差异化）
        if is_first_loan:
            model_standards_section = """【模型评估标准 - 首贷基准】
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 模型合格线（首贷）：
   - AUC合格线：≥0.51
   - KS合格线：≥0.01
   - PSI警戒线：<0.01

🏆 优秀标准（首贷）：
   - AUC优秀：≥0.60
   - KS优秀：≥0.23
   - PSI优秀：<0.1

💡 首贷模型特点：
   - 薄征信场景，AUC/KS较低是正常的
   - 重点关注模型稳定性（PSI）
   - 欺诈检测与信用评估并重
"""
        else:
            model_standards_section = """【模型评估标准 - 复贷基准】
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 模型合格线（复贷）：
   - AUC合格线：≥0.60
   - KS合格线：≥0.25
   - PSI警戒线：<0.05

🏆 优秀标准（复贷）：
   - AUC优秀：≥0.70
   - KS优秀：≥0.35
   - PSI优秀：<0.02

💡 复贷模型特点：
   - 历史数据丰富，可使用更复杂模型
   - 重点关注预测准确性和区分度
   - 基于还款历史进行精细化评估
"""
        
        # 构建业务场景上下文
        biz_section = ""
        benchmark = biz_context.get('benchmark', {})
        if biz_context.get('country_name') or biz_context.get('scenario_name'):
            biz_lines = ["【本次评估业务场景】"]
            if biz_context.get('country_flag'):
                biz_lines.append(f"🌍 国家：{biz_context['country_flag']} {biz_context.get('country_name', '')}")
            biz_lines.append(f"👥 客群：{'首贷（新客户首次借款）' if is_first_loan else '复贷（老客户重复借款）'}")
            if benchmark:
                biz_lines.append(f"📊 业务目标：")
                biz_lines.append(f"   - 通过率：{benchmark.get('通过率下限', '-')} ~ {benchmark.get('优秀通过率', '-')}")
                biz_lines.append(f"   - 首逾容忍：{benchmark.get('首逾上限', '-')}（优秀<{benchmark.get('优秀首逾', '-')})")
            if biz_context.get('module_name'):
                biz_lines.append(f"🎯 分析模块：{biz_context['module_name']}")
            biz_section = "\n".join(biz_lines)
        
        # 构建分析方向提示
        focus_hints = ""
        if analysis_tags:
            focus_list = "、".join(analysis_tags)
            focus_hints = f"\n【本次评估重点】{focus_list}"
        
        prompt = f"""你是资深金融建模师，专注于海外现金贷风控模型评估，精通薄征信场景下的模型优化。

【业务目标基准 - 必须遵循】
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🌍 市场：印度、印尼、菲律宾
📅 产品期限：7天（35%砍头息）/ 15天（32%砍头息）
💰 定价：APR 800%+，高收益覆盖高风险
👥 客群：18-62岁
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

{model_standards_section}

{biz_section}

【用户补充说明】
{user_note if user_note else "（无特殊说明，基于模型数据自由评估）"}

{focus_hints}

【模型分析数据】
{summary}

【任务】
作为金融建模师，从专业角度深度评估这批模型：

1️⃣ 模型达标情况
   - 哪些模型达到合格线？
   - 哪些模型达到优秀标准？
   - 不达标的模型差距在哪里？

2️⃣ 模型相关性分析
   - 模型之间的相关性如何？
   - 能否通过组合（加权/堆叠）提升效果？
   - 互补性如何？是否有"捞回"潜力？

3️⃣ 稳定性评估（PSI）
   - PSI是否在警戒线以内？
   - 稳定性随时间的变化趋势？
   - 是否存在特征漂移风险？

4️⃣ 区分度分析
   - 模型的区分度主要来自哪些分数区间？
   - 高分段/低分段的区分效果如何？
   - 在业务阈值附近的区分能力？

5️⃣ 优化建议
   - 如何进一步提升模型效果？
   - 针对当前业务场景的模型调优方向？
   - 特征工程或模型结构的改进建议？

【重要】
- 不要被固定格式束缚，用你最专业的建模视角自由分析
- 用数据和指标说话，给出具体可量化的评估
- 找出模型的亮点和短板，给出针对性的优化建议
- 首贷/复贷的评估标准不同，要注意区分

请开始你的专业评估："""

        try:
            response = call_glm_with_ds(
                prompt=prompt,
                system="你是资深金融建模师，从专业角度自由评估模型，不拘格式，用数据说话。",
                json_mode=False,
            )
            if response.get('success'):
                return response.get('content', '')
            return f"[评估失败] {response.get('error', '未知错误')}"
        except Exception as e:
            return f"[异常] {str(e)}"


class RiskStrategistAgent:
    """
    风控策略专家 - 真正调用专业Skill
    """
    name = "🎯 风控策略专家"
    
    @classmethod
    def analyze(cls, file_path: str, file_name: str, target_col: str,
                score_cols: List[str], file_type: str, n_bins: int,
                user_note: str, analysis_tags: List[str],
                data_summary: str, model_summary: str,
                data_diagnosis: str, model_evaluation: str,
                biz_context: Dict = None) -> Dict[str, Any]:
        """
        执行风控策略专家的专业分析
        """
        biz_context = biz_context or {}
        analysis_results = {}
        
        # 1. 获取分箱数据用于策略模拟
        bins_data = []
        for score_col in (score_cols[:2] if score_cols else []):
            bin_result = SkillCaller.bin_optimize(
                file_path, target_col, score_col, n_bins, file_type
            )
            if bin_result.get('success'):
                bins_data.append({
                    'model': score_col,
                    'bins': bin_result.get('result', {}).get('bins', [])
                })
        analysis_results['strategy_bins'] = bins_data
        
        # 2. 生成策略建议
        if bins_data and bins_data[0].get('bins'):
            metrics = {'ks': 0.3, 'auc': 0.7, 'psi': 0.05, 'bad_rate': 0.05}
            strategy_result = SkillCaller.strategy_suggestion(
                metrics, bins_data[0]['bins'], pass_rate=0.8
            )
            analysis_results['strategy_suggestion'] = strategy_result
        
        # 3. 调用LLM进行综合策略建议
        strategy_advice = cls._call_llm_strategy(
            file_name=file_name,
            user_note=user_note,
            analysis_tags=analysis_tags,
            data_summary=data_summary,
            model_summary=model_summary,
            data_diagnosis=data_diagnosis,
            model_evaluation=model_evaluation,
            strategy_data=analysis_results,
            biz_context=biz_context,
        )
        
        return {
            'expert': cls.name,
            'skill_results': analysis_results,
            'strategy_advice': strategy_advice,
            # === 新增：结构化数据（供前端图表展示）===
            'strategy_bins': bins_data,
            'strategy_suggestion': analysis_results.get('strategy_suggestion', {}),
        }
    
    @classmethod
    def _call_llm_strategy(cls, file_name: str, user_note: str,
                          analysis_tags: List[str],
                          data_summary: str, model_summary: str,
                          data_diagnosis: str, model_evaluation: str,
                          strategy_data: Dict,
                          biz_context: Dict = None) -> str:
        """调用LLM进行综合策略建议 - 自由发挥版（支持业务场景）"""
        
        biz_context = biz_context or {}
        
        # 构建完整的业务目标基准
        scenario = biz_context.get('scenario', 'first_loan')
        is_first_loan = scenario == 'first_loan'
        benchmark = biz_context.get('benchmark', {})
        
        # 核心业务逻辑
        common_logic = """
【核心业务逻辑】
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
✅ 高收益覆盖高风险（APR 800%+）
✅ 快速迭代、快速放款
✅ 逾期容忍上限：50%（超过则亏损）
✅ 薄征信场景，策略要简单高效
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
        
        # 构建具体业务基准（首贷/复贷差异化）
        if is_first_loan:
            bench_section = f"""
【首贷业务目标基准】
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🌍 市场：{biz_context.get('country_flag', '')} {biz_context.get('country_name', '')}
📅 产品：{benchmark.get('产品期限', '7/15天')} {benchmark.get('定价', '')}

📊 核心指标目标：
   - 首逾上限：{benchmark.get('首逾上限', '45%')}
   - 优秀目标：首逾{benchmark.get('优秀首逾', '40%-')}

🎯 首贷策略重点：
   - 欺诈识别优先
   - 薄征信下的快速筛选
   - 高收益覆盖高风险
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
        else:
            bench_section = f"""
【复贷业务目标基准】
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🌍 市场：{biz_context.get('country_flag', '')} {biz_context.get('country_name', '')}
📅 产品：{benchmark.get('产品期限', '7/15天')} {benchmark.get('定价', '')}

📊 核心指标目标：
   - 首逾上限：{benchmark.get('首逾上限', '25%')}
   - 优秀目标：首逾{benchmark.get('优秀首逾', '25%-')}

🎯 复贷策略重点：
   - 维系客户优先
   - 低首逾控制
   - 历史行为精准预测
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
        
        # 构建业务场景上下文
        biz_section = ""
        if biz_context.get('country_name') or biz_context.get('scenario_name'):
            biz_lines = ["【本次策略业务场景】"]
            if biz_context.get('country_flag'):
                biz_lines.append(f"🌍 国家：{biz_context['country_flag']} {biz_context.get('country_name', '')}")
            biz_lines.append(f"👥 客群：{'首贷（新客户首次借款）' if is_first_loan else '复贷（老客户重复借款）'}")
            if biz_context.get('module_name'):
                biz_lines.append(f"🎯 分析模块：{biz_context['module_name']}")
            biz_section = "\n".join(biz_lines)
        
        # 构建分析方向提示
        focus_hints = ""
        if analysis_tags:
            focus_list = "、".join(analysis_tags)
            focus_hints = f"\n【本次策略重点】{focus_list}"
        
        prompt = f"""你是资深风控策略专家，精通海外现金贷业务策略设计，拥有丰富的薄征信场景风控经验。

{common_logic}

{bench_section}

{biz_section}

【用户补充说明】
{user_note if user_note else "（无特殊说明，基于数据分析自由制定策略）"}

{focus_hints}

【数据分析师诊断】
{data_diagnosis}

【建模师评估】
{model_evaluation}

【任务】
作为风控策略专家，你的核心职责是：仔细审视上述数据分析师和建模师的分析结果，**发现数据中的关键洞察和实际问题**，然后给出真正有针对性的策略建议。

请按照以下步骤思考：

1️⃣ **数据诊断**：快速扫描数据，发现最关键的问题或亮点（3-5个）
   - 哪些问题最紧急/最严重？
   - 哪些地方表现优秀值得保持？
   - 有什么反常或异常的数据模式？

2️⃣ **洞察提炼**：基于诊断结果，提取3-5个核心洞察
   - 这些洞察直接决定了后续策略方向
   - 不要泛泛而谈，要具体到数据表现

3️⃣ **策略建议**：针对每个核心洞察，给出可落地的策略建议
   - 包含：具体操作、预期效果、风险提示
   - 要敢给具体数字（阈值、逾期率目标等）
   - 策略要能解决你发现的核心问题

**【重要】必须生成至少6条策略建议，建议8-12条，按重要性排序。**

【策略设计原则】
- 体现"高收益覆盖高风险"的业务逻辑
- 追求收益最大化前提下的风险可控
- 策略要可落地、可执行
- 首贷重欺诈检测，复贷重复借率提升
- **本次分析不涉及通过率预测，专注于风控策略本身**

【重要】
- 不要按照固定框架泛泛而谈，要根据你发现的具体问题给出建议
- 策略要有洞察、有胆识、有落地性
- 敢于给出具体的分数阈值和逾期率目标建议
- 要区分首贷和复贷的策略差异
- **必须输出至少6条策略建议**

请开始你的分析，只输出最有价值的建议："""

        try:
            response = call_glm_with_ds(
                prompt=prompt,
                system="你是海外现金贷风控策略专家，自由制定策略，有洞察有胆识，追求收益最大化下的风险可控。",
                json_mode=False,
            )
            if response.get('success'):
                return response.get('content', '')
            return f"[建议失败] {response.get('error', '未知错误')}"
        except Exception as e:
            return f"[异常] {str(e)}"


# ════════════════════════════════════════════════════════════════════════════════
# 多专家协作分析器（重构版）
# ════════════════════════════════════════════════════════════════════════════════

class MultiExpertAnalyzer:
    """
    多专家协作分析器（重构版）
    
    核心改变：
    1. 每个专家真正调用专业Skill进行分析
    2. 不再生成固定格式报告
    3. 让AI自由发挥，给出真正有价值的分析
    4. 支持分析结果下载保存
    """
    
    @classmethod
    def analyze(cls, file_path: str, file_name: str, target_col: str,
                score_cols: List[str], file_type: str, n_bins: int,
                user_note: str = '', analysis_tags: List[str] = None,
                upload_folder: str = None,
                # 业务场景参数
                biz_scenario: str = '',
                biz_country: str = '',
                biz_module: str = '',
                ) -> Dict[str, Any]:
        """
        执行多专家协作分析
        
        Args:
            file_path: 数据文件路径
            file_name: 文件名
            target_col: 目标列
            score_cols: 分数列列表
            file_type: 文件类型
            n_bins: 分箱数
            user_note: 用户补充说明
            analysis_tags: 分析类型标签
            upload_folder: 上传文件夹路径（用于保存报告）
            biz_scenario: 客群类型（first_loan / repeat_loan）
            biz_country: 国家（india / indonesia / philippines）
            biz_module: 模块（model / rule）
            
        Returns:
            {
                'success': bool,
                'expert_results': {...},    # 各专家分析结果
                'data_summary': str,        # 数据摘要
                'model_summary': str,       # 模型摘要
                'report': str,             # 完整报告（markdown格式）
                'report_path': str,        # 报告保存路径（如果有）
            }
        """
        analysis_tags = analysis_tags or []
        
        results = {
            'success': True,
            'expert_results': {},
            'data_summary': '',
            'model_summary': '',
            'report': '',
            'report_path': '',
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        }
        
        try:
            # 整理业务场景信息
            biz_context = _build_biz_context(biz_scenario, biz_country, biz_module)
            
            # ════════════════════════════════════════════════════════════════
            # 第一阶段：数据分析师
            # ════════════════════════════════════════════════════════════════
            print("[多专家分析] 阶段1：数据分析师分析中...")
            data_result = DataAnalystAgent.analyze(
                file_path=file_path,
                file_name=file_name,
                target_col=target_col,
                score_cols=score_cols,
                file_type=file_type,
                n_bins=n_bins,
                user_note=user_note,
                analysis_tags=analysis_tags,
                biz_context=biz_context,
            )
            results['expert_results']['data_analyst'] = data_result
            results['data_summary'] = data_result.get('summary', '')
            
            # ════════════════════════════════════════════════════════════════
            # 第二阶段：金融建模师
            # ════════════════════════════════════════════════════════════════
            print("[多专家分析] 阶段2：金融建模师分析中...")
            model_result = ModelEngineerAgent.analyze(
                file_path=file_path,
                file_name=file_name,
                target_col=target_col,
                score_cols=score_cols,
                file_type=file_type,
                n_bins=n_bins,
                user_note=user_note,
                analysis_tags=analysis_tags,
                data_summary=data_result.get('skill_results', {}),
                biz_context=biz_context,
            )
            results['expert_results']['model_engineer'] = model_result
            results['model_summary'] = model_result.get('summary', '')
            
            # ════════════════════════════════════════════════════════════════
            # 第三阶段：风控策略专家
            # ════════════════════════════════════════════════════════════════
            print("[多专家分析] 阶段3：风控策略专家分析中...")
            strategy_result = RiskStrategistAgent.analyze(
                file_path=file_path,
                file_name=file_name,
                target_col=target_col,
                score_cols=score_cols,
                file_type=file_type,
                n_bins=n_bins,
                user_note=user_note,
                analysis_tags=analysis_tags,
                data_summary=results['data_summary'],
                model_summary=results['model_summary'],
                data_diagnosis=data_result.get('diagnosis', ''),
                model_evaluation=model_result.get('evaluation', ''),
                biz_context=biz_context,
            )
            results['expert_results']['risk_strategist'] = strategy_result
            
            # ════════════════════════════════════════════════════════════════
            # 第四阶段：生成完整报告
            # ════════════════════════════════════════════════════════════════
            results['report'] = cls._build_report(
                file_name=file_name,
                user_note=user_note,
                analysis_tags=analysis_tags,
                data_result=data_result,
                model_result=model_result,
                strategy_result=strategy_result,
                biz_context=biz_context,
            )
            
            # 保存报告到文件
            if upload_folder:
                report_filename = f"analysis_report_{uuid.uuid4().hex[:8]}.md"
                report_path = os.path.join(upload_folder, report_filename)
                try:
                    with open(report_path, 'w', encoding='utf-8') as f:
                        f.write(results['report'])
                    results['report_path'] = report_path
                    results['report_filename'] = report_filename
                    print(f"[多专家分析] 报告已保存: {report_path}")
                except Exception as e:
                    print(f"[多专家分析] 保存报告失败: {e}")
            
            return results
            
        except Exception as e:
            results['success'] = False
            results['error'] = str(e)
            import traceback
            traceback.print_exc()
            return results
    
    @classmethod
    def _build_report(cls, file_name: str, user_note: str,
                     analysis_tags: List[str],
                     data_result: Dict, model_result: Dict,
                     strategy_result: Dict,
                     biz_context: Dict = None) -> str:
        """生成完整报告（Markdown格式）- 海外现金贷场景"""
        
        biz_context = biz_context or {}
        scenario = biz_context.get('scenario', 'first_loan')
        is_first_loan = scenario == 'first_loan'
        benchmark = biz_context.get('benchmark', {})
        
        # 构建详细的业务基准线
        business_benchmarks = f"""
### 🌐 业务目标基准

| 项目 | 内容 |
|------|------|
| 市场 | {biz_context.get('country_flag', '')} {biz_context.get('country_name', '印度/印尼/菲律宾')} |
| 客群 | {'🔵 首贷（新客户首次借款）' if is_first_loan else '🔴 复贷（老客户重复借款）'} |
| 产品期限 | {benchmark.get('产品期限', '7天/15天')} |
| 定价 | {benchmark.get('定价', '32%-35%砍头息')} |
| 通过率下限 | {benchmark.get('通过率下限', '15%')} |
| 首逾上限 | {benchmark.get('首逾上限', '45%')} |
| 优秀通过率 | {benchmark.get('优秀通过率', '25%+')} |
| 优秀首逾 | {benchmark.get('优秀首逾', '40%-')} |

### 📊 模型评估标准

**首贷合格线：**
- AUC ≥ 0.51（薄征信场景）
- KS ≥ 0.01
- PSI < 0.01

**复贷合格线：**
- AUC ≥ 0.60
- KS ≥ 0.25
- PSI < 0.05

### 🎯 核心业务逻辑
- **高收益覆盖高风险**（APR 800%+）
- **快速迭代、快速放款**
- **逾期容忍上限：50%**（超过则亏损）
"""
        
        lines = [
            "# 🔍 RiskPilot AI 策略分析报告",
            "",
            f"**📁 分析文件：** {file_name}",
            f"**⏰ 分析时间：** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"**🏷️ 分析类型：** {', '.join(analysis_tags) if analysis_tags else '通用分析'}",
            "",
            "---",
            business_benchmarks,
        ]
        
        if user_note:
            lines.append(f"")
            lines.append(f"### 📝 用户需求")
            lines.append(f"**用户补充说明：** {user_note}")
        
        lines.append("")
        lines.append("---")
        lines.append("")
        
        # ════════════════════════════════════════════════════════════════
        # 数据分析师报告
        # ════════════════════════════════════════════════════════════════
        lines.append("## 📊 数据分析师诊断")
        lines.append("")
        lines.append(data_result.get('diagnosis', '（无诊断结果）'))
        lines.append("")
        lines.append("---")
        lines.append("")
        
        # ════════════════════════════════════════════════════════════════
        # 建模师报告
        # ════════════════════════════════════════════════════════════════
        lines.append("## 🤖 金融建模师评估")
        lines.append("")
        lines.append(model_result.get('evaluation', '（无评估结果）'))
        lines.append("")
        lines.append("---")
        lines.append("")
        
        # ════════════════════════════════════════════════════════════════
        # 策略专家报告
        # ════════════════════════════════════════════════════════════════
        lines.append("## 🎯 风控策略专家建议")
        lines.append("")
        lines.append(strategy_result.get('strategy_advice', '（无建议）'))
        lines.append("")
        lines.append("---")
        lines.append("")
        
        # ════════════════════════════════════════════════════════════════
        # 附录：原始分析数据
        # ════════════════════════════════════════════════════════════════
        lines.append("## 📋 附录：原始分析数据")
        lines.append("")
        
        lines.append("### 数据摘要")
        lines.append("```")
        lines.append(data_result.get('summary', '（无数据）'))
        lines.append("```")
        lines.append("")
        
        lines.append("### 模型摘要")
        lines.append("```")
        lines.append(model_result.get('summary', '（无模型数据）'))
        lines.append("```")
        lines.append("")
        
        lines.append("---")
        lines.append(f"*本报告由 RiskPilot 多专家AI系统自动生成*")
        
        return "\n".join(lines)


# ════════════════════════════════════════════════════════════════════════════════
# 多专家分析快捷入口
# ════════════════════════════════════════════════════════════════════════════════

def multi_expert_analysis(
    metrics: Dict,
    bins: List[Dict],
    file_info: Dict,
    user_note: str = '',
    analysis_tags: List[str] = None,
    file_path: str = '',
    target_col: str = '',
    score_cols: List[str] = None,
    file_type: str = 'xlsx',
    n_bins: int = 10,
    upload_folder: str = None,
    # 业务场景参数
    biz_scenario: str = '',   # first_loan / repeat_loan
    biz_country: str = '',    # india / indonesia / philippines
    biz_module: str = '',     # model / rule
) -> Dict[str, Any]:
    """
    多专家协作分析快捷入口（供 routes/analysis.py 调用）
    
    Args:
        metrics: 核心指标 {ks, auc, psi, bad_rate, ...}
        bins: 分箱数据
        file_info: 文件信息 {file_name, n_rows, target_col, score_col}
        user_note: 用户补充说明
        biz_scenario: 客群类型（first_loan / repeat_loan）
        biz_country: 国家（india / indonesia / philippines）
        biz_module: 模块（model / rule）
        analysis_tags: 分析类型标签
        file_path: 数据文件路径（用于调用Skill）
        target_col: 目标列
        score_cols: 分数列列表
        file_type: 文件类型
        n_bins: 分箱数
        upload_folder: 上传文件夹（用于保存报告）
        
    Returns:
        {
            'success': bool,
            'expert_reports': {...},   # 各专家报告
            'final_report': str,       # 完整报告
            'report_path': str,        # 报告文件路径
            'suggestions': [...],      # 建议列表
        }
    """
    score_cols = score_cols or [file_info.get('score_col', '')]
    
    # 如果提供了文件路径，使用完整的多专家分析
    if file_path and os.path.exists(file_path):
        result = MultiExpertAnalyzer.analyze(
            file_path=file_path,
            file_name=file_info.get('file_name', '未知'),
            target_col=target_col or file_info.get('target_col', 'label'),
            score_cols=score_cols,
            file_type=file_type,
            n_bins=n_bins,
            user_note=user_note,
            analysis_tags=analysis_tags,
            upload_folder=upload_folder,
            # 业务场景参数
            biz_scenario=biz_scenario,
            biz_country=biz_country,
            biz_module=biz_module,
        )
        
        return {
            'success': result.get('success', False),
            'expert_reports': result.get('expert_results', {}),
            'final_report': result.get('report', ''),
            'report_path': result.get('report_path', ''),
            'report_filename': result.get('report_filename', ''),
            'data_summary': result.get('data_summary', ''),
            'model_summary': result.get('model_summary', ''),
            'suggestions': _parse_suggestions_from_result(result),
        }
    else:
        # 降级模式：使用原有的简化分析
        return _fallback_analysis(metrics, bins, file_info, user_note, analysis_tags)


def _parse_suggestions_from_result(result: Dict) -> List[Dict]:
    """解析建议"""
    suggestions = []
    
    expert_results = result.get('expert_results', {})
    
    # 数据分析师建议
    if 'data_analyst' in expert_results:
        diagnosis = expert_results['data_analyst'].get('diagnosis', '')
        if diagnosis:
            suggestions.append({
                'level': 'info',
                'category': '数据分析师',
                'title': '📊 数据诊断完成',
                'content': diagnosis[:500],
                'highlight': True
            })
    
    # 建模师建议
    if 'model_engineer' in expert_results:
        evaluation = expert_results['model_engineer'].get('evaluation', '')
        if evaluation:
            suggestions.append({
                'level': 'info',
                'category': '金融建模师',
                'title': '🤖 模型评估完成',
                'content': evaluation[:500],
                'highlight': True
            })
    
    # 策略专家建议
    if 'risk_strategist' in expert_results:
        advice = expert_results['risk_strategist'].get('strategy_advice', '')
        if advice:
            suggestions.append({
                'level': 'info',
                'category': '风控策略专家',
                'title': '🎯 策略建议完成',
                'content': advice[:500],
                'highlight': True
            })
    
    return suggestions


def _fallback_analysis(metrics: Dict, bins: List[Dict],
                      file_info: Dict, user_note: str,
                      analysis_tags: List[str]) -> Dict[str, Any]:
    """降级分析（没有文件路径时）"""
    return {
        'success': True,
        'expert_reports': {},
        'final_report': f"""# 分析结果

文件：{file_info.get('file_name', '未知')}
用户备注：{user_note or '无'}
分析类型：{', '.join(analysis_tags) if analysis_tags else '通用'}

## 核心指标
- KS: {metrics.get('ks', 0):.4f}
- AUC: {metrics.get('auc', 0):.4f}
- 逾期率: {metrics.get('bad_rate', 0):.2%}

（降级模式：仅显示指标，未进行深度分析）
""",
        'report_path': '',
        'suggestions': [{
            'level': 'warning',
            'category': '系统',
            'title': '降级模式',
            'content': '未提供文件路径，仅显示基础指标。',
            'highlight': False
        }]
    }


# ════════════════════════════════════════════════════════════════════════════════
# 其他原有函数（保持向后兼容）
# ════════════════════════════════════════════════════════════════════════════════

def agent_analysis(
    user_request: str,
    file_path: str,
    file_type: str = "xlsx",
    agent_type: str = "all",
    target_col: str = "label",
    score_col: str = "",
    n_bins: int = 10,
) -> Dict[str, Any]:
    """
    调用 Agent 系统进行多 Agent 协作分析
    """
    return run_agent_analysis(
        user_request=user_request,
        file_path=file_path,
        file_type=file_type,
        agent_type=agent_type,
        target_col=target_col,
        score_col=score_col,
        n_bins=n_bins,
    )


def check_llm_config() -> Dict[str, Any]:
    """
    检查大模型配置状态
    """
    status = check_router_status()
    return {
        'configured': status.get('glm', {}).get('configured') or status.get('ds', {}).get('configured'),
        'api_url':    status.get('glm', {}).get('base_url', LLM_API_URL),
        'model':      status.get('glm', {}).get('model', LLM_MODEL),
        'message':    status.get('recommendation', '状态未知'),
        'glm_status': {
            'configured': status.get('glm', {}).get('configured', False),
            'model':      status.get('glm', {}).get('model', ''),
            'cost_per_m': '免费',
            'recommendation': '主力模型（免费）' if status.get('glm', {}).get('configured') else '请配置智谱API Key',
        },
        'ds_status': {
            'configured': status.get('ds', {}).get('configured', False),
            'model':      status.get('ds', {}).get('model', ''),
            'cost_per_m': '输入1元/M，输出8元/M',
            'recommendation': '备用模型（兜底）' if status.get('ds', {}).get('configured') else '建议配置DeepSeek API Key',
        },
        'router_stats':  status.get('stats', {}),
        'agent_available': True,
        'multi_expert_available': True,
    }


def generate_llm_suggestion(metrics: Dict, bins: List[Dict],
                            file_info: Dict, user_note: str = '',
                            analysis_tags: List[str] = None) -> Dict[str, Any]:
    """
    Generate AI suggestion (backward compatibility)
    Uses multi_expert_analysis as the underlying implementation
    """
    return multi_expert_analysis(
        metrics=metrics,
        bins=bins,
        file_info=file_info,
        user_note=user_note,
        analysis_tags=analysis_tags,
    )



