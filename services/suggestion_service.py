"""
AI 策略建议生成服务
根据分析结果生成可落地的策略调整建议

v2：接入 LLM 动态生成 + 规则引擎兜底
- generate_llm_dynamic_suggestion() → 调用 LLM 根据实际数据动态生成针对性建议
- generate_suggestion()              → 规则引擎快速建议（仅 LLM 失败时使用）
"""

import json
from typing import List, Dict, Any, Optional

# ── LLM 调用（复用项目已有的路由）────────────────────────────────────────────
from services.agent_router import call_glm_with_ds

# 阈值定义
THRESHOLDS = {
    'ks_good':   0.35,
    'ks_warning': 0.25,
    'auc_good':   0.75,
    'auc_warning': 0.65,
    'psi_good':   0.1,
    'psi_warning': 0.25,
    'bad_rate_low': 0.02,
    'bad_rate_high': 0.08,
}


# ════════════════════════════════════════════════════════════════════════════════
# LLM 动态策略建议生成
# ════════════════════════════════════════════════════════════════════════════════

# 策略专家系统提示词（动态生成版）
_STRATEGY_SYSTEM_PROMPT = """你是一位资深的海外信贷风控策略专家，专注于印度、印尼、菲律宾等市场的超短期贷款（7天/15天，APR 800%+）风控策略。

【你的分析流程】
1. 先仔细阅读数据，**发现问题**：找出数据中最关键的问题、异常或亮点
2. **提炼洞察**：3-5个核心发现，这些决定策略方向
3. **针对性建议**：每个核心发现对应的具体可落地建议

【核心要求】
- **必须基于数据中的实际问题**，不是模板话术
- **不要使用固定框架**：不要按"规则设计、分层策略..."这种套路来
- **具体数字优先**：敢给阈值、逾期率目标等具体数字
- **洞察驱动**：先发现问题，再给建议
- **不讨论通过率**：本次分析不涉及通过率预测，只关注风控策略本身

【输出格式】
用 JSON 数组返回，每条建议：
{
  "title": "简短标题（emoji开头）",
  "content": "核心分析 + 具体操作建议（100-300字）",
  "level": "info/warning/success/danger"
}
**重要**：建议数量**至少6条**，建议生成8-12条，按重要性排序。

【业务背景】
- 高APR定价策略：APR 800%+
- 多模型组合：串行/捞回策略
- 逾期容忍上限：50%
- 重点关注：欺诈检测、信用评估、分箱优化、阈值设定、规则拦截效果

【规则分析专项】
当分析数据包含规则相关内容时（拦截率、命中分布、交叉分析等），重点关注：
- 高拦截率规则的合理性，是否会误杀优质客户
- 规则命中后的逾期率（Lift），评估规则有效性
- 规则之间的冗余性，避免重复拦截
- 规则与分数的串行策略最优阈值选择
- 欺诈检测规则的特殊设计"""


