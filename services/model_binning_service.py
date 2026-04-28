"""
模型分箱分析服务
严格按照 model_binning_analysis.py 的计算口径实现：
- 等频分箱分析
- 模型分 > 1（倒序）和 < 1（正序）两种排序方式
- 输出列：箱号、分数下限、分数上限、样本数、坏样本数、cum_bad%、cum_good%、逾期率、Lift、累积KS、最大KS
"""

import io
import base64
import warnings
import numpy as np
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from sklearn.metrics import roc_auc_score, roc_curve
from sklearn.tree import DecisionTreeClassifier, plot_tree

warnings.filterwarnings('ignore')

# 中文字体
plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'WenQuanYi Micro Hei']
plt.rcParams['axes.unicode_minus'] = False


def _analyze_single_model(
    df: pd.DataFrame,
    model_col: str,
    label_col: str,
    n_bins: int,
    threshold: float,
) -> dict:
    """单模型分箱任务（供并发调用）。"""
    try:
        binning_df, auc, ks, sort_desc = equal_freq_binning(
            df, model_col, label_col, n_bins, threshold
        )
        return {
            'ok': True,
            'model_name': model_col,
            'binning_df': binning_df,
            'auc': auc,
            'ks': ks,
            'sort_desc': sort_desc,
        }
    except Exception as e:
        return {'ok': False, 'model_name': model_col, 'error': str(e)}


def calculate_lift(bad_rate_bin, bad_rate_overall):
    """计算 Lift 值"""
    if bad_rate_overall == 0:
        return 1.0
    return bad_rate_bin / bad_rate_overall


def equal_freq_binning(df, score_col, label_col, n_bins=10, threshold=1.0):
    """
    等频分箱分析

    参数:
        df: DataFrame
        score_col: 模型分列名
        label_col: 标签列名 (1=坏, 0=好)
        n_bins: 分箱数
        threshold: 判断标准，默认1.0
                  - 模型分 > threshold: 倒序排序（分数越高风险越低）
                  - 模型分 <= threshold: 正序排序（分数越高风险越高）

    返回:
        binning_result: 分箱结果DataFrame
        model_auc: 模型AUC
        model_ks: 模型KS
        sort_desc: 排序方式描述
    """
    # 复制数据
    data = df[[score_col, label_col]].copy()
    data = data.dropna()

    # 判断排序方向
    max_score = data[score_col].max()
    min_score = data[score_col].min()

    # 判断是否需要倒序（基于分数范围）
    if max_score > threshold:
        # 倒序：分数越高，箱号越大（风险越低）
        ascending = False
        sort_desc = "倒序（分数越高风险越低）"
    else:
        # 正序：分数越高，箱号越大（风险越高）
        ascending = True
        sort_desc = "正序（分数越高风险越高）"

    # 按分数排序
    data = data.sort_values(by=score_col, ascending=ascending).reset_index(drop=True)

    # 等频分箱
    data['bin'] = pd.qcut(data.index, q=n_bins, labels=False, duplicates='drop') + 1

    # 总体统计
    total_samples = len(data)
    total_bad = data[label_col].sum()
    total_good = total_samples - total_bad
    overall_bad_rate = total_bad / total_samples if total_samples > 0 else 0

    # 计算模型整体 AUC 和 KS
    # 根据排序方向调整分数：如果是倒序（分数越高风险越低），需要取反
    if not ascending:  # 倒序情况
        adjusted_score = -data[score_col]
    else:
        adjusted_score = data[score_col]

    try:
        model_auc = roc_auc_score(data[label_col], adjusted_score)
    except:
        model_auc = 0.5

    # 计算 KS（使用调整后的分数）
    fpr, tpr, _ = roc_curve(data[label_col], adjusted_score)
    model_ks = max(tpr - fpr) if len(tpr) > 0 else 0

    # 分箱统计
    result_rows = []
    cum_bad_count = 0
    cum_good_count = 0
    max_cum_ks = 0  # 追踪最大累积KS

    grouped = data.groupby('bin', sort=True)
    for bin_num, bin_data in grouped:

        if len(bin_data) == 0:
            continue

        # 基础统计
        n_samples = int(len(bin_data))
        n_bad = float(bin_data[label_col].sum())
        n_good = n_samples - n_bad

        # 分数范围
        score_min = bin_data[score_col].min()
        score_max = bin_data[score_col].max()

        # 逾期率
        bad_rate = n_bad / n_samples if n_samples > 0 else 0

        # 累积占比
        cum_bad_count += n_bad
        cum_good_count += n_good
        cum_bad_pct = cum_bad_count / total_bad if total_bad > 0 else 0
        cum_good_pct = cum_good_count / total_good if total_good > 0 else 0

        # 累计逾期率 = 累计坏样本数 / 累计样本数
        cum_samples = cum_bad_count + cum_good_count
        cum_bad_rate = cum_bad_count / cum_samples if cum_samples > 0 else 0

        # Lift
        lift = calculate_lift(bad_rate, overall_bad_rate)

        # 累积KS = |累积坏率 - 累积好率|，取绝对值确保为正
        cum_ks = abs(cum_bad_pct - cum_good_pct)
        # 更新最大累积KS
        max_cum_ks = max(max_cum_ks, cum_ks)

        result_rows.append({
            '箱号': bin_num,
            '分数下限': score_min,
            '分数上限': score_max,
            '样本数': n_samples,
            '坏样本数': int(n_bad),
            'cum_bad%': f"{cum_bad_pct * 100:.2f}%",
            'cum_good%': f"{cum_good_pct * 100:.2f}%",
            '累计逾期率': f"{cum_bad_rate * 100:.2f}%",
            '逾期率': f"{bad_rate * 100:.2f}%",
            'Lift': round(lift, 6),
            '累积KS': round(cum_ks, 6),
            '最大KS': round(max_cum_ks, 6)
        })

    binning_df = pd.DataFrame(result_rows)

    return binning_df, model_auc, model_ks, sort_desc


