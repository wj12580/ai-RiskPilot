"""
风控 Agent 路由核心
统一模型顺序：GPT → GLM → DeepSeek
"""

import os
import time
from typing import Optional, Dict, Any, List

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

_GPT_KEY_HARDCODE = "sk-5c0s1ajxHKoow8e2ozA0khQqHoPMZwmsJqD0S9wxczYsJgqZ"
_GLM_KEY_HARDCODE = "b0d3d79a850a422cb6026d7ed7937d16.bxdAMKWFeJMw6gBN"
_DS_KEY_HARDCODE = ""

GPT_API_KEY = os.environ.get('GPT_API_KEY', '').strip() or _GPT_KEY_HARDCODE
GPT_BASE_URL = os.environ.get('GPT_BASE_URL', 'https://www.packyapi.com/v1')
GPT_MODEL = os.environ.get('GPT_MODEL', 'gpt-5.4')

GLM_API_KEY = os.environ.get('GLM_API_KEY', '').strip() or _GLM_KEY_HARDCODE
GLM_BASE_URL = os.environ.get('GLM_BASE_URL', 'https://open.bigmodel.cn/api/paas/v4')
GLM_MODEL = os.environ.get('GLM_MODEL', 'glm-4-flash')

DS_API_KEY = os.environ.get('DS_API_KEY', '').strip() or _DS_KEY_HARDCODE
DS_BASE_URL = os.environ.get('DS_BASE_URL', 'https://api.deepseek.com')
DS_MODEL = os.environ.get('DS_MODEL', 'deepseek-chat')

DEFAULT_TEMPERATURE = 0.7
DEFAULT_MAX_TOKENS = 3000
REQUEST_TIMEOUT = int(os.environ.get("LLM_REQUEST_TIMEOUT", "30"))