def generate_llm_dynamic_suggestion(
    analysis_data: Dict[str, Any],
    biz_scenario: str = '',
    biz_country: str = '',
    biz_module: str = '',
) -> Dict[str, Any]:
    """
    调用 LLM 根据实际分析数据动态生成策略建议

    Args:
        analysis_data: 分析结果数据，支持两种模式：
            - model_binning: {'model_summary': [...], 'data_summary': {...}, 'all_results': [...]}
            - model_correlation: {'performance': [...], 'correlation': [...], 'strategy_metrics': {...}}
        biz_scenario: 客群类型（first_loan / repeat_loan）
        biz_country: 国家（india / indonesia / philippines）
        biz_module: 模块（model / rule）

    Returns:
        {
            'success': bool,
            'source': 'llm' | 'fallback',
            'suggestions': [...],        # 标准格式建议列表（供前端 renderSuggestions 使用）
            'llm_raw': str,              # LLM 原始回复（Markdown 格式）
            'llm_parsed': bool,          # JSON 解析是否成功
        }
    """
    # 构建数据摘要给 LLM
    data_prompt = _build_data_prompt(analysis_data, biz_scenario, biz_country, biz_module)

    if not data_prompt:
        return {
            'success': False,
            'source': 'fallback',
            'suggestions': [],
            'llm_raw': '',
            'llm_parsed': False,
        }

    user_message = f"""请根据以下实际分析数据，给出针对性的风控策略建议：

{data_prompt}

---

业务背景：
- 客群类型：{'首贷（New Customer）' if biz_scenario == 'first_loan' else '复贷（Repeat Customer）' if biz_scenario == 'repeat_loan' else '未指定'}
- 目标市场：{'印度' if biz_country == 'india' else '印尼' if biz_country == 'indonesia' else '菲律宾' if biz_country == 'philippines' else '未指定'}
- 分析模块：{'模型分析' if biz_module == 'model' else '规则分析' if biz_module == 'rule' else '综合分析'}

请严格基于以上数据中的具体数值和发现给出建议，不要给出与数据无关的通用建议。直接返回 JSON 数组，不要加 ```json 代码块。"""

    try:
        result = call_glm_with_ds(
            prompt=user_message,
            system=_STRATEGY_SYSTEM_PROMPT,
            json_mode=True,
            temperature=0.7,
        )

        if not result.get('success'):
            print(f"[LLM建议] 调用失败: {result.get('error', '未知错误')}")
            return {
                'success': False,
                'source': 'fallback',
                'suggestions': [],
                'llm_raw': '',
                'llm_parsed': False,
            }

        raw_content = result.get('content', '').strip()

        # 尝试解析 JSON
        suggestions = _parse_llm_suggestions(raw_content)

        if suggestions:
            print(f"[LLM建议] 解析成功，共 {len(suggestions)} 条建议")
            return {
                'success': True,
                'source': 'llm',
                'suggestions': suggestions,
                'llm_raw': raw_content,
                'llm_parsed': True,
            }
        else:
            # JSON 解析失败，但有文本内容，尝试提取结构化建议
            print(f"[LLM建议] JSON 解析失败，尝试文本提取")
            text_suggestions = _extract_suggestions_from_text(raw_content)
            if text_suggestions:
                return {
                    'success': True,
                    'source': 'llm',
                    'suggestions': text_suggestions,
                    'llm_raw': raw_content,
                    'llm_parsed': False,
                }
            return {
                'success': False,
                'source': 'fallback',
                'suggestions': [],
                'llm_raw': raw_content,
                'llm_parsed': False,
            }

    except Exception as e:
        print(f"[LLM建议] 异常: {e}")
        import traceback
        traceback.print_exc()
        return {
            'success': False,
            'source': 'fallback',
            'suggestions': [],
            'llm_raw': str(e),
            'llm_parsed': False,
        }


