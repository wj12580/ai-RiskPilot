"""
策略复盘模型
"""

from models.database import db
from datetime import datetime, date
import json


class StrategyReview(db.Model):
    """策略复盘表"""
    __tablename__ = 'strategy_reviews'

    id = db.Column(db.Integer, primary_key=True)
    record_id = db.Column(db.Integer, db.ForeignKey('strategy_records.id'), nullable=False)
    review_date = db.Column(db.Date, nullable=False)
    review_result = db.Column(db.Text, default='{}')  # JSON 对象
    ai_conclusion = db.Column(db.Text, default='')
    manual_label = db.Column(db.String(20), nullable=True)  # effective/ineffective/observing
    manual_note = db.Column(db.Text, default='')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # 关联策略记录
    record = db.relationship('StrategyRecord', backref='reviews', lazy=True)

    def to_dict(self):
        return {
            'id': self.id,
            'record_id': self.record_id,
            'review_date': self.review_date.isoformat() if self.review_date else None,
            'review_result': json.loads(self.review_result) if self.review_result else {},
            'ai_conclusion': self.ai_conclusion,
            'manual_label': self.manual_label,
            'manual_note': self.manual_note,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'record': self.record.to_dict() if hasattr(self, 'record') and self.record else None,
        }
