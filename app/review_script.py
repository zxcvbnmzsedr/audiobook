import argparse
import json
import os
import re
import signal
import sys
import time
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from generate_script_chapters import build_chat_model, resolve_model_name, resolve_provider
from json_utils import clean_json_string, repair_json_array, salvage_json_entries
from project import group_into_chunks
from review_prompts import REVIEW_SYSTEM_PROMPT, REVIEW_USER_PROMPT


_cancel_flag = {"stop": False}
CHAPTER_META_FIELDS = ("chapter_id", "chapter_index", "chapter_title")


def _handle_sigterm(signum, frame):
    _cancel_flag["stop"] = True


def emit(event_type: str, **data: Any) -> None:
    print(f"[EVENT] {json.dumps({'type': event_type, 'data': data}, ensure_ascii=False)}", flush=True)


def emit_llm_attempt(label: str, attempt: int, stage: str = "review", expected: str = "array") -> None:
    emit("llm_attempt", label=label, attempt=attempt, stage=stage, expected=expected)


def read_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"Warning: failed to read {path}: {exc}", flush=True)
        return fallback


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def normalize_text(text: str) -> str:
    text = str(text or "").lower()
    text = re.sub(r"[^\w\s]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def check_text_loss(original_entries: list[dict], corrected_entries: list[dict], threshold: float = 0.95) -> dict[str, Any]:
    original_words: list[str] = []
    corrected_words: list[str] = []
    for entry in original_entries:
        original_words.extend(normalize_text(str(entry.get("text") or "")).split())
    for entry in corrected_entries:
        corrected_words.extend(normalize_text(str(entry.get("text") or "")).split())

    if not original_words:
        ratio = 1.0
    else:
        ratio = len(corrected_words) / len(original_words)
    return {
        "passed": ratio >= threshold,
        "original_word_count": len(original_words),
        "corrected_word_count": len(corrected_words),
        "ratio": ratio,
    }


def diff_entries(original: list[dict], corrected: list[dict]) -> dict[str, int]:
    stats = {
        "text_changed": 0,
        "speaker_changed": 0,
        "instruct_changed": 0,
        "entries_original": len(original),
        "entries_corrected": len(corrected),
    }
    for idx in range(min(len(original), len(corrected))):
        if str(original[idx].get("text") or "") != str(corrected[idx].get("text") or ""):
            stats["text_changed"] += 1
        if str(original[idx].get("speaker") or original[idx].get("type") or "") != str(corrected[idx].get("speaker") or corrected[idx].get("type") or ""):
            stats["speaker_changed"] += 1
        if str(original[idx].get("instruct") or "") != str(corrected[idx].get("instruct") or ""):
            stats["instruct_changed"] += 1
    return stats


def is_section_break(text: Any) -> bool:
    stripped = str(text or "").strip()
    if not stripped:
        return True
    if re.match(r"(?i)^chapter\b", stripped):
        return True
    if stripped == stripped.upper() and len(stripped) < 80 and stripped.isascii():
        return True
    if len(stripped) < 80 and stripped[-1:] not in ".!?。！？":
        return True
    return False


def entry_chapter_key(entry: dict) -> tuple[Any, Any, Any]:
    return tuple(entry.get(field) for field in CHAPTER_META_FIELDS)


def merge_consecutive_narrators(entries: list[dict], max_merged_length: int = 800) -> tuple[list[dict], int]:
    if not entries:
        return entries, 0

    merged: list[dict] = []
    merges = 0
    index = 0
    while index < len(entries):
        entry = entries[index]
        speaker = str(entry.get("speaker") or entry.get("type") or "")
        if speaker != "NARRATOR" or is_section_break(entry.get("text", "")):
            merged.append(entry)
            index += 1
            continue

        item = dict(entry)
        combined_text = str(item.get("text") or "")
        instruct = str(item.get("instruct") or "")
        chapter_key = entry_chapter_key(item)
        run_count = 1
        cursor = index + 1
        while cursor < len(entries):
            next_entry = entries[cursor]
            next_speaker = str(next_entry.get("speaker") or next_entry.get("type") or "")
            if next_speaker != "NARRATOR":
                break
            if str(next_entry.get("instruct") or "") != instruct:
                break
            if entry_chapter_key(next_entry) != chapter_key:
                break
            if is_section_break(next_entry.get("text", "")):
                break
            candidate = f"{combined_text} {str(next_entry.get('text') or '')}".strip()
            if len(candidate) > max_merged_length:
                break
            combined_text = candidate
            run_count += 1
            cursor += 1

        item["speaker"] = "NARRATOR"
        item["text"] = combined_text
        item["instruct"] = instruct
        merged.append(item)
        if run_count > 1:
            merges += run_count - 1
        index = cursor

    return merged, merges


def message_to_text(message: Any) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict):
                parts.append(str(part.get("text") or ""))
            else:
                parts.append(str(part))
        return "\n".join(part for part in parts if part)
    return str(content or "")


