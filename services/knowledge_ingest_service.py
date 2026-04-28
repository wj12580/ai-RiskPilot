import json
import re
from typing import Dict, List, Any

from models.database import db
from models.knowledge_document import KnowledgeDocument
from models.knowledge_chunk import KnowledgeChunk


def _normalize_keywords(words: List[str]) -> List[str]:
    seen = []
    for word in words:
        word = (word or '').strip().lower()
        if len(word) < 2:
            continue
        if word not in seen:
            seen.append(word)
    return seen[:30]


def _extract_keywords(text: str) -> List[str]:
    tokens = re.findall(r'[A-Za-z0-9_\-一-鿿]+', text or '')
    stop_words = {
        'the', 'and', 'for', 'with', 'this', 'that', 'from', 'into', '你', '我', '他', '她', '它',
        '一个', '一些', '可以', '如何', '什么', '以及', '进行', '相关', '分析', '说明', '支持'
    }
    return _normalize_keywords([t for t in tokens if t.lower() not in stop_words])


def _chunk_text(text: str, max_chars: int = 500) -> List[str]:
    text = (text or '').strip()
    if not text:
        return []
    paragraphs = [p.strip() for p in re.split(r'\n{2,}', text) if p.strip()]
    chunks = []
    current = ''
    for paragraph in paragraphs:
        if len(current) + len(paragraph) + 2 <= max_chars:
            current = f"{current}\n\n{paragraph}".strip()
        else:
            if current:
                chunks.append(current)
            if len(paragraph) <= max_chars:
                current = paragraph
            else:
                for i in range(0, len(paragraph), max_chars):
                    part = paragraph[i:i + max_chars].strip()
                    if part:
                        chunks.append(part)
                current = ''
    if current:
        chunks.append(current)
    return chunks


def _upsert_document(doc: Dict[str, Any]) -> KnowledgeDocument:
    existing = KnowledgeDocument.query.filter_by(source_ref=doc['source_ref']).first()
    if existing is None:
        existing = KnowledgeDocument()
        db.session.add(existing)

    existing.title = doc['title']
    existing.knowledge_type = doc['knowledge_type']
    existing.topic = doc['topic']
    existing.source_type = doc['source_type']
    existing.source_ref = doc['source_ref']
    existing.summary = doc['summary']
    existing.content_markdown = doc['content_markdown']
    existing.keywords = json.dumps(doc['keywords'], ensure_ascii=False)
    existing.status = 'published'
    db.session.flush()
    return existing


def _replace_chunks(document: KnowledgeDocument, chunks: List[str]) -> None:
    KnowledgeChunk.query.filter_by(document_id=document.id).delete()
    for index, chunk in enumerate(chunks):
        db.session.add(KnowledgeChunk(
            document_id=document.id,
            chunk_index=index,
            content=chunk,
            keywords=json.dumps(_extract_keywords(chunk), ensure_ascii=False),
            token_length=len(chunk)
        ))


