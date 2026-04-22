"""
RiskPilot - 风控策略分析师AI助手
Flask 主应用入口
"""

import os
from flask import Flask, render_template
from models.database import db
from routes import analysis_bp, records_bp, reviews_bp, knowledge_bp

def create_app():
    """应用工厂函数"""
    app = Flask(__name__)
    
    # 配置
    base_dir = os.path.dirname(os.path.abspath(__file__))
    app.config['SECRET_KEY'] = 'riskpilot-secret-key-change-in-production'
    app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{os.path.join(base_dir, "data", "riskpilot.db")}'
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    app.config['UPLOAD_FOLDER'] = os.path.join(base_dir, 'uploads')
    app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB
    
    # 确保目录存在
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    os.makedirs(os.path.join(base_dir, 'data'), exist_ok=True)
    
    # 初始化数据库
    db.init_app(app)
    with app.app_context():
        db.create_all()
    
    # 注册蓝图
    app.register_blueprint(analysis_bp, url_prefix='/api/analysis')
    app.register_blueprint(records_bp, url_prefix='/api/records')
    app.register_blueprint(reviews_bp, url_prefix='/api/reviews')
    app.register_blueprint(knowledge_bp, url_prefix='/api/knowledge')
    
    # 主页路由
    @app.route('/')
    def index():
        return render_template('index.html')
    
    return app


if __name__ == '__main__':
    app = create_app()
    try:
        print("=" * 50)
        print("RiskPilot - Flask Server Starting...")
        print("=" * 50)
        print("访问地址: http://127.0.0.1:5000")
        print("=" * 50)
    except Exception:
        pass
    app.run(debug=True, host='0.0.0.0', port=5000)
