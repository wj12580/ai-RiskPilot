"""
策略调整记录 API 路由
GET    /api/records          列表（支持分页/筛选）
POST   /api/records          新增
GET    /api/records/<id>     详情
PUT    /api/records/<id>     更新
DELETE /api/records/<id>     删除
GET    /api/records/export   导出 Excel
GET    /api/records/stats    全局统计数字
GET    /api/records/channels 渠道组列表
"""

import io
import json
from datetime import datetime
from flask import Blueprint, request, jsonify, send_file
from models.database import db
from models.strategy_record import StrategyRecord
from services.export_service import export_records_excel

records_bp = Blueprint('records', __name__)


# ── 全局统计数字 ─────────────────────────────────────────────────────────────
@records_bp.route('/stats', methods=['GET'])
def get_stats():
    from models.analysis_task import AnalysisTask
    from models.strategy_review import StrategyReview

    analysis_count = AnalysisTask.query.count()
    record_count   = StrategyRecord.query.count()
    done_count     = StrategyRecord.query.filter(StrategyRecord.review_status == 'done').count()
    pending_count  = StrategyRecord.query.filter(StrategyRecord.review_status == 'pending').count()

    return jsonify({
        'analysis_count':  analysis_count,
        'record_count':    record_count,
        'review_done':     done_count,
        'review_pending':  pending_count,
    })


# ── 渠道组列表 ───────────────────────────────────────────────────────────────
@records_bp.route('/channels', methods=['GET'])
def get_channels():
    """从历史记录中提取所有不重复的渠道组"""
    records = StrategyRecord.query.filter(StrategyRecord.channel != '').all()
    all_channels = set()
    for r in records:
        for ch in (r.channel or '').split(','):
            ch = ch.strip()
            if ch:
                all_channels.add(ch)
    return jsonify({'channels': sorted(all_channels)})


# ── 列表 ─────────────────────────────────────────────────────────────────────
@records_bp.route('', methods=['GET'])
def list_records():
    page          = int(request.args.get('page', 1))
    limit         = int(request.args.get('limit', 20))
    strategy_type = request.args.get('type', '')
    review_status = request.args.get('status', '')
    keyword       = request.args.get('q', '')

    query = StrategyRecord.query
    if strategy_type:
        query = query.filter(StrategyRecord.strategy_type == strategy_type)
    if review_status:
        query = query.filter(StrategyRecord.review_status == review_status)
    if keyword:
        query = query.filter(StrategyRecord.strategy_name.contains(keyword))
    channel = request.args.get('channel', '')
    if channel:
        query = query.filter(StrategyRecord.channel.contains(channel))

    total   = query.count()
    records = query.order_by(StrategyRecord.adjusted_at.desc()) \
                   .offset((page - 1) * limit).limit(limit).all()

    return jsonify({'records': [r.to_dict() for r in records], 'total': total})


# ── 新增 ─────────────────────────────────────────────────────────────────────
@records_bp.route('', methods=['POST'])
def create_record():
    data = request.get_json()

    required = ['strategy_name', 'adjusted_at', 'strategy_type']
    for field in required:
        if not data.get(field):
            return jsonify({'error': f'缺少必填字段：{field}'}), 400

    try:
        adj_date = datetime.strptime(data['adjusted_at'], '%Y-%m-%d').date()
    except ValueError:
        return jsonify({'error': '日期格式错误，请用 YYYY-MM-DD'}), 400

    record = StrategyRecord(
        strategy_name  = data['strategy_name'],
        adjusted_at    = adj_date,
        strategy_type  = data['strategy_type'],
        content        = data.get('content', ''),
        reason_tags    = json.dumps(data.get('reason_tags', []), ensure_ascii=False),
        analysis_tags  = data.get('analysis_tags', ''),
        metrics_before = json.dumps(data.get('metrics_before', {}), ensure_ascii=False),
        metrics_after  = json.dumps(data.get('metrics_after', {}), ensure_ascii=False),
        expected_goal  = data.get('expected_goal', ''),
        analysis_id    = data.get('analysis_id'),
        notes          = data.get('notes', ''),
        review_status  = 'pending',
        channel       = data.get('channel', ''),
    )
    db.session.add(record)
    db.session.commit()
    return jsonify(record.to_dict()), 201


# ── 详情 ─────────────────────────────────────────────────────────────────────
@records_bp.route('/<int:record_id>', methods=['GET'])
def get_record(record_id):
    record = StrategyRecord.query.get_or_404(record_id)
    return jsonify(record.to_dict())


# ── 更新 ─────────────────────────────────────────────────────────────────────
@records_bp.route('/<int:record_id>', methods=['PUT'])
def update_record(record_id):
    record = StrategyRecord.query.get_or_404(record_id)
    data   = request.get_json()

    if 'strategy_name'  in data: record.strategy_name  = data['strategy_name']
    if 'adjusted_at'    in data:
        try:
            record.adjusted_at = datetime.strptime(data['adjusted_at'], '%Y-%m-%d').date()
        except ValueError:
            return jsonify({'error': '日期格式错误'}), 400
    if 'strategy_type'  in data: record.strategy_type  = data['strategy_type']
    if 'content'        in data: record.content        = data['content']
    if 'reason_tags'    in data: record.reason_tags    = json.dumps(data['reason_tags'],    ensure_ascii=False)
    if 'analysis_tags'  in data: record.analysis_tags  = data['analysis_tags']
    if 'metrics_before' in data: record.metrics_before = json.dumps(data['metrics_before'], ensure_ascii=False)
    if 'metrics_after'  in data: record.metrics_after  = json.dumps(data['metrics_after'],  ensure_ascii=False)
    if 'expected_goal'  in data: record.expected_goal  = data['expected_goal']
    if 'notes'          in data: record.notes          = data['notes']
    if 'analysis_id'    in data: record.analysis_id    = data['analysis_id']
    if 'channel'        in data: record.channel        = data['channel']

    db.session.commit()
    return jsonify(record.to_dict())


# ── 删除 ─────────────────────────────────────────────────────────────────────
@records_bp.route('/<int:record_id>', methods=['DELETE'])
def delete_record(record_id):
    record = StrategyRecord.query.get_or_404(record_id)
    db.session.delete(record)
    db.session.commit()
    return jsonify({'message': '删除成功'})


# ── 导出 Excel ───────────────────────────────────────────────────────────────
@records_bp.route('/export', methods=['GET'])
def export_records():
    records = StrategyRecord.query.order_by(StrategyRecord.adjusted_at.desc()).all()
    excel_bytes = export_records_excel([r.to_dict() for r in records])
    return send_file(
        io.BytesIO(excel_bytes),
        as_attachment=True,
        download_name='策略调整记录.xlsx',
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