def analyze_all_models(df, label_col='label', n_bins=10, threshold=1.0):
    """
    分析文件中所有模型

    参数:
        df: DataFrame
        label_col: 标签列名
        n_bins: 分箱数
        threshold: 分数阈值，判断排序方向
    """
    # 识别模型列（排除标签列和ID列）
    exclude_cols = [label_col, 'user_id', 'id', 'apply_id', 'order_id', 'label']
    model_cols = [col for col in df.columns if col not in exclude_cols 
                  and pd.api.types.is_numeric_dtype(df[col])]

    # 存储所有结果
    all_results = []
    model_summary = []

    if not model_cols:
        return all_results, pd.DataFrame()

    # 并发执行单模型分箱（对外输出结构不变）
    max_workers = min(8, len(model_cols))
    if max_workers <= 1:
        task_results = [
            _analyze_single_model(df, model_col, label_col, n_bins, threshold)
            for model_col in model_cols
        ]
    else:
        task_results = []
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(
                    _analyze_single_model, df, model_col, label_col, n_bins, threshold
                ): model_col
                for model_col in model_cols
            }
            for future in as_completed(futures):
                model_col = futures[future]
                try:
                    task_results.append(future.result())
                except Exception as e:
                    task_results.append({'ok': False, 'model_name': model_col, 'error': str(e)})

    # 恢复为原列顺序，保持结果稳定
    task_by_model = {item.get('model_name', ''): item for item in task_results}
    ordered_results = [task_by_model.get(model_col) for model_col in model_cols if task_by_model.get(model_col)]

    for item in ordered_results:
        if not item.get('ok'):
            print(f"  [错误] {item.get('model_name', 'unknown')} 分析失败: {item.get('error', 'unknown')}")
            continue

        all_results.append({
            'model_name': item['model_name'],
            'binning_df': item['binning_df'],
            'auc': item['auc'],
            'ks': item['ks'],
            'sort_desc': item['sort_desc']
        })

        model_summary.append({
            '模型名称': item['model_name'],
            'AUC': round(item['auc'], 4),
            'KS': round(item['ks'], 4),
            '排序方式': item['sort_desc']
        })

    # 模型排名
    summary_df = pd.DataFrame(model_summary)
    if len(summary_df) > 0:
        summary_df['综合得分'] = summary_df['AUC'] * 0.4 + summary_df['KS'] * 0.6
        summary_df = summary_df.sort_values('综合得分', ascending=False).reset_index(drop=True)
        summary_df['排名'] = range(1, len(summary_df) + 1)

    return all_results, summary_df


