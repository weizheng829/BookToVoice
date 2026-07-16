"""冒烟测试：多编码解码、章节切分、DB 状态机。

只测 parser + db，不依赖 fastapi/httpx/edge_tts，本地无需装依赖即可跑。

直接运行：  python tests/test_smoke.py
用 pytest：  pip install pytest && pytest tests/
"""
import sys
import tempfile
from pathlib import Path

# 让脚本能从项目根导入 app
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import parser, config


def test_decode_bytes():
    assert parser.decode_bytes("测试".encode("utf-8-sig")) == "测试"
    assert parser.decode_bytes("中文".encode("utf-8")) == "中文"
    assert parser.decode_bytes("简体".encode("gb18030")) == "简体"
    # big5 与 gb18030 字节有重叠，自动探测可能被 gb18030 抢先；仅验证不抛异常
    assert isinstance(parser.decode_bytes("繁體".encode("big5")), str)
    # 坏字节兜底（不抛异常）
    assert isinstance(parser.decode_bytes(b"\xff\xfe\xab\xcd"), str)


def test_parse_chapters():
    sample = (
        "楔子部分这里写一些超过二十个字的序言内容用来测试序言保留逻辑。\n\n"
        "第一章 初入江湖\n\n正文一。\n\n"
        "第002章 雪中\n\n正文二。\n\n"
        "第〇三章 特殊\n\n正文三。\n"
    )
    chapters = parser.parse_chapters(sample)
    titles = [t for t, _ in chapters]
    assert any("序言" in t for t in titles)
    assert any("第一章" in t for t in titles)
    assert any("002" in t for t in titles), "阿拉伯数字章节"
    assert any("〇三" in t for t in titles), "〇 字符识别"
    assert all(b for _, b in chapters), "无空正文章节"


def test_parse_chapters_fallback():
    # 无章节标题 → 按字数分块
    chapters = parser.parse_chapters("一段没有任何章节标题的纯正文文本内容。" * 200)
    assert len(chapters) >= 1


def test_db():
    config.DB_PATH = Path(tempfile.mktemp(suffix=".db"))
    config.OUTPUT_DIR = Path(tempfile.mkdtemp())
    from app import db
    db._conn = None
    db.get_db()

    # engine 列应已移除
    cols = [r[1] for r in db.get_db().execute("PRAGMA table_info(books)").fetchall()]
    assert "engine" not in cols

    bid = db.create_book("测试书", "/x", "zh-CN-XiaoxiaoNeural", "+0%", "+0Hz", True)
    db.create_chapters(bid, [("第一章", "正文一"), ("第二章", "正文二"), ("第三章", "正文三")])
    db.set_book_total(bid, 3)

    ch = db.get_next_pending_chapter()
    assert ch["idx"] == 1
    db.set_chapter_done(ch["id"], "/tmp/1.mp3")
    p = db.book_progress(bid)
    assert p["done"] == 1 and p["total"] == 3 and p["percent"] == 33

    db.refresh_book_status(bid)
    assert db.get_book(bid)["status"] == "running"

    # 全部完成 → done
    for c in db.list_chapters(bid):
        if c["status"] != "done":
            db.set_chapter_done(c["id"], f"/tmp/{c['idx']}.mp3")
    db.refresh_book_status(bid)
    assert db.get_book(bid)["status"] == "done"


def test_db_delete_cascade():
    config.DB_PATH = Path(tempfile.mktemp(suffix=".db"))
    config.OUTPUT_DIR = Path(tempfile.mkdtemp())
    from app import db
    db._conn = None
    db.get_db()

    bid = db.create_book("待删除", "/x/src.txt", "zh-CN-XiaoxiaoNeural", "+0%", "+0Hz", True)
    db.create_chapters(bid, [("第一章", "正文一"), ("第二章", "正文二"), ("第三章", "正文三")])
    db.set_book_total(bid, 3)
    chapters = db.list_chapters(bid)
    db.set_chapter_done(chapters[0]["id"], "/tmp/a/1.mp3")
    db.set_chapter_done(chapters[1]["id"], "/tmp/a/2.mp3")

    assert db.get_book(bid) is not None
    assert len(db.list_chapters(bid)) == 3

    db.delete_book(bid)

    assert db.get_book(bid) is None              # 书已删
    assert db.list_chapters(bid) == []           # 章节由 ON DELETE CASCADE 级联清除
    assert db.book_progress(bid)["total"] == 0   # 无残留


if __name__ == "__main__":
    tests = [test_decode_bytes, test_parse_chapters,
             test_parse_chapters_fallback, test_db, test_db_delete_cascade]
    passed = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
            passed += 1
        except Exception as e:  # noqa: BLE001
            print(f"FAIL  {t.__name__}: {e}")
    print(f"\n{passed}/{len(tests)} passed")
    sys.exit(0 if passed == len(tests) else 1)
