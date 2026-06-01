import re
from typing import Any


CHINESE_NUMBER = r"零〇一二两三四五六七八九十百千万亿壹贰叁肆伍陆柒捌玖拾佰仟\d０-９"
TITLE_SEPARATOR = r"[：:、.．。\-—－]"
STRONG_SENTENCE_ENDINGS = set("。！？!?；;")

MARKDOWN_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s*")
BRACKETED_TITLE_RE = re.compile(r"^[【\[]\s*(.*?)\s*[】\]]$")
DECORATION_RE = re.compile(r"^[\s\-=*_~·•—－]+$")

CHINESE_CHAPTER_RE = re.compile(
    r"^"
    r"(?:(?:第\s*)?[" + CHINESE_NUMBER + r"]+\s*[卷集部篇]\s*)?"
    r"(?:第\s*)?[" + CHINESE_NUMBER + r"]+\s*[章节回幕场卷集部篇]"
    r"(?:(?:\s*" + TITLE_SEPARATOR + r"\s*|\s+).{0,70})?"
    r"$",
)
CHINESE_SECTION_RE = re.compile(
    r"^"
    r"(?:第\s*)?[" + CHINESE_NUMBER + r"]+\s*节"
    r"(?:(?:\s*" + TITLE_SEPARATOR + r"\s*|\s+).{0,70})?"
    r"$",
)
CHINESE_VOLUME_RE = re.compile(
    r"^(?:卷|集|部|篇)\s*[" + CHINESE_NUMBER + r"]+"
    r"(?:(?:\s*" + TITLE_SEPARATOR + r"\s*|\s+).{0,70})?$"
)
NUMBERED_TITLE_RE = re.compile(
    r"^(?:[" + CHINESE_NUMBER + r"]+)\s*[、.．:：\-—－]\s*\S.{0,70}$"
)
ENGLISH_TITLE_RE = re.compile(
    r"^(?:"
    r"(?:chapter|chap\.?)\s*\d+"
    r"|(?:book|volume|vol\.?|part)\s*\d+"
    r"|prologue|epilogue|interlude|appendix"
    r")(?:(?:\s*" + TITLE_SEPARATOR + r"\s*|\s+).{0,70})?$",
    re.IGNORECASE,
)
SPECIAL_TITLE_RE = re.compile(
    r"^(?:"
    r"序章|楔子|引子|前言|序言|正文|尾声|终章|后记"
    r"|外传|外篇|番外(?:篇|章|卷)?(?:\s*(?:第\s*)?[" + CHINESE_NUMBER + r"]+\s*(?:[章节回幕场])?)?"
    r")"
    r"(?:(?:\s*" + TITLE_SEPARATOR + r"\s*|\s+).{0,70})?$"
)


def safe_chapter_filename(index: int) -> str:
    return f"chapter_{index:04d}.txt"


def normalize_title_candidate(line: str) -> str:
    title = str(line or "").strip()
    title = title.lstrip("\ufeff").replace("\u3000", " ").strip()
    title = MARKDOWN_HEADING_RE.sub("", title).strip()
    bracketed = BRACKETED_TITLE_RE.match(title)
    if bracketed:
        title = bracketed.group(1).strip()
    return re.sub(r"\s+", " ", title)


def parse_chapter_title(line: str) -> str | None:
    title = normalize_title_candidate(line)
    if not title or len(title) > 100 or DECORATION_RE.match(title):
        return None
    if title[-1] in STRONG_SENTENCE_ENDINGS:
        return None
    if (
        CHINESE_CHAPTER_RE.match(title)
        or CHINESE_SECTION_RE.match(title)
        or CHINESE_VOLUME_RE.match(title)
        or NUMBERED_TITLE_RE.match(title)
        or ENGLISH_TITLE_RE.match(title)
        or SPECIAL_TITLE_RE.match(title)
    ):
        return title
    return None


def looks_like_chapter_title(line: str) -> bool:
    return parse_chapter_title(line) is not None


def is_volume_title(title: str) -> bool:
    value = normalize_title_candidate(title)
    if not value:
        return False
    if re.search(r"[章节回节幕场]", value):
        return False
    return bool(
        re.search(r"[卷集部篇]", value)
        or re.match(r"^(?:book|volume|vol\.?|part)\s*\d+", value, re.IGNORECASE)
    )


def has_body_after_heading(lines: list[str], heading_line_count: int) -> bool:
    return any(line.strip() for line in lines[max(heading_line_count, 0):])


def split_text_into_chapters(text: str, *, default_title: str = "全文") -> list[dict[str, Any]]:
    """Split a plain-text novel into chapter records using deterministic title rules."""
    normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.split("\n")
    chapters: list[dict[str, Any]] = []
    current_title = "正文"
    current_lines: list[str] = []
    current_heading_line_count = 0
    current_is_volume = False
    current_start_line = 1
    saw_heading = False
    pending_volume_titles: list[str] = []
    pending_volume_lines: list[str] = []
    pending_volume_start_line = 1

    def push_current() -> None:
        content = "\n".join(current_lines).strip()
        if not content:
            return
        index = len(chapters) + 1
        chapters.append(
            {
                "chapter_id": f"chapter_{index:04d}",
                "index": index,
                "title": current_title,
                "filename": safe_chapter_filename(index),
                "path": "",
                "char_count": len(content),
                "start_line": current_start_line,
                "content": content,
            }
        )

    for line_no, line in enumerate(lines, start=1):
        stripped = line.strip()
        parsed_title = parse_chapter_title(stripped)
        if parsed_title:
            if current_lines:
                if (
                    saw_heading
                    and current_is_volume
                    and not has_body_after_heading(current_lines, current_heading_line_count)
                ):
                    if not pending_volume_lines:
                        pending_volume_start_line = current_start_line
                    pending_volume_titles.append(current_title)
                    pending_volume_lines.extend(current_lines)
                elif saw_heading or any(part.strip() for part in current_lines):
                    if not saw_heading and current_title == "正文":
                        current_title = "前言"
                    push_current()
            saw_heading = True
            current_is_volume = is_volume_title(parsed_title)
            if pending_volume_titles:
                current_title = " / ".join([*pending_volume_titles, parsed_title])
                current_lines = [*pending_volume_lines, stripped]
                current_heading_line_count = len(pending_volume_lines) + 1
                current_start_line = pending_volume_start_line
                pending_volume_titles = []
                pending_volume_lines = []
            else:
                current_title = parsed_title
                current_lines = [stripped]
                current_heading_line_count = 1
                current_start_line = line_no
        else:
            current_lines.append(line)

    push_current()

    if not saw_heading and chapters:
        chapters[0]["title"] = default_title

    return chapters
