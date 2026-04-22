"""
风控 Agent 调度器
===================
整合 Skill 注册表 + 混合路由，构建完整的多 Agent 协作系统。

Agent 类型：
  - data       → 数据分析师（数据质量、特征重要性）
  - model      → 建模评估师（模型评估、相关性分析）
  - strategy   → 策略调整师（分箱优化、策略建议）
  - all        → 综合分析（调用所有 Agent，整合结论）

与 Hermes Agent 的兼容层：
  - SkillRegistry  → Hermes Skills 规范
  - HermesAgent    → Agent 基类标准接口
  - Orchestrator   → Hermes Workflow 规范
"""

import json
import time
import os
from typing import Dict, Any, List, Optional

from services.agent_router import (
    router, call_glm_with_ds,
    RISK_SYSTEM_PROMPT, RISK_ANALYSIS_PROMPT, RISK_STRATEGY_PROMPT,
    build_bins_text,
)
from services.agent_skills import registry, SkillRegistry


# ════════════════════════════════════════════════════════════════════════════════
# Agent 定义
# ════════════════════════════════════════════════════════════════════════════════

class HermesCompatibleAgent:
    """
    Agent 基类（兼容 Hermes Agent 规范）

    标准接口：
      - think(task)     → 思考 + 调用工具 + 返回结论
      - name            → Agent 名称
      - role            → Agent 角色描述
      - system_prompt   → 系统提示词
      - available_tools → 可用工具列表
    """

    def __init__(
        self,
        name: str,
        role: str,
        tools: List[Dict],
        system_prompt: str,
        max_turns: int = 5,
    ):
        self.name = name
        self.role = role
        self.available_tools = tools
        self.system_prompt = system_prompt
        self.max_turns = max_turns
        self.history: List[Dict] = []
        self.call_log: List[Dict] = []  # 工具调用记录

    def think(
        self,
        task: str,
        context: Optional[Dict] = None,
    ) -> Dict[str, Any]:
        """
        Agent 思考主循环：
          1. 构建提示词（包含工具定义）
          2. 调用 LLM（大模型决定调用哪些工具）
          3. 执行工具，返回结果
          4. 重复直到得出结论
        """
        self.history = []
        self.call_log = []

        user_msg = task
        if context:
            context_str = "\n".join([f"- {k}: {v}" for k, v in context.items()])
            user_msg = f"{task}\n\n【上下文信息】\n{context_str}"

        self.history.append({"role": "user", "content": user_msg})

        for turn in range(self.max_turns):
            # ── 调用 LLM ───────────────────────────────────────────────────
            messages = [
                {"role": "system", "content": self.system_prompt},
                *self.history,
            ]

            response = call_glm_with_ds(
                prompt="",  # messages 里已有内容
                system="\n".join([m["content"] if m["role"] == "system" else
                                 f"**{'用户' if m['role']=='user' else '助手'}**: {m['content']}"
                                 for m in messages]),
                json_mode=False,
            )

            if not response["success"]:
                return {
                    "agent":   self.name,
                    "success": False,
                    "error":   response.get("error", "LLM调用失败"),
                    "turns":   turn + 1,
                }

            content = response["content"]
            self.history.append({"role": "assistant", "content": content})

            # ── 检查是否需要调用工具 ──────────────────────────────────────
            tool_result = self._maybe_call_tools(content)

            if tool_result is None:
                # 无需工具，返回最终结论
                return {
                    "agent":       self.name,
                    "success":     True,
                    "conclusion":  content,
                    "turns":       turn + 1,
                    "tools_used":  [log["tool"] for log in self.call_log],
                    "tool_logs":   self.call_log,
                    "model":       response.get("model", "unknown"),
                    "latency":     response.get("latency", 0),
                }

            # ── 工具执行结果回传 ──────────────────────────────────────────
            tool_summary = self._summarize_tool_result(tool_result)
            self.history.append({
                "role":    "user",
                "content": f"【工具执行结果】\n{tool_summary}\n\n请根据以上结果继续分析。",
            })

        # 达到最大轮次
        return {
            "agent":   self.name,
            "success": True,
            "warning": "达到最大循环次数，结论可能不完整",
            "conclusion": content,
            "turns":   self.max_turns,
            "tools_used": [log["tool"] for log in self.call_log],
            "tool_logs":  self.call_log,
        }

    def _maybe_call_tools(self, llm_response: str) -> Optional[List[Dict]]:
        """
        从 LLM 响应中解析工具调用指令并执行
        支持 JSON 格式的工具调用描述
        """
        # 尝试解析 JSON 工具调用
        try:
            # 尝试从响应中提取 JSON 块
            import re
            json_blocks = re.findall(r'\{[^{}]*"(tool|action|fn|function)"[^{}]*\}', llm_response, re.DOTALL)
            calls = []

            for block_str in json_blocks:
                # 找到完整的 JSON 对象
                for match in re.finditer(r'\{[^{}]*\}', llm_response, re.DOTALL):
                    try:
                        obj = json.loads(match.group())
                        if "tool" in obj or "action" in obj or "fn" in obj:
                            tool_name = obj.get("tool") or obj.get("action") or obj.get("fn")
                            if tool_name in [t["function"]["name"] for t in self.available_tools]:
                                calls.append(obj)
                    except json.JSONDecodeError:
                        continue

            if not calls:
                return None

            results = []
            for call in calls:
                tool_name = call.get("tool") or call.get("action") or call.get("fn")
                args = call.get("args", call.get("arguments", {}))
                result = registry.invoke(tool_name, **args)
                results.append({
                    "tool":  tool_name,
                    "args":  args,
                    "result": result,
                })
                self.call_log.append({"tool": tool_name, "args": args, "success": result["success"]})

            return results

        except Exception:
            return None

    def _summarize_tool_result(self, results: List[Dict]) -> str:
        """将工具执行结果格式化为可读文本"""
        lines = []
        for r in results:
            lines.append(f"🔧 工具 {r['tool']} 执行{'✅ 成功' if r['result']['success'] else '❌ 失败'}:")
            if r["result"]["success"]:
                result_data = r["result"]["result"]
                # 截断大结果避免 token 爆炸
                result_str = json.dumps(result_data, ensure_ascii=False)
                if len(result_str) > 2000:
                    result_str = result_str[:2000] + "...(已截断)"
                lines.append(result_str)
            else:
                lines.append(f"错误：{r['result']['error']}")
            lines.append("")
        return "\n".join(lines)


