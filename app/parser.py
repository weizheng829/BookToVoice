"""TXT → 章节切分；多编码读取。"""
import re

# 中文数字（支持「一二三…十百千万两」与阿拉伯数字，含〇）
_CN_NUM = r"[0-9零〇一二三四五六七八九十百千万两]+"
# 行首：第X章/节/回/卷/篇  |  Chapter N  |  N、 / N. / N．
_CHAPTER_RE = re.compile(
    r"^[ \t　]*"
    r"(?:第\s*(" + _CN_NUM + r")\s*[章节回卷篇]"   # 第123章
    r"|Chapter\s+(\d+)"                               # Chapter 1
    r"|(\d{1,4})\s*[、\.．]"                          # 1、 / 1.
    r")"
    r"[ \t　:：、\.．]*([^\n]*)$",
    re.MULTILINE,
)


def decode_bytes(raw: bytes) -> str:
    """多编码读取 TXT：严格解码优先，全部失败用 gb18030 容错兜底。

    顺序：utf-8-sig（带 BOM）→ utf-8 → gb18030 → gbk → big5（繁体）→ utf-16。
    覆盖常见中文 TXT 编码；坏字节以 U+FFFD 替换，不致整体失败。
    """
    for enc in ("utf-8-sig", "utf-8", "gb18030", "gbk", "big5", "utf-16"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("gb18030", errors="replace")


def parse_chapters(content: str) -> list[tuple[str, str]]:
    """返回 [(title, body), ...]，已剔除空正文章节。

    匹配不到任何章节标题时，退化为按字数分块。
    """
    content = content.replace("\r\n", "\n").replace("\r", "\n")
    matches = list(_CHAPTER_RE.finditer(content))

    if not matches:
        return _fallback_split(content)

    chapters: list[tuple[str, str]] = []

    # 标题之前的序言
    preface = content[: matches[0].start()].strip()
    if preface and len(preface) > 20:
        chapters.append(("序言", preface))

    for i, m in enumerate(matches):
        cn, en, num, rest = m.group(1), m.group(2), m.group(3), (m.group(4) or "").strip()
        if cn:
            title = f"第{cn}章"
        elif en:
            title = f"Chapter {en}"
        else:
            title = f"第{num}章"
        if rest:
            title = f"{title} {rest}"
        body_start = m.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
        body = content[body_start:body_end].strip()
        if body:
            chapters.append((title, body))

    return chapters


def _fallback_split(content: str, size: int = 3000) -> list[tuple[str, str]]:
    content = content.strip()
    if not content:
        return []
    parts = []
    total = (len(content) + size - 1) // size
    for i in range(total):
        chunk = content[i * size:(i + 1) * size]
        if chunk.strip():
            parts.append((f"第{i + 1}部分", chunk))
    return parts
