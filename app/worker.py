"""后台串行 worker：取 pending 章节 → 调 edge-tts 合成 → 落盘 → 更新状态。

- 串行（单线程），不并发；
- 失败指数退避重试，超过 MAX_RETRIES 标记 failed；
- 启动时把上次中断的 generating 章节回退到 pending，实现断点续跑。
"""
import logging
import re
import threading
import time
from pathlib import Path

from . import config, db, tts

log = logging.getLogger("worker")

_stop = threading.Event()
_thread: threading.Thread | None = None


def _sanitize(name: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|\s]+', "_", name).strip("_")
    return cleaned or "untitled"


def _audio_path(chapter, book) -> Path:
    folder = config.OUTPUT_DIR / _sanitize(book["name"])
    idx = f"{chapter['idx']:04d}"
    title = _sanitize(chapter["title"])[:30]
    fname = f"{idx}_{title}.mp3" if title else f"{idx}.mp3"
    return folder / fname


def _build_text(chapter, book) -> str:
    """整章文本（标题可选 + 正文）。直连 edge-tts 一次合成，无需切段。"""
    parts = []
    if book["narrate_title"] and chapter["title"]:
        parts.append(chapter["title"].strip())
    body = chapter["text"].strip()
    if body:
        parts.append(body)
    return "\n".join(parts)


def _process_chapter(chapter) -> None:
    book = db.get_book(chapter["book_id"])
    if not book:
        db.set_chapter_failed(chapter["id"], "找不到所属书籍")
        return
    db.update_book_status(book["id"], "running")
    db.set_chapter_generating(chapter["id"])

    out = _audio_path(chapter, book)
    # 断点续传：目标文件已存在且非空 → 直接标记完成（借鉴本地脚本）
    if out.exists() and out.stat().st_size > 0:
        log.info("已存在，跳过: %s", out)
        db.set_chapter_done(chapter["id"], str(out))
        return

    text = _build_text(chapter, book)
    if not text.strip():
        db.set_chapter_failed(chapter["id"], "章节无有效正文")
        return

    log.info("开始生成: book=%s chapter[%s] %s -> %s",
             book["name"], chapter["idx"], chapter["title"], out)
    tts.synthesize(text, out, book["voice"], book["rate"], None, book["pitch"])
    # 合成期间书籍可能被删除 → 删掉刚写出的孤儿文件，不再 set_chapter_done（章节行已级联消失）
    if not db.get_book(chapter["book_id"]):
        log.info("合成完成时书籍已被删除，清理孤儿文件: %s", out)
        try:
            out.unlink()
        except OSError:
            pass
        try:
            out.parent.rmdir()  # 仅当目录为空时才删除
        except OSError:
            pass
        return
    db.set_chapter_done(chapter["id"], str(out))


def _worker_loop() -> None:
    log.info("Worker 启动（串行模式）")
    while not _stop.is_set():
        try:
            chapter = db.get_next_pending_chapter()
            if chapter is None:
                time.sleep(config.WORKER_IDLE_SLEEP)
                continue

            book_id = chapter["book_id"]
            try:
                _process_chapter(chapter)
            except Exception as e:  # noqa: BLE001
                log.exception("章节生成失败 chapter_id=%s", chapter["id"])
                retries = chapter["retries"] + 1
                if retries < config.MAX_RETRIES:
                    backoff = config.RETRY_BACKOFF_BASE * retries
                    log.warning("将在 %.1fs 后重试 (%d/%d) chapter_id=%s",
                                backoff, retries, config.MAX_RETRIES, chapter["id"])
                    db.requeue_chapter(chapter["id"], retries)
                    time.sleep(backoff)
                else:
                    db.set_chapter_failed(chapter["id"], str(e))

            db.refresh_book_status(book_id)
        except Exception:  # noqa: BLE001
            log.exception("Worker 循环异常")
            time.sleep(config.WORKER_IDLE_SLEEP)
    log.info("Worker 已停止")


def start() -> None:
    """启动 worker（重复调用安全）。"""
    global _thread
    if _thread and _thread.is_alive():
        return
    db.reset_generating_to_pending()
    db.reset_running_books()
    _stop.clear()
    _thread = threading.Thread(target=_worker_loop, daemon=True, name="book-worker")
    _thread.start()


def stop() -> None:
    _stop.set()