def _fig_to_b64(fig) -> str:
    """将图表转换为 base64"""
    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=120, bbox_inches='tight',
                facecolor='white', edgecolor='none')
    buf.seek(0)
    img_b64 = base64.b64encode(buf.read()).decode('utf-8')
    plt.close(fig)
    return img_b64


def plot_model_summary(summary_df: pd.DataFrame) -> str:
    """绘制模型汇总排名图"""
    if len(summary_df) == 0:
        return ''
    
    fig, axes = plt.subplots(1, 2, figsize=(14, 6))
    
    # 左图：AUC排名
    ax1 = axes[0]
    colors = plt.cm.RdYlGn(np.linspace(0.3, 0.9, len(summary_df)))
    bars1 = ax1.barh(range(len(summary_df)), summary_df['AUC'].values[::-1], color=colors[::-1])
    ax1.set_yticks(range(len(summary_df)))
    ax1.set_yticklabels(summary_df['模型名称'].values[::-1])
    ax1.set_xlabel('AUC')
    ax1.set_title('模型 AUC 排名', fontweight='bold')
    ax1.axvline(x=0.5, color='red', linestyle='--', alpha=0.5, label='随机线')
    for i, v in enumerate(summary_df['AUC'].values[::-1]):
        ax1.text(v + 0.01, i, f'{v:.4f}', va='center', fontsize=9)
    ax1.legend()
    ax1.grid(axis='x', alpha=0.3)
    
    # 右图：KS排名
    ax2 = axes[1]
    bars2 = ax2.barh(range(len(summary_df)), summary_df['KS'].values[::-1], color=colors[::-1])
    ax2.set_yticks(range(len(summary_df)))
    ax2.set_yticklabels(summary_df['模型名称'].values[::-1])
    ax2.set_xlabel('KS')
    ax2.set_title('模型 KS 排名', fontweight='bold')
    ax2.axvline(x=0.15, color='orange', linestyle='--', alpha=0.5, label='可用线 (0.15)')
    for i, v in enumerate(summary_df['KS'].values[::-1]):
        ax2.text(v + 0.01, i, f'{v:.4f}', va='center', fontsize=9)
    ax2.legend()
    ax2.grid(axis='x', alpha=0.3)
    
    plt.tight_layout()
    return _fig_to_b64(fig)


