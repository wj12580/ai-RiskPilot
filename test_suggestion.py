# -*- coding: utf-8 -*-
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

"""测试策略建议生成"""

from services.suggestion_service import generate_llm_dynamic_suggestion

# 模拟相关性分析数据
test_data = {
    'performance': [
        {'model': 'model_A', 'auc': 0.52, 'ks': 0.08, 'coverage': 0.85, 'bad_rate': 0.12, 'n': 5000},
        {'model': 'model_B', 'auc': 0.55, 'ks': 0.12, 'coverage': 0.72, 'bad_rate': 0.15, 'n': 4800},
        {'model': 'model_C', 'auc': 0.58, 'ks': 0.15, 'coverage': 0.68, 'bad_rate': 0.18, 'n': 4500},
    ],
    'correlation': [
        {'model_a': 'model_A', 'model_b': 'model_B', 'correlation': 0.92},
        {'model_a': 'model_B', 'model_b': 'model_C', 'correlation': 0.75},
    ],
    'complementarity': [
        {'model_a': 'model_A', 'model_b': 'model_C', 'complementarity': 0.15},
    ],
    'strategy_metrics': {
        'strategies': [
            {'main_model': 'model_C', 'rescue_model': 'model_A', 'reject_rate': 10, 'pass_rate': 0.22, 'pass_bad_rate': 0.16, 'rescue_count': 50},
        ]
    },
    'data_summary': {
        'total_samples': 5000,
        'bad_samples': 600,
        'overall_bad_rate': 0.12
    }
}

print("=== Test 1: India Market (Low Performance Models) ===")
result1 = generate_llm_dynamic_suggestion(test_data, biz_scenario='first_loan', biz_country='india', biz_module='model')
print("success:", result1.get("success"))
print("source:", result1.get("source"))
print("suggestions count:", len(result1.get("suggestions", [])))
for i, s in enumerate(result1.get('suggestions', [])[:3]):
    print("Suggestion %d: %s" % (i+1, s.get("title", "No Title")))
    content = s.get('content', '')
    print("  Content: %s..." % content[:150])
print()

# Another different country data
test_data2 = {
    'performance': [
        {'model': 'model_X', 'auc': 0.72, 'ks': 0.38, 'coverage': 0.92, 'bad_rate': 0.05, 'n': 8000},
        {'model': 'model_Y', 'auc': 0.68, 'ks': 0.32, 'coverage': 0.88, 'bad_rate': 0.08, 'n': 7500},
    ],
    'correlation': [
        {'model_a': 'model_X', 'model_b': 'model_Y', 'correlation': 0.65},
    ],
    'complementarity': [
        {'model_a': 'model_X', 'model_b': 'model_Y', 'complementarity': 0.35},
    ],
    'strategy_metrics': {
        'strategies': [
            {'main_model': 'model_X', 'rescue_model': 'model_Y', 'reject_rate': 15, 'pass_rate': 0.35, 'pass_bad_rate': 0.06, 'rescue_count': 200},
        ]
    },
    'data_summary': {
        'total_samples': 8000,
        'bad_samples': 400,
        'overall_bad_rate': 0.05
    }
}

print("=== Test 2: Indonesia Market (High Performance Models) ===")
result2 = generate_llm_dynamic_suggestion(test_data2, biz_scenario='first_loan', biz_country='indonesia', biz_module='model')
print("success:", result2.get("success"))
print("source:", result2.get("source"))
print("suggestions count:", len(result2.get("suggestions", [])))
for i, s in enumerate(result2.get('suggestions', [])[:3]):
    print("Suggestion %d: %s" % (i+1, s.get("title", "No Title")))
    content = s.get('content', '')
    print("  Content: %s..." % content[:150])
print()

print("=== Comparison: Are suggestions different? ===")
sugs1 = [s.get('title', '') for s in result1.get('suggestions', [])]
sugs2 = [s.get('title', '') for s in result2.get('suggestions', [])]
print("India suggestions:", sugs1)
print("Indonesia suggestions:", sugs2)
print("Same:", sugs1 == sugs2)
