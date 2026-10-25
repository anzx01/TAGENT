"""条件触发器/盯守(对标 Muse 的"世界变化时行动")。

条件类型:file_exists(路径或 glob)、window_appears(窗口标题包含)、
mail_arrived 交给连接器层判断(当前机器无 Outlook 时返回 False)。
存储 data/watchers.json;触发有冷却时间防循环。
"""
from __future__ import annotations

import json
import time
from pathlib import Path


def store_path(base_dir: Path) -> Path:
    return Path(base_dir) / "data" / "watchers.json"


def load(base_dir: Path) -> list[dict]:
    try:
        data = json.loads(store_path(base_dir).read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def save(base_dir: Path, watchers: list[dict]) -> None:
    path = store_path(base_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(watchers, ensure_ascii=False, indent=2), encoding="utf-8")


def upsert(base_dir: Path, rec_id: str, condition: dict, cooldown_min: int = 10) -> dict:
    watchers = load(base_dir)
    for item in watchers:
        if item.get("rec_id") == rec_id:
            item["condition"] = condition
            item["cooldown_min"] = max(1, int(cooldown_min))
            item["enabled"] = True
            save(base_dir, watchers)
            return item
    item = {"id": f"w{int(time.time() * 1000) % 10**9}", "rec_id": rec_id,
            "condition": condition, "cooldown_min": max(1, int(cooldown_min)),
            "enabled": True, "last_fired": 0}
    watchers.append(item)
    save(base_dir, watchers)
    return item


def remove(base_dir: Path, rec_id: str) -> bool:
    watchers = load(base_dir)
    kept = [w for w in watchers if w.get("rec_id") != rec_id]
    if len(kept) != len(watchers):
        save(base_dir, kept)
        return True
    return False


def _file_exists(value: str) -> bool:
    value = str(value or "")
    if any(ch in value for ch in "*?["):
        return bool(list(Path(".").glob(value)) or list(Path(value).parent.glob(Path(value).name)))
    return Path(value).exists()


def _window_appears(value: str, enumerate_windows=None) -> bool:
    if not value:
        return False
    if enumerate_windows is None:
        def enumerate_windows():
            import win32gui
            out = []
            win32gui.EnumWindows(lambda h, l: (l.append(win32gui.GetWindowText(h)), True)[1], out)
            return out
    needle = str(value).casefold()
    return any(needle in str(t).casefold() for t in enumerate_windows())


def evaluate(condition: dict, *, file_exists=None, window_appears=None) -> bool:
    kind = condition.get("kind")
    if kind == "file_exists":
        check = file_exists or _file_exists
        return bool(check(condition.get("value", "")))
    if kind == "window_appears":
        check = window_appears or _window_appears
        return bool(check(condition.get("value", "")))
    # shortcut: mail_arrived 依赖连接器实现,当前恒 False;接入邮件连接器时扩展
    return False


def due(watcher: dict, now: float | None = None) -> bool:
    """冷却判断:距上次触发超过 cooldown 分钟才允许再触发。"""
    now = now if now is not None else time.time()
    return (now - float(watcher.get("last_fired", 0))) >= float(watcher.get("cooldown_min", 10)) * 60


def mark_fired(base_dir: Path, watcher_id: str) -> None:
    watchers = load(base_dir)
    for item in watchers:
        if item.get("id") == watcher_id:
            item["last_fired"] = time.time()
    save(base_dir, watchers)
