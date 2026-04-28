from models.database import db
from datetime import datetime


class KnowledgeChunk(db.Model):
    """轻量知识分块表"""
    __tablename__ = 'knowledge_chunks'

    id = db.Column(db.Integer, primary_key=True)
    document_id = db.Column(db.Integer, db.ForeignKey('knowledge_documents.id'), nullable=False)
    chunk_index = db.Column(db.Integer, nullable=False, default=0)
    content = db.Column(db.Text, default='')
    keywords = db.Column(db.Text, default='[]')
    token_length = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    document = db.relationship('KnowledgeDocument', backref='chunks', lazy=True)

    def to_dict(self):
        return {
            'id': self.id,
            'document_id': self.document_id,
            'chunk_index': self.chunk_index,
            'content': self.content,
            'keywords': self.keywords,
            'token_length': self.token_length,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }
