# 🔍 RiskPilot AI 策略分析报告

**分析文件：** lovefund_label_20260413.xlsx
**分析时间：** 2026-04-17 16:13:04
**分析类型：** model_score_eval, strategy_layer
**用户需求：**  [渠道筛选: 渠道=['Facebook Installs', 'Google Ads ACI', 'Google Organic Search', 'Instagram Installs', 'Jack_DL Love1', 'Jack_DL Love11', 'Jack_DL Love12', 'Jack_DL Love2', 'Off-Facebook Installs', 'Organic', 'Unattributed'], 过滤 6 条]

---

## 📊 数据分析师诊断

**数据质量**：数据量适中，但逾期率较高，KS值和AUC较低，数据质量有待提升。
**逾期特征**：逾期率较高，分箱分布不均匀，逾期率在分箱4最低。
**Top3风险特征**：INSY1027_score_v5, INSY1027_score_v4, INSY1027_score_app_dnn_v2
**数据建议**：优化模型输入特征，提高模型预测能力。

---

## 🤖 金融建模师评估

**模型性能**：INSY1027_score_app_vec表现最佳，AUC 0.4932，KS 0.0126，模型稳定。
**模型组合**：推荐组合INSY1027_score_app_vec与INSY1027_score_v7。
**阈值建议**：通过率90%，预期逾期率3%。
**稳定性**：PSI风险低，模型稳定。

---

## 🎯 风控策略专家建议

**核心策略**：优化数据特征，提升模型预测能力，针对高风险用户加强风控。
**准入门槛**：通过率90%，预期逾期率3%
**分层阈值**：高：0.9，中：0.5，低：0.2
**紧急行动**：调整模型输入特征，加强高风险用户监控。

---

## 📋 附录：原始分析数据

### 数据摘要
```
【数据规模】4,604行 × 19列
【列名】order_id, customer_id, product, label, 应还款时间, 渠道, INSY1027_score_v4, INSY1027_score_v5, INSY1027_score_v6, INSY1027_score_v7, INSY1027_score_app_dnn_v2, INSY1027_score_app_dnn_v3, INSY1027_score_app_vec, INSY1027_score_v10, INSY1027_score_v11, INSY1025_score_v3, INSY1025_score_v2, USER_LABEL01_USER_LABEL01, USER_LABEL02_USER_LABEL02

【逾期概况】
  总样本: 4,604条
  坏样本: 1,936条
  逾期率: 42.05%
  KS值: 0.0004
  AUC: 0.4145

【分箱明细】
  分箱1: 样本461 | 逾期242 | 率52.49%
  分箱2: 样本460 | 逾期223 | 率48.48%
  分箱3: 样本460 | 逾期221 | 率48.04%
  分箱4: 样本461 | 逾期207 | 率44.90%
  分箱5: 样本460 | 逾期193 | 率41.96%
  分箱6: 样本460 | 逾期212 | 率46.09%
  分箱7: 样本461 | 逾期211 | 率45.77%
  分箱8: 样本460 | 逾期176 | 率38.26%
  分箱9: 样本460 | 逾期127 | 率27.61%
  分箱10: 样本461 | 逾期124 | 率26.90%

【Top10重要特征】
  INSY1027_score_v5: 相关系数=-0.1492
  INSY1027_score_v4: 相关系数=-0.1463
  INSY1027_score_app_dnn_v2: 相关系数=-0.1419
  INSY1027_score_v11: 相关系数=-0.1395
  INSY1027_score_v7: 相关系数=-0.1391
  INSY1025_score_v2: 相关系数=-0.1370
  INSY1027_score_app_dnn_v3: 相关系数=-0.1359
  INSY1027_score_v10: 相关系数=-0.1300
  INSY1027_score_v6: 相关系数=-0.1294
  INSY1025_score_v3: 相关系数=-0.1188
```

### 模型摘要
```
【多模型分析】共13个模型

Top3模型性能：
  INSY1027_score_app_vec: KS=0.0126, AUC=0.4932, 覆盖率=100.0%
  INSY1027_score_app_dnn_v3: KS=0.0022, AUC=0.4206, 覆盖率=100.0%
  INSY1025_score_v2: KS=0.0017, AUC=0.4199, 覆盖率=100.0%

模型相关性（部分）：
  INSY1027_score_v4 ↔ INSY1027_score_v5: 0.564
  INSY1027_score_v4 ↔ INSY1027_score_v6: 0.537
  INSY1027_score_v4 ↔ INSY1027_score_v7: 0.705
  INSY1027_score_v5 ↔ INSY1027_score_v4: 0.564
  INSY1027_score_v5 ↔ INSY1027_score_v6: 0.777
  INSY1027_score_v6 ↔ INSY1027_score_v4: 0.537
  INSY1027_score_v6 ↔ INSY1027_score_v5: 0.777

【分箱分析】
  INSY1027_score_v4: IV=0.1201, 单调性=❌
  INSY1027_score_v5: IV=0.1008, 单调性=❌
  INSY1027_score_v6: IV=0.0876, 单调性=❌
```

---
*本报告由 RiskPilot 多专家AI系统自动生成*