"""
风控知识库问答 API 路由
POST /api/knowledge/ask   提问（调用大模型回答风控知识）
GET  /api/knowledge/topics 获取知识分类主题
"""

import json
from flask import Blueprint, request, jsonify
from services.agent_router import router, RISK_SYSTEM_PROMPT

knowledge_bp = Blueprint('knowledge', __name__)

# ── 风控知识库系统提示词 ──────────────────────────────────────────────────────
KNOWLEDGE_SYSTEM_PROMPT = """你是一位经验丰富的风控策略分析师，专注于信贷风控领域。
你的职责是回答用户关于风控指标、策略分析方法、模型评估等方面的专业问题。

你精通以下领域：
1. **模型评估指标**：KS值、AUC/ROC、IV（信息价值）、PSI（群体稳定性指数）、Gini系数等的计算方法、含义与业务解读
2. **分箱与特征工程**：等频分箱、等距分箱、卡方分箱、IV分箱的原理与应用
3. **逾期率分析**：M1/M2/M3逾期率定义、Vintage分析、Lift曲线解读
4. **策略设计**：准入策略、额度策略、定价策略、催收策略的设计思路
5. **模型开发**：逻辑回归、GBM、SHAP解释、样本设计、时间窗设置
6. **PSI稳定性**：PSI计算、CSI特征稳定性、模型漂移检测与处理
7. **信贷业务知识**：首贷/复贷策略、申请评分卡、行为评分卡、催收评分卡

回答要求：
- 专业准确，结合实际业务场景
- 包含计算公式（用数学表达式或示例说明）
- 给出可操作的实践建议
- 中文回答，简洁易懂
- 如涉及策略历史效果，给出通用的行业参考范围
"""

# ── 知识主题分类 ──────────────────────────────────────────────────────────────
KNOWLEDGE_TOPICS = [
    {
        "category": "模型评估",
        "icon": "📊",
        "questions": [
            "KS值怎么计算？多少算好？",
            "AUC和KS有什么区别？",
            "IV值怎么计算？特征IV多少值得保留？",
            "PSI是什么？怎么判断模型稳定性？",
        ]
    },
    {
        "category": "分箱分析",
        "icon": "📦",
        "questions": [
            "逾期率/Lift分析怎么做？",
            "等频分箱和等距分箱的区别？",
            "WOE编码的原理和应用",
            "分箱数量如何选择？",
        ]
    },
    {
        "category": "策略设计",
        "icon": "🎯",
        "questions": [
            "首贷准入策略如何设计？",
            "复贷策略和首贷策略有什么区别？",
            "多评分模型串行策略是什么？",
            "策略分层效果如何评估？",
        ]
    },
    {
        "category": "业务指标",
        "icon": "📈",
        "questions": [
            "M1/M2/M3逾期率定义是什么？",
            "Vintage分析是什么？怎么做？",
            "坏账率和逾期率有什么区别？",
            "Lift值怎么理解？",
        ]
    },
    {
        "category": "模型开发",
        "icon": "🤖",
        "questions": [
            "申请评分卡和行为评分卡的区别？",
            "SHAP值如何解释特征重要性？",
            "训练集和验证集如何划分？",
            "样本不均衡怎么处理？",
        ]
    },
]


# ── 获取知识主题 ──────────────────────────────────────────────────────────────
@knowledge_bp.route('/topics', methods=['GET'])
def get_topics():
    """获取知识库主题分类和常见问题"""
    return jsonify({'topics': KNOWLEDGE_TOPICS})


# ── 提问接口 ──────────────────────────────────────────────────────────────────
@knowledge_bp.route('/ask', methods=['POST'])
def ask():
    """
    调用大模型回答风控知识问题
    Body: { "question": "...", "context": "..." (可选附加上下文) }
    """
    data = request.get_json()
    if not data:
        return jsonify({'error': '请求体不能为空'}), 400

    question = (data.get('question') or '').strip()
    context = (data.get('context') or '').strip()

    if not question:
        return jsonify({'error': '问题不能为空'}), 400

    if len(question) > 2000:
        return jsonify({'error': '问题长度不能超过2000字'}), 400

    # 构建用户消息
    user_msg = question
    if context:
        user_msg = f"【背景信息】\n{context}\n\n【问题】\n{question}"

    try:
        result = router.call(
            prompt=user_msg,
            system=KNOWLEDGE_SYSTEM_PROMPT,
            temperature=0.6,
            max_tokens=2000,
        )
        if result.get('success'):
            return jsonify({
                'question': question,
                'answer': result['content'],
                'model': result.get('model', ''),
                'success': True,
            })
        else:
            return jsonify({
                'question': question,
                'answer': f'暂时无法获取AI回答，请检查大模型配置。错误：{result.get("error", "未知错误")}',
                'model': '',
                'success': False,
            })
    except Exception as e:
        # 若大模型调用失败，返回结构化错误
        return jsonify({
            'question': question,
            'answer': f'暂时无法获取AI回答，请检查大模型配置。错误信息：{str(e)}',
            'model': '',
            'success': False,
        }), 200  # 仍返回200，让前端区分处理
