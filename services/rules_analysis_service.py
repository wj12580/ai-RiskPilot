"""
规则分析服务
分析规则分箱对应的首逾和lift，以及用户画像分析

功能：
1. 规则分箱分析（每个规则取值对应的首逾和lift）
2. 用户画像分析（好/坏用户群体的规则取值和组合）
"""

import pandas as pd
import numpy as np
import io
import base64
from typing import Dict, List, Any, Optional
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from sklearn.tree import DecisionTreeClassifier, _tree, plot_tree

PAIR_ANALYSIS_MAX_RULES = 14
PAIR_ANALYSIS_MAX_UNIQUE = 60
PAIR_ANALYSIS_MAX_PAIRS = 91
PAIR_ANALYSIS_MIN_PAIR_SAMPLE = 30
PAIR_ANALYSIS_FALLBACK_MIN_PAIR_SAMPLE = 18
PAIR_ANALYSIS_FALLBACK_MAX_PAIRS = 45
TREE_MAX_RULES = 14
TREE_MAX_CATEGORY_VALUES = 16
TREE_MAX_DUMMY_FEATURES = 220


def _prepare_data(df: pd.DataFrame, target_col: str, rule_cols: List[str]) -> pd.DataFrame:
    """
    预处理数据，确保目标列和规则列都是数值类型
    返回处理后的数据副本
    """
    df = df.copy()
    
    # 确保目标列是数值类型（0/1）
    if target_col in df.columns:
        df[target_col] = pd.to_numeric(df[target_col], errors='coerce').fillna(0).astype(int)
    
    # 确保规则列是数值类型或标准标记类型
    for col in rule_cols:
        if col in df.columns:
            col_data = df[col]
            
            # 已经是数值类型，无需处理
            if pd.api.types.is_numeric_dtype(col_data):
                continue
            
            # 检查是否是 0/1 或 True/False 标记型
            unique_vals = col_data.dropna().unique()
            str_set = set(str(v) for v in unique_vals)
            binary_markers = {'0', '1', 'True', 'False', 'true', 'false', 'yes', 'no', 'Y', 'N', 'YES', 'NO'}
            
            if str_set.issubset(binary_markers):
                # 二值标记型：转换为 0/1
                df[col] = df[col].astype(str).isin(['1', 'True', 'true', 'yes', 'Y', 'YES']).astype(int)
            else:
                # 字符型或混合型：转换为字符串（保留用于分箱）
                df[col] = df[col].astype(str)
    
    return df


def run_rule_analysis(
    df: pd.DataFrame,
    rule_cols: List[str],
    target_col: str,
    score_col: Optional[str] = None,
) -> Dict[str, Any]:
    """
    执行完整的规则分析
    
    Args:
        df: 数据集（已通过风控规则的用户）
        rule_cols: 规则列列表
        target_col: 目标列（逾期标签）
        score_col: 可选的分数列
    
    Returns:
        包含各类分析结果的字典
    """
    # 预处理数据，确保类型正确
    df = _prepare_data(df, target_col, rule_cols)
    
    result = {
        'success': True,
        'data_summary': _get_data_summary(df, target_col),
        'rule_list': rule_cols,
    }
    
    # 1~3. 规则分箱 / 用户画像 / 决策树并发执行（互不依赖）
    task_defaults = {
        'rule_binning': {},
        'user_profile': {
            'overall_bad_rate': float(result['data_summary'].get('overall_bad_rate', 0.0)),
            'total_samples': int(result['data_summary'].get('total_samples', 0)),
            'good_samples': int(result['data_summary'].get('good_samples', 0)),
            'bad_samples': int(result['data_summary'].get('bad_samples', 0)),
            'good_rules': [],
            'bad_rules': [],
            'good_combinations': [],
            'bad_combinations': [],
        },
        'decision_tree': {'leaf_nodes': [], 'tree_image': ''},
    }
    task_functions = {
        'rule_binning': lambda: _analyze_rule_binning(df, rule_cols, target_col),
        'user_profile': lambda: _analyze_user_profile(df, rule_cols, target_col),
        'decision_tree': lambda: _build_rule_decision_tree(df, rule_cols, target_col),
    }

    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {
            key: executor.submit(func)
            for key, func in task_functions.items()
        }
        for key, future in futures.items():
            try:
                result[key] = future.result()
            except Exception:
                result[key] = task_defaults[key]
    
    # 4. 图表数据
    result['charts'] = _generate_chart_data(result)
    
    # 5. HTML报告
    result['summary_table'] = _generate_summary_table(result)
    
    # 6. 详细数据
    result['details'] = _generate_details(result)
    
    return result


def _get_data_summary(df: pd.DataFrame, target_col: str) -> Dict:
    """获取数据概要"""
    total = len(df)
    bad_count = df[target_col].sum() if target_col in df.columns else 0
    bad_rate = bad_count / total if total > 0 else 0
    
    return {
        'total_samples': total,
        'bad_samples': int(bad_count),
        'good_samples': int(total - bad_count),
        'overall_bad_rate': float(bad_rate),
        'overall_good_rate': float(1 - bad_rate),
    }


def _analyze_single_rule_binning(
    df: pd.DataFrame,
    rule_col: str,
    target_col: str,
    overall_bad_rate: float,
) -> tuple:
    """分析单个规则的分箱（供并发调用），返回 (rule_col, result_dict)"""
    col_data = df[rule_col]

    # 获取有效非空值（仅保留必要列，减少拷贝成本）
    valid_mask = col_data.notna() & (col_data != '') & (col_data != 'nan')
    valid_df = df.loc[valid_mask, [rule_col, target_col]].copy()

    if len(valid_df) == 0:
        return rule_col, {
            'rule_name': rule_col,
            'unique_count': 0,
            'bin_type': 'discrete',
            'is_numeric': pd.api.types.is_numeric_dtype(col_data),
            'bins': [],
        }

    unique_count = valid_df[rule_col].nunique()
    is_numeric = pd.api.types.is_numeric_dtype(col_data)

    if unique_count > 10:
        bin_result = _bin_continuous_variable(
            valid_df, rule_col, target_col, overall_bad_rate, n_bins=10, prefiltered=True
        )
    else:
        bin_result = _bin_discrete_variable(
            valid_df, rule_col, target_col, overall_bad_rate, prefiltered=True
        )

    return rule_col, {
        'rule_name': rule_col,
        'unique_count': unique_count,
        'bin_type': 'continuous' if unique_count > 10 else 'discrete',
        'is_numeric': is_numeric,
        'bins': bin_result,
    }


