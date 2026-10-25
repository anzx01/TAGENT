"""CALL_USER 通知推送(对标 Muse/Grok Bot 的"需要时找到你")。

配置 data/notify_config.json: {"url": "...", "enabled": true}
url 模板可含 {title}/{body} 占位(Bark/Server酱/企业微信 webhook 通用);
无占位时 POST JSON {"title","body"}。未配置或网络白名单拦截时静默跳过。
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from pathlib import Path


def config_path(base_dir: Path) -> Path:
    return Path(base_dir) / "data" / "notify_config.json"


def load_config(base_dir: Path) -> dict:
    try:
        data = json.loads(config_path(base_dir).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_config(base_dir: Path, config: dict) -> None:
    path = config_path(base_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")


def notify(title: str, body: str, base_dir: Path, *, urlopen=None) -> bool:
    """推送一条通知;任何失败都不抛(通知是尽力而为)。"""
    config = load_config(base_dir)
    if not config.get("enabled") or not config.get("url"):
        return False
    url = str(config["url"])
    from services.network_guard import outbound_allowed
    if not outbound_allowed(base_dir, url):
        return False
    urlopen = urlopen or urllib.request.urlopen
    try:
        if "{title}" in url or "{body}" in url:
            quoted = urllib.parse.quote
            target = url.replace("{title}", quoted(title)).replace("{body}", quoted(body))
            urlopen(urllib.request.Request(target), timeout=5)
        else:
            req = urllib.request.Request(
                url, method="POST", headers={"Content-Type": "application/json"},
                data=json.dumps({"title": title, "body": body}, ensure_ascii=False).encode("utf-8"))
            urlopen(req, timeout=5)
        return True
    except Exception:
        return False
