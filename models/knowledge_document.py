from models.database import db
from datetime import datetime


class KnowledgeDocument(db.Model):
    """轻量知识文档表"""
    __tablename__ = 'knowledge_documents'

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    knowledge_type = db.Column(db.String(50), nullable=False, default='domain')
    topic = db.Column(db.String(100), default='')
    source_type = db.Column(db.String(50), default='manual')
    source_ref = db.Column(db.String(255), default='')
    summary = db.Column(db.Text, default='')
    content_markdown = db.Column(db.Text, default='')
    keywords = db.Column(db.Text, default='[]')
    status = db.Column(db.String(20), default='published')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'title': self.title,
            'knowledge_type': self.knowledge_type,
            'topic': self.topic,
            'source_type': self.source_type,
            'source_ref': self.source_ref,
            'summary': self.summary,
            'content_markdown': self.content_markdown,
            'keywords': self.keywords,
            'status': self.status,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }
