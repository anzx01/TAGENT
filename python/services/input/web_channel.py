"""CDP 网页注入通道（移植 desktop-recorder drive-web.js）
- 通过 Chrome DevTools Protocol 向浏览器页面注入输入，不抢桌面焦点
- 仅在浏览器以 --remote-debugging-port 启动时可用；否则全通道优雅跳过
"""
import time

from logger import get_logger
from services.input.common import StepResult

log = get_logger("web_channel")

DEBUG_PORTS = (9222, 9223, 9224)
BROWSER_SUFFIXES = (" - Google Chrome", " - Microsoft Edge", " - Brave", " - Firefox")
KEY_MAP = {"enter": "Enter", "tab": "Tab", "esc": "Escape", "space": "Space",
           "backspace": "Backspace", "delete": "Delete", "home": "Home", "end": "End",
           "up": "ArrowUp", "down": "ArrowDown", "left": "ArrowLeft",
           "right": "ArrowRight", "pageup": "PageUp", "pagedown": "PageDown",
           "ctrl": "Control", "alt": "Alt", "shift": "Shift"}


def _strip_browser(title: str) -> str:
    """窗口标题去浏览器名后缀（如 'xxx - Brave' -> 'xxx'）"""
    for suffix in BROWSER_SUFFIXES:
        if title.endswith(suffix):
            return title[: -len(suffix)]
    return title


def _safe_title(page) -> str:
    try:
        return page.title()
    except Exception:
        return "?"


class WebChannel:
    """浏览器页面输入通道：click/type/key 全部经 CDP 注入，窗口无需前台"""

    def __init__(self):
        self._pw = None
        self._browser = None
        self._tried = False  # 已尝试过连接（避免每步反复探测 3 个端口）
        self.active = False  # 最近一次操作是否已走网页通道（决定 type 是否走页内）

    # ---------- 连接 ----------

    def ensure(self) -> bool:
        if self._browser is not None:
            return True
        if self._tried:
            return False
        self._tried = True
        try:
            from playwright.sync_api import sync_playwright
            self._pw = sync_playwright().start()
            for port in DEBUG_PORTS:
                try:
                    self._browser = self._pw.chromium.connect_over_cdp(
                        f"http://127.0.0.1:{port}", timeout=700)
                    log.info("web_channel 已连接 CDP :%d", port)
                    return True
                except Exception:
                    continue
            self._pw.stop()
            self._pw = None
            log.info("web_channel: 未发现调试端口，网页通道不可用")
            return False
        except Exception as e:
            log.warning("web_channel 初始化失败: %s", e)
            self._browser = None
            return False

    # ---------- 页面匹配 ----------

    def _find_page(self, window_title: str):
        """按窗口标题匹配页面（录制时 fg 标题即浏览器标题）"""
        if not window_title or not self._browser:
            return None
        base = _strip_browser(window_title)
        try:
            for ctx in self._browser.contexts:
                for page in ctx.pages:
                    try:
                        t = page.title()
                    except Exception:
                        continue
                    if t and (t == base or base.startswith(t)):
                        return page
                    if not t and len(ctx.pages) == 1:
                        return page
        except Exception as e:
            log.debug("页面匹配失败: %s", e)
        return None

    def can_inject(self, window_title: str) -> bool:
        """网页通道对某窗口是否可用（CDP 已连 + 页面匹配成功）"""
        if not window_title or not self.ensure():
            return False
        return self._find_page(window_title) is not None

    # ---------- 动作 ----------

    def _locate(self, page, locator: dict):
        sel = (locator.get("selector") or "").strip()
        if sel.startswith("text='"):
            txt = sel[len("text='"):-1]
            return page.get_by_text(txt, exact=True)
        if sel.startswith("label='"):
            return page.get_by_label(sel[len("label='"):-1], exact=True)
        if sel.startswith("placeholder='"):
            return page.get_by_placeholder(sel[len("placeholder='"):-1], exact=True)
        if sel.startswith("#") or sel.startswith("."):
            return page.locator(sel)
        if sel:
            return page.locator(sel)
        return None

    def maybe_click(self, window_title: str, locator: dict) -> StepResult | None:
        """网页点击：返回 None=通道不可用（交由物理通道）；StepResult=已尝试"""
        if not locator or locator.get("type") != "dom" or not window_title:
            return None
        if not self.ensure():
            return None
        page = self._find_page(window_title)
        if not page:
            return None
        try:
            target = self._locate(page, locator)
            if target is None:
                return None
            count = target.count()
            if count <= 0:
                log.warning("element_not_found: sel=%r page=%r title=%r",
                            locator.get("selector"), page.url, _safe_title(page))
                self.active = False
                # Let the caller fall back to the recorded desktop locator.
                # A stale DOM selector must not strand an otherwise replayable
                # workflow in the human-takeover loop.
                return None
            box = target.first.bounding_box()
            if not box:
                self.active = False
                return None
            page.mouse.click(box["x"] + box["width"] / 2,
                             box["y"] + box["height"] / 2)
            self.active = True
            return StepResult(True, "web_click")
        except Exception as e:
            log.debug("web_click 失败: %s", e)
            self.active = False
            return None

    def maybe_type(self, window_title: str, text: str) -> StepResult | None:
        """页内输入（需目标页面已聚焦；无此前提则跳过）"""
        if not self.active or not window_title:
            return None
        if not self.ensure():
            return None
        page = self._find_page(window_title)
        if not page:
            return None
        try:
            page.keyboard.insert_text(text)
            time.sleep(0.1)
            return StepResult(True, "web_type")
        except Exception as e:
            log.debug("web_type 失败: %s", e)
            return None

    def maybe_key(self, window_title: str, combo: str) -> StepResult | None:
        """页内按键（仅常用单键/组合）"""
        if not self.active or not window_title or not combo:
            return None
        if not self.ensure():
            return None
        page = self._find_page(window_title)
        if not page:
            return None
        parts = combo.lower().split()
        key = parts[-1]
        key_name = KEY_MAP.get(key) or (key.upper() if len(key) == 1 else None)
        if not key_name:
            return None
        mods = " ".join(KEY_MAP.get(m, m) for m in parts[:-1])
        combo_key = f"{mods}+{key_name}" if mods else key_name
        try:
            page.keyboard.press(combo_key)
            time.sleep(0.1)
            return StepResult(True, "web_key")
        except Exception as e:
            log.debug("web_key 失败: %s", e)
            return None


_web: WebChannel | None = None


def get_web_channel() -> WebChannel:
    global _web
    if _web is None:
        _web = WebChannel()
    return _web