def plot_binning_detail(binning_df: pd.DataFrame, model_name: str, 
                        auc: float, ks: float, sort_desc: str) -> str:
    """绘制单个模型的分箱详情图"""
    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    
    # 1. 逾期率柱状图
    ax1 = axes[0, 0]
    bins = binning_df['箱号'].values
    bad_rates = [float(r.replace('%', '')) for r in binning_df['逾期率'].values]
    colors = plt.cm.RdYlGn_r(np.array(bad_rates) / max(bad_rates) if max(bad_rates) > 0 else [0.5]*len(bad_rates))
    bars = ax1.bar(bins, bad_rates, color=colors, edgecolor='white', linewidth=0.5)
    ax1.set_xlabel('箱号')
    ax1.set_ylabel('逾期率 (%)')
    ax1.set_title(f'{model_name} 各箱逾期率', fontweight='bold')
    ax1.set_xticks(bins)
    ax1.grid(axis='y', alpha=0.3)
    for bar, rate in zip(bars, bad_rates):
        ax1.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.5,
                f'{rate:.1f}%', ha='center', va='bottom', fontsize=8)
    
    # 2. 样本数分布
    ax2 = axes[0, 1]
    counts = binning_df['样本数'].values
    ax2.bar(bins, counts, color='steelblue', edgecolor='white', linewidth=0.5)
    ax2.set_xlabel('箱号')
    ax2.set_ylabel('样本数')
    ax2.set_title(f'{model_name} 各箱样本数分布', fontweight='bold')
    ax2.set_xticks(bins)
    ax2.grid(axis='y', alpha=0.3)
    for b, c in zip(bins, counts):
        ax2.text(b, c + max(counts)*0.02, str(c), ha='center', va='bottom', fontsize=8)
    
    # 3. 累积KS曲线
    ax3 = axes[1, 0]
    cum_ks = binning_df['累积KS'].values
    max_ks = binning_df['最大KS'].values
    ax3.plot(bins, cum_ks, 'b-o', linewidth=2, markersize=6, label='累积KS')
    ax3.plot(bins, max_ks, 'r--s', linewidth=2, markersize=5, label='最大KS')
    ax3.set_xlabel('箱号')
    ax3.set_ylabel('KS值')
    ax3.set_title(f'{model_name} KS曲线', fontweight='bold')
    ax3.set_xticks(bins)
    ax3.legend()
    ax3.grid(alpha=0.3)
    ax3.set_ylim(0, max(max_ks) * 1.1 if len(max_ks) > 0 else 0.3)
    
    # 4. Lift曲线
    ax4 = axes[1, 1]
    lifts = binning_df['Lift'].values
    ax4.bar(bins, lifts, color='coral', edgecolor='white', linewidth=0.5)
    ax4.axhline(y=1.0, color='red', linestyle='--', alpha=0.7, label='基准线 (Lift=1)')
    ax4.set_xlabel('箱号')
    ax4.set_ylabel('Lift')
    ax4.set_title(f'{model_name} Lift提升度', fontweight='bold')
    ax4.set_xticks(bins)
    ax4.legend()
    ax4.grid(axis='y', alpha=0.3)
    
    # 添加模型信息
    fig.suptitle(f'模型分箱分析详情\n{model_name} | AUC={auc:.4f} | KS={ks:.4f} | {sort_desc}', 
                 fontsize=14, fontweight='bold', y=1.02)
    
    plt.tight_layout()
    return _fig_to_b64(fig)


def plot_all_models_badrate(all_binning_results: list, top_n: int = 6) -> str:
    """绘制多模型逾期率对比图"""
    top_results = all_binning_results[:top_n]
    if not top_results:
        return ''
    
    fig, ax = plt.subplots(figsize=(14, 6))
    
    n_bins = len(top_results[0]['binning_df'])
    x = np.arange(n_bins)
    width = 0.12
    colors = plt.cm.tab10(np.linspace(0, 1, len(top_results)))
    
    for i, result in enumerate(top_results):
        binning_df = result['binning_df']
        bad_rates = [float(r.replace('%', '')) for r in binning_df['逾期率'].values]
        offset = (i - len(top_results)/2 + 0.5) * width
        bars = ax.bar(x + offset, bad_rates, width, label=result['model_name'], 
                      color=colors[i], edgecolor='white', linewidth=0.5)
    
    ax.set_xlabel('箱号', fontsize=12)
    ax.set_ylabel('逾期率 (%)', fontsize=12)
    ax.set_title('Top 6 模型分箱逾期率对比', fontsize=14, fontweight='bold')
    ax.set_xticks(x)
    ax.set_xticklabels([str(i+1) for i in range(n_bins)])
    ax.legend(loc='upper right', fontsize=9)
    ax.grid(axis='y', alpha=0.3)
    
    plt.tight_layout()
    return _fig_to_b64(fig)


