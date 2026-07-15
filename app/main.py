"""FastAPI 主程序：Web UI + REST API + 下载。"""
import logging
import os
import re
import tempfile
import zipfile
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    RedirectResponse,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.background import BackgroundTask

from . import config, db, parser, tts, worker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
)
log = logging.getLogger("bookToVoice")

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

_UNSAFE = re.compile(r'[\\/:*?"<>|\s]+')


def _sanitize(name: str) -> str:
    return _UNSAFE.sub("_", name).strip("_") or "untitled"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.get_db()          # 初始化数据库 + 建表
    worker.start()       # 启动串行 worker（含断点续跑）
    log.info("BookToVoice 就绪: 直连 Edge-TTS")
    yield


app = FastAPI(title="BookToVoice", lifespan=lifespan)
app.mount(
    "/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static"
)


# ---------------- 页面 ----------------

@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(
        "index.html", {"request": request, "books": db.list_books(),
                       "voice_map": config.VOICE_MAP}
    )


@app.get("/books/new", response_class=HTMLResponse)
def new_book_form(request: Request):
    return templates.TemplateResponse(
        "new_book.html",
        {
            "request": request,
            "voices": config.VOICE_OPTIONS,
            "default_voice": config.DEFAULT_VOICE,
            "narrate_default": config.NARRATE_TITLE_DEFAULT,
        },
    )


@app.post("/books")
async def create_book(
    name: str = Form(""),
    file: UploadFile = File(...),
    voice: str = Form(""),
    rate: str = Form("+0%"),
    narrate_title: str = Form(""),
):
    raw = await file.read()
    content = parser.decode_bytes(raw)

    book_name = name.strip() or Path(file.filename or "book").stem
    voice = voice.strip() or config.DEFAULT_VOICE
    narrate = narrate_title == "on"

    chapters = parser.parse_chapters(content)
    if not chapters:
        raise HTTPException(400, "未解析到任何章节，请检查 TXT 格式（需含「第X章」等标题）")

    # 保存原始 TXT 便于追溯
    config.INPUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    src = config.INPUT_DIR / f"{_sanitize(book_name)}_{stamp}.txt"
    src.write_text(content, encoding="utf-8")

    book_id = db.create_book(
        book_name, str(src), voice, rate, config.DEFAULT_PITCH, narrate
    )
    db.create_chapters(book_id, chapters)
    db.set_book_total(book_id, len(chapters))
    log.info("新建有声书: id=%s name=%s chapters=%d", book_id, book_name, len(chapters))
    return RedirectResponse(url=f"/books/{book_id}", status_code=303)


@app.get("/books/{book_id}", response_class=HTMLResponse)
def book_detail(request: Request, book_id: int):
    book = db.get_book(book_id)
    if not book:
        raise HTTPException(404, "书籍不存在")
    return templates.TemplateResponse(
        "book_detail.html",
        {
            "request": request,
            "book": book,
            "chapters": db.list_chapters(book_id),
            "progress": db.book_progress(book_id),
            "voices": config.VOICE_OPTIONS,
            "voice_map": config.VOICE_MAP,
        },
    )


# ---------------- API（轮询/操作）----------------

@app.get("/api/books/{book_id}")
def api_book_progress(book_id: int):
    book = db.get_book(book_id)
    if not book:
        raise HTTPException(404, "书籍不存在")
    progress = db.book_progress(book_id)
    chapters = [
        {
            "id": c["id"],
            "idx": c["idx"],
            "title": c["title"],
            "status": c["status"],
            "error": c["error"],
            "audio": bool(c["audio_path"]),
        }
        for c in db.list_chapters(book_id)
    ]
    return {"book": dict(book), "progress": progress, "chapters": chapters}


