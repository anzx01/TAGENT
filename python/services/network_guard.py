"""网络白名单(本地版 Sentinel 承诺):开启后,除显式允许的主机外禁止外联。

只约束本项目自身发起的网络调用(通知推送、LLM);不是操作系统级防火墙。
shortcut: 无法拦截第三方库的隐式联网,需要硬隔离时应配合系统防火墙
"""
from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse


def config_path(base_dir: Path) -> Path:
    return Path(base_dir) / "data" / "network_guard.json"


def load(base_dir: Path) -> dict:
    try:
        data = json.loads(config_path(base_dir).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save(base_dir: Path, config: dict) -> None:
    path = config_path(base_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")


def outbound_allowed(base_dir: Path, url: str) -> bool:
    """白名单关闭→全放行;开启→主机必须在 allow_hosts 内。"""
    config = load(base_dir)
    if not config.get("enabled"):
        return True
    host = (urlparse(str(url)).hostname or "").lower()
    allowed = {str(h).lower() for h in config.get("allow_hosts", [])}
    allowed.add("127.0.0.1")  # 本机回环永远允许
    allowed.add("localhost")
    return host in allowed