def build_model_decision_tree(df: pd.DataFrame, label_col: str, score_cols: list) -> dict:
    """构建模型分决策树（深度<=3，叶子样本>=10）并输出图像与叶子摘要"""
    if label_col not in df.columns or not score_cols:
        return {'image': '', 'leaf_nodes': []}

    selected = [col for col in score_cols[:6] if col in df.columns]
    if not selected:
        return {'image': '', 'leaf_nodes': []}

    data = df[selected + [label_col]].copy()
    for col in selected:
        data[col] = pd.to_numeric(data[col], errors='coerce')
        if data[col].notna().any():
            data[col] = data[col].fillna(data[col].median())
        else:
            data[col] = data[col].fillna(0)
    data[label_col] = pd.to_numeric(data[label_col], errors='coerce').fillna(0).astype(int)
    data = data.dropna(subset=selected)
    if len(data) < 30:
        return {'image': '', 'leaf_nodes': []}

    x = data[selected]
    y = data[label_col]

    try:
        clf = DecisionTreeClassifier(max_depth=3, min_samples_leaf=10, random_state=42)
        clf.fit(x, y)

        fig, ax = plt.subplots(figsize=(max(10, len(selected) * 1.8), 6))
        plot_tree(
            clf, feature_names=selected, class_names=['good', 'bad'],
            filled=True, rounded=True, fontsize=7, impurity=False, ax=ax
        )
        ax.set_title('模型分决策树（深度<=3）', fontsize=12)
        tree_image = _fig_to_b64(fig)

        leaf_id = clf.apply(x)
        leaf_nodes = []
        for lid in sorted(pd.Series(leaf_id).unique()):
            mask = leaf_id == lid
            sample_count = int(mask.sum())
            if sample_count < 10:
                continue
            bad_count = int(y[mask].sum())
            bad_rate = float(bad_count / sample_count) if sample_count else 0.0
            leaf_nodes.append({
                'leaf_id': int(lid),
                'sample_count': sample_count,
                'bad_count': bad_count,
                'bad_rate': bad_rate,
            })
        leaf_nodes.sort(key=lambda item: item['bad_rate'], reverse=True)
        return {'image': tree_image, 'leaf_nodes': leaf_nodes[:12], 'features': selected}
    except Exception:
        return {'image': '', 'leaf_nodes': []}


def run_model_binning_analysis(df: pd.DataFrame, 
                               label_col: str = 'label',
                               n_bins: int = 10,
                               threshold: float = 1.0) -> dict:
    """
    执行模型分箱分析主函数

    返回:
        dict: 包含以下键值
            - summary_df: 模型汇总DataFrame
            - all_results: 所有模型的详细分箱结果
            - charts: 图表base64字典
            - model_summary_table: 模型汇总表格HTML
            - binning_details: 各模型分箱详情表格
    """
    # 数据统计
    total_samples = len(df)
    total_bad = df[label_col].sum() if label_col in df.columns else 0
    overall_bad_rate = total_bad / total_samples if total_samples > 0 else 0
    
    # 执行分析
    all_results, summary_df = analyze_all_models(
        df, label_col, n_bins, threshold
    )
    
    if len(all_results) == 0:
        return {
            'error': '未找到有效的模型列进行分析'
        }
    
    # 生成图表
    charts = {}
    charts['01_模型排名'] = plot_model_summary(summary_df)
    charts['02_逾期率对比'] = plot_all_models_badrate(all_results, top_n=min(6, len(all_results)))
    
    # 每个模型的详情图（当前iframe报告未使用，跳过生成以提升速度）
    
    # 生成表格HTML
    model_summary_html = summary_df[['排名', '模型名称', 'AUC', 'KS', '综合得分', '排序方式']].to_html(
        index=False, border=0, classes='data-table'
    )
    
    # 分箱详情表格
    binning_details = {}
    for result in all_results:
        model_name = result['model_name']
        binning_df = result['binning_df']
        
        # 添加模型信息行
        info_row = pd.DataFrame([{
            '箱号': f"【{model_name}】",
            '分数下限': f"AUC={result['auc']:.4f}",
            '分数上限': f"KS={result['ks']:.4f}",
            '样本数': result['sort_desc'],
            '坏样本数': '',
            'cum_bad%': '',
            'cum_good%': '',
            '累计逾期率': '',
            '逾期率': '',
            'Lift': '',
            '累积KS': '',
            '最大KS': ''
        }])
        
        # 合并
        detail_df = pd.concat([info_row, binning_df], ignore_index=True)
        binning_details[model_name] = detail_df.to_html(index=False, border=0, classes='data-table')
    
    decision_tree = build_model_decision_tree(
        df=df,
        label_col=label_col,
        score_cols=[r['model_name'] for r in all_results]
    )

    return {
        'data_summary': {
            'total_samples': total_samples,
            'total_bad': int(total_bad),
            'overall_bad_rate': overall_bad_rate,  # 原始数值，前端会格式化
            'overall_bad_rate_str': f"{overall_bad_rate * 100:.2f}%",  # 字符串格式
            'n_models': len(all_results),
            'n_bins': n_bins,
        },
        'summary_df': summary_df.to_dict(orient='records'),
        'all_results': [
            {
                'model_name': r['model_name'],
                'auc': r['auc'],
                'ks': r['ks'],
                'sort_desc': r['sort_desc'],
                'binning_df': r['binning_df'].to_dict(orient='records')
            }
            for r in all_results
        ],
        'charts': charts,
        'model_summary_table': model_summary_html,
        'binning_details': binning_details,
        'decision_tree': decision_tree,
        'top_models': summary_df.head(5)['模型名称'].tolist() if len(summary_df) > 0 else [],
    }