def _build_data_prompt(
    analysis_data: Dict[str, Any],
    biz_scenario: str,
    biz_country: str,
    biz_module: str,
) -> str:
    """构建给 LLM 的数据摘要"""

    sections = []

    # ── 模型分箱分析数据 ──
    model_summary = analysis_data.get('model_summary', [])
    data_summary = analysis_data.get('data_summary', {})

    if model_summary:
        lines = ['## 模型性能概览']
        for i, m in enumerate(model_summary[:20]):
            name = m.get('model_name', m.get('model', f'模型{i+1}'))
            auc = m.get('auc', 0)
            ks = m.get('ks', 0)
            bad_rate = m.get('bad_rate', 0)
            coverage = m.get('coverage', 0)
            # 兼容字符串格式的逾期率
            if isinstance(bad_rate, str):
                bad_rate = float(bad_rate.replace('%', '')) / 100 if '%' in bad_rate else float(bad_rate)
            lines.append(
                f"- {name}: AUC={auc}, KS={ks}, 逾期率={bad_rate:.4f}, 覆盖率={coverage:.4f}"
            )
        sections.append('\n'.join(lines))

    if data_summary:
        lines = ['## 数据概况']
        if isinstance(data_summary, dict):
            total = data_summary.get('total_samples', data_summary.get('total_count', '未知'))
            bad = data_summary.get('bad_samples', data_summary.get('bad_count', '未知'))
            rate = data_summary.get('overall_bad_rate', '未知')
            if isinstance(rate, (int, float)):
                rate = f'{rate:.4f}'
            lines.append(f"- 样本量: {total}")
            lines.append(f"- 坏样本: {bad}")
            lines.append(f"- 整体逾期率: {rate}")
            n_models = data_summary.get('n_models', data_summary.get('model_count', '未知'))
            if n_models and n_models != '未知':
                lines.append(f"- 模型数量: {n_models}")
        sections.append('\n'.join(lines))

    # ── 模型相关性分析数据 ──
    performance = analysis_data.get('performance', [])
    strategy_metrics = analysis_data.get('strategy_metrics', {})
    correlation = analysis_data.get('correlation', [])

    if performance:
        lines = ['## 各模型详细性能']
        sorted_perf = sorted(performance, key=lambda x: x.get('ks', 0), reverse=True)
        for p in sorted_perf[:15]:
            name = p.get('model', '未知')
            auc = p.get('auc', 0)
            ks = p.get('ks', 0)
            cov = p.get('coverage', 0)
            br = p.get('bad_rate', 0)
            lines.append(
                f"- {name}: AUC={auc:.4f}, KS={ks:.4f}, 覆盖率={cov:.2%}, 逾期率={br:.4f}"
            )
        sections.append('\n'.join(lines))

    if correlation and isinstance(correlation, list) and len(correlation) > 0:
        # 提取高相关对
        lines = ['## 模型间 Spearman 相关性']
        high_corr = [c for c in correlation if abs(c.get('correlation', c.get('corr', 0))) > 0.8]
        if high_corr:
            lines.append('高相关模型对（|ρ|>0.8）：')
            for c in high_corr[:10]:
                a = c.get('model_a', c.get('col_a', ''))
                b = c.get('model_b', c.get('col_b', ''))
                val = c.get('correlation', c.get('corr', 0))
                lines.append(f"- {a} ↔ {b}: ρ={val:.4f}")
        else:
            lines.append('未发现高相关模型对（所有 |ρ|≤0.8），模型间信息冗余度较低。')
        sections.append('\n'.join(lines))

    if strategy_metrics:
        strategies = strategy_metrics.get('strategies', [])
        if strategies:
            lines = ['## 串行策略模拟结果']
            for s in strategies[:8]:
                main = s.get('main_model', '')
                rescue = s.get('rescue_model', '无')
                q = s.get('reject_rate', 0)
                pr = s.get('pass_rate', 0)
                pbr = s.get('pass_bad_rate', 0)
                rc = s.get('rescue_count', 0)
                lines.append(
                    f"- 主模型={main}, 捞回模型={rescue}, q={q}%, "
                    f"通过率={pr:.2%}, 通过逾期率={pbr:.4f}, 捞回人数={rc}"
                )
            sections.append('\n'.join(lines))

    # ── 规则分析数据 ──
    rule_binning = analysis_data.get('rule_binning', {})
    user_profile = analysis_data.get('user_profile', {})
    is_rule_analysis = analysis_data.get('rule_analysis', False)
    
    if is_rule_analysis or rule_binning:
        # 规则分箱分析（取值-首逾-Lift）
        if rule_binning:
            lines = ['## 规则分箱分析（取值-样本-逾期率-Lift）']
            for rule_name, bin_data in rule_binning.items():
                bin_type = '等频分箱' if bin_data.get('bin_type') == 'continuous' else '离散取值'
                unique_count = bin_data.get('unique_count', 0)
                lines.append(f'### {rule_name} (共{unique_count}个取值, {bin_type})')
                bins = bin_data.get('bins', [])
                for b in bins[:15]:  # 最多15行
                    if bin_data.get('bin_type') == 'continuous':
                        label = b.get('bin_range', '')
                    else:
                        label = b.get('value', '')
                    lines.append(
                        f"- {label}: "
                        f"样本={b.get('count', 0):,}, "
                        f"逾期率={b.get('bad_rate', 0)*100:.2f}%, "
                        f"Lift={b.get('lift', 0):.2f}"
                    )
            sections.append('\n'.join(lines))
        
        # 用户画像分析（好/坏用户群体）
        if user_profile:
            overall_bad = user_profile.get('overall_bad_rate', 0)
            lines = [f'## 用户画像分析（整体逾期率: {overall_bad*100:.2f}%）']
            
            # 好用户群体
            good_rules = user_profile.get('good_rules', [])
            if good_rules:
                lines.append('### 好用户群体（逾期率低于整体70%）')
                for r in good_rules[:8]:
                    lines.append(
                        f"- {r['rule']}={r['value']}: "
                        f"样本={r['sample_count']:,}, "
                        f"逾期率={r['bad_rate']*100:.2f}%, "
                        f"Lift={r['lift']:.2f}"
                    )
            
            # 坏用户群体
            bad_rules = user_profile.get('bad_rules', [])
            if bad_rules:
                lines.append('### 坏用户群体（逾期率高于整体150%）')
                for r in bad_rules[:8]:
                    lines.append(
                        f"- {r['rule']}={r['value']}: "
                        f"样本={r['sample_count']:,}, "
                        f"逾期率={r['bad_rate']*100:.2f}%, "
                        f"Lift={r['lift']:.2f}"
                    )
            
            # 好用户组合
            good_combos = user_profile.get('good_combinations', [])
            if good_combos:
                lines.append('### 好用户组合')
                for c in good_combos[:5]:
                    lines.append(
                        f"- {c['combo']}: "
                        f"样本={c['sample_count']:,}, "
                        f"逾期率={c['bad_rate']*100:.2f}%, "
                        f"Lift={c['lift']:.2f}"
                    )
            
            # 坏用户组合
            bad_combos = user_profile.get('bad_combinations', [])
            if bad_combos:
                lines.append('### 坏用户组合')
                for c in bad_combos[:5]:
                    lines.append(
                        f"- {c['combo']}: "
                        f"样本={c['sample_count']:,}, "
                        f"逾期率={c['bad_rate']*100:.2f}%, "
                        f"Lift={c['lift']:.2f}"
                    )
            
            sections.append('\n'.join(lines))
    
    # ── 分箱数据 ──
    all_results = analysis_data.get('all_results', [])
    if all_results and len(all_results) > 0:
        # 取第一个模型的分箱数据作为示例
        bins = all_results[0].get('bins', [])
        if bins:
            model_name = all_results[0].get('model_name', all_results[0].get('model', '模型1'))
            lines = [f'## 分箱示例（{model_name}）']
            for b in bins:
                bad_r = b.get('bad_rate', 0)
                if isinstance(bad_r, str):
                    bad_r = float(bad_r.replace('%', '')) / 100 if '%' in bad_r else float(bad_r)
                lines.append(
                    f"- 箱 {b.get('bin', '?')}: "
                    f"样本={b.get('count', 0)}, 逾期率={bad_r:.4f}, "
                    f"坏样本={b.get('bad_count', 0)}"
                )
            sections.append('\n'.join(lines))

    if not sections:
        return ''

    return '\n\n'.join(sections)