def _analyze_rule_binning(df: pd.DataFrame, rule_cols: List[str], target_col: str) -> Dict[str, List[Dict]]:
    """
    分析每个规则的分箱对应的首逾和lift
    - 规则取值数 > 10：连续型变量，做等频分箱（10箱）
    - 规则取值数 <= 10：离散变量，直接看每个值对应的首逾和lift
    支持数值型和字符型变量
    【优化】多线程并行处理各规则列，线程数取 min(8, cpu_count, len(rule_cols))
    """
    overall_bad_rate = df[target_col].mean() if target_col in df.columns else 0
    results = {}

    max_workers = min(12, len(rule_cols)) if rule_cols else 1
    if max_workers <= 1:
        # 单规则无需并发
        for rule_col in rule_cols:
            k, v = _analyze_single_rule_binning(df, rule_col, target_col, overall_bad_rate)
            results[k] = v
        return results

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(_analyze_single_rule_binning, df, rule_col, target_col, overall_bad_rate): rule_col
            for rule_col in rule_cols
        }
        for future in as_completed(futures):
            try:
                k, v = future.result()
                results[k] = v
            except Exception:
                rule_col = futures[future]
                results[rule_col] = {
                    'rule_name': rule_col,
                    'unique_count': 0,
                    'bin_type': 'discrete',
                    'is_numeric': False,
                    'bins': [],
                }

    return results


def _bin_continuous_variable(
    df: pd.DataFrame, 
    rule_col: str, 
    target_col: str, 
    overall_bad_rate: float,
    n_bins: int = 10,
    prefiltered: bool = False
) -> List[Dict]:
    """
    连续型变量等频分箱
    支持数值型和字符型变量
    """
    # 获取非空值（已预过滤时可直接复用）
    if prefiltered:
        valid_df = df.copy()
    else:
        valid_mask = df[rule_col].notna() & (df[rule_col] != '') & (df[rule_col] != 'nan')
        valid_df = df.loc[valid_mask].copy()
    
    if len(valid_df) == 0:
        return []
    
    # 判断是否是数值型
    is_numeric = pd.api.types.is_numeric_dtype(df[rule_col])
    
    if is_numeric:
        # 数值型：使用 pd.qcut 等频分箱
        try:
            valid_df['bin'] = pd.qcut(valid_df[rule_col], q=n_bins, duplicates='drop')
        except ValueError:
            try:
                valid_df['bin'] = pd.qcut(valid_df[rule_col], q=min(5, valid_df[rule_col].nunique()), duplicates='drop')
            except ValueError:
                return []
    else:
        # 字符型：先转数值尝试，不行则按频率等频分组
        try:
            # 尝试转换为数值
            numeric_col = pd.to_numeric(valid_df[rule_col], errors='raise')
            valid_df['_numeric_col'] = numeric_col
            try:
                valid_df['bin'] = pd.qcut(valid_df['_numeric_col'], q=n_bins, duplicates='drop')
            except ValueError:
                valid_df['bin'] = pd.qcut(valid_df['_numeric_col'], q=min(5, valid_df[rule_col].nunique()), duplicates='drop')
        except (ValueError, TypeError):
            # 无法转为数值，按频率排序后等频分箱
            freq_order = valid_df[rule_col].value_counts().index.tolist()
            valid_df['_freq_order'] = valid_df[rule_col].map({v: i for i, v in enumerate(freq_order)})
            try:
                valid_df['bin'] = pd.qcut(valid_df['_freq_order'], q=n_bins, duplicates='drop')
            except ValueError:
                try:
                    valid_df['bin'] = pd.qcut(valid_df['_freq_order'], q=min(5, n_bins), duplicates='drop')
                except ValueError:
                    # 分箱失败，按字符直接分组
                    return _bin_discrete_variable(
                        valid_df, rule_col, target_col, overall_bad_rate, prefiltered=True
                    )
    
    # 按分箱计算统计
    bin_stats = valid_df.groupby('bin', observed=True).agg(
        count=(target_col, 'count'),
        bad_count=(target_col, 'sum')
    ).reset_index()
    
    bin_stats['bad_rate'] = bin_stats['bad_count'] / bin_stats['count']
    bin_stats['lift'] = bin_stats['bad_rate'] / overall_bad_rate if overall_bad_rate > 0 else 0
    
    # 排序并构建结果
    bin_stats = bin_stats.sort_values('bin')
    
    result = []
    for _, row in bin_stats.iterrows():
        bin_label = str(row['bin'])
        result.append({
            'bin_range': bin_label,
            'count': int(row['count']),
            'count_pct': float(row['count'] / len(df) * 100),
            'bad_count': int(row['bad_count']),
            'good_count': int(row['count'] - row['bad_count']),
            'bad_rate': float(row['bad_rate']),
            'good_rate': float(1 - row['bad_rate']),
            'lift': float(row['lift']),
        })
    
    return result


