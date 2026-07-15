"""集中配置：从环境变量读取，提供合理默认值。"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# ---- 合成默认参数 ----
DEFAULT_VOICE = os.getenv("DEFAULT_VOICE", "zh-CN-XiaoxiaoNeural")  # 晓晓
DEFAULT_RATE = os.getenv("DEFAULT_RATE", "+0%")
DEFAULT_PITCH = os.getenv("DEFAULT_PITCH", "+0Hz")

# ---- Worker ----
NARRATE_TITLE_DEFAULT = os.getenv("NARRATE_TITLE_DEFAULT", "true").lower() in (
    "1", "true", "yes", "on",
)
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))
RETRY_BACKOFF_BASE = float(os.getenv("RETRY_BACKOFF_BASE", "5"))  # 秒
WORKER_IDLE_SLEEP = float(os.getenv("WORKER_IDLE_SLEEP", "2"))

# ---- 存储 ----
INPUT_DIR = Path(os.getenv("INPUT_DIR", str(BASE_DIR / "input")))
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", str(BASE_DIR / "output")))
DB_PATH = Path(os.getenv("DB_PATH", str(BASE_DIR / "data" / "book.db")))

# ---- UI 声音下拉 ----
VOICE_OPTIONS = [
    ("zh-CN-XiaoxiaoNeural", "晓晓（女）"),
    ("zh-CN-XiaoyiNeural", "晓伊（女）"),
    ("zh-CN-XiaoxuanNeural", "晓萱（女）"),
    ("zh-CN-YunxiNeural", "云希（男）"),
    ("zh-CN-YunjianNeural", "云健（男）"),
    ("zh-CN-YunyangNeural", "云扬（男）"),
]
VOICE_MAP = {v: label for v, label in VOICE_OPTIONS}