def _parse_llm_suggestions(raw: str) -> List[Dict]:
    """从 LLM 回复中解析 JSON 建议列表"""
    import re

    content = raw.strip()

    # 去掉可能的 markdown 代码块包裹
    content = re.sub(r'^```json\s*', '', content)
    content = re.sub(r'^```\s*', '', content)
    content = re.sub(r'\s*```$', '', content)
    content = content.strip()

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        # 尝试提取第一个 [ ... ] 数组
        match = re.search(r'\[.*\]', content, re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group())
            except json.JSONDecodeError:
                return []
        else:
            return []

    if not isinstance(parsed, list):
        return []

    suggestions = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        title = str(item.get('title', item.get('heading', ''))).strip()
        content_text = str(item.get('content', item.get('text', item.get('description', '')))).strip()
        level = str(item.get('level', 'info')).strip()
        details = str(item.get('details', item.get('action', ''))).strip()

        if not title and not content_text:
            continue

        # 规范化 level
        if level not in ('info', 'warning', 'success', 'danger', 'primary', 'strategy', 'performance', 'business', 'quality', 'sort'):
            level = 'info'

        suggestions.append({
            'type': level,
            'title': title,
            'content': content_text,
            'details': details,
        })

    # 去重
    suggestions = _deduplicate_suggestions(suggestions)
    return suggestions


