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
# 去站名水印（合成时剔除网文站点水印），默认关闭：按特征过滤偶有误伤，交由用户勾选
STRIP_WATERMARKS_DEFAULT = os.getenv("STRIP_WATERMARKS_DEFAULT", "false").lower() in (
    "1", "true", "yes", "on",
)
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))
RETRY_BACKOFF_BASE = float(os.getenv("RETRY_BACKOFF_BASE", "5"))  # 秒
WORKER_IDLE_SLEEP = float(os.getenv("WORKER_IDLE_SLEEP", "2"))
# 并发 worker 线程数：合成是网络 I/O 密集型，多线程并行可成倍提速。
# 过大可能触发 Edge-TTS 限流（429/失败增多），默认 5；失败频繁就调小，网络好可调大。
WORKER_CONCURRENCY = max(1, int(os.getenv("WORKER_CONCURRENCY", "5")))

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

# ---- 全局设置项默认值 ----
# env 仅作初始兜底；用户在 Web UI（右上角 ⚙）改过则写库，db.get_settings() 库值优先。
DEFAULT_SETTINGS = {
    "worker_concurrency": WORKER_CONCURRENCY,
    "max_retries": MAX_RETRIES,
    "retry_backoff_base": RETRY_BACKOFF_BASE,
    "default_voice": DEFAULT_VOICE,
    "default_rate": DEFAULT_RATE,
    "narrate_title": NARRATE_TITLE_DEFAULT,
}
