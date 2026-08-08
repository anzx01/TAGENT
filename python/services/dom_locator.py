"""
Playwright DOM 定位模块（阶段2.2）
- 对 Chromium 浏览器（Chrome/Edge/Brave）补充 DOM 定位
- 录制时通过 CDP 连接浏览器，抓取点击元素的 DOM 路径
- 回放时通过 CSS 选择器定位元素并执行操作
"""
import json
from logger import get_logger

log = get_logger("dom_locator")

# 浏览器进程名前缀（用于判断当前前台是否是浏览器）
BROWSER_PROCESSES = ("chrome", "msedge", "brave")


def is_browser_window(window_title: str) -> bool:
    """判断窗口标题是否属于浏览器"""
    title_lower = window_title.lower()
    return any(b in title_lower for b in ("chrome", "edge", "brave", "chromium"))


def build_dom_locator(element_info: dict) -> dict:
    """从 UIA 元素信息构建 DOM 定位器
    element_info: {name, automation_id, class_name, ...}
    返回: {type: "dom", selector: "...", text: "..."}
    """
    name = element_info.get("name", "")
    auto_id = element_info.get("automation_id", "")
    # 优先用 name 作为文本选择器
    if name:
        return {"type": "dom", "selector": f"text='{name}'", "text": name}
    if auto_id:
        return {"type": "dom", "selector": f"#{auto_id}", "text": auto_id}
    return None


async def locate_by_dom(page, selector: str, timeout: int = 5000):
    """用 Playwright 通过 CSS 选择器定位元素，返回 bounding box (x, y, w, h)"""
    try:
        element = await page.query_selector(selector)
        if element:
            box = await element.bounding_box()
            if box:
                return (int(box["x"] + box["width"] / 2),
                        int(box["y"] + box["height"] / 2))
    except Exception as e:
        log.debug("DOM 定位失败: %s", e)
    return None


async def connect_browser(port: int = 9222):
    """通过 CDP 连接已运行的浏览器"""
    try:
        from playwright.async_api import async_playwright
        pw = await async_playwright().start()
        browser = await pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
        return browser, pw
    except Exception as e:
        log.debug("连接浏览器失败: %s (需要以 --remote-debugging-port=%d 启动浏览器)", e, port)
        return None, None


def get_browser_debug_port(hwnd) -> int:
    """尝试检测浏览器的调试端口"""
    # 常见调试端口
    for port in (9222, 9223, 9224):
        try:
            import urllib.request
            req = urllib.request.Request(f"http://127.0.0.1:{port}/json/version",
                                         headers={"Accept": "application/json"})
            resp = urllib.request.urlopen(req, timeout=1)
            data = json.loads(resp.read())
            if "Chrome" in data.get("Browser", ""):
                return port
        except Exception:
            pass
    return 0
