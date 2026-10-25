"""
LLM 自愈模块（阶段3.3 补充）
- OpenAI 兼容接口，支持本地 Qwen-VL 等
- 可选开关：配置 data/llm_config.json 启用
- 回放失败且纠错库未命中时，向 LLM 请求纠正建议
"""
import json
import os
import urllib.request
from pathlib import Path

import paths
from logger import get_logger

log = get_logger("llm_heal")

CONFIG_FILE = "data/llm_config.json"


class LLMHealer:
    def __init__(self):
        self._config = self._load_config()

    def _load_config(self) -> dict:
        p = paths.BASE_DIR / CONFIG_FILE
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {}

    @property
    def enabled(self) -> bool:
        return bool(self._config.get("api_base") and self._config.get("api_key"))

    def set_config(self, config: dict):
        self._config = config
        p = paths.BASE_DIR / CONFIG_FILE
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")

    def get_config(self) -> dict:
        return self._config

    def plan_visual(self, goal: str, image_bytes: bytes) -> dict | None:
        """Ask an OpenAI-compatible vision model for a structured next action."""
        if not self.enabled:
            return None
        import base64
        payload = {"model": self._config.get("model", "gpt-4o-mini"), "temperature": 0.1,
                   "max_tokens": 500, "messages": [{"role": "system", "content": "Return JSON only with action, target, x, y, text, expected."},
                   {"role": "user", "content": [{"type": "text", "text": goal}, {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(image_bytes).decode()}}]}]}
        req = urllib.request.Request(self._config["api_base"].rstrip("/") + "/chat/completions", method="POST", headers={"Content-Type":"application/json", "Authorization":"Bearer " + self._config["api_key"]}, data=json.dumps(payload).encode())
        try:
            content = json.loads(urllib.request.urlopen(req, timeout=20).read())["choices"][0]["message"]["content"]
            if "```" in content: content = content.split("```", 2)[1].removeprefix("json").strip()
            return json.loads(content)
        except Exception as exc:
            log.warning("视觉规划失败: %s", exc); return None
    def heal(self, failed_step: dict, context: list, error: str = "") -> dict | None:
        if not self.enabled:
            return None
        from services.network_guard import outbound_allowed
        if not outbound_allowed(paths.BASE_DIR, self._config["api_base"]):
            log.warning("网络白名单拦截 LLM 调用")
            return None
        api_base = self._config["api_base"].rstrip("/")
        api_key = self._config["api_key"]
        model = self._config.get("model", "gpt-4o-mini")

        prompt = self._build_prompt(failed_step, context, error)
        try:
            from services.memory_store import context as memory_context
            extra = memory_context(paths.BASE_DIR)
            if extra:
                prompt = extra + "\n" + prompt
        except Exception:
            pass
        try:
            req = urllib.request.Request(
                f"{api_base}/chat/completions",
                method="POST",
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_key}",
                },
                data=json.dumps({
                    "model": model,
                    "messages": [
                        {"role": "system", "content": "你是 RPA 自愈助手。用户会给你一个失败的操作步骤，"
                         "请分析失败原因并给出纠正后的操作步骤。返回 JSON 格式。"},
                        {"role": "user", "content": prompt}
                    ],
                    "temperature": 0.3,
                    "max_tokens": 500,
                }).encode("utf-8")
            )
            resp = urllib.request.urlopen(req, timeout=15)
            data = json.loads(resp.read())
            content = data["choices"][0]["message"]["content"]
            # 尝试提取 JSON
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0]
            elif "```" in content:
                content = content.split("```")[1].split("```")[0]
            return json.loads(content.strip())
        except Exception as e:
            log.warning("LLM 自愈请求失败: %s", e)
            return None

    def ask_json(self, prompt: str) -> dict:
        """通用 JSON 问答(意图分析等复用);失败抛异常,由调用方降级。"""
        if not self.enabled:
            raise RuntimeError("llm disabled")
        req = urllib.request.Request(
            f"{self._config['api_base'].rstrip('/')}/chat/completions",
            method="POST",
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {self._config['api_key']}"},
            data=json.dumps({"model": self._config.get("model", "gpt-4o-mini"),
                             "messages": [{"role": "system", "content": "只返回 JSON,不要解释。"},
                                          {"role": "user", "content": prompt}],
                             "temperature": 0.2, "max_tokens": 600}).encode("utf-8"))
        content = json.loads(urllib.request.urlopen(req, timeout=15).read())["choices"][0]["message"]["content"]
        if "```" in content:
            content = content.split("```", 2)[1].removeprefix("json").strip()
        return json.loads(content)

    def _build_prompt(self, failed_step: dict, context: list, error: str) -> str:
        lines = ["请分析以下 RPA 操作失败并给出纠正步骤。\n"]
        lines.append(f"失败步骤: {json.dumps(failed_step, ensure_ascii=False)}")
        if context:
            lines.append(f"前序步骤: {json.dumps(context[-2:], ensure_ascii=False)}")
        if error:
            lines.append(f"失败原因: {error}")
        lines.append("\n请返回 JSON，格式如:")
        lines.append('{"action": "click", "locator": [...], "screen_x": 100, "screen_y": 200}')
        lines.append('或 {"action": "input_text", "text": "正确文本"}')
        lines.append('或 {"action": "key_press", "key": "enter"}')
        return "\n".join(lines)


_store: LLMHealer | None = None


def get_healer() -> LLMHealer:
    global _store
    if _store is None:
        _store = LLMHealer()
    return _store
