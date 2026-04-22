"""
策略复盘 API 路由
GET  /api/reviews              列表（支持 type/status 筛选）
POST /api/reviews              创建复盘（上传数据+AI分析）
POST /api/reviews/manual      手动创建复盘
GET  /api/reviews/<id>         详情
PUT  /api/reviews/<id>/label   更新人工标注
POST /api/reviews/file-columns 解析上传文件列名（用于复盘弹窗）
"""

import os
import json
import uuid
import pandas as pd
from datetime import date
from flask import Blueprint, request, jsonify, current_app
from models.database import db
from models.strategy_record import StrategyRecord
from models.strategy_review import StrategyReview
from services.review_agent import run_review, infer_target_col, infer_score_col

reviews_bp = Blueprint('reviews', __name__)


# ── 列表（支持筛选）────────────────────────────────────────────────────────────
@reviews_bp.route('', methods=['GET'])
def list_reviews():
    keyword = request.args.get('q', '')
    query   = StrategyReview.query

    if keyword:
        query = query.join(StrategyRecord).filter(
            StrategyRecord.strategy_name.contains(keyword)
        )

    reviews = query.order_by(StrategyReview.created_at.desc()).all()
    return jsonify({'reviews': [r.to_dict() for r in reviews]})


# ── 解析上传文件的列名 ────────────────────────────────────────────────────────
@reviews_bp.route('/file-columns', methods=['POST'])
def get_file_columns():
    """用于复盘弹窗：用户上传文件后返回列名，供前端填充时间列选项"""
    if 'file' not in request.files:
        return jsonify({'error': '请上传文件'}), 400

    f   = request.files['file']
    ext = f.filename.rsplit('.', 1)[-1].lower() if '.' in f.filename else 'csv'
    tmp_name = f"{uuid.uuid4().hex}.{ext}"
    tmp_path = os.path.join(current_app.config['UPLOAD_FOLDER'], tmp_name)
    f.save(tmp_path)

    try:
        if ext == 'csv':
            df = pd.read_csv(tmp_path, nrows=5)
        else:
            df = pd.read_excel(tmp_path, nrows=5)
    except Exception as e:
        return jsonify({'error': f'读取文件失败：{str(e)}'}), 400
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

    columns = list(df.columns)
    # 智能推荐
    recommended_time  = _recommend_col(columns, ['date', 'time', '时间', '日期', 'created', 'updated', 'apply'])
    recommended_target = infer_target_col(df) or _recommend_col(columns, ['overdue', '逾期', 'bad', 'default', 'label'])
    recommended_score = infer_score_col(df) or _recommend_col(columns, ['score', '分数', 'prob', 'model', 'risk'])

    return jsonify({
        'columns':            columns,
        'recommended_time':   recommended_time,
        'recommended_target': recommended_target,
        'recommended_score': recommended_score,
    })


def _recommend_col(columns, patterns):
    for col in columns:
        col_lower = col.lower()
        if any(p in col_lower for p in patterns):
            return col
    return None