def _extract_suggestions_from_text(text: str) -> List[Dict]:
    """当 JSON 解析失败时，从文本中提取建议"""
    import re

    suggestions = []
    # 尝试匹配 "标题：内容" 模式
    # 常见模式：数字编号 / emoji开头 / #### 标题
    blocks = re.split(r'\n(?=\d+[.、)）]|(?=[📊📈🚨⚠️✅🎯💡📌🚀]))', text)

    for block in blocks:
        block = block.strip()
        if len(block) < 20:
            continue

        # 提取标题（第一行）
        lines = block.split('\n', 1)
        title = lines[0].strip()
        content = lines[1].strip() if len(lines) > 1 else ''

        # 去掉编号前缀
        title = re.sub(r'^\d+[.、)）]\s*', '', title)

        if title:
            # 简单的情绪判断
            level = 'info'
            if any(w in title for w in ['🚨', '警告', '危险', '高企', '偏高', '严重']):
                level = 'danger'
            elif any(w in title for w in ['⚠️', '注意', '关注', '待优化', '风险']):
                level = 'warning'
            elif any(w in title for w in ['✅', '良好', '健康', '优秀']):
                level = 'success'

            suggestions.append({
                'type': level,
                'title': title[:80],
                'content': content[:500],
                'details': '',
            })

    # 去重
    suggestions = _deduplicate_suggestions(suggestions)
    return suggestions[:10]  # 最多 10 条


def _deduplicate_suggestions(suggestions: List[Dict]) -> List[Dict]:
    """
    建议去重
    基于 title 和 content 的相似度进行去重
    """
    import re
    
    if not suggestions:
        return []
    
    seen_titles = []
    seen_title_norms = []
    result = []
    
    for s in suggestions:
        title = s.get('title', '')
        content = s.get('content', '')
        
        # 标题归一化：去掉emoji、前后空格、转小写
        title_normalized = _normalize_text(title)
        
        # 检查是否与已有标题相似
        is_duplicate = False
        
        # 1. 精确匹配
        if title_normalized in [n for n, _ in seen_title_norms]:
            is_duplicate = True
        
        # 2. 检查核心关键词是否相同（去掉"建议"、"策略"等修饰词）
        if not is_duplicate:
            # 提取核心关键词
            core_words = _extract_core_keywords(title_normalized)
            for _, existing_cores in seen_title_norms:
                if core_words and existing_cores:
                    # 核心词完全相同
                    if core_words == existing_cores:
                        is_duplicate = True
                        break
                    # 核心词重叠超过60%
                    overlap = len(core_words & existing_cores) / max(len(core_words), len(existing_cores))
                    if overlap >= 0.6:
                        is_duplicate = True
                        break
        
        # 3. 内容相似度检查（更严格）
        if not is_duplicate and content:
            content_normalized = _normalize_text(content)
            # 取核心内容（前150字符去掉标点后的内容）
            content_core = re.sub(r'[^\w\u4e00-\u9fff]', '', content_normalized)[:150]
            for existing in result:
                existing_content = existing.get('content', '')
                existing_core = re.sub(r'[^\w\u4e00-\u9fff]', '', _normalize_text(existing_content))[:150]
                # 内容核心部分相同超过80个字符
                if len(content_core) > 20 and len(existing_core) > 20:
                    if content_core in existing_core or existing_core in content_core:
                        is_duplicate = True
                        break
        
        if not is_duplicate:
            core_words = _extract_core_keywords(title_normalized)
            seen_title_norms.append((title_normalized, core_words))
            result.append(s)
    
    return result