def _usage_value(details: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in details:
            return details.get(key)
    return None


def log_usage_metadata(label: str, message: Any, stage: str = "review") -> None:
    usage = getattr(message, "usage_metadata", None)
    if usage is None and isinstance(message, dict):
        usage = message.get("usage_metadata")
    if not isinstance(usage, dict):
        return

    input_details = usage.get("input_token_details") or {}
    cache_read = _usage_value(
        input_details,
        "cache_read",
        "cached_tokens",
        "priority_cache_read",
        "flex_cache_read",
    )
    print(
        "[LLM] usage "
        f"label={label} input_tokens={usage.get('input_tokens')} "
        f"output_tokens={usage.get('output_tokens')} total_tokens={usage.get('total_tokens')} "
        f"cache_read={cache_read}",
        flush=True,
    )
    emit(
        "llm_usage",
        label=label,
        stage=stage,
        input_tokens=usage.get("input_tokens"),
        output_tokens=usage.get("output_tokens"),
        total_tokens=usage.get("total_tokens"),
        cache_read=cache_read,
    )


def invoke_review_text(model: Any, system_prompt: str, user_prompt: str, label: str) -> str:
    started_at = time.perf_counter()
    message = model.invoke([SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)])
    log_usage_metadata(label, message)
    text = message_to_text(message).strip()
    emit("llm_stream", label=label, stage="review", done=True, chars=len(text), elapsed_ms=int((time.perf_counter() - started_at) * 1000))
    return text


def extract_review_entries(text: str) -> list[dict] | None:
    json_text = clean_json_string(text)
    if not json_text:
        return None
    entries = repair_json_array(json_text)
    if entries:
        return entries
    return salvage_json_entries(json_text)


def batch_context(batch_num: int, total_batches: int, previous_tail: list[dict] | None, source_context: str | None) -> str:
    parts = [f"Batch {batch_num} of {total_batches}."]
    if previous_tail:
        parts.append("\nPrevious batch ended with:")
        for entry in previous_tail:
            parts.append(json.dumps(entry, ensure_ascii=False))
    if source_context:
        parts.append(f"\nORIGINAL SOURCE TEXT (for reference):\n{source_context}")
    return "\n".join(parts)


def normalize_corrected_entries(corrected: list[dict], original: list[dict]) -> list[dict]:
    original_same_chapter = len({entry_chapter_key(entry) for entry in original}) == 1
    shared_meta = {field: original[0].get(field) for field in CHAPTER_META_FIELDS if original and original[0].get(field) is not None}
    normalized: list[dict] = []

    for idx, raw in enumerate(corrected):
        if not isinstance(raw, dict):
            continue
        speaker = str(raw.get("speaker") or raw.get("type") or "").strip()
        text = str(raw.get("text") or "").strip()
        instruct = str(raw.get("instruct") or "").strip()
        if not text:
            continue
        item = {
            "speaker": "NARRATOR" if speaker.upper() in {"", "NARRATOR", "旁白"} else speaker,
            "text": text,
            "instruct": instruct or "Neutral, even narration.",
        }
        if idx < len(original):
            for field in CHAPTER_META_FIELDS:
                if original[idx].get(field) is not None:
                    item[field] = original[idx].get(field)
            if original[idx].get("pause_after") is not None and len(corrected) == len(original):
                item["pause_after"] = original[idx].get("pause_after")
        elif original_same_chapter:
            item.update(shared_meta)
        normalized.append(item)

    return normalized


