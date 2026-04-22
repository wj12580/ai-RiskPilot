"""
修复验证脚本：测试含非法标签值的多模型分析
"""
import pandas as pd
import numpy as np
import sys
sys.path.insert(0, '.')

from services.multi_model_analysis import MultiModelAnalyzer

np.random.seed(42)
n = 300
df = pd.DataFrame({
    'label': [0, 1] * 150,
    'model_a': np.random.randn(n),
    'model_b': np.random.randn(n),
    'model_c': np.random.randn(n),
})
df.loc[10, 'label'] = 2       # 非法值
df.loc[20, 'label'] = None   # None
df.loc[30, 'model_a'] = None  # 缺失

analyzer = MultiModelAnalyzer(df, 'label', ['model_a', 'model_b', 'model_c'])
result = analyzer.run_all()

print('VERIFY_OK')
print('n_models:', result['n_models'])
print('n_samples:', result['n_samples'])
print('chart_count:', len(result['charts']))
print('top_auc:', result['performance'][0]['auc'])
print('top_ks:', result['performance'][0]['ks'])
