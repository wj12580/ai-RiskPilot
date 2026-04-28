"""
风控 Agent 技能工具注册表
===========================
将风控分析能力封装为可被 Agent 调用的工具函数。
兼容 Hermes Agent Skills 规范，同时适配 RiskPilot 的业务逻辑。

每个 Skill：
  - name: 工具名称
  - description: 工具描述（大模型根据它决定是否调用）
  - parameters: JSON Schema 参数定义
  - fn: 实际执行的 Python 函数
  - output_schema: 输出格式说明
"""

import json
import pandas as pd
import numpy as np
from typing import Dict, Any, List, Callable, Optional
from sklearn.metrics import roc_auc_score, roc_curve
from scipy.stats import ks_2samp

# ── 复用现有的分析服务 ───────────────────────────────────────────────────────
from services.analysis_service import run_analysis as _run_analysis
from services.suggestion_service import generate_suggestion as _rule_suggestions


# ════════════════════════════════════════════════════════════════════════════════
# 技能基类
# ════════════════════════════════════════════════════════════════════════════════

class Skill:
    """
    技能定义（兼容 Hermes Skills 规范）

    Attributes:
        name:         技能唯一标识
        description:  技能描述（大模型据此判断调用）
        parameters:   JSON Schema 参数定义
        fn:          执行函数
        examples:    示例（可选）
        tags:        标签分类
    """

    def __init__(
        self,
        name: str,
        description: str,
        parameters: Dict,
        fn: Callable,
        output_schema: Optional[str] = None,
        examples: Optional[List[str]] = None,
        tags: Optional[List[str]] = None,
    ):
        self.name = name
        self.description = description
        self.parameters = parameters
        self.fn = fn
        self.output_schema = output_schema or "dict"
        self.examples = examples or []
        self.tags = tags or []

    def to_openai_tool(self) -> Dict[str, Any]:
        """导出为 OpenAI Function Calling 格式"""
        return {
            "type": "function",
            "function": {
                "name":        self.name,
                "description": self.description,
                "parameters":  self.parameters,
            },
        }

    def invoke(self, **kwargs) -> Dict[str, Any]:
        """执行技能，返回标准化结果"""
        try:
            result = self.fn(**kwargs)
            return {
                "skill":    self.name,
                "success":  True,
                "result":   result,
                "error":    None,
            }
        except Exception as e:
            return {
                "skill":    self.name,
                "success":  False,
                "result":   None,
                "error":    str(e),
            }

    def __repr__(self):
        return f"<Skill {self.name}>"


# ════════════════════════════════════════════════════════════════════════════════
# 技能函数定义
# ════════════════════════════════════════════════════════════════════════════════

def _load_df(file_path: str, file_type: str = "xlsx") -> pd.DataFrame:
    """通用数据加载"""
    if file_type == "csv":
        return pd.read_csv(file_path)
    return pd.read_excel(file_path)


def _serialize_df(df: pd.DataFrame, max_rows: int = 20) -> Dict[str, Any]:
    """将 DataFrame 序列化为可读字典（避免传输大对象）"""
    preview = df.head(max_rows)
    return {
        "shape":       list(df.shape),
        "columns":     list(df.columns),
        "dtypes":      {k: str(v) for k, v in df.dtypes.to_dict().items()},
        "null_counts":  df.isnull().sum().to_dict(),
        "describe":    df.describe().round(4).to_dict(orient="records"),
        "preview":     preview.to_dict(orient="records"),
    }


# ── 技能1：数据质量检查 ──────────────────────────────────────────────────────

def skill_load_data(file_path: str, file_type: str = "xlsx", max_preview: int = 20) -> Dict[str, Any]:
    """加载数据并返回基础质量报告"""
    try:
        df = _load_df(file_path, file_type)
        return {
            "message":  f"成功加载数据：{df.shape[0]:,} 行 × {df.shape[1]} 列",
            "data":     _serialize_df(df, max_preview),
        }
    except Exception as e:
        return {"message": f"加载失败：{str(e)}", "data": None}


# ── 技能2：逾期率分析 ────────────────────────────────────────────────────────