def _extract_core_keywords(title_normalized: str) -> set:
    """提取标题的核心关键词（去掉常见修饰词）"""
    # 常见修饰词
    stop_words = {
        '建议', '策略', '分析', '优化', '调整', '建议对', '建议对进行',
        '持续', '监控', '建议定期', '定期', '每周', '每月',
        '对于', '基于', '根据', '结合', '通过',
        '1', '2', '3', '4', '5', '一', '二', '三', '四', '五',
        '的', '了', '和', '与', '或', '等', '各', '该',
        '进行', '开展', '实施', '执行', '落实',
    }
    words = set(title_normalized.split())
    core = words - stop_words
    return core if core else words


def _normalize_text(text: str) -> str:
    """归一化文本用于比较"""
    import re
    # 去掉emoji
    text = re.sub(r'[\U00010000-\U0010ffff]', '', text)
    # 去掉特殊符号
    text = re.sub(r'[📊📈🚨⚠️✅🎯💡📌🚀🔥⚡🎉🏆🔍💰📋⚖️✨🎯👥📱🔧📉⚡]', '', text)
    # 转小写，去前后空格
    text = text.lower().strip()
    # 多个空格变一个
    text = re.sub(r'\s+', ' ', text)
    return text


# ════════════════════════════════════════════════════════════════════════════════
# 规则引擎（快速兜底）
# ════════════════════════════════════════════════════════════════════════════════

