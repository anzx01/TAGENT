"""连接器权限分级(对标 Muse 的"仅读 vs 代发")。

连接器:outlook(邮件)、clipboard(剪贴板)、files(文件读写)。
档位:readonly(只读)/ act(可代操作)。默认 act 保持既有行为;
readonly 时代操作类动作返回失败而非静默放行。
存储 data/connector_permissions.json
"""
from __future__ import annotations

import json
from pathlib import Path

CONNECTORS = ("outlook", "clipboard", "files")
DEFAULTS = {name: "act" for name in CONNECTORS}


def store_path(base_dir: Path) -> Path:
    return Path(base_dir) / "data" / "connector_permissions.json"


def load(base_dir: Path) -> dict:
    result = dict(DEFAULTS)
    try:
        data = json.loads(store_path(base_dir).read_text(encoding="utf-8"))
        if isinstance(data, dict):
            for name in CONNECTORS:
                if data.get(name) in ("readonly", "act"):
                    result[name] = data[name]
    except (OSError, ValueError):
        pass
    return result


def set_permission(base_dir: Path, name: str, level: str) -> bool:
    if name not in CONNECTORS or level not in ("readonly", "act"):
        return False
    current = load(base_dir)
    current[name] = level
    path = store_path(base_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    return True


def allowed(base_dir: Path, name: str) -> bool:
    """该连接器是否允许"代操作";readonly 返回 False。"""
    return load(base_dir).get(name, "act") == "act"
