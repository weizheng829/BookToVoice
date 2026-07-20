"""后台并发 worker：取 pending 章节 → 调 edge-tts 合成 → 落盘 → 更新状态。

- 多 worker 并发；并发数由全局设置驱动，**运行时可动态伸缩**（set_concurrency）；
  扩容立即补线程，缩容时多余线程跑完当前章后自然退出——绝不在合成中途打断。
- 章节由 claim_next_chapter() 原子领取（SELECT+UPDATE 在同一把锁内），不会重复合成；
- 失败指数退避重试，超过 _max_retries 标记 failed（重试参数也运行时可调）；
- 启动时把上次中断的 generating 章节回退到 pending，实现断点续跑。
"""
import logging
import re
import threading
import time
from pathlib import Path

from . import config, db, parser, tts

log = logging.getLogger("worker")

_stop = threading.Event()            # 全停：停整个 worker 池
_threads: list[threading.Thread] = []
_scale = threading.Lock()            # 守护 _alive / _leaving / _target / _threads 的伸缩
_alive = 0                           # 当前活动 worker 数（含已决定退出、尚未 finally 的）
_leaving = 0                         # 已决定缩容退出、尚未 finally 的（用于精确计算有效并发）
_target = config.WORKER_CONCURRENCY  # 目标并发（set_concurrency 改它）
_max_retries = config.MAX_RETRIES    # 运行时可调
_backoff_base = config.RETRY_BACKOFF_BASE  # 运行时可调
_next_wid = 0                        # worker 自增编号（线程名唯一）


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
    text = "\n".join(parts)
    if book["strip_watermarks"]:
        text = parser.strip_watermarks(text)  # 按特征剔除网文站点水印（仅影响朗读内容）
    return text


def _process_chapter(chapter) -> None:
    book = db.get_book(chapter["book_id"])
    if not book:
        db.set_chapter_failed(chapter["id"], "找不到所属书籍")
        return
    db.update_book_status(book["id"], "running")
    # 章节已由 claim_next_chapter() 原子领取并标记为 generating，这里无需重复

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


def _worker_loop(wid: int) -> None:
    global _alive, _leaving
    leaving = False
    with _scale:
        _alive += 1
    try:
        while not _stop.is_set():
            # 缩容：有效活动数（_alive - _leaving）超过目标 → 本线程预占一个退出名额后退出。
            # 用 _leaving 计数避免多个线程同时判定 surplus 导致过度退出。
            if not leaving:
                with _scale:
                    if (_alive - _leaving) > _target:
                        _leaving += 1
                        leaving = True
            if leaving:
                log.info("[w%d] 缩容退出", wid)
                return

            try:
                chapter = db.claim_next_chapter()
                if chapter is None:
                    time.sleep(config.WORKER_IDLE_SLEEP)
                    continue

                book_id = chapter["book_id"]
                try:
                    _process_chapter(chapter)
                except Exception as e:  # noqa: BLE001
                    log.exception("[w%d] 章节生成失败 chapter_id=%s", wid, chapter["id"])
                    retries = chapter["retries"] + 1
                    if retries < _max_retries:
                        backoff = _backoff_base * retries
                        log.warning("[w%d] 将在 %.1fs 后重试 (%d/%d) chapter_id=%s",
                                    wid, backoff, retries, _max_retries, chapter["id"])
                        db.requeue_chapter(chapter["id"], retries)
                        time.sleep(backoff)
                    else:
                        db.set_chapter_failed(chapter["id"], str(e))

                db.refresh_book_status(book_id)
            except Exception:  # noqa: BLE001
                log.exception("[w%d] Worker 循环异常", wid)
                time.sleep(config.WORKER_IDLE_SLEEP)
    finally:
        with _scale:
            _alive -= 1
            if leaving:
                _leaving -= 1


def _spawn_worker() -> None:
    """建一个 worker 线程并启动（线程名唯一，登记进 _threads）。"""
    global _next_wid
    with _scale:
        _next_wid += 1
        wid = _next_wid
        t = threading.Thread(target=_worker_loop, args=(wid,), daemon=True,
                             name=f"book-worker-{wid}")
        _threads.append(t)
    t.start()


def alive_count() -> int:
    """当前活动 worker 数（调试/观察用）。"""
    with _scale:
        return _alive


def set_concurrency(n: int) -> None:
    """动态调整并发：扩容立即补线程；缩容靠多余线程自然退出（不打断当前章）。"""
    global _target
    n = max(1, int(n))
    with _scale:
        old = _target
        _target = n
        effective = _alive - _leaving          # 真正"会留下"的线程数
        to_spawn = max(0, n - effective)
        _threads[:] = [t for t in _threads if t.is_alive()]  # 顺手清理已退线程
    if to_spawn:
        log.info("并发调整 %d -> %d（补 %d 线程）", old, n, to_spawn)
        for _ in range(to_spawn):
            _spawn_worker()
    elif n != old:
        log.info("并发调整 %d -> %d（多余线程将自然退出）", old, n)


def apply_settings(s: dict) -> None:
    """应用 UI 保存的全局设置：重试参数即时改，并发数动态伸缩。"""
    global _max_retries, _backoff_base
    _max_retries = int(s.get("max_retries", _max_retries))
    _backoff_base = float(s.get("retry_backoff_base", _backoff_base))
    set_concurrency(int(s.get("worker_concurrency", _target)))


def start() -> None:
    """启动 worker 池（重复调用安全）。初始并发/重试参数取自持久化设置（库值优先）。"""
    global _target, _max_retries, _backoff_base
    if _threads and any(t.is_alive() for t in _threads):
        return
    db.reset_generating_to_pending()
    db.reset_running_books()
    _stop.clear()
    s = db.get_settings()
    _target = s["worker_concurrency"]
    _max_retries = s["max_retries"]
    _backoff_base = s["retry_backoff_base"]
    for _ in range(_target):
        _spawn_worker()
    log.info("Worker 启动（并发=%d）", _target)


def stop() -> None:
    _stop.set()
