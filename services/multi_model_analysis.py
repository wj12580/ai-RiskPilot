"""
多模型综合分析引擎
支持对多个模型同时进行：
  1. 基础性能评估（AUC / KS / 覆盖率 / 逾期率）
  2. Spearman 相关性热力图 + 层次聚类树状图
  3. 模型互补性矩阵（量化捞回潜力）
  4. 串行策略模拟（主拒绝 + 候选捞回）
  5. Top-N ROC 曲线对比
  6. 分数分布对比图（好坏客户分离度）

导出报告为独立 HTML（内嵌 base64 图片，无外部依赖）。
"""

import io
import base64
import warnings
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.gridspec import GridSpec
from scipy.cluster.hierarchy import linkage, dendrogram, fcluster
from scipy.spatial.distance import squareform
from scipy.stats import spearmanr
from sklearn.metrics import roc_curve, auc as sk_auc, roc_auc_score
from sklearn.preprocessing import StandardScaler
from concurrent.futures import ThreadPoolExecutor, as_completed

warnings.filterwarnings('ignore')

# ── 中文字体 ──────────────────────────────────────────────────────────────────
try:
    plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'WenQuanYi Micro Hei']
except Exception:
    pass
plt.rcParams['axes.unicode_minus'] = False

#########
# ══════════════════════════════════════════════════════════════════════════════
#  核心分析类
# ══════════════════════════════════════════════════════════════════════════════