def _seed_documents() -> List[Dict[str, Any]]:
    docs = [
        {
            'title': '风控知识问答主题',
            'knowledge_type': 'domain',
            'topic': '模型评估',
            'source_type': 'code_seed',
            'source_ref': 'routes/knowledge.py#topics',
            'summary': '沉淀模型评估、分箱分析、策略设计、业务指标和模型开发等知识问答主题。',
            'content_markdown': (
                '模型评估关注 KS、AUC、IV、PSI 等指标。\n\n'
                '分箱分析关注等频分箱、等距分箱、WOE 编码、Lift 解读。\n\n'
                '策略设计关注首贷、复贷、多模型串行策略和分层效果评估。\n\n'
                '业务指标关注 M1/M2/M3、Vintage、坏账率与逾期率。\n\n'
                '模型开发关注申请评分卡、行为评分卡、SHAP、样本不均衡处理。'
            ),
        },
        {
            'title': '风控策略建议方法论',
            'knowledge_type': 'domain',
            'topic': '策略设计',
            'source_type': 'code_seed',
            'source_ref': 'services/suggestion_service.py#strategy',
            'summary': '总结策略专家提示词中的核心方法：先发现问题，再提炼洞察，再给出针对性建议。',
            'content_markdown': (
                '策略建议必须基于实际分析数据，先发现问题，再给出对应建议。\n\n'
                '重点场景包括高 APR 信贷、欺诈检测、信用评估、分箱优化、阈值设定和规则拦截效果。\n\n'
                '规则分析重点关注高拦截率规则是否误杀、规则命中后的逾期率 Lift、规则冗余和规则与分数的串行最优阈值。'
            ),
        },
        {
            'title': '业务场景与国家基线',
            'knowledge_type': 'domain',
            'topic': '业务基线',
            'source_type': 'code_seed',
            'source_ref': 'services/llm_service.py#biz_context',
            'summary': '整理印度、印尼、菲律宾，以及首贷/复贷、模型/规则模块的业务基线。',
            'content_markdown': (
                '项目内置了印度、印尼、菲律宾三国场景，以及首贷和复贷两类客群。\n\n'
                '模型分析模块关注 AUC、KS、PSI 等评估标准。\n\n'
                '策略分析模块关注高收益覆盖高风险、快速放款和逾期容忍上限。'
            ),
        },
        {
            'title': 'RiskPilot 项目能力说明',
            'knowledge_type': 'project',
            'topic': '系统使用',
            'source_type': 'code_seed',
            'source_ref': 'README.md#project',
            'summary': '说明 RiskPilot 支持的分析、记录、复盘和知识问答能力。',
            'content_markdown': (
                'RiskPilot 覆盖分析、记录、复盘完整策略生命周期。\n\n'
                '系统支持上传 CSV/Excel，自动计算 KS/AUC/PSI，生成 AI 策略建议，并支持多 Agent 协作分析。\n\n'
                '还支持策略调整记录、策略复盘和知识问答。'
            ),
        },
        {
            'title': '模型分箱分析口径',
            'knowledge_type': 'metric',
            'topic': '分箱分析',
            'source_type': 'code_seed',
            'source_ref': 'services/model_binning_service.py#equal_freq_binning',
            'summary': '总结项目内模型分箱的分析口径与输出指标。',
            'content_markdown': (
                '模型分箱采用等频分箱。\n\n'
                '当模型分最大值大于 1 时，按倒序理解为分数越高风险越低；否则按正序理解为分数越高风险越高。\n\n'
                '输出关注箱号、分数区间、样本数、坏样本数、累积坏样本占比、逾期率、Lift 和累积 KS。'
            ),
        },
        {
            'title': '模型相关性分析口径',
            'knowledge_type': 'metric',
            'topic': '模型相关性',
            'source_type': 'code_seed',
            'source_ref': 'services/model_correlation_service.py#compute_correlation',
            'summary': '总结项目内模型相关性、互补性和串行策略模拟方法。',
            'content_markdown': (
                '模型相关性分析包含覆盖率、AUC、KS、Spearman 相关性、层次聚类和模型互补性。\n\n'
                '互补性通过比较不同模型在拒绝样本上的差异来衡量捞回潜力。\n\n'
                '串行策略模拟会先选主模型，再模拟不同拒绝率和副模型捞回效果。'
            ),
        },
        {
            'title': '规则分析口径',
            'knowledge_type': 'metric',
            'topic': '规则分析',
            'source_type': 'code_seed',
            'source_ref': 'services/rules_analysis_service.py#run_rule_analysis',
            'summary': '总结项目内规则分箱、用户画像和决策树分析方法。',
            'content_markdown': (
                '规则分析关注每个规则取值或分箱后的样本数、坏样本数、坏样本率和 Lift。\n\n'
                '当规则取值较多时使用等频分箱；当取值较少时直接按离散值分析。\n\n'
                '同时会生成好坏用户画像、规则组合和决策树视图。'
            ),
        },
        # ── 扩充文档 ──────────────────────────────────────────────────────────
        {
            'title': 'KS 与 AUC 指标解读',
            'knowledge_type': 'metric',
            'topic': '模型评估',
            'source_type': 'code_seed',
            'source_ref': 'knowledge/ks_auc_explanation',
            'summary': '详解风控模型中 KS 和 AUC 的含义、计算方式及优劣判断标准。',
            'content_markdown': (
                'KS（Kolmogorov-Smirnov）衡量模型区分好坏客户的最大累计分离度，值越大越好，通常要求 KS ≥ 0.2 为及格，≥ 0.4 为优秀。\n\n'
                'AUC（Area Under ROC Curve）衡量模型整体排序能力，取值 0.5~1.0，越大越好。AUC=0.5 等同于随机，AUC≥0.7 一般认为有实用价值。\n\n'
                'KS 关注最大分离点，AUC 关注全局排序。两者结合使用效果更全面。\n\n'
                'PSI（Population Stability Index）衡量模型分数分布稳定性，PSI<0.1 为稳定，0.1~0.25 为轻微波动，>0.25 为显著漂移。\n\n'
                'IV（Information Value）衡量特征对目标的区分能力，IV<0.02 无价值，0.02~0.1 弱，0.1~0.3 中等，>0.3 强。'
            ),
        },
        {
            'title': 'WOE 与 IV 的计算与应用',
            'knowledge_type': 'metric',
            'topic': '分箱分析',
            'source_type': 'code_seed',
            'source_ref': 'knowledge/woe_iv_binning',
            'summary': '介绍 WOE 编码和 IV 值的计算原理及在评分卡建模中的应用。',
            'content_markdown': (
                'WOE（Weight of Evidence）= ln(好客户分布 / 坏客户分布)，用于将特征转化为对数比率，可以捕捉非线性关系。\n\n'
                'WOE 值越大表示该箱对应的客户越优质（坏客户比例低），WOE 值越小表示越差。\n\n'
                'IV = Σ (好客户分布 - 坏客户分布) × WOE，汇总各箱的区分度。\n\n'
                '等频分箱是最常用的分箱方式，每箱样本数相等，适合数值型特征。\n\n'
                '单调性是分箱质量的重要标准，WOE 序列应该与风险方向一致（单调递增或递减）。'
            ),
        },
        {
            'title': '逾期率与首逾分析',
            'knowledge_type': 'domain',
            'topic': '模型评估',
            'source_type': 'code_seed',
            'source_ref': 'knowledge/overdue_rate_analysis',
            'summary': '解释首逾率、逾期率、M1/M2/M3 等核心风控业务指标的含义。',
            'content_markdown': (
                '首逾率（First Payment Default）指贷款后第一期账单未还款的比例，是衡量申请评分卡效果的核心指标。\n\n'
                '逾期率按账龄分为 M1（逾期 1~30 天）、M2（逾期 31~60 天）、M3（逾期 61~90 天）等。\n\n'
                'Vintage 分析以放款月份为维度，追踪不同批次贷款的逾期率发展趋势，用于评估策略效果。\n\n'
                '坏账率（Bad Rate / Default Rate）通常以 M3+ 或 M6+ 作为坏账定义口径。\n\n'
                'Lift 值 = 该箱坏率 / 整体坏率，大于 1 表示该箱坏客户集中，小于 1 表示该箱客质优良。'
            ),
        },
        {
            'title': '多模型串行策略与捞回方法',
            'knowledge_type': 'domain',
            'topic': '策略设计',
            'source_type': 'code_seed',
            'source_ref': 'knowledge/serial_strategy_rescue',
            'summary': '介绍多模型串行策略设计、捞回逻辑和互补性评估。',
            'content_markdown': (
                '串行策略：先用主模型（KS 最高）做第一层过滤，被主模型拒绝的客户再由副模型进行捞回评估。\n\n'
                '捞回：对被主模型拒绝但副模型打分高的客户进行二次通过，捞回率 = 被捞回样本数 / 被主模型拒绝总样本数。\n\n'
                '互补性 = A 拒 B 不拒的比例 + B 拒 A 不拒的比例，互补性越高表示两个模型各有所长，适合搭配使用。\n\n'
                '串行策略效果评估关注：通过率、通过后逾期率、捞回量和捞回后坏率。\n\n'
                '阈值选取一般以通过逾期率不超过业务容忍上限为约束，在此条件下最大化通过率。'
            ),
        },
        {
            'title': '特征工程与特征筛选',
            'knowledge_type': 'domain',
            'topic': '模型评估',
            'source_type': 'code_seed',
            'source_ref': 'knowledge/feature_engineering',
            'summary': '介绍风控建模中常用的特征工程和特征筛选方法。',
            'content_markdown': (
                '特征筛选常用方法：缺失率过滤（缺失率>70%剔除）、单值率过滤（单值占比>95%剔除）、IV 筛选（IV<0.02 剔除）、PSI 稳定性筛选（PSI>0.25 慎用）。\n\n'
                'Spearman 相关系数衡量特征与目标的秩相关强度，|corr|>0.1 通常认为有预测价值。\n\n'
                'SHAP（SHapley Additive exPlanations）用于解释模型预测，展示每个特征对单次预测的贡献方向和大小。\n\n'
                'Optuna 是常用的超参数调优框架，支持 XGBoost、LightGBM、Logistic Regression 等模型。\n\n'
                '样本不均衡处理：可用过采样（SMOTE）、欠采样、class_weight 调整、focal loss 等方法。'
            ),
        },
        {
            'title': '欺诈规则与反欺诈策略',
            'knowledge_type': 'domain',
            'topic': '规则分析',
            'source_type': 'code_seed',
            'source_ref': 'knowledge/fraud_rules',
            'summary': '介绍反欺诈规则设计、命中率分析和规则效果评估方法。',
            'content_markdown': (
                '欺诈规则通常基于设备指纹、IP 地址、手机号黑名单、身份证黑名单等维度设置。\n\n'
                '规则命中率 = 被规则拦截的申请数 / 总申请数，命中率过高可能导致误杀，命中率过低则规则无效。\n\n'
                '规则 Lift = 规则命中人群的坏率 / 整体坏率，Lift 越大表示规则精准度越高。\n\n'
                '规则冗余分析：如果两条规则的命中人群高度重叠，应考虑合并或删除其中一条。\n\n'
                '规则误杀率 = 规则拦截的好客户数 / 规则命中总数，误杀率越低规则越精准。'
            ),
        },
        {
            'title': '准入策略与阈值设定',
            'knowledge_type': 'domain',
            'topic': '策略设计',
            'source_type': 'code_seed',
            'source_ref': 'knowledge/admission_threshold',
            'summary': '介绍信贷风控中准入阈值设定、分层审批和动态调整方法。',
            'content_markdown': (
                '准入阈值（Cut-off）是模型分数的通过/拒绝分界点，设置过高会导致通过率下降，设置过低会导致坏账率上升。\n\n'
                '分层审批：将客户按分数分为自动通过、人工审核、自动拒绝三档，提高审批效率。\n\n'
                '动态阈值调整：根据宏观经济环境、资金成本、业务目标定期调整阈值，通常每季度复盘一次。\n\n'
                '首贷客户风险较高，准入标准应更严格；复贷客户有还款历史，可适当放宽准入条件。\n\n'
                '阈值敏感性分析：通过 ROC 曲线和分箱逾期率，评估不同阈值下通过率和坏账率的权衡关系。'
            ),
        },
        {
            'title': '数据上传与分析流程说明',
            'knowledge_type': 'project',
            'topic': '系统使用',
            'source_type': 'code_seed',
            'source_ref': 'knowledge/system_workflow',
            'summary': '说明 RiskPilot 的数据上传格式、分析流程和常见问题处理。',
            'content_markdown': (
                '数据上传支持 CSV 和 Excel 格式，文件中需包含目标列（如 label、overdue_m1）和至少一个数值型特征或分数列。\n\n'
                '分析流程：上传文件 → 选择目标列和分数列 → 选择分析模式 → 查看分析报告 → AI 策略建议。\n\n'
                '目标列要求为 0/1 二值变量，1 表示坏客户/逾期，0 表示好客户/正常。\n\n'
                '如需 Agent 分析，系统会自动调用三位专家（数据分析师、金融建模师、策略专家）协作完成分析。\n\n'
                '分析结果支持下载 Excel 报告，也可通过知识问答进一步深入了解具体指标。'
            ),
        },
    ]

    for doc in docs:
        doc['keywords'] = _extract_keywords(' '.join([
            doc['title'], doc['topic'], doc['summary'], doc['content_markdown']
        ]))
    return docs


def ensure_knowledge_base_seeded() -> Dict[str, Any]:
    docs = _seed_documents()
    created = 0
    updated = 0
    for payload in docs:
        existing = KnowledgeDocument.query.filter_by(source_ref=payload['source_ref']).first()
        document = _upsert_document(payload)
        chunks = _chunk_text(payload['content_markdown'])
        _replace_chunks(document, chunks)
        if existing is None:
            created += 1
        else:
            updated += 1
    db.session.commit()
    return {
        'success': True,
        'created': created,
        'updated': updated,
        'documents': len(docs),
    }
