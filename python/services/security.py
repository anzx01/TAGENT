"""
安全模块（阶段4.3）
- 密码框检测：录制时跳过/降敏处理密码输入（不落明文）
- 进程黑名单：恶意/高危进程禁止自动化
- 录制数据本地加密：对敏感步骤的文本做加密存储（密钥本地文件）
"""
import base64
import hashlib
import json
import os
from pathlib import Path

from logger import get_logger

log = get_logger("security")

BLACKLIST = ("format.com", "diskpart.exe", "defrag.exe", "cleanmgr.exe",
             "shutdown.exe", "msconfig.exe")

KEY_FILE = "data/secret.key"
DATA_DIR = Path(__file__).parent.parent.parent


class Security:
    def __init__(self):
        self._key = self._load_or_create_key()

    # ---------- 4.3 录制数据加密 ----------

    def _load_or_create_key(self) -> bytes:
        key_path = DATA_DIR / KEY_FILE
        key_path.parent.mkdir(parents=True, exist_ok=True)
        if key_path.exists():
            return key_path.read_bytes()
        key = os.urandom(32)
        key_path.write_bytes(key)
        log.info("已生成本地加密密钥: %s", key_path)
        return key

    def encrypt_text(self, text: str) -> str:
        """简单对称加密（XOR+Base64），防止明文落盘；密钥仅本机。"""
        data = text.encode("utf-8")
        key = self._key
        xored = bytes(b ^ key[i % len(key)] for i, b in enumerate(data))
        return "enc:" + base64.b64encode(xored).decode()

    def decrypt_text(self, blob: str) -> str:
        if not blob.startswith("enc:"):
            return blob
        raw = base64.b64decode(blob[4:])
        key = self._key
        return bytes(b ^ key[i % len(key)] for i, b in enumerate(raw)).decode("utf-8")

    # ---------- 4.5 密码框检测 ----------

    def is_password_edit(self, hwnd) -> bool:
        """检查窗口是否含密码编辑框（UIA IsPassword 模式）"""
        try:
            import uiautomation as ua
            ctrl = ua.ControlFromHandle(hwnd)
            if ctrl is None:
                return False
            edit = ctrl.EditControl(searchDepth=8)
            if not edit.Exists(1.0):
                return False
            try:
                return bool(edit.IsPassword())
            except Exception:
                return False
        except Exception:
            return False

    def is_blacklisted(self, exe_name: str) -> bool:
        return exe_name.lower() in BLACKLIST

    @staticmethod
    def current_exe() -> str:
        try:
            import psutil
            return psutil.Process().name()
        except ImportError:
            return ""


_store: Security | None = None


def get_security() -> Security:
    global _store
    if _store is None:
        _store = Security()
    return _store