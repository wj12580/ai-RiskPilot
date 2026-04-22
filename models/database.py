"""
数据库配置与初始化
"""

from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


def init_db(app=None):
    """初始化数据库"""
    if app:
        db.init_app(app)
        with app.app_context():
            db.create_all()
    else:
        db.create_all()