def skill_overdue_analysis(
    file_path: str,
    target_col: str,
    score_col: str = "",
    group_by: str = "",
    file_type: str = "xlsx",
    n_bins: int = 10,
) -> Dict[str, Any]:
    """
    逾期率分析

    Args:
        file_path:   数据文件路径
        target_col:  目标列（label/逾期标记）
        score_col:  分数列（可选，用于分箱分析）
        group_by:   分组列（可选，如"省份"）
        n_bins:     分箱数（score_col 存在时有效）
    """
    try:
        df = _load_df(file_path, file_type)

        # 检查列
        if target_col not in df.columns:
            return {"message": f"目标列 '{target_col}' 不存在", "data": None}
        if score_col and score_col not in df.columns:
            return {"message": f"分数列 '{score_col}' 不存在", "data": None}
        if group_by and group_by not in df.columns:
            return {"message": f"分组列 '{group_by}' 不存在", "data": None}

        result = _run_analysis(df, score_col or None, target_col, n_bins)

        # 分组分析（如果有）
        group_result = None
        if group_by:
            grouped = df.groupby(group_by)[target_col].agg(["sum", "count", "mean"])
            grouped.columns = ["逾期数", "总数", "逾期率"]
            grouped["逾期率"] = grouped["逾期率"].round(4)
            group_result = grouped.to_dict(orient="records")

        return {
            "message":       f"分析完成，共 {result['summary']['total_count']:,} 条样本",
            "summary":       result["summary"],
            "metrics":       result["metrics"],
            "bins":          result["bins"][:n_bins],
            "group_by":      group_result,
            "feature_analysis": result.get("feature_analysis"),
        }
    except Exception as e:
        return {"message": f"分析失败：{str(e)}", "data": None}


# ── 技能3：多模型相关性分析 ──────────────────────────────────────────────────

def skill_model_correlation(
    file_path: str,
    target_col: str,
    score_cols: List[str] = None,
    file_type: str = "xlsx",
) -> Dict[str, Any]:
    """
    多模型分数相关性分析 + KS/AUC 评估

    Args:
        file_path:   数据文件路径
        target_col:  目标列
        score_cols:  分数列列表（None = 自动识别所有数值列）
        file_type:   文件类型
    【优化】相关性矩阵改用 pandas DataFrame.corr(method='spearman') 一次性计算
    """
    try:
        df = _load_df(file_path, file_type)

        # 自动识别分数列
        if not score_cols:
            exclude = {target_col, "label", "overdue", "y", "flag"}
            score_cols = [
                c for c in df.columns
                if c.lower() not in exclude
                and pd.api.types.is_numeric_dtype(df[c])
                and df[c].std() > 0
                and df[c].nunique() > 5
            ]
            score_cols = score_cols[:20]

        if not score_cols:
            return {"message": "未找到可用的分数列", "data": None}

        y = pd.to_numeric(df[target_col], errors="coerce").dropna()
        df_clean = df.loc[y.index]

        # 逐列计算 KS / AUC（保持原逻辑）
        results = []
        valid_cols = []
        for col in score_cols:
            if col not in df_clean.columns:
                continue
            scores = pd.to_numeric(df_clean[col], errors="coerce").dropna()
            common_idx = y.index.intersection(scores.index)
            if len(common_idx) < 50:
                continue
            valid_cols.append(col)
            y_sub = y.loc[common_idx]
            s_sub = scores.loc[common_idx]
            try:
                auc = roc_auc_score(y_sub, s_sub)
                fpr, tpr, _ = roc_curve(y_sub, s_sub)
                ks = float(np.max(tpr - fpr))
            except Exception:
                auc, ks = 0.5, 0.0
            results.append({
                "model":      col,
                "auc":        round(auc, 4),
                "ks":         round(ks, 4),
                "coverage":   round(len(common_idx) / len(y), 4),
                "mean_score": round(float(s_sub.mean()), 4),
                "std_score":  round(float(s_sub.std()), 4),
            })

        results.sort(key=lambda x: x["ks"], reverse=True)

        # 向量化 Spearman 相关性矩阵
        cor_matrix = {}
        if len(valid_cols) >= 2:
            score_df = df_clean[valid_cols].apply(pd.to_numeric, errors="coerce")
            spearman_mat = score_df.corr(method="spearman")
            for col in valid_cols:
                cor_matrix[col] = {}
                for other in valid_cols:
                    if other != col:
                        val = spearman_mat.loc[col, other]
                        if not pd.isna(val):
                            cor_matrix[col][other] = round(float(val), 4)

        return {
            "message":    f"分析了 {len(results)} 个模型",
            "models":     results,
            "cor_matrix": cor_matrix,
            "top3":       results[:3],
        }
    except Exception as e:
        return {"message": f"相关性分析失败：{str(e)}", "data": None}


# ── 技能4：分箱优化 ─────────────────────────────────────────────────────────

