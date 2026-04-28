from typing import Any, Dict, List

from services.agent_router import call_glm_only, call_gpt_only


KNOWLEDGE_RAG_SYSTEM_PROMPT = """你是专业风控知识助手。请基于给定知识片段回答：
1. 先给结论，再给可执行建议；
2. 仅在片段有依据时给确定结论；
3. 片段不足时可补充通用经验，并明确是通用经验；
4. 不要虚构项目中不存在的规则或口径。"""

KNOWLEDGE_FALLBACK_SYSTEM_PROMPT = """你是金融风控知识助手。请针对用户问题给出专业、可执行建议，
覆盖建模、策略与指标解释，中文输出，简洁清晰。"""


def build_retrieval_fallback_answer(question: str, matches: List[Dict[str, Any]]) -> str:
    top_matches = matches[:3]
    if not top_matches:
        return (
            "当前未命中明确知识片段。请补充业务场景（首贷/复贷、国家、渠道）与指标口径"
            "（如 KS/AUC/PSI、分箱、规则命中），我会给出更精准建议。"
        )

    lines = [f"针对你的问题「{question}」，先给你基于知识库片段的建议："]
    for idx, item in enumerate(top_matches, start=1):
        title = str(item.get("title", "")).strip() or f"参考片段{idx}"
        snippet = str(item.get("snippet", "")).strip() or str(item.get("content", "")).strip()
        if len(snippet) > 200:
            snippet = snippet[:200].rstrip() + "..."
        lines.append(f"{idx}. {title}：{snippet}")
    lines.append("如需我量化阈值和策略，请补充目标坏账率、通过率目标和样本周期。")
    return "\n".join(lines)


def _call_glm_then_gpt(
    prompt: str,
    system: str,
    temperature: float,
    max_tokens: int,
) -> Dict[str, Any]:
    # 先 GLM，失败再 GPT（满足“GLM 优先，GPT 兜底”）
    glm_result = call_glm_only(prompt=prompt, system=system)
    if glm_result.get("success") and (glm_result.get("content") or "").strip():
        return glm_result

    gpt_result = call_gpt_only(prompt=prompt, system=system)
    if gpt_result.get("success") and (gpt_result.get("content") or "").strip():
        return gpt_result

    return glm_result if glm_result.get("error") else gpt_result


def answer_with_knowledge(
    question: str,
    retrieval_result: Dict[str, Any],
    extra_context: str = "",
) -> Dict[str, Any]:
    matches = retrieval_result.get("matches", [])
    is_fallback = retrieval_result.get("fallback", False)

    if matches:
        sections = []
        for index, item in enumerate(matches[:4], start=1):
            content = str(item.get("content", "")).strip()
            if len(content) > 320:
                content = content[:320].rstrip() + "..."
            sections.append(
                f"[知识片段{index}]\n"
                f"标题：{item.get('title', '')}\n"
                f"主题：{item.get('topic', '')}\n"
                f"内容：{content}"
            )

        context_block = "\n\n".join(sections)
        fallback_hint = (
            "\n\n注意：本次检索未命中直接匹配片段，以下为相关背景，请结合通用经验作答。"
            if is_fallback
            else ""
        )
        user_prompt = f"""请基于以下知识片段回答问题。{fallback_hint}

{context_block}

[附加背景]
{extra_context or '无'}

[用户问题]
{question}

请先给结论，再给可执行建议。"""

        result = _call_glm_then_gpt(
            prompt=user_prompt,
            system=KNOWLEDGE_RAG_SYSTEM_PROMPT,
            temperature=0.3,
            max_tokens=800,
        )

        sources = [
            {
                "title": item.get("title", ""),
                "topic": item.get("topic", ""),
                "snippet": item.get("snippet", ""),
                "source_type": item.get("source_type", ""),
                "source_ref": item.get("source_ref", ""),
            }
            for item in matches[:4]
        ]

        answer_text = (result.get("content") or "").strip()
        if not answer_text:
            answer_text = build_retrieval_fallback_answer(question, matches)

        return {
            "success": True,
            "answer": answer_text,
            "model": result.get("model", ""),
            "sources": sources,
            "used_retrieval": True,
        }

    result = _call_glm_then_gpt(
        prompt=f"""用户问题：{question}

附加背景：{extra_context or '无'}

请给出专业回答和下一步可执行建议。""",
        system=KNOWLEDGE_FALLBACK_SYSTEM_PROMPT,
        temperature=0.4,
        max_tokens=700,
    )

    answer_text = (result.get("content") or "").strip()
    if not answer_text:
        answer_text = (
            "当前未命中明确知识片段，且大模型服务暂不可用。\n"
            "建议先执行：\n"
            "1. 上传带 label 的样本并运行模型/规则分析；\n"
            "2. 明确问题口径（KS/AUC/PSI、分箱、规则命中、阈值）；\n"
            "3. 补充业务场景（首贷/复贷、国家、渠道）后再提问。"
        )

    return {
        "success": True,
        "answer": answer_text,
        "model": result.get("model", ""),
        "sources": [],
        "used_retrieval": False,
    }
