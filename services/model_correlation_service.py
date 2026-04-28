"""
模型相关性分析服务
严格按照 模型相关性分析.py 的计算口径实现：
- 模型基础性能评估（覆盖率 / AUC / KS / 逾期率）
- Spearman 相关性热力图 + 层次聚类树状图
- 模型互补性矩阵（量化捞回潜力）
- 串行拒绝策略模拟
- Top-N ROC 曲线对比
- 分数分布对比图
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
from scipy.cluster.hierarchy import linkage, dendrogram, fcluster
from scipy.spatial.distance import squareform
from scipy.stats import spearmanr
from sklearn.metrics import roc_curve, auc as sk_auc, roc_auc_score
from services.model_binning_service import equal_freq_binning, build_model_decision_tree

warnings.filterwarnings('ignore')

# 中文字体
plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'WenQuanYi Micro Hei']
plt.rcParams['axes.unicode_minus'] = False


def _compute_single_model_performance(
    df: pd.DataFrame,
    target_col: str,
    col: str,
    n_total: int,
) -> dict:
    """单模型性能计算任务（供并发调用）。"""
    tmp = df[[col, target_col]].dropna()
    if len(tmp) < 10:
        return {}

    y = tmp[target_col].values
    s = tmp[col].values

    max_score = np.max(s)
    adjusted_score = -s if max_score > 1 else s

    try:
        auc = roc_auc_score(y, adjusted_score)
    except Exception:
        auc = 0.5

    try:
        fpr, tpr, _ = roc_curve(y, adjusted_score)
        ks = float(max(tpr - fpr))
    except Exception:
        ks = 0.0

    bad_unique = np.unique(y)
    bad_rate = float(np.mean(y == 1)) if 1 in bad_unique else (
        float(np.mean(y == -1)) if -1 in bad_unique else float(y.mean())
    )

    return {
        'model': col,
        'coverage': round(len(tmp) / n_total, 4),
        'auc': round(auc, 4),
        'ks': round(ks, 4),
        'bad_rate': round(bad_rate, 4),
        'n': len(tmp),
    }


def _fig_to_b64(fig) -> str:
    """将图表转换为 base64"""
    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=120, bbox_inches='tight',
                facecolor='white', edgecolor='none')
    buf.seek(0)
    img_b64 = base64.b64encode(buf.read()).decode('utf-8')
    plt.close(fig)
    return img_b64


def compute_model_performance(df: pd.DataFrame, target_col: str, score_cols: list) -> pd.DataFrame:
    """计算各模型的覆盖率 / AUC / KS / 逾期率"""
    n_total = len(df)
    if not score_cols:
        return pd.DataFrame()

    records = []
    max_workers = min(8, len(score_cols))
    if max_workers <= 1:
        for col in score_cols:
            rec = _compute_single_model_performance(df, target_col, col, n_total)
            if rec:
                records.append(rec)
    else:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(_compute_single_model_performance, df, target_col, col, n_total): col
                for col in score_cols
            }
            for future in as_completed(futures):
                try:
                    rec = future.result()
                    if rec:
                        records.append(rec)
                except Exception:
                    continue

    if not records:
        return pd.DataFrame()
    return pd.DataFrame(records).sort_values('ks', ascending=False).reset_index(drop=True)


def compute_correlation(df: pd.DataFrame, score_cols: list) -> tuple:
    """计算 Spearman 相关性矩阵、聚类顺序和聚类分组结果"""
    df_score = df[score_cols].dropna()
    if len(df_score) < 10:
        return pd.DataFrame(index=score_cols, columns=score_cols), score_cols, {}

    # Spearman 相关性
    corr_mat, _ = spearmanr(df_score.values, axis=0)
    corr_mat = pd.DataFrame(corr_mat, index=score_cols, columns=score_cols)

    # 层次聚类排序
    dist = 1 - np.triu(corr_mat.values, k=1)
    dist = dist + dist.T
    np.fill_diagonal(dist, 0)
    dist = np.clip(dist, 0, None)

    cluster_groups = {}
    if dist.sum() == 0:
        cluster_order = score_cols
    else:
        try:
            Z = linkage(squareform(dist), method='average')
            from scipy.cluster.hierarchy import leaves_list
            cluster_order = [score_cols[i] for i in leaves_list(Z)]
            # 自动聚类分组（使用最大距离的一半作为阈值）
            if len(Z) > 0:
                max_dist = Z[:, 2].max()
                threshold = max_dist * 0.7
                if threshold < 0.1:
                    threshold = 0.3  # 最小阈值保护
                cluster_labels = fcluster(Z, t=threshold, criterion='distance')
                for label_val in sorted(set(cluster_labels)):
                    members = [cluster_order[i] for i in range(len(cluster_order))
                               if cluster_labels[i] == label_val]
                    cluster_groups[f'聚类{label_val}'] = members
        except Exception:
            cluster_order = score_cols

    corr_ordered = corr_mat.loc[cluster_order, cluster_order]
    return corr_ordered, cluster_order, cluster_groups


def compute_complementarity(df: pd.DataFrame, target_col: str, score_cols: list) -> pd.DataFrame:
    """
    互补性矩阵计算：
    以各模型分位数20%为"拒绝"，计算 A拒B不拒 + B拒A不拒 的比例
    """
    records = []
    df_clean = df.dropna(subset=score_cols + [target_col])
    if df_clean.empty or len(score_cols) < 2:
        return pd.DataFrame(records)

    # 预计算每个模型的拒绝掩码，避免重复 percentile 计算
    reject_masks = {}
    for col in score_cols:
        s = df_clean[col].values
        q20 = np.percentile(s, 20)
        reject_masks[col] = s < q20

    for i, col_a in enumerate(score_cols):
        reject_a = reject_masks[col_a]
        for j, col_b in enumerate(score_cols):
            if i >= j:
                continue
            reject_b = reject_masks[col_b]

            # A拒B不拒
            a_not_b = reject_a & (~reject_b)
            # B拒A不拒
            b_not_a = reject_b & (~reject_a)

            comp_a = a_not_b.mean()
            comp_b = b_not_a.mean()
            comp_total = comp_a + comp_b

            records.append({
                'model_a': col_a,
                'model_b': col_b,
                'a_reject_b_not': round(comp_a, 4),
                'b_reject_a_not': round(comp_b, 4),
                'complementarity': round(comp_total, 4),
            })

    return pd.DataFrame(records).sort_values('complementarity', ascending=False).reset_index(drop=True)


def compute_strategy_simulation(df: pd.DataFrame, target_col: str, score_cols: list, perf_df: pd.DataFrame) -> dict:
    """串行策略模拟"""
    if len(perf_df) < 2:
        return {'main_model': None, 'strategies': []}

    main_model = perf_df.iloc[0]['model']
    df_clean = df.dropna(subset=score_cols + [target_col])
    y = df_clean[target_col].values
    s_main = df_clean[main_model].values

    strategies = []
    for q_pct in [10, 15, 20, 25, 30]:
        # 仅主模型
        q = np.percentile(s_main, q_pct)
        passed_main = s_main >= q
        bad_main = y[passed_main].mean() if passed_main.sum() > 0 else 0

        strategies.append({
            'reject_rate': q_pct,
            'strategy': f"仅主模型 q={q_pct}%",
            'pass_rate': round(passed_main.mean(), 4),
            'pass_bad_rate': round(float(bad_main), 4),
            'rescue_count': 0,
            'rescue_bad_rate': None,
        })

        # 各候选捞回
        for col in score_cols:
            if col == main_model:
                continue
            s_sub = df_clean[col].values
            rejected_mask = ~passed_main
            s_rej = s_sub[rejected_mask]
            y_rej = y[rejected_mask]

            if len(s_rej) < 10:
                continue

            q_rej = np.percentile(s_rej, 50)
            rescued_in_rej = s_rej >= q_rej

            rescued_mask = np.zeros(len(y), dtype=bool)
            rescued_mask[rejected_mask] = rescued_in_rej

            rescued_bad = y_rej[rescued_in_rej].mean() if rescued_in_rej.sum() > 0 else 0
            all_passed = passed_main | rescued_mask
            all_bad = y[all_passed].mean() if all_passed.sum() > 0 else 0

            strategies.append({
                'reject_rate': q_pct,
                'strategy': f"主模型q={q_pct}% + {col}捞回",
                'pass_rate': round(all_passed.mean(), 4),
                'pass_bad_rate': round(float(all_bad), 4),
                'rescue_count': int(rescued_mask.sum()),
                'rescue_bad_rate': round(float(rescued_bad), 4),
            })

    return {
        'main_model': main_model,
        'strategies': strategies,
    }


# ─── 图表生成 ──────────────────────────────────────────────────────────────────

def plot_performance_bubble(perf: pd.DataFrame) -> str:
    """气泡图：X=AUC, Y=KS, size=覆盖率"""
    fig, ax = plt.subplots(figsize=(10, 7))
    # 确保 bad_rate 有效
    perf_clean = perf.copy()
    perf_clean['bad_rate'] = pd.to_numeric(perf_clean['bad_rate'], errors='coerce').fillna(0)
    sc = ax.scatter(
        perf_clean['auc'], perf_clean['ks'],
        s=perf_clean['coverage'] * 800 + 50,
        c=perf_clean['bad_rate'], cmap='RdYlGn_r',
        alpha=0.75, edgecolors='white', linewidths=1.5, zorder=3
    )
    for _, r in perf.iterrows():
        ax.annotate(r['model'], (r['auc'], r['ks']),
                    fontsize=8, ha='center', va='bottom',
                    xytext=(0, 5), textcoords='offset points')
    plt.colorbar(sc, label='逾期率', shrink=0.8)
    # 参考线
    ax.axhline(0.15, color='gray', linestyle='--', linewidth=0.8, alpha=0.5)
    ax.axvline(0.5, color='gray', linestyle='--', linewidth=0.8, alpha=0.5)
    # 坐标范围根据数据自适应
    auc_vals = perf['auc'].values
    ks_vals = perf['ks'].values
    auc_min, auc_max = auc_vals.min(), auc_vals.max()
    ks_min, ks_max = ks_vals.min(), ks_vals.max()
    auc_pad = max((auc_max - auc_min) * 0.15, 0.01)
    ks_pad = max((ks_max - ks_min) * 0.15, 0.01)
    ax.set_xlim(auc_min - auc_pad, auc_max + auc_pad)
    ax.set_ylim(ks_min - ks_pad, ks_max + ks_pad)
    ax.set_xlabel('AUC', fontsize=12)
    ax.set_ylabel('KS', fontsize=12)
    ax.set_title('多模型性能气泡图（气泡大小=覆盖率，颜色=逾期率）', fontsize=14, pad=12)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    return _fig_to_b64(fig)


def plot_correlation_heatmap(corr: pd.DataFrame, order: list) -> str:
    """Spearman 相关性热力图（下半三角 + 白到蓝渐变）"""
    if corr.empty:
        return ''
    vals = corr.values.copy()
    n = len(order)
    # 只保留下半三角，上半三角置为 NaN
    for i in range(n):
        for j in range(i + 1, n):
            vals[i, j] = np.nan
    fig, ax = plt.subplots(figsize=(max(n * 0.7, 6), max(n * 0.6, 5)))
    # 自定义白到蓝渐变色
    from matplotlib.colors import LinearSegmentedColormap
    blue_cmap = LinearSegmentedColormap.from_list('white_blue', ['#ffffff', '#d4e6f1', '#85c1e9', '#2e86c1', '#1b4f72'])
    im = ax.imshow(vals, cmap=blue_cmap, vmin=0, vmax=1, aspect='auto')
    ax.set_xticks(range(n))
    ax.set_yticks(range(n))
    ax.set_xticklabels(order, rotation=45, ha='right', fontsize=8)
    ax.set_yticklabels(order, fontsize=8)
    plt.colorbar(im, ax=ax, shrink=0.8, label='Spearman ρ')
    for i in range(n):
        for j in range(n):
            if np.isnan(vals[i, j]):
                continue
            val = vals[i, j]
            color = 'white' if val > 0.75 else 'black'
            ax.text(j, i, f'{val:.2f}', ha='center', va='center',
                    fontsize=7, color=color)
    ax.set_title('模型间 Spearman 相关性热力图（聚类排序，下半三角）', fontsize=13, pad=10)
    fig.tight_layout()
    return _fig_to_b64(fig)


def plot_dendrogram(corr: pd.DataFrame, order: list, cluster_groups: dict = None) -> str:
    """层次聚类树状图（按聚类分组着丰富颜色，包括连线）"""
    if corr.empty or len(order) < 2:
        return ''
    n = len(order)
    dist = 1 - np.triu(corr.values, k=1)
    dist = dist + dist.T
    np.fill_diagonal(dist, 0)
    dist = np.clip(dist, 0, None)
    if dist.sum() == 0:
        return ''
    try:
        condensed = squareform(dist)
        Z = linkage(condensed, method='average')
    except Exception:
        return ''

    # 丰富颜色：为每个叶子节点按聚类分组分配颜色
    rich_colors = ['#E74C3C', '#2ECC71', '#3498DB', '#F39C12', '#9B59B6',
                   '#1ABC9C', '#E67E22', '#2980B9', '#C0392B', '#27AE60']
    # 将 cluster_groups 转为 model -> color_index 的映射
    model_color_map = {}
    if cluster_groups:
        for idx, (cname, members) in enumerate(cluster_groups.items()):
            for m in members:
                model_color_map[m] = rich_colors[idx % len(rich_colors)]
    # 构造 leaf 颜色列表
    from scipy.cluster.hierarchy import leaves_list
    leaf_idx = leaves_list(Z)
    leaf_labels = [order[i] for i in leaf_idx]
    xlv_colors = [model_color_map.get(lbl, '#7F8C8D') for lbl in leaf_labels]

    fig, ax = plt.subplots(figsize=(max(n * 0.8, 8), 5))
    # color_threshold=0 让 scipy 不自动着色，全部用灰色先画
    R = dendrogram(
        Z, labels=leaf_labels, ax=ax,
        leaf_rotation=45, leaf_font_size=9,
        color_threshold=0,
        above_threshold_color='#BDC3C7',
    )
    # 给叶子标签着色
    xlbls = ax.get_xticklabels()
    for lbl_obj, c in zip(xlbls, xlv_colors):
        lbl_obj.set_color(c)
        lbl_obj.set_fontweight('bold')

    # 给所有分支线按叶子颜色着色
    # scipy dendrogram 返回的 icoord/dcoord 坐标中，
    # 每个合并步骤的 x 坐标为 (left_x, left_x, merge_x, merge_x)
    # 合并 x=2 处的值为内部节点的 x 坐标
    icoord = np.array(R['icoord'])
    dcoord = np.array(R['dcoord'])

    for i in range(len(icoord)):
        # 找这个合并连接的两个子节点叶子
        merge_x = icoord[i][2]  # 内部节点的 x 坐标（居中）
        # 左子节点：icoord[i][0]（左叶的 x）或 icoord[i][1]（左叶顶端）
        # 右子节点：icoord[i][3]（右叶顶端）
        # 找最近的叶子 x 坐标来确定颜色
        all_leaf_x = [icoord[j][0] for j in range(len(icoord)) if i != j]
        left_child_x = icoord[i][0]
        right_child_x = icoord[i][3]

        # 找到左右子节点最近的叶子颜色
        def find_color(x_target):
            best_dist = float('inf')
            best_color = '#BDC3C7'
            for j, lx in enumerate(xlv_colors):
                leaf_x_pos = icoord[j][0] if j < len(icoord) else None
                if leaf_x_pos is None:
                    continue
                d = abs(leaf_x_pos - x_target)
                if d < best_dist:
                    best_dist = d
                    best_color = lx
            # 同时检查所有 icoord 中的叶子位置
            for j in range(len(icoord)):
                for k in [0]:  # 每个分支的第0个x是叶子位置
                    lx = icoord[j][k]
                    d = abs(lx - x_target)
                    if d < best_dist:
                        best_dist = d
            # 用最近的叶子标签颜色
            return best_color

        # 更简洁的方法：遍历每个合并，找到其覆盖的最下方叶子
        # 用递归查找：从 merge_i 出发，找到所有属于它的叶子
        # 由于 Z 是 scipy linkage 矩阵，n-1 行，每行 (left, right, dist, count)
        def get_leaves(merge_idx, Z_mat, n_leaves):
            """获取合并步骤 merge_idx 包含的所有原始叶子索引"""
            if merge_idx < n_leaves:
                return [merge_idx]
            left = int(Z_mat[merge_idx - n_leaves, 0])
            right = int(Z_mat[merge_idx - n_leaves, 1])
            return get_leaves(left, Z_mat, n_leaves) + get_leaves(right, Z_mat, n_leaves)

        # 找这个 icoord 行对应的 Z 矩阵行
        z_row = i  # dendrogram 返回顺序和 Z 顺序一致
        leaves = get_leaves(z_row + n, Z, n)
        # 取多数叶子颜色作为该分支颜色
        leaf_color_counts = {}
        for li in leaves:
            if li < len(xlv_colors):
                c = xlv_colors[li]
                leaf_color_counts[c] = leaf_color_counts.get(c, 0) + 1
        if leaf_color_counts:
            branch_color = max(leaf_color_counts, key=leaf_color_counts.get)
        else:
            branch_color = '#BDC3C7'

        # 画这个合并步骤的3条线段
        # 水平线：从 (left_x, merge_y) 到 (right_x, merge_y) 不画（scipy 已经画了灰色）
        # 需要覆盖：先删除旧的再画新的
        xs = icoord[i]
        ys = dcoord[i]
        # 画 3 条线段：左竖线、水平线、右竖线
        ax.plot([xs[0], xs[1]], [ys[0], ys[1]], color=branch_color, linewidth=2, zorder=4)
        ax.plot([xs[1], xs[2]], [ys[1], ys[2]], color=branch_color, linewidth=2, zorder=4)
        ax.plot([xs[2], xs[3]], [ys[2], ys[3]], color=branch_color, linewidth=2, zorder=4)

    ax.set_title('模型相似度层次聚类树状图（同颜色=同一簇）', fontsize=13, pad=10)
    ax.set_ylabel('距离（1 - Spearman ρ）', fontsize=11)
    fig.tight_layout()
    return _fig_to_b64(fig)


def plot_complementarity_matrix(complement: pd.DataFrame, score_cols: list) -> str:
    """互补性热力图"""
    if complement.empty:
        return ''
    models = score_cols
    n = len(models)
    mat = np.full((n, n), np.nan)
    idx = {m: i for i, m in enumerate(models)}
    for _, r in complement.iterrows():
        i, j = idx[r['model_a']], idx[r['model_b']]
        mat[i, j] = r['complementarity']
        mat[j, i] = r['complementarity']
    fig, ax = plt.subplots(figsize=(max(n * 0.8, 7), max(n * 0.7, 6)))
    im = ax.imshow(mat, cmap='YlOrRd', vmin=0, aspect='auto')
    ax.set_xticks(range(n))
    ax.set_yticks(range(n))
    ax.set_xticklabels(models, rotation=45, ha='right', fontsize=8)
    ax.set_yticklabels(models, fontsize=8)
    plt.colorbar(im, ax=ax, shrink=0.8, label='互补性指数')
    for i in range(n):
        for j in range(n):
            if not np.isnan(mat[i, j]):
                color = 'white' if mat[i, j] > 0.3 else 'black'
                ax.text(j, i, f'{mat[i,j]:.2f}', ha='center', va='center',
                        fontsize=7, color=color)
    ax.set_title('模型互补性矩阵（值越大=捞回潜力越高）', fontsize=13, pad=10)
    fig.tight_layout()
    return _fig_to_b64(fig)


def plot_strategy_chart(df: pd.DataFrame, target_col: str, score_cols: list, metrics: dict) -> str:
    """串行策略效果图"""
    strategies = metrics.get('strategies', [])
    if not strategies:
        return ''
    df_s = pd.DataFrame(strategies)
    df_main = df_s[df_s['rescue_count'] == 0]
    df_rescue = df_s[df_s['rescue_count'] > 0]

    fig, ax = plt.subplots(figsize=(10, 7))
    ax.plot(df_main['pass_rate'] * 100, df_main['pass_bad_rate'] * 100,
            'ko-', linewidth=2, markersize=8, label='仅主模型', zorder=5)

    colors = plt.cm.Set2(np.linspace(0, 1, len(df_rescue)))
    for idx, (_, row) in enumerate(df_rescue.iterrows()):
        ax.scatter(row['pass_rate'] * 100, row['pass_bad_rate'] * 100,
                   color=colors[idx], s=60, alpha=0.8, zorder=3)
        q = row['reject_rate']
        base_row = df_main[df_main['reject_rate'] == q]
        if not base_row.empty:
            bx = base_row['pass_rate'].values[0] * 100
            by = base_row['pass_bad_rate'].values[0] * 100
            ax.annotate('', xy=(row['pass_rate'] * 100, row['pass_bad_rate'] * 100),
                        xytext=(bx, by),
                        arrowprops=dict(arrowstyle='->', color='gray', alpha=0.5))

    overall_bad_rate = df[target_col].mean() * 100
    ax.axhline(overall_bad_rate, ls='--', color='red', lw=1.2, alpha=0.7,
               label=f'全量逾期率 {overall_bad_rate:.1f}%')

    ax.set_xlabel('通过率（%）', fontsize=12)
    ax.set_ylabel('通过逾期率（%）', fontsize=12)
    ax.set_title(f'串行策略模拟（主模型：{metrics.get("main_model","?")}）',
                 fontsize=14, pad=12)
    ax.legend(fontsize=10)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    return _fig_to_b64(fig)


def plot_roc_curves(df: pd.DataFrame, target_col: str, score_cols: list, perf: pd.DataFrame) -> str:
    """Top 5 ROC 曲线对比（按 AUC 排序，使用性能表中计算的 AUC 值）"""
    # 按 AUC 排序取前5
    perf_auc = perf.sort_values('auc', ascending=False)
    top5 = perf_auc.head(min(5, len(perf_auc)))
    df_clean = df.dropna(subset=score_cols + [target_col])
    y = df_clean[target_col].values

    fig, ax = plt.subplots(figsize=(8, 8))
    colors = plt.cm.tab10(np.linspace(0, 1, len(top5)))
    for idx, (_, row) in enumerate(top5.iterrows()):
        col = row['model']
        s = df_clean[col].dropna().values
        y_valid = df_clean[target_col].loc[df_clean[col].notna()].values
        if len(s) < 10:
            ax.plot([], [], color=colors[idx], linewidth=2, label=f"{col} (数据不足)")
            continue
        try:
            # 与 compute_model_performance 保持一致的方向逻辑
            max_score = np.max(s)
            if max_score > 1:
                s_plot = -s  # 分数越高风险越低，取反
            else:
                s_plot = s  # 概率分数，高分=高风险
            fpr, tpr, _ = roc_curve(y_valid, s_plot)
            # 使用性能表中已计算的 AUC，确保与详细数据一致
            roc_auc = row['auc']
            ax.plot(fpr, tpr, color=colors[idx], linewidth=2,
                    label=f"{col} (AUC={roc_auc:.4f})")
        except Exception:
            ax.plot([], [], color=colors[idx], linewidth=2, label=f"{col} (数据异常)")

    ax.plot([0, 1], [0, 1], 'k--', linewidth=1, label='随机 (AUC=0.500)')
    ax.set_xlabel('FPR（假阳性率）', fontsize=12)
    ax.set_ylabel('TPR（真阳性率）', fontsize=12)
    ax.set_title('Top5 模型 ROC 曲线对比（按 AUC 排序）', fontsize=14, pad=12)
    ax.legend(loc='lower right', fontsize=10)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    return _fig_to_b64(fig)


def plot_score_distributions(df: pd.DataFrame, target_col: str, score_cols: list, perf: pd.DataFrame) -> str:
    """好坏客户分数分布（按 AUC 排序取 Top5）"""
    perf_auc = perf.sort_values('auc', ascending=False)
    top5 = perf_auc.head(min(5, len(perf_auc)))
    df_clean = df.dropna(subset=score_cols + [target_col])
    y = df_clean[target_col].values

    n = len(top5)
    # 根据数量调整布局
    ncols = min(n, 3)
    nrows = (n + ncols - 1) // ncols
    fig, axes = plt.subplots(nrows, ncols, figsize=(5 * ncols, 4 * nrows), squeeze=False)
    colors_good, colors_bad = '#4CAF50', '#F44336'
    bins = 40

    for col_idx, (_, row) in enumerate(top5.iterrows()):
        ax = axes[col_idx // ncols][col_idx % ncols]
        col = row['model']
        s = df_clean[col].dropna().values
        y_valid = df_clean[target_col].loc[df_clean[col].notna()].values
        
        max_score = np.max(s)
        # 与 compute_model_performance 保持一致的方向逻辑
        if max_score > 1:
            s = -s  # 分数越高风险越低，取反
        
        good = s[y_valid == 0]
        bad = s[y_valid == 1]

        good_pct = len(good) / max(len(good) + len(bad), 1) * 100
        bad_pct = len(bad) / max(len(good) + len(bad), 1) * 100

        ax.hist(good, bins=bins, alpha=0.6, color=colors_good,
                label=f'正常 (n={len(good)}, {good_pct:.1f}%)', density=True)
        ax.hist(bad, bins=bins, alpha=0.6, color=colors_bad,
                label=f'逾期 (n={len(bad)}, {bad_pct:.1f}%)', density=True)
        ax.set_title(f'{col} (AUC={row["auc"]:.4f})', fontsize=12)
        ax.set_xlabel('分数', fontsize=10)
        ax.set_ylabel('密度', fontsize=10)
        ax.legend(fontsize=9)
        ax.grid(True, alpha=0.3)

    # 隐藏多余的子图
    for col_idx in range(n, nrows * ncols):
        axes[col_idx // ncols][col_idx % ncols].set_visible(False)

    fig.suptitle('好坏客户分数分布对比（Top5 模型，按 AUC 排序）', fontsize=14, y=1.02)
    fig.tight_layout()
    return _fig_to_b64(fig)


def run_correlation_analysis(df: pd.DataFrame,
                           target_col: str = 'label',
                           score_cols: list = None,
                           missing_thresh: float = 0.3) -> dict:
    """
    执行模型相关性分析

    返回:
        dict: 包含所有分析结果和图表
    """
    # 过滤缺失率过高的模型
    if score_cols is None:
        score_cols = df.select_dtypes(include='number').columns.tolist()
        score_cols = [c for c in score_cols if c != target_col]

    n_total = len(df)
    usable_cols = [c for c in score_cols 
                   if c in df.columns and df[c].notna().mean() >= (1 - missing_thresh)]

    if len(usable_cols) < 2:
        return {'error': '可用模型数量不足（至少需要2个模型）'}

    # 数据清洗
    df_clean = df.copy()
    raw = pd.to_numeric(df_clean[target_col], errors='coerce')
    valid_mask = raw.isin([0, 1, 0.0, 1.0, True, False])
    df_clean.loc[~valid_mask, target_col] = np.nan
    df_clean[target_col] = pd.to_numeric(df_clean[target_col], errors='coerce')

    # 计算各项指标
    perf_df = compute_model_performance(df_clean, target_col, usable_cols)
    corr_df, cluster_order, cluster_groups = compute_correlation(df_clean, usable_cols)
    complement_df = compute_complementarity(df_clean, target_col, usable_cols)
    strategy_metrics = compute_strategy_simulation(df_clean, target_col, usable_cols, perf_df)

    # 生成图表
    charts = {}
    charts['01_模型基础性能'] = plot_performance_bubble(perf_df)
    charts['02_相关性热力图'] = plot_correlation_heatmap(corr_df, cluster_order)
    charts['03_聚类树状图'] = plot_dendrogram(corr_df, cluster_order, cluster_groups)
    charts['04_模型互补性矩阵'] = plot_complementarity_matrix(complement_df, usable_cols)
    charts['05_串行策略效果'] = plot_strategy_chart(df_clean, target_col, usable_cols, strategy_metrics)
    charts['06_ROC曲线对比'] = plot_roc_curves(df_clean, target_col, usable_cols, perf_df)
    charts['07_分数分布'] = plot_score_distributions(df_clean, target_col, usable_cols, perf_df)

    # 生成表格HTML
    perf_table = perf_df[['model', 'coverage', 'auc', 'ks', 'bad_rate', 'n']].copy()
    perf_table.columns = ['模型', '覆盖率', 'AUC', 'KS', '逾期率', '样本量']
    perf_table['覆盖率'] = perf_table['覆盖率'].map(lambda x: f'{x:.1%}')
    perf_table['逾期率'] = perf_table['逾期率'].map(lambda x: f'{x:.1%}' if pd.notna(x) else 'N/A')
    perf_table['AUC'] = perf_table['AUC'].map(lambda x: f'{x:.4f}')
    perf_table['KS'] = perf_table['KS'].map(lambda x: f'{x:.4f}')
    perf_table['样本量'] = perf_table['样本量'].map(lambda x: f'{x:,}')
    perf_table_html = perf_table.to_html(index=False, border=0, classes='data-table')

    # 策略表格
    strategy_table = pd.DataFrame(strategy_metrics.get('strategies', []))
    if len(strategy_table) > 0:
        strategy_table = strategy_table[['strategy', 'pass_rate', 'pass_bad_rate', 'rescue_count', 'rescue_bad_rate']].copy()
        strategy_table.columns = ['策略', '通过率', '通过逾期率', '捞回人数', '捞回逾期率']
        strategy_table['通过率'] = strategy_table['通过率'].map(lambda x: f'{x:.1%}')
        strategy_table['通过逾期率'] = strategy_table['通过逾期率'].map(lambda x: f'{x:.1%}')
        strategy_table['捞回人数'] = strategy_table['捞回人数'].map(lambda x: f'{x:,}')
        strategy_table['捞回逾期率'] = strategy_table['捞回逾期率'].map(
            lambda x: f'{x:.1%}' if pd.notna(x) else '-')
    strategy_table_html = strategy_table.to_html(index=False, border=0, classes='data-table') if len(strategy_table) > 0 else ''

    # 补充：多模型分箱 + 决策树（用于模型分析报告增强）
    model_binning_details = []
    if usable_cols:
        max_workers = min(8, len(usable_cols))
        if max_workers <= 1:
            iter_results = []
            for model_col in usable_cols:
                try:
                    iter_results.append((model_col, equal_freq_binning(
                        df=df_clean,
                        score_col=model_col,
                        label_col=target_col,
                        n_bins=10,
                        threshold=1.0
                    )))
                except Exception:
                    continue
        else:
            iter_results = []
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {
                    executor.submit(
                        equal_freq_binning,
                        df_clean, model_col, target_col, 10, 1.0
                    ): model_col
                    for model_col in usable_cols
                }
                for future in as_completed(futures):
                    model_col = futures[future]
                    try:
                        iter_results.append((model_col, future.result()))
                    except Exception:
                        continue

        # 按原列顺序输出，保持报告稳定
        temp_map = {}
        for model_col, result_tuple in iter_results:
            try:
                binning_df, _, _, sort_desc = result_tuple
                rows = []
                for _, row in binning_df.iterrows():
                    row_dict = row.to_dict()
                    row_values = list(row_dict.values())
                    rows.append({
                        'bin': row_values[0] if len(row_values) > 0 else '',
                        'score_min': row_values[1] if len(row_values) > 1 else '',
                        'score_max': row_values[2] if len(row_values) > 2 else '',
                        'sample_count': row_values[3] if len(row_values) > 3 else 0,
                        'bad_count': row_values[4] if len(row_values) > 4 else 0,
                        'cum_bad_rate': row_values[7] if len(row_values) > 7 else '0%',
                        'bad_rate': row_values[8] if len(row_values) > 8 else '0%',
                        'lift': row_values[9] if len(row_values) > 9 else 0,
                        'cum_ks': row_values[10] if len(row_values) > 10 else 0,
                    })
                temp_map[model_col] = {
                    'model': model_col,
                    'sort_desc': sort_desc,
                    'rows': rows,
                }
            except Exception:
                continue

        for model_col in usable_cols:
            if model_col in temp_map:
                model_binning_details.append(temp_map[model_col])

    model_decision_tree = build_model_decision_tree(
        df=df_clean,
        label_col=target_col,
        score_cols=usable_cols
    )

    return {
        'data_summary': {
            'total_samples': n_total,
            'bad_samples': int(df_clean[target_col].sum()),
            'overall_bad_rate': f"{df_clean[target_col].mean() * 100:.2f}%",
            'n_models': len(usable_cols),
        },
        'performance': perf_df.to_dict(orient='records'),
        'correlation': corr_df.to_dict(orient='records') if not corr_df.empty else [],
        'complementarity': complement_df.to_dict(orient='records'),
        'strategy_metrics': strategy_metrics,
        'charts': charts,
        'perf_table_html': perf_table_html,
        'strategy_table_html': strategy_table_html,
        'score_cols': usable_cols,
        'cluster_order': cluster_order,
        'cluster_groups': cluster_groups,
        'model_binning_details': model_binning_details,
        'model_decision_tree': model_decision_tree,
    }


def generate_correlation_html_report(analysis_result: dict,
                                    analysis_date: str = '') -> str:
    """生成模型相关性分析HTML报告"""
    data_summary = analysis_result.get('data_summary', {})
    charts = analysis_result.get('charts', {})
    perf_table = analysis_result.get('perf_table_html', '')
    strategy_table = analysis_result.get('strategy_table_html', '')
    strategy_metrics = analysis_result.get('strategy_metrics', {})
    cluster_groups = analysis_result.get('cluster_groups', {})
    performance = analysis_result.get('performance', [])
    model_binning_details = analysis_result.get('model_binning_details', [])
    model_decision_tree = analysis_result.get('model_decision_tree', {})

    # 聚类颜色
    cluster_colors = ['#3B82F6', '#10B981', '#F59E0B', '#EF4444', '#8B5CF6', '#EC4899', '#06B6D4', '#84CC16']

    def build_cluster_html():
        """生成聚类分组结果的HTML"""
        if not cluster_groups:
            return ''
        rows = []
        for idx, (cluster_name, members) in enumerate(cluster_groups.items()):
            color = cluster_colors[idx % len(cluster_colors)]
            tags = ' '.join(
                f'<span class="tag" style="background:{color}20;color:{color};border:1px solid {color}50">{m}</span>'
                for m in members
            )
            rows.append(f'<div class="cluster-row"><b style="color:{color}">{cluster_name}</b>'
                        f'（{len(members)} 个模型）：{tags}</div>')
        return '\n'.join(rows)

    # 从 performance 中提取最优模型的 AUC 和 KS
    best_auc = '-'
    best_ks = '-'
    if performance:
        best_auc = max(p.get('auc', 0) for p in performance)
        best_ks = max(p.get('ks', 0) for p in performance)

    def build_kpi_html():
        """生成 5 个 KPI 卡片"""
        total_samples = data_summary.get('total_samples', 0)
        overall_bad_rate = data_summary.get('overall_bad_rate', '-')
        n_models = data_summary.get('n_models', 0)
        return f'''
<div class="kpi-grid">
  <div class="kpi"><div class="val">{total_samples:,}</div><div class="lbl">总样本量</div></div>
  <div class="kpi"><div class="val">{overall_bad_rate}</div><div class="lbl">整体逾期率</div></div>
  <div class="kpi"><div class="val">{n_models}</div><div class="lbl">可用模型数</div></div>
  <div class="kpi"><div class="val">{best_auc:.4f}</div><div class="lbl">最优模型 AUC</div></div>
  <div class="kpi"><div class="val">{best_ks:.4f}</div><div class="lbl">最优模型 KS</div></div>
</div>'''

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

    def img_side_by_side(name1, cap1, name2, cap2):
        """两个图表并排展示"""
        d1 = charts.get(name1, '')
        d2 = charts.get(name2, '')
        if not d1 and not d2:
            return ''
        left = f'''
    <div class="img-box">
      <img src="data:image/png;base64,{d1}" alt="{name1}">
      <div class="caption">{cap1}</div>
    </div>''' if d1 else ''
        right = f'''
    <div class="img-box">
      <img src="data:image/png;base64,{d2}" alt="{name2}">
      <div class="caption">{cap2}</div>
    </div>''' if d2 else ''
        return f'''
  <div class="img-grid">
    {left}
    {right}
  </div>'''

    def _to_pct(value):
        try:
            if isinstance(value, str):
                return float(value.replace('%', '').strip())
            return float(value)
        except Exception:
            return 0.0

    def build_model_binning_section():
        if not model_binning_details:
            return ''
        sections = []
        for item in model_binning_details:
            rows = item.get('rows', [])
            if not rows:
                continue
            max_bad = max([_to_pct(r.get('bad_rate', '0%')) for r in rows] or [1.0])
            table_rows = []
            for r in rows:
                bad_rate_text = str(r.get('bad_rate', '0%'))
                bad_rate_num = _to_pct(bad_rate_text)
                bar_width = 0 if max_bad <= 0 else min(100, bad_rate_num / max_bad * 100)
                table_rows.append(f"""
                <tr>
                    <td>{r.get('bin', '--')}</td>
                    <td>{r.get('score_min', '--')}</td>
                    <td>{r.get('score_max', '--')}</td>
                    <td>{int(float(r.get('sample_count', 0) or 0)):,}</td>
                    <td>{int(float(r.get('bad_count', 0) or 0)):,}</td>
                    <td><span class="rate-bar-track"><span class="rate-bar-fill" style="width:{bar_width:.1f}%"></span></span>{bad_rate_text}</td>
                    <td>{r.get('cum_bad_rate', '0%')}</td>
                    <td>{r.get('lift', 0)}</td>
                    <td>{r.get('cum_ks', 0)}</td>
                </tr>
                """)
            sections.append(f"""
            <div class="section">
              <h2>📌 {item.get('model', 'model')} Binning Detail</h2>
              <p>Sort rule: <b>{item.get('sort_desc', '')}</b></p>
              <table class="data-table">
                <thead><tr><th>Bin</th><th>Score Min</th><th>Score Max</th><th>Samples</th><th>Bad Samples</th><th>Bad Rate</th><th>Cum Bad Rate</th><th>Lift</th><th>Cum KS</th></tr></thead>
                <tbody>{''.join(table_rows)}</tbody>
              </table>
            </div>
            """)
        return ''.join(sections)

    def build_model_tree_section():
        image = model_decision_tree.get('image', '')
        leaf_nodes = model_decision_tree.get('leaf_nodes', []) or []
        if not image and not leaf_nodes:
            return ''
        rows = ''.join(
            f"<tr><td>{n.get('leaf_id', '--')}</td><td>{n.get('sample_count', 0):,}</td><td>{n.get('bad_count', 0):,}</td><td>{n.get('bad_rate', 0) * 100:.2f}%</td></tr>"
            for n in leaf_nodes
        )
        tree_image_html = f'<img src="data:image/png;base64,{image}" alt="model decision tree">' if image else '<div>No decision tree image.</div>'
        return f"""
        <div class="section">
          <h2>🌲 Model Decision Tree</h2>
          <div class="tree-image-box">{tree_image_html}</div>
          <table class="data-table">
            <thead><tr><th>Leaf</th><th>Samples</th><th>Bad Samples</th><th>Bad Rate</th></tr></thead>
            <tbody>{rows or '<tr><td colspan="4">No leaf-node details.</td></tr>'}</tbody>
          </table>
        </div>
        """

    html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>多模型相关性与策略组合分析报告</title>
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
  .section h3{{font-size:15px;font-weight:600;color:#334155;margin:18px 0 10px}}
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
  .rate-bar-track{{width:120px;height:10px;border-radius:999px;background:#e5e7eb;overflow:hidden;display:inline-block;vertical-align:middle;margin-right:8px}}
  .rate-bar-fill{{height:100%;background:linear-gradient(90deg,#f59e0b,#dc2626);display:block}}
  .tree-image-box{{border:1px solid #e2e8f0;background:#f8fafc;border-radius:10px;padding:10px;margin:10px 0}}
  .tree-image-box img{{width:100%;display:block}}
  .method-box{{background:#eff6ff;border-left:4px solid #2563eb;border-radius:8px;padding:14px 16px;margin:12px 0}}
  .method-box h4{{color:#1e40af;font-size:14px;margin-bottom:6px}}
  .method-box p{{font-size:13px;color:#374151}}
  .recommend{{background:#ecfdf5;border-left:4px solid #10b981;border-radius:8px;padding:14px 16px;margin:12px 0}}
  .recommend h4{{color:#065f46;font-size:14px;margin-bottom:6px}}
  .recommend p,.recommend li{{font-size:13px;color:#374151}}
  .warn{{background:#fffbeb;border-left:4px solid #f59e0b;border-radius:8px;padding:14px 16px;margin:12px 0}}
  .warn p{{font-size:13px;color:#92400e}}
  .tag{{display:inline-block;padding:3px 8px;border-radius:12px;font-size:12px;margin:2px}}
  .cluster-row{{margin:8px 0;font-size:14px}}
  footer{{text-align:center;padding:24px;color:#94a3b8;font-size:12px}}
</style>
</head>
<body>
<div class="header">
  <h1>📊 多模型相关性与策略组合分析报告</h1>
</div>

<div class="container">

{build_kpi_html()}

{build_model_tree_section()}

{build_model_binning_section()}

<!-- 分析框架 -->
<div class="section">
  <h2>🧭 分析框架：多模型组合需要考虑哪些维度？</h2>
  <div class="method-box">
    <h4>📌 相关性分析</h4>
    <p>Spearman 相关系数衡量两模型排序的线性相关程度。相关性高（&gt;0.9）→ 模型学到类似信息，串行使用增益有限；相关性低（&lt;0.7）→ 模型捕捉不同维度，互补性强，捞回策略更有价值。</p>
  </div>
  <div class="method-box">
    <h4>📌 单模型判别力（AUC / KS）</h4>
    <p>弱模型不适合作主力拒绝模型，但可以作为捞回辅助。KS&gt;0.15 是基本可用线，KS&gt;0.25 是优质模型。</p>
  </div>
  <div class="recommend">
    <h4>✅ 多模型策略设计推荐框架</h4>
    <ul>
      <li>① 选1个高KS+高覆盖率模型作<b>主拒绝模型</b></li>
      <li>② 从与主模型<b>低相关、互补性强</b>的模型中选1-2个作<b>捞回模型</b></li>
      <li>③ 串行顺序：风险控制强的先拒 → 再用不同维度模型捞回</li>
    </ul>
  </div>
</div>

<!-- 模型基础性能 -->
<div class="section">
  <h2>① 模型基础性能（覆盖率 / AUC / KS）</h2>
  {img_tag('01_模型基础性能', '气泡大小=覆盖率 | 右上角=AUC高+KS高=优质模型 | 虚线为参考线')}
  <h3>详细性能数据</h3>
  {perf_table}
</div>

<!-- 相关性分析 -->
<div class="section">
  <h2>② 模型间 Spearman 相关性</h2>
  <p>Spearman 相关性衡量两模型的<b>排序一致性</b>。相关性越高说明两个模型信息高度重叠，组合收益小。</p>
  {img_side_by_side('02_相关性热力图', '层次聚类排序的 Spearman 相关性热力图（白到蓝=相关性越高）',
                     '03_聚类树状图', '模型相似度层次聚类树状图（同颜色=同一簇，相距越近=越相似）')}
  <h3>聚类分组结果（同簇模型高度相关，不建议同时作为主+捞回）</h3>
  {build_cluster_html()}
</div>

<!-- 互补性分析 -->
<div class="section">
  <h2>③ 模型互补性分析（量化捞回潜力）</h2>
  {img_tag('04_模型互补性矩阵', '互补性矩阵（值越大=差异越大=捞回潜力越高）', 'single')}
</div>

<!-- 串行策略模拟 -->
<div class="section">
  <h2>④ 串行拒绝 + 捞回策略模拟</h2>
  <p>主模型：<b>{strategy_metrics.get('main_model', '?')}</b>（KS最优）</p>
  {img_tag('05_串行策略效果', 'X轴=通过率，Y轴=通过逾期率 | 箭头方向=加入捞回后的变化 | 越往左下角越好', 'single')}
  <h3>策略模拟明细</h3>
  {strategy_table}
</div>

<!-- ROC + 分布 -->
<div class="section">
  <h2>⑤ Top5 模型 ROC 曲线 &amp; 分数分布</h2>
  {img_side_by_side('06_ROC曲线对比', 'Top5 模型 ROC 曲线对比（曲线越靠左上角越好）',
                     '07_分数分布', '好坏客户分数分布（绿=正常 红=逾期 | 分离度越大=模型越强）')}
</div>

<!-- 策略建议 -->
<div class="section">
  <h2>⑥ 策略设计建议</h2>
  <div class="recommend">
    <h4>🏆 推荐主拒绝模型</h4>
    <p><b>{strategy_metrics.get('main_model', '?')}</b></p>
  </div>
  <h3>多模型串行策略设计要点</h3>
  <ul>
    <li>🔑 <b>先用主模型做底线风控</b>：拒绝低分（建议q=15%~20%），对通过客群再做精细化策略</li>
    <li>🔑 <b>捞回模型选择标准</b>：与主模型 Spearman 相关性 &lt; 0.8，且对被拒群体有一定判别力</li>
    <li>🔑 <b>捞回阈值</b>：建议对被拒群体取高分50%~60%捞回，捞回后整体逾期率涨幅控制在&lt;2pp</li>
    <li>🔑 <b>定期监控 PSI</b>：多模型策略上线后，需同时监控各模型分数的 PSI</li>
  </ul>
</div>
</div>
<footer>多模型相关性与策略组合分析报告 · 生成于 {analysis_date} · RiskPilot</footer>
</body>
</html>'''
    return html