class MultiModelAnalyzer:
    """
    多模型综合分析器

    Args:
        df:              DataFrame，包含 target_col 和多个模型分数列
        target_col:      目标列名（0/1 或 True/False）
        score_cols:      模型分数列名列表
        missing_thresh:  缺失率阈值，超过该比例的模型被过滤（默认 0.7，即缺失>70%则丢弃）
    """

    def __init__(self, df: pd.DataFrame, target_col: str,
                 score_cols: list, missing_thresh: float = 0.7):
        # 数据清洗
        self.df_raw = df.copy()
        self.target_col = target_col
        self.missing_thresh = missing_thresh

        # 只保留缺失率在阈值内的模型
        self.score_cols = self._filter_by_missing(df, score_cols)
        self._prepare_data(df)

        # 分析结果缓存
        self._perf: pd.DataFrame = None
        self._corr: pd.DataFrame = None
        self._complement: pd.DataFrame = None
        self._cluster_order: list = None

    # ── 公开方法 ──────────────────────────────────────────────────────────────

    def run_all(self) -> dict:
        """运行全部分析，返回所有结果和图片 base64"""
        perf = self.compute_performance()
        corr, order = self.compute_correlation()
        complement = self.compute_complementarity()
        metrics = self.compute_strategy_simulation()

        images = self._generate_all_charts(perf, corr, order, complement, metrics)

        return {
            'performance': perf.to_dict(orient='records'),
            'correlation': corr.to_dict(orient='records'),
            'complementarity': complement.to_dict(orient='records'),
            'metrics_summary': metrics,
            'charts': images,
            'score_cols': self.score_cols,
            'n_models': len(self.score_cols),
            'n_samples': len(self.df),
        }

    def compute_performance(self) -> pd.DataFrame:
        """计算各模型的覆盖率 / AUC / KS / 逾期率"""
        records = []
        for col in self.score_cols:
            tmp = self.df[[col, self.target_col]].dropna()
            if len(tmp) < 10:
                continue
            y = tmp[self.target_col].values
            s = tmp[col].values

            # 根据分数范围判断风险方向：
            # 最大模型分 > 1：分数越高风险越低 → 需要对分数取反再计算
            # 最大模型分 <= 1：分数越高风险越高 → 直接计算
            max_score = np.max(s)
            if max_score > 1:
                # 分数越高风险越低，取反
                adjusted_score = -s
            else:
                # 分数越高风险越高，直接用
                adjusted_score = s

            # 计算 AUC
            try:
                auc = roc_auc_score(y, adjusted_score)
            except Exception:
                auc = 0.5

            # 计算 KS（使用调整后的分数）
            try:
                fpr, tpr, _ = roc_curve(y, adjusted_score)
                ks = float(max(tpr - fpr))
            except Exception:
                ks = 0.0

            # 逾期率
            bad_rate = float(y.mean())

            records.append({
                'model': col,
                'coverage': round(len(tmp) / len(self.df), 4),
                'auc': round(auc, 4),
                'ks': round(ks, 4),
                'bad_rate': round(bad_rate, 4),
                'n': len(tmp),
            })

        self._perf = pd.DataFrame(records).sort_values('ks', ascending=False).reset_index(drop=True)
        return self._perf

    def compute_correlation(self) -> tuple:
        """
        计算 Spearman 相关性矩阵，返回 (DataFrame, 聚类排序列表)
        """
        df_score = self.df[self.score_cols].dropna()
        if len(df_score) < 10:
            self._corr = pd.DataFrame(index=self.score_cols, columns=self.score_cols)
            self._cluster_order = self.score_cols
            return self._corr, self._cluster_order

        # Spearman
        corr_mat, _ = spearmanr(df_score.values, axis=0)
        corr_mat = pd.DataFrame(corr_mat, index=self.score_cols, columns=self.score_cols)

        # 层次聚类排序
        dist = 1 - np.triu(corr_mat.values, k=1)   # 上三角 → 相异性
        dist = dist + dist.T
        np.fill_diagonal(dist, 0)
        dist = np.clip(dist, 0, None)

        if dist.sum() == 0:
            self._cluster_order = self.score_cols
        else:
            try:
                Z = linkage(squareform(dist), method='average')
                self._cluster_order = [self.score_cols[i] for i in
                                       self._build_order(Z, len(self.score_cols))]
            except Exception:
                self._cluster_order = self.score_cols

        # 按聚类顺序重排
        self._corr = corr_mat.loc[self._cluster_order, self._cluster_order]
        return self._corr, self._cluster_order

    def compute_complementarity(self) -> pd.DataFrame:
        """
        互补性矩阵：
        对每对模型(A,B)，以各自分位数20%为"拒绝"，
        计算 A拒B不拒 + B拒A不拒 的比例 = 互补性
        【优化】用 numpy 向量化替换 O(N²) 双层循环
        """
        df = self.df.dropna(subset=self.score_cols + [self.target_col])
        n_models = len(self.score_cols)

        # 一次性计算所有模型的 20% 分位拒绝矩阵 (n_samples × n_models, bool)
        score_mat = df[self.score_cols].values  # (n, m)
        q20 = np.percentile(score_mat, 20, axis=0)  # (m,)
        reject_mat = score_mat < q20  # (n, m) bool

        records = []
        for i in range(n_models):
            for j in range(i + 1, n_models):
                a_not_b = reject_mat[:, i] & (~reject_mat[:, j])
                b_not_a = reject_mat[:, j] & (~reject_mat[:, i])
                comp_a = a_not_b.mean()
                comp_b = b_not_a.mean()
                records.append({
                    'model_a': self.score_cols[i],
                    'model_b': self.score_cols[j],
                    'a_reject_b_not': round(comp_a, 4),
                    'b_reject_a_not': round(comp_b, 4),
                    'complementarity': round(comp_a + comp_b, 4),
                })

        self._complement = pd.DataFrame(records).sort_values(
            'complementarity', ascending=False).reset_index(drop=True)
        return self._complement

    def compute_strategy_simulation(self) -> dict:
        """
        串行策略模拟：
        以 KS 最优模型为主拒绝模型，
        在不同拒绝比例（q=10%~30%）下，测试各候选捞回模型的效果
        """
        if self._perf is None:
            self.compute_performance()
        if len(self._perf) < 2:
            return {'main_model': None, 'strategies': []}

        main_model = self._perf.iloc[0]['model']
        df = self.df.dropna(subset=self.score_cols + [self.target_col])
        y = df[self.target_col].values
        s_main = df[main_model].values

        strategies = []
        for q_pct in [10, 15, 20, 25, 30]:
            # 仅主模型
            q = np.percentile(s_main, q_pct)
            passed_main = s_main >= q
            bad_main = y[passed_main].mean() if passed_main.sum() > 0 else 0

            row = {
                'reject_rate': q_pct,
                'strategy': f"仅主模型 q={q_pct}%",
                'pass_rate': round((~passed_main).mean() + passed_main.mean(), 4),
                'pass_bad_rate': round(float(bad_main), 4),
                'rescue_count': 0,
                'rescue_bad_rate': None,
            }
            strategies.append(row)

            # 各候选捞回
            for col in self.score_cols:
                if col == main_model:
                    continue
                s_sub = df[col].values
                # 被主模型拒绝的群体
                rejected_mask = ~passed_main
                s_rej = s_sub[rejected_mask]
                y_rej = y[rejected_mask]

                if len(s_rej) < 10:
                    continue

                # 取被拒中高分（50%分位以上）作为捞回候选
                q_rej = np.percentile(s_rej, 50)
                rescued_in_rej = s_rej >= q_rej

                # 构造全量掩码（rejected_mask 中为 True 且 rescued_in_rej 中为 True）
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

    # ── 图表生成 ───────────────────────────────────────────────────────────────

    def _generate_all_charts(self, perf, corr, order, complement, metrics) -> dict:
        """
        生成所有图片，返回 {name: base64}
        【优化】ThreadPoolExecutor 并发生成 7 张图，matplotlib 线程安全（每个图独立 fig/ax）
        """
        task_map = {
            '01_模型基础性能':   (self._plot_performance_bubble,    (perf,)),
            '02_相关性热力图':   (self._plot_correlation_heatmap,   (corr, order)),
            '03_聚类树状图':     (self._plot_dendrogram,            (corr, order)),
            '04_模型互补性矩阵': (self._plot_complementarity_matrix,(complement,)),
            '05_串行策略效果':   (self._plot_strategy_chart,        (metrics,)),
            '06_ROC曲线对比':    (self._plot_roc_curves,            (perf,)),
            '07_分数分布':       (self._plot_score_distributions,   (perf,)),
        }

        charts = {}
        with ThreadPoolExecutor(max_workers=min(7, len(task_map))) as executor:
            futures = {
                executor.submit(fn, *args): name
                for name, (fn, args) in task_map.items()
            }
            for future in as_completed(futures):
                name = futures[future]
                try:
                    charts[name] = future.result()
                except Exception:
                    charts[name] = ''
        return charts

    def _fig_to_b64(self, fig) -> str:
        buf = io.BytesIO()
        fig.savefig(buf, format='png', dpi=150, bbox_inches='tight',
                    facecolor='white', edgecolor='none')
        buf.seek(0)
        img_b64 = base64.b64encode(buf.read()).decode('utf-8')
        plt.close(fig)
        return img_b64

    def _plot_performance_bubble(self, perf: pd.DataFrame) -> str:
        """气泡图：X=KS, Y=AUC, size=覆盖率，颜色=逾期率"""
        fig, ax = plt.subplots(figsize=(10, 7))
        sc = ax.scatter(
            perf['ks'], perf['auc'],
            s=perf['coverage'] * 800 + 50,
            c=perf['bad_rate'], cmap='RdYlGn_r',
            alpha=0.75, edgecolors='white', linewidths=1.5, zorder=3
        )
        for _, r in perf.iterrows():
            ax.annotate(r['model'], (r['ks'], r['auc']),
                        fontsize=8, ha='center', va='bottom',
                        xytext=(0, 5), textcoords='offset points')
        plt.colorbar(sc, label='逾期率', shrink=0.8)
        ax.axhline(0.5, color='gray', linestyle='--', linewidth=0.8, alpha=0.5)
        ax.axvline(0.15, color='gray', linestyle='--', linewidth=0.8, alpha=0.5)
        ax.set_xlabel('KS', fontsize=12)
        ax.set_ylabel('AUC', fontsize=12)
        ax.set_title('多模型性能气泡图（气泡大小=覆盖率，颜色=逾期率）', fontsize=14, pad=12)
        ax.set_xlim(0, 1)
        ax.set_ylim(0.5, 1)
        ax.grid(True, alpha=0.3)
        fig.tight_layout()
        return self._fig_to_b64(fig)

    def _plot_correlation_heatmap(self, corr: pd.DataFrame, order: list) -> str:
        """Spearman 相关性热力图（按聚类排序）"""
        if corr.empty:
            return ''
        vals = corr.values
        n = len(order)
        fig, ax = plt.subplots(figsize=(max(n * 0.7, 6), max(n * 0.6, 5)))
        im = ax.imshow(vals, cmap='RdBu_r', vmin=-1, vmax=1, aspect='auto')
        ax.set_xticks(range(n))
        ax.set_yticks(range(n))
        ax.set_xticklabels(order, rotation=45, ha='right', fontsize=8)
        ax.set_yticklabels(order, fontsize=8)
        plt.colorbar(im, ax=ax, shrink=0.8, label='Spearman ρ')
        for i in range(n):
            for j in range(n):
                val = vals[i, j]
                color = 'white' if abs(val) > 0.6 else 'black'
                ax.text(j, i, f'{val:.2f}', ha='center', va='center',
                        fontsize=7, color=color)
        ax.set_title('模型间 Spearman 相关性热力图（聚类排序）', fontsize=13, pad=10)
        fig.tight_layout()
        return self._fig_to_b64(fig)

    def _plot_dendrogram(self, corr: pd.DataFrame, order: list) -> str:
        """层次聚类树状图"""
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
        fig, ax = plt.subplots(figsize=(max(n * 0.8, 8), 5))
        dendrogram(
            Z, labels=order, ax=ax,
            leaf_rotation=45, leaf_font_size=9,
            color_threshold=0.5 * max(Z[:, 2]),
        )
        ax.set_title('模型相似度层次聚类树状图（同颜色≈同簇）', fontsize=13, pad=10)
        ax.set_ylabel('距离（1 - Spearman ρ）', fontsize=11)
        fig.tight_layout()
        return self._fig_to_b64(fig)

    def _plot_complementarity_matrix(self, complement: pd.DataFrame) -> str:
        """互补性热力图（上三角矩阵）"""
        if complement.empty:
            return ''
        models = list(set(complement['model_a'].tolist() + complement['model_b'].tolist()))
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
        return self._fig_to_b64(fig)

    def _plot_strategy_chart(self, metrics: dict) -> str:
        """串行策略效果：X=通过率，Y=通过逾期率"""
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
            # 画箭头（从主模型点到当前点）
            q = row['reject_rate']
            base_row = df_main[df_main['reject_rate'] == q]
            if not base_row.empty:
                bx = base_row['pass_rate'].values[0] * 100
                by = base_row['pass_bad_rate'].values[0] * 100
                dx = (row['pass_rate'] - base_row['pass_rate'].values[0]) * 100
                dy = (row['pass_bad_rate'] - base_row['pass_bad_rate'].values[0]) * 100
                ax.annotate('', xy=(row['pass_rate'] * 100, row['pass_bad_rate'] * 100),
                            xytext=(bx, by),
                            arrowprops=dict(arrowstyle='->', color='gray', alpha=0.5))

        ax.set_xlabel('通过率（%）', fontsize=12)
        ax.set_ylabel('通过逾期率（%）', fontsize=12)
        ax.set_title(f'串行策略模拟（主模型：{metrics.get("main_model","?")}）',
                     fontsize=14, pad=12)
        ax.legend(fontsize=10)
        ax.grid(True, alpha=0.3)
        fig.tight_layout()
        return self._fig_to_b64(fig)

    def _plot_roc_curves(self, perf: pd.DataFrame) -> str:
        """Top-N ROC 曲线对比（最多显示 Top 5）"""
        top5 = perf.head(min(5, len(perf)))
        df = self.df.dropna(subset=self.score_cols + [self.target_col])
        y = df[self.target_col].values

        fig, ax = plt.subplots(figsize=(8, 8))
        colors = plt.cm.tab10(np.linspace(0, 1, len(top5)))
        for idx, (_, row) in enumerate(top5.iterrows()):
            col = row['model']
            s = df[col].values
            try:
                # 根据分数范围判断是否需要取反
                max_score = np.max(s)
                if max_score <= 1:
                    s_plot = -s  # 分数越高风险越高时取反
                else:
                    s_plot = s
                fpr, tpr, _ = roc_curve(y, s_plot)
                roc_auc = sk_auc(fpr, tpr)
                ax.plot(fpr, tpr, color=colors[idx], linewidth=2,
                        label=f"{col} (AUC={roc_auc:.3f})")
            except Exception:
                ax.plot([], [], color=colors[idx], linewidth=2,
                        label=f"{col} (数据异常)")

        ax.plot([0, 1], [0, 1], 'k--', linewidth=1, label='随机 (AUC=0.500)')
        ax.set_xlabel('FPR（假阳性率）', fontsize=12)
        ax.set_ylabel('TPR（真阳性率）', fontsize=12)
        ax.set_title('Top5 模型 ROC 曲线对比', fontsize=14, pad=12)
        ax.legend(loc='lower right', fontsize=10)
        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1)
        ax.grid(True, alpha=0.3)
        fig.tight_layout()
        return self._fig_to_b64(fig)

    def _plot_score_distributions(self, perf: pd.DataFrame) -> str:
        """好坏客户分数分布（Top 3 模型）"""
        top3 = perf.head(min(3, len(perf)))
        df = self.df.dropna(subset=self.score_cols + [self.target_col])
        y = df[self.target_col].values

        n = len(top3)
        fig, axes = plt.subplots(1, n, figsize=(5 * n, 4), squeeze=False)
        colors_good, colors_bad = '#4CAF50', '#F44336'
        bins = 40

        for col_idx, (_, row) in enumerate(top3.iterrows()):
            ax = axes[0][col_idx]
            col = row['model']
            s = df[col].values
            
            # 根据分数范围判断是否需要取反（保持好客户在右、坏客户在左的直觉）
            max_score = np.max(s)
            if max_score <= 1:
                s = -s  # 分数越高风险越高时取反
            
            good = s[y == 0]
            bad = s[y == 1]

            ax.hist(good, bins=bins, alpha=0.6, color=colors_good,
                    label=f'正常 (n={len(good)})', density=True)
            ax.hist(bad, bins=bins, alpha=0.6, color=colors_bad,
                    label=f'逾期 (n={len(bad)})', density=True)
            ax.set_title(f'{col}', fontsize=12)
            ax.set_xlabel('分数', fontsize=10)
            ax.set_ylabel('密度', fontsize=10)
            ax.legend(fontsize=9)
            ax.grid(True, alpha=0.3)

        fig.suptitle('好坏客户分数分布对比（Top3 模型）', fontsize=14, y=1.02)
        fig.tight_layout()
        return self._fig_to_b64(fig)

    # ── 私有工具 ───────────────────────────────────────────────────────────────

    def _filter_by_missing(self, df: pd.DataFrame, score_cols: list) -> list:
        n_total = len(df)
        return [c for c in score_cols
                if c in df.columns and df[c].notna().mean() >= (1 - self.missing_thresh)]

    def _prepare_data(self, df: pd.DataFrame):
        self.df = df.copy()
        # 先强转为数值，非法值变成 NaN
        raw = pd.to_numeric(self.df[self.target_col], errors='coerce')
        # 只保留严格的二值（0/1 以及 True/False），其他全扔掉
        valid_mask = raw.isin([0, 1, 0.0, 1.0, True, False])
        self.df.loc[~valid_mask, self.target_col] = np.nan
        self.df[self.target_col] = pd.to_numeric(self.df[self.target_col], errors='coerce')

    def _build_order(self, Z, n):
        """从 linkage 矩阵重建叶子顺序"""
        leaf_order = [n + Z[0, 0].astype(int), n + Z[0, 1].astype(int)]
        for i in range(1, n - 1):
            last = leaf_order[-1]
            merged = Z[i, 0], Z[i, 1]
            idx0, idx1 = int(merged[0]), int(merged[1])
            if idx0 == last:
                leaf_order.insert(-1, idx1 if idx1 >= n else int(idx1))
            elif idx1 == last:
                leaf_order.insert(-1, idx0 if idx0 >= n else int(idx0))
            else:
                leaf_order.extend([idx0 if idx0 >= n else int(idx0),
                                   idx1 if idx1 >= n else int(idx1)])
        return [x for x in leaf_order if x < n]