def review_batch(
    model: Any,
    batch_entries: list[dict],
    batch_num: int,
    total_batches: int,
    *,
    previous_tail: list[dict] | None,
    source_context: str | None,
    system_prompt: str,
    user_prompt_template: str,
    max_retries: int = 2,
) -> dict[str, Any]:
    context = batch_context(batch_num, total_batches, previous_tail, source_context)
    batch_json = json.dumps(batch_entries, indent=2, ensure_ascii=False)
    user_prompt = user_prompt_template.format(context=context, batch=batch_json)

    last_error = ""
    for attempt in range(max_retries + 1):
        attempt_num = attempt + 1
        label = f"review_batch_{batch_num}_attempt_{attempt_num}"
        emit_llm_attempt(label, attempt_num)
        try:
            text = invoke_review_text(model, system_prompt, user_prompt, label)
            corrected = extract_review_entries(text)
            if not corrected:
                last_error = "model response did not contain a parseable JSON array"
                emit("llm_retry", label=label, attempt=attempt_num, stage="review", error=last_error)
                print(f"  Attempt {attempt_num}: {last_error}", flush=True)
                continue
            corrected = normalize_corrected_entries(corrected, batch_entries)
            if not corrected:
                last_error = "model returned no usable entries"
                emit("llm_retry", label=label, attempt=attempt_num, stage="review", error=last_error)
                print(f"  Attempt {attempt_num}: {last_error}", flush=True)
                continue
            text_loss = check_text_loss(batch_entries, corrected)
            return {
                "entries": corrected,
                "fallback": False,
                "text_loss": text_loss,
                "diff": diff_entries(batch_entries, corrected),
            }
        except Exception as exc:
            last_error = str(exc)
            emit("llm_retry", label=label, attempt=attempt_num, stage="review", error=last_error)
            print(f"  Attempt {attempt_num} failed: {exc}", flush=True)

    return {"entries": None, "fallback": True, "error": last_error or "review failed"}


def make_batches(entries: list[dict], batch_size: int) -> list[list[dict]]:
    batch_size = max(1, int(batch_size or 25))
    batches: list[list[dict]] = []
    current: list[dict] = []
    current_chapter = object()
    for entry in entries:
        chapter_id = entry.get("chapter_id")
        crosses_chapter = current and chapter_id and chapter_id != current_chapter
        if len(current) >= batch_size or crosses_chapter:
            batches.append(current)
            current = []
        current.append(entry)
        current_chapter = chapter_id
    if current:
        batches.append(current)
    return batches


def sync_chunks(workspace_dir: Path, entries: list[dict]) -> Path:
    chunks = group_into_chunks(entries)
    for idx, chunk in enumerate(chunks):
        chunk["id"] = idx
        chunk.setdefault("status", "pending")
        chunk["audio_path"] = None
    chunks_path = workspace_dir / "chunks.json"
    write_json(chunks_path, chunks)
    return chunks_path


