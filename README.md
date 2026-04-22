# RiskPilot · 风控策略分析师 AI 分身

> **AI 创新赛参赛作品** | 专为风控策略分析师设计的智能工作助手

![Python](https://img.shields.io/badge/Python-3.8+-blue.svg)
![Flask](https://img.shields.io/badge/Flask-2.3+-green.svg)
![License](https://img.shields.io/badge/License-MIT-yellow.svg)

---

## 🎯 项目简介

**RiskPilot** 是风控策略分析师的 AI 工作分身，覆盖"分析 → 记录 → 复盘"完整策略生命周期：

- 🤖 **AI 策略分析**：上传数据自动计算 KS/AUC/PSI 等专业指标，调用大模型实时生成策略优化建议
- 📋 **策略调整记录**：结构化保存每次策略调整，形成可追溯的决策档案
- 🔍 **智能策略复盘**：自动对比调整前后指标，量化评估策略效果

---

## 📁 项目结构

```
RiskPilot/
├── app.py                    # Flask 应用主入口
├── requirements.txt          # Python 依赖包
├── README.md                 # 项目说明文档
├── start.bat                 # Windows 一键启动脚本
├── start.sh                  # Linux/Mac 一键启动脚本
│
├── models/                   # 数据库模型层
│   ├── database.py          # 数据库连接配置
│   ├── analysis_task.py     # 分析任务模型
│   ├── strategy_record.py   # 策略记录模型
│   └── strategy_review.py   # 策略复盘模型
│
├── routes/                   # API 路由层
│   ├── analysis.py          # 分析相关接口
│   ├── records.py           # 记录相关接口
│   └── reviews.py           # 复盘相关接口
│
├── services/                 # 业务逻辑层
│   ├── analysis_service.py  # 风控指标计算（KS/AUC/PSI/分箱）
│   ├── suggestion_service.py # AI 建议生成
│   ├── export_service.py    # Excel 导出服务
│   ├── agent_router.py      # 🤖 GLM+DeepSeek 混合路由
│   ├── agent_skills.py      # 🤖 风控技能工具注册表（6个技能）
│   └── agent_orchestrator.py # 🤖 多 Agent 调度器
│
├── static/                   # 静态资源
│   ├── css/style.css        # 样式文件
│   └── js/app.js            # 前端交互逻辑
│
├── templates/                # HTML 模板
│   └── index.html           # 主页面
│
├── uploads/                  # 上传文件存储目录（自动创建）
│
└── .idea/                    # PyCharm 配置
    └── runConfigurations/
        └── RiskPilot.run.xml
```

---

## 🚀 快速开始

### 方式一：一键启动脚本（推荐）

**Windows:**
```bash
# 进入项目目录
cd F:\2022gk\ai赛2\RiskPilot

# 双击运行或命令行执行
start.bat
```

**Linux/Mac:**
```bash
cd RiskPilot
chmod +x start.sh
./start.sh
```

### 方式二：PyCharm 运行

1. 打开 PyCharm → `File` → `Open`
2. 选择 `F:\2022gk\ai赛2\RiskPilot` 文件夹
3. 配置 Python 解释器（File → Settings → Project → Python Interpreter）
4. 点击右上角运行按钮，或按 `Shift+F10`
5. 浏览器访问 `http://127.0.0.1:5000`

### 方式三：手动命令行

```bash
# 1. 进入项目目录
cd F:\2022gk\ai赛2\RiskPilot

# 2. 创建虚拟环境
python -m venv venv

# 3. 激活虚拟环境
# Windows:
venv\Scripts\activate
# Linux/Mac:
source venv/bin/activate

# 4. 安装依赖
pip install -r requirements.txt

# 5. 启动应用
python app.py

# 6. 浏览器访问
# http://127.0.0.1:5000
```

---

## 📊 功能说明

### 1. 策略分析模块（支持 AI Agent）

上传包含模型分数和逾期标签的数据文件，系统自动：

- ✅ 计算 **KS 值**、**AUC**、**PSI** 等核心指标
- ✅ 执行 **等频分箱**，展示各分数段逾期率
- ✅ 调用 **大模型实时生成 AI 策略建议**
- ✅ 导出 **格式化 Excel 报告**
- 🤖 **【新增】多 Agent 协作分析**：数据分析师 + 建模评估师 + 策略调整师 三大 Agent 并行工作

**大模型配置（推荐免费方案）：**
| 模型 | 费用 | 说明 |
|---|---|---|
| **GLM-4-Flash**（主力）| **免费** | 智谱AI，数据分析够用 |
| **DeepSeek V3**（备用）| 1元/M输入 | GLM 限流时自动切换 |

> 💡 **完全不花钱**：智谱 API 注册即送无限免费额度

**使用 Agent 分析：**
在 `/api/analysis/run` 请求中添加 `agent_type` 参数：
```json
{
  "file_id": "xxx.xlsx",
  "target_col": "label",
  "score_col": "model_score",
  "agent_type": "all"
}
```
- `data`     → 数据分析师（数据质量 + 特征重要性）
- `model`    → 建模评估师（KS/AUC + 相关性分析）
- `strategy` → 策略调整师（阈值 + 串行/捞回策略）
- `all`      → 三 Agent 协作，整合综合报告

**支持的文件格式：** CSV、Excel (.xlsx/.xls)

### 2. 策略调整记录模块

记录每次策略调整的完整信息：

- 策略名称、调整日期、策略类型
- 调整内容、调整原因标签
- 调整前指标（KS/AUC/逾期率等）
- 预期目标、备注说明
- 关联分析任务

**功能特性：**
- 支持筛选（按类型、状态）和搜索
- 一键导出 Excel
- 与复盘模块联动

### 3. 策略复盘模块

评估策略调整的实际效果：

- 上传调整后的业务数据
- 自动对比调整前后核心指标
- AI 生成复盘结论（有效/无效/需观察）
- 支持人工标注和备注

---

## 🔧 技术栈

| 层级 | 技术 |
|------|------|
| 后端框架 | Flask 2.3+ |
| 数据库 | SQLite（开发）/ MySQL（生产） |
| ORM | SQLAlchemy |
| 数据分析 | pandas, numpy, scipy |
| Excel 导出 | openpyxl |
| 前端 | 原生 HTML5 + CSS3 + JavaScript |
| 图表 | Chart.js |

---

## 📋 数据格式要求

### 分析任务数据示例

```csv
user_id,model_score,overdue_m1,loan_amount
10001,0.85,1,5000
10002,0.42,0,3000
10003,0.73,1,8000
...
```

**必需字段：**
- `model_score`：模型预测分数（0-1 之间）
- `overdue_m1`：是否逾期（0/1 或 True/False）

**可选字段：**
- `user_id`：用户标识
- `loan_amount`：贷款金额
- 其他业务字段

---

## ⚙️ 配置说明

### 环境变量（可选）

```bash
# 数据库 URL（默认 SQLite）
DATABASE_URL=sqlite:///riskpilot.db

# 上传文件大小限制（默认 16MB）
MAX_CONTENT_LENGTH=16777216

# 调试模式（开发时开启）
FLASK_DEBUG=1

# ===== 大模型配置（重要！启用 AI 分析功能）=====
# 推荐使用智谱 GLM（免费）+ DeepSeek（备用），完全不花钱

# 智谱 AI（GLM-4-Flash）：免费，优先使用
# 注册地址：https://open.bigmodel.cn
GLM_API_KEY=your_zhipu_api_key_here
GLM_BASE_URL=https://open.bigmodel.cn/api/paas/v4
GLM_MODEL=glm-4-flash

# DeepSeek V3（备用）：1元/M输入，8元/M输出
# 注册地址：https://platform.deepseek.com
DS_API_KEY=your_deepseek_api_key_here
DS_BASE_URL=https://api.deepseek.com
DS_MODEL=deepseek-chat

# 旧版 OpenAI 配置（保留兼容）
LLM_API_KEY=sk-your-api-key-here
LLM_API_URL=https://api.openai.com/v1/chat/completions
LLM_MODEL=gpt-3.5-turbo
# 配置后可启用真实大模型进行实时策略分析
# 不配置则使用内置模拟模式

# OpenAI API Key（或其他兼容API）
LLM_API_KEY=sk-your-api-key-here

# API 地址（默认 OpenAI，可替换为其他兼容服务）
LLM_API_URL=https://api.openai.com/v1/chat/completions

# 模型名称（默认 gpt-3.5-turbo）
LLM_MODEL=gpt-3.5-turbo
```

### 阈值配置

在 `services/suggestion_service.py` 中可调整评估阈值：

```python
THRESHOLDS = {
    'ks_good': 0.35,      # KS 良好阈值
    'ks_warning': 0.25,   # KS 警戒阈值
    'auc_good': 0.75,     # AUC 良好阈值
    'psi_good': 0.1,      # PSI 正常阈值
    'psi_warning': 0.25,  # PSI 警戒阈值
}
```

---

## 🐛 常见问题

### Q1: 启动时报错 "No module named 'flask'"
**解决：** 确保已激活虚拟环境并安装依赖
```bash
venv\Scripts\activate
pip install -r requirements.txt
```

### Q2: 上传文件后分析失败
**解决：** 检查数据格式
- 确认包含目标列（如 `overdue_m1`）
- 确认分数列（如 `model_score`）为数值类型
- 确保没有空值或异常值

### Q3: 如何清空测试数据
**解决：** 删除数据库文件后重启
```bash
# Windows
del riskpilot.db

# Linux/Mac
rm riskpilot.db
```

---

## 📝 更新日志

### v1.0.0 (2026-04-13)
- ✅ 初始版本发布
- ✅ 策略分析模块（KS/AUC/PSI/分箱）
- ✅ 策略调整记录模块
- ✅ 策略复盘模块
- ✅ Excel 导出功能
- ✅ AI 建议生成
- 🤖 多 Agent 协作系统（GLM + DeepSeek 混合路由）

### 1.1 Agent 系统架构

```
用户请求（自然语言）
       ↓
  Orchestrator（任务调度）
       ↓  ↓  ↓
  ┌────────┐ ┌────────┐ ┌────────────┐
  │数据分析师│ │建模评估师│ │ 策略调整师  │
  └────────┘ └────────┘ └────────────┘
       ↓           ↓           ↓
  SkillRegistry（风控技能工具注册表）
  ┌──────────────────────────────────────┐
  │ load_data         │ 数据质量检查      │
  │ overdue_analysis  │ 逾期率分析       │
  │ model_correlation │ 多模型相关性      │
  │ bin_optimize      │ 智能分箱优化      │
  │ strategy_suggestion│ 策略建议        │
  │ feature_importance│ 特征重要性        │
  └──────────────────────────────────────┘
       ↓
  ModelRouter（混合路由）
  ┌────────┐ ┌────────┐
  │GLM免费 │ │DeepSeek │
  │(主)   │ │ (备)   │
  └────────┘ └────────┘
```

---

## 📄 开源协议

MIT License © 2026 RiskPilot Team

---

## 🤝 联系方式

如有问题或建议，欢迎交流讨论！

**祝你在 AI 创新赛中取得好成绩！** 🏆