# ════════════════════════════════════════════════════════════════════════════════
# 专职 Agent 工厂
# ════════════════════════════════════════════════════════════════════════════════

def create_data_agent() -> HermesCompatibleAgent:
    """数据分析师 Agent"""
    return HermesCompatibleAgent(
        name="数据分析师",
        role="专注数据质量检查、特征分布分析、逾期率统计、特征重要性计算",
        tools=registry.list_tools(),
        system_prompt=(
            RISK_SYSTEM_PROMPT + "\n\n"
            "你是一个专业的数据分析师 Agent。\n"
            "收到分析任务后：\n"
            "1. 先用 load_data 了解数据概况\n"
            "2. 用 overdue_analysis 做逾期率分析\n"
            "3. 用 feature_importance 识别重要特征\n"
        ),
    )


def create_model_agent() -> HermesCompatibleAgent:
    """建模评估师 Agent"""
    return HermesCompatibleAgent(
        name="建模评估师",
        role="专注模型性能评估（KS/AUC/PSI）、模型相关性分析、模型组合策略设计",
        tools=registry.list_tools(),
        system_prompt=(
            RISK_SYSTEM_PROMPT + "\n\n"
            "你是一个专业的建模评估 Agent。\n"
            "收到评估任务后：\n"
            "1. overdue_analysis 评估单个模型效果\n"
            "2. 用 bin_optimize 分析关键特征分箱，分箱后加首逾和累计首逾并加上数据条涂色"
            "3. 分析模型分时候你可以分析每个模型的ROC对比曲线，Spearman模型相关性热力图，聚类树状图，模型互补性矩阵和分数分布图等等  \n"
            "4. 给出模型组合和阈值建议\n"
        ),
    )


def create_strategy_agent() -> HermesCompatibleAgent:
    """策略调整师 Agent"""
    return HermesCompatibleAgent(
        name="策略调整师",
        role="专注风控策略设计、阈值调整、串行/捞回规则制定、多模型组合策略",
        tools=registry.list_tools(),
        system_prompt=(
            RISK_SYSTEM_PROMPT + "\n\n"
            "你是一个专业的策略分析师 Agent。\n"
            "收到策略设计任务后：\n"
            "1. 先了解数据和分析结果\n"
            "2. 结合多模型分析设计串行/捞回方案\n"
            "3. 给出具体可落地的阈值和通过率建议\n"
            "4. 用 strategy_suggestion 生成专业策略建议\n"
        ),
    )


# ════════════════════════════════════════════════════════════════════════════════
# 主调度器（Orchestrator）
# ════════════════════════════════════════════════════════════════════════════════