def main():
    signal.signal(signal.SIGTERM, _handle_sigterm)

    parser = argparse.ArgumentParser(description="Review and fix annotated audiobook script")
    parser.add_argument("--source", help="Path to original source text for comparison")
    parser.add_argument(
        "--workspace-dir",
        default=os.path.abspath(os.path.join(os.path.dirname(__file__), "..")),
        help="Directory containing annotated_script.json and chunks.json",
    )
    parser.add_argument("--dry-run", action="store_true", help="Plan the review without calling the LLM or writing outputs.")
    args = parser.parse_args()
    workspace_dir = Path(args.workspace_dir).resolve()

    script_path = workspace_dir / "annotated_script.json"
    if not script_path.exists():
        print("Error: annotated_script.json not found. Generate a script first.", flush=True)
        sys.exit(1)

    entries = read_json(script_path, [])
    if not isinstance(entries, list):
        print("Error: annotated_script.json must contain a JSON array.", flush=True)
        sys.exit(1)
    entries = [entry for entry in entries if isinstance(entry, dict)]
    print(f"Loaded {len(entries)} script entries for review", flush=True)

    source_context = None
    if args.source:
        source_path = Path(args.source)
        if source_path.exists():
            source_context = source_path.read_text(encoding="utf-8")
            print(f"Loaded source text: {len(source_context)} chars", flush=True)
        else:
            print(f"Warning: Source file not found: {args.source}", flush=True)

    app_dir = Path(__file__).resolve().parent
    config = read_json(app_dir / "config.json", {})
    prompts_config = config.get("prompts", {}) if isinstance(config, dict) else {}
    review_sys = prompts_config.get("review_system_prompt") or REVIEW_SYSTEM_PROMPT
    review_usr = prompts_config.get("review_user_prompt") or REVIEW_USER_PROMPT
    generation_config = config.get("generation", {}) if isinstance(config, dict) else {}

    batch_size = int(generation_config.get("review_batch_size") or 25)
    merge_narrators_enabled = bool(generation_config.get("merge_narrators", False))
    model_name = resolve_model_name(config)
    provider = resolve_provider(config)
    batches = make_batches(entries, batch_size)
    print(f"Using model: {model_name} ({provider})", flush=True)
    print(f"Split into {len(batches)} batches of up to {batch_size} entries", flush=True)
    print("Review engine: lightweight deterministic batch loop.", flush=True)
    emit(
        "init",
        task="review",
        entry_count=len(entries),
        batch_count=len(batches),
        batch_size=batch_size,
        model=model_name,
        provider=provider,
        has_source=bool(source_context),
    )

    if args.dry_run:
        print("Dry run: no model calls and no files written.", flush=True)
        print("Task review dry-run completed successfully.", flush=True)
        return

    try:
        model = build_chat_model(config)
    except Exception as exc:
        print(f"Error: failed to initialize review model: {exc}", flush=True)
        sys.exit(1)

    all_corrected: list[dict] = []
    total_stats = {
        "text_changed": 0,
        "speaker_changed": 0,
        "instruct_changed": 0,
        "entries_added": 0,
        "entries_removed": 0,
        "batches_failed": 0,
    }
    previous_tail: list[dict] | None = None

    for idx, batch in enumerate(batches, start=1):
        if _cancel_flag["stop"]:
            emit("cancelled", task="review", reason="cancel requested", completed_batches=idx - 1, total_batches=len(batches))
            print("Review cancelled.", flush=True)
            sys.exit(0)
        print(f"\nReviewing batch {idx}/{len(batches)} ({len(batch)} entries)...", flush=True)
        emit("review_batch_start", batch=idx, total=len(batches), entries=len(batch))
        result = review_batch(
            model,
            batch,
            idx,
            len(batches),
            previous_tail=previous_tail,
            source_context=source_context,
            system_prompt=review_sys,
            user_prompt_template=review_usr,
        )
        corrected = result.get("entries") if isinstance(result, dict) else None
        if corrected is None:
            print(f"  FAILED - keeping original entries for batch {idx}: {result.get('error') if isinstance(result, dict) else ''}", flush=True)
            all_corrected.extend(batch)
            total_stats["batches_failed"] += 1
            previous_tail = batch[-2:] if len(batch) >= 2 else batch
            emit(
                "review_batch_done",
                batch=idx,
                total=len(batches),
                entries=len(batch),
                fallback=True,
                error=result.get("error") if isinstance(result, dict) else "",
                failed_batches=total_stats["batches_failed"],
            )
            continue

        text_loss = result.get("text_loss") or {}
        if not text_loss.get("passed", True):
            ratio = float(text_loss.get("ratio") or 0)
            print(f"  WARNING: text loss detected; word ratio {ratio:.2f}. Keeping original entries.", flush=True)
            all_corrected.extend(batch)
            total_stats["batches_failed"] += 1
            previous_tail = batch[-2:] if len(batch) >= 2 else batch
            emit(
                "review_batch_done",
                batch=idx,
                total=len(batches),
                entries=len(batch),
                fallback=True,
                error="text loss detected",
                text_loss_ratio=ratio,
                failed_batches=total_stats["batches_failed"],
            )
            continue

        stats = result.get("diff") or {}
        entry_diff = len(corrected) - len(batch)
        if entry_diff > 0:
            total_stats["entries_added"] += entry_diff
        elif entry_diff < 0:
            total_stats["entries_removed"] += abs(entry_diff)
        total_stats["text_changed"] += int(stats.get("text_changed") or 0)
        total_stats["speaker_changed"] += int(stats.get("speaker_changed") or 0)
        total_stats["instruct_changed"] += int(stats.get("instruct_changed") or 0)
        changes = int(stats.get("text_changed") or 0) + int(stats.get("speaker_changed") or 0) + int(stats.get("instruct_changed") or 0)
        if changes or entry_diff:
            print(
                f"  Changes: {stats.get('text_changed', 0)} text, "
                f"{stats.get('speaker_changed', 0)} speaker, "
                f"{stats.get('instruct_changed', 0)} instruct, entry_delta={entry_diff}",
                flush=True,
            )
        else:
            print("  No changes", flush=True)

        all_corrected.extend(corrected)
        previous_tail = corrected[-2:] if len(corrected) >= 2 else corrected
        emit(
            "review_batch_done",
            batch=idx,
            total=len(batches),
            entries=len(corrected),
            fallback=False,
            text_changed=int(stats.get("text_changed") or 0),
            speaker_changed=int(stats.get("speaker_changed") or 0),
            instruct_changed=int(stats.get("instruct_changed") or 0),
            entry_delta=entry_diff,
            total_changes=changes + abs(entry_diff),
            failed_batches=total_stats["batches_failed"],
        )

    narrator_merges = 0
    if merge_narrators_enabled:
        before = len(all_corrected)
        all_corrected, narrator_merges = merge_consecutive_narrators(all_corrected, max_merged_length=800)
        if narrator_merges:
            print(f"\nPost-processing: merged {narrator_merges} narrator entries ({before} -> {len(all_corrected)})", flush=True)
    else:
        print("\nNarrator merging: disabled (enable in Setup > Advanced)", flush=True)

    write_json(script_path, all_corrected)
    chunks_path = sync_chunks(workspace_dir, all_corrected)
    total_changes = (
        total_stats["text_changed"]
        + total_stats["speaker_changed"]
        + total_stats["instruct_changed"]
        + total_stats["entries_added"]
        + total_stats["entries_removed"]
        + narrator_merges
    )

    print(f"\n{'=' * 60}", flush=True)
    print(f"Review complete: {len(entries)} -> {len(all_corrected)} entries", flush=True)
    print(f"  Text changed:    {total_stats['text_changed']}", flush=True)
    print(f"  Speaker changed: {total_stats['speaker_changed']}", flush=True)
    print(f"  Instruct changed:{total_stats['instruct_changed']}", flush=True)
    print(f"  Entries added:   {total_stats['entries_added']}", flush=True)
    print(f"  Entries removed: {total_stats['entries_removed']}", flush=True)
    print(f"  Narrators merged:{narrator_merges}", flush=True)
    if total_stats["batches_failed"]:
        print(f"  Batches failed:  {total_stats['batches_failed']}", flush=True)
    print(f"  Total changes:   {total_changes}", flush=True)
    print(f"{'=' * 60}", flush=True)
    print(f"Output saved to: {script_path}", flush=True)
    print(f"Chunks rebuilt at: {chunks_path}", flush=True)
    emit(
        "done",
        task="review",
        input_entries=len(entries),
        output_entries=len(all_corrected),
        total_changes=total_changes,
        batches=len(batches),
        batches_failed=total_stats["batches_failed"],
        narrator_merges=narrator_merges,
        chunks_path=str(chunks_path),
    )
    print("Task review completed successfully.", flush=True)


if __name__ == "__main__":
    main()
