"""CDP DOM 证据抓取（录制侧）
- Chrome/Edge/Brave 的 UIA 桥不暴露页面控件文本（只有 Chrome Legacy Window）
- 通过 Playwright 连接调试端口，用 elementFromPoint 抓点击点元素的
  text / id / aria-label / placeholder，供编译器生成 dom 定位器
"""
import time

from logger import get_logger
from services.input.web_channel import DEBUG_PORTS, _strip_browser

log = get_logger("web_dom")

UI_OFFSET = 110  # Chrome 标题栏+标签栏+地址栏高度估算（视口顶到窗口顶）
EVAL_JS = """(() => {
  const el = document.elementFromPoint(%d, %d);
  if (!el) return null;
  let node = el.closest ? el.closest('button,a,input,select,textarea,label') : null;
  if (!node) node = el;
  const d = {tag: node.tagName.toLowerCase()};
  if (node.id) d.id = node.id;
  const txt = (node.innerText || node.textContent || '').trim().slice(0, 60);
  if (txt) d.text = txt;
  const label = node.getAttribute && node.getAttribute('aria-label');
  if (label) d.label = label;
  if (node.placeholder) d.placeholder = node.placeholder;
  if (node.name) d.name = node.name;
  return d;
})()"""


class WebDom:
    def __init__(self):
        self._pw = None
        self._browser = None

    def _ensure(self) -> bool:
        if self._browser is not None:
            return True
        try:
            from playwright.sync_api import sync_playwright
            self._pw = sync_playwright().start()
            for port in DEBUG_PORTS:
                try:
                    self._browser = self._pw.chromium.connect_over_cdp(
                        f"http://127.0.0.1:{port}", timeout=700)
                    return True
                except Exception:
                    continue
            self._pw.stop()
            self._pw = None
            return False
        except Exception:
            return False

    def element_desc(self, window_title: str, screen_x: int, screen_y: int) -> dict | None:
        """抓取屏幕坐标处的 DOM 元素描述；失败返回 None（不阻断录制）"""
        if not self._ensure():
            return None
        try:
            base = _strip_browser(window_title)
            for ctx in self._browser.contexts:
                pages = ctx.pages
                for page in pages:
                    try:
                        title = page.title()
                        # file:///空标题页在仅单页时兜底匹配；否则按标题前缀
                        if (title and base.startswith(title)) or (
                                not title and len(pages) == 1):
                            vx, vy = screen_x, screen_y - UI_OFFSET
                            desc = page.evaluate(EVAL_JS % (vx, vy))
                            if desc:
                                return desc
                    except Exception:
                        continue
        except Exception as e:
            log.debug("web_dom.element_desc 失败: %s", e)
        return None

    def close(self):
        try:
            if self._browser:
                self._browser.close()
        except Exception:
            pass
        try:
            if self._pw:
                self._pw.stop()
        except Exception:
            pass
        self._browser = None
        self._pw = None


_dom: WebDom | None = None


def get_web_dom() -> WebDom:
    global _dom
    if _dom is None:
        _dom = WebDom()
    return _dom


def build_dom_locator_from_cdp(window_title: str, screen_x: int, screen_y: int) -> dict | None:
    """编译器入口：CDP 抓取 + 生成 {type: dom, selector, text}"""
    if screen_x is None or screen_y is None:
        return None
    desc = get_web_dom().element_desc(window_title, screen_x, screen_y)
    if not desc:
        return None
    selector = None
    text = ""
    if desc.get("label") and len(desc["label"]) <= 40:
        selector = f"label='{desc['label']}'"
        text = desc["label"]
    elif desc.get("placeholder") and len(desc["placeholder"]) <= 40:
        selector = f"placeholder='{desc['placeholder']}'"
        text = desc["placeholder"]
    elif desc.get("text") and len(desc["text"]) <= 20 and desc.get("tag") != "input":
        selector = f"text='{desc['text']}'"
        text = desc["text"]
    elif desc.get("id"):
        selector = f"#{desc['id']}"
        text = desc["id"]
    if not selector:
        log.debug("web_dom: 元素无明显定位特征 %s", {k: v for k, v in desc.items() if k != "text"})
        return None
    return {"type": "dom", "selector": selector, "text": text}