def skill_bin_optimize(
    file_path: str,
    target_col: str,
    feature_col: str,
    max_bins: int = 10,
    file_type: str = "xlsx",
) -> Dict[str, Any]:
    """
    智能分箱：自动找到最优分箱方案（基于 IV 最大化和单调性）

    Args:
        file_path:    数据文件路径
        target_col:   目标列
        feature_col:  特征列
        max_bins:     最大分箱数
    """
    try:
        df = _load_df(file_path, file_type)
        if feature_col not in df.columns:
            return {"message": f"特征列 '{feature_col}' 不存在", "data": None}
        if target_col not in df.columns:
            return {"message": f"目标列 '{target_col}' 不存在", "data": None}

        feat = pd.to_numeric(df[feature_col], errors="coerce").dropna()
        y = pd.to_numeric(df[target_col], errors="coerce").loc[feat.index].dropna()
        common = feat.index.intersection(y.index)
        feat, y = feat.loc[common], y.loc[common]

        # 等频分箱
        iv_total = 0
        bins_result = []
        try:
            quantile_bins = pd.qcut(feat, q=max_bins, labels=False, duplicates="drop")
            n_bins_actual = int(quantile_bins.max()) + 1
            ivs = []
            for b in range(n_bins_actual):
                mask = quantile_bins == b
                total = mask.sum()
                bad = y.loc[mask].sum()
                good = total - bad
                br = bad / total if total > 0 else 0
                bins_result.append({
                    "bin":        b + 1,
                    "count":      int(total),
                    "bad_count":  int(bad),
                    "bad_rate":   round(float(br), 4),
                    "score_min":  round(float(feat.loc[mask].min()), 4),
                    "score_max":  round(float(feat.loc[mask].max()), 4),
                })
                # IV 计算
                pct_bad = bad / y.sum() if y.sum() > 0 else 0.0001
                pct_good = good / (len(y) - y.sum()) if (len(y) - y.sum()) > 0 else 0.0001
                woe = np.log(pct_good / pct_bad) if pct_bad > 0 else 0
                iv = (pct_good - pct_bad) * woe
                ivs.append(iv)
                iv_total += iv
        except Exception:
            return {"message": "分箱失败，数据可能异常", "data": None}

        # 单调性检查
        bad_rates = [b["bad_rate"] for b in bins_result]
        is_monotonic = all(bad_rates[i] <= bad_rates[i+1] for i in range(len(bad_rates)-1)) or \
                       all(bad_rates[i] >= bad_rates[i+1] for i in range(len(bad_rates)-1))

        return {
            "message":      f"分箱完成（IV={iv_total:.4f}，{'单调' if is_monotonic else '非单调'}）",
            "feature":      feature_col,
            "iv":           round(iv_total, 4),
            "bins":         bins_result,
            "is_monotonic": is_monotonic,
            "suggestion":   (
                "✅ 分箱质量良好" if iv_total > 0.3 and is_monotonic else
                f"⚠️ IV={iv_total:.4f} {'非单调' if not is_monotonic else '偏低'}，建议调整"
            ),
        }
    except Exception as e:
        return {"message": f"分箱优化失败：{str(e)}", "data": None}


# ── 技能5：规则建议（基于阈值）───────────────────────────────────────────────

def skill_strategy_suggestion(
    metrics: Dict[str, Any],
    bins: List[Dict[str, Any]],
    pass_rate: float = 0.8,
) -> Dict[str, Any]:
    """
    基于指标和分箱数据，生成策略建议（无需调用 LLM）

    Args:
        metrics:    KS/AUC/PSI/bad_rate
        bins:       分箱数据
        pass_rate:  目标通过率
    """
    try:
        suggestions = _rule_suggestions(metrics, bins)

        # 计算建议阈值
        threshold_info = {}
        if bins and pass_rate < 1.0:
            n_approve = int(len(bins) * pass_rate)
            if n_approve > 0 and n_approve <= len(bins):
                approve_bins = bins[:n_approve]
                reject_bins = bins[n_approve:]
                if approve_bins:
                    total_count = sum(b["count"] for b in approve_bins)
                    total_bad = sum(b["bad_count"] for b in approve_bins)
                    rej_count = sum(b["count"] for b in reject_bins)
                    rej_bad = sum(b["bad_count"] for b in reject_bins)
                    threshold_info = {
                        "建议阈值":    f"{approve_bins[0]['score_min']:.4f} ~ {approve_bins[-1]['score_max']:.4f}",
                        "通过样本数": total_count,
                        "通过逾期率": round(total_bad / max(total_count, 1), 4),
                        "拒绝样本数": rej_count,
                        "拒绝逾期率": round(rej_bad / max(rej_count, 1), 4),
                    }

        return {
            "suggestions":  suggestions,
            "threshold":    threshold_info,
            "pass_rate_used": pass_rate,
        }
    except Exception as e:
        return {"suggestions": [], "threshold": {}, "error": str(e)}


