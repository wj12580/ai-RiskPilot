"""
风控知识库问答 API
POST /api/knowledge/ask
GET  /api/knowledge/topics
GET  /api/knowledge/status
POST /api/knowledge/init
"""

from flask import Blueprint, request, jsonify

from models.knowledge_document import KnowledgeDocument
from services.agent_router import check_router_status
from services.knowledge_ingest_service import ensure_knowledge_base_seeded
from services.knowledge_retrieval_service import retrieve_knowledge
from services.knowledge_answer_service import (
    answer_with_knowledge,
    build_retrieval_fallback_answer,
)

knowledge_bp = Blueprint("knowledge", __name__)

KNOWLEDGE_TOPICS = [
    {
        "category": "模型评估",
        "icon": "📊",
        "questions": [
            "KS 值怎么计算？多少算好？",
            "AUC 和 KS 的区别是什么？",
            "PSI 怎么判断模型稳定性？",
            "IV 一般如何分级？",
            "Gini、AUC、KS 在风控里怎么一起看？",
            "模型上线后多久做一次效果复盘？",
        ],
    },
    {
        "category": "分箱分析",
        "icon": "📦",
        "questions": [
            "逾期率 / Lift 分析怎么做？",
            "等频分箱和等距分箱有什么区别？",
            "WOE 编码的原理是什么？",
            "分箱数量通常怎么选？",
            "坏账率单调性不满足时怎么处理？",
            "累计逾期率在分箱里怎么解释？",
        ],
    },
    {
        "category": "规则策略",
        "icon": "🧩",
        "questions": [
            "首贷策略应该怎么设计？",
            "复贷策略和首贷策略有哪些关键差异？",
            "规则拦截率和命中逾期率如何平衡？",
            "规则与模型分怎么做联合决策？",
            "如何识别并去除规则冗余？",
            "规则阈值调整后要重点看哪些指标？",
        ],
    },
    {
        "category": "组合策略",
        "icon": "🔗",
        "questions": [
            "串行捞回策略如何评估收益与风险？",
            "双模型组合什么时候优于单模型？",
            "主模型+补充模型的阈值如何联调？",
            "相关性高的模型还能组合吗？",
            "如何构建拒绝流量的再筛选策略？",
            "多策略并行时如何做冲突仲裁？",
        ],
    },
    {
        "category": "稳定性监控",
        "icon": "📈",
        "questions": [
            "模型漂移常见信号有哪些？",
            "线上监控看板应包含哪些核心指标？",
            "PSI 超过阈值后应该怎么排查？",
            "样本口径变化会怎样影响监控结论？",
            "节假日或活动期如何做特殊监控？",
            "什么时候应该触发策略回滚？",
        ],
    },
    {
        "category": "业务口径",
        "icon": "📝",
        "questions": [
            "首逾、30+、M1+ 的口径差异是什么？",
            "通过率、授信率、放款率怎么区分？",
            "坏账率按申请维度还是放款维度统计？",
            "样本观察窗怎么设更合理？",
            "拒绝推断在风控复盘中如何使用？",
            "跨渠道对比时如何统一口径？",
        ],
    },
]


@knowledge_bp.route("/topics", methods=["GET"])
def get_topics():
    return jsonify({"topics": KNOWLEDGE_TOPICS})


@knowledge_bp.route("/status", methods=["GET"])
def get_status():
    return jsonify(
        {
            "knowledge_documents": KnowledgeDocument.query.count(),
            "router": check_router_status(),
        }
    )


@knowledge_bp.route("/init", methods=["POST"])
def initialize_knowledge():
    result = ensure_knowledge_base_seeded()
    return jsonify(result)


@knowledge_bp.route("/ask", methods=["POST"])
def ask():
    data = request.get_json() or {}
    question = (data.get("question") or "").strip()
    context = (data.get("context") or "").strip()

    if not question:
        return jsonify({"error": "问题不能为空"}), 400
    if len(question) > 2000:
        return jsonify({"error": "问题长度不能超过2000字"}), 400

    if KnowledgeDocument.query.count() == 0:
        try:
            ensure_knowledge_base_seeded()
        except Exception:
            pass

    retrieval_result = retrieve_knowledge(question)
    if not retrieval_result.get("hit", False):
        try:
            ensure_knowledge_base_seeded()
            retrieval_result = retrieve_knowledge(question)
        except Exception:
            pass

    # 路由层超时保护：避免偶发长等待，超时返回基于检索片段的可执行建议
    try:
        from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError

        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(answer_with_knowledge, question, retrieval_result, context)
            try:
                answer_result = future.result(timeout=25)
            except FuturesTimeoutError:
                future.cancel()
                answer_result = {
                    "success": True,
                    "answer": build_retrieval_fallback_answer(question, retrieval_result.get("matches", [])),
                    "model": "",
                    "sources": retrieval_result.get("matches", [])[:3],
                    "used_retrieval": bool(retrieval_result.get("hit", False)),
                }
    except Exception:
        answer_result = answer_with_knowledge(question, retrieval_result, context)

    if not (answer_result.get("answer") or "").strip():
        answer_result = {
            "success": True,
            "answer": build_retrieval_fallback_answer(question, retrieval_result.get("matches", [])),
            "model": answer_result.get("model", ""),
            "sources": answer_result.get("sources", []),
            "used_retrieval": bool(retrieval_result.get("hit", False)),
        }

    return jsonify(
        {
            "question": question,
            "answer": answer_result.get("answer", ""),
            "model": answer_result.get("model", ""),
            "success": answer_result.get("success", False),
            "sources": answer_result.get("sources", []),
            "used_retrieval": answer_result.get("used_retrieval", False),
            "retrieval": {
                "hit": retrieval_result.get("hit", False),
                "knowledge_type": retrieval_result.get("query", {}).get("knowledge_type", ""),
                "topic": retrieval_result.get("query", {}).get("topic", ""),
                "match_count": len(retrieval_result.get("matches", [])),
            },
        }
    )
