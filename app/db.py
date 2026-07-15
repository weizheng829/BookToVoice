"""SQLite 数据层：建表 + CRUD。单进程内共享连接，全局锁串行化写。"""
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime

from . import config

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS books (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT    NOT NULL,
    source_path   TEXT,
    voice         TEXT    NOT NULL DEFAULT 'zh-CN-XiaoxiaoNeural',
    rate          TEXT    NOT NULL DEFAULT '+0%',
    pitch         TEXT    NOT NULL DEFAULT '+0Hz',
    narrate_title INTEGER NOT NULL DEFAULT 1,
    paused        INTEGER NOT NULL DEFAULT 0,
    total_chapters INTEGER DEFAULT 0,
    status        TEXT    NOT NULL DEFAULT 'pending',  -- pending/running/done/failed
    created_at    TEXT    NOT NULL,
    updated_at    TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS chapters (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id    INTEGER NOT NULL,
    idx        INTEGER NOT NULL,
    title      TEXT    NOT NULL DEFAULT '',
    text       TEXT    NOT NULL,
    status     TEXT    NOT NULL DEFAULT 'pending',  -- pending/generating/done/failed
    audio_path TEXT,
    error      TEXT,
    retries    INTEGER NOT NULL DEFAULT 0,
    created_at TEXT    NOT NULL,
    updated_at TEXT    NOT NULL,
    FOREIGN KEY (book_id) REFERENCES books(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_chapters_book ON chapters(book_id);
CREATE INDEX IF NOT EXISTS idx_chapters_status ON chapters(status);
"""


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _migrate(conn) -> None:
    """向前兼容：给旧库补列。"""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(books)")}
    if "paused" not in cols:
        conn.execute("ALTER TABLE books ADD COLUMN paused INTEGER NOT NULL DEFAULT 0")


def get_db() -> sqlite3.Connection:
    """返回共享连接（首次调用时建表）。"""
    global _conn
    if _conn is None:
        config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(config.DB_PATH), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(SCHEMA)
        _migrate(conn)
        conn.commit()
        _conn = conn
    return _conn


@contextmanager
def db_cursor():
    conn = get_db()
    with _lock:
        cur = conn.cursor()
        try:
            yield cur
            conn.commit()
        except Exception:
            conn.rollback()
            raise


# ---------------- books ----------------

def create_book(name, source_path, voice, rate, pitch, narrate_title: bool) -> int:
    ts = now()
    with db_cursor() as cur:
        cur.execute(
            """INSERT INTO books
               (name, source_path, voice, rate, pitch, narrate_title, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (name, source_path, voice, rate, pitch, 1 if narrate_title else 0, ts, ts),
        )
        return cur.lastrowid


def get_book(book_id: int):
    with db_cursor() as cur:
        cur.execute("SELECT * FROM books WHERE id=?", (book_id,))
        return cur.fetchone()


def list_books():
    with db_cursor() as cur:
        cur.execute("SELECT * FROM books ORDER BY id DESC")
        return cur.fetchall()


def update_book_status(book_id: int, status: str):
    with db_cursor() as cur:
        cur.execute(
            "UPDATE books SET status=?, updated_at=? WHERE id=?",
            (status, now(), book_id),
        )


def set_book_paused(book_id: int, paused: bool):
    with db_cursor() as cur:
        cur.execute(
            "UPDATE books SET paused=?, updated_at=? WHERE id=?",
            (1 if paused else 0, now(), book_id),
        )


def set_book_total(book_id: int, total: int):
    with db_cursor() as cur:
        cur.execute(
            "UPDATE books SET total_chapters=?, updated_at=? WHERE id=?",
            (total, now(), book_id),
        )


def update_book_settings(book_id: int, voice: str | None = None,
                         rate: str | None = None,
                         narrate_title: bool | None = None,
                         name: str | None = None):
    fields, vals = [], []
    if name is not None:
        fields.append("name=?"); vals.append(name)
    if voice is not None:
        fields.append("voice=?"); vals.append(voice)
    if rate is not None:
        fields.append("rate=?"); vals.append(rate)
    if narrate_title is not None:
        fields.append("narrate_title=?"); vals.append(1 if narrate_title else 0)
    if not fields:
        return
    fields.append("updated_at=?"); vals.append(now()); vals.append(book_id)
    with db_cursor() as cur:
        cur.execute(f"UPDATE books SET {','.join(fields)} WHERE id=?", vals)


# ---------------- chapters ----------------

def create_chapters(book_id: int, chapters: list[tuple[str, str]]):
    """chapters: [(title, text), ...]"""
    ts = now()
    rows = [
        (book_id, i + 1, title, text, "pending", 0, ts, ts)
        for i, (title, text) in enumerate(chapters)
    ]
    with db_cursor() as cur:
        cur.executemany(
            """INSERT INTO chapters
               (book_id, idx, title, text, status, retries, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            rows,
        )


def list_chapters(book_id: int):
    with db_cursor() as cur:
        cur.execute(
            "SELECT * FROM chapters WHERE book_id=? ORDER BY idx", (book_id,)
        )
        return cur.fetchall()


def get_chapter(chapter_id: int):
    with db_cursor() as cur:
        cur.execute("SELECT * FROM chapters WHERE id=?", (chapter_id,))
        return cur.fetchone()


def get_next_pending_chapter():
    """取最早一本书的第一个 pending 章节。"""
    with db_cursor() as cur:
        cur.execute(
            """SELECT c.* FROM chapters c
               JOIN books b ON c.book_id = b.id
               WHERE c.status='pending' AND b.paused=0
               ORDER BY c.book_id ASC, c.idx ASC
               LIMIT 1"""
        )
        return cur.fetchone()


def set_chapter_generating(chapter_id: int):
    with db_cursor() as cur:
        cur.execute(
            "UPDATE chapters SET status='generating', updated_at=? WHERE id=?",
            (now(), chapter_id),
        )


def set_chapter_done(chapter_id: int, audio_path: str):
    with db_cursor() as cur:
        cur.execute(
            """UPDATE chapters
               SET status='done', audio_path=?, error=NULL, updated_at=?
               WHERE id=?""",
            (audio_path, now(), chapter_id),
        )


def set_chapter_failed(chapter_id: int, error: str):
    with db_cursor() as cur:
        cur.execute(
            "UPDATE chapters SET status='failed', error=?, updated_at=? WHERE id=?",
            (error, now(), chapter_id),
        )


def requeue_chapter(chapter_id: int, retries: int):
    """重试：回到 pending，累加 retries。"""
    with db_cursor() as cur:
        cur.execute(
            """UPDATE chapters
               SET status='pending', retries=?, error=NULL, updated_at=?
               WHERE id=?""",
            (retries, now(), chapter_id),
        )


def requeue_chapter_by_id(chapter_id: int):
    """手动单章重生：回到 pending，retries 清零。"""
    with db_cursor() as cur:
        cur.execute(
            """UPDATE chapters
               SET status='pending', retries=0, error=NULL, audio_path=NULL, updated_at=?
               WHERE id=?""",
            (now(), chapter_id),
        )


def requeue_failed_chapters(book_id: int):
    with db_cursor() as cur:
        cur.execute(
            """UPDATE chapters
               SET status='pending', retries=0, error=NULL, updated_at=?
               WHERE book_id=? AND status='failed'""",
            (now(), book_id),
        )


def reset_generating_to_pending():
    """启动时：上次中断的 generating 章节回到 pending（retries 保留）。"""
    with db_cursor() as cur:
        cur.execute(
            "UPDATE chapters SET status='pending' WHERE status='generating'"
        )


def reset_running_books():
    """启动时：running 的书回到 pending，由 worker 重新推进。"""
    with db_cursor() as cur:
        cur.execute(
            "UPDATE books SET status='pending' WHERE status='running'"
        )


# ---------------- 状态汇总 ----------------

def book_progress(book_id: int) -> dict:
    with db_cursor() as cur:
        cur.execute(
            "SELECT status, COUNT(*) AS c FROM chapters WHERE book_id=? GROUP BY status",
            (book_id,),
        )
        counts = {r["status"]: r["c"] for r in cur.fetchall()}
    total = sum(counts.values())
    done = counts.get("done", 0)
    failed = counts.get("failed", 0)
    generating = counts.get("generating", 0)
    pending = counts.get("pending", 0)
    percent = int(done * 100 / total) if total else 0
    return {
        "total": total,
        "done": done,
        "failed": failed,
        "generating": generating,
        "pending": pending,
        "percent": percent,
    }


def refresh_book_status(book_id: int):
    """根据章节状态刷新书籍状态。"""
    p = book_progress(book_id)
    total = p["total"]
    if total == 0:
        return
    if p["pending"] == 0 and p["generating"] == 0:
        update_book_status(book_id, "failed" if p["failed"] > 0 else "done")
    else:
        update_book_status(book_id, "running")
