import re
from typing import Any, Optional


TAGGED_SCRIPT_TOKEN_RE = re.compile(r"<\s*([^:：<>]+?)\s*[:：]\s*>")


def speaker_label(value: Any) -> str:
    speaker = str(value or "").strip()
    if not speaker:
        return "NARRATOR"
    if speaker.upper() in {"NARRATOR", "旁白"}:
        return "旁白"
    return speaker


def normalize_speaker(value: Any) -> str:
    speaker = str(value or "").strip()
    if not speaker or speaker.upper() in {"NARRATOR", "旁白"}:
        return "NARRATOR"
    return speaker


def entries_to_tagged_text(entries: list[dict]) -> str:
    lines = []
    current_chapter = object()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        chapter_id = str(entry.get("chapter_id") or "")
        chapter_title = str(entry.get("chapter_title") or "")
        if chapter_id and chapter_id != current_chapter:
            current_chapter = chapter_id
            title_suffix = f" {chapter_title}" if chapter_title else ""
            lines.append(f"# [{chapter_id}]{title_suffix}")
        speaker = speaker_label(entry.get("speaker") or entry.get("type") or "")
        text = str(entry.get("text") or "").strip()
        if not text:
            continue
        instruct = str(entry.get("instruct") or "").strip()
        suffix = f" {{instruct={instruct}}}" if instruct else ""
        lines.append(f"<{speaker}:>{text}{suffix}")
    return "\n".join(lines).strip()


def parse_tagged_script_text(
    content: str,
    *,
    default_instruct: str = "",
    chapter_meta: Optional[dict] = None,
) -> tuple[list[dict], list[dict]]:
    entries: list[dict] = []
    issues: list[dict] = []
    current: Optional[dict] = None

    def make_entry(speaker: str, text: str, instruct: str, line_no: int) -> dict:
        entry = {
            "speaker": normalize_speaker(speaker),
            "text": text.strip(),
            "instruct": instruct.strip(),
            "_line": line_no,
        }
        if chapter_meta:
            entry.update(chapter_meta)
        return entry

    def split_instruct(text: str) -> tuple[str, str]:
        instruct = default_instruct.strip()
        instruct_match = re.search(r"\s*\{instruct=(.*?)\}\s*$", text)
        if instruct_match:
            instruct = instruct_match.group(1).strip()
            text = text[:instruct_match.start()].strip()
        return text.strip(), instruct

    def flush_current() -> None:
        nonlocal current
        if not current:
            return
        current["text"] = str(current.get("text") or "").strip()
        if current["text"]:
            entries.append(current)
        else:
            issues.append({
                "line": current.get("_line") or 0,
                "level": "warning",
                "message": f"说话人 {current.get('speaker') or 'NARRATOR'} 的文本为空，已跳过。",
            })
        current = None

    normalized = str(content or "").replace("\r\n", "\n").replace("\r", "\n")
    for line_no, raw_line in enumerate(normalized.split("\n"), start=1):
        line = raw_line.strip()
        if not line:
            flush_current()
            continue
        if line.startswith("#"):
            flush_current()
            continue

        matches = list(TAGGED_SCRIPT_TOKEN_RE.finditer(line))
        if matches:
            prefix = line[:matches[0].start()].strip()
            if prefix:
                issues.append({
                    "line": line_no,
                    "level": "warning",
                    "message": "标签前的文本已并入上一条，若不是续行请补充 <角色:> 标签。",
                })
                if current is None:
                    current = make_entry("NARRATOR", prefix, default_instruct, line_no)
                else:
                    current["text"] = f"{current.get('text', '')}\n{prefix}".strip()

            for idx, match in enumerate(matches):
                flush_current()
                speaker = match.group(1).strip()
                start = match.end()
                end = matches[idx + 1].start() if idx + 1 < len(matches) else len(line)
                text, instruct = split_instruct(line[start:end].strip())
                current = make_entry(speaker, text, instruct, line_no)
            if len(matches) > 1:
                issues.append({
                    "line": line_no,
                    "level": "info",
                    "message": f"检测到同一行 {len(matches)} 个标签，已拆成多条。",
                })
            continue

        if line.startswith("<") and ":" in line and ">" not in line.split(":", 1)[0]:
            issues.append({
                "line": line_no,
                "level": "warning",
                "message": "这一行看起来像标签但格式不完整，将并入上一条文本。",
            })
        if current is None:
            issues.append({
                "line": line_no,
                "level": "warning",
                "message": "未带 <角色:> 标签的文本已按旁白导入。",
            })
            current = make_entry("NARRATOR", line, default_instruct, line_no)
        else:
            current["text"] = f"{current.get('text', '')}\n{line}".strip()

    flush_current()
    for entry in entries:
        entry.pop("_line", None)
    return entries, issues


def tagged_script_preview(entries: list[dict], issues: list[dict]) -> dict[str, Any]:
    speaker_counts: dict[str, int] = {}
    char_count = 0
    empty_instruct_count = 0
    for entry in entries:
        speaker = str(entry.get("speaker") or "NARRATOR")
        speaker_counts[speaker] = speaker_counts.get(speaker, 0) + 1
        char_count += len(str(entry.get("text") or ""))
        if not str(entry.get("instruct") or "").strip():
            empty_instruct_count += 1

    return {
        "entry_count": len(entries),
        "speaker_count": len(speaker_counts),
        "speakers": [
            {"speaker": speaker, "count": count}
            for speaker, count in sorted(speaker_counts.items(), key=lambda item: (-item[1], item[0]))
        ],
        "char_count": char_count,
        "empty_instruct_count": empty_instruct_count,
        "issues": issues,
    }