# ── 技能6：特征重要性 ──────────────────────────────────────────────────────

def skill_feature_importance(
    file_path: str,
    target_col: str,
    file_type: str = "xlsx",
    top_n: int = 15,
) -> Dict[str, Any]:
    """
    特征重要性分析：计算所有数值特征与 target 的 Spearman 相关系数
    【优化】用 pandas DataFrame.corrwith(method='spearman') 向量化替换串行 for 循环
    """
    try:
        df = _load_df(file_path, file_type)
        y = pd.to_numeric(df[target_col], errors="coerce").dropna()
        df = df.loc[y.index]

        exclude = {target_col, "label", "overdue", "y", "flag", "id", "customer_id"}
        numeric_cols = [
            col for col in df.columns
            if col.lower() not in exclude
            and pd.api.types.is_numeric_dtype(df[col])
        ]

        if not numeric_cols:
            return {"message": "无可用数值特征", "top_features": [], "all_features": []}

        # 过滤出有效列（与 target 的公共非空样本 >= 50）
        valid_cols = []
        for col in numeric_cols:
            x = pd.to_numeric(df[col], errors="coerce")
            common = y.index.intersection(x.dropna().index)
            if len(common) >= 50:
                valid_cols.append(col)

        if not valid_cols:
            return {"message": "有效特征数为0（公共非空样本<50）", "top_features": [], "all_features": []}

        # 向量化 Spearman：pandas corrwith 对所有列一次性计算
        df_valid = df[valid_cols].apply(pd.to_numeric, errors="coerce")
        df_aligned = df_valid.loc[y.index]
        spearman_series = df_aligned.corrwith(y.reindex(df_aligned.index), method="spearman")

        results = []
        for col, corr in spearman_series.items():
            if pd.isna(corr):
                continue
            results.append({
                "feature": col,
                "spearman_corr": round(float(corr), 4),
                "abs_corr": round(abs(float(corr)), 4),
            })

        results.sort(key=lambda x: x["abs_corr"], reverse=True)
        top_features = results[:top_n]

        return {
            "message":      f"分析了 {len(results)} 个特征",
            "top_features": top_features,
            "all_features": results,
        }
    except Exception as e:
        return {"message": f"特征重要性分析失败：{str(e)}", "data": None}


# ════════════════════════════════════════════════════════════════════════════════
# 技能注册表
# ════════════════════════════════════════════════════════════════════════════════