# ══════════════════════════════════════════════════════════════════════════════
#  HTML 报告生成
# ══════════════════════════════════════════════════════════════════════════════

def generate_html_report(analysis_result: dict,
                         file_name: str = '未知',
                         product: str = '通用',
                         analysis_date: str = '') -> str:
    """
    将多模型分析结果渲染为完整 HTML 报告
    analysis_result：由 MultiModelAnalyzer.run_all() 返回
    """
    perf = pd.DataFrame(analysis_result.get('performance', []))
    complement = pd.DataFrame(analysis_result.get('complementarity', []))
    metrics = analysis_result.get('metrics_summary', {})
    charts = analysis_result.get('charts', {})
    score_cols = analysis_result.get('score_cols', [])
    n_samples = analysis_result.get('n_samples', 0)
    n_models = analysis_result.get('n_models', 0)
    bad_rate = perf['bad_rate'].mean() if len(perf) else 0

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

    # ── 预计算 KPI 展示值 ──────────────────────────────────────────────────
    _top_auc = f"{perf.iloc[0]['auc']:.3f}" if len(perf) else '-'
    _top_ks  = f"{perf.iloc[0]['ks']:.3f}"  if len(perf) else '-'
    _top_model = perf.iloc[0]['model'] if len(perf) else '?'
    _top_cov  = f"{perf.iloc[0]['coverage']:.1%}" if len(perf) else '-'

    # 聚类分组
    cluster_html = ''
    if complement is not None and len(complement) > 0:
        all_models = list(set(analysis_result.get('score_cols', [])))
        if len(all_models) > 1:
            dist = np.ones((len(all_models), len(all_models)))
            idx = {m: i for i, m in enumerate(all_models)}
            for _, r in complement.iterrows():
                i, j = idx[r['model_a']], idx[r['model_b']]
                dist[i, j] = dist[j, i] = 1 - r['complementarity']
            np.fill_diagonal(dist, 0)
            dist = np.clip(dist, 0, None)
            if dist.sum() > 0:
                try:
                    Z = linkage(squareform(dist), method='average')
                    clust_models = [all_models[i] for i in range(len(all_models))]
                    # 简单按阈值分组
                    cluster_ids = fcluster(Z, t=0.5, criterion='distance')
                    groups = {}
                    for m, cid in zip(clust_models, cluster_ids):
                        groups.setdefault(cid, []).append(m)
                    colors_map = ['#3B82F6', '#10B981', '#F59E0B', '#EF4444',
                                  '#8B5CF6', '#EC4899', '#06B6D4', '#84CC16']
                    for cid, models in sorted(groups.items()):
                        color = colors_map[(cid - 1) % len(colors_map)]
                        tags = ' '.join(
                            f'<span class="tag" style="background:{color}20;color:{color};border:1px solid {color}50">{m}</span>'
                            for m in models)
                        cluster_html += f'<div class="cluster-row"><b style="color:{color}">聚类 {cid}</b>（{len(models)} 个模型）：{tags}</div>\n'
                except Exception:
                    pass

    # 策略表格
    strategy_rows = ''
    for s in metrics.get('strategies', []):
        rescue_bad = f"{s['rescue_bad_rate']:.1%}" if s.get('rescue_bad_rate') is not None else '-'
        strategy_rows += f'''
    <tr>
      <td>{s['strategy']}</td>
      <td>{s['pass_rate']:.1%}</td>
      <td>{s['pass_bad_rate']:.1%}</td>
      <td>{s.get('rescue_count', 0):,}</td>
      <td>{rescue_bad}</td>
    </tr>'''

    # 性能表格
    perf_rows = ''
    for _, r in perf.iterrows():
        perf_rows += f'''
    <tr>
      <td>{r['model']}</td>
      <td>{r['coverage']:.1%}</td>
      <td>{r['auc']:.4f}</td>
      <td>{r['ks']:.4f}</td>
      <td>{r['bad_rate']:.1%}</td>
      <td>{int(r['n']):,}</td>
    </tr>'''

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
  .tag{{display:inline-block;padding:3px 8px;border-radius:12px;font-size:12px;margin:2px}}
  .cluster-row{{margin:8px 0;font-size:14px}}
  .method-box{{background:#eff6ff;border-left:4px solid #2563eb;border-radius:8px;padding:14px 16px;margin:12px 0}}
  .method-box h4{{color:#1e40af;font-size:14px;margin-bottom:6px}}
  .method-box p{{font-size:13px;color:#374151}}
  .recommend{{background:#ecfdf5;border-left:4px solid #10b981;border-radius:8px;padding:14px 16px;margin:12px 0}}
  .recommend h4{{color:#065f46;font-size:14px;margin-bottom:6px}}
  .recommend p,.recommend li{{font-size:13px;color:#374151}}
  .warn{{background:#fffbeb;border-left:4px solid #f59e0b;border-radius:8px;padding:14px 16px;margin:12px 0}}
  .warn p{{font-size:13px;color:#92400e}}
  footer{{text-align:center;padding:24px;color:#94a3b8;font-size:12px}}
</style>
</head>
<body>
<div class="header">
  <h1>📊 多模型相关性与策略组合分析报告</h1>
  <p>产品：{product} &nbsp;|&nbsp; 样本量：{n_samples:,} &nbsp;|&nbsp; 整体逾期率：{bad_rate:.2%} &nbsp;|&nbsp; 模型数量：{n_models} 个&nbsp;|&nbsp; 分析日期：{analysis_date}</p>
</div>

<div class="container">

<!-- KPI -->
<div class="kpi-grid">
  <div class="kpi"><div class="val">{n_samples:,}</div><div class="lbl">总样本量</div></div>
  <div class="kpi"><div class="val">{bad_rate:.1%}</div><div class="lbl">整体逾期率</div></div>
  <div class="kpi"><div class="val">{n_models}</div><div class="lbl">可用模型数</div></div>
  <div class="kpi"><div class="val">{_top_auc}</div><div class="lbl">最优模型 AUC</div></div>
  <div class="kpi"><div class="val">{_top_ks}</div><div class="lbl">最优模型 KS</div></div>
</div>

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
  <div class="method-box">
    <h4>📌 互补性（差异化判断能力）</h4>
    <p>A拒B不拒 + B拒A不拒的比例 = 两模型对彼此被拒群体的潜在捞回空间。互补性高意味着组合后提升显著。</p>
  </div>
  <div class="recommend">
    <h4>✅ 多模型策略设计推荐框架</h4>
    <ul>
      <li>① 选1个高KS+高覆盖率模型作<b>主拒绝模型</b></li>
      <li>② 从与主模型<b>低相关、互补性强</b>的模型中选1-2个作<b>捞回模型</b></li>
      <li>③ 串行顺序：风险控制强的先拒 → 再用不同维度模型捞回</li>
      <li>④ 捞回后逾期率涨幅控制在可接受阈值内（通常不超全量逾期率+3pp）</li>
    </ul>
  </div>
</div>

<!-- ① 模型基础性能 -->
<div class="section">
  <h2>① 模型基础性能（覆盖率 / AUC / KS）</h2>
  <p>对 {n_models} 个可用模型计算覆盖率、AUC、KS，气泡大小表示覆盖率。</p>
  {img_tag('01_模型基础性能', '气泡大小=覆盖率 | 右上角=AUC高+KS高=优质模型 | 虚线为参考线')}
  <h3>详细性能数据</h3>
  <table class="data-table">
    <thead><tr><th>模型</th><th>覆盖率</th><th>AUC</th><th>KS</th><th>逾期率</th><th>样本量</th></tr></thead>
    <tbody>{perf_rows}</tbody>
  </table>
  <div class="warn">
    <p>⚠️ 注：部分模型分数越高表示越好（正向分），部分越低越好（逆向分）。AUC已统一取 max(auc, 1-auc)，KS基于实际方向计算。</p>
  </div>
</div>

<!-- ② 相关性 -->
<div class="section">
  <h2>② 模型间 Spearman 相关性（判断能否互补）</h2>
  <p>Spearman 相关性衡量两模型的<b>排序一致性</b>。相关性越高说明两个模型信息高度重叠，组合收益小。</p>
  {img_tag('02_相关性热力图', '层次聚类排序的 Spearman 相关性热力图（蓝=正相关，红=负相关）', 'img-grid')}
  {img_tag('03_聚类树状图', '模型相似度层次聚类树状图（同颜色=同一簇，相距越近=越相似）', 'img-grid')}
  <h3>聚类分组结果</h3>
  <div class="cluster-row">{cluster_html or '<p style="color:#64748b;font-size:13px">（模型数量不足，无法进行聚类分组）</p>'}</div>
</div>

<!-- ③ 互补性 -->
<div class="section">
  <h2>③ 模型互补性分析（量化捞回潜力）</h2>
  <p>以各模型低分20%为"拒绝"，计算两模型各自拒绝对方未拒绝的比例之和，即<b>互补性</b>。值越大，捞回空间越大。</p>
  {img_tag('04_模型互补性矩阵', '互补性矩阵（值越大=差异越大=捞回潜力越高 | 推荐选互补性>0.2的对作串行组合）', 'single')}
</div>

<!-- ④ 串行策略 -->
<div class="section">
  <h2>④ 串行拒绝 + 捞回策略模拟</h2>
  <p>主模型：<b>{metrics.get('main_model','?')}</b>（KS最优）。对不同拒绝阈值（10%~30%低分拒绝），分别用候选捞回模型对被拒群体高分捞回（取被拒中50%高分）。</p>
  {img_tag('05_串行策略效果', 'X轴=通过率，Y轴=通过逾期率 | 箭头方向=加入捞回后的变化 | 越往左下角越好', 'single')}
  <h3>策略模拟明细</h3>
  <table class="data-table">
    <thead><tr><th>策略</th><th>通过率</th><th>通过逾期率</th><th>捞回人数</th><th>捞回逾期率</th></tr></thead>
    <tbody>{strategy_rows}</tbody>
  </table>
</div>

<!-- ⑤ ROC + 分布 -->
<div class="section">
  <h2>⑤ Top5 模型 ROC 曲线 &amp; 分数分布</h2>
  {img_tag('06_ROC曲线对比', 'Top5 模型 ROC 曲线对比（曲线越靠左上角越好）', 'img-grid')}
  {img_tag('07_分数分布', '好坏客户分数分布（绿=正常 红=逾期 | 分离度越大=模型越强）', 'img-grid')}
</div>

<!-- ⑥ 策略建议 -->
<div class="section">
  <h2>⑥ 策略设计建议</h2>
  <div class="recommend">
    <h4>🏆 推荐主拒绝模型</h4>
    <p><b>{_top_model}</b>（KS={_top_ks}，AUC={_top_auc}，覆盖率={_top_cov}）</p>
  </div>
  <h3>多模型串行策略设计要点</h3>
  <ul>
    <li>🔑 <b>先用主模型做底线风控</b>：拒绝低分（建议q=15%~20%），对通过客群再做精细化策略</li>
    <li>🔑 <b>捞回模型选择标准</b>：与主模型 Spearman 相关性 &lt; 0.8，且对被拒群体有一定判别力（KS&gt;0.10）</li>
    <li>🔑 <b>捞回阈值</b>：建议对被拒群体取高分50%~60%捞回，捞回后整体逾期率涨幅控制在&lt;2pp</li>
    <li>🔑 <b>同簇模型不重复使用</b>：聚类相同的模型信息高度重叠，串行意义不大，选不同簇的模型组合</li>
    <li>🔑 <b>覆盖率互补</b>：主模型打不了分的客群（缺失），用其他高覆盖率模型兜底，避免漏审</li>
    <li>🔑 <b>定期监控 PSI</b>：多模型策略上线后，需同时监控各模型分数的 PSI，任一模型漂移都需响应</li>
  </ul>
</div>

</div>
<footer>多模型相关性与策略组合分析报告 · 生成于 {analysis_date} · RiskPilot</footer>
</body>
</html>'''
    return html
