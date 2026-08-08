"""统一日志配置：所有模块输出到 logs/tagent.log + 控制台。"""

import logging
import sys
from pathlib import Path

from paths import BASE_DIR

LOG_DIR = BASE_DIR / "logs"
LOG_FILE = "tagent.log"
LOG_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"


def setup_logging(level: int = logging.INFO) -> logging.Logger:
    """初始化根 logger（幂等，可重复调用）。"""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    if root.handlers:
        return root
    root.setLevel(level)
    formatter = logging.Formatter(LOG_FORMAT)

    file_handler = logging.FileHandler(LOG_DIR / LOG_FILE, encoding="utf-8")
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    root.addHandler(console_handler)
    return root


def get_logger(name: str) -> logging.Logger:
    setup_logging()
    return logging.getLogger(name)