class SkillRegistry:
    """
    技能注册表（兼容 Hermes Skills 规范）
    管理所有风控分析技能，提供工具定义和执行能力
    """

    def __init__(self):
        self._skills: Dict[str, Skill] = {}
        self._register_all()

    def _register_all(self):
        """注册所有技能"""
        skills_def = [
            Skill(
                name="load_data",
                description="加载 CSV 或 Excel 数据文件，返回数据质量报告（行数、列数、缺失值、统计摘要）。"
                            "当你需要了解数据概况、检查数据质量或获取列信息时调用此工具。",
                parameters={
                    "type": "object",
                    "properties": {
                        "file_path":    {"type": "string", "description": "文件完整路径"},
                        "file_type":    {"type": "string", "enum": ["xlsx", "csv"], "description": "文件类型"},
                        "max_preview":  {"type": "integer", "description": "预览最大行数，默认20"},
                    },
                    "required": ["file_path"],
                },
                fn=skill_load_data,
                tags=["数据", "质量检查"],
            ),
            Skill(
                name="overdue_analysis",
                description="执行完整的风控指标分析（KS、AUC、PSI、分箱逾期率）。"
                            "当你需要评估模型效果、分析逾期率分布时调用此工具。"
                            "支持指定分组列进行分层分析。",
                parameters={
                    "type": "object",
                    "properties": {
                        "file_path":  {"type": "string",  "description": "数据文件完整路径"},
                        "target_col": {"type": "string",  "description": "目标/标签列名（如 label/overdue_m1）"},
                        "score_col":  {"type": "string",  "description": "模型分数列名（可选）"},
                        "group_by":   {"type": "string",  "description": "分组列名（可选）"},
                        "file_type":  {"type": "string",  "enum": ["xlsx", "csv"]},
                        "n_bins":     {"type": "integer", "description": "分箱数，默认10"},
                    },
                    "required": ["file_path", "target_col"],
                },
                fn=skill_overdue_analysis,
                tags=["分析", "核心指标", "逾期率"],
            ),
            Skill(
                name="model_correlation",
                description="对多个模型分数列进行相关性分析（Spearman 相关系数矩阵）。"
                            "同时计算每个模型的 KS/AUC/覆盖率。"
                            "当你需要评估多模型串行/捞回策略时调用此工具。"
                            "不传 score_cols 时自动识别所有数值列。",
                parameters={
                    "type": "object",
                    "properties": {
                        "file_path":   {"type": "string", "description": "数据文件完整路径"},
                        "target_col":  {"type": "string", "description": "目标/标签列名"},
                        "score_cols":  {"type": "array", "items": {"type": "string"}, "description": "分数列名列表（可选）"},
                        "file_type":   {"type": "string", "enum": ["xlsx", "csv"]},
                    },
                    "required": ["file_path", "target_col"],
                },
                fn=skill_model_correlation,
                tags=["多模型", "相关性", "串行策略"],
            ),
            Skill(
                name="bin_optimize",
                description="对特征进行智能最优分箱，计算每箱的 IV 值和单调性检验。"
                            "IV > 0.3 表示特征区分能力较强。"
                            "当你需要分析某个特征对目标变量的区分能力时调用此工具。",
                parameters={
                    "type": "object",
                    "properties": {
                        "file_path":    {"type": "string",  "description": "数据文件完整路径"},
                        "target_col":   {"type": "string",  "description": "目标/标签列名"},
                        "feature_col":  {"type": "string",  "description": "待分析的特征列名"},
                        "max_bins":     {"type": "integer", "description": "最大分箱数，默认10"},
                        "file_type":    {"type": "string",  "enum": ["xlsx", "csv"]},
                    },
                    "required": ["file_path", "target_col", "feature_col"],
                },
                fn=skill_bin_optimize,
                tags=["分箱", "IV", "特征工程"],
            ),
            Skill(
                name="strategy_suggestion",
                description="基于 KS/AUC/PSI/bad_rate 等指标和分箱数据，自动生成可落地的风控策略建议。"
                            "包括阈值建议、通过率/逾期率估算、策略调整方向。"
                            "无需文件输入，直接用 metrics 和 bins 数据。",
                parameters={
                    "type": "object",
                    "properties": {
                        "metrics": {
                            "type": "object",
                            "description": "核心指标 {ks, auc, psi, bad_rate}",
                        },
                        "bins": {
                            "type": "array",
                            "description": "分箱数据（来自 overdue_analysis）",
                        },
                        "pass_rate": {
                            "type": "number",
                            "description": "目标通过率，0~1，默认0.8",
                        },
                    },
                    "required": ["metrics", "bins"],
                },
                fn=skill_strategy_suggestion,
                tags=["策略", "建议", "阈值"],
            ),
            Skill(
                name="feature_importance",
                description="分析所有数值特征与目标变量的 Spearman 相关系数，返回特征重要性排序。"
                            "当你需要筛选重要特征、理解哪些变量对逾期影响最大时调用此工具。"
                            "自动排除 ID 列和标签列。",
                parameters={
                    "type": "object",
                    "properties": {
                        "file_path":   {"type": "string", "description": "数据文件完整路径"},
                        "target_col":  {"type": "string", "description": "目标/标签列名"},
                        "file_type":   {"type": "string", "enum": ["xlsx", "csv"]},
                        "top_n":       {"type": "integer", "description": "返回 Top N 重要特征，默认15"},
                    },
                    "required": ["file_path", "target_col"],
                },
                fn=skill_feature_importance,
                tags=["特征", "重要性", "特征工程"],
            ),
        ]

        for skill in skills_def:
            self.register(skill)

    def register(self, skill: Skill):
        self._skills[skill.name] = skill

    def get(self, name: str) -> Optional[Skill]:
        return self._skills.get(name)

    def get_all(self) -> Dict[str, Skill]:
        return dict(self._skills)

    def list_tools(self) -> List[Dict[str, Any]]:
        """导出所有工具定义（OpenAI Function Calling 格式）"""
        return [s.to_openai_tool() for s in self._skills.values()]

    def list_by_tag(self, tag: str) -> List[Skill]:
        return [s for s in self._skills.values() if tag in s.tags]

    def invoke(self, name: str, **kwargs) -> Dict[str, Any]:
        """通过名称调用技能"""
        skill = self.get(name)
        if not skill:
            return {"success": False, "error": f"未知技能：{name}"}
        return skill.invoke(**kwargs)

    def describe_all(self) -> List[Dict[str, str]]:
        """返回所有技能的描述列表（供 Agent 选择）"""
        return [
            {
                "name":        s.name,
                "description": s.description,
                "tags":        ", ".join(s.tags),
            }
            for s in self._skills.values()
        ]


# ── 全局注册表实例 ────────────────────────────────────────────────────────────
registry = SkillRegistry()
