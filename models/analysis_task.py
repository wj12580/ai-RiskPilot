from models.database import db
from datetime import datetime
import json


class AnalysisTask(db.Model):
    __tablename__ = 'analysis_tasks'

    id            = db.Column(db.Integer, primary_key=True)
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)
    file_name     = db.Column(db.String(255))
    file_type     = db.Column(db.String(20))   # csv / excel
    analysis_type = db.Column(db.String(200))  # model_eval / iv / overdue / layered / psi（可多个逗号分隔）
    analysis_tags = db.Column(db.Text, default='')  # 逗号分隔的分析类型标签
    feature_cols  = db.Column(db.Text, default='')  # 逗号分隔的特征列
    target_col    = db.Column(db.String(100))
    score_col     = db.Column(db.String(100))
    n_bins        = db.Column(db.Integer, default=10)
    user_note     = db.Column(db.Text)
    result_json   = db.Column(db.Text)         # 完整分析结果
    suggestion    = db.Column(db.Text)         # AI 建议 JSON 数组

    def to_dict(self):
        return {
            'id':            self.id,
            'created_at':    self.created_at.strftime('%Y-%m-%d %H:%M'),
            'file_name':     self.file_name,
            'analysis_type': self.analysis_type,
            'analysis_tags': self.analysis_tags or '',
            'feature_cols':  self.feature_cols or '',
            'target_col':    self.target_col,
            'score_col':     self.score_col,
            'result':        json.loads(self.result_json or '{}'),
            'suggestion':    json.loads(self.suggestion or '[]'),
        }