class Orchestrator:
    """
    风控 Agent 调度器

    使用方式：
        orch = Orchestrator()
        result = orch.run(
            user_request="分析在贷笔数对首逾的影响",
            file_path="F:/data.xlsx",
            file_type="xlsx",
            agent_type="all",     # data / model / strategy / all
            target_col="label",
            score_col="model_score",
            n_bins=10,
        )
    """

    def __init__(self):
        self.data_agent     = create_data_agent()
        self.model_agent    = create_model_agent()
        self.strategy_agent = create_strategy_agent()
        self.router_stats   = router.stats()

    def run(
        self,
        user_request: str,
        file_path: str,
        file_type: str = "xlsx",
        agent_type: str = "all",
        target_col: str = "",
        score_col: str = "",
        n_bins: int = 10,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        主入口：调度 Agent 完成分析任务

        Args:
            user_request: 用户的需求描述（自然语言）
            file_path:    数据文件路径
            file_type:    文件类型（xlsx / csv）
            agent_type:   使用的 Agent 类型
                          "data"     → 只用数据分析师
                          "model"    → 只用建模评估师
                          "strategy" → 只用策略调整师
                          "all"      → 依次调用三个 Agent，整合结论
            target_col:   目标列名（label）
            score_col:    分数列名
            n_bins:       分箱数

        Returns:
            {
                "success":      bool,
                "agent_type":   str,
                "data_result":  {...},   # 数据分析结果
                "model_result": {...},   # 模型评估结果
                "strategy_result": {...}, # 策略建议结果
                "final_report": str,    # 最终综合报告
                "tool_logs":   [...],   # 工具调用记录
                "router_stats": {...},  # 路由统计
                "cost":        float,   # 本次费用
                "total_time":  float,   # 总耗时（秒）
            }
        """
        start_time = time.time()

        # ── 通用上下文 ───────────────────────────────────────────────────
        context = {
            "file_path":   file_path,
            "file_type":   file_type,
            "target_col":  target_col,
            "score_col":   score_col,
            "n_bins":      str(n_bins),
            "分析类型":    agent_type,
        }

        # ── 通用分析（所有 Agent 都需要先做）────────────────────────────────
        # 加载数据 + 逾期率分析
        data_result = self.data_agent.think(
            task=(
                f"用户需求：{user_request}\n\n"
                "请先加载数据，然后对目标列进行逾期率分析。"
                f"目标列：{target_col}，分数列：{score_col}，分箱数：{n_bins}"
            ),
            context=context,
        )

        model_result = {}
        strategy_result = {}

        # ── 按类型调度 ─────────────────────────────────────────────────────
        if agent_type in ("data", "all"):
            # 数据分析已有，直接用
            pass

        if agent_type in ("model", "all"):
            model_result = self.model_agent.think(
                task=(
                    f"用户需求：{user_request}\n\n"
                    "请分析多模型的相关性和各自效果，给出模型组合建议。"
                    f"目标列：{target_col}，自动识别所有分数列。"
                ),
                context=context,
            )

        if agent_type in ("strategy", "all"):
            strategy_result = self.strategy_agent.think(
                task=(
                    f"用户需求：{user_request}\n\n"
                    "请基于分析结果设计具体可落地的风控策略。"
                    "给出阈值、通过率、逾期率预期等具体数字。"
                ),
                context=context,
            )

        # ── 整合报告 ─────────────────────────────────────────────────────
        final_report = self._build_report(user_request, data_result, model_result, strategy_result)

        total_time = time.time() - start_time
        all_logs = (
            data_result.get("tool_logs", []) +
            model_result.get("tool_logs", []) +
            strategy_result.get("tool_logs", [])
        )

        return {
            "success":        True,
            "agent_type":     agent_type,
            "data_result":    data_result,
            "model_result":   model_result,
            "strategy_result": strategy_result,
            "final_report":   final_report,
            "tool_logs":      all_logs,
            "router_stats":   router.stats(),
            "cost":           sum(
                r.get("cost", 0) for r in [data_result, model_result, strategy_result]
                if r.get("success")
            ),
            "total_time":     round(total_time, 2),
        }

    def _build_report(
        self,
        user_request: str,
        data_result: Dict,
        model_result: Dict,
        strategy_result: Dict,
    ) -> str:
        """整合各 Agent 结论，生成综合报告"""
        sections = [f"## 📊 风控策略分析报告\n**需求：** {user_request}\n"]

        # 数据分析
        if data_result.get("success"):
            sections.append("### 🔍 数据分析结论\n")
            conclusion = data_result.get("conclusion", "")
            sections.append(conclusion[:1000] if conclusion else "数据分析完成。")

        # 模型评估
        if model_result.get("success"):
            sections.append("\n### 📈 模型评估结论\n")
            conclusion = model_result.get("conclusion", "")
            sections.append(conclusion[:1000] if conclusion else "模型评估完成。")

        # 策略建议
        if strategy_result.get("success"):
            sections.append("\n### 🎯 策略建议\n")
            conclusion = strategy_result.get("conclusion", "")
            sections.append(conclusion[:1000] if conclusion else "策略建议完成。")

        sections.append(f"\n---\n*报告生成耗时 {time.time():.0f}s，由 RiskPilot Agent 系统自动生成*")
        return "\n".join(sections)


# ════════════════════════════════════════════════════════════════════════════════
# 快捷入口（Flask 路由直接调用）
# ════════════════════════════════════════════════════════════════════════════════

def run_agent_analysis(
    user_request: str,
    file_path: str,
    file_type: str = "xlsx",
    agent_type: str = "all",
    target_col: str = "label",
    score_col: str = "",
    n_bins: int = 10,
) -> Dict[str, Any]:
    """
    快捷入口：供 Flask 路由直接调用
    等价于 Orchestrator().run(...)
    """
    orch = Orchestrator()
    return orch.run(
        user_request=user_request,
        file_path=file_path,
        file_type=file_type,
        agent_type=agent_type,
        target_col=target_col,
        score_col=score_col,
        n_bins=n_bins,
    )