def _bin_discrete_variable(
    df: pd.DataFrame, 
    rule_col: str, 
    target_col: str, 
    overall_bad_rate: float,
    prefiltered: bool = False
) -> List[Dict]:
    """
    离散变量按每个取值分析
    支持数值型和字符型变量
    """
    # 获取非空值（已预过滤时可直接复用）
    if prefiltered:
        valid_df = df.copy()
    else:
        valid_mask = df[rule_col].notna() & (df[rule_col] != '') & (df[rule_col] != 'nan')
        valid_df = df.loc[valid_mask].copy()
    
    if len(valid_df) == 0:
        return []
    
    # 按取值分组计算统计
    grouped = valid_df.groupby(rule_col, observed=True, dropna=False).agg(
        count=(target_col, 'count'),
        bad_count=(target_col, 'sum')
    ).reset_index()
    
    # 处理 NaN 情况
    if f'{rule_col}' in grouped.columns:
        grouped[rule_col] = grouped[rule_col].fillna('空值')
    
    # 计算逾期率和lift
    grouped['bad_rate'] = grouped['bad_count'] / grouped['count']
    grouped['lift'] = grouped['bad_rate'] / overall_bad_rate if overall_bad_rate > 0 else 0
    
    # 按逾期率降序排序
    grouped = grouped.sort_values('bad_rate', ascending=False)
    
    result = []
    for _, row in grouped.iterrows():
        # 获取取值名称
        if rule_col in grouped.columns:
            value_label = str(row[rule_col])
        else:
            value_label = str(row.name)
        
        result.append({
            'value': value_label,
            'count': int(row['count']),
            'count_pct': float(row['count'] / len(df) * 100) if len(df) > 0 else 0,
            'bad_count': int(row['bad_count']),
            'good_count': int(row['count'] - row['bad_count']),
            'bad_rate': float(row['bad_rate']),
            'good_rate': float(1 - row['bad_rate']),
            'lift': float(row['lift']),
        })
    
    return result


