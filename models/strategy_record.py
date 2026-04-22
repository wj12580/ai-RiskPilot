"""
策略调整记录模型
"""

from models.database import db
from datetime import datetime
import json


class StrategyRecord(db.Model):
    """策略调整记录表"""
    __tablename__ = 'strategy_records'

    id = db.Column(db.Integer, primary_key=True)
    strategy_name = db.Column(db.String(200), nullable=False)
    adjusted_at = db.Column(db.Date, nullable=False)
    strategy_type = db.Column(db.String(50), nullable=False)  # approval/pricing/retargeting/collection
    content = db.Column(db.Text, default='')
    reason_tags = db.Column(db.Text, default='[]')  # JSON 数组
    analysis_tags = db.Column(db.Text, default='')  # 逗号分隔的分析类型标签
    metrics_before = db.Column(db.Text, default='{}')  # JSON 对象
    metrics_after = db.Column(db.Text, default='{}')  # JSON 对象
    expected_goal = db.Column(db.String(500), default='')
    analysis_id = db.Column(db.Integer, db.ForeignKey('analysis_tasks.id'), nullable=True)
    notes = db.Column(db.Text, default='')
    review_status = db.Column(db.String(20), default='pending')  # pending/done
    channel = db.Column(db.String(100), default='')  # 渠道组（逗号分隔多选）
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'strategy_name': self.strategy_name,
            'adjusted_at': self.adjusted_at.isoformat() if self.adjusted_at else None,
            'strategy_type': self.strategy_type,
            'content': self.content,
            'reason_tags': json.loads(self.reason_tags) if self.reason_tags else [],
            'analysis_tags': self.analysis_tags or '',
            'metrics_before': json.loads(self.metrics_before) if self.metrics_before else {},
            'metrics_after': json.loads(self.metrics_after) if self.metrics_after else {},
            'expected_goal': self.expected_goal,
            'analysis_id': self.analysis_id,
            'notes': self.notes,
            'review_status': self.review_status,
            'channel': self.channel or '',
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }
