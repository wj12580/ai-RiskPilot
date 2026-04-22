"""迁移脚本：给 strategy_records 表加 channel 字段"""
import sqlite3, os

db_path = os.path.join(os.path.dirname(__file__), 'data', 'riskpilot.db')
conn = sqlite3.connect(db_path)
cur = conn.cursor()

# 检查 channel 列是否存在
cur.execute("PRAGMA table_info(strategy_records)")
cols = [row[1] for row in cur.fetchall()]
print('现有列:', cols)

if 'channel' not in cols:
    cur.execute("ALTER TABLE strategy_records ADD COLUMN channel VARCHAR(100) DEFAULT ''")
    conn.commit()
    print('已添加 channel 列')
else:
    print('channel 列已存在')

conn.close()