def generate_binning_html_report(analysis_result: dict,
                                 analysis_date: str = '') -> str:
    """Generate enhanced model report HTML (binning + decision tree)."""
    data_summary = analysis_result.get('data_summary', {})
    charts = analysis_result.get('charts', {})
    model_summary_table = analysis_result.get('model_summary_table', '')
    all_results = analysis_result.get('all_results', []) or []
    decision_tree = analysis_result.get('decision_tree', {}) or {}

    def parse_pct(v):
        if isinstance(v, str):
            try:
                return float(v.replace('%', '').strip())
            except Exception:
                return 0.0
        try:
            return float(v)
        except Exception:
            return 0.0

    def pick(row, keys, default=''):
        for key in keys:
            if key in row:
                return row.get(key, default)
        return default

    def pick_by_index(row, index, default=''):
        keys = list(row.keys())
        if 0 <= index < len(keys):
            return row.get(keys[index], default)
        return default

    def img_tag(name, caption):
        data = charts.get(name, '')
        if not data:
            return ''
        return (
            f'<div class="img-box"><img src="data:image/png;base64,{data}" alt="{name}">'
            f'<div class="caption">{caption}</div></div>'
        )

    model_sections = []
    strategy_points = []
    for model_item in all_results:
        model_name = model_item.get('model_name', 'unknown_model')
        auc = float(model_item.get('auc', 0) or 0)
        ks = float(model_item.get('ks', 0) or 0)
        sort_desc = str(model_item.get('sort_desc', '') or '')
        rows = model_item.get('binning_df', []) or []

        bad_rate_values = [parse_pct(pick(row, ['bad_rate'], pick_by_index(row, 8, '0%'))) for row in rows]
        max_bad_rate = max(bad_rate_values) if bad_rate_values else 1.0

        table_rows = ''
        for row in rows:
            bin_no = pick(row, ['bin'], pick_by_index(row, 0, '--'))
            score_min = pick(row, ['score_min'], pick_by_index(row, 1, '--'))
            score_max = pick(row, ['score_max'], pick_by_index(row, 2, '--'))
            sample_cnt = int(float(pick(row, ['sample_count'], pick_by_index(row, 3, 0)) or 0))
            bad_cnt = int(float(pick(row, ['bad_count'], pick_by_index(row, 4, 0)) or 0))
            bad_rate_text = str(pick(row, ['bad_rate'], pick_by_index(row, 8, '0%')))
            cum_bad_rate_text = str(pick(row, ['cum_bad_rate'], pick_by_index(row, 7, '0%')))
            lift = pick(row, ['Lift', 'lift'], pick_by_index(row, 9, 0))
            cum_ks = pick(row, ['cum_ks'], pick_by_index(row, 10, 0))
            bad_rate_num = parse_pct(bad_rate_text)
            bar_width = 0 if max_bad_rate <= 0 else min(100, bad_rate_num / max_bad_rate * 100)
            table_rows += (
                f'<tr><td>{bin_no}</td><td>{score_min}</td><td>{score_max}</td><td>{sample_cnt:,}</td><td>{bad_cnt:,}</td>'
                f'<td><span class="rate-bar-track"><span class="rate-bar-fill" style="width:{bar_width:.1f}%"></span></span>{bad_rate_text}</td>'
                f'<td>{cum_bad_rate_text}</td><td>{lift}</td><td>{cum_ks}</td></tr>'
            )

        model_sections.append(
            f'<div class="section"><h2>📌 {model_name} Binning Detail</h2>'
            f'<div class="meta-line">AUC: <b>{auc:.4f}</b> | KS: <b>{ks:.4f}</b> | Sort Rule: <b>{sort_desc}</b></div>'
            '<table class="data-table"><thead><tr><th>Bin</th><th>Score Min</th><th>Score Max</th><th>Samples</th><th>Bad Samples</th><th>Bad Rate</th><th>Cum Bad Rate</th><th>Lift</th><th>Cum KS</th></tr></thead>'
            f'<tbody>{table_rows}</tbody></table></div>'
        )

        if ks >= 0.25:
            strategy_points.append(f'{model_name}: KS={ks:.3f}, strong discrimination; suitable for core segmentation.')
        elif ks >= 0.18:
            strategy_points.append(f'{model_name}: KS={ks:.3f}, usable discrimination; combine with rules for decisions.')
        else:
            strategy_points.append(f'{model_name}: KS={ks:.3f}, weak discrimination; use as secondary ranking feature.')

    tree_image = decision_tree.get('image', '')
    leaf_nodes = decision_tree.get('leaf_nodes', []) or []
    leaf_rows = ''.join(
        f"<tr><td>{leaf.get('leaf_id', '--')}</td><td>{leaf.get('sample_count', 0):,}</td><td>{leaf.get('bad_count', 0):,}</td><td>{leaf.get('bad_rate', 0) * 100:.2f}%</td></tr>"
        for leaf in leaf_nodes
    )
    tree_image_html = f'<img src="data:image/png;base64,{tree_image}" class="tree-image" alt="model decision tree">' if tree_image else '<p>No decision tree image available.</p>'

    total_samples = int(data_summary.get('total_samples', 0) or 0)
    total_bad = int(data_summary.get('total_bad', 0) or 0)
    overall_bad_rate = data_summary.get('overall_bad_rate_str') or data_summary.get('overall_bad_rate', 'N/A')
    n_models = int(data_summary.get('n_models', 0) or 0)
    n_bins = int(data_summary.get('n_bins', 0) or 0)

    html = f"""<!DOCTYPE html>
<html lang='zh-CN'><head><meta charset='UTF-8'><meta name='viewport' content='width=device-width, initial-scale=1.0'><title>Model Analysis Report</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}} body{{font-family:'Microsoft YaHei','PingFang SC',sans-serif;background:#f5f7fb;color:#1f2937}}
.header{{background:linear-gradient(135deg,#1e3a8a,#2563eb);color:#fff;padding:32px 40px}} .header h1{{font-size:28px;margin-bottom:8px}} .header p{{font-size:14px;opacity:.92}}
.container{{max-width:1320px;margin:0 auto;padding:24px 20px 36px}} .kpi-grid{{display:grid;grid-template-columns:repeat(5,1fr);gap:14px;margin-bottom:18px}}
.kpi{{background:#fff;border-radius:12px;padding:18px;text-align:center;box-shadow:0 2px 10px rgba(0,0,0,.06)}} .kpi .val{{font-size:24px;font-weight:700;color:#1d4ed8}} .kpi .lbl{{font-size:12px;color:#64748b;margin-top:6px}}
.section{{background:#fff;border-radius:12px;padding:20px 22px;margin-bottom:18px;box-shadow:0 2px 10px rgba(0,0,0,.06)}} .section h2{{font-size:18px;color:#0f172a;margin-bottom:10px;padding-bottom:8px;border-bottom:2px solid #e2e8f0}}
.section p,.section li{{line-height:1.8;font-size:14px;color:#475569}} .section ul{{padding-left:20px;margin-top:6px}}
.img-grid{{display:grid;grid-template-columns:1fr 1fr;gap:14px}} .img-box{{background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;overflow:hidden}} .img-box img{{width:100%;display:block}} .caption{{padding:8px 10px;font-size:12px;color:#64748b}}
.meta-line{{font-size:13px;color:#334155;margin-bottom:10px}} .data-table{{width:100%;border-collapse:collapse;font-size:13px}} .data-table th{{background:#eff6ff;color:#1e3a8a;padding:9px 10px;text-align:left}} .data-table td{{padding:8px 10px;border-bottom:1px solid #e5e7eb}} .data-table tr:nth-child(even){{background:#f8fafc}}
.rate-bar-track{{width:136px;height:10px;border-radius:999px;background:#e5e7eb;overflow:hidden;display:inline-block;vertical-align:middle;margin-right:8px}} .rate-bar-fill{{height:100%;background:linear-gradient(90deg,#f59e0b,#dc2626);display:block}}
.tree-image-wrap{{background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;padding:10px;margin-bottom:10px}} .tree-image{{width:100%;height:auto;display:block}} footer{{text-align:center;color:#94a3b8;font-size:12px;padding-top:8px}}
</style></head><body>
<div class='header'><h1>📊 Model Analysis Report (Binning + Decision Tree)</h1><p>Date: {analysis_date} | Samples: {total_samples:,} | Overall Bad Rate: {overall_bad_rate}</p></div>
<div class='container'>
<div class='kpi-grid'><div class='kpi'><div class='val'>{total_samples:,}</div><div class='lbl'>Total Samples</div></div><div class='kpi'><div class='val'>{total_bad:,}</div><div class='lbl'>Bad Samples</div></div><div class='kpi'><div class='val'>{overall_bad_rate}</div><div class='lbl'>Overall Bad Rate</div></div><div class='kpi'><div class='val'>{n_models}</div><div class='lbl'>Models</div></div><div class='kpi'><div class='val'>{n_bins}</div><div class='lbl'>Bins</div></div></div>
<div class='section'><h2>🌲 Decision Tree Analysis</h2><p>Tree depth is capped at 3 and minimum leaf sample is 10 for stable segmentation.</p><div class='tree-image-wrap'>{tree_image_html}</div><table class='data-table'><thead><tr><th>Leaf Node</th><th>Samples</th><th>Bad Samples</th><th>Bad Rate</th></tr></thead><tbody>{leaf_rows or '<tr><td colspan="4">No leaf-node details.</td></tr>'}</tbody></table></div>
{''.join(model_sections)}
<div class='section'><h2>🏆 Model Summary</h2>{model_summary_table}</div>
<div class='section'><h2>📈 Performance Charts</h2><div class='img-grid'>{img_tag('01_模型排名', 'AUC/KS ranking comparison')}{img_tag('02_逾期率对比', 'Top-6 model bad-rate by bins')}</div></div>
<div class='section'><h2>🧭 Strategy Recommendations</h2><ul>{''.join(f'<li>{p}</li>' for p in strategy_points[:8])}<li>For high-bad-rate bins, apply joint controls: lower limit + manual review + anti-fraud checks.</li><li>Use cumulative bad-rate inflection bins as approval threshold candidates and monitor migration weekly.</li><li>Sorting rule is explicit: max score > 1 uses descending; max score ≤ 1 uses ascending.</li></ul></div>
</div><footer>RiskPilot · Model Analysis Report</footer></body></html>"""
    return html

