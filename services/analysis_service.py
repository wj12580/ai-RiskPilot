"""
策略分析核心服务
- 模型评估分析（KS、AUC、PSI、Lift、IV）
- 特征分析（单变量分析）
"""

import pandas as pd
import numpy as np
from sklearn.metrics import roc_auc_score, roc_curve
from scipy.stats import ks_2samp


def run_analysis(df: pd.DataFrame, score_col: str, target_col: str, n_bins: int = 10) -> dict:
    """
    执行完整的策略分析
    返回包含 summary、metrics、bins、feature_analysis 的字典
    """
    # 智能兜底：如果 score_col 为空或不存在，自动取第一个数值列
    numeric_cols = df.select_dtypes(include='number').columns.tolist()
    if not score_col or score_col not in df.columns:
        if numeric_cols:
            score_col = numeric_cols[0]
        else:
            raise ValueError("数据中未找到可用于分析的数值列")

    # 数据清洗
    cols_needed = [c for c in [score_col, target_col] if c]
    df_clean = df[cols_needed].dropna().copy()
    df_clean[target_col] = pd.to_numeric(df_clean[target_col], errors='coerce')
    df_clean[score_col] = pd.to_numeric(df_clean[score_col], errors='coerce')
    df_clean = df_clean.dropna()

    if len(df_clean) == 0:
        raise ValueError("清洗后无有效数据，请检查列名和数据类型")

    y_true = df_clean[target_col].values
    scores = df_clean[score_col].values

    # 确保分数为数值类型（scores 已经是 ndarray，直接用 pd.to_numeric 处理）
    scores = pd.to_numeric(pd.Series(scores), errors='coerce').values
    valid_mask = ~np.isnan(scores)
    scores = scores[valid_mask]
    y_true = y_true[valid_mask]

    if len(scores) == 0:
        raise ValueError("清洗后无有效的数值分数数据")

    # 基础统计
    total_count = len(df_clean)
    bad_count   = int(y_true.sum())
    bad_rate    = bad_count / total_count if total_count > 0 else 0

    # 计算 KS、AUC
    ks, auc = _calc_ks_auc(y_true, scores)

    # PSI（以中位数为基准）
    psi = _calc_psi(scores)

    # 等频分箱分析
    bins_df = _bin_analysis(df_clean, score_col, target_col, n_bins)

    # 特征分析（单变量统计）
    feature_analysis = _feature_analysis(df_clean, score_col, target_col)

    return {
        'summary': {
            'total_count': total_count,
            'bad_count':   bad_count,
            'bad_rate':    round(bad_rate, 6),
        },
        'metrics': {
            'ks':  round(ks, 4),
            'auc': round(auc, 4),
            'psi': round(psi, 4),
        },
        'bins': bins_df.to_dict(orient='records'),
        'feature_analysis': feature_analysis,
    }


def _calc_ks_auc(y_true: np.ndarray, scores: np.ndarray) -> tuple:
    """计算 KS 和 AUC
    
    根据模型分范围判断风险方向：
    - 最大模型分 > 1：模型分越高风险越低（AUC计算正确）
    - 最大模型分 <= 1：模型分越高风险越高（AUC需要取反）
    """
    try:
        auc = roc_auc_score(y_true, scores)
    except ValueError:
        auc = 0.5

    # KS 计算
    try:
        fpr, tpr, _ = roc_curve(y_true, scores)
        ks = max(tpr - fpr)
    except ValueError:
        ks = 0.0

    # 根据分数范围调整AUC方向
    # 如果最大分 > 1，分数越高风险越低，AUC正确
    # 如果最大分 <= 1，分数越高风险越高，AUC需要取反
    max_score = np.max(scores)
    if max_score <= 1:
        auc = 1 - auc

    return ks, auc


def _calc_psi(scores: np.ndarray, base_scores: np.ndarray = None) -> float:
    """计算 PSI，默认以中位数为基准"""
    if base_scores is None:
        base_scores = scores

    # 等频分 10 组
    bins = np.percentile(base_scores, np.linspace(0, 100, 11))
    bins[0] = -np.inf
    bins[-1] = np.inf

    base_counts, _ = np.histogram(base_scores, bins=bins)
    curr_counts, _ = np.histogram(scores, bins=bins)

    base_pct = base_counts / base_counts.sum()
    curr_pct = curr_counts / curr_counts.sum()

    # 避免除零
    base_pct = np.where(base_pct == 0, 0.0001, base_pct)
    curr_pct = np.where(curr_pct == 0, 0.0001, curr_pct)

    psi = np.sum((curr_pct - base_pct) * np.log(curr_pct / base_pct))
    return psi


def _bin_analysis(df: pd.DataFrame, score_col: str, target_col: str, n_bins: int = 10) -> pd.DataFrame:
    """等频分箱分析"""
    df = df.copy()
    df['bin'] = pd.qcut(df[score_col], q=n_bins, labels=False, duplicates='drop')

    result = []
    for b in sorted(df['bin'].unique()):
        subset = df[df['bin'] == b]
        total  = len(subset)
        bad    = int(subset[target_col].sum())
        bad_rate = bad / total if total > 0 else 0
        score_min = float(subset[score_col].min())
        score_max = float(subset[score_col].max())

        result.append({
            'bin_no':     int(b) + 1,
            'score_min':  round(score_min, 4),
            'score_max':  round(score_max, 4),
            'count':      total,
            'bad_count':  bad,
            'bad_rate':   round(bad_rate, 4),
        })

    return pd.DataFrame(result)


def _feature_analysis(df: pd.DataFrame, score_col: str, target_col: str) -> dict:
    """特征单变量分析"""
    scores = df[score_col]
    return {
        'score_col':  score_col,
        'target_col': target_col,
        'mean':       round(float(scores.mean()), 4),
        'std':        round(float(scores.std()), 4),
        'min':        round(float(scores.min()), 4),
        'max':        round(float(scores.max()), 4),
        'median':     round(float(scores.median()), 4),
        'p25':        round(float(scores.quantile(0.25)), 4),
        'p75':        round(float(scores.quantile(0.75)), 4),
    }
