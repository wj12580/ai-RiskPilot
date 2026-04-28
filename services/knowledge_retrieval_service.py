import json
import re
from typing import Dict, List, Any

from models.knowledge_document import KnowledgeDocument
from models.knowledge_chunk import KnowledgeChunk


TOPIC_KEYWORDS = {
    '模型评估': [
        'ks', 'auc', 'iv', 'psi', 'roc', 'gini', '评估', '模型', '指标',
        '效果', '区分', '排序', '坏率', '好坏', 'auc值', 'ks值', '稳定性',
        'vintage', '坏账', '逾期', 'overdue', 'default', '首逾',
        '特征', '变量', 'spearman', '相关', 'shap', '重要性', '贡献',
        'xgboost', 'lgb', 'lightgbm', 'logistic', '评分卡',
    ],
    '分箱分析': [
        '分箱', 'lift', 'woe', 'iv', '单调', '等频', '等距', '分位',
        '箱', '区间', '分段', '分组', '阈值', '截断', 'bin', 'bucket',
        '逾期率', '坏率', '累积', 'encoding', '编码',
    ],
    '策略设计': [
        '策略', '阈值', '准入', '复贷', '首贷', '捞回', '串行',
        '拒绝', '通过', '审批', '放款', 'cutoff', 'cut-off',
        '多模型', '联合', '组合', '互补', '分层',
        'apr', '利率', '资金', '收益', '风险',
    ],
    '规则分析': [
        '规则', '命中', '拦截', '欺诈', '画像',
        '黑名单', '白名单', '误杀', '精准', '冗余',
        '决策树', '树', 'tree', '节点', 'leaf',
    ],
    '系统使用': [
        '怎么用', '接口', '上传', '字段', 'agent', '系统',
        '如何', '操作', '步骤', '功能', 'csv', 'excel',
        '报告', '下载', '使用', '流程', '教程',
    ],
    '模型相关性': [
        '相关性', 'spearman', '聚类', '互补性', '热力图', '矩阵',
        '相似', '独立', '冗余', '层次', 'hierarchical',
    ],
    '业务基线': [
        '印度', '印尼', '菲律宾', '首贷', '复贷', 'apr',
        '海外', '国家', '基线', 'india', 'indonesia', 'philippines',
        'm1', 'm2', 'm3', 'vintage', '坏账率',
    ],
}

TYPE_KEYWORDS = {
    'project': ['系统', '接口', '上传', '字段', '前端', 'agent', '如何', '怎么', '使用', 'csv', 'excel', '流程'],
    'metric': ['ks', 'auc', 'psi', 'iv', 'lift', '分箱', '相关性', '指标', 'woe', '逾期率', '坏率'],
    'domain': ['策略', '规则', '风控', '欺诈', '准入', '复贷', '首贷', '捞回', '串行', '画像'],
}


def _tokenize(text: str) -> List[str]:
    raw_tokens = [t.lower() for t in re.findall(r'[A-Za-z0-9_\-一-鿿]+', text or '') if t.strip()]
    expanded = []
    for token in raw_tokens:
        expanded.append(token)
        for part in re.findall(r'[A-Za-z]+|\d+|[一-鿿]{1,8}', token):
            part = part.lower().strip()
            if part and part not in expanded:
                expanded.append(part)
    return expanded


def analyze_query(question: str) -> Dict[str, Any]:
    tokens = _tokenize(question)
    knowledge_type = 'domain'
    topic = ''

    for candidate_type, words in TYPE_KEYWORDS.items():
        if any(word.lower() in tokens or word in question for word in words):
            knowledge_type = candidate_type
            break

    for candidate_topic, words in TOPIC_KEYWORDS.items():
        if any(word.lower() in tokens or word in question for word in words):
            topic = candidate_topic
            break

    return {
        'knowledge_type': knowledge_type,
        'topic': topic,
        'tokens': tokens,
    }


def _load_keywords(raw: str) -> List[str]:
    try:
        data = json.loads(raw or '[]')
        return [str(x).lower() for x in data]
    except Exception:
        return []


