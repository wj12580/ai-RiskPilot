"""
策略复盘 Agent
专门负责对比策略调整前后的数据表现，生成结构化复盘报告。

核心逻辑：
1. 根据调整时间列，自动识别调整前/后两段数据
2. 计算各维度的指标对比（逾期率、KS、AUC、Lift、覆盖率等）
3. 调用大模型生成深度复盘结论
4. 返回可视化所需的原始数据和 HTML 报告
"""

import json
import textwrap
from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score, roc_curve

from services.llm_service import call_llm


# ─────────────────────────────────────────────────────────────────────────────
# 指标计算工具
# ─────────────────────────────────────────────────────────────────────────────

def compute_metrics(df: pd.DataFrame, score_col: str, target_col: str) -> dict:
    """计算单段的完整指标"""
    target = df[target_col].values
    scores = df[score_col].values

    # 过滤非法标签
    valid = np.isfinite(target) & pd.Series(target).isin([0, 1, 0.0, 1.0, '0', '1', '0.0', '1.0']).values
    target = target[valid].astype(int)
    scores = scores[valid.values] if hasattr(scores[valid.values], '__iter__') else scores[valid]

    if len(target) < 10 or len(np.unique(target)) < 2:
        return _empty_metrics()

    # KS
    try:
        fpr, tpr, thresholds = roc_curve(target, scores)
        ks = float(np.max(tpr - fpr))
    except Exception:
        ks = 0.0

    # AUC
    try:
        auc = float(roc_auc_score(target, scores))
    except Exception:
        auc = 0.0

    # 逾期率
    bad_rate = float(target.mean())

    # 覆盖率
    coverage = len(target) / len(df) if len(df) > 0 else 0.0

    # 分箱 Lift（Top20% vs 整体）
    try:
        n_top = max(1, int(len(scores) * 0.2))
        sorted_idx = np.argsort(scores)[::-1]
        top_bad_rate = target[sorted_idx[:n_top]].mean()
        lift = (top_bad_rate / bad_rate) if bad_rate > 0 else 1.0
    except Exception:
        lift = 1.0

    return {
        'n_samples':  int(len(target)),
        'n_bad':      int(target.sum()),
        'bad_rate':   round(bad_rate, 6),
        'ks':         round(ks, 6),
        'auc':        round(auc, 6),
        'lift':       round(lift, 4),
        'coverage':   round(coverage, 4),
    }


