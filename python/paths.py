"""项目根目录解析：源码模式指向仓库根，PyInstaller 打包后指向 exe 所在目录"""
import sys
from pathlib import Path

if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parent.parent