class ModelRouter:
    """统一模型路由：GPT 优先，失败后回退 GLM，再回退 DeepSeek。"""

    def __init__(self):
        self._gpt_ok = bool(GPT_API_KEY)
        self._glm_ok = bool(GLM_API_KEY)
        self._ds_ok = bool(DS_API_KEY)
        self._stats = {"gpt_calls": 0, "glm_calls": 0, "ds_calls": 0, "fallbacks": 0}

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
        start = time.time()
        forced = (model or "").lower().strip()

        glm_error = "Skipped"
        gpt_error = "Skipped"
        ds_error = "Skipped"

        # 默认顺序：GLM -> GPT -> DS
        provider_order = ["glm", "gpt", "ds"]
        if forced == "gpt":
            provider_order = ["gpt"]
        elif forced == "ds":
            provider_order = ["ds"]
        elif forced == "glm":
            provider_order = ["glm"]

        for provider in provider_order:
            if provider == "glm":
                if not self._glm_ok:
                    continue
                result = self._call_glm(prompt, system, temperature, max_tokens, json_mode, tools, tool_choice)
                result["latency"] = round(time.time() - start, 2)
                if result.get("success"):
                    self._stats["glm_calls"] += 1
                    return result
                self._stats["fallbacks"] += 1
                glm_error = result.get("error", "Unknown")
            elif provider == "gpt":
                if not self._gpt_ok:
                    continue
                result = self._call_gpt(prompt, system, temperature, max_tokens, json_mode, tools, tool_choice)
                result["latency"] = round(time.time() - start, 2)
                if result.get("success"):
                    self._stats["gpt_calls"] += 1
                    return result
                self._stats["fallbacks"] += 1
                gpt_error = result.get("error", "Unknown")
            elif provider == "ds":
                if not self._ds_ok:
                    continue
                result = self._call_ds(prompt, system, temperature, max_tokens, json_mode, tools, tool_choice)
                result["latency"] = round(time.time() - start, 2)
                if result.get("success"):
                    self._stats["ds_calls"] += 1
                    return result
                ds_error = result.get("error", "Unknown")

        return {
            "content": f"[all providers failed]\n\nGLM error: {glm_error}\nGPT error: {gpt_error}\nDS error: {ds_error}",
            "model": "none",
            "tokens_used": 0,
            "cost": 0.0,
            "latency": round(time.time() - start, 2),
            "success": False,
            "error": "No available provider succeeded",
        }

    def batch_call(self, prompts: List[Dict[str, str]], model: Optional[str] = None) -> List[Dict[str, Any]]:
        results = []
        for p in prompts:
            r = self.call(prompt=p.get("prompt", ""), system=p.get("system"), model=model)
            r["id"] = p.get("id")
            results.append(r)
        return results

    def stats(self) -> Dict[str, Any]:
        return {
            **self._stats,
            "gpt_configured": self._gpt_ok,
            "glm_configured": self._glm_ok,
            "ds_configured": self._ds_ok,
        }

    def config_status(self) -> Dict[str, Any]:
        return {
            "glm": {
                "configured": self._glm_ok,
                "model": GLM_MODEL,
                "base_url": GLM_BASE_URL,
                "priority": 1,
            },
            "gpt": {
                "configured": self._gpt_ok,
                "model": GPT_MODEL,
                "base_url": GPT_BASE_URL,
                "priority": 2,
                "warning": "当前 GPT 默认渠道若为 packyapi，可能出现 gpt-4o-mini distributor 不可用问题",
            },
            "ds": {
                "configured": self._ds_ok,
                "model": DS_MODEL,
                "base_url": DS_BASE_URL,
                "priority": 3,
            },
            "recommendation": self._recommend(),
        }

    def _recommend(self) -> str:
        if self._glm_ok and self._gpt_ok:
            return "✅ 已配置 GLM + GPT，自动路由生效（GLM优先，GPT兜底）"
        if self._glm_ok and self._ds_ok:
            return "✅ 已配置 GLM + DeepSeek，自动路由生效（GLM优先，DS兜底）"
        if self._glm_ok:
            return "ℹ️ 仅配置 GLM，建议补充 GPT 作为超时兜底"
        if self._gpt_ok:
            return "⚠️ 仅配置 GPT，当前项目建议改为 GLM 主用，GPT 备用"
        if self._ds_ok:
            return "ℹ️ 仅配置 DeepSeek，建议补充 GLM"
        return "❌ 未配置任何可用 API Key"

    def _call_gpt(self, prompt: str, system: Optional[str], temperature: float, max_tokens: int, json_mode: bool, tools: Optional[List[Dict]], tool_choice: Optional[str]) -> Dict[str, Any]:
        try:
            import openai
        except ImportError:
            return self._call_openai_compatible_http(
                provider="gpt",
                api_key=GPT_API_KEY,
                base_url=GPT_BASE_URL,
                model_name=GPT_MODEL,
                prompt=prompt,
                system=system,
                temperature=temperature,
                max_tokens=max_tokens,
                json_mode=json_mode,
                tools=tools,
                tool_choice=tool_choice,
            )

        try:
            client = openai.OpenAI(api_key=GPT_API_KEY, base_url=GPT_BASE_URL, timeout=REQUEST_TIMEOUT)
            messages = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": prompt})

            kwargs = {
                "model": GPT_MODEL,
                "messages": messages,
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
            return {
                "success": True,
                "content": content,
                "model": f"gpt/{GPT_MODEL}",
                "tokens_used": response.usage.total_tokens if hasattr(response, "usage") else 0,
                "cost": 0.0,
                "error": None,
            }
        except Exception as e:
            return {"success": False, "error": str(e), "model": f"gpt/{GPT_MODEL}"}

    def _call_glm(self, prompt: str, system: Optional[str], temperature: float, max_tokens: int, json_mode: bool, tools: Optional[List[Dict]], tool_choice: Optional[str]) -> Dict[str, Any]:
        try:
            import openai
        except ImportError:
            return self._call_openai_compatible_http(
                provider="glm",
                api_key=GLM_API_KEY,
                base_url=GLM_BASE_URL,
                model_name=GLM_MODEL,
                prompt=prompt,
                system=system,
                temperature=temperature,
                max_tokens=max_tokens,
                json_mode=json_mode,
                tools=tools,
                tool_choice=tool_choice,
            )

        try:
            client = openai.OpenAI(api_key=GLM_API_KEY, base_url=GLM_BASE_URL, timeout=REQUEST_TIMEOUT)
            messages = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": prompt})

            kwargs = {
                "model": GLM_MODEL,
                "messages": messages,
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
            return {
                "success": True,
                "content": content,
                "model": f"glm/{GLM_MODEL}",
                "tokens_used": response.usage.total_tokens if hasattr(response, "usage") else 0,
                "cost": 0.0,
                "error": None,
            }
        except Exception as e:
            return {"success": False, "error": str(e), "model": f"glm/{GLM_MODEL}"}

    def _call_ds(self, prompt: str, system: Optional[str], temperature: float, max_tokens: int, json_mode: bool, tools: Optional[List[Dict]], tool_choice: Optional[str]) -> Dict[str, Any]:
        try:
            import openai
        except ImportError:
            return self._call_openai_compatible_http(
                provider="ds",
                api_key=DS_API_KEY,
                base_url=DS_BASE_URL,
                model_name=DS_MODEL,
                prompt=prompt,
                system=system,
                temperature=temperature,
                max_tokens=max_tokens,
                json_mode=json_mode,
                tools=tools,
                tool_choice=tool_choice,
            )

        try:
            client = openai.OpenAI(api_key=DS_API_KEY, base_url=DS_BASE_URL, timeout=REQUEST_TIMEOUT)
            messages = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": prompt})

            kwargs = {
                "model": DS_MODEL,
                "messages": messages,
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
            cost = tokens_used / 1_000_000 * 5
            return {
                "success": True,
                "content": content,
                "model": f"ds/{DS_MODEL}",
                "tokens_used": tokens_used,
                "cost": round(cost, 4),
                "error": None,
            }
        except Exception as e:
            return {"success": False, "error": str(e), "model": f"ds/{DS_MODEL}"}

    @staticmethod
    def _strip_markdown(text: str) -> str:
        text = text.strip()
        if text.startswith("```json"):
            text = text[7:]
        elif text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]
        return text.strip()

    def _call_openai_compatible_http(
        self,
        provider: str,
        api_key: str,
        base_url: str,
        model_name: str,
        prompt: str,
        system: Optional[str],
        temperature: float,
        max_tokens: int,
        json_mode: bool,
        tools: Optional[List[Dict]],
        tool_choice: Optional[str],
    ) -> Dict[str, Any]:
        """
        在 openai SDK 不可用时，走 OpenAI 兼容 HTTP 协议调用，避免依赖导致全量失败。
        """
        try:
            import requests
        except ImportError:
            return {"success": False, "error": "请安装 requests: pip install requests", "model": f"{provider}/{model_name}"}

        try:
            messages = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": prompt})

            payload: Dict[str, Any] = {
                "model": model_name,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
            if json_mode:
                payload["response_format"] = {"type": "json_object"}
            if tools:
                payload["tools"] = tools
            if tool_choice:
                payload["tool_choice"] = tool_choice

            url = f"{base_url.rstrip('/')}/chat/completions"
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            }
            resp = requests.post(url, headers=headers, json=payload, timeout=REQUEST_TIMEOUT)
            if resp.status_code >= 400:
                error_msg = f"HTTP {resp.status_code}: {resp.text[:300]}"
                return {"success": False, "error": error_msg, "model": f"{provider}/{model_name}"}

            body = resp.json()
            choices = body.get("choices", []) if isinstance(body, dict) else []
            if not choices:
                return {"success": False, "error": "empty choices", "model": f"{provider}/{model_name}"}

            message = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
            content = message.get("content", "")
            if isinstance(content, list):
                # 兼容多段 content 结构
                parts = []
                for part in content:
                    if isinstance(part, dict):
                        txt = part.get("text")
                        if txt:
                            parts.append(str(txt))
                content = "\n".join(parts)
            content = str(content or "")
            if json_mode:
                content = self._strip_markdown(content)

            usage = body.get("usage", {}) if isinstance(body, dict) else {}
            tokens_used = int(usage.get("total_tokens") or 0)
            if tokens_used <= 0:
                try:
                    tokens_used = int((usage.get("prompt_tokens") or 0) + (usage.get("completion_tokens") or 0))
                except Exception:
                    tokens_used = 0

            cost = 0.0
            if provider == "ds":
                cost = round(tokens_used / 1_000_000 * 5, 4)

            return {
                "success": True,
                "content": content,
                "model": f"{provider}/{model_name}",
                "tokens_used": tokens_used,
                "cost": cost,
                "error": None,
            }
        except Exception as e:
            return {"success": False, "error": str(e), "model": f"{provider}/{model_name}"}


router = ModelRouter()


def call_glm_with_ds(
    prompt: str,
    system: Optional[str] = None,
    json_mode: bool = False,
    temperature: float = DEFAULT_TEMPERATURE,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> Dict[str, Any]:
    return router.call(
        prompt,
        system=system,
        json_mode=json_mode,
        temperature=temperature,
        max_tokens=max_tokens,
    )


def call_glm_only(prompt: str, system: Optional[str] = None) -> Dict[str, Any]:
    return router.call(prompt, system=system, model="glm")


def call_gpt_only(prompt: str, system: Optional[str] = None) -> Dict[str, Any]:
    return router.call(prompt, system=system, model="gpt")


def call_ds_only(prompt: str, system: Optional[str] = None) -> Dict[str, Any]:
    return router.call(prompt, system=system, model="ds")


def check_router_status() -> Dict[str, Any]:
    status = router.config_status()
    stats = router.stats()
    return {**status, "stats": stats}


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
    lines = []
    for i, b in enumerate(bins[:max_bins]):
        lines.append(
            f"  分箱{i+1}: 范围 {b.get('score_min', b.get('bin_min', 0)):.4f}"
            f"~{b.get('score_max', b.get('bin_max', 0)):.4f}, "
            f"样本{b.get('count', 0):,}, 逾期率 {b.get('bad_rate', 0):.2%}"
        )
    return "\n".join(lines)


def estimate_cost(prompt_tokens: int, completion_tokens: int, model: str = "ds") -> float:
    rates = {
        "ds": (1 / 1_000_000, 8 / 1_000_000),
        "glm": (0, 0),
        "gpt": (0, 0),
    }
    if model not in rates:
        return 0.0
    inp, out = rates[model]
    return round(prompt_tokens / 1_000_000 * inp + completion_tokens / 1_000_000 * out, 4)