def _empty_metrics() -> dict:
    return {
        'n_samples': 0, 'n_bad': 0,
        'bad_rate': 0.0, 'ks': 0.0,
        'auc': 0.0, 'lift': 1.0, 'coverage': 0.0,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 指标对比
# ─────────────────────────────────────────────────────────────────────────────

def compare_metrics(before: dict, after: dict) -> list:
    """构建指标对比列表"""
    fields = [
        ('bad_rate',  'M1逾期率',       True,  '下降为改善'),
        ('ks',        'KS 值',          False, '上升为改善'),
        ('auc',       'AUC',            False, '上升为改善'),
        ('lift',      'Top20% Lift',    False, '上升为改善'),
        ('coverage',  '覆盖率',          False, '上升为改善'),
    ]
    result = []
    for key, label, lower_better, note in fields:
        b = before.get(key)
        a = after.get(key)
        if b is None or a is None or (b == 0 and a == 0):
            continue
        delta = round(float(a) - float(b), 6)
        improved = (delta < 0) if lower_better else (delta > 0)
        result.append({
            'key':      key,
            'label':    label,
            'note':     note,
            'before':   round(float(b), 6),
            'after':    round(float(a), 6),
            'delta':    round(delta, 6),
            'improved': improved,
        })
    return result


# ─────────────────────────────────────────────────────────────────────────────
# 时间分切
# ─────────────────────────────────────────────────────────────────────────────

def split_by_time(df: pd.DataFrame, time_col: str, adjustment_date: str) -> tuple:
    """
    根据调整时间和文件中的时间列，将数据切分为调整前/后两段。

    Args:
        df: 完整数据 DataFrame
        time_col: 文件中的时间列名（从列名推断或用户指定）
        adjustment_date: 调整日期（YYYY-MM-DD），可以来自用户手动输入或 strategy_record.adjusted_at

    Returns:
        (df_before, df_after) 两个 DataFrame
    """
    # 兼容多种时间格式
    for fmt in ['%Y-%m-%d', '%Y/%m/%d', '%Y%m%d', '%d/%m/%Y']:
        try:
            adj_dt = datetime.strptime(adjustment_date, fmt)
            break
        except ValueError:
            continue
    else:
        adj_dt = datetime.strptime(adjustment_date, '%Y-%m-%d')

    # 强制转换时间列为 datetime
    try:
        df = df.copy()
        df['_parse_time'] = pd.to_datetime(df[time_col], errors='coerce')
        df_before = df[df['_parse_time'] < adj_dt].drop(columns=['_parse_time'])
        df_after  = df[df['_parse_time'] >= adj_dt].drop(columns=['_parse_time'])
    except Exception:
        # fallback：前50%为调整前，后50%为调整后
        n = len(df)
        df_sorted = df.sort_values(time_col).reset_index(drop=True)
        df_before = df_sorted.iloc[:n // 2]
        df_after  = df_sorted.iloc[n // 2:]

    return df_before, df_after


# ─────────────────────────────────────────────────────────────────────────────
# 智能推断分数列
# ─────────────────────────────────────────────────────────────────────────────

def infer_score_col(df: pd.DataFrame) -> Optional[str]:
    """从 DataFrame 列名推断最像分数的列"""
    score_patterns = ['score', '分数', 'prob', 'probability', 'pred', 'predict', 'model', 'risk', 'rating']
    for col in df.columns:
        col_lower = col.lower()
        if any(p in col_lower for p in score_patterns):
            return col
    # 兜底：选第一个数值列
    for col in df.columns:
        if df[col].dtype in ['float64', 'int64']:
            return col
    return df.columns[0] if len(df.columns) > 0 else None


# ─────────────────────────────────────────────────────────────────────────────
# 智能推断目标列
# ─────────────────────────────────────────────────────────────────────────────

def infer_target_col(df: pd.DataFrame) -> Optional[str]:
    """从 DataFrame 列名推断最像标签的列"""
    target_patterns = ['overdue', '逾期', 'bad', 'default', 'label', 'target', 'y', 'flag']
    for col in df.columns:
        col_lower = col.lower()
        if any(p in col_lower for p in target_patterns):
            return col
    return None


# ─────────────────────────────────────────────────────────────────────────────
# AI 复盘结论生成
# ─────────────────────────────────────────────────────────────────────────────

def generate_ai_conclusion(record_name: str, strategy_type: str,
                             expected_goal: str, before: dict, after: dict,
                             comparison: list) -> str:
    """调用大模型生成深度复盘结论"""

    summary_prompt = textwrap.dedent(f"""
        你是一名资深风控策略分析师，正在对一次策略调整进行复盘。

        ## 策略信息
        - 策略名称：{record_name}
        - 策略类型：{strategy_type}
        - 预期目标：{expected_goal or '未设定明确目标'}

        ## 调整前指标
        {json.dumps(before, ensure_ascii=False, indent=2)}

        ## 调整后指标
        {json.dumps(after, ensure_ascii=False, indent=2)}

        ## 指标对比
        {json.dumps(comparison, ensure_ascii=False, indent=2)}

        请生成一份结构化的复盘报告，要求：
        1. 给出策略效果总体评价（有效/无效/需观察）
        2. 逐项解读每个指标的变化原因和业务含义
        3. 分析逾期率变化是否达到预期目标
        4. 提出后续行动建议（固化策略/继续调整/扩大试验等）
        5. 语气专业、结论明确，避免模糊表述

        请用中文回复，直接给出报告内容，不要用 JSON。
    """)

    try:
        result = call_llm(summary_prompt, temperature=0.3)
        if result and len(result) > 50:
            return result
    except Exception as e:
        pass

    # Fallback：规则生成
    bad_delta = after.get('bad_rate', 0) - before.get('bad_rate', 0)
    ks_delta  = after.get('ks', 0) - before.get('ks', 0)
    lines = [f"策略调整「{record_name}」复盘结论："]

    if bad_delta < -0.003:
        lines.append("【整体评价：有效】逾期率下降明显，策略调整正向效果显著。")
    elif bad_delta > 0.003:
        lines.append("【整体评价：需调整】逾期率出现上升，建议深入排查原因。")
    else:
        lines.append("【整体评价：观察中】逾期率变化不显著，建议持续跟踪。")

    if ks_delta > 0.01:
        lines.append(f"KS 提升 {ks_delta:.4f}，模型区分度改善。")
    elif ks_delta < -0.01:
        lines.append(f"KS 下降 {abs(ks_delta):.4f}，需关注模型稳定性。")

    if expected_goal:
        lines.append(f"预期目标：{expected_goal}")
    lines.append("建议：持续跟踪近期表现，若趋势稳定可固化策略。")
    return '\n'.join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# 主复盘流程
# ─────────────────────────────────────────────────────────────────────────────

def run_review(df: pd.DataFrame, record: dict, manual_adjustment_date: str = None) -> dict:
    """
    执行完整复盘流程。

    Args:
        df: 完整复盘数据 DataFrame（含调整前后所有数据）
        record: StrategyRecord 字典（含 adjusted_at / strategy_name 等）
        manual_adjustment_date: 用户手动输入的调整日期（YYYY-MM-DD），优先使用

    Returns:
        {
            'metrics_before': {...},
            'metrics_after':  {...},
            'comparison':     [...],
            'ai_conclusion':  str,
            'html_report':    str,
            'summary':        str,
        }
    """
    # 优先使用用户手动输入的日期，否则使用 record 中的日期
    adj_date = manual_adjustment_date or record.get('adjusted_at', '')
    # 解析日期字符串
    if isinstance(adj_date, str) and 'T' in adj_date:
        adj_date = adj_date.split('T')[0]

    # 推断列
    time_col   = record.get('time_col') or infer_target_col(df) or df.columns[0]
    score_col  = record.get('score_col') or infer_score_col(df)
    target_col = record.get('target_col') or infer_target_col(df)

    if not all([time_col, score_col, target_col]):
        raise ValueError(f"无法自动推断必要列：time_col={time_col}, score_col={score_col}, target_col={target_col}")

    # 分切数据
    df_before, df_after = split_by_time(df, time_col, adj_date)

    # 计算指标
    metrics_before = compute_metrics(df_before, score_col, target_col)
    metrics_after  = compute_metrics(df_after,  score_col, target_col)

    # 对比
    comparison = compare_metrics(metrics_before, metrics_after)

    # AI 结论
    ai_conclusion = generate_ai_conclusion(
        record.get('strategy_name', ''),
        record.get('strategy_type', ''),
        record.get('expected_goal', ''),
        metrics_before,
        metrics_after,
        comparison,
    )

    # 生成 HTML 报告
    html_report = _build_html_report(
        record.get('strategy_name', ''),
        metrics_before,
        metrics_after,
        comparison,
        ai_conclusion,
    )

    summary = f"调整前逾期率 {metrics_before['bad_rate']:.2%} → 调整后 {metrics_after['bad_rate']:.2%}"

    return {
        'metrics_before': metrics_before,
        'metrics_after':  metrics_after,
        'comparison':     comparison,
        'ai_conclusion':  ai_conclusion,
        'html_report':    html_report,
        'summary':        summary,
        'time_col':       time_col,
        'score_col':      score_col,
        'target_col':     target_col,
    }


# ─────────────────────────────────────────────────────────────────────────────
# HTML 报告生成
# ─────────────────────────────────────────────────────────────────────────────

def _fmt(v, kind='rate'):
    if kind == 'rate':
        return f"{v:.2%}" if isinstance(v, float) else str(v)
    if kind == 'num':
        return f"{v:.4f}" if isinstance(v, float) else str(v)
    return str(v)


def _improved_badge(improved):
    color = '#10b981' if improved else '#ef4444'
    icon  = '↗' if improved else '↘'
    return f'<span style="color:{color};font-weight:bold;">{icon} {_delta_display(improved)}</span>'


def _delta_display(improved):
    return '改善' if improved else '恶化'


def _build_html_report(strategy_name: str,
                        before: dict, after: dict,
                        comparison: list,
                        ai_conclusion: str) -> str:
    """生成自包含 HTML 复盘报告（无外部依赖）"""

    rows = ''
    for item in comparison:
        delta_str = _fmt(item['delta'], 'num')
        improved  = item['improved']
        color     = '#10b981' if improved else '#ef4444'
        icon      = '↗' if improved else '↘'
        rows += f'''
        <tr>
            <td>{item['label']}</td>
            <td>{_fmt(item['before'], 'rate' if 'rate' in item['key'] else 'num')}</td>
            <td>{_fmt(item['after'],  'rate' if 'rate' in item['key'] else 'num')}</td>
            <td style="color:{color};font-weight:bold;">{icon} {delta_str}</td>
            <td>{item['note']}</td>
            <td><span style="color:{color};font-weight:600;">{'✓ 改善' if improved else '✗ 恶化'}</span></td>
        </tr>'''

    # 摘要指标卡
    def metric_card(label, b_val, a_val, key):
        b_str = _fmt(b_val, 'rate' if 'rate' in key else 'num')
        a_str = _fmt(a_val, 'rate' if 'rate' in key else 'num')
        return f'''
        <div style="flex:1;min-width:120px;background:#f9fafb;border-radius:8px;padding:16px;text-align:center;border:1px solid #e5e7eb;">
            <div style="font-size:0.75rem;color:#6b7280;margin-bottom:8px;">{label}</div>
            <div style="font-size:1.4rem;font-weight:700;color:#1f2937;">{b_str}</div>
            <div style="font-size:1.2rem;color:#2563eb;margin-top:4px;">{a_str}</div>
            <div style="font-size:0.7rem;color:#9ca3af;margin-top:4px;">↑ 调整后</div>
        </div>'''

    cards = metric_card('逾期率', before.get('bad_rate'), after.get('bad_rate'), 'bad_rate')
    cards += metric_card('KS', before.get('ks'), after.get('ks'), 'ks')
    cards += metric_card('AUC', before.get('auc'), after.get('auc'), 'auc')
    cards += metric_card('Lift', before.get('lift'), after.get('lift'), 'lift')
    cards += metric_card('样本量', before.get('n_samples'), after.get('n_samples'), 'n_samples')
    cards += metric_card('覆盖率', before.get('coverage'), after.get('coverage'), 'coverage')

    html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<style>
  *{{margin:0;padding:0;box-sizing:border-box;}}
  body{{font-family:"PingFang SC","Microsoft YaHei",sans-serif;background:#f3f4f6;padding:24px;color:#1f2937;}}
  .report{{max-width:900px;margin:0 auto;}}
  .header{{background:linear-gradient(135deg,#2563eb,#1d4ed8);color:#fff;border-radius:12px;padding:24px;margin-bottom:20px;}}
  .header h1{{font-size:1.4rem;margin-bottom:8px;}}
  .header p{{font-size:0.85rem;opacity:0.85;}}
  .summary-cards{{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:20px;}}
  .section{{background:#fff;border-radius:12px;padding:20px;margin-bottom:16px;box-shadow:0 1px 3px rgba(0,0,0,0.1);}}
  .section-title{{font-size:1rem;font-weight:600;margin-bottom:16px;color:#374151;border-bottom:2px solid #e5e7eb;padding-bottom:8px;}}
  table{{width:100%;border-collapse:collapse;font-size:0.88rem;}}
  th{{background:#f9fafb;text-align:left;padding:10px 12px;color:#6b7280;font-weight:500;border-bottom:1px solid #e5e7eb;}}
  td{{padding:10px 12px;border-bottom:1px solid #f3f4f6;}}
  tr:last-child td{{border-bottom:none;}}
  .ai-box{{background:#eff6ff;border-left:4px solid #2563eb;border-radius:4px;padding:16px;line-height:1.9;font-size:0.88rem;white-space:pre-wrap;}}
  .tag有效{{background:#d1fae5;color:#065f46;padding:2px 8px;border-radius:4px;font-size:0.75rem;}}
  .tag无效{{background:#fee2e2;color:#991b1b;padding:2px 8px;border-radius:4px;font-size:0.75rem;}}
  .tag观察{{background:#fef3c7;color:#92400e;padding:2px 8px;border-radius:4px;font-size:0.75rem;}}
</style>
</head>
<body>
<div class="report">
  <div class="header">
    <h1>🛡️ 策略复盘报告</h1>
    <p>策略名称：{strategy_name}</p>
    <p>生成时间：{datetime.now().strftime("%Y-%m-%d %H:%M")}</p>
  </div>

  <div class="summary-cards">{cards}</div>

  <div class="section">
    <div class="section-title">📊 指标对比详情</div>
    <table>
      <thead>
        <tr>
          <th>指标</th><th>调整前</th><th>调整后</th><th>变化量</th><th>说明</th><th>结论</th>
        </tr>
      </thead>
      <tbody>{rows}</tbody>
    </table>
  </div>

  <div class="section">
    <div class="section-title">🤖 AI 复盘结论</div>
    <div class="ai-box">{ai_conclusion}</div>
  </div>
</div>
</body>
</html>'''
    return html
