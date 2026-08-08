"""
安全模块（阶段4.3）
- 密码框检测：录制时跳过/降敏处理密码输入（不落明文）
- 进程黑名单：恶意/高危进程禁止自动化
- 录制数据本地加密：AES 加密存储（密钥本地文件）
"""
import base64
import json
import os
from pathlib import Path

import paths
from logger import get_logger

log = get_logger("security")

BLACKLIST = ("format.com", "diskpart.exe", "defrag.exe", "cleanmgr.exe",
             "shutdown.exe", "msconfig.exe")

KEY_FILE = "data/secret.key"
DATA_DIR = paths.BASE_DIR


class Security:
    def __init__(self):
        self._fernet = None
        self._legacy_key = None  # 旧 XOR 密钥（向后兼容）
        self._init_crypto()

    # ---------- 4.3 录制数据加密 ----------

    def _init_crypto(self):
        """初始化 AES (Fernet) 加密，加载旧 XOR 密钥用于解密历史数据"""
        key_path = DATA_DIR / KEY_FILE
        key_path.parent.mkdir(parents=True, exist_ok=True)
        # 旧密钥（XOR 兼容）
        if key_path.exists():
            raw = key_path.read_bytes()
            self._legacy_key = raw
        else:
            raw = os.urandom(32)
            key_path.write_bytes(raw)
            self._legacy_key = raw
            log.info("已生成本地加密密钥: %s", key_path)
        # Fernet 密钥（从原始密钥派生）
        try:
            from cryptography.fernet import Fernet
            import hashlib
            # 用 SHA256 派生 Fernet 兼容的 32 字节密钥
            derived = hashlib.sha256(raw).digest()
            fernet_key = base64.urlsafe_b64encode(derived)
            self._fernet = Fernet(fernet_key)
        except ImportError:
            log.warning("cryptography 库未安装，降级为 XOR 加密")

    def encrypt_text(self, text: str) -> str:
        """AES 加密（Fernet），防止明文落盘；密钥仅本机。"""
        if self._fernet:
            data = text.encode("utf-8")
            return "aes:" + self._fernet.encrypt(data).decode()
        # 降级：XOR
        data = text.encode("utf-8")
        key = self._legacy_key
        xored = bytes(b ^ key[i % len(key)] for i, b in enumerate(data))
        return "enc:" + base64.b64encode(xored).decode()

    def decrypt_text(self, blob: str) -> str:
        """解密：支持 AES (aes:) 和旧 XOR (enc:) 两种格式"""
        if blob.startswith("aes:"):
            if self._fernet:
                return self._fernet.decrypt(blob[4:].encode()).decode("utf-8")
            raise ValueError("AES 加密数据但 cryptography 库未安装")
        if blob.startswith("enc:"):
            # 旧 XOR 格式，向后兼容
            raw = base64.b64decode(blob[4:])
            key = self._legacy_key
            return bytes(b ^ key[i % len(key)] for i, b in enumerate(raw)).decode("utf-8")
        return blob  # 明文

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