"""数据库迁移脚本：为 analysis_tasks 和 strategy_records 添加新字段"""
import sqlite3, os

db_path = os.path.join(os.path.dirname(__file__), 'data', 'riskpilot.db')
print(f"数据库路径: {db_path}")
conn = sqlite3.connect(db_path)
cur = conn.cursor()

# analysis_tasks 表
cur.execute('PRAGMA table_info(analysis_tasks)')
cols = [row[1] for row in cur.fetchall()]
print(f"analysis_tasks 列: {cols}")

if 'analysis_tags' not in cols:
    cur.execute('ALTER TABLE analysis_tasks ADD COLUMN analysis_tags TEXT DEFAULT ""')
    print("  + 添加 analysis_tags")

if 'feature_cols' not in cols:
    cur.execute('ALTER TABLE analysis_tasks ADD COLUMN feature_cols TEXT DEFAULT ""')
    print("  + 添加 feature_cols")

# strategy_records 表
cur.execute('PRAGMA table_info(strategy_records)')
cols2 = [row[1] for row in cur.fetchall()]
print(f"strategy_records 列: {cols2}")

if 'analysis_tags' not in cols2:
    cur.execute('ALTER TABLE strategy_records ADD COLUMN analysis_tags TEXT DEFAULT ""')
    print("  + 添加 analysis_tags 到 strategy_records")

conn.commit()
conn.close()
print("迁移完成")
