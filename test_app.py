# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, '.')
sys.stdout.reconfigure(encoding='utf-8')

from app import create_app
app = create_app()
print('Flask app created successfully')

with app.test_client() as client:
    # 测试统计接口
    resp = client.get('/api/records/stats')
    print('Stats API status:', resp.status_code)
    print('Stats data:', resp.get_json())

    # 测试上传接口（mock）
    resp2 = client.get('/api/analysis/llm-config')
    print('LLM-config API status:', resp2.status_code)