@app.post("/api/preview")
def preview_tts(text: str = Form(...), voice: str = Form(""), rate: str = Form("+0%")):
    """试听：用指定声音合成一小段 mp3 并返回，供前端播放/下载。"""
    text = (text or "").strip()
    if not text:
        raise HTTPException(400, "试听文本不能为空")
    fd, tmp = tempfile.mkstemp(suffix=".mp3")
    os.close(fd)
    try:
        tts.synthesize(text, Path(tmp), voice or config.DEFAULT_VOICE,
                       rate, None, config.DEFAULT_PITCH)
    except Exception as e:  # noqa: BLE001
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise HTTPException(500, str(e))

    def _cleanup():
        try:
            os.unlink(tmp)
        except OSError:
            pass

    return FileResponse(tmp, media_type="audio/mpeg", filename="preview.mp3",
                        background=BackgroundTask(_cleanup))


@app.post("/books/{book_id}/retry-failed")
def retry_failed(book_id: int):
    if not db.get_book(book_id):
        raise HTTPException(404)
    db.requeue_failed_chapters(book_id)
    db.update_book_status(book_id, "running")
    return RedirectResponse(url=f"/books/{book_id}", status_code=303)


@app.post("/books/{book_id}/pause")
def pause_book(book_id: int):
    if not db.get_book(book_id):
        raise HTTPException(404)
    db.set_book_paused(book_id, True)
    return RedirectResponse(url=f"/books/{book_id}", status_code=303)


@app.post("/books/{book_id}/resume")
def resume_book(book_id: int):
    if not db.get_book(book_id):
        raise HTTPException(404)
    db.set_book_paused(book_id, False)
    db.update_book_status(book_id, "running")
    return RedirectResponse(url=f"/books/{book_id}", status_code=303)


@app.post("/books/{book_id}/chapters/{chapter_id}/regenerate")
def regenerate_chapter(book_id: int, chapter_id: int):
    ch = db.get_chapter(chapter_id)
    if not ch or ch["book_id"] != book_id:
        raise HTTPException(404)
    db.requeue_chapter_by_id(chapter_id)
    db.update_book_status(book_id, "running")
    return RedirectResponse(url=f"/books/{book_id}", status_code=303)


@app.post("/books/{book_id}/settings")
def update_settings(
    book_id: int,
    name: str = Form(""),
    voice: str = Form(""),
    rate: str = Form(""),
    narrate_title: str = Form(""),
):
    if not db.get_book(book_id):
        raise HTTPException(404)
    db.update_book_settings(
        book_id,
        name=name.strip() or None,
        voice=voice.strip() or None,
        rate=rate.strip() or None,
        narrate_title=(narrate_title == "on"),
    )
    return RedirectResponse(url=f"/books/{book_id}", status_code=303)


# ---------------- 下载 ----------------

@app.get("/books/{book_id}/chapters/{chapter_id}/audio")
def download_chapter(book_id: int, chapter_id: int):
    ch = db.get_chapter(chapter_id)
    if not ch or ch["book_id"] != book_id or ch["status"] != "done":
        raise HTTPException(404, "音频未生成")
    p = Path(ch["audio_path"])
    if not p.exists():
        raise HTTPException(404, "文件不存在")
    return FileResponse(str(p), media_type="audio/mpeg", filename=p.name)


@app.get("/books/{book_id}/download")
def download_book(book_id: int):
    book = db.get_book(book_id)
    if not book:
        raise HTTPException(404)
    # 写入临时文件再流式返回，避免大书撑爆内存
    fd, tmp_path = tempfile.mkstemp(suffix=".zip")
    os.close(fd)
    added = 0
    try:
        with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for ch in db.list_chapters(book_id):
                if ch["status"] == "done" and ch["audio_path"]:
                    p = Path(ch["audio_path"])
                    if p.exists():
                        zf.write(p, arcname=p.name)
                        added += 1
        if added == 0:
            raise HTTPException(404, "暂无可下载的章节音频")
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise

    fname = f"{_sanitize(book['name'])}.zip"

    def _cleanup():
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    return FileResponse(
        tmp_path,
        media_type="application/zip",
        filename=fname,
        background=BackgroundTask(_cleanup),
    )