# ── 创建复盘（上传数据 + AI 分析）──────────────────────────────────────────────
@reviews_bp.route('', methods=['POST'])
def create_review():
    record_id   = request.form.get('record_id')
    target_col  = request.form.get('target_col', '')
    time_col    = request.form.get('time_col', '')   # 时间字段列（从文件列中选择）
    adjustment_date = request.form.get('adjustment_date', '')  # 用户手动选择的调整日期
    score_col   = request.form.get('score_col', '')

    if not record_id:
        return jsonify({'error': '缺少 record_id'}), 400

    record = StrategyRecord.query.get_or_404(int(record_id))

    if 'file' not in request.files:
        return jsonify({'error': '请上传复盘数据文件'}), 400

    f   = request.files['file']
    ext = f.filename.rsplit('.', 1)[-1].lower() if '.' in f.filename else 'csv'
    tmp_name = f"{uuid.uuid4().hex}.{ext}"
    tmp_path = os.path.join(current_app.config['UPLOAD_FOLDER'], tmp_name)
    f.save(tmp_path)

    try:
        if ext == 'csv':
            df = pd.read_csv(tmp_path)
        else:
            df = pd.read_excel(tmp_path)
    except Exception as e:
        return jsonify({'error': f'复盘文件读取失败：{str(e)}'}), 400

    # 构建 record dict 传给 review agent
    record_dict = {
        'strategy_name':  record.strategy_name,
        'strategy_type':  record.strategy_type,
        'adjusted_at':    record.adjusted_at.isoformat() if record.adjusted_at else '',
        'expected_goal':  record.expected_goal or '',
        'target_col':     target_col or None,
        'time_col':       time_col or None,
        'score_col':      score_col or None,
    }

    try:
        # 传入手动选择的调整日期（优先使用）
        review_output = run_review(df, record_dict, manual_adjustment_date=adjustment_date if adjustment_date else None)
    except Exception as e:
        return jsonify({'error': f'复盘分析失败：{str(e)}'}), 500
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

    # 更新 record
    metrics_after = review_output['metrics_after']
    record.metrics_after  = json.dumps(metrics_after, ensure_ascii=False)
    record.review_status = 'done'

    review = StrategyReview(
        record_id     = record.id,
        review_date   = date.today(),
        review_result = json.dumps(review_output['comparison'], ensure_ascii=False),
        ai_conclusion = review_output['ai_conclusion'],
        manual_label  = None,
    )
    db.session.add(review)
    db.session.commit()

    return jsonify({
        'review_id':     review.id,
        'review_result': review_output['comparison'],
        'ai_conclusion': review_output['ai_conclusion'],
        'html_report':   review_output.get('html_report', ''),
        'summary':       review_output.get('summary', ''),
    }), 201


# ── 手动创建复盘 ──────────────────────────────────────────────────────────────
@reviews_bp.route('/manual', methods=['POST'])
def create_manual_review():
    """手动输入复盘数据（不依赖文件上传）"""
    data = request.get_json()
    
    record_id = data.get('record_id')
    if not record_id:
        return jsonify({'error': '缺少 record_id'}), 400
    
    record = StrategyRecord.query.get_or_404(int(record_id))
    
    comparison = data.get('comparison', [])
    if not comparison:
        return jsonify({'error': '请至少填写一个指标对比'}), 400
    
    # 计算调整后的指标汇总
    metrics_after = {}
    for item in comparison:
        label = item.get('label', '')
        after_val = item.get('after', 0)
        if label and after_val:
            metrics_after[label] = after_val
    
    # 更新 record 状态
    record.metrics_after  = json.dumps(metrics_after, ensure_ascii=False)
    record.review_status = 'done'
    
    # 创建复盘记录
    review = StrategyReview(
        record_id     = record.id,
        review_date   = date.today(),
        review_result = json.dumps(comparison, ensure_ascii=False),
        ai_conclusion = data.get('ai_conclusion', ''),
        manual_label  = data.get('manual_label') or None,
        manual_note   = data.get('manual_note', '') or '',
    )
    db.session.add(review)
    db.session.commit()
    
    return jsonify({
        'review_id':     review.id,
        'review_result': comparison,
        'ai_conclusion': review.ai_conclusion,
        'manual_label':  review.manual_label,
    }), 201


# ── 详情 ─────────────────────────────────────────────────────────────────────
@reviews_bp.route('/<int:review_id>', methods=['GET'])
def get_review(review_id):
    review = StrategyReview.query.get_or_404(review_id)
    return jsonify(review.to_dict())


# ── 更新人工标注 ──────────────────────────────────────────────────────────────
@reviews_bp.route('/<int:review_id>/label', methods=['PUT'])
def update_label(review_id):
    review = StrategyReview.query.get_or_404(review_id)
    data   = request.get_json()
    label  = data.get('manual_label', '')

    if label and label not in ('effective', 'ineffective', 'observing'):
        return jsonify({'error': '标签值无效'}), 400

    review.manual_label = label if label else None
    review.manual_note  = data.get('manual_note', '') or ''
    db.session.commit()
    return jsonify(review.to_dict())