def _score_chunk(chunk, doc, tokens: List[str]) -> int:
    """计算单个 chunk 的关键词匹配分数"""
    score = 0
    doc_keywords = _load_keywords(doc.keywords)
    chunk_text = chunk.content or ''
    chunk_lower = chunk_text.lower()
    chunk_keywords = _load_keywords(chunk.keywords)
    for token in tokens:
        if token in (doc.title or '').lower():
            score += 5
        if token in doc_keywords:
            score += 3
        if token in chunk_keywords:
            score += 2
        if token in chunk_lower:
            score += 1
    return score


def retrieve_knowledge(question: str, limit: int = 5) -> Dict[str, Any]:
    """
    检索与问题相关的知识片段。

    策略：
    1. 先用关键词精确命中（type + topic 过滤 + token 匹配）
    2. 若精确命中数量不足 limit，扩大范围：放宽 type/topic 过滤，对全量 published 文档重新打分
    3. 若仍无任何命中（score>0），返回全量文档中评分最高的 top-N（兜底：始终有内容给 LLM）
    """
    query = analyze_query(question)
    tokens = query['tokens']

    # ── 第一轮：精确检索（type + topic 双过滤）─────────────────────────────
    docs_query = KnowledgeDocument.query.filter_by(status='published')
    if query['knowledge_type']:
        docs_query = docs_query.filter_by(knowledge_type=query['knowledge_type'])
    documents = docs_query.all()

    if query['topic']:
        topic_filtered = [doc for doc in documents if doc.topic == query['topic']]
        if topic_filtered:
            documents = topic_filtered

    scored_chunks = _collect_scored_chunks(documents, tokens)

    # ── 第二轮：放宽过滤，扩展到全量 published 文档 ─────────────────────────
    if len([c for c in scored_chunks if c['score'] > 0]) < limit:
        all_docs = KnowledgeDocument.query.filter_by(status='published').all()
        extra_doc_ids = {doc.id for doc in documents}
        extra_docs = [d for d in all_docs if d.id not in extra_doc_ids]
        if extra_docs:
            extra_chunks = _collect_scored_chunks(extra_docs, tokens)
            # 合并，过滤重复
            existing_chunk_pairs = {(c['document_id'], c['snippet'][:50]) for c in scored_chunks}
            for ec in extra_chunks:
                key = (ec['document_id'], ec['snippet'][:50])
                if key not in existing_chunk_pairs:
                    scored_chunks.append(ec)
                    existing_chunk_pairs.add(key)

    scored_chunks.sort(key=lambda x: (-x['score'], x['title']))
    top_chunks = [c for c in scored_chunks if c['score'] > 0][:limit]

    # ── 第三轮：兜底 —— 无任何 token 命中时，取全库最热门文档的片段 ─────────
    if not top_chunks:
        all_docs = KnowledgeDocument.query.filter_by(status='published').all()
        fallback_chunks = _collect_scored_chunks(all_docs, tokens, fallback=True)
        top_chunks = fallback_chunks[:limit]

    return {
        'query': query,
        'matches': top_chunks,
        'hit': len(top_chunks) > 0,
        'fallback': len([c for c in top_chunks]) > 0 and all(c.get('score', 0) == 0 for c in top_chunks),
    }


def _collect_scored_chunks(documents, tokens: List[str], fallback: bool = False) -> List[Dict]:
    """遍历文档列表，计算每个 chunk 的匹配分数，返回已打分的 chunk 列表"""
    scored_chunks = []
    for doc in documents:
        doc_keywords = _load_keywords(doc.keywords)
        chunks = KnowledgeChunk.query.filter_by(document_id=doc.id).all()
        for chunk in chunks:
            chunk_text = chunk.content or ''
            chunk_lower = chunk_text.lower()
            chunk_keywords = _load_keywords(chunk.keywords)
            score = 0
            for token in tokens:
                if token in (doc.title or '').lower():
                    score += 5
                if token in doc_keywords:
                    score += 3
                if token in chunk_keywords:
                    score += 2
                if token in chunk_lower:
                    score += 1

            # fallback 模式下即使 score=0 也纳入（兜底）
            if score > 0 or fallback:
                scored_chunks.append({
                    'score': score,
                    'document_id': doc.id,
                    'title': doc.title,
                    'topic': doc.topic,
                    'knowledge_type': doc.knowledge_type,
                    'source_type': doc.source_type,
                    'source_ref': doc.source_ref,
                    'snippet': chunk.content[:240],
                    'content': chunk.content,
                })
    return scored_chunks