def _analyze_rule_pair(
    rule1: str,
    rule2: str,
    s1: pd.Series,
    s2: pd.Series,
    y: pd.Series,
    overall_bad_rate: float,
    min_pair_sample: int = PAIR_ANALYSIS_MIN_PAIR_SAMPLE,
    min_combo_count: int = 20,
) -> tuple:
    """
    计算单个规则对的好/坏组合，供并发调用。
    返回 (good_list, bad_list)，均为 dict 列表。
    """
    valid_mask = s1.notna() & s2.notna()
    if int(valid_mask.sum()) < min_pair_sample:
        return [], []

    pair_df = pd.DataFrame({
        'v1': s1.loc[valid_mask].astype(str, copy=False).values,
        'v2': s2.loc[valid_mask].astype(str, copy=False).values,
        'y': y.loc[valid_mask].values
    })
    if pair_df.empty:
        return [], []

    pair_grouped = pair_df.groupby(['v1', 'v2'], observed=True).agg(
        count=('y', 'count'),
        bad_count=('y', 'sum')
    ).reset_index()

    pair_grouped['good_count'] = pair_grouped['count'] - pair_grouped['bad_count']
    pair_grouped['bad_rate'] = pair_grouped['bad_count'] / pair_grouped['count']
    pair_grouped['lift'] = pair_grouped['bad_rate'] / overall_bad_rate if overall_bad_rate > 0 else 0

    good_list = []
    good_combos = pair_grouped[
        (pair_grouped['bad_rate'] < overall_bad_rate * 0.75)
        & (pair_grouped['count'] >= min_combo_count)
    ].sort_values('bad_rate')
    if good_combos.empty:
        relaxed_min_count = max(8, min_combo_count // 2)
        good_combos = pair_grouped[
            (pair_grouped['bad_rate'] < overall_bad_rate * 0.9)
            & (pair_grouped['count'] >= relaxed_min_count)
        ].sort_values('bad_rate')
    if good_combos.empty:
        relaxed_min_count = max(6, min_combo_count // 3)
        good_combos = pair_grouped[
            (pair_grouped['bad_rate'] < overall_bad_rate)
            & (pair_grouped['count'] >= relaxed_min_count)
        ].sort_values('bad_rate')
    if good_combos.empty:
        relaxed_min_count = max(6, min_combo_count // 3)
        good_combos = pair_grouped[
            pair_grouped['count'] >= relaxed_min_count
        ].sort_values('bad_rate')
    for _, row in good_combos.head(5).iterrows():
        value1 = str(row['v1'])
        value2 = str(row['v2'])
        good_list.append({
            'rule1': rule1,
            'value1': value1,
            'rule2': rule2,
            'value2': value2,
            'combo': f"{rule1}={value1} + {rule2}={value2}",
            'combo_key': f"{rule1}:{value1}|{rule2}:{value2}",
            'sample_count': int(row['count']),
            'good_count': int(row['good_count']),
            'bad_rate': float(row['bad_rate']),
            'lift': float(row['lift']),
        })

    bad_list = []
    bad_combos = pair_grouped[
        (pair_grouped['bad_rate'] > overall_bad_rate * 1.3)
        & (pair_grouped['count'] >= min_combo_count)
    ].sort_values('bad_rate', ascending=False)
    if bad_combos.empty:
        relaxed_min_count = max(8, min_combo_count // 2)
        bad_combos = pair_grouped[
            (pair_grouped['bad_rate'] > overall_bad_rate * 1.1)
            & (pair_grouped['count'] >= relaxed_min_count)
        ].sort_values('bad_rate', ascending=False)
    if bad_combos.empty:
        relaxed_min_count = max(6, min_combo_count // 3)
        bad_combos = pair_grouped[
            (pair_grouped['bad_rate'] > overall_bad_rate)
            & (pair_grouped['count'] >= relaxed_min_count)
        ].sort_values('bad_rate', ascending=False)
    if bad_combos.empty:
        relaxed_min_count = max(6, min_combo_count // 3)
        bad_combos = pair_grouped[
            pair_grouped['count'] >= relaxed_min_count
        ].sort_values('bad_rate', ascending=False)
    for _, row in bad_combos.head(5).iterrows():
        value1 = str(row['v1'])
        value2 = str(row['v2'])
        bad_list.append({
            'rule1': rule1,
            'value1': value1,
            'rule2': rule2,
            'value2': value2,
            'combo': f"{rule1}={value1} + {rule2}={value2}",
            'combo_key': f"{rule1}:{value1}|{rule2}:{value2}",
            'sample_count': int(row['count']),
            'bad_count': int(row['bad_count']),
            'bad_rate': float(row['bad_rate']),
            'lift': float(row['lift']),
        })

    return good_list, bad_list


def _analyze_user_profile(
    df: pd.DataFrame, 
    rule_cols: List[str], 
    target_col: str
) -> Dict[str, Any]:
    """
    用户画像分析
    识别好/坏用户群体的规则取值和组合
    【优化】两两规则对分析改为 ThreadPoolExecutor 并行计算
    """
    overall_bad_rate = df[target_col].mean() if target_col in df.columns else 0
    total_count = len(df)
    total_bad = int(df[target_col].sum())
    total_good = int(total_count - total_bad)
    
    result = {
        'overall_bad_rate': float(overall_bad_rate),
        'total_samples': total_count,
        'good_samples': total_good,
        'bad_samples': total_bad,
        'good_rules': [],
        'bad_rules': [],
        'good_combinations': [],
        'bad_combinations': [],
    }
    good_combo_keys = set()
    bad_combo_keys = set()
    y = pd.to_numeric(df[target_col], errors='coerce').fillna(0)

    # 预先构建每个规则列的有效值视图，避免在双层循环中重复转换
    valid_cols_data = {}
    pair_series_map = {}
    for rule_col in rule_cols:
        col_series = df[rule_col]
        if pd.api.types.is_numeric_dtype(col_series):
            pair_series = pd.to_numeric(col_series, errors='coerce')
            valid_mask = pair_series.notna()
        else:
            col_as_str = col_series.astype(str)
            valid_mask = col_series.notna() & (col_as_str != '') & (col_as_str != 'nan')
            pair_series = col_as_str.where(valid_mask, np.nan)

        if valid_mask.sum() == 0:
            continue
        pair_series_map[rule_col] = pair_series
        valid_cols_data[rule_col] = (
            col_series.loc[valid_mask].astype(str),
            y.loc[valid_mask]
        )

    # 1. 分析单个规则的取值与好坏用户的关系
    for rule_col, (col_values, y_valid) in valid_cols_data.items():
        grouped = pd.DataFrame({
            rule_col: col_values.values,
            target_col: y_valid.values
        }).groupby(rule_col, observed=True).agg(
            count=(target_col, 'count'),
            bad_count=(target_col, 'sum')
        ).reset_index()

        grouped['good_count'] = grouped['count'] - grouped['bad_count']
        grouped['bad_rate'] = grouped['bad_count'] / grouped['count']
        grouped['good_rate'] = grouped['good_count'] / grouped['count']
        grouped['lift'] = grouped['bad_rate'] / overall_bad_rate if overall_bad_rate > 0 else 0

        good_threshold = max(0.5, overall_bad_rate * 0.7)
        good_values = grouped[
            (grouped['bad_rate'] < good_threshold) & (grouped['count'] > 10)
        ].sort_values('bad_rate')
        for _, row in good_values.iterrows():
            result['good_rules'].append({
                'rule': rule_col,
                'value': str(row[rule_col]),
                'sample_count': int(row['count']),
                'good_count': int(row['good_count']),
                'bad_rate': float(row['bad_rate']),
                'good_rate': float(row['good_rate']),
                'lift': float(row['lift']),
            })

        bad_threshold = min(1.0, overall_bad_rate * 1.5)
        bad_values = grouped[
            (grouped['bad_rate'] > bad_threshold) & (grouped['count'] > 10)
        ].sort_values('bad_rate', ascending=False)
        for _, row in bad_values.iterrows():
            result['bad_rules'].append({
                'rule': rule_col,
                'value': str(row[rule_col]),
                'sample_count': int(row['count']),
                'bad_count': int(row['bad_count']),
                'bad_rate': float(row['bad_rate']),
                'good_rate': float(row['good_rate']),
                'lift': float(row['lift']),
            })

    # 2. 并行分析两两规则组合
    pair_rule_meta = []
    for rule_name, pair_series in pair_series_map.items():
        pair_rule_meta.append(
            (
                rule_name,
                int(pair_series.nunique(dropna=True)),
                int(pair_series.notna().sum())
            )
        )
    filtered_meta = [meta for meta in pair_rule_meta if meta[1] <= PAIR_ANALYSIS_MAX_UNIQUE]
    if len(filtered_meta) < 2:
        filtered_meta = pair_rule_meta
    filtered_meta.sort(key=lambda item: (item[1], -item[2], item[0]))
    valid_rule_list = [name for name, _, _ in filtered_meta[:PAIR_ANALYSIS_MAX_RULES]]
    pairs = [
        (valid_rule_list[i], valid_rule_list[j])
        for i in range(len(valid_rule_list))
        for j in range(i + 1, len(valid_rule_list))
    ]
    if len(pairs) > PAIR_ANALYSIS_MAX_PAIRS:
        pairs = pairs[:PAIR_ANALYSIS_MAX_PAIRS]

    analyzed_pairs = []
    if pairs:
        analyzed_pairs = pairs[:]
        max_workers = min(12, len(pairs))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(
                    _analyze_rule_pair,
                    r1,
                    r2,
                    pair_series_map.get(r1),
                    pair_series_map.get(r2),
                    y,
                    overall_bad_rate,
                ): (r1, r2)
                for r1, r2 in pairs
            }
            for future in as_completed(futures):
                try:
                    good_list, bad_list = future.result()
                except Exception:
                    continue
                for item in good_list:
                    key = item['combo_key']
                    if key not in good_combo_keys:
                        good_combo_keys.add(key)
                        result['good_combinations'].append(item)
                for item in bad_list:
                    key = item['combo_key']
                    if key not in bad_combo_keys:
                        bad_combo_keys.add(key)
                        result['bad_combinations'].append(item)

    # 兜底补充：若严格筛选后组合为空，再做一次轻量扫描，避免“组合分析看起来消失”
    if (not result['good_combinations'] or not result['bad_combinations']) and len(filtered_meta) >= 2:
        fallback_rule_list = [name for name, _, _ in filtered_meta[:max(PAIR_ANALYSIS_MAX_RULES, 14)]]
        fallback_pairs = [
            (fallback_rule_list[i], fallback_rule_list[j])
            for i in range(len(fallback_rule_list))
            for j in range(i + 1, len(fallback_rule_list))
            if (fallback_rule_list[i], fallback_rule_list[j]) not in analyzed_pairs
        ][:PAIR_ANALYSIS_FALLBACK_MAX_PAIRS]

        if fallback_pairs:
            max_workers = min(10, len(fallback_pairs))
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {
                    executor.submit(
                        _analyze_rule_pair,
                        r1,
                        r2,
                        pair_series_map.get(r1),
                        pair_series_map.get(r2),
                        y,
                        overall_bad_rate,
                        PAIR_ANALYSIS_FALLBACK_MIN_PAIR_SAMPLE,
                        10,
                    ): (r1, r2)
                    for r1, r2 in fallback_pairs
                }
                for future in as_completed(futures):
                    try:
                        good_list, bad_list = future.result()
                    except Exception:
                        continue
                    for item in good_list:
                        key = item['combo_key']
                        if key not in good_combo_keys:
                            good_combo_keys.add(key)
                            result['good_combinations'].append(item)
                    for item in bad_list:
                        key = item['combo_key']
                        if key not in bad_combo_keys:
                            bad_combo_keys.add(key)
                            result['bad_combinations'].append(item)

    # 按lift排序（去掉辅助字段）
    result['good_rules'].sort(key=lambda x: x['lift'])
    result['bad_rules'].sort(key=lambda x: x['lift'], reverse=True)
    result['good_combinations'].sort(key=lambda x: x['lift'])
    result['bad_combinations'].sort(key=lambda x: x['lift'], reverse=True)
    result['good_combinations'] = result['good_combinations'][:80]
    result['bad_combinations'] = result['bad_combinations'][:80]
    
    # 去掉辅助字段
    for c in result['good_combinations'] + result['bad_combinations']:
        c.pop('combo_key', None)
    
    return result


def _build_rule_decision_tree(df: pd.DataFrame, rule_cols: List[str], target_col: str) -> Dict[str, Any]:
    """Build a shallow decision tree (max depth 3, min leaf samples 10)."""
    if target_col not in df.columns or not rule_cols:
        return {'leaf_nodes': [], 'tree_image': ''}

    valid_cols = [col for col in rule_cols if col in df.columns][:TREE_MAX_RULES]
    if not valid_cols or len(df) < 20:
        return {'leaf_nodes': [], 'tree_image': ''}

    try:
        feature_df = df[valid_cols].copy()
        for col in valid_cols:
            if pd.api.types.is_numeric_dtype(feature_df[col]):
                feature_df[col] = pd.to_numeric(feature_df[col], errors='coerce').fillna(
                    feature_df[col].median() if feature_df[col].notna().any() else 0
                )
            else:
                cat_series = feature_df[col].fillna('missing').astype(str)
                value_count = cat_series.value_counts(dropna=False)
                if len(value_count) > TREE_MAX_CATEGORY_VALUES:
                    keep_values = set(value_count.head(TREE_MAX_CATEGORY_VALUES - 1).index.tolist())
                    cat_series = cat_series.where(cat_series.isin(keep_values), 'other')
                feature_df[col] = cat_series

        x = pd.get_dummies(feature_df, dummy_na=False)
        if x.shape[1] > TREE_MAX_DUMMY_FEATURES:
            variance = x.var(axis=0)
            keep_cols = variance.sort_values(ascending=False).head(TREE_MAX_DUMMY_FEATURES).index
            x = x.loc[:, keep_cols]
        if x.empty:
            return {'leaf_nodes': [], 'tree_image': ''}

        y = pd.to_numeric(df[target_col], errors='coerce').fillna(0).astype(int)
        clf = DecisionTreeClassifier(max_depth=3, min_samples_leaf=10, random_state=42)
        clf.fit(x, y)

        fig, ax = plt.subplots(figsize=(14, 7))
        plot_tree(
            clf,
            feature_names=list(x.columns),
            class_names=['good', 'bad'],
            filled=True,
            rounded=True,
            fontsize=8,
            impurity=False,
            ax=ax,
        )
        ax.set_title('Rule Decision Tree (depth<=3, min leaf samples>=10)', fontsize=12)
        image_buf = io.BytesIO()
        fig.savefig(image_buf, format='png', dpi=96, bbox_inches='tight', facecolor='white')
        image_buf.seek(0)
        tree_image = base64.b64encode(image_buf.read()).decode('utf-8')
        plt.close(fig)

        tree = clf.tree_
        feature_names = list(x.columns)
        leaf_nodes = []

        def walk(node_id: int, path_rules: List[str]):
            feature_index = tree.feature[node_id]
            if feature_index == _tree.TREE_UNDEFINED:
                sample_count = int(tree.n_node_samples[node_id])
                if sample_count < 10:
                    return
                values = tree.value[node_id][0]
                bad_count = float(values[1]) if len(values) > 1 else 0.0
                bad_rate = bad_count / sample_count if sample_count else 0.0
                last_rule = path_rules[-1] if path_rules else 'all_samples'
                leaf_nodes.append({
                    'node_id': node_id,
                    'rule_name': last_rule.split(' ')[0] if last_rule != 'all_samples' else 'all_samples',
                    'threshold': last_rule,
                    'sample_count': sample_count,
                    'bad_count': int(round(bad_count)),
                    'bad_rate': float(bad_rate),
                    'rules': path_rules[:] or ['all_samples'],
                })
                return

            feature_name = feature_names[feature_index]
            threshold = float(tree.threshold[node_id])
            if threshold == 0.5 and ('_' in feature_name):
                rule_name, feature_value = feature_name.split('_', 1)
                left_rule = f"{rule_name} != {feature_value}"
                right_rule = f"{rule_name} = {feature_value}"
            else:
                left_rule = f"{feature_name} <= {threshold:.4f}"
                right_rule = f"{feature_name} > {threshold:.4f}"

            walk(tree.children_left[node_id], path_rules + [left_rule])
            walk(tree.children_right[node_id], path_rules + [right_rule])

        walk(0, [])
        leaf_nodes.sort(key=lambda item: item['bad_rate'], reverse=True)
        return {'leaf_nodes': leaf_nodes[:12], 'tree_image': tree_image}
    except Exception:
        return {'leaf_nodes': [], 'tree_image': ''}

def _generate_chart_data(result: Dict) -> Dict:
    """生成图表数据"""
    charts = {}
    
    # 规则分箱对比图
    if result.get('rule_binning'):
        charts['规则分箱逾期对比'] = {
            'type': 'bar',
            'data': {},
        }
    
    return charts


def _generate_summary_table(result: Dict) -> str:
    """生成汇总表格 HTML"""
    # 只显示规则分箱表
    html = '<div class="section-title">规则分箱分析（取值-样本-逾期率-Lift）</div>'
    html += '<table class="rule-summary-table"><thead><tr>'
    html += '<th>规则名称</th>'
    html += '<th>取值/分箱</th>'
    html += '<th>样本数</th>'
    html += '<th>占比</th>'
    html += '<th>逾期率</th>'
    html += '<th>Lift</th>'
    html += '</tr></thead><tbody>'
    
    rule_binning = result.get('rule_binning', {})
    for rule_name, bin_data in rule_binning.items():
        bins = bin_data.get('bins', [])
        for b in bins:
            bin_label = b.get('bin_range', b.get('value', ''))
            lift = b.get('lift', 0)
            lift_color = 'red' if lift > 1.5 else ('orange' if lift > 1 else 'green')
            html += '<tr>'
            html += f'<td>{rule_name}</td>'
            html += f'<td>{bin_label}</td>'
            html += f'<td>{b.get("count", 0):,}</td>'
            html += f'<td>{b.get("count_pct", 0):.1f}%</td>'
            html += f'<td>{b.get("bad_rate", 0)*100:.2f}%</td>'
            html += f'<td style="color:{lift_color}">{lift:.2f}</td>'
            html += '</tr>'
    
    html += '</tbody></table>'
    return html


def _generate_details(result: Dict) -> Dict:
    """生成详细数据"""
    return {
        'rule_binning_detail': result.get('rule_binning', {}),
        'user_profile_detail': result.get('user_profile', {}),
        'decision_tree_detail': result.get('decision_tree', {}),
    }


def generate_rule_html_report(result: Dict, analysis_date: str) -> str:
    """生成规则分析的 HTML 报告"""
    
    summary = result.get('data_summary', {})
    rule_binning = result.get('rule_binning', {})
    user_profile = result.get('user_profile', {})
    decision_tree = result.get('decision_tree', {})
    
    # 渲染好/坏用户规则列表
    good_rules_html = _render_user_rules(user_profile.get('good_rules', []), 'good')
    bad_rules_html = _render_user_rules(user_profile.get('bad_rules', []), 'bad')
    
    # 渲染好/坏用户组合
    good_combos_html = _render_user_combos(user_profile.get('good_combinations', []), 'good')
    bad_combos_html = _render_user_combos(user_profile.get('bad_combinations', []), 'bad')
    
    # 渲染规则分箱表
    bin_tables_html = _render_rule_binning_tables(rule_binning)
    decision_tree_html = _render_decision_tree(decision_tree)
    
    html = f"""
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>规则分析报告 - 用户画像</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; padding: 20px; background: #f5f7fa; }}
        .report-header {{ background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 30px; border-radius: 12px; margin-bottom: 24px; }}
        .report-header h1 {{ font-size: 28px; margin-bottom: 10px; }}
        .report-header .meta {{ opacity: 0.9; font-size: 14px; }}
        .stats-row {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; margin-bottom: 24px; }}
        .stat-card {{ background: white; padding: 20px; border-radius: 10px; box-shadow: 0 2px 8px rgba(0,0,0,0.08); text-align: center; }}
        .stat-card .value {{ font-size: 28px; font-weight: bold; color: #333; }}
        .stat-card .label {{ color: #666; font-size: 13px; margin-top: 5px; }}
        .stat-card.good .value {{ color: #16a34a; }}
        .stat-card.bad .value {{ color: #dc2626; }}
        .section {{ background: white; border-radius: 10px; padding: 24px; margin-bottom: 24px; box-shadow: 0 2px 8px rgba(0,0,0,0.08); }}
        .section-title {{ font-size: 18px; font-weight: 600; color: #333; margin-bottom: 16px; padding-bottom: 10px; border-bottom: 2px solid #667eea; }}
        .profile-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 24px; }}
        .profile-card {{ border-radius: 10px; padding: 20px; }}
        .profile-card.good {{ background: linear-gradient(135deg, #dcfce7 0%, #bbf7d0 100%); border: 2px solid #16a34a; }}
        .profile-card.bad {{ background: linear-gradient(135deg, #fee2e2 0%, #fecaca 100%); border: 2px solid #dc2626; }}
        .profile-card h3 {{ margin-bottom: 12px; font-size: 16px; }}
        .profile-card.good h3 {{ color: #16a34a; }}
        .profile-card.bad h3 {{ color: #dc2626; }}
        .profile-item {{ background: white; border-radius: 6px; padding: 12px; margin-bottom: 8px; font-size: 13px; }}
        .profile-item .rule-name {{ font-weight: 600; color: #333; margin-bottom: 4px; }}
        .profile-item .rule-value {{ color: #666; }}
        .profile-item .stats {{ margin-top: 6px; font-size: 12px; }}
        .profile-item .bad-rate {{ color: #dc2626; font-weight: 600; }}
        .profile-item .good-rate {{ color: #16a34a; font-weight: 600; }}
        .profile-item .lift {{ color: #d97706; }}
        .badge {{ display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 11px; font-weight: 500; margin-left: 8px; }}
        .badge-good {{ background: #dcfce7; color: #16a34a; }}
        .badge-bad {{ background: #fee2e2; color: #dc2626; }}
        table {{ width: 100%; border-collapse: collapse; margin: 16px 0; font-size: 14px; }}
        th, td {{ padding: 10px 12px; text-align: left; border-bottom: 1px solid #eee; }}
        th {{ background: #f8f9fa; font-weight: 600; color: #333; }}
        tr:hover {{ background: #f8f9fa; }}
        .lift-high {{ color: #dc2626; font-weight: bold; }}
        .lift-mid {{ color: #d97706; }}
        .lift-low {{ color: #16a34a; }}
        .rule-binning-section {{ margin-bottom: 24px; }}
        .rule-binning-section h4 {{ margin: 16px 0 12px; color: #333; font-size: 15px; }}
        .rule-binning-section h4 span {{ font-weight: normal; color: #666; font-size: 12px; margin-left: 8px; }}
        .combo-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-top: 12px; }}
        .combo-item {{ background: white; border-radius: 6px; padding: 10px; font-size: 12px; }}
        .empty-msg {{ text-align: center; padding: 40px; color: #999; }}
        .bin-table {{ font-size: 13px; }}
        .bin-table th {{ background: #f0f4ff; font-size: 12px; }}
        .tree-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 16px; }}
        .tree-card {{ border: 1px solid #dbeafe; background: linear-gradient(180deg, #eff6ff 0%, #ffffff 100%); border-radius: 10px; padding: 16px; }}
        .tree-card h4 {{ font-size: 15px; color: #1d4ed8; margin-bottom: 10px; }}
        .tree-rules {{ font-size: 12px; color: #374151; line-height: 1.7; margin-bottom: 10px; }}
        .tree-meta {{ font-size: 12px; color: #6b7280; }}
        .tree-image-wrap {{ background:#f8fafc; border:1px solid #e2e8f0; border-radius:10px; padding:12px; margin-bottom:12px; }}
        .tree-image {{ width:100%; height:auto; display:block; border-radius:8px; }}
        .tree-leaf-table-wrap {{ margin-top:8px; }}
        .tree-leaf-table th {{ background:#ecfeff; }}
        .rate-bar-track {{ width: 160px; height: 10px; border-radius: 999px; background: #e5e7eb; overflow: hidden; display: inline-block; vertical-align: middle; margin-right: 8px; }}
        .rate-bar-fill {{ height: 100%; background: linear-gradient(90deg, #f59e0b 0%, #dc2626 100%); border-radius: 999px; display: block; }}
    </style>
</head>
<body>
    <div class="report-header">
        <h1>📋 规则分析报告 - 用户画像</h1>
        <div class="meta">分析时间: {analysis_date}</div>
    </div>
    
    <!-- 数据概览 -->
    <div class="stats-row">
        <div class="stat-card">
            <div class="value">{summary.get('total_samples', 0):,}</div>
            <div class="label">总样本量</div>
        </div>
        <div class="stat-card good">
            <div class="value">{summary.get('good_samples', 0):,}</div>
            <div class="label">好样本数</div>
        </div>
        <div class="stat-card bad">
            <div class="value">{summary.get('bad_samples', 0):,}</div>
            <div class="label">坏样本数</div>
        </div>
        <div class="stat-card">
            <div class="value">{summary.get('overall_bad_rate', 0)*100:.2f}%</div>
            <div class="label">整体逾期率</div>
        </div>
    </div>
    
    <!-- 用户画像分析 -->
    <div class="section">
        <div class="section-title">👥 用户画像分析</div>
        <p style="color: #666; margin-bottom: 16px; font-size: 13px;">
            整体逾期率: {user_profile.get('overall_bad_rate', 0)*100:.2f}% | 
            好用户: Lift &lt; 1 的取值 | 
            坏用户: Lift &gt; 1.5 的取值
        </p>
        
        <div class="profile-grid">
            <!-- 好用户群体 -->
            <div class="profile-card good">
                <h3>✅ 好用户群体 <span class="badge badge-good">{len(user_profile.get('good_rules', []))} 个取值</span></h3>
                <p style="color: #16a34a; font-size: 12px; margin-bottom: 12px;">逾期率低于整体70%的规则取值 → 优质客户特征</p>
                {good_rules_html if good_rules_html else '<div class="empty-msg">未发现明显的优质客户取值</div>'}
            </div>
            
            <!-- 坏用户群体 -->
            <div class="profile-card bad">
                <h3>⚠️ 坏用户群体 <span class="badge badge-bad">{len(user_profile.get('bad_rules', []))} 个取值</span></h3>
                <p style="color: #dc2626; font-size: 12px; margin-bottom: 12px;">逾期率高于整体150%的规则取值 → 高风险客户特征</p>
                {bad_rules_html if bad_rules_html else '<div class="empty-msg">未发现明显的高风险客户取值</div>'}
            </div>
        </div>
        
        <!-- 好/坏用户组合 -->
        <div style="margin-top: 24px;">
            <h4 style="color: #333; margin-bottom: 12px;">🔗 好/坏用户组合分析（两两规则取值组合）</h4>
            <div class="combo-grid">
                <div>
                    <h5 style="color: #16a34a; margin-bottom: 8px;">✅ 好用户组合</h5>
                    {good_combos_html if good_combos_html else '<div class="empty-msg">无明显好用户组合</div>'}
                </div>
                <div>
                    <h5 style="color: #dc2626; margin-bottom: 8px;">⚠️ 坏用户组合</h5>
                    {bad_combos_html if bad_combos_html else '<div class="empty-msg">无明显坏用户组合</div>'}
                </div>
            </div>
        </div>
    </div>
    
    <!-- 决策树可视化 -->
    <div class="section">
        <div class="section-title">🌳 规则决策树</div>
        <p style="color: #666; margin-bottom: 16px; font-size: 13px;">树深度不超过 3 层，叶子节点样本数大于 10，展示叶子节点规则名、阈值、逾期率和样本数。</p>
        {decision_tree_html}
    </div>
    
    <!-- 规则分箱分析 -->
    <div class="section">
        <div class="section-title">📊 规则分箱分析（取值-首逾-Lift）</div>
        <p style="color: #666; margin-bottom: 16px; font-size: 13px;">
            说明：取值数>10视为连续型变量做等频分箱，取值数≤10视为离散变量直接展示每个取值
        </p>
        {bin_tables_html}
    </div>
    
</body>
</html>"""
    
    return html


def _render_user_rules(rules: List[Dict], profile_type: str) -> str:
    """渲染用户规则列表"""
    if not rules:
        return ''
    
    html = ''
    for r in rules[:10]:
        lift = r.get('lift', 0)
        html += f"""
        <div class="profile-item">
            <div class="rule-name">{r['rule']} <span class="badge badge-{profile_type}">{r['value']}</span></div>
            <div class="rule-value">样本量: {r['sample_count']:,}</div>
            <div class="stats">
                <span class="bad-rate">逾期率: {r['bad_rate']*100:.2f}%</span> | 
                <span class="good-rate">正常率: {r.get('good_rate', 0)*100:.2f}%</span> | 
                <span class="lift">Lift: {lift:.2f}</span>
            </div>
        </div>
        """
    
    return html


def _render_user_combos(combos: List[Dict], profile_type: str) -> str:
    """渲染用户组合列表"""
    if not combos:
        return ''
    
    html = ''
    for c in combos[:12]:
        lift = c.get('lift', 0)
        rule1 = c.get('rule1', '')
        value1 = c.get('value1', '')
        rule2 = c.get('rule2', '')
        value2 = c.get('value2', '')
        html += f"""
        <div class="combo-item">
            <div>
                <span style="color: #333;"><strong>{rule1}</strong></span>
                <span style="color: #666;">=</span>
                <span style="color: {'#16a34a' if profile_type == 'good' else '#dc2626'}; font-weight: 600;">{value1}</span>
                <span style="color: #999; margin: 0 8px;">+</span>
                <span style="color: #333;"><strong>{rule2}</strong></span>
                <span style="color: #666;">=</span>
                <span style="color: {'#16a34a' if profile_type == 'good' else '#dc2626'}; font-weight: 600;">{value2}</span>
            </div>
            <div style="font-size: 11px; color: #666;">
                样本: {c.get('sample_count', 0):,} | 
                逾期率: {c.get('bad_rate', 0)*100:.2f}% | 
                Lift: {lift:.2f}
            </div>
        </div>
        """
    
    return html


def _render_decision_tree(decision_tree: Dict[str, Any]) -> str:
    """Render decision tree image and leaf-node details."""
    tree_image = (decision_tree or {}).get('tree_image', '')
    leaf_nodes = (decision_tree or {}).get('leaf_nodes', [])
    if not tree_image and not leaf_nodes:
        return '<div class="empty-msg">No decision tree result available.</div>'

    image_html = ''
    if tree_image:
        image_html = f'''
        <div class="tree-image-wrap">
            <img src="data:image/png;base64,{tree_image}" alt="rule decision tree" class="tree-image" />
        </div>
        '''

    rows = ''
    for idx, node in enumerate(leaf_nodes, start=1):
        rows += f'''
        <tr>
            <td>Leaf {idx}</td>
            <td>{node.get('rule_name', '--')}</td>
            <td>{node.get('threshold', '--')}</td>
            <td>{node.get('sample_count', 0):,}</td>
            <td>{node.get('bad_rate', 0) * 100:.2f}%</td>
        </tr>
        '''

    table_html = ''
    if rows:
        table_html = f'''
        <div class="tree-leaf-table-wrap">
            <table class="bin-table tree-leaf-table">
                <thead>
                    <tr>
                        <th>Leaf</th>
                        <th>Rule Name</th>
                        <th>Threshold / Path</th>
                        <th>Samples</th>
                        <th>Bad Rate</th>
                    </tr>
                </thead>
                <tbody>{rows}</tbody>
            </table>
        </div>
        '''

    return f'{image_html}{table_html}'

def _render_rule_binning_tables(rule_binning: Dict) -> str:
    """渲染规则分箱表格"""
    if not rule_binning:
        return '<div class="empty-msg">暂无分箱数据</div>'
    
    html = ''
    for rule_name, bin_data in rule_binning.items():
        bin_type = '等频分箱' if bin_data.get('bin_type') == 'continuous' else '离散取值'
        bins = bin_data.get('bins', [])
        max_bad_rate = max([b.get('bad_rate', 0) for b in bins], default=0)
        html += f"""
        <div class="rule-binning-section">
            <h4>📌 {rule_name} <span>({bin_type}, 共{bin_data.get('unique_count', 0)}个取值)</span></h4>
            <table class="bin-table">
                <thead>
                    <tr>
                        <th>{'分箱区间' if bin_data.get('bin_type') == 'continuous' else '取值'}</th>
                        <th>样本数</th>
                        <th>占比</th>
                        <th>好样本</th>
                        <th>坏样本</th>
                        <th>逾期率</th>
                        <th>Lift</th>
                    </tr>
                </thead>
                <tbody>
"""
        for b in bins:
            lift = b.get('lift', 0)
            lift_class = 'lift-high' if lift > 1.5 else ('lift-mid' if lift > 1 else 'lift-low')
            bin_label = b.get('bin_range', b.get('value', ''))
            bar_width = 100 if max_bad_rate <= 0 else min(100, (b.get('bad_rate', 0) / max_bad_rate) * 100)
            html += f"""
                    <tr>
                        <td>{bin_label}</td>
                        <td>{b.get('count', 0):,}</td>
                        <td>{b.get('count_pct', 0):.1f}%</td>
                        <td style="color:#16a34a">{b.get('good_count', 0):,}</td>
                        <td style="color:#dc2626">{b.get('bad_count', 0):,}</td>
                        <td>
                            <span class="rate-bar-track"><span class="rate-bar-fill" style="width:{bar_width:.1f}%"></span></span>
                            {b.get('bad_rate', 0)*100:.2f}%
                        </td>
                        <td class="{lift_class}">{lift:.2f}</td>
                    </tr>
"""
        html += """
                </tbody>
            </table>
        </div>
"""
    
    return html
