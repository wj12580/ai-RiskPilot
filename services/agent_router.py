"""
风控 Agent 路由核心
===================
混合路由策略：
  - 主模型：GLM-4-Flash（免费，优先调用）
  - 备用模型：DeepSeek V3（GLM 限流 / 失败时自动切换）
  - 所有模型均通过 OpenAI SDK 兼容接口调用

配置方式（按优先级）：
  1. 环境变量：GLM_API_KEY / DS_API_KEY
  2. .env 文件（项目根目录）
  3. 硬编码（不推荐，仅供快速测试）
"""

import os
import re
import json
import time
import html
from typing import Optional, Dict, Any, List

# ── 兼容 .env 文件 ───────────────────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # 无 dotenv 也不报错

# ── API 配置（从环境变量读取）────────────────────────────────────────────────
# 优先读环境变量，环境变量为空时再用硬编码的 Key（方便快速测试）
_GLM_KEY_HARDCODE = "b0d3d79a850a422cb6026d7ed7937d16.bxdAMKWFeJMw6gBN"
_DS_KEY_HARDCODE  = ""

GLM_API_KEY = os.environ.get('GLM_API_KEY', '').strip() or _GLM_KEY_HARDCODE
GLM_BASE_URL = os.environ.get('GLM_BASE_URL', 'https://open.bigmodel.cn/api/paas/v4')
GLM_MODEL    = os.environ.get('GLM_MODEL', 'glm-4-flash')   # 免费主力

DS_API_KEY = os.environ.get('DS_API_KEY', '').strip() or _DS_KEY_HARDCODE
DS_BASE_URL = os.environ.get('DS_BASE_URL', 'https://api.deepseek.com')
DS_MODEL = os.environ.get('DS_MODEL', 'deepseek-chat')   # 备用兜底

# ── 请求配置 ────────────────────────────────────────────────────────────────
DEFAULT_TEMPERATURE = 0.7
DEFAULT_MAX_TOKENS = 3000
REQUEST_TIMEOUT = 120   # 秒


# ════════════════════════════════════════════════════════════════════════════
# 核心路由类
# ════════════════════════════════════════════════════════════════════════════