def generate_suggestion(metrics: dict, bins: List[Dict]) -> List[Dict]:
    """
    规则引擎快速建议（LLM 失败时的兜底方案）

    返回建议列表，每条建议包含：
    - level: warning/info/success
    - category: 模型效果/稳定性/业务指标/策略建议
    - title: 建议标题
    - content: 建议内容
    - action: 建议操作
    """
    suggestions = []

    ks  = metrics.get('ks', 0)
    auc = metrics.get('auc', 0)
    psi = metrics.get('psi', 0)
    bad_rate = metrics.get('bad_rate', 0)

    # ── 模型效果评估 ─────────────────────────────────────────────────────────
    if ks < THRESHOLDS['ks_warning']:
        suggestions.append({
            'level':    'warning',
            'category': '模型效果',
            'title':    'KS 值偏低，模型区分度不足',
            'content':  f'当前 KS = {ks:.4f}，低于警戒值 {THRESHOLDS["ks_warning"]}，模型对好坏样本的区分能力较弱。',
            'action':   '建议：1) 检查特征工程，引入更多强区分特征；2) 考虑更换模型算法或调整超参数；3) 分析样本分布是否存在偏移。',
        })
    elif ks < THRESHOLDS['ks_good']:
        suggestions.append({
            'level':    'info',
            'category': '模型效果',
            'title':    'KS 值中等，有优化空间',
            'content':  f'当前 KS = {ks:.4f}，处于中等水平，模型有一定区分能力但仍有提升空间。',
            'action':   '建议：1) 尝试特征组合或交叉特征；2) 对高分段/低分段进行精细化建模；3) 定期监控模型衰减情况。',
        })
    else:
        suggestions.append({
            'level':    'success',
            'category': '模型效果',
            'title':    'KS 值良好',
            'content':  f'当前 KS = {ks:.4f}，模型区分度良好。',
            'action':   '建议：继续保持，定期监控模型稳定性。',
        })

    # AUC 评估
    if auc < THRESHOLDS['auc_warning']:
        suggestions.append({
            'level':    'warning',
            'category': '模型效果',
            'title':    'AUC 偏低',
            'content':  f'当前 AUC = {auc:.4f}，模型排序能力较弱。',
            'action':   '建议：结合 KS 分析，若两者均低，需重点优化模型；若 AUC 低但 KS 正常，检查分数分布是否过于集中。',
        })

    # ── 稳定性评估 ───────────────────────────────────────────────────────────
    if psi > THRESHOLDS['psi_warning']:
        suggestions.append({
            'level':    'warning',
            'category': '稳定性',
            'title':    'PSI 过高，模型稳定性差',
            'content':  f'当前 PSI = {psi:.4f}，超过警戒值 {THRESHOLDS["psi_warning"]}，分数分布发生显著变化。',
            'action':   '建议：1) 排查近期是否有数据源变更或外部环境影响；2) 进行变量稳定性分析（CSI）；3) 考虑模型重训练或校准。',
        })
    elif psi > THRESHOLDS['psi_good']:
        suggestions.append({
            'level':    'info',
            'category': '稳定性',
            'title':    'PSI 略高，需关注',
            'content':  f'当前 PSI = {psi:.4f}，分数分布有轻微偏移。',
            'action':   '建议：持续监控，若趋势持续上升需采取干预措施。',
        })
    else:
        suggestions.append({
            'level':    'success',
            'category': '稳定性',
            'title':    'PSI 正常',
            'content':  f'当前 PSI = {psi:.4f}，模型稳定性良好。',
            'action':   '建议：继续保持监控频率。',
        })

    # ── 业务指标评估 ─────────────────────────────────────────────────────────
    if bad_rate > THRESHOLDS['bad_rate_high']:
        suggestions.append({
            'level':    'warning',
            'category': '业务指标',
            'title':    '逾期率偏高',
            'content':  f'当前逾期率 {bad_rate:.2%}，超过警戒线 {THRESHOLDS["bad_rate_high"]:.0%}。',
            'action':   '建议：1) 收紧审批策略，提高准入门槛；2) 加强贷中监控和预警；3) 排查近期是否有高风险客群涌入。',
        })
    elif bad_rate < THRESHOLDS['bad_rate_low']:
        suggestions.append({
            'level':    'info',
            'category': '业务指标',
            'title':    '逾期率较低',
            'content':  f'当前逾期率 {bad_rate:.2%}，资产质量良好。',
            'action':   '建议：可适当放宽策略，提升业务规模，但需平衡风险与收益。',
        })

    # ── 分箱策略建议 ─────────────────────────────────────────────────────────
    if bins:
        # 检查单调性
        bad_rates = [b['bad_rate'] for b in bins]
        is_monotonic = all(bad_rates[i] <= bad_rates[i+1] for i in range(len(bad_rates)-1)) or \
                       all(bad_rates[i] >= bad_rates[i+1] for i in range(len(bad_rates)-1))

        if not is_monotonic:
            suggestions.append({
                'level':    'warning',
                'category': '策略建议',
                'title':    '分箱单调性异常',
                'content':  '各分数段逾期率未呈现单调趋势，可能存在策略漏洞或异常客群。',
                'action':   '建议：1) 检查中间分数段是否有特殊客群；2) 考虑调整分箱方式或策略阈值；3) 对非单调区间进行深度分析。',
            })

        # 首尾段分析
        first_bin = bins[0]
        last_bin  = bins[-1]
        lift = last_bin['bad_rate'] / first_bin['bad_rate'] if first_bin['bad_rate'] > 0 else 0

        if lift < 2:
            suggestions.append({
                'level':    'info',
                'category': '策略建议',
                'title':    'Lift 较低',
                'content':  f'最高分箱逾期率 {last_bin["bad_rate"]:.2%} 与最低分箱 {first_bin["bad_rate"]:.2%} 差异较小（Lift={lift:.2f}）。',
                'action':   '建议：1) 优化模型以拉开好坏样本分数差距；2) 调整策略阈值，提高区分度。',
            })
        else:
            suggestions.append({
                'level':    'success',
                'category': '策略建议',
                'title':    'Lift 良好',
                'content':  f'最高分箱与最低分箱逾期率差异明显（Lift={lift:.2f}），策略区分度较好。',
                'action':   '建议：可基于分箱结果制定差异化策略（如不同额度、利率）。',
            })

    return suggestions
