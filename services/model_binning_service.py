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
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from sklearn.metrics import roc_auc_score, roc_curve

warnings.filterwarnings('ignore')

# 中文字体
plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'WenQuanYi Micro Hei']
plt.rcParams['axes.unicode_minus'] = False


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

    for bin_num in range(1, n_bins + 1):
        bin_data = data[data['bin'] == bin_num]

        if len(bin_data) == 0:
            continue

        # 基础统计
        n_samples = len(bin_data)
        n_bad = bin_data[label_col].sum()
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

    for model_col in model_cols:
        try:
            # 执行分箱
            binning_df, auc, ks, sort_desc = equal_freq_binning(
                df, model_col, label_col, n_bins, threshold
            )

            # 保存结果
            all_results.append({
                'model_name': model_col,
                'binning_df': binning_df,
                'auc': auc,
                'ks': ks,
                'sort_desc': sort_desc
            })

            model_summary.append({
                '模型名称': model_col,
                'AUC': round(auc, 4),
                'KS': round(ks, 4),
                '排序方式': sort_desc
            })

        except Exception as e:
            print(f"  [错误] {model_col} 分析失败: {str(e)}")
            continue

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
    fig.savefig(buf, format='png', dpi=150, bbox_inches='tight',
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
    
    # 每个模型的详情图
    for i, result in enumerate(all_results[:6]):  # 最多6个
        charts[f'03_{result["model_name"]}_分箱详情'] = plot_binning_detail(
            result['binning_df'],
            result['model_name'],
            result['auc'],
            result['ks'],
            result['sort_desc']
        )
    
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
        'top_models': summary_df.head(5)['模型名称'].tolist() if len(summary_df) > 0 else [],
    }


def generate_binning_html_report(analysis_result: dict, 
                                 analysis_date: str = '') -> str:
    """生成模型分箱分析HTML报告"""
    data_summary = analysis_result.get('data_summary', {})
    charts = analysis_result.get('charts', {})
    model_summary_table = analysis_result.get('model_summary_table', '')
    binning_details = analysis_result.get('binning_details', {})
    
    def img_tag(name, caption, grid_class='single'):
        data = charts.get(name, '')
        if not data:
            return ''
        return f'''
  <div class="img-grid {grid_class}">
    <div class="img-box">
      <img src="data:image/png;base64,{data}" alt="{name}">
      <div class="caption">{caption}</div>
    </div>
  </div>'''
    
    # 生成各模型详情HTML
    model_detail_sections = ''
    for model_name, detail_html in binning_details.items():
        model_detail_sections += f'''
<!-- {model_name} 分箱详情 -->
<div class="section">
  <h2>📊 {model_name} 分箱详情</h2>
  {detail_html}
</div>'''
    
    html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>模型分箱分析报告</title>
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{font-family:'Microsoft YaHei','PingFang SC',sans-serif;background:#f0f4f8;color:#1a202c}}
  .header{{background:linear-gradient(135deg,#1e3a5f 0%,#2563eb 100%);color:white;padding:36px 40px}}
  .header h1{{font-size:26px;font-weight:700;margin-bottom:8px}}
  .header p{{font-size:14px;opacity:0.85}}
  .container{{max-width:1300px;margin:0 auto;padding:32px 24px}}
  .kpi-grid{{display:grid;grid-template-columns:repeat(5,1fr);gap:16px;margin-bottom:32px}}
  .kpi{{background:white;border-radius:12px;padding:20px 16px;text-align:center;box-shadow:0 2px 8px rgba(0,0,0,.06)}}
  .kpi .val{{font-size:28px;font-weight:700;color:#2563eb}}
  .kpi .lbl{{font-size:12px;color:#64748b;margin-top:4px}}
  .section{{background:white;border-radius:14px;padding:28px 28px 20px;box-shadow:0 2px 10px rgba(0,0,0,.06);margin-bottom:28px}}
  .section h2{{font-size:18px;font-weight:700;color:#1e3a5f;margin-bottom:6px;padding-bottom:10px;border-bottom:2px solid #e2e8f0}}
  .section p,.section li{{font-size:14px;color:#475569;line-height:1.8}}
  .section ul{{padding-left:20px;margin-top:8px}}
  .img-grid{{display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-top:16px}}
  .img-grid.single{{grid-template-columns:1fr}}
  .img-box{{border-radius:10px;overflow:hidden;box-shadow:0 2px 6px rgba(0,0,0,.08)}}
  .img-box img{{width:100%;display:block}}
  .img-box .caption{{background:#f8fafc;padding:8px 12px;font-size:12px;color:#64748b;text-align:center}}
  .data-table{{width:100%;border-collapse:collapse;font-size:13px;margin-top:12px}}
  .data-table th{{background:#1e3a5f;color:white;padding:10px 12px;text-align:left}}
  .data-table td{{padding:8px 12px;border-bottom:1px solid #e2e8f0}}
  .data-table tr:nth-child(even){{background:#f8fafc}}
  footer{{text-align:center;padding:24px;color:#94a3b8;font-size:12px}}
</style>
</head>
<body>
<div class="header">
  <h1>📊 模型分箱分析报告</h1>
  <p>样本量：{data_summary.get('total_samples', 0):,} &nbsp;|&nbsp; 整体逾期率：{data_summary.get('overall_bad_rate', 'N/A')} &nbsp;|&nbsp; 模型数量：{data_summary.get('n_models', 0)} 个 &nbsp;|&nbsp; 分箱数：{data_summary.get('n_bins', 10)} &nbsp;|&nbsp; 分析日期：{analysis_date}</p>
</div>

<div class="container">

<!-- KPI -->
<div class="kpi-grid">
  <div class="kpi"><div class="val">{data_summary.get('total_samples', 0):,}</div><div class="lbl">总样本量</div></div>
  <div class="kpi"><div class="val">{data_summary.get('total_bad', 0):,}</div><div class="lbl">坏样本数</div></div>
  <div class="kpi"><div class="val">{data_summary.get('overall_bad_rate', 'N/A')}</div><div class="lbl">整体逾期率</div></div>
  <div class="kpi"><div class="val">{data_summary.get('n_models', 0)}</div><div class="lbl">分析模型数</div></div>
  <div class="kpi"><div class="val">{data_summary.get('n_bins', 10)}</div><div class="lbl">分箱数</div></div>
</div>

<!-- 模型汇总 -->
<div class="section">
  <h2>🏆 模型综合排名</h2>
  {model_summary_table}
</div>

<!-- 排名图表 -->
<div class="section">
  <h2>📈 模型性能对比图</h2>
  {img_tag('01_模型排名', '左：AUC排名 | 右：KS排名 | 颜色越绿性能越好')}
</div>

<!-- 逾期率对比 -->
<div class="section">
  <h2>📉 Top 6 模型分箱逾期率对比</h2>
  {img_tag('02_逾期率对比', '各箱逾期率对比，颜色越红风险越高')}
</div>

{model_detail_sections}

</div>
<footer>模型分箱分析报告 · 生成于 {analysis_date} · RiskPilot</footer>
</body>
</html>'''
    return html