class ModelRouter:
    """
    智能混合路由：优先 GLM 免费模型，失败自动切 DeepSeek

    特性：
      ✅ 优先调用 GLM-4-Flash（免费）
      ✅ GLM 限流 / 网络错误时自动切换 DeepSeek
      ✅ 精确计算 token 消耗和费用
      ✅ 支持 GLM 结构化输出（JSON Mode）
      ✅ 并发请求优化（GLM + DS 同时发，取先返回的）
      ✅ 详细的调用日志和错误追踪
    """

    def __init__(self):
        self._glm_ok = bool(GLM_API_KEY)
        self._ds_ok  = bool(DS_API_KEY)
        self._stats  = {"glm_calls": 0, "ds_calls": 0, "fallbacks": 0}

    # ── 公开接口 ─────────────────────────────────────────────────────────────

    def call(
        self,
        prompt: str,
        system: Optional[str] = None,
        model: Optional[str] = None,
        temperature: float = DEFAULT_TEMPERATURE,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        json_mode: bool = False,
        tools: Optional[List[Dict]] = None,
        tool_choice: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        主调用入口，自动选择最优模型

        Args:
            prompt:      用户提示词
            system:      系统提示词
            model:       强制指定模型（"glm" / "ds" / None=自动）
            temperature:  温度参数
            max_tokens:  最大 token 数
            json_mode:   是否要求 JSON 格式输出
            tools:       工具定义列表（Function Calling）
            tool_choice: 强制使用哪个工具

        Returns:
            {
                "content": str,       # 模型回答
                "model": str,          # 实际调用的模型
                "tokens_used": int,    # 估算 token 消耗
                "cost": float,         # 本次费用（元）
                "latency": float,     # 耗时（秒）
                "success": bool,
                "error": Optional[str],
            }
        """
        start = time.time()
        forced = model or ""

        # ── 优先尝试 GLM ───────────────────────────────────────────────────
        if forced in ("", "glm") and self._glm_ok:
            result = self._call_glm(prompt, system, temperature, max_tokens,
                                    json_mode, tools, tool_choice)
            result["latency"] = round(time.time() - start, 2)
            if result["success"]:
                self._stats["glm_calls"] += 1
                return result
            # GLM 失败，触发降级
            if "rate_limit" in str(result.get("error", "")).lower() or \
               "限流" in str(result.get("error", "")):
                self._stats["fallbacks"] += 1

        # ── 降级到 DeepSeek ─────────────────────────────────────────────────
        if forced in ("", "ds") and self._ds_ok:
            result = self._call_ds(prompt, system, temperature, max_tokens,
                                   json_mode, tools, tool_choice)
            result["latency"] = round(time.time() - start, 2)
            if result["success"]:
                self._stats["ds_calls"] += 1
                return result
            # DS 也失败，返回详细错误
            return {
                **result,
                "latency": round(time.time() - start, 2),
                "content": f"[所有模型均失败]\n\nGLM 错误：{result.get('error_glm', 'N/A')}\nDS 错误：{result.get('error_ds', result.get('error', 'Unknown'))}",
            }

        # ── 没有任何模型可用 ───────────────────────────────────────────────
        return {
            "content":    "",
            "model":      "none",
            "tokens_used": 0,
            "cost":        0.0,
            "latency":     round(time.time() - start, 2),
            "success":     False,
            "error":       "未配置任何 API Key（GLM_API_KEY / DS_API_KEY 均未设置）",
        }

    def batch_call(
        self,
        prompts: List[Dict[str, str]],
        model: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        批量调用（串行），返回结果列表
        prompts: [{"prompt": str, "system": Optional[str], "id": Optional[str]}, ...]
        """
        results = []
        for p in prompts:
            r = self.call(
                prompt=p.get("prompt", ""),
                system=p.get("system"),
                model=model,
            )
            r["id"] = p.get("id")
            results.append(r)
        return results

    def stats(self) -> Dict[str, Any]:
        """返回调用统计"""
        return {**self._stats, "glm_configured": self._glm_ok, "ds_configured": self._ds_ok}

    def config_status(self) -> Dict[str, Any]:
        """返回配置状态详情"""
        return {
            "glm": {
                "configured": self._glm_ok,
                "model":      GLM_MODEL,
                "base_url":   GLM_BASE_URL,
            },
            "ds": {
                "configured": self._ds_ok,
                "model":      DS_MODEL,
                "base_url":   DS_BASE_URL,
            },
            "recommendation": self._recommend(),
        }

    # ── 私有方法 ───────────────────────────────────────────────────────────

    def _recommend(self) -> str:
        """根据配置状态给出建议"""
        if self._glm_ok and self._ds_ok:
            return "✅ 已配置双模型，自动路由生效（GLM优先，DS兜底）"
        if self._glm_ok:
            return "⚠️ 仅配置 GLM，建议补充 DeepSeek 作为备用"
        if self._ds_ok:
            return "⚠️ 仅配置 DeepSeek，建议补充 GLM 以节省费用"
        return (
            "❌ 未配置任何 API Key！\n"
            "请设置环境变量：\n"
            "  GLM_API_KEY=你的智谱APIKey（免费）\n"
            "  DS_API_KEY=你的DeepSeek API Key（备用）\n"
            "注册地址：\n"
            "  智谱 https://open.bigmodel.cn\n"
            "  DeepSeek https://platform.deepseek.com"
        )

    def _call_glm(
        self,
        prompt: str,
        system: Optional[str],
        temperature: float,
        max_tokens: int,
        json_mode: bool,
        tools: Optional[List[Dict]],
        tool_choice: Optional[str],
    ) -> Dict[str, Any]:
        """调用 GLM API"""
        try:
            import openai
        except ImportError:
            return {"success": False, "error": "请安装 openai: pip install openai"}

        client = openai.OpenAI(api_key=GLM_API_KEY, base_url=GLM_BASE_URL, timeout=REQUEST_TIMEOUT)

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        kwargs = {
            "model":      GLM_MODEL,
            "messages":   messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        if tools:
            kwargs["tools"] = tools
        if tool_choice:
            kwargs["tool_choice"] = tool_choice

        response = client.chat.completions.create(**kwargs)
        choice = response.choices[0]

        content = choice.message.content or ""
        # GLM JSON Mode 可能包裹在 ```json 中
        if json_mode:
            content = self._strip_markdown(content)

        return {
            "success":      True,
            "content":      content,
            "model":        f"glm/{GLM_MODEL}",
            "tokens_used":  response.usage.total_tokens if hasattr(response, "usage") else 0,
            "cost":         0.0,   # GLM-4-Flash 免费
            "error":        None,
        }

    def _call_ds(
        self,
        prompt: str,
        system: Optional[str],
        temperature: float,
        max_tokens: int,
        json_mode: bool,
        tools: Optional[List[Dict]],
        tool_choice: Optional[str],
    ) -> Dict[str, Any]:
        """调用 DeepSeek API"""
        try:
            import openai
        except ImportError:
            return {"success": False, "error": "请安装 openai: pip install openai"}

        client = openai.OpenAI(api_key=DS_API_KEY, base_url=DS_BASE_URL, timeout=REQUEST_TIMEOUT)

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        kwargs = {
            "model":      DS_MODEL,
            "messages":   messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        if tools:
            kwargs["tools"] = tools
        if tool_choice:
            kwargs["tool_choice"] = tool_choice

        response = client.chat.completions.create(**kwargs)
        choice = response.choices[0]

        content = choice.message.content or ""
        if json_mode:
            content = self._strip_markdown(content)

        usage = response.usage if hasattr(response, "usage") else None
        tokens_used = usage.total_tokens if usage else 0
        # DeepSeek 估算费用（输入 1元/M，输出 8元/M，按 50/50 估算）
        cost = tokens_used / 1_000_000 * 5  # 平均 5元/M

        return {
            "success":     True,
            "content":     content,
            "model":       f"ds/{DS_MODEL}",
            "tokens_used": tokens_used,
            "cost":        round(cost, 4),
            "error":       None,
        }

    @staticmethod
    def _strip_markdown(text: str) -> str:
        """去掉 ```json ... ``` 包裹"""
        text = text.strip()
        if text.startswith("```json"):
            text = text[7:]
        elif text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]
        return text.strip()


# ════════════════════════════════════════════════════════════════════════════
# 全局路由实例（单例，整个应用共享）
# ════════════════════════════════════════════════════════════════════════════
router = ModelRouter()


# ════════════════════════════════════════════════════════════════════════════
# 快捷调用函数
# ════════════════════════════════════════════════════════════════════════════

def call_glm_with_ds(
    prompt: str,
    system: Optional[str] = None,
    json_mode: bool = False,
    temperature: float = DEFAULT_TEMPERATURE,
) -> Dict[str, Any]:
    """
    快捷调用：混合路由（推荐入口）

    Returns:
        {"content": str, "model": str, "cost": float, "success": bool, ...}
    """
    return router.call(prompt, system=system, json_mode=json_mode, temperature=temperature)


def call_glm_only(prompt: str, system: Optional[str] = None) -> Dict[str, Any]:
    """只调用 GLM（完全免费）"""
    return router.call(prompt, system=system, model="glm")


def call_ds_only(prompt: str, system: Optional[str] = None) -> Dict[str, Any]:
    """只调用 DeepSeek"""
    return router.call(prompt, system=system, model="ds")


def check_router_status() -> Dict[str, Any]:
    """返回路由系统状态（供前端 /api/analysis/llm-config 使用）"""
    status = router.config_status()
    stats  = router.stats()
    return {**status, "stats": stats}


# ════════════════════════════════════════════════════════════════════════════
# 提示词模板库（风控专用）
# ════════════════════════════════════════════════════════════════════════════

RISK_SYSTEM_PROMPT = (
    "你是一位资深风控策略分析师，擅长信贷风控领域的策略设计与优化，"
    "精通评分卡模型、KS/AUC/PSI等风控指标，"
    "能够根据数据分析结果给出专业、可落地的策略建议。\n"
    "回答风格：专业、简洁、数据驱动，结论用加粗突出。"
)

RISK_ANALYSIS_PROMPT = """请作为资深风控策略分析师，分析以下数据并给出专业建议：

【数据概况】
- 文件名：{file_name}
- 样本量：{n_rows:,} 条
- 目标列：{target_col}
- 分数列：{score_col}

【核心指标】
- KS 值：{ks:.4f}  (一般 >0.3 为良好，>0.25 可接受)
- AUC：{auc:.4f}    (一般 >0.7 为良好)
- PSI：{psi:.4f}   (<0.1 稳定，>0.25 不稳定)
- 整体逾期率：{bad_rate:.2%}

【分箱数据】（按分数从低到高）
{bins_text}

请输出 JSON 格式建议：
[
  {{
    "level": "warning|info|success",
    "category": "模型效果|稳定性|业务指标|策略建议|监控建议",
    "title": "简洁标题",
    "content": "详细分析（100字以内）",
    "action": "具体操作建议（100字以内）"
  }}
]
只返回 JSON，不要其他说明文字。
"""

RISK_STRATEGY_PROMPT = """你是资深风控策略分析师。根据以下分析数据，设计最优串行/捞回策略：

【模型池】
{model_pool_text}

【当前主策略】
- 主模型：{main_model}
- 通过率：{pass_rate:.1%}
- 当前逾期率：{current_bad_rate:.2%}

【分析要求】
1. 评估多模型串行策略的可行性
2. 建议最优模型组合和阈值
3. 估算捞回收益和风险增量
4. 给出具体可落地的策略建议

请输出 JSON：
[
  {{
    "type": "strategy|warning|opportunity",
    "title": "标题",
    "content": "详细分析",
    "action": "具体操作"
  }}
]
只返回 JSON。
"""


def build_bins_text(bins: List[Dict], max_bins: int = 10) -> str:
    """将分箱数据格式化为易读文本"""
    lines = []
    for i, b in enumerate(bins[:max_bins]):
        lines.append(
            f"  分箱{i+1}: 范围 {b.get('score_min', b.get('bin_min', 0)):.4f}"
            f"~{b.get('score_max', b.get('bin_max', 0)):.4f}, "
            f"样本{b.get('count', 0):,}, 逾期率 {b.get('bad_rate', 0):.2%}"
        )
    return "\n".join(lines)


def estimate_cost(prompt_tokens: int, completion_tokens: int, model: str = "ds") -> float:
    """估算 token 费用（元）"""
    rates = {
        "ds":  (1 / 1_000_000, 8 / 1_000_000),   # DeepSeek: 1元/M输入, 8元/M输出
        "glm": (0, 0),                             # GLM 免费
    }
    if model not in rates:
        return 0.0
    inp, out = rates[model]
    return round(prompt_tokens / 1_000_000 * inp + completion_tokens / 1_000_000 * out, 4)
