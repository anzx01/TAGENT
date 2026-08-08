"""
截图 + 百分比坐标网格 - 拷贝自 ScreenClaw
PrintWindow 后台截图（无需窗口在前台）
网格为百分比坐标 0-100，分辨率无关
"""
import ctypes
from ctypes import windll, c_void_p, byref
import win32gui
from PIL import Image, ImageDraw, ImageFont

PW_RENDERFULLCONTENT = 0x2


def capture_window(hwnd):
    """用 PrintWindow 截图窗口（支持后台）"""
    try:
        rect = win32gui.GetWindowRect(hwnd)
        width = rect[2] - rect[0]
        height = rect[3] - rect[1]
        if width <= 0 or height <= 0:
            return None

        hwnd_dc = win32gui.GetWindowDC(hwnd)
        mem_dc = windll.gdi32.CreateCompatibleDC(hwnd_dc)

        # 创建 DIB
        bmi = ctypes.create_string_buffer(40)
        ctypes.memset(bmi, 0, 40)
        ctypes.memmove(bmi, ctypes.byref(ctypes.c_int32(40)), 4)
        ctypes.memmove(ctypes.addressof(bmi) + 4, ctypes.byref(ctypes.c_int32(width)), 4)
        ctypes.memmove(ctypes.addressof(bmi) + 8, ctypes.byref(ctypes.c_int32(-height)), 4)
        ctypes.memmove(ctypes.addressof(bmi) + 12, ctypes.byref(ctypes.c_ushort(1)), 2)
        ctypes.memmove(ctypes.addressof(bmi) + 14, ctypes.byref(ctypes.c_ushort(32)), 2)

        pbits = c_void_p()
        hbitmap = windll.gdi32.CreateDIBSection(hwnd_dc, bmi, 0, byref(pbits), None, 0)
        old_bitmap = windll.gdi32.SelectObject(mem_dc, hbitmap)

        result = windll.user32.PrintWindow(hwnd, mem_dc, PW_RENDERFULLCONTENT)

        image = None
        if result:
            buf_size = width * height * 4
            pixel_buf = ctypes.create_string_buffer(buf_size)
            ctypes.memmove(pixel_buf, pbits, buf_size)
            image = Image.frombuffer('RGBA', (width, height), pixel_buf.raw, 'raw', 'BGRA', 0, 1)
            image = image.convert('RGB')

        windll.gdi32.SelectObject(mem_dc, old_bitmap)
        windll.gdi32.DeleteObject(hbitmap)
        windll.gdi32.DeleteDC(mem_dc)
        win32gui.ReleaseDC(hwnd, hwnd_dc)

        return image
    except Exception:
        return None


def capture_screen():
    """截取整个屏幕（用于无法获取窗口句柄时）"""
    import mss
    with mss.mss() as sct:
        monitor = sct.monitors[1]
        img = sct.grab(monitor)
        return Image.frombytes('RGB', img.size, img.bgra, 'raw', 'BGRX')


class GridRenderer:
    """百分比坐标网格渲染器"""

    def __init__(self, density=5.0, grid_opacity=40, number_size=11):
        self.density = density
        self.grid_opacity = grid_opacity
        self.number_size = number_size

    def draw_grid(self, image):
        """在截图上叠加百分比网格 + 坐标数字"""
        width, height = image.size
        if image.mode != 'RGBA':
            image = image.convert('RGBA')
        overlay = Image.new('RGBA', image.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        grid_color = (255, 0, 0, int(255 * self.grid_opacity / 100))
        num_color = (255, 0, 0, 255)
        stroke_color = (255, 255, 255, 200)
        font = self._load_font(self.number_size)

        step_x = int(width * self.density / 100)
        step_y = int(height * self.density / 100)
        if step_x == 0 or step_y == 0:
            return image

        # 网格线
        pct = 0.0
        while pct <= 100.0:
            x = int(width * pct / 100)
            draw.line([(x, 0), (x, height)], fill=grid_color, width=1)
            pct += self.density
        pct = 0.0
        while pct <= 100.0:
            y = int(height * pct / 100)
            draw.line([(0, y), (width, y)], fill=grid_color, width=1)
            pct += self.density

        # 坐标数字（每2个网格标一个）
        num_step = self.density * 2
        pct_x = 0.0
        while pct_x <= 100.0:
            x = int(width * pct_x / 100)
            pct_y = 0.0
            while pct_y <= 100.0:
                y = int(height * pct_y / 100)
                label = f"{pct_x:.0f}x{pct_y:.0f}"
                draw.text((x + 2, y + 2), label, fill=num_color, font=font,
                          stroke_width=1, stroke_fill=stroke_color)
                pct_y += num_step
            pct_x += num_step

        return Image.alpha_composite(image, overlay)

    def draw_marker(self, image, pct_x, pct_y):
        """在截图上标记点击位置"""
        width, height = image.size
        if image.mode != 'RGBA':
            image = image.convert('RGBA')
        overlay = Image.new('RGBA', image.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        px = int(width * pct_x / 100)
        py = int(height * pct_y / 100)
        # 外圈
        draw.ellipse((px - 12, py - 12, px + 12, py + 12),
                     outline=(255, 0, 0, 255), width=2)
        # 中心点
        draw.ellipse((px - 3, py - 3, px + 3, py + 3), fill=(255, 0, 0, 255))
        return Image.alpha_composite(image, overlay)

    def _load_font(self, size):
        for name in ['arial.ttf', 'msyh.ttc', 'simsun.ttc']:
            try:
                return ImageFont.truetype(name, size)
            except Exception:
                continue
        return ImageFont.load_default()
