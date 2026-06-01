import os
import sys
import gc
import json
import shutil
import logging
import asyncio
import tempfile
import io
import platform
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, BackgroundTasks
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
import re
import time
import threading
import zipfile
import subprocess
import aiofiles
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import quote, urlencode
import xml.etree.ElementTree as ET

# Import ProjectManager
from project import MAX_CHUNK_CHARS, ProjectManager, group_into_chunks
from default_prompts import load_default_prompts
from review_prompts import load_review_prompts
from hf_utils import fetch_builtin_manifest, download_builtin_adapter, is_adapter_downloaded
from chapter_splitter import safe_chapter_filename, split_text_into_chapters
from tagged_script import entries_to_tagged_text, parse_tagged_script_text, tagged_script_preview
from module_manager import all_module_statuses, install_huggingface_snapshot, module_definition, module_status

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("VocStudioUI")

app = FastAPI(title="Voc Studio Audiobook")

# Paths
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(BASE_DIR)
APP_NAME = "Voc Studio"
DATA_DIR = os.path.abspath(os.environ.get("VOC_STUDIO_DATA_DIR") or ROOT_DIR)
CONFIG_DIR = os.path.join(DATA_DIR, "config") if DATA_DIR != ROOT_DIR else BASE_DIR
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")
VOICES_PATH = os.path.join(DATA_DIR, "voices.json")
VOICE_CONFIG_PATH = os.path.join(DATA_DIR, "voice_config.json")
SCRIPT_PATH = os.path.join(DATA_DIR, "annotated_script.json")
AUDIOBOOK_PATH = os.path.join(DATA_DIR, "cloned_audiobook.mp3")
M4B_PATH = os.path.join(DATA_DIR, "audiobook.m4b")
UPLOADS_DIR = os.path.join(DATA_DIR, "uploads") if DATA_DIR != ROOT_DIR else os.path.join(BASE_DIR, "uploads")
SCRIPTS_DIR = os.path.join(DATA_DIR, "scripts")
CHUNKS_PATH = os.path.join(DATA_DIR, "chunks.json")
BOOKS_DIR = os.path.join(DATA_DIR, "books")
BOOKS_MANIFEST_PATH = os.path.join(BOOKS_DIR, "manifest.json")
DESIGNED_VOICES_DIR = os.path.join(DATA_DIR, "designed_voices")
CLONE_VOICES_DIR = os.path.join(DATA_DIR, "clone_voices")
LORA_MODELS_DIR = os.path.join(DATA_DIR, "lora_models")
LORA_DATASETS_DIR = os.path.join(DATA_DIR, "lora_datasets")
BUILTIN_LORA_DIR = os.path.join(DATA_DIR, "builtin_lora")
BUILTIN_LORA_MANIFEST = os.path.join(BUILTIN_LORA_DIR, "manifest.json")
DATASET_BUILDER_DIR = os.path.join(DATA_DIR, "dataset_builder")
CACHE_DIR = os.path.join(DATA_DIR, "cache")
VOLCENGINE_VOICE_CACHE_PATH = os.path.join(CACHE_DIR, "volcengine_voices.json")
VOLCENGINE_VOICE_CACHE_TTL_SECONDS = 24 * 60 * 60
VOLCENGINE_VOICE_DOC_LIBRARY_ID = 6561
VOLCENGINE_VOICE_DOC_DOCUMENT_ID = 1257544

os.makedirs(CONFIG_DIR, exist_ok=True)
os.makedirs(UPLOADS_DIR, exist_ok=True)
os.makedirs(SCRIPTS_DIR, exist_ok=True)
os.makedirs(BOOKS_DIR, exist_ok=True)
os.makedirs(CACHE_DIR, exist_ok=True)
os.makedirs(DESIGNED_VOICES_DIR, exist_ok=True)
os.makedirs(CLONE_VOICES_DIR, exist_ok=True)
os.makedirs(LORA_MODELS_DIR, exist_ok=True)
os.makedirs(LORA_DATASETS_DIR, exist_ok=True)
os.makedirs(DATASET_BUILDER_DIR, exist_ok=True)

if not os.path.exists(BUILTIN_LORA_MANIFEST):
    source_manifest = os.path.join(ROOT_DIR, "builtin_lora", "manifest.json")
    if os.path.exists(source_manifest):
        os.makedirs(BUILTIN_LORA_DIR, exist_ok=True)
        try:
            shutil.copy2(source_manifest, BUILTIN_LORA_MANIFEST)
        except OSError as exc:
            logger.warning("Failed to seed built-in LoRA manifest: %s", exc)

# Mount static files with absolute path
STATIC_DIR = os.path.join(BASE_DIR, "static")
os.makedirs(STATIC_DIR, exist_ok=True)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.mount("/books", StaticFiles(directory=BOOKS_DIR), name="books")

# Create voicelines directory if it doesn't exist to prevent startup error
VOICELINES_DIR = os.path.join(DATA_DIR, "voicelines")
os.makedirs(VOICELINES_DIR, exist_ok=True)
app.mount("/voicelines", StaticFiles(directory=VOICELINES_DIR), name="voicelines")

# Designed voices directory for voice designer feature
app.mount("/designed_voices", StaticFiles(directory=DESIGNED_VOICES_DIR), name="designed_voices")

# Clone voices directory for user-uploaded reference audio
app.mount("/clone_voices", StaticFiles(directory=CLONE_VOICES_DIR), name="clone_voices")

# LoRA models directory for trained adapter test audio
app.mount("/lora_models", StaticFiles(directory=LORA_MODELS_DIR), name="lora_models")

# Built-in LoRA adapters directory
os.makedirs(BUILTIN_LORA_DIR, exist_ok=True)
app.mount("/builtin_lora", StaticFiles(directory=BUILTIN_LORA_DIR), name="builtin_lora")

# Dataset builder directory for preview audio
app.mount("/dataset_builder", StaticFiles(directory=DATASET_BUILDER_DIR), name="dataset_builder")

def _now_iso():
    return datetime.now(timezone.utc).isoformat()

def _is_legacy_json_generation_prompt(system_prompt: Any, user_prompt: Any) -> bool:
    combined = f"{system_prompt or ''}\n{user_prompt or ''}".casefold()
    if "output only valid json arrays" in combined:
        return True
    return '"speaker"' in combined and '"text"' in combined and '"instruct"' in combined

def _is_cache_unfriendly_generation_prompt(user_prompt: Any) -> bool:
    text = str(user_prompt or "")
    if "CACHE_FRIENDLY_ANNOTATION_PROMPT_V1" in text:
        return False
    first_dynamic = min(
        (idx for token in ("{context}", "{character_book}", "{chunk}", "{chapter_content}", "{source_text}") if (idx := text.find(token)) >= 0),
        default=-1,
    )
    first_task = text.find("任务")
    return first_dynamic >= 0 and (first_task < 0 or first_dynamic < first_task)

def _is_legacy_english_generation_prompt(system_prompt: Any, user_prompt: Any) -> bool:
    combined = f"{system_prompt or ''}\n{user_prompt or ''}"
    return (
        "You are an audiobook script adapter and annotator." in combined
        and "The novel is the source of truth." in combined
        and "TTS-friendly text." in combined
    )

def _is_legacy_english_review_prompt(system_prompt: Any, user_prompt: Any) -> bool:
    combined = f"{system_prompt or ''}\n{user_prompt or ''}"
    return (
        "You are a script reviewer for an audiobook TTS pipeline." in combined
        and "CRITICAL RULES" in combined
        and "SCRIPT ENTRIES TO REVIEW" in combined
    )

def _ensure_tagged_generation_prompts(prompts: Any) -> dict:
    prompts = dict(prompts) if isinstance(prompts, dict) else {}
    sys_prompt, usr_prompt = load_default_prompts()
    if (
        not prompts.get("system_prompt")
        or not prompts.get("user_prompt")
        or _is_legacy_json_generation_prompt(prompts.get("system_prompt"), prompts.get("user_prompt"))
        or _is_cache_unfriendly_generation_prompt(prompts.get("user_prompt"))
        or _is_legacy_english_generation_prompt(prompts.get("system_prompt"), prompts.get("user_prompt"))
    ):
        prompts["system_prompt"] = sys_prompt
        prompts["user_prompt"] = usr_prompt
    return prompts

def _ensure_review_prompts(prompts: Any) -> dict:
    prompts = dict(prompts) if isinstance(prompts, dict) else {}
    if (
        not prompts.get("review_system_prompt")
        or not prompts.get("review_user_prompt")
        or _is_legacy_english_review_prompt(prompts.get("review_system_prompt"), prompts.get("review_user_prompt"))
    ):
        try:
            rev_sys, rev_usr = load_review_prompts()
            prompts["review_system_prompt"] = rev_sys
            prompts["review_user_prompt"] = rev_usr
        except RuntimeError:
            pass
    return prompts

LEGACY_GENERATION_KEYS = {
    "agent_model",
    "agent_provider",
    "enable_review_agent_planning",
    "scout_chunk_size",
    "enable_emotion_enricher",
    "enable_coherence_checker",
}

def _normalize_generation_config(generation: Any, llm: Optional[dict] = None) -> dict:
    generation = dict(generation) if isinstance(generation, dict) else {}
    llm = llm if isinstance(llm, dict) else {}
    legacy_model = generation.get("agent_model")
    generation.pop("engine", None)
    for key in LEGACY_GENERATION_KEYS:
        generation.pop(key, None)
    if not generation.get("model_name"):
        generation["model_name"] = legacy_model or llm.get("model_name") or "claude-opus-4-7"
    return generation

def _sanitize_book_id(name: str) -> str:
    name = re.sub(r'[^\w\- ]', '', name).strip()
    name = re.sub(r'\s+', '_', name)
    return name.lower() or "book"

def _load_books_manifest():
    if os.path.exists(BOOKS_MANIFEST_PATH):
        try:
            with open(BOOKS_MANIFEST_PATH, "r", encoding="utf-8") as f:
                manifest = json.load(f)
            manifest.setdefault("current_book_id", None)
            manifest.setdefault("books", [])
            return manifest
        except (json.JSONDecodeError, ValueError):
            logger.warning("books/manifest.json is invalid; starting with an empty book manifest")
    return {"current_book_id": None, "books": []}

def _save_books_manifest(manifest):
    os.makedirs(BOOKS_DIR, exist_ok=True)
    with open(BOOKS_MANIFEST_PATH, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

def _book_dir(book_id: str) -> str:
    return os.path.join(BOOKS_DIR, book_id)

def _safe_book_import_path(book_dir: str, name: str) -> Optional[str]:
    path = os.path.normpath(os.path.join(book_dir, name))
    root = os.path.abspath(book_dir)
    if os.path.abspath(path).startswith(root + os.sep):
        return path
    return None

def _find_book(manifest, book_id: str):
    return next((book for book in manifest.get("books", []) if book.get("id") == book_id), None)

def _unique_book_id(manifest, title: str) -> str:
    base = _sanitize_book_id(title)
    existing = {book.get("id") for book in manifest.get("books", [])}
    candidate = base
    suffix = 2
    while candidate in existing:
        candidate = f"{base}_{suffix}"
        suffix += 1
    return candidate

def _create_book(title: str, source_filename: str = "", select: bool = True):
    manifest = _load_books_manifest()
    book_id = _unique_book_id(manifest, title)
    now = _now_iso()
    book = {
        "id": book_id,
        "title": title.strip() or book_id,
        "source_filename": source_filename,
        "created_at": now,
        "updated_at": now,
    }
    manifest["books"].append(book)
    if select:
        manifest["current_book_id"] = book_id
    os.makedirs(os.path.join(_book_dir(book_id), "source"), exist_ok=True)
    os.makedirs(os.path.join(_book_dir(book_id), "voicelines"), exist_ok=True)
    _save_books_manifest(manifest)
    return book

def _current_book():
    manifest = _load_books_manifest()
    book_id = manifest.get("current_book_id")
    if not book_id:
        return None
    return _find_book(manifest, book_id)

def _touch_book(book_id: str, **fields):
    manifest = _load_books_manifest()
    book = _find_book(manifest, book_id)
    if book:
        book.update(fields)
        book["updated_at"] = _now_iso()
        _save_books_manifest(manifest)
    return book

def _ensure_current_book():
    book = _current_book()
    if not book:
        raise HTTPException(status_code=400, detail="No book selected. Create or select a book first.")
    return book

def _current_book_dir():
    return _book_dir(_ensure_current_book()["id"])

def _project_manager_for_current_book():
    return ProjectManager(_current_book_dir())

def _current_script_path():
    return os.path.join(_current_book_dir(), "annotated_script.json")

def _current_chunks_path():
    return os.path.join(_current_book_dir(), "chunks.json")

def _current_voice_config_path():
    return os.path.join(_current_book_dir(), "voice_config.json")

def _current_state_path():
    return os.path.join(_current_book_dir(), "state.json")

def _current_script_generation_state_path():
    return os.path.join(_current_book_dir(), "script_generation_state.json")

def _current_character_analysis_state_path():
    return os.path.join(_current_book_dir(), "character_analysis_state.json")

def _current_chapter_memory_path():
    return os.path.join(_current_book_dir(), "chapter_memory.json")

def _current_script_issues_path():
    return os.path.join(_current_book_dir(), "script_issues.json")

def _current_story_bible_path():
    return os.path.join(_current_book_dir(), "story_bible.json")

def _current_generation_snapshots_dir():
    return os.path.join(_current_book_dir(), "generation_snapshots")

def _current_generation_snapshot_state_path():
    return os.path.join(_current_generation_snapshots_dir(), "latest.json")

def _current_chapters_dir():
    return os.path.join(_current_book_dir(), "chapters")

def _current_chapters_manifest_path():
    return os.path.join(_current_chapters_dir(), "manifest.json")

def _current_audiobook_path():
    return os.path.join(_current_book_dir(), "cloned_audiobook.mp3")

def _current_m4b_path():
    return os.path.join(_current_book_dir(), "audiobook.m4b")

def _current_cover_path():
    return os.path.join(_current_book_dir(), "m4b_cover.jpg")

def _safe_chapter_filename(index: int) -> str:
    return safe_chapter_filename(index)

def _audio_file_exists(book_dir: str, audio_path: Optional[str]) -> bool:
    if not audio_path:
        return False
    path = audio_path if os.path.isabs(audio_path) else os.path.join(book_dir, audio_path)
    return os.path.exists(path)

def _book_relative_path(book_dir: str, path: str) -> Optional[str]:
    try:
        rel_path = os.path.relpath(path, book_dir)
    except ValueError:
        return None
    if rel_path.startswith("..") or os.path.isabs(rel_path):
        return None
    return rel_path.replace(os.sep, "/")

def _resolve_book_audio_path(book_dir: str, audio_path: Any) -> Optional[str]:
    value = str(audio_path or "").strip()
    if not value or os.path.isabs(value):
        return None
    path = os.path.abspath(os.path.join(book_dir, value))
    root = os.path.abspath(book_dir)
    if path != root and not path.startswith(root + os.sep):
        return None
    return path

def _remove_unreferenced_audio_files(book_dir: str, candidate_paths: list[Any], live_chunks: Optional[list[dict]] = None) -> list[str]:
    live_chunks = live_chunks if live_chunks is not None else _read_json_list(os.path.join(book_dir, "chunks.json"))
    live_paths = {
        str(chunk.get("audio_path") or "").strip()
        for chunk in live_chunks
        if isinstance(chunk, dict) and str(chunk.get("audio_path") or "").strip()
    }
    removed: list[str] = []
    seen: set[str] = set()
    for candidate in candidate_paths:
        candidate_value = str(candidate or "").strip()
        if not candidate_value or candidate_value in live_paths or candidate_value in seen:
            continue
        seen.add(candidate_value)
        path = _resolve_book_audio_path(book_dir, candidate_value)
        if not path or not os.path.exists(path):
            continue
        rel_path = _book_relative_path(book_dir, path)
        if not rel_path:
            continue
        if not (rel_path.startswith("voicelines/") or rel_path.startswith("chapter_audio/")):
            continue
        try:
            os.remove(path)
            removed.append(rel_path)
        except OSError as exc:
            logger.warning("Failed to remove unreferenced audio file %s: %s", path, exc)
    return removed

NARRATOR_NAMES = {"NARRATOR", "旁白"}
DASHSCOPE_QWEN3_FLASH_MODEL = "qwen3-tts-flash"
DASHSCOPE_QWEN3_INSTRUCT_MODEL = "qwen3-tts-instruct-flash"
DASHSCOPE_QWEN3_FLASH_ONLY_VOICES = {
    "Jennifer", "Ryan", "Katerina", "Aiden", "Bodega", "Sonrisa",
    "Alek", "Dolce", "Sohee", "Ono Anna", "Lenn", "Emilien",
    "Andre", "Radio Gol", "Jada", "Dylan", "Li", "Marcus",
    "Roy", "Peter", "Sunny", "Eric", "Rocky", "Kiki",
}
MAX_CHARACTER_TRAITS_CHARS = 320
MAX_VOICE_PROFILE_CHARS = 120
MAX_NARRATOR_STYLE_CHARS = 120
MAX_KEY_TERMS = 120

def _normalize_aliases(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        value = [value]
    aliases = []
    seen = set()
    for item in value:
        alias = str(item or "").strip()
        if not alias:
            continue
        key = alias.casefold()
        if key in seen:
            continue
        seen.add(key)
        aliases.append(alias)
    return aliases

def _character_traits(value: Any) -> str:
    if isinstance(value, list):
        return "、".join(str(item).strip() for item in value if str(item).strip())
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return str(value or "").strip()

def _split_profile_sentences(text: str) -> list[str]:
    text = re.sub(r"\s+", "", str(text or "").strip())
    if not text:
        return []
    parts = re.split(r"[；;。！？!?]\s*", text)
    return [part.strip(" ，、,") for part in parts if part.strip(" ，、,")]

def _profile_sentence_is_temporary(sentence: str, *, kind: str) -> bool:
    if not sentence:
        return True
    temporary_markers = (
        "本章未直接出场",
        "本章主要通过",
        "当前记忆中",
        "当前章节",
        "五年后本章",
    )
    if any(marker in sentence for marker in temporary_markers):
        return True
    if sentence.startswith(("本章", "此前", "当前")):
        return True
    return False

def _profile_sentence_score(sentence: str, *, kind: str) -> int:
    if kind == "voice_profile":
        keywords = (
            "声线", "男声", "女声", "童声", "老年", "青年", "中年", "少年",
            "少女", "低沉", "清亮", "沙哑", "语速", "口音", "克制", "温和",
            "冷硬", "急促", "缓慢", "尾音", "情绪", "压抑", "疲惫",
        )
    else:
        keywords = (
            "之女", "之子", "母亲", "父亲", "县令", "书办", "书吏", "差役",
            "官员", "家人", "亲属", "熟悉", "负责", "主导", "参与", "见证",
            "证人", "被害", "被卷入", "为", "与", "性格", "克制", "谨慎",
        )
    score = sum(2 for keyword in keywords if keyword in sentence)
    score += max(0, 4 - len(sentence) // 80)
    if kind == "traits" and any(marker in sentence for marker in ("本章", "此前", "未直接出场")):
        score -= 5
    return score

def _compact_profile_text(existing: Any, incoming: Any = "", *, max_chars: int, kind: str) -> str:
    sentences: list[str] = []
    seen = set()
    for source in (existing, incoming):
        for sentence in _split_profile_sentences(_character_traits(source)):
            if _profile_sentence_is_temporary(sentence, kind=kind):
                continue
            key = sentence.casefold()
            if key in seen:
                continue
            seen.add(key)
            sentences.append(sentence)
    if not sentences:
        return ""

    indexed = list(enumerate(sentences))
    indexed.sort(key=lambda item: (-_profile_sentence_score(item[1], kind=kind), item[0]))
    chosen: list[tuple[int, str]] = []
    used = 0
    for original_index, sentence in indexed:
        extra = len(sentence) + (1 if chosen else 0)
        if used + extra <= max_chars:
            chosen.append((original_index, sentence))
            used += extra
    if not chosen:
        first = indexed[0][1]
        return first[:max_chars].rstrip("，、；。,. ")
    chosen.sort(key=lambda item: item[0])
    return "；".join(sentence for _, sentence in chosen).strip()

def _compact_key_terms(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    terms = []
    seen = set()
    for term in value:
        text = str(term or "").strip()
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        terms.append(text)
        if len(terms) >= MAX_KEY_TERMS:
            break
    return terms

def _default_character_book() -> dict:
    return {
        "characters": [],
        "narrator_style": "",
        "genre": "",
        "key_terms": [],
    }

def _json_payload_from_text(content: str) -> Any:
    text = str(content or "").strip()
    if not text:
        raise ValueError("No character book content provided")
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
        text = text.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        decoder = json.JSONDecoder()
        for idx, ch in enumerate(text):
            if ch not in "[{":
                continue
            try:
                value, _ = decoder.raw_decode(text[idx:])
                return value
            except json.JSONDecodeError:
                continue
    raise ValueError("Character book content must contain a JSON object or array")

def _localized_value(item: dict, *keys: str) -> Any:
    for key in keys:
        if key in item and item.get(key) is not None:
            return item.get(key)
    return None

def _coerce_character_import_payload(payload: Any) -> dict:
    def split_list(value: Any) -> Any:
        if isinstance(value, str):
            return [part.strip() for part in re.split(r"[、,，\n]", value) if part.strip()]
        return value

    if isinstance(payload, list):
        raw_characters = payload
        meta = {}
    elif isinstance(payload, dict):
        meta = payload
        raw_characters = _localized_value(
            payload,
            "characters",
            "人物",
            "角色",
            "roles",
            "speakers",
        )
        if raw_characters is None and any(key in payload for key in ("canonical", "name", "角色名", "姓名")):
            raw_characters = [payload]
    else:
        raise ValueError("Character book JSON must be an object or an array")

    if not isinstance(raw_characters, list):
        raise ValueError("Character book JSON must include a characters array")

    characters = []
    for item in raw_characters:
        if not isinstance(item, dict):
            continue
        name = _localized_value(item, "canonical", "name", "角色名", "姓名", "speaker")
        aliases = _localized_value(item, "aliases", "别名", "alias", "称呼")
        traits = _localized_value(
            item,
            "traits",
            "description",
            "描述",
            "人设",
            "身份",
            "性格",
        )
        voice_profile = _localized_value(
            item,
            "voice_profile",
            "voice_style",
            "voice_description",
            "声音倾向",
            "音色",
            "音色描述",
            "声音描述",
            "style",
        )
        confidence = _localized_value(item, "confidence", "置信度")
        character = {
            "canonical": name,
            "aliases": split_list(aliases) or [],
            "traits": traits or "",
            "voice_profile": voice_profile or "",
        }
        if confidence is not None:
            character["confidence"] = confidence
        characters.append(character)

    return _normalize_character_book({
        "characters": characters,
        "narrator_style": _localized_value(meta, "narrator_style", "旁白风格", "旁白") or "",
        "genre": _localized_value(meta, "genre", "题材", "类型") or "",
        "key_terms": split_list(_localized_value(meta, "key_terms", "关键术语", "术语")) or [],
    })

def _merge_character_books(existing: dict, incoming: dict) -> dict:
    existing = _normalize_character_book(existing)
    incoming = _normalize_character_book(incoming)
    return _normalize_character_book({
        "characters": [
            *(existing.get("characters") or []),
            *(incoming.get("characters") or []),
        ],
        "narrator_style": incoming.get("narrator_style") or existing.get("narrator_style") or "",
        "genre": incoming.get("genre") or existing.get("genre") or "",
        "key_terms": [
            *(existing.get("key_terms") or []),
            *(incoming.get("key_terms") or []),
        ],
    })

def _normalize_character_book(value: Any) -> dict:
    book = _default_character_book()
    if not isinstance(value, dict):
        return book

    book["narrator_style"] = _compact_profile_text(
        value.get("narrator_style") or "",
        "",
        max_chars=MAX_NARRATOR_STYLE_CHARS,
        kind="voice_profile",
    )
    book["genre"] = str(value.get("genre") or "").strip()
    book["key_terms"] = _compact_key_terms(_normalize_aliases(value.get("key_terms") or []))

    def names_for(character: dict) -> set[str]:
        names = {str(character.get("canonical") or "").casefold()}
        names.update(alias.casefold() for alias in _normalize_aliases(character.get("aliases") or []))
        return {name for name in names if name}

    for item in value.get("characters") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("canonical") or item.get("name") or "").strip()
        if not name or name.upper() == "NARRATOR":
            continue
        aliases = [
            alias for alias in _normalize_aliases(item.get("aliases") or [])
            if alias.casefold() != name.casefold()
        ]
        traits = _compact_profile_text(
            item.get("traits")
            or item.get("description")
            or item.get("描述")
            or item.get("人设")
            or item.get("身份")
            or item.get("性格")
            or "",
            "",
            max_chars=MAX_CHARACTER_TRAITS_CHARS,
            kind="traits",
        )
        voice_profile = _compact_profile_text(
            item.get("voice_profile")
            or item.get("voice_style")
            or item.get("voice_description")
            or item.get("声音倾向")
            or item.get("音色")
            or item.get("音色描述")
            or item.get("声音描述")
            or item.get("style")
            or "",
            "",
            max_chars=MAX_VOICE_PROFILE_CHARS,
            kind="voice_profile",
        )
        if not traits and not voice_profile and not aliases:
            continue
        character = {
            "canonical": name,
            "aliases": aliases,
            "traits": traits,
            "voice_profile": voice_profile,
        }
        confidence = item.get("confidence")
        if confidence is not None:
            try:
                character["confidence"] = max(0.0, min(1.0, float(confidence)))
            except (TypeError, ValueError):
                pass

        incoming_names = names_for(character)
        existing = next(
            (
                existing_character
                for existing_character in book["characters"]
                if names_for(existing_character) & incoming_names
            ),
            None,
        )
        if existing is None:
            book["characters"].append(character)
            continue

        merged_aliases = [
            *(_normalize_aliases(existing.get("aliases") or [])),
            name,
            *aliases,
        ]
        existing["aliases"] = [
            alias for alias in _normalize_aliases(merged_aliases)
            if alias.casefold() != str(existing.get("canonical") or "").casefold()
        ]
        existing["traits"] = _compact_profile_text(
            existing.get("traits"),
            character.get("traits"),
            max_chars=MAX_CHARACTER_TRAITS_CHARS,
            kind="traits",
        )
        existing["voice_profile"] = _compact_profile_text(
            existing.get("voice_profile"),
            character.get("voice_profile"),
            max_chars=MAX_VOICE_PROFILE_CHARS,
            kind="voice_profile",
        )
        if character.get("confidence") is not None:
            existing["confidence"] = max(
                float(existing.get("confidence") or 0.0),
                float(character.get("confidence") or 0.0),
            )
    return book

def _load_character_book(book_dir: str) -> dict:
    path = os.path.join(book_dir, "character_book.json")
    if not os.path.exists(path):
        return _default_character_book()
    try:
        with open(path, "r", encoding="utf-8") as f:
            return _normalize_character_book(json.load(f))
    except (json.JSONDecodeError, ValueError):
        return _default_character_book()

def _save_character_book(book_dir: str, character_book: dict) -> dict:
    normalized = _normalize_character_book(character_book)
    path = os.path.join(book_dir, "character_book.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(normalized, f, indent=2, ensure_ascii=False)
    return normalized

def _clear_character_book(book_dir: str) -> dict:
    return _save_character_book(book_dir, _default_character_book())

def _character_voice_style(character: dict) -> str:
    voice_profile = _character_traits(character.get("voice_profile"))
    if voice_profile:
        return voice_profile
    parts = []
    aliases = _normalize_aliases(character.get("aliases") or [])
    if aliases:
        parts.append(f"别名：{'、'.join(aliases)}")
    traits = _character_traits(character.get("traits"))
    if traits:
        parts.append(traits)
    return "；".join(parts).strip()

def _character_lookup(character_book: dict) -> dict[str, str]:
    lookup = {}
    for character in character_book.get("characters") or []:
        canonical = str(character.get("canonical") or "").strip()
        if not canonical:
            continue
        names = [canonical, *(_normalize_aliases(character.get("aliases") or []))]
        for name in names:
            lookup[name.casefold()] = canonical
    return lookup

def _normalize_speaker_name(value: Any, lookup: dict[str, str]) -> str:
    speaker = str(value or "").strip()
    if not speaker:
        return speaker
    if speaker.upper() in NARRATOR_NAMES:
        return "NARRATOR"
    return lookup.get(speaker.casefold(), speaker)

def _move_voice_config_aliases(book_dir: str, lookup: dict[str, str]) -> int:
    voice_config_path = os.path.join(book_dir, "voice_config.json")
    if not os.path.exists(voice_config_path):
        return 0
    try:
        with open(voice_config_path, "r", encoding="utf-8") as f:
            voice_config = json.load(f)
    except (json.JSONDecodeError, ValueError):
        return 0
    if not isinstance(voice_config, dict):
        return 0

    def has_user_voice_choice(name: str, config: dict) -> bool:
        return _voice_config_has_required_choice(config)

    changed = 0
    for name in list(voice_config.keys()):
        canonical = _normalize_speaker_name(name, lookup)
        if not canonical or canonical == name:
            continue
        if canonical not in voice_config:
            voice_config[canonical] = voice_config[name]
        elif isinstance(voice_config[canonical], dict) and isinstance(voice_config[name], dict):
            if has_user_voice_choice(name, voice_config[name]) and not has_user_voice_choice(canonical, voice_config[canonical]):
                voice_config[canonical] = {
                    **voice_config[canonical],
                    **voice_config[name],
                }
            else:
                for key, value in voice_config[name].items():
                    if key not in voice_config[canonical] or voice_config[canonical].get(key) in (None, ""):
                        voice_config[canonical][key] = value
        del voice_config[name]
        changed += 1
    if changed:
        with open(voice_config_path, "w", encoding="utf-8") as f:
            json.dump(voice_config, f, indent=2, ensure_ascii=False)
    return changed

def _clear_voice_config(book_dir: str) -> None:
    path = os.path.join(book_dir, "voice_config.json")
    if os.path.exists(path):
        os.remove(path)

def _normalize_script_speakers_for_book(book_dir: str, character_book: dict) -> dict[str, int]:
    lookup = _character_lookup(character_book)
    script_path = os.path.join(book_dir, "annotated_script.json")
    chunks_path = os.path.join(book_dir, "chunks.json")
    script_updates = 0
    chunk_updates = 0

    script_entries = _read_json_list(script_path)
    if script_entries:
        for entry in script_entries:
            speaker = entry.get("speaker") if entry.get("speaker") is not None else entry.get("type")
            canonical = _normalize_speaker_name(speaker, lookup)
            if canonical and canonical != speaker:
                if entry.get("speaker") is not None:
                    entry["speaker"] = canonical
                else:
                    entry["type"] = canonical
                script_updates += 1
        if script_updates:
            _write_json(script_path, script_entries)

    chunks = _read_json_list(chunks_path)
    if chunks:
        for chunk in chunks:
            speaker = chunk.get("speaker")
            canonical = _normalize_speaker_name(speaker, lookup)
            if canonical and canonical != speaker:
                chunk["speaker"] = canonical
                chunk_updates += 1
        if chunk_updates:
            _write_json(chunks_path, chunks)

    voice_config_updates = _move_voice_config_aliases(book_dir, lookup)
    return {
        "script_speaker_updates": script_updates,
        "chunk_speaker_updates": chunk_updates,
        "voice_config_updates": voice_config_updates,
    }

def _normalize_entries_for_character_book(entries: list[dict], character_book: dict) -> tuple[list[dict], int]:
    lookup = _character_lookup(character_book)
    normalized = []
    updates = 0
    for entry in entries:
        item = dict(entry)
        speaker = item.get("speaker") if item.get("speaker") is not None else item.get("type")
        canonical = _normalize_speaker_name(speaker, lookup)
        if canonical and canonical != speaker:
            if item.get("speaker") is not None:
                item["speaker"] = canonical
            else:
                item["type"] = canonical
            updates += 1
        normalized.append(item)
    return normalized, updates

def _legacy_auto_voice_config_for_speaker(speaker: str, character_book: Optional[dict] = None) -> dict[str, Any]:
    speaker = str(speaker or "").strip()
    config: dict[str, Any] = {"type": "custom", "voice": "Ryan", "seed": "-1"}
    character_book = character_book or {}
    style = ""
    if speaker.upper() in NARRATOR_NAMES:
        style = str(character_book.get("narrator_style") or "").strip()
    else:
        for character in character_book.get("characters") or []:
            if not isinstance(character, dict):
                continue
            canonical = str(character.get("canonical") or "").strip()
            aliases = _normalize_aliases(character.get("aliases") or [])
            names = {canonical.casefold(), *(alias.casefold() for alias in aliases)}
            if speaker.casefold() in names:
                style = _character_voice_style(character)
                break
    if style:
        config["character_style"] = style
    return config

def _normalize_voice_config_item(config: Any) -> tuple[dict[str, Any], bool]:
    if not isinstance(config, dict):
        return {}, False

    normalized = dict(config)
    changed = False
    if normalized.get("type") == "dashscope":
        model = normalized.get("dashscope_model") or DASHSCOPE_QWEN3_INSTRUCT_MODEL
        voice = str(normalized.get("dashscope_voice") or "").strip()
        if model == DASHSCOPE_QWEN3_INSTRUCT_MODEL and voice in DASHSCOPE_QWEN3_FLASH_ONLY_VOICES:
            model = DASHSCOPE_QWEN3_FLASH_MODEL
        if normalized.get("dashscope_model") != model:
            normalized["dashscope_model"] = model
            changed = True

    return normalized, changed

def _voice_config_has_required_choice(config: Any) -> bool:
    if not isinstance(config, dict) or not config:
        return False
    config_type = str(config.get("type") or "custom")
    if config_type == "custom":
        return bool(str(config.get("voice") or "").strip())
    if config_type == "edge":
        return bool(str(config.get("edge_voice") or "").strip())
    if config_type == "dashscope":
        return bool(str(config.get("dashscope_voice") or "").strip())
    if config_type == "volcengine":
        return bool(str(config.get("volcengine_speaker") or "").strip())
    if config_type == "clone":
        return bool(str(config.get("ref_audio") or "").strip() and str(config.get("ref_text") or "").strip())
    if config_type in {"builtin_lora", "lora"}:
        return bool(str(config.get("adapter_id") or config.get("adapter_path") or "").strip())
    if config_type == "design":
        return bool(str(config.get("description") or "").strip())
    return True

def _is_legacy_unconfirmed_auto_voice_config(
    speaker: str,
    config: Any,
    character_book: Optional[dict] = None,
) -> bool:
    if not isinstance(config, dict) or config.get("confirmed"):
        return False
    if str(config.get("type") or "custom") != "custom":
        return False
    legacy_auto = _legacy_auto_voice_config_for_speaker(speaker, character_book)
    return _voice_config_effective_signature(config) == _voice_config_effective_signature(legacy_auto)

def _clean_voice_config_mapping(
    voice_config: dict[str, Any],
    character_book: Optional[dict] = None,
) -> tuple[dict[str, dict[str, Any]], list[str], list[str]]:
    cleaned: dict[str, dict[str, Any]] = {}
    normalized_names: list[str] = []
    removed_names: list[str] = []
    for name, existing in list(voice_config.items()):
        speaker = str(name or "").strip()
        if not speaker or not isinstance(existing, dict) or not existing:
            if speaker:
                removed_names.append(speaker)
            continue
        normalized, changed = _normalize_voice_config_item(existing)
        if changed:
            normalized_names.append(speaker)
        if (
            not _voice_config_has_required_choice(normalized)
            or _is_legacy_unconfirmed_auto_voice_config(speaker, normalized, character_book)
        ):
            removed_names.append(speaker)
            continue
        cleaned[speaker] = normalized
    return cleaned, normalized_names, removed_names

def _refresh_saved_character_voice_styles(book_dir: str, character_book: dict) -> dict[str, Any]:
    return {
        "updated": [],
        "total": 0,
    }

def _voice_config_status_for_speaker(
    speaker: str,
    config: Any,
    character_book: Optional[dict] = None,
    *,
    raw_config_exists: bool = True,
) -> dict[str, Any]:
    if not isinstance(config, dict) or not config:
        return {
            "voice_config_status": "missing",
            "has_voice_config": False,
            "has_custom_voice_config": False,
            "has_confirmed_voice_config": False,
        }

    config_type = str(config.get("type") or "custom")
    confirmed = bool(config.get("confirmed"))
    if not _voice_config_has_required_choice(config):
        status = "missing"
        custom = False
        confirmed = False
    elif _is_legacy_unconfirmed_auto_voice_config(speaker, config, character_book):
        status = "missing"
        custom = False
        confirmed = False
    else:
        status = "confirmed" if confirmed else "customized"
        custom = not confirmed
    return {
        "voice_config_status": status,
        "has_voice_config": bool(raw_config_exists) and status != "missing",
        "has_custom_voice_config": custom,
        "has_confirmed_voice_config": custom or confirmed,
    }

def _stored_voice_config_names(book_dir: Optional[str] = None) -> set[str]:
    book_dir = book_dir or _current_book_dir()
    payload = _read_json_payload(os.path.join(book_dir, "voice_config.json"), {})
    if not isinstance(payload, dict):
        return set()
    return {str(name) for name, config in payload.items() if isinstance(config, dict) and config}

def _speaker_sort_value(item: dict[str, Any], key: str) -> int:
    try:
        return int(item.get(key) or 0)
    except (TypeError, ValueError):
        return 0

def _sort_speaker_rows(
    rows: list[dict[str, Any]],
    sort_by: Optional[str] = None,
    sort_order: Optional[str] = None,
) -> list[dict[str, Any]]:
    if sort_by not in {"line_count", "char_count"}:
        return rows
    reverse = str(sort_order or "").casefold() == "desc"
    by_name = sorted(rows, key=lambda item: str(item.get("name") or "").casefold())
    return sorted(by_name, key=lambda item: _speaker_sort_value(item, sort_by), reverse=reverse)

def _script_speakers_from_entries(entries: list[dict]) -> list[str]:
    speakers = []
    seen = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        speaker = str(entry.get("speaker") or entry.get("type") or "").strip()
        if not speaker:
            continue
        speaker = "NARRATOR" if speaker.upper() in NARRATOR_NAMES else speaker
        key = speaker.casefold()
        if key in seen:
            continue
        seen.add(key)
        speakers.append(speaker)
    return speakers

def _merge_speaker_lists(*speaker_lists: list[str]) -> list[str]:
    speakers = []
    seen = set()
    for values in speaker_lists:
        for speaker in values or []:
            speaker = str(speaker or "").strip()
            if not speaker:
                continue
            speaker = "NARRATOR" if speaker.upper() in NARRATOR_NAMES else speaker
            key = speaker.casefold()
            if key in seen:
                continue
            seen.add(key)
            speakers.append(speaker)
    return speakers

def _character_book_speakers(book_dir: str) -> list[str]:
    character_book = _load_character_book(book_dir)
    characters = character_book.get("characters") or []
    speakers = []
    if characters:
        speakers.append("NARRATOR")
    for character in characters:
        if not isinstance(character, dict):
            continue
        canonical = str(character.get("canonical") or "").strip()
        if canonical:
            speakers.append(canonical)
    return speakers

def _has_reusable_character_book(book_dir: str) -> bool:
    return any(speaker != "NARRATOR" for speaker in _character_book_speakers(book_dir))

def _script_and_chunk_speakers(book_dir: str) -> list[str]:
    return list(_voice_speaker_metadata(book_dir).keys())

def _script_text_char_count(value: Any) -> int:
    return len(str(value or "").strip())

def _voice_speaker_metadata(book_dir: str, character_book: Optional[dict] = None) -> dict[str, dict[str, Any]]:
    character_book = _normalize_character_book(character_book) if isinstance(character_book, dict) else _load_character_book(book_dir)
    lookup = _character_lookup(character_book)
    metadata: dict[str, dict[str, Any]] = {}

    def append_unique(values: list[str], value: Any) -> None:
        text = str(value or "").strip()
        if not text:
            return
        key = text.casefold()
        if key not in {item.casefold() for item in values}:
            values.append(text)

    def ensure_voice(name: Any, source: str) -> dict[str, Any]:
        voice_name = str(name or "").strip()
        if voice_name == "NARRATOR":
            voice_name = "NARRATOR"
        item = metadata.get(voice_name)
        if not item:
            item = {
                "name": voice_name,
                "source": source,
                "aliases": [],
                "raw_speakers": [],
                "inherited_speakers": [],
                "voice_profile": "",
                "voice_profile_source": "",
                "line_count": 0,
                "char_count": 0,
                "is_narrator": voice_name == "NARRATOR",
                "has_character_book": False,
            }
            metadata[voice_name] = item
        elif item.get("source") == "script" and source != "script":
            item["source"] = source
        return item

    if character_book.get("characters") or character_book.get("narrator_style"):
        narrator = ensure_voice("NARRATOR", "narrator")
        narrator["is_narrator"] = True
        narrator["voice_profile"] = str(character_book.get("narrator_style") or "").strip()
        narrator["voice_profile_source"] = "narrator_style"
        append_unique(narrator["aliases"], "旁白")

    for character in character_book.get("characters") or []:
        if not isinstance(character, dict):
            continue
        canonical = str(character.get("canonical") or "").strip()
        if not canonical:
            continue
        item = ensure_voice(canonical, "character_book")
        item["has_character_book"] = True
        item["voice_profile"] = str(character.get("voice_profile") or "").strip()
        item["voice_profile_source"] = "voice_profile"
        for alias in _normalize_aliases(character.get("aliases") or []):
            append_unique(item["aliases"], alias)

    entries = _read_json_list(os.path.join(book_dir, "chunks.json")) or _read_json_list(os.path.join(book_dir, "annotated_script.json"))
    for entry in entries:
        raw_speaker = str(entry.get("speaker") or entry.get("type") or "").strip()
        if not raw_speaker:
            continue
        item = ensure_voice(raw_speaker, "script")
        item["line_count"] += 1
        item["char_count"] += _script_text_char_count(entry.get("text"))
        append_unique(item["raw_speakers"], raw_speaker)
        canonical = _normalize_speaker_name(raw_speaker, lookup)
        if canonical and raw_speaker.casefold() != canonical.casefold():
            append_unique(item["inherited_speakers"], raw_speaker)

    return metadata

def _ensure_voice_config_for_script(book_dir: Optional[str] = None, *, write: bool = True) -> dict[str, Any]:
    book_dir = book_dir or _current_book_dir()
    voice_config_path = os.path.join(book_dir, "voice_config.json")
    voice_config = _read_json_payload(voice_config_path, {})
    if not isinstance(voice_config, dict):
        voice_config = {}

    character_book = _load_character_book(book_dir)
    migrated_aliases = _move_voice_config_aliases(book_dir, _character_lookup(character_book)) if write else 0
    if migrated_aliases:
        voice_config = _read_json_payload(voice_config_path, {})
        if not isinstance(voice_config, dict):
            voice_config = {}
    voice_config, updated, removed = _clean_voice_config_mapping(voice_config, character_book)

    if write and (updated or removed):
        if voice_config:
            _write_json(voice_config_path, voice_config)
        elif os.path.exists(voice_config_path):
            os.remove(voice_config_path)

    return {
        "voice_config": voice_config,
        "added": [],
        "updated": updated,
        "removed": removed,
        "migrated_aliases": migrated_aliases,
        "total": len(voice_config),
    }

def _validate_script_entries(
    chapter: Optional[dict],
    entries: list[dict],
    character_book: dict,
    parse_issues: list[dict],
) -> dict[str, Any]:
    chapter_id = str(chapter.get("chapter_id") or "") if chapter else ""
    lookup = _character_lookup(character_book)
    known_speakers = {name for name in lookup.values() if name}
    known_speakers.add("NARRATOR")
    known_speaker_keys = set(lookup.keys())
    issues: list[dict[str, Any]] = []

    for issue in parse_issues:
        if not isinstance(issue, dict):
            continue
        issues.append({
            "severity": issue.get("level") or "warning",
            "code": "tagged_parse",
            "message": str(issue.get("message") or "Tagged script parse issue"),
            "line": issue.get("line"),
        })

    for idx, entry in enumerate(entries):
        speaker = str(entry.get("speaker") or entry.get("type") or "").strip()
        text = str(entry.get("text") or "").strip()
        instruct = str(entry.get("instruct") or "").strip()
        entry_chapter_id = str(entry.get("chapter_id") or "")
        if not text:
            issues.append({"severity": "error", "code": "empty_text", "message": "脚本条目文本为空。", "entry_index": idx})
        if not speaker:
            issues.append({"severity": "error", "code": "empty_speaker", "message": "脚本条目缺少 speaker。", "entry_index": idx})
        elif speaker not in known_speakers and speaker.casefold() not in known_speaker_keys:
            issues.append({
                "severity": "warning",
                "code": "unknown_speaker",
                "message": f"说话人「{speaker}」不在角色表中。",
                "entry_index": idx,
                "speaker": speaker,
            })
        if not instruct:
            issues.append({
                "severity": "info",
                "code": "missing_instruct",
                "message": "脚本条目缺少 instruct，TTS 表演可能不稳定。",
                "entry_index": idx,
                "speaker": speaker,
            })
        if len(text) > MAX_CHUNK_CHARS:
            issues.append({
                "severity": "warning",
                "code": "long_text",
                "message": f"单条文本 {len(text)} 字，可能过长。",
                "entry_index": idx,
                "speaker": speaker,
            })
        if chapter_id and entry_chapter_id and entry_chapter_id != chapter_id:
            issues.append({
                "severity": "error",
                "code": "chapter_mismatch",
                "message": "脚本条目的 chapter_id 与当前章节不一致。",
                "entry_index": idx,
            })

    coverage = {}
    if chapter:
        source_text = str(chapter.get("content") or "")
        if not source_text:
            try:
                path = _chapter_file_path(chapter)
                if os.path.exists(path):
                    source_text = _read_text_file(path)
            except Exception:
                source_text = ""
        coverage = _source_coverage_report(source_text, entries)
        _append_source_coverage_issues(coverage, issues)

    unknown_speakers = sorted({
        issue.get("speaker")
        for issue in issues
        if issue.get("code") == "unknown_speaker" and issue.get("speaker")
    })
    error_count = sum(1 for issue in issues if issue.get("severity") == "error")
    warning_count = sum(1 for issue in issues if issue.get("severity") == "warning")
    return {
        "chapter_id": chapter_id,
        "chapter_index": chapter.get("index") if chapter else None,
        "chapter_title": (chapter.get("title") or chapter_id) if chapter else "",
        "issue_count": len(issues),
        "error_count": error_count,
        "warning_count": warning_count,
        "unknown_speaker_count": len(unknown_speakers),
        "unknown_speakers": unknown_speakers,
        **coverage,
        "issues": issues,
        "updated_at": _now_iso(),
    }

def _script_speakers_by_character(book_dir: str, character_book: dict) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    lookup = _character_lookup(character_book)
    chunk_data = _read_json_list(os.path.join(book_dir, "chunks.json"))
    script_data = chunk_data or _read_json_list(os.path.join(book_dir, "annotated_script.json"))
    for entry in script_data:
        speaker = str(entry.get("speaker") or entry.get("type") or "").strip()
        if not speaker:
            continue
        canonical = lookup.get(speaker.casefold(), speaker)
        result.setdefault(canonical, set()).add(speaker)
    return result

def _load_script_entries(script_path: str) -> list[dict]:
    if not os.path.exists(script_path):
        return []
    try:
        with open(script_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return [entry for entry in data if isinstance(entry, dict)] if isinstance(data, list) else []
    except (json.JSONDecodeError, ValueError):
        return []

def _chunk_to_script_entry(chunk: dict) -> dict:
    entry = {
        "speaker": str(chunk.get("speaker") or "NARRATOR").strip() or "NARRATOR",
        "text": str(chunk.get("text") or ""),
        "instruct": str(chunk.get("instruct") or ""),
    }
    for field in ("chapter_id", "chapter_index", "chapter_title", "pause_after"):
        if chunk.get(field) is not None:
            entry[field] = chunk.get(field)
    return entry

def _script_entries_from_current_chunks() -> list[dict]:
    entries = []
    for chunk in _read_json_list(_current_chunks_path()):
        if not str(chunk.get("text") or "").strip():
            continue
        entries.append(_chunk_to_script_entry(chunk))
    return entries

def _current_script_entries_for_export(*, sync_from_chunks: bool = True) -> list[dict]:
    chunks = _read_json_list(_current_chunks_path())
    chunk_entries = _script_entries_from_current_chunks()
    if chunks:
        if sync_from_chunks:
            _write_json(_current_script_path(), chunk_entries)
        return chunk_entries
    if sync_from_chunks and chunk_entries:
        _write_json(_current_script_path(), chunk_entries)
        return chunk_entries
    if chunk_entries:
        return chunk_entries
    return _load_script_entries(_current_script_path())

def _sync_current_script_from_chunks(*, source: str = "chunk_editor") -> dict[str, Any]:
    entries = _current_script_entries_for_export(sync_from_chunks=True)
    character_book = _load_character_book(_current_book_dir())
    issues = _revalidate_current_script_issues(character_book)
    state = _sync_script_generation_state_from_entries(entries, source=source)
    return {
        "entries": entries,
        "entry_count": len(entries),
        "script_issue_reports": len((issues.get("chapters") or {}) if isinstance(issues, dict) else {}),
        "state": state,
    }

def _load_script_generation_state() -> dict:
    path = _current_script_generation_state_path()
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, ValueError):
        return {}

def _save_script_generation_state(state: dict) -> dict:
    if not isinstance(state, dict):
        state = {}
    _write_json(_current_script_generation_state_path(), state)
    return state


def _load_character_analysis_state() -> dict:
    path = _current_character_analysis_state_path()
    if not os.path.exists(path):
        return {"chapters": {}}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {"chapters": {}}
        if not isinstance(data.get("chapters"), dict):
            data["chapters"] = {}
        return data
    except (json.JSONDecodeError, ValueError):
        return {"chapters": {}}


def _save_character_analysis_state(state: dict) -> dict:
    if not isinstance(state, dict):
        state = {}
    if not isinstance(state.get("chapters"), dict):
        state["chapters"] = {}
    state["updated_at"] = _now_iso()
    _write_json(_current_character_analysis_state_path(), state)
    return state


def _invalidate_character_analysis_state(chapter_ids: Optional[list[str]] = None) -> dict[str, Any]:
    state = _load_character_analysis_state()
    chapters = state.get("chapters") if isinstance(state.get("chapters"), dict) else {}
    chapter_set = {str(chapter_id or "") for chapter_id in (chapter_ids or []) if str(chapter_id or "")}
    if not chapter_set:
        removed = len(chapters)
        _save_character_analysis_state({"chapters": {}})
        return {"removed_character_analysis_chapters": removed}

    removed = 0
    for chapter_id in chapter_set:
        if chapter_id in chapters:
            chapters.pop(chapter_id, None)
            removed += 1
    state["chapters"] = chapters
    _save_character_analysis_state(state)
    return {"removed_character_analysis_chapters": removed}


def _update_script_generation_state_for_changes(
    chapter_ids: Optional[list[str]] = None,
    *,
    status: str,
    source: str,
    entry_counts: Optional[dict[str, int]] = None,
    clear_error: bool = True,
) -> dict:
    state = _load_script_generation_state()
    chapters = state.get("chapters") if isinstance(state.get("chapters"), dict) else {}
    chapter_set = {str(chapter_id or "") for chapter_id in (chapter_ids or []) if str(chapter_id or "")}
    if not chapter_set:
        manifest = load_current_chapters_manifest()
        chapter_set = {
            str(chapter.get("chapter_id") or "")
            for chapter in manifest.get("chapters") or []
            if str(chapter.get("chapter_id") or "")
        }
    now = _now_iso()
    entry_counts = entry_counts or {}
    for chapter_id in chapter_set:
        current = chapters.get(chapter_id) if isinstance(chapters.get(chapter_id), dict) else {}
        item = {
            **current,
            "chapter_id": chapter_id,
            "status": status,
            "source": source,
            "updated_at": now,
        }
        if chapter_id in entry_counts:
            item["entry_count"] = entry_counts[chapter_id]
        elif status in {"missing", "cancelled", "error"}:
            item["entry_count"] = 0
        if clear_error:
            item["error"] = ""
        chapters[chapter_id] = item
    state["chapters"] = chapters
    state["status"] = source
    state["updated_at"] = now
    if status != "running":
        state["finished_at"] = now
    return _save_script_generation_state(state)

def _script_entry_counts_by_chapter(entries: list[dict]) -> dict[str, int]:
    entry_counts: dict[str, int] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        chapter_id = str(entry.get("chapter_id") or "").strip()
        if chapter_id:
            entry_counts[chapter_id] = entry_counts.get(chapter_id, 0) + 1
    return entry_counts

def _sync_script_generation_state_from_entries(
    entries: list[dict],
    *,
    source: str,
    manifest: Optional[dict] = None,
) -> dict:
    manifest = manifest or load_current_chapters_manifest()
    entry_counts = _script_entry_counts_by_chapter(entries)
    now = _now_iso()
    state = _load_script_generation_state()
    previous_chapters = state.get("chapters") if isinstance(state.get("chapters"), dict) else {}
    chapters = {}
    issue_chapters = (_load_script_issues().get("chapters") or {})
    if not isinstance(issue_chapters, dict):
        issue_chapters = {}

    for chapter in manifest.get("chapters") or []:
        chapter_id = str(chapter.get("chapter_id") or "").strip()
        if not chapter_id:
            continue
        count = entry_counts.get(chapter_id, 0)
        issue_report = issue_chapters.get(chapter_id) if isinstance(issue_chapters.get(chapter_id), dict) else {}
        item = {
            "chapter_id": chapter_id,
            "chapter_index": chapter.get("index"),
            "chapter_title": chapter.get("title") or chapter_id,
            "status": "done" if count else "missing",
            "source": source,
            "entry_count": count,
            "parse_issues": 0,
            "issue_count": issue_report.get("issue_count") or 0,
            "error_count": issue_report.get("error_count") or 0,
            "warning_count": issue_report.get("warning_count") or 0,
            "unknown_speaker_count": issue_report.get("unknown_speaker_count") or 0,
            "error": "",
            "updated_at": now,
        }
        if isinstance(previous_chapters.get(chapter_id), dict):
            item["started_at"] = previous_chapters[chapter_id].get("started_at") or ""
        chapters[chapter_id] = item

    state["chapters"] = chapters
    state["status"] = source
    state["updated_at"] = now
    state["finished_at"] = now
    return _save_script_generation_state(state)


def _mark_stale_script_generation_interrupted(manifest: Optional[dict] = None, *, force: bool = False) -> dict:
    if process_state.get("script", {}).get("running") and not force:
        return _load_script_generation_state()

    state = _load_script_generation_state()
    if not state:
        return state

    chapters = state.get("chapters") if isinstance(state.get("chapters"), dict) else {}
    if not chapters and state.get("status") != "running":
        return state

    manifest = manifest or load_current_chapters_manifest()
    now = _now_iso()
    changed = False
    stale_statuses = {"running", "pending"}

    for chapter in manifest.get("chapters") or []:
        chapter_id = str(chapter.get("chapter_id") or "").strip()
        if not chapter_id:
            continue
        item = chapters.get(chapter_id) if isinstance(chapters.get(chapter_id), dict) else {}
        status = str(item.get("status") or "").strip().lower()
        if status not in stale_statuses:
            continue
        item = {
            **item,
            "chapter_id": chapter_id,
            "chapter_index": item.get("chapter_index") or chapter.get("index"),
            "chapter_title": item.get("chapter_title") or chapter.get("title") or chapter_id,
            "status": "interrupted",
            "entry_count": 0,
            "updated_at": now,
        }
        item["error"] = item.get("error") or "上次脚本生成中断，可继续生成该章节。"
        item["interrupted_at"] = now
        chapters[chapter_id] = item
        changed = True

    if state.get("status") == "running":
        state["status"] = "interrupted"
        state["finished_at"] = state.get("finished_at") or now
        changed = True
    if changed:
        state["chapters"] = chapters
        state["updated_at"] = now
        return _save_script_generation_state(state)
    return state

def _load_chapter_memory() -> dict:
    path = _current_chapter_memory_path()
    if not os.path.exists(path):
        return {"chapters": {}}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {"chapters": {}}
        if not isinstance(data.get("chapters"), dict):
            data["chapters"] = {}
        return data
    except (json.JSONDecodeError, ValueError):
        return {"chapters": {}}

def _save_chapter_memory(memory: dict) -> dict:
    if not isinstance(memory, dict):
        memory = {}
    if not isinstance(memory.get("chapters"), dict):
        memory["chapters"] = {}
    memory["updated_at"] = _now_iso()
    _write_json(_current_chapter_memory_path(), memory)
    return memory

def _load_script_issues() -> dict:
    path = _current_script_issues_path()
    if not os.path.exists(path):
        return {"chapters": {}}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {"chapters": {}}
        if not isinstance(data.get("chapters"), dict):
            data["chapters"] = {}
        return data
    except (json.JSONDecodeError, ValueError):
        return {"chapters": {}}

def _save_script_issues(issues: dict) -> dict:
    if not isinstance(issues, dict):
        issues = {}
    if not isinstance(issues.get("chapters"), dict):
        issues["chapters"] = {}
    issues["updated_at"] = _now_iso()
    _write_json(_current_script_issues_path(), issues)
    return issues

def _compact_for_coverage(value: Any) -> str:
    text = str(value or "").casefold()
    return re.sub(r"[\s\W_]+", "", text, flags=re.UNICODE)

SOURCE_COVERAGE_CATEGORY_META = {
    "dialogue": {"label": "对白", "weight": 3.0, "severity": "high"},
    "key_detail": {"label": "关键线索", "weight": 2.5, "severity": "high"},
    "number_time": {"label": "数字/时间", "weight": 2.2, "severity": "high"},
    "state_relation": {"label": "人物状态", "weight": 1.8, "severity": "medium"},
    "narration": {"label": "叙述", "weight": 1.0, "severity": "low"},
}

SOURCE_DIALOGUE_RE = re.compile(r"[“\"「『]([^”\"」』]{2,160})[”\"」』]")
SOURCE_NUMBER_TIME_RE = re.compile(
    r"[零〇一二两三四五六七八九十百千万亿\d０-９]{1,8}"
    r"(?:年|月|日|天|夜|更|刻|时|里|丈|尺|寸|枚|封|页|两|钱|人|次|遍|章|号|件|条|本|把|只|盏|间|处|座|个)?"
)
SOURCE_KEY_DETAIL_RE = re.compile(
    r"[\u4e00-\u9fffA-Za-z0-9]{1,10}"
    r"(?:符|令|信|书|册|账|页|刀|剑|门|楼|城|巷|山|寺|院|房|厅|县|府|印|封|纸|卷|案|灯|钟|铜|银|金|药|血|骨|名册|铜符)"
)
SOURCE_STATE_RE = re.compile(
    r"(?:记起|想起|知道|明白|意识到|相信|怀疑|害怕|愤怒|迟疑|沉默|攥|盯|望|等|逼|必须|不能|没有说完|关系|承诺|背叛|威胁|秘密|密语)"
)

def _source_coverage_display(value: str, *, limit: int = 96) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."

def _source_coverage_point_key(category: str, text: str) -> str:
    return f"{category}:{_compact_for_coverage(text)[:80]}"

def _source_coverage_add_point(points: list[dict[str, Any]], seen: set[str], category: str, text: str) -> None:
    compact = _compact_for_coverage(text)
    if len(compact) < 3:
        return
    key = _source_coverage_point_key(category, text)
    if key in seen:
        return
    seen.add(key)
    meta = SOURCE_COVERAGE_CATEGORY_META.get(category, SOURCE_COVERAGE_CATEGORY_META["narration"])
    points.append({
        "category": category,
        "category_label": meta["label"],
        "severity": meta["severity"],
        "weight": meta["weight"],
        "text": _source_coverage_display(text),
        "compact": compact,
    })

def _source_coverage_points(source_text: str) -> list[dict[str, Any]]:
    points: list[dict[str, Any]] = []
    seen: set[str] = set()
    source_text = str(source_text or "")

    for match in SOURCE_DIALOGUE_RE.finditer(source_text):
        _source_coverage_add_point(points, seen, "dialogue", match.group(1))

    for match in SOURCE_NUMBER_TIME_RE.finditer(source_text):
        token = match.group(0)
        if len(_compact_for_coverage(token)) >= 2:
            _source_coverage_add_point(points, seen, "number_time", token)

    for match in SOURCE_KEY_DETAIL_RE.finditer(source_text):
        token = match.group(0)
        if len(_compact_for_coverage(token)) >= 3:
            _source_coverage_add_point(points, seen, "key_detail", token)

    raw_units = [
        part.strip()
        for part in re.split(r"[\n。！？!?；;]+", source_text)
        if part.strip()
    ]
    for raw in raw_units:
        compact = _compact_for_coverage(raw)
        if len(compact) < 6:
            continue
        category = "state_relation" if SOURCE_STATE_RE.search(raw) else "narration"
        if len(compact) > 80:
            for idx in range(0, len(compact), 60):
                unit = compact[idx:idx + 80]
                if len(unit) >= 6:
                    _source_coverage_add_point(points, seen, category, unit)
        else:
            _source_coverage_add_point(points, seen, category, raw)

    return points

def _source_point_covered(point: dict[str, Any], generated_compact: str) -> bool:
    compact = str(point.get("compact") or "")
    if not compact:
        return True
    if compact in generated_compact:
        return True
    category = point.get("category")
    if category in {"number_time", "key_detail"}:
        return len(compact) >= 2 and compact in generated_compact

    window = 10 if category == "dialogue" else 16
    if len(compact) <= window:
        return compact in generated_compact

    hits = 0
    probes = 0
    step = max(6, window // 2)
    for start in range(0, max(1, len(compact) - window + 1), step):
        probe = compact[start:start + window]
        if len(probe) < min(8, window):
            continue
        probes += 1
        if probe in generated_compact:
            hits += 1
    if probes == 0:
        return False
    required_ratio = 0.34 if category == "narration" else 0.5
    return (hits / probes) >= required_ratio

def _source_coverage_summary(points: list[dict[str, Any]]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for category, meta in SOURCE_COVERAGE_CATEGORY_META.items():
        category_points = [point for point in points if point.get("category") == category]
        total_weight = sum(float(point.get("weight") or 0) for point in category_points)
        covered_weight = sum(float(point.get("weight") or 0) for point in category_points if point.get("covered"))
        summary[category] = {
            "label": meta["label"],
            "severity": meta["severity"],
            "total": len(category_points),
            "covered": sum(1 for point in category_points if point.get("covered")),
            "missing": sum(1 for point in category_points if not point.get("covered")),
            "weight": round(total_weight, 2),
            "covered_weight": round(covered_weight, 2),
            "ratio": round(covered_weight / total_weight, 4) if total_weight else 1.0,
        }
    return summary

def _source_coverage_report(source_text: str, entries: list[dict]) -> dict[str, Any]:
    source_text = str(source_text or "")
    generated_text = "\n".join(str(entry.get("text") or "") for entry in entries if isinstance(entry, dict))
    source_compact = _compact_for_coverage(source_text)
    generated_compact = _compact_for_coverage(generated_text)
    if not source_compact:
        return {
            "source_char_count": 0,
            "generated_text_char_count": len(generated_text),
            "source_coverage_ratio": 1.0,
            "source_coverage_text_ratio": 1.0,
            "source_coverage_weighted_ratio": 1.0,
            "source_covered_chars": 0,
            "source_uncovered_samples": [],
            "source_coverage_findings": [],
            "source_coverage_category_summary": _source_coverage_summary([]),
            "source_critical_missing_count": 0,
        }

    raw_units = [
        part.strip()
        for part in re.split(r"[\n。！？!?；;]+", source_text)
        if part.strip()
    ]
    units: list[str] = []
    for raw in raw_units:
        compact = _compact_for_coverage(raw)
        if len(compact) < 6:
            continue
        if len(compact) > 80:
            for idx in range(0, len(compact), 60):
                unit = compact[idx:idx + 80]
                if len(unit) >= 6:
                    units.append(unit)
        else:
            units.append(compact)

    if not units:
        units = [source_compact[idx:idx + 80] for idx in range(0, len(source_compact), 80)]

    covered_chars = 0
    uncovered: list[str] = []
    for unit in units:
        if not unit:
            continue
        probe = unit if len(unit) <= 24 else unit[:24]
        if probe and probe in generated_compact:
            covered_chars += len(unit)
            continue
        window_hit = False
        if len(unit) > 24:
            for start in range(0, max(1, len(unit) - 23), 12):
                if unit[start:start + 24] in generated_compact:
                    window_hit = True
                    break
        if window_hit:
            covered_chars += len(unit)
        elif len(uncovered) < 5:
            uncovered.append(unit[:80])

    total = sum(len(unit) for unit in units) or len(source_compact)
    text_ratio = min(1.0, covered_chars / max(total, 1))
    points = _source_coverage_points(source_text)
    for point in points:
        point["covered"] = _source_point_covered(point, generated_compact)
    total_weight = sum(float(point.get("weight") or 0) for point in points)
    covered_weight = sum(float(point.get("weight") or 0) for point in points if point.get("covered"))
    weighted_ratio = covered_weight / total_weight if total_weight else text_ratio
    uncovered_points = [point for point in points if not point.get("covered")]
    severity_order = {"high": 0, "medium": 1, "low": 2}
    uncovered_points.sort(key=lambda point: (severity_order.get(point.get("severity"), 3), -float(point.get("weight") or 0)))
    findings = [
        {
            "category": point.get("category"),
            "category_label": point.get("category_label"),
            "severity": point.get("severity"),
            "weight": point.get("weight"),
            "text": point.get("text"),
        }
        for point in uncovered_points[:12]
    ]
    ratio = min(text_ratio, weighted_ratio) if points else text_ratio
    return {
        "source_char_count": len(source_text),
        "generated_text_char_count": len(generated_text),
        "source_coverage_ratio": round(ratio, 4),
        "source_coverage_text_ratio": round(text_ratio, 4),
        "source_coverage_weighted_ratio": round(weighted_ratio, 4),
        "source_covered_chars": covered_chars,
        "source_uncovered_samples": [item.get("text") for item in findings[:5]] or uncovered,
        "source_coverage_findings": findings,
        "source_coverage_category_summary": _source_coverage_summary(points),
        "source_critical_missing_count": sum(1 for point in uncovered_points if point.get("severity") == "high"),
    }

def _append_source_coverage_issues(report: dict[str, Any], issues: list[dict[str, Any]]) -> None:
    ratio = float(report.get("source_coverage_ratio") or 0)
    source_chars = int(report.get("source_char_count") or 0)
    critical_missing = int(report.get("source_critical_missing_count") or 0)
    findings = report.get("source_coverage_findings") or []
    if source_chars >= 80 and critical_missing:
        samples = [
            f"{item.get('category_label') or '信息点'}：{item.get('text')}"
            for item in findings[:5]
            if isinstance(item, dict)
        ]
        issues.append({
            "severity": "warning",
            "code": "missing_source_information_points",
            "message": f"可能遗漏 {critical_missing} 个高权重原文信息点，优先检查对白、数字或关键线索。",
            "coverage_ratio": ratio,
            "samples": samples,
        })
    if source_chars >= 120 and ratio < 0.55:
        issues.append({
            "severity": "warning",
            "code": "low_source_coverage",
            "message": f"加权原文覆盖率约 {round(ratio * 100)}%，可能存在正文遗漏或过度概括。",
            "coverage_ratio": ratio,
            "samples": report.get("source_uncovered_samples") or [],
        })

def _book_file_item(book: dict, rel_path: str, label: str, kind: str, *, expected: bool = True) -> dict[str, Any]:
    book_dir = _book_dir(book["id"])
    path = os.path.normpath(os.path.join(book_dir, rel_path))
    root = os.path.abspath(book_dir)
    exists = os.path.exists(path) and os.path.abspath(path).startswith(root + os.sep)
    item: dict[str, Any] = {
        "label": label,
        "kind": kind,
        "path": rel_path.replace(os.sep, "/"),
        "exists": bool(exists),
        "expected": expected,
    }
    if exists:
        stat = os.stat(path)
        item.update({
            "size": stat.st_size,
            "updated_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
            "url": f"/books/{quote(book['id'])}/{quote(rel_path.replace(os.sep, '/'), safe='/')}",
        })
    return item

def _script_output_files(book: dict) -> dict[str, Any]:
    book_dir = _book_dir(book["id"])
    core_files = [
        ("source", "source", "源稿目录"),
        ("chapters/manifest.json", "chapters", "章节清单"),
        ("annotated_script.json", "script", "标注脚本"),
        ("annotated_script.partial.json", "script", "生成中的部分脚本"),
        ("chunks.json", "chunks", "TTS 片段"),
        ("character_book.json", "characters", "人物池"),
        ("character_analysis_state.json", "characters", "人物分析状态"),
        ("chapter_memory.json", "memory", "章节记忆"),
        ("script_issues.json", "issues", "脚本校验报告"),
        ("story_bible.json", "story_bible", "故事 Bible"),
        ("voice_config.json", "voices", "声音配置"),
        ("script_generation_state.json", "progress", "生成状态"),
        ("cloned_audiobook.mp3", "audio", "合并 MP3"),
        ("audiobook.m4b", "audio", "M4B 有声书"),
    ]
    files = [
        _book_file_item(book, rel_path, label, kind, expected=rel_path != "annotated_script.partial.json")
        for rel_path, kind, label in core_files
    ]
    chapter_audio_dir = os.path.join(book_dir, "chapter_audio")
    chapter_audio_count = 0
    if os.path.isdir(chapter_audio_dir):
        for name in sorted(os.listdir(chapter_audio_dir)):
            if not name.lower().endswith((".mp3", ".wav", ".m4a")):
                continue
            chapter_audio_count += 1
            files.append(_book_file_item(book, f"chapter_audio/{name}", "章节音频", "audio", expected=False))
    voiceline_dir = os.path.join(book_dir, "voicelines")
    voiceline_count = 0
    if os.path.isdir(voiceline_dir):
        for name in os.listdir(voiceline_dir):
            if name.lower().endswith((".mp3", ".wav", ".m4a")):
                voiceline_count += 1
    existing = [item for item in files if item.get("exists")]
    return {
        "book_id": book.get("id"),
        "book_title": book.get("title"),
        "files": files,
        "summary": {
            "existing_files": len(existing),
            "expected_missing": sum(1 for item in files if item.get("expected") and not item.get("exists")),
            "total_size": sum(int(item.get("size") or 0) for item in existing),
            "chapter_audio_count": chapter_audio_count,
            "voiceline_count": voiceline_count,
        },
    }

def _build_story_bible_for_current_book() -> dict[str, Any]:
    book = _ensure_current_book()
    manifest = load_current_chapters_manifest()
    character_book = _load_character_book(_current_book_dir())
    memory = _load_chapter_memory()
    progress = _script_chapter_progress(manifest)
    issues = _load_script_issues()
    memory_chapters = memory.get("chapters") if isinstance(memory.get("chapters"), dict) else {}
    issue_chapters = issues.get("chapters") if isinstance(issues.get("chapters"), dict) else {}
    chapters = []
    for chapter in manifest.get("chapters") or []:
        chapter_id = str(chapter.get("chapter_id") or "")
        chapter_memory = memory_chapters.get(chapter_id) if isinstance(memory_chapters.get(chapter_id), dict) else {}
        issue_report = issue_chapters.get(chapter_id) if isinstance(issue_chapters.get(chapter_id), dict) else {}
        chapters.append({
            "chapter_id": chapter_id,
            "chapter_index": chapter.get("index"),
            "chapter_title": chapter.get("title") or chapter_id,
            "char_count": chapter.get("char_count") or 0,
            "summary": chapter_memory.get("summary") or "",
            "ending_state": chapter_memory.get("ending_state") or "",
            "open_threads": chapter_memory.get("open_threads") or [],
            "speakers": chapter_memory.get("speakers") or [],
            "memory_stale": bool(chapter_memory.get("stale")),
            "issue_count": issue_report.get("issue_count") or 0,
            "source_coverage_ratio": issue_report.get("source_coverage_ratio"),
            "source_critical_missing_count": issue_report.get("source_critical_missing_count") or 0,
        })
    bible = {
        "version": 1,
        "book_id": book.get("id"),
        "book_title": book.get("title"),
        "updated_at": _now_iso(),
        "source_filename": book.get("source_filename") or "",
        "narrator_style": character_book.get("narrator_style") or "",
        "genre": character_book.get("genre") or "",
        "key_terms": character_book.get("key_terms") or [],
        "characters": character_book.get("characters") or [],
        "chapter_count": len(chapters),
        "chapters": chapters,
        "summary": progress.get("summary") or {},
    }
    _write_json(_current_story_bible_path(), bible)
    return bible

def _latest_generation_snapshot() -> dict[str, Any]:
    path = _current_generation_snapshot_state_path()
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, ValueError):
        return {}

GENERATION_SNAPSHOT_REL_PATHS = (
    "annotated_script.json",
    "annotated_script.partial.json",
    "chunks.json",
    "voice_config.json",
    "character_book.json",
    "character_analysis_state.json",
    "chapter_memory.json",
    "script_issues.json",
    "story_bible.json",
    "script_generation_state.json",
)

def _create_generation_snapshot(reason: str, selected_chapter_ids: list[str]) -> dict[str, Any]:
    book = _ensure_current_book()
    book_dir = _current_book_dir()
    snapshot_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    snapshot_dir = os.path.join(_current_generation_snapshots_dir(), snapshot_id)
    os.makedirs(snapshot_dir, exist_ok=True)
    files = []
    for rel_path in GENERATION_SNAPSHOT_REL_PATHS:
        src = os.path.join(book_dir, rel_path)
        backup = os.path.join(snapshot_dir, rel_path)
        existed = os.path.exists(src)
        item = {"path": rel_path, "existed": existed}
        if existed:
            os.makedirs(os.path.dirname(backup), exist_ok=True)
            shutil.copy2(src, backup)
            item["backup_path"] = os.path.relpath(backup, book_dir).replace(os.sep, "/")
        files.append(item)
    state = {
        "snapshot_id": snapshot_id,
        "status": "created",
        "created_at": _now_iso(),
        "reason": reason,
        "book_id": book.get("id"),
        "book_title": book.get("title"),
        "selected_chapter_ids": selected_chapter_ids,
        "snapshot_dir": os.path.relpath(snapshot_dir, book_dir).replace(os.sep, "/"),
        "files": files,
    }
    _write_json(_current_generation_snapshot_state_path(), state)
    return state

def _restore_generation_snapshot(reason: str) -> dict[str, Any]:
    state = _latest_generation_snapshot()
    if not state:
        return {"status": "missing", "restored_files": []}
    book_dir = _current_book_dir()
    snapshot_dir = os.path.join(book_dir, state.get("snapshot_dir") or "")
    restored_files: list[str] = []
    removed_files: list[str] = []
    for item in state.get("files") or []:
        if not isinstance(item, dict):
            continue
        rel_path = str(item.get("path") or "")
        if not rel_path:
            continue
        target = os.path.join(book_dir, rel_path)
        if item.get("existed"):
            backup = os.path.join(book_dir, str(item.get("backup_path") or ""))
            if os.path.exists(backup):
                os.makedirs(os.path.dirname(target), exist_ok=True)
                shutil.copy2(backup, target)
                restored_files.append(rel_path)
        elif os.path.exists(target):
            os.remove(target)
            removed_files.append(rel_path)
    state.update({
        "status": "restored",
        "restored_at": _now_iso(),
        "restore_reason": reason,
        "restored_files": restored_files,
        "removed_files": removed_files,
    })
    _write_json(_current_generation_snapshot_state_path(), state)
    return state

def _mark_generation_snapshot_completed() -> dict[str, Any]:
    state = _latest_generation_snapshot()
    if not state:
        return {}
    state["status"] = "completed"
    state["completed_at"] = _now_iso()
    _write_json(_current_generation_snapshot_state_path(), state)
    return state


def _mark_generation_snapshot_interrupted(reason: str) -> dict[str, Any]:
    state = _latest_generation_snapshot()
    if not state:
        return {}
    state["status"] = "interrupted"
    state["interrupted_at"] = _now_iso()
    state["interrupt_reason"] = reason
    _write_json(_current_generation_snapshot_state_path(), state)
    return state

def _script_generation_estimate(
    manifest: dict,
    selected_chapter_ids: list[str],
    *,
    mode: str,
    reuse_character_book: bool,
    enable_chapter_memory: bool,
    generation: dict,
) -> dict[str, Any]:
    selected = set(selected_chapter_ids)
    chapters = [
        chapter for chapter in manifest.get("chapters") or []
        if not selected or str(chapter.get("chapter_id") or "") in selected
    ]
    target_chapter_ids = [str(chapter.get("chapter_id") or "") for chapter in chapters if str(chapter.get("chapter_id") or "")]
    chapter_count = len(chapters)
    char_count = sum(int(chapter.get("char_count") or 0) for chapter in chapters)
    if mode == "characters":
        breakdown = {"character_analysis": chapter_count, "script_annotation": 0, "chapter_memory": 0}
    else:
        breakdown = {
            "character_analysis": 0 if reuse_character_book else chapter_count,
            "script_annotation": chapter_count,
            "chapter_memory": chapter_count if enable_chapter_memory else 0,
        }
    llm_calls = sum(breakdown.values())
    max_tokens = int(generation.get("max_tokens") or 4096)
    return {
        "chapter_count": chapter_count,
        "selected_chapter_ids": selected_chapter_ids,
        "target_chapter_ids": target_chapter_ids,
        "input_char_count": char_count,
        "estimated_llm_calls": llm_calls,
        "retry_call_ceiling": llm_calls * 3,
        "output_token_budget": max_tokens * llm_calls,
        "breakdown": breakdown,
        "notes": [
            "估算不调用 LLM，不会消耗额度。",
            "失败重试上限按每类调用最多 3 次估算，实际通常低于该值。",
        ],
    }

def _script_action_items() -> dict[str, Any]:
    manifest = load_current_chapters_manifest()
    progress = _script_chapter_progress(manifest)
    story_exists = os.path.exists(_current_story_bible_path())
    items: list[dict[str, Any]] = []
    for chapter in progress.get("chapters") or []:
        chapter_id = chapter.get("chapter_id")
        title = chapter.get("chapter_title") or chapter_id
        if not chapter.get("generated"):
            items.append({
                "severity": "warning" if chapter.get("partial") or chapter.get("cancelled") else "info",
                "code": "missing_script",
                "chapter_id": chapter_id,
                "title": title,
                "message": f"{title} 还没有完整脚本。",
                "action": "resume_generation",
            })
        if chapter.get("failed"):
            items.append({
                "severity": "error",
                "code": "failed_chapter",
                "chapter_id": chapter_id,
                "title": title,
                "message": f"{title} 上次生成失败：{chapter.get('error') or '未知错误'}",
                "action": "rerun_chapter",
            })
        if chapter.get("memory_stale"):
            items.append({
                "severity": "warning",
                "code": "stale_memory",
                "chapter_id": chapter_id,
                "title": title,
                "message": f"{title} 的章节记忆已过期。",
                "action": "rerun_from_chapter",
            })
        if chapter.get("unknown_speaker_count"):
            items.append({
                "severity": "warning",
                "code": "unknown_speaker",
                "chapter_id": chapter_id,
                "title": title,
                "message": f"{title} 有 {chapter.get('unknown_speaker_count')} 个未知说话人。",
                "action": "open_issue_detail",
            })
    issues = _load_script_issues().get("chapters") or {}
    for chapter_id, report in issues.items():
        if not isinstance(report, dict):
            continue
        ratio = report.get("source_coverage_ratio")
        if ratio is not None and float(ratio or 0) < 0.55:
            items.append({
                "severity": "warning",
                "code": "low_source_coverage",
                "chapter_id": chapter_id,
                "title": report.get("chapter_title") or chapter_id,
                "message": f"{report.get('chapter_title') or chapter_id} 原文覆盖率偏低（约 {round(float(ratio or 0) * 100)}%）。",
                "action": "review_or_rerun",
            })
        critical_missing = int(report.get("source_critical_missing_count") or 0)
        if critical_missing:
            items.append({
                "severity": "warning",
                "code": "missing_source_information_points",
                "chapter_id": chapter_id,
                "title": report.get("chapter_title") or chapter_id,
                "message": f"{report.get('chapter_title') or chapter_id} 可能遗漏 {critical_missing} 个高权重原文信息点。",
                "action": "review_or_rerun",
            })
    if progress.get("summary", {}).get("generated_chapters") and not story_exists:
        items.append({
            "severity": "info",
            "code": "missing_story_bible",
            "message": "已生成脚本，但 story_bible.json 尚未建立。",
            "action": "rebuild_story_bible",
        })
    severity_order = {"error": 0, "warning": 1, "info": 2}
    items.sort(key=lambda item: (severity_order.get(item.get("severity"), 3), item.get("chapter_id") or ""))
    return {
        "summary": {
            "total": len(items),
            "errors": sum(1 for item in items if item.get("severity") == "error"),
            "warnings": sum(1 for item in items if item.get("severity") == "warning"),
            "infos": sum(1 for item in items if item.get("severity") == "info"),
        },
        "items": items[:100],
    }

def _mark_memory_stale_after(chapter_ids: list[str], reason: str) -> None:
    chapter_set = {str(chapter_id or "") for chapter_id in chapter_ids if str(chapter_id or "")}
    if not chapter_set:
        return
    manifest = load_current_chapters_manifest()
    order = [str(chapter.get("chapter_id") or "") for chapter in manifest.get("chapters") or []]
    indexes = [idx for idx, chapter_id in enumerate(order) if chapter_id in chapter_set]
    if not indexes:
        return
    memory = _load_chapter_memory()
    chapters = memory.get("chapters") if isinstance(memory.get("chapters"), dict) else {}
    for chapter_id in chapter_set:
        chapters.pop(chapter_id, None)
    for chapter_id in order[min(indexes) + 1:]:
        item = chapters.get(chapter_id)
        if isinstance(item, dict):
            item["stale"] = True
            item["stale_reason"] = reason
            item["stale_since"] = _now_iso()
    memory["chapters"] = chapters
    _save_chapter_memory(memory)

def _script_chapter_progress(manifest: dict) -> dict:
    _mark_stale_script_generation_interrupted(manifest)
    script_entries = _load_script_entries(_current_script_path())
    partial_entries = _load_script_entries(os.path.join(_current_book_dir(), "annotated_script.partial.json"))
    generation_state = _load_script_generation_state()
    generation_chapters = generation_state.get("chapters") if isinstance(generation_state.get("chapters"), dict) else {}
    generation_active = bool(process_state.get("script", {}).get("running"))
    generation_mode = str(generation_state.get("mode") or "").strip().lower()
    generation_engine = str(generation_state.get("engine") or "").strip().lower()
    is_character_progress_state = generation_mode == "characters" or generation_engine == "character_pipeline"
    character_analysis_state = _load_character_analysis_state()
    character_analysis_chapters = character_analysis_state.get("chapters") if isinstance(character_analysis_state.get("chapters"), dict) else {}
    chapter_memory = _load_chapter_memory()
    memory_chapters = chapter_memory.get("chapters") if isinstance(chapter_memory.get("chapters"), dict) else {}
    script_issues = _load_script_issues()
    issue_chapters = script_issues.get("chapters") if isinstance(script_issues.get("chapters"), dict) else {}

    entry_counts = {}
    partial_counts = {}
    speaker_counts = {}
    for entry in script_entries:
        chapter_id = str(entry.get("chapter_id") or "")
        if not chapter_id:
            continue
        entry_counts[chapter_id] = entry_counts.get(chapter_id, 0) + 1
        speaker = str(entry.get("speaker") or entry.get("type") or "").strip()
        if speaker:
            speaker_counts.setdefault(chapter_id, set()).add(speaker)
    for entry in partial_entries:
        chapter_id = str(entry.get("chapter_id") or "")
        if chapter_id:
            partial_counts[chapter_id] = partial_counts.get(chapter_id, 0) + 1

    chapters = []
    for chapter in manifest.get("chapters") or []:
        chapter_id = str(chapter.get("chapter_id") or "")
        count = entry_counts.get(chapter_id, 0)
        partial_count = partial_counts.get(chapter_id, 0)
        state = generation_chapters.get(chapter_id) if isinstance(generation_chapters.get(chapter_id), dict) else {}
        analysis_state = character_analysis_chapters.get(chapter_id) if isinstance(character_analysis_chapters.get(chapter_id), dict) else {}
        memory = memory_chapters.get(chapter_id) if isinstance(memory_chapters.get(chapter_id), dict) else {}
        issue_report = issue_chapters.get(chapter_id) if isinstance(issue_chapters.get(chapter_id), dict) else {}
        state_status = str(state.get("status") or "").strip().lower()
        if state_status == "running" and not generation_active:
            state_status = "interrupted"
        analysis_status = str(analysis_state.get("status") or "").strip().lower()
        if analysis_status == "running" and not generation_active:
            analysis_status = "interrupted"
        stale_unfinished = state_status in {"pending", "interrupted", "error", "cancelled"} and count > 0
        generated = count > 0 and not stale_unfinished
        legacy_character_analyzed = bool(is_character_progress_state and state_status == "done")
        legacy_character_analysis_running = bool(is_character_progress_state and state_status == "running")
        legacy_character_analysis_failed = bool(is_character_progress_state and state_status == "error")
        character_analyzed = analysis_status == "done" or legacy_character_analyzed
        character_analysis_running = analysis_status == "running" or legacy_character_analysis_running
        character_analysis_failed = analysis_status in {"error", "interrupted"} or legacy_character_analysis_failed
        failed = state_status == "error"
        running = not generated and state_status == "running"
        cancelled = state_status == "cancelled"
        interrupted = state_status == "interrupted"
        status = "generated" if generated else (state_status if state_status in {"running", "error", "cancelled", "interrupted"} else ("partial" if partial_count > 0 else "missing"))
        chapters.append({
            "chapter_id": chapter_id,
            "chapter_index": chapter.get("index"),
            "chapter_title": chapter.get("title") or chapter_id,
            "char_count": chapter.get("char_count") or 0,
            "entry_count": count,
            "partial_entry_count": partial_count,
            "speaker_count": len(speaker_counts.get(chapter_id) or set()),
            "generated": generated,
            "character_analyzed": character_analyzed,
            "character_analysis_running": character_analysis_running,
            "character_analysis_failed": character_analysis_failed,
            "partial": count == 0 and partial_count > 0,
            "failed": failed,
            "running": running,
            "cancelled": cancelled,
            "interrupted": interrupted,
            "status": status,
            "error": str(state.get("error") or ""),
            "updated_at": state.get("updated_at") or "",
            "last_entry_count": state.get("entry_count") or 0,
            "parse_issues": state.get("parse_issues") or 0,
            "issue_count": issue_report.get("issue_count") or state.get("issue_count") or 0,
            "error_count": issue_report.get("error_count") or state.get("error_count") or 0,
            "warning_count": issue_report.get("warning_count") or state.get("warning_count") or 0,
            "unknown_speaker_count": issue_report.get("unknown_speaker_count") or state.get("unknown_speaker_count") or 0,
            "unknown_speakers": issue_report.get("unknown_speakers") or [],
            "source_coverage_ratio": issue_report.get("source_coverage_ratio"),
            "source_uncovered_samples": issue_report.get("source_uncovered_samples") or [],
            "source_critical_missing_count": issue_report.get("source_critical_missing_count") or 0,
            "character_analysis_error": analysis_state.get("error") or (state.get("error") if legacy_character_analysis_failed else "") or "",
            "character_analysis_updated_at": analysis_state.get("updated_at") or (state.get("updated_at") if is_character_progress_state else "") or "",
            "memory_available": bool(memory),
            "memory_stale": bool(memory.get("stale")),
            "memory_summary": memory.get("summary") or "",
            "memory_ending_state": memory.get("ending_state") or "",
            "memory_stale_reason": memory.get("stale_reason") or "",
        })

    generated = sum(1 for chapter in chapters if chapter["generated"])
    partial = sum(1 for chapter in chapters if chapter["partial"])
    failed = sum(1 for chapter in chapters if chapter["failed"] and not chapter["generated"])
    running = sum(1 for chapter in chapters if chapter["running"] and not chapter["generated"])
    cancelled = sum(1 for chapter in chapters if chapter["cancelled"] and not chapter["generated"])
    interrupted = sum(1 for chapter in chapters if chapter["interrupted"] and not chapter["generated"])
    character_analyzed = sum(1 for chapter in chapters if chapter["character_analyzed"])
    character_analysis_running = sum(1 for chapter in chapters if chapter["character_analysis_running"])
    character_analysis_failed = sum(1 for chapter in chapters if chapter["character_analysis_failed"])
    manifest_ids = {str(chapter.get("chapter_id") or "") for chapter in manifest.get("chapters") or []}
    extra_issue_reports = [
        item for key, item in issue_chapters.items()
        if key not in manifest_ids and isinstance(item, dict)
    ]
    issue_chapter_count = sum(1 for chapter in chapters if chapter.get("issue_count"))
    stale_chapters = sum(1 for chapter in chapters if chapter.get("memory_stale"))
    return {
        "summary": {
            "total_chapters": len(chapters),
            "generated_chapters": generated,
            "missing_chapters": len(chapters) - generated,
            "partial_chapters": partial,
            "failed_chapters": failed,
            "running_chapters": running,
            "cancelled_chapters": cancelled,
            "interrupted_chapters": interrupted,
            "character_analyzed_chapters": character_analyzed,
            "character_analysis_running_chapters": character_analysis_running,
            "character_analysis_failed_chapters": character_analysis_failed,
            "issue_chapters": issue_chapter_count,
            "extra_issue_reports": len(extra_issue_reports),
            "stale_chapters": stale_chapters,
            "total_issues": (
                sum(chapter.get("issue_count") or 0 for chapter in chapters)
                + sum(item.get("issue_count") or 0 for item in extra_issue_reports)
            ),
            "unknown_speaker_chapters": (
                sum(1 for chapter in chapters if chapter.get("unknown_speaker_count"))
                + sum(1 for item in extra_issue_reports if item.get("unknown_speaker_count"))
            ),
            "low_source_coverage_chapters": sum(
                1
                for chapter in chapters
                if chapter.get("source_coverage_ratio") is not None
                and float(chapter.get("source_coverage_ratio") or 0) < 0.55
            ),
            "critical_source_missing_chapters": sum(
                1
                for chapter in chapters
                if int(chapter.get("source_critical_missing_count") or 0) > 0
            ),
            "total_entries": sum(chapter["entry_count"] for chapter in chapters),
            "partial_entries": sum(chapter["partial_entry_count"] for chapter in chapters),
            "state_status": generation_state.get("status") or "",
            "state_started_at": generation_state.get("started_at") or "",
            "state_finished_at": generation_state.get("finished_at") or "",
        },
        "chapters": chapters,
    }

# Initialize Project Manager for the currently selected book, or a placeholder
project_manager = _project_manager_for_current_book() if _current_book() else ProjectManager(ROOT_DIR)

# Reset any chunks stuck in "generating" from a prior interrupted session
_startup_chunks = project_manager.load_chunks()
if _startup_chunks:
    _reset_count = 0
    for chunk in _startup_chunks:
        if chunk.get("status") == "generating":
            chunk["status"] = "pending"
            _reset_count += 1
    if _reset_count:
        project_manager.save_chunks(_startup_chunks)
        print(f"Startup: reset {_reset_count} stuck 'generating' chunk(s) to 'pending'")
    del _startup_chunks, _reset_count

# CORS for development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Data Models
class LLMConfig(BaseModel):
    base_url: str = ""
    api_key: str = ""
    model_name: str
    provider: str = "anthropic"
    openai_api_type: str = "responses"

class TTSConfig(BaseModel):
    mode: str = "local"  # "local" or "external"
    url: str = "http://127.0.0.1:7860"  # external mode only
    device: str = "auto"  # local mode: "auto", "cuda:0", "cpu", etc.
    language: str = "Chinese"  # TTS language
    parallel_workers: int = 2  # concurrent TTS workers
    dashscope_api_key: str = ""  # Alibaba Cloud DashScope API key (for dashscope voice type)
    volcengine_api_key: str = ""  # Volcengine API key (for volcengine voice type)
    volcengine_resource_id: str = "seed-tts-2.0"  # Doubao TTS resource/model id
    volcengine_sample_rate: int = 24000  # output sample rate for Volcengine PCM/WAV
    volcengine_uid: str = "voc-studio"  # user id sent to Volcengine
    batch_seed: Optional[int] = None  # Single seed for batch mode, None/-1 = random
    compile_codec: bool = False  # torch.compile the codec for ~3-4x batch throughput (slow first run)
    sub_batch_enabled: bool = True  # split batch by text length to reduce padding waste
    sub_batch_min_size: int = 4  # minimum chunks per sub-batch before allowing a split
    sub_batch_ratio: float = 5.0  # max longest/shortest length ratio before splitting
    sub_batch_max_items: int = 0  # hard cap on sequences per sub-batch (0 = auto from VRAM estimate)
    batch_group_by_type: bool = False  # group chunks by voice type for efficient batching
    pause_between_speakers_ms: int = 500  # silence (ms) between different speakers during merge
    pause_same_speaker_ms: int = 250  # silence (ms) when same speaker continues during merge

class GenerationConfig(BaseModel):
    model_name: Optional[str] = None
    chunk_size: int = 3000
    max_tokens: int = 4096
    temperature: float = 0.6
    top_p: float = 0.8
    top_k: int = 0
    min_p: float = 0
    presence_penalty: float = 0.0
    banned_tokens: List[str] = []
    merge_narrators: bool = False
    enable_chapter_memory: bool = True
    review_batch_size: int = 25

class PromptConfig(BaseModel):
    system_prompt: Optional[str] = None
    user_prompt: Optional[str] = None
    review_system_prompt: Optional[str] = None
    review_user_prompt: Optional[str] = None

class AppConfig(BaseModel):
    llm: LLMConfig
    tts: TTSConfig
    prompts: Optional[PromptConfig] = None
    generation: Optional[GenerationConfig] = None

class VoiceConfigItem(BaseModel):
    type: str = "custom"
    voice: Optional[str] = None
    character_style: Optional[str] = ""
    default_style: Optional[str] = ""  # backward compat, prefer character_style
    seed: Optional[str] = "-1"
    confirmed: bool = False
    ref_audio: Optional[str] = None
    ref_text: Optional[str] = None
    adapter_id: Optional[str] = None
    adapter_path: Optional[str] = None
    description: Optional[str] = ""  # voice description (for design type)
    edge_voice: Optional[str] = None
    edge_rate: Optional[str] = "+0%"
    edge_pitch: Optional[str] = "+0Hz"
    dashscope_model: Optional[str] = None
    dashscope_voice: Optional[str] = None
    language_type: Optional[str] = None
    volcengine_resource_id: Optional[str] = None
    volcengine_speaker: Optional[str] = None
    volcengine_sample_rate: Optional[int] = None
    volcengine_speech_rate: Optional[int] = 0
    volcengine_loudness_rate: Optional[int] = 0
    volcengine_emotion: Optional[str] = ""
    volcengine_emotion_scale: Optional[int] = 4

class ChunkUpdate(BaseModel):
    text: Optional[str] = None
    instruct: Optional[str] = None
    speaker: Optional[str] = None
    pause_after: Optional[int] = None

class TaggedScriptImportRequest(BaseModel):
    content: str
    default_instruct: str = ""
    chapter_id: Optional[str] = None
    replace_scope: str = "all"  # all, chapter
    dry_run: bool = False

class BatchGenerateRequest(BaseModel):
    indices: List[int] = Field(default_factory=list)
    chapter_id: Optional[str] = None
    regenerate_all: bool = False
    dry_run: bool = False

class RenderPlanRequest(BaseModel):
    chapter_id: Optional[str] = None
    regenerate_all: bool = False

class VoicePreviewRequest(BaseModel):
    text: str = "那座古老图书馆立在两条被遗忘小径的交叉口，斑驳石墙上爬满已经生长了数百年的藤蔓。"
    voice_name: str
    voice_config: Dict[str, Any]

class VoiceDesignPreviewRequest(BaseModel):
    description: str
    sample_text: str
    language: Optional[str] = None

class VoiceDesignSaveRequest(BaseModel):
    name: str
    description: str
    sample_text: str
    preview_file: str

class LoraTrainingRequest(BaseModel):
    name: str
    dataset_id: str
    epochs: int = 5
    lr: float = 5e-6
    batch_size: int = 1
    lora_r: int = 32
    lora_alpha: int = 128
    gradient_accumulation_steps: int = 8
    language: str = "chinese"

class LoraTestRequest(BaseModel):
    adapter_id: str
    text: str
    instruct: str = ""

class LoraDatasetSample(BaseModel):
    emotion: str = ""
    text: str

class LoraGenerateDatasetRequest(BaseModel):
    name: str
    description: str  # root voice description
    samples: Optional[List[LoraDatasetSample]] = None  # emotion+text pairs
    texts: Optional[List[str]] = None  # back-compat: flat text list (no emotions)
    language: Optional[str] = None

class DatasetSampleGenRequest(BaseModel):
    description: str      # full voice description (root + emotion already combined by frontend)
    text: str
    dataset_name: str     # working directory name
    sample_index: int     # row number
    seed: int = -1        # -1 = random, >= 0 = manual seed

class DatasetBatchGenRequest(BaseModel):
    name: str
    description: str      # root voice description
    samples: List[LoraDatasetSample]
    indices: Optional[List[int]] = None  # which rows to generate (None = all)
    global_seed: int = -1 # -1 = random, >= 0 = same seed for all lines
    seeds: Optional[List[int]] = None  # per-line seeds (overrides global_seed)

class DatasetSaveRequest(BaseModel):
    name: str
    ref_index: int = 0    # which sample to use as ref.wav

class DatasetBuilderCreateRequest(BaseModel):
    name: str

class DatasetBuilderUpdateMetaRequest(BaseModel):
    name: str
    description: str = ""
    global_seed: str = ""

class DatasetBuilderUpdateRowsRequest(BaseModel):
    name: str
    rows: List[dict]  # [{emotion, text, seed}]

class BookCreateRequest(BaseModel):
    title: str

class BookSelectRequest(BaseModel):
    book_id: str

class ScriptGenerateRequest(BaseModel):
    chapter_ids: Optional[List[str]] = None
    missing_only: bool = False
    dry_run: bool = False
    mode: str = "script"  # script, characters
    reuse_character_book: bool = False


class ScriptReviewRequest(BaseModel):
    dry_run: bool = False


class ChapterUpdateRequest(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None

class ChapterSplitRequest(BaseModel):
    split_at: Optional[int] = None
    title: str = ""
    new_title: str = ""
    content_before: Optional[str] = None
    content_after: Optional[str] = None

class ChapterMergeNextRequest(BaseModel):
    title: str = ""
    content: Optional[str] = None

class ChapterResplitRequest(BaseModel):
    confirm_invalidate: bool = False
    dry_run: bool = False

class CharacterItem(BaseModel):
    name: str
    aliases: List[str] = []
    traits: str = ""
    voice_profile: str = ""
    confidence: Optional[float] = None

class CharacterBookSaveRequest(BaseModel):
    characters: List[CharacterItem]
    narrator_style: str = ""
    genre: str = ""
    key_terms: List[str] = []
    normalize_script_speakers: bool = True
    voice_config: Optional[Dict[str, VoiceConfigItem]] = None

class CharacterBookImportRequest(BaseModel):
    content: str
    merge: bool = True
    normalize_script_speakers: bool = True
    dry_run: bool = False

class CharacterVoiceApplyRequest(BaseModel):
    names: Optional[List[str]] = None
    mode: str = "missing"  # missing, overwrite, append

def _voice_config_item_dict(config: Any) -> dict[str, Any]:
    if hasattr(config, "model_dump"):
        item = config.model_dump()
    elif isinstance(config, dict):
        item = VoiceConfigItem(**config).model_dump()
    else:
        raise ValueError("Invalid voice config item")

    if item.get("type") == "dashscope":
        model = item.get("dashscope_model") or DASHSCOPE_QWEN3_INSTRUCT_MODEL
        voice = str(item.get("dashscope_voice") or "").strip()
        if model == DASHSCOPE_QWEN3_INSTRUCT_MODEL and voice in DASHSCOPE_QWEN3_FLASH_ONLY_VOICES:
            model = DASHSCOPE_QWEN3_FLASH_MODEL
        item["dashscope_model"] = model
    elif item.get("type") == "volcengine":
        item["volcengine_resource_id"] = item.get("volcengine_resource_id") or "seed-tts-2.0"
        item["volcengine_sample_rate"] = item.get("volcengine_sample_rate") or 24000
        item["volcengine_speech_rate"] = item.get("volcengine_speech_rate") or 0
        item["volcengine_loudness_rate"] = item.get("volcengine_loudness_rate") or 0
        item["volcengine_emotion_scale"] = item.get("volcengine_emotion_scale") or 4
    return item

def _voice_config_effective_signature(config: Any) -> dict[str, Any]:
    """Return the TTS-affecting part of a voice config.

    UI-only confirmation state should not force audio regeneration.
    """
    if not isinstance(config, dict) and not hasattr(config, "model_dump"):
        return {}
    try:
        item = _voice_config_item_dict(config)
    except (TypeError, ValueError):
        return {}

    style = str(item.get("character_style") or item.get("default_style") or "").strip()
    item["character_style"] = style
    item.pop("default_style", None)
    item.pop("confirmed", None)
    config_type = str(item.get("type") or "custom")
    fields_by_type = {
        "custom": ("type", "voice", "character_style", "seed"),
        "dashscope": ("type", "dashscope_model", "dashscope_voice", "character_style", "seed"),
        "volcengine": (
            "type",
            "volcengine_resource_id",
            "volcengine_speaker",
            "volcengine_sample_rate",
            "volcengine_speech_rate",
            "volcengine_loudness_rate",
            "volcengine_emotion",
            "volcengine_emotion_scale",
            "character_style",
        ),
        "edge": ("type", "edge_voice", "edge_rate", "edge_pitch"),
        "clone": ("type", "ref_audio", "ref_text", "character_style", "seed"),
        "builtin_lora": ("type", "adapter_id", "adapter_path", "character_style", "seed"),
        "lora": ("type", "adapter_id", "adapter_path", "character_style", "seed"),
        "design": ("type", "description", "seed"),
    }
    fields = fields_by_type.get(config_type, tuple(sorted(item.keys())))
    return {
        key: item.get(key)
        for key in fields
        if item.get(key) not in (None, "", [], {})
    }

def _save_voice_config_mapping(
    book_dir: str,
    config_data: Optional[dict[str, Any]],
    *,
    character_book: Optional[dict] = None,
    normalize_names: bool = False,
) -> dict[str, Any]:
    voice_config_path = os.path.join(book_dir, "voice_config.json")
    character_book = character_book if isinstance(character_book, dict) else _load_character_book(book_dir)

    current_config = _read_json_payload(voice_config_path, {})
    if not isinstance(current_config, dict):
        current_config = {}
    current_config, normalized_names, removed_names = _clean_voice_config_mapping(current_config, character_book)
    previous_config = {
        str(name): dict(config)
        for name, config in current_config.items()
        if isinstance(config, dict)
    }

    saved_names: list[str] = []
    changed_names: list[str] = []
    seen: set[str] = set()
    changed_seen: set[str] = set()
    removed_seen = {name.casefold() for name in removed_names}

    for voice_name, config in (config_data or {}).items():
        original_name = str(voice_name or "").strip()
        if not original_name:
            continue
        config_name = (
            _normalize_speaker_name(original_name, _character_lookup(character_book)) or original_name
            if normalize_names
            else original_name
        )
        item = _voice_config_item_dict(config)
        previous_item = previous_config.get(config_name)
        if not _voice_config_has_required_choice(item):
            current_config.pop(config_name, None)
            key = config_name.casefold()
            if key not in removed_seen:
                removed_names.append(config_name)
                removed_seen.add(key)
            continue
        current_config[config_name] = item
        key = config_name.casefold()
        if key not in seen:
            saved_names.append(config_name)
            seen.add(key)
        if not isinstance(previous_item, dict):
            previous_item = {}
        previous_signature = _voice_config_effective_signature(previous_item)
        current_signature = _voice_config_effective_signature(item)
        if previous_signature != current_signature and key not in changed_seen:
            changed_names.append(config_name)
            changed_seen.add(key)

    if saved_names or removed_names or normalized_names:
        if current_config:
            _write_json(voice_config_path, current_config)
        elif os.path.exists(voice_config_path):
            os.remove(voice_config_path)

    return {
        "voice_config": current_config,
        "saved": len(saved_names),
        "saved_names": saved_names,
        "changed": len(changed_names),
        "changed_names": changed_names,
        "removed": len(removed_names),
        "removed_names": removed_names,
        "normalized": len(normalized_names),
        "normalized_names": normalized_names,
        "total": len(current_config),
    }

def _voice_audio_speaker_names(
    book_dir: str,
    speaker_names: list[str],
    character_book: Optional[dict] = None,
) -> set[str]:
    speaker_set: set[str] = set()

    for name in speaker_names:
        raw_name = str(name or "").strip()
        if not raw_name:
            continue
        speaker_set.add(raw_name)

    return speaker_set

def _invalidate_voice_audio(
    book_dir: str,
    speaker_names: list[str],
    *,
    character_book: Optional[dict] = None,
) -> dict[str, Any]:
    speaker_set = _voice_audio_speaker_names(book_dir, speaker_names, character_book)
    if not speaker_set:
        return {"invalidated_chunks": 0, "removed_audio_files": []}

    chunks_path = os.path.join(book_dir, "chunks.json")
    chunks = _read_json_list(chunks_path)
    if not chunks:
        return {"invalidated_chunks": 0, "removed_audio_files": []}

    invalidated = 0
    changed = False
    old_audio_paths: list[Any] = []
    for chunk in chunks:
        if str(chunk.get("speaker") or "").strip() not in speaker_set:
            continue
        old_audio_path = chunk.get("audio_path")
        if old_audio_path:
            old_audio_paths.append(old_audio_path)
        if chunk.get("audio_path") or chunk.get("status") == "done":
            invalidated += 1
        if chunk.get("status") != "pending" or chunk.get("audio_path") is not None:
            changed = True
            chunk["status"] = "pending"
            chunk["audio_path"] = None

    if changed:
        _write_json(chunks_path, chunks)

    removed_audio_files = _remove_unreferenced_audio_files(book_dir, old_audio_paths, chunks)
    return {
        "invalidated_chunks": invalidated,
        "removed_audio_files": removed_audio_files,
    }

# Global state for process tracking
process_state = {
    "script": {"running": False, "logs": []},
    "voices": {"running": False, "logs": []},
    "audio": {"running": False, "logs": [], "cancel": False},
    "audacity_export": {"running": False, "logs": []},
    "m4b_export": {"running": False, "logs": []},
    "review": {"running": False, "logs": []},
    "lora_training": {"running": False, "logs": []},
    "dataset_gen": {"running": False, "logs": []},
    "dataset_builder": {"running": False, "logs": [], "cancel": False},
    "module_install": {
        "running": False,
        "logs": [],
        "module_id": None,
        "error": "",
        "cancel": False,
        "started_at": "",
        "finished_at": "",
    },
}
running_processes = {}

PROCESS_TASK_ALIASES = {
    "script_generation": "script",
}

def _resolve_process_task_name(task_name: str) -> str:
    return PROCESS_TASK_ALIASES.get(task_name, task_name)

@app.get("/api/books")
async def list_books():
    manifest = _load_books_manifest()
    return {
        "current_book_id": manifest.get("current_book_id"),
        "books": manifest.get("books", []),
    }

@app.get("/api/books/current")
async def get_current_book():
    return _current_book() or {}

@app.post("/api/books")
async def create_book(request: BookCreateRequest):
    book = _create_book(request.title, select=True)
    global project_manager
    project_manager = ProjectManager(_book_dir(book["id"]))
    return {"status": "created", "book": book}

@app.post("/api/books/select")
async def select_book(request: BookSelectRequest):
    if process_state["audio"]["running"] or process_state["script"]["running"] or process_state["review"]["running"]:
        raise HTTPException(status_code=409, detail="Cannot switch books while generation is running.")
    manifest = _load_books_manifest()
    book = _find_book(manifest, request.book_id)
    if not book:
        raise HTTPException(status_code=404, detail=f"Book '{request.book_id}' not found.")
    manifest["current_book_id"] = request.book_id
    _save_books_manifest(manifest)
    global project_manager
    project_manager = ProjectManager(_book_dir(request.book_id))
    return {"status": "selected", "book": book}

@app.delete("/api/books/{book_id}")
async def delete_book(book_id: str):
    if process_state["audio"]["running"] or process_state["script"]["running"] or process_state["review"]["running"]:
        raise HTTPException(status_code=409, detail="Cannot delete a book while generation is running.")
    manifest = _load_books_manifest()
    book = _find_book(manifest, book_id)
    if not book:
        raise HTTPException(status_code=404, detail=f"Book '{book_id}' not found.")
    manifest["books"] = [item for item in manifest.get("books", []) if item.get("id") != book_id]
    if manifest.get("current_book_id") == book_id:
        manifest["current_book_id"] = manifest["books"][0]["id"] if manifest["books"] else None
    shutil.rmtree(_book_dir(book_id), ignore_errors=True)
    _save_books_manifest(manifest)
    global project_manager
    project_manager = _project_manager_for_current_book() if manifest.get("current_book_id") else ProjectManager(ROOT_DIR)
    return {"status": "deleted", "book_id": book_id, "current_book_id": manifest.get("current_book_id")}


_EXPORT_CONFIG_FILES = [
    "voice_config.json",
    "character_book.json",
    "annotated_script.json",
    "chunks.json",
    "story_bible.json",
    "chapter_memory.json",
    "script_generation_state.json",
    "script_issues.json",
    "character_analysis_state.json",
    "state.json",
]


@app.get("/api/books/{book_id}/export_config")
async def export_book_config(book_id: str):
    manifest = _load_books_manifest()
    book = _find_book(manifest, book_id)
    if not book:
        raise HTTPException(status_code=404, detail=f"Book '{book_id}' not found.")

    book_dir = _book_dir(book_id)
    buf = io.BytesIO()

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        meta = {
            "export_version": 1,
            "exported_at": _now_iso(),
            "book_id": book_id,
            "book_title": book.get("title", book_id),
        }
        zf.writestr("_export_meta.json", json.dumps(meta, indent=2, ensure_ascii=False))

        for filename in _EXPORT_CONFIG_FILES:
            filepath = os.path.join(book_dir, filename)
            if not os.path.exists(filepath):
                continue
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except (json.JSONDecodeError, ValueError):
                zf.write(filepath, filename)
                continue

            if filename == "chunks.json" and isinstance(data, list):
                for chunk in data:
                    if isinstance(chunk, dict):
                        chunk["status"] = "pending"
                        chunk["audio_path"] = None

            zf.writestr(filename, json.dumps(data, indent=2, ensure_ascii=False))

        chapters_dir = os.path.join(book_dir, "chapters")
        if os.path.isdir(chapters_dir):
            for name in sorted(os.listdir(chapters_dir)):
                full = os.path.join(chapters_dir, name)
                if os.path.isfile(full):
                    zf.write(full, f"chapters/{name}")

        source_dir = os.path.join(book_dir, "source")
        if os.path.isdir(source_dir):
            for name in sorted(os.listdir(source_dir)):
                full = os.path.join(source_dir, name)
                if os.path.isfile(full):
                    zf.write(full, f"source/{name}")

    buf.seek(0)
    raw_title = book.get("title", book_id)
    utf8_name = quote(raw_title + "_config.zip")
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={
            "Content-Disposition": (
                f"attachment; filename=\"config.zip\"; "
                f"filename*=UTF-8''{utf8_name}"
            ),
        },
    )


@app.post("/api/books/{book_id}/import_config")
async def import_book_config(book_id: str, file: UploadFile = File(...)):
    if process_state["audio"]["running"] or process_state["script"]["running"] or process_state["review"]["running"]:
        raise HTTPException(status_code=409, detail="Cannot import while generation is running.")

    manifest = _load_books_manifest()
    book = _find_book(manifest, book_id)
    if not book:
        raise HTTPException(status_code=404, detail=f"Book '{book_id}' not found.")

    content = await file.read()
    try:
        zf = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="Invalid ZIP file.")

    if "_export_meta.json" not in zf.namelist():
        raise HTTPException(status_code=400, detail="Not a valid book config export (missing _export_meta.json).")

    book_dir = _book_dir(book_id)
    imported_files = []
    allowed_config = set(_EXPORT_CONFIG_FILES)

    for name in zf.namelist():
        if name == "_export_meta.json" or name.endswith("/"):
            continue

        if name in allowed_config:
            data = zf.read(name)
            if name == "chunks.json":
                try:
                    chunks = json.loads(data)
                    if isinstance(chunks, list):
                        for chunk in chunks:
                            if isinstance(chunk, dict):
                                chunk["status"] = "pending"
                                chunk["audio_path"] = None
                        data = json.dumps(chunks, indent=2, ensure_ascii=False).encode("utf-8")
                except (json.JSONDecodeError, ValueError):
                    pass
            target = _safe_book_import_path(book_dir, name)
            if not target:
                continue
            with open(target, "wb") as f:
                f.write(data if isinstance(data, bytes) else data.encode("utf-8"))
            imported_files.append(name)

        elif name.startswith("chapters/") or name.startswith("source/"):
            target = _safe_book_import_path(book_dir, name)
            if not target:
                continue
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "wb") as f:
                f.write(zf.read(name))
            imported_files.append(name)

    zf.close()

    chapter_count = 0
    chapters_manifest_path = os.path.join(book_dir, "chapters", "manifest.json")
    if os.path.exists(chapters_manifest_path):
        try:
            with open(chapters_manifest_path, "r", encoding="utf-8") as f:
                cm = json.load(f)
            chapter_count = cm.get("chapter_count", 0)
        except (json.JSONDecodeError, ValueError):
            pass

    state_path = os.path.join(book_dir, "state.json")
    if os.path.exists(state_path):
        try:
            with open(state_path, "r", encoding="utf-8") as f:
                state = json.load(f)
            source_dir = os.path.join(book_dir, "source")
            source_files = sorted(os.listdir(source_dir)) if os.path.isdir(source_dir) else []
            txt_files = [fn for fn in source_files if fn.endswith(".txt")]
            if txt_files:
                state["input_file_path"] = os.path.join(source_dir, txt_files[0])
            state["chapters_manifest_path"] = chapters_manifest_path
            state["chapter_count"] = chapter_count
            with open(state_path, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2, ensure_ascii=False)
        except (json.JSONDecodeError, ValueError):
            pass

    source_filename = ""
    source_dir = os.path.join(book_dir, "source")
    if os.path.isdir(source_dir):
        for fn in sorted(os.listdir(source_dir)):
            if fn.endswith((".txt", ".md", ".epub")):
                source_filename = fn
                break

    _touch_book(book_id, chapter_count=chapter_count, source_filename=source_filename)

    global project_manager
    if manifest.get("current_book_id") == book_id:
        project_manager = ProjectManager(book_dir)

    return {
        "status": "imported",
        "book_id": book_id,
        "imported_files": imported_files,
        "file_count": len(imported_files),
        "chapter_count": chapter_count,
    }


@app.get("/api/desktop")
async def get_desktop_metadata():
    return _desktop_metadata()


@app.get("/api/modules")
async def get_modules():
    config = read_json_config()
    return {
        "app_name": APP_NAME,
        "desktop": _desktop_metadata(),
        "install_task": _install_task_state(),
        "modules": all_module_statuses(
            config=config,
            data_dir=DATA_DIR,
            cache_dir=CACHE_DIR,
            install_task=_install_task_state(),
        ),
    }


@app.get("/api/modules/install/status")
async def get_module_install_status():
    return _install_task_state()


@app.post("/api/modules/install/cancel")
async def cancel_module_install():
    state = _install_task_state()
    if not state.get("running"):
        return {"status": "idle"}
    state["cancel"] = True
    _append_install_log("Cancel requested. The active download will stop at the next safe checkpoint.")
    return {"status": "cancel_requested", "module_id": state.get("module_id")}


@app.get("/api/modules/{module_id}")
async def get_module(module_id: str):
    module = module_definition(module_id)
    if not module:
        raise HTTPException(status_code=404, detail="Module not found")
    return module_status(module, config=read_json_config(), data_dir=DATA_DIR, cache_dir=CACHE_DIR)


@app.post("/api/modules/{module_id}/install")
async def install_module(module_id: str, background_tasks: BackgroundTasks):
    module = module_definition(module_id)
    if not module:
        raise HTTPException(status_code=404, detail="Module not found")
    if module.install_kind != "hf_snapshot":
        detail = module.manual_hint or "This module cannot be installed automatically yet."
        raise HTTPException(status_code=400, detail=detail)
    state = _install_task_state()
    if state.get("running"):
        raise HTTPException(status_code=409, detail="Another module install is already running.")
    background_tasks.add_task(_run_module_install, module_id)
    return {"status": "installing", "module_id": module_id}


@app.post("/api/modules/{module_id}/repair")
async def repair_module(module_id: str, background_tasks: BackgroundTasks):
    return await install_module(module_id, background_tasks)


@app.post("/api/modules/{module_id}/uninstall")
async def uninstall_module(module_id: str):
    module = module_definition(module_id)
    if not module:
        raise HTTPException(status_code=404, detail="Module not found")
    raise HTTPException(
        status_code=501,
        detail="Automatic uninstall is not implemented yet. Use the model cache path shown in the module details.",
    )


def run_process(command: List[str], task_name: str):
    """Run a subprocess and capture logs."""
    global process_state
    process_state[task_name]["running"] = True
    process_state[task_name]["logs"] = []

    logger.info(f"Starting task {task_name}: {' '.join(command)}")

    try:
        env = os.environ.copy()
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=BASE_DIR,
            bufsize=1,
            universal_newlines=True,
            env=env,
        )
        running_processes[task_name] = process

        for line in process.stdout:
            log_line = line.strip()
            if log_line:
                process_state[task_name]["logs"].append(log_line)
                # Keep log size manageable
                if len(process_state[task_name]["logs"]) > 1000:
                    process_state[task_name]["logs"].pop(0)

        process.wait()
        return_code = process.returncode

        if return_code == 0:
            if task_name == "script":
                try:
                    _mark_generation_snapshot_completed()
                    bible = _build_story_bible_for_current_book()
                    process_state[task_name]["logs"].append(
                        "[EVENT] " + json.dumps({
                            "type": "story_bible_done",
                            "data": {
                                "chapter_count": bible.get("chapter_count", 0),
                                "character_count": len(bible.get("characters") or []),
                                "path": "story_bible.json",
                            },
                        }, ensure_ascii=False)
                    )
                except Exception as exc:
                    logger.warning("Failed to finalize script outputs: %s", exc)
                    process_state[task_name]["logs"].append(
                        "[EVENT] " + json.dumps({
                            "type": "story_bible_error",
                            "data": {"message": str(exc)},
                        }, ensure_ascii=False)
                    )
            process_state[task_name]["logs"].append(f"Task {task_name} completed successfully.")
        else:
            if task_name == "script":
                try:
                    _mark_stale_script_generation_interrupted(force=True)
                    snapshot = _mark_generation_snapshot_interrupted(f"script task failed with return code {return_code}")
                    process_state[task_name]["logs"].append(
                        "[EVENT] " + json.dumps({
                            "type": "interrupted",
                            "data": {
                                "status": snapshot.get("status") or "interrupted",
                                "reason": f"script task failed with return code {return_code}",
                            },
                        }, ensure_ascii=False)
                    )
                except Exception as exc:
                    logger.warning("Failed to mark script generation interrupted: %s", exc)
                    process_state[task_name]["logs"].append(
                        "[EVENT] " + json.dumps({
                            "type": "interrupted",
                            "data": {"message": str(exc)},
                        }, ensure_ascii=False)
                    )
            process_state[task_name]["logs"].append(f"Task {task_name} failed with return code {return_code}.")

    except Exception as e:
        logger.error(f"Error running {task_name}: {e}")
        if task_name == "script":
            try:
                _mark_stale_script_generation_interrupted(force=True)
                snapshot = _mark_generation_snapshot_interrupted(f"script task raised {type(e).__name__}")
                process_state[task_name]["logs"].append(
                    "[EVENT] " + json.dumps({
                        "type": "interrupted",
                        "data": {
                            "status": snapshot.get("status") or "interrupted",
                            "reason": f"script task raised {type(e).__name__}",
                        },
                    }, ensure_ascii=False)
                )
            except Exception as interrupted_exc:
                logger.warning("Failed to mark script generation interrupted after exception: %s", interrupted_exc)
        process_state[task_name]["logs"].append(f"Error: {str(e)}")
    finally:
        running_processes.pop(task_name, None)
        process_state[task_name]["running"] = False

def read_json_config() -> Dict:
    if not os.path.exists(CONFIG_PATH):
        return {}
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, ValueError):
        return {}

def _desktop_metadata() -> dict[str, Any]:
    return {
        "app_name": APP_NAME,
        "desktop": os.environ.get("VOC_STUDIO_DESKTOP", "").lower() in ("1", "true", "yes"),
        "base_dir": BASE_DIR,
        "source_dir": ROOT_DIR,
        "data_dir": DATA_DIR,
        "config_dir": CONFIG_DIR,
        "cache_dir": CACHE_DIR,
        "static_dir": STATIC_DIR,
        "python": sys.executable,
        "platform": platform.platform(),
        "hf_home": os.environ.get("HF_HOME", ""),
        "hf_endpoint": os.environ.get("HF_ENDPOINT", ""),
    }

def _install_task_state() -> dict[str, Any]:
    return process_state.get("module_install", {"running": False, "logs": []})

def _append_install_log(message: str) -> None:
    state = _install_task_state()
    state.setdefault("logs", []).append(message)
    if len(state["logs"]) > 1000:
        state["logs"].pop(0)

def _run_module_install(module_id: str) -> None:
    state = _install_task_state()
    state.update({
        "running": True,
        "module_id": module_id,
        "logs": [f"Starting module install: {module_id}"],
        "error": "",
        "cancel": False,
        "started_at": _now_iso(),
        "finished_at": "",
    })
    try:
        module = module_definition(module_id)
        if not module:
            raise RuntimeError(f"Unknown module: {module_id}")
        install_huggingface_snapshot(
            module,
            log=_append_install_log,
            should_cancel=lambda: bool(state.get("cancel")),
        )
        _append_install_log(f"Module {module_id} installed.")
    except Exception as exc:
        state["error"] = str(exc)
        _append_install_log(f"Install failed: {exc}")
    finally:
        state["running"] = False
        state["finished_at"] = _now_iso()

VOLCENGINE_FALLBACK_VOICES = {
    "seed-tts-2.0": [
        {"value": "zh_female_vv_uranus_bigtts", "label": "Vivi 2.0（通用场景）", "name": "Vivi 2.0", "scene": "通用场景"},
        {"value": "zh_female_xiaohe_uranus_bigtts", "label": "小何 2.0（通用场景）", "name": "小何 2.0", "scene": "通用场景"},
        {"value": "zh_male_m191_uranus_bigtts", "label": "云舟 2.0（通用场景）", "name": "云舟 2.0", "scene": "通用场景"},
        {"value": "zh_male_taocheng_uranus_bigtts", "label": "小天 2.0（通用场景）", "name": "小天 2.0", "scene": "通用场景"},
        {"value": "zh_male_liufei_uranus_bigtts", "label": "刘飞 2.0（通用场景）", "name": "刘飞 2.0", "scene": "通用场景"},
        {"value": "zh_female_sophie_uranus_bigtts", "label": "魅力苏菲 2.0（通用场景）", "name": "魅力苏菲 2.0", "scene": "通用场景"},
        {"value": "zh_female_qingxinnvsheng_uranus_bigtts", "label": "清新女声 2.0（通用场景）", "name": "清新女声 2.0", "scene": "通用场景"},
        {"value": "zh_female_cancan_uranus_bigtts", "label": "知性灿灿 2.0（角色扮演）", "name": "知性灿灿 2.0", "scene": "角色扮演"},
        {"value": "zh_female_gaolengyujie_uranus_bigtts", "label": "高冷御姐 2.0（通用场景）", "name": "高冷御姐 2.0", "scene": "通用场景"},
        {"value": "zh_female_shuangkuaisisi_uranus_bigtts", "label": "爽快思思 2.0（通用场景）", "name": "爽快思思 2.0", "scene": "通用场景"},
        {"value": "zh_female_wenroumama_uranus_bigtts", "label": "温柔妈妈 2.0（通用场景）", "name": "温柔妈妈 2.0", "scene": "通用场景"},
        {"value": "zh_male_jieshuoxiaoming_uranus_bigtts", "label": "解说小明 2.0（通用场景）", "name": "解说小明 2.0", "scene": "通用场景"},
        {"value": "zh_male_ruyaqingnian_uranus_bigtts", "label": "儒雅青年 2.0（通用场景）", "name": "儒雅青年 2.0", "scene": "通用场景"},
        {"value": "en_male_tim_uranus_bigtts", "label": "Tim（多语种）", "name": "Tim", "scene": "多语种"},
        {"value": "en_female_dacey_uranus_bigtts", "label": "Dacey（多语种）", "name": "Dacey", "scene": "多语种"},
    ],
    "seed-tts-1.0": [
        {"value": "zh_female_gaolengyujie_moon_bigtts", "label": "高冷御姐 1.0（通用场景）", "name": "高冷御姐", "scene": "通用场景"},
        {"value": "zh_female_shuangkuaisisi_moon_bigtts", "label": "爽快思思 1.0（通用场景）", "name": "爽快思思", "scene": "通用场景"},
        {"value": "zh_female_qingxinnvsheng_moon_bigtts", "label": "清新女声 1.0（通用场景）", "name": "清新女声", "scene": "通用场景"},
        {"value": "zh_male_jieshuoxiaoming_moon_bigtts", "label": "解说小明 1.0（通用场景）", "name": "解说小明", "scene": "通用场景"},
    ],
    "seed-tts-1.0-concurr": [
        {"value": "zh_female_gaolengyujie_moon_bigtts", "label": "高冷御姐 1.0（通用场景）", "name": "高冷御姐", "scene": "通用场景"},
        {"value": "zh_female_shuangkuaisisi_moon_bigtts", "label": "爽快思思 1.0（通用场景）", "name": "爽快思思", "scene": "通用场景"},
        {"value": "zh_female_qingxinnvsheng_moon_bigtts", "label": "清新女声 1.0（通用场景）", "name": "清新女声", "scene": "通用场景"},
        {"value": "zh_male_jieshuoxiaoming_moon_bigtts", "label": "解说小明 1.0（通用场景）", "name": "解说小明", "scene": "通用场景"},
    ],
    "seed-icl-2.0": [],
    "seed-icl-1.0": [],
    "seed-icl-1.0-concurr": [],
}


def _volcengine_voice_cache_payload(voices_by_resource: dict[str, list[dict[str, Any]]], source: str, error: str = "") -> dict[str, Any]:
    return {
        "status": "ok",
        "source": source,
        "updated_at": _now_iso(),
        "doc_url": f"https://www.volcengine.com/docs/{VOLCENGINE_VOICE_DOC_LIBRARY_ID}/{VOLCENGINE_VOICE_DOC_DOCUMENT_ID}?lang=zh",
        "voices": voices_by_resource,
        "error": error,
    }


def _resource_for_volcengine_voice(voice_type: str, section_title: str) -> Optional[str]:
    voice_type = str(voice_type or "").strip()
    section_title = str(section_title or "")
    if not voice_type:
        return None
    if "模型2.0" in section_title and voice_type.endswith("_uranus_bigtts"):
        return "seed-tts-2.0"
    if "模型1.0" in section_title:
        if voice_type.endswith("_moon_bigtts") or voice_type.endswith("_mars_bigtts") or "_emo_" in voice_type:
            return "seed-tts-1.0"
    return None


def _parse_volcengine_voice_doc(content: str) -> dict[str, list[dict[str, Any]]]:
    voices: dict[str, list[dict[str, Any]]] = {key: [] for key in VOLCENGINE_FALLBACK_VOICES}
    seen: dict[str, set[str]] = {key: set() for key in voices}
    model_section = ""
    voice_type_pattern = re.compile(r"^[A-Za-z0-9_]+(?:_bigtts|_tob)$")

    for raw_line in str(content or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("##"):
            plain_heading = re.sub(r"<[^>]+>", "", line)
            if "豆包语音合成模型2.0" in plain_heading:
                model_section = "豆包语音合成模型2.0"
            elif "豆包语音合成模型1.0" in plain_heading:
                model_section = "豆包语音合成模型1.0"
            continue
        if not line.startswith("|"):
            continue

        cells = [re.sub(r"<[^>]+>", "", cell).strip().strip("\\") for cell in line.strip("|").split("|")]
        cells = [cell for cell in cells if cell and cell != "^^"]
        if len(cells) < 3:
            continue
        if any(cell in {"---", "**voice_type**"} or "voice_type" in cell for cell in cells):
            continue

        voice_index = next((idx for idx, cell in enumerate(cells) if voice_type_pattern.match(cell)), -1)
        if voice_index < 0:
            continue
        name_index = voice_index - 1
        scene_index = voice_index - 2
        if name_index < 0:
            continue

        value = cells[voice_index].strip()
        name = cells[name_index].strip()
        scene = cells[scene_index].strip() if scene_index >= 0 else ""
        resource_id = _resource_for_volcengine_voice(value, model_section)
        if not resource_id:
            continue
        if value in seen[resource_id]:
            continue
        seen[resource_id].add(value)
        label = f"{name}（{scene}）" if scene else name
        voices[resource_id].append({
            "value": value,
            "label": label,
            "name": name,
            "scene": scene,
        })

    for resource_id in ("seed-tts-1.0-concurr",):
        if not voices.get(resource_id):
            voices[resource_id] = [dict(item) for item in voices.get("seed-tts-1.0", [])]
    return voices


def _fetch_volcengine_voice_doc() -> str:
    query = urlencode({
        "LibraryID": VOLCENGINE_VOICE_DOC_LIBRARY_ID,
        "DocumentID": VOLCENGINE_VOICE_DOC_DOCUMENT_ID,
        "type": "",
        "lang": "zh",
    })
    url = f"https://www.volcengine.com/api/doc/getDocDetail?{query}"
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "VocStudio-Audiobook",
            "X-Language": "zh",
            "X-Use-Bff-Version": "1",
        },
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        payload = json.loads(response.read().decode("utf-8"))
    content = ((payload.get("Result") or {}).get("Content") or "")
    if not content:
        raise ValueError("Volcengine voice document content is empty")
    return content


def _read_volcengine_voice_cache() -> Optional[dict[str, Any]]:
    if not os.path.exists(VOLCENGINE_VOICE_CACHE_PATH):
        return None
    try:
        with open(VOLCENGINE_VOICE_CACHE_PATH, "r", encoding="utf-8") as f:
            payload = json.load(f)
        if isinstance(payload, dict) and isinstance(payload.get("voices"), dict):
            return payload
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    return None


def _write_volcengine_voice_cache(payload: dict[str, Any]) -> None:
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(VOLCENGINE_VOICE_CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)


def _load_volcengine_voices(force_refresh: bool = False) -> dict[str, Any]:
    cached = _read_volcengine_voice_cache()
    if cached and not force_refresh:
        try:
            age = time.time() - os.path.getmtime(VOLCENGINE_VOICE_CACHE_PATH)
            if age < VOLCENGINE_VOICE_CACHE_TTL_SECONDS:
                cached["cache_hit"] = True
                return cached
        except OSError:
            pass

    try:
        content = _fetch_volcengine_voice_doc()
        voices = _parse_volcengine_voice_doc(content)
        if not any(voices.get(resource_id) for resource_id in ("seed-tts-2.0", "seed-tts-1.0")):
            raise ValueError("No Volcengine voices parsed from document")
        payload = _volcengine_voice_cache_payload(voices, "volcengine_doc")
        payload["cache_hit"] = False
        _write_volcengine_voice_cache(payload)
        return payload
    except Exception as exc:
        logger.warning("Failed to refresh Volcengine voice list: %s", exc)
        if cached:
            cached["cache_hit"] = True
            cached["source"] = cached.get("source") or "cache"
            cached["error"] = str(exc)
            return cached
        payload = _volcengine_voice_cache_payload(
            {key: [dict(item) for item in value] for key, value in VOLCENGINE_FALLBACK_VOICES.items()},
            "fallback",
            str(exc),
        )
        payload["cache_hit"] = False
        return payload

# Endpoints

@app.get("/")
async def read_index():
    return FileResponse(
        os.path.join(STATIC_DIR, "index.html"),
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"}
    )

@app.get("/favicon.ico")
async def read_favicon():
    favicon_path = os.path.join(ROOT_DIR, "icon.png")
    if os.path.exists(favicon_path):
        return FileResponse(favicon_path, media_type="image/png")
    raise HTTPException(status_code=404, detail="Favicon not found")

@app.get("/api/config")
async def get_config():
    default_config = {
        "llm": {
            "base_url": "",
            "api_key": "",
            "model_name": "claude-opus-4-7",
            "provider": "anthropic",
            "openai_api_type": "responses"
        },
        "tts": {
            "mode": "local",
            "url": "http://127.0.0.1:7860",
            "device": "auto",
            "language": "Chinese",
            "dashscope_api_key": "",
            "volcengine_api_key": "",
            "volcengine_resource_id": "seed-tts-2.0",
            "volcengine_sample_rate": 24000,
            "volcengine_uid": "voc-studio"
        },
        "prompts": {
            "system_prompt": "",
            "user_prompt": ""
        },
        "generation": {
            "model_name": "claude-opus-4-7",
            "chunk_size": 3000,
            "max_tokens": 4096,
            "temperature": 0.6,
            "top_p": 0.8,
            "top_k": 0,
            "min_p": 0.0,
            "presence_penalty": 0.0,
            "banned_tokens": [],
            "merge_narrators": False,
            "enable_chapter_memory": True,
            "review_batch_size": 25
        }
    }

    if not os.path.exists(CONFIG_PATH):
        sys_prompt, usr_prompt = load_default_prompts()
        default_config["prompts"]["system_prompt"] = sys_prompt
        default_config["prompts"]["user_prompt"] = usr_prompt
        try:
            rev_sys, rev_usr = load_review_prompts()
            default_config["prompts"]["review_system_prompt"] = rev_sys
            default_config["prompts"]["review_user_prompt"] = rev_usr
        except RuntimeError:
            pass
        config = default_config
    else:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            config = json.load(f)

    if not isinstance(config.get("llm"), dict):
        config["llm"] = dict(default_config["llm"])
    else:
        for key, value in default_config["llm"].items():
            config["llm"].setdefault(key, value)
    if not isinstance(config.get("tts"), dict):
        config["tts"] = dict(default_config["tts"])
    else:
        for key, value in default_config["tts"].items():
            config["tts"].setdefault(key, value)
    config["generation"] = _normalize_generation_config(config.get("generation"), config.get("llm"))
    for key, value in default_config["generation"].items():
        config["generation"].setdefault(key, value)

    # Ensure generation prompts use the lightweight tagged-text pipeline. Existing
    # config files may still contain the previous JSON-array generation prompt.
    config["prompts"] = _ensure_tagged_generation_prompts(config.get("prompts"))
    config["prompts"] = _ensure_review_prompts(config["prompts"])

    # Include current input file info if available
    current_book = _current_book()
    if current_book:
        state_path = os.path.join(_book_dir(current_book["id"]), "state.json")
        if os.path.exists(state_path):
            try:
                with open(state_path, "r", encoding="utf-8") as sf:
                    state = json.load(sf)
                input_path = state.get("input_file_path", "")
                if input_path and os.path.exists(input_path):
                    config["current_file"] = os.path.basename(input_path)
            except (json.JSONDecodeError, ValueError):
                pass

    return config

@app.get("/api/default_prompts")
async def get_default_prompts():
    system_prompt, user_prompt = load_default_prompts()
    result = {
        "system_prompt": system_prompt,
        "user_prompt": user_prompt
    }
    try:
        review_sys, review_usr = load_review_prompts()
        result["review_system_prompt"] = review_sys
        result["review_user_prompt"] = review_usr
    except RuntimeError:
        pass
    return result

@app.get("/api/volcengine/voices")
async def get_volcengine_voices(refresh: bool = False):
    return _load_volcengine_voices(force_refresh=refresh)

@app.post("/api/config")
async def save_config(config: AppConfig):
    payload = config.model_dump()
    generation = payload.get("generation")
    if not isinstance(generation, dict):
        generation = {}
        payload["generation"] = generation
    payload["prompts"] = _ensure_tagged_generation_prompts(payload.get("prompts"))
    payload["prompts"] = _ensure_review_prompts(payload["prompts"])
    llm = payload.get("llm")
    if not isinstance(llm, dict):
        llm = {}
        payload["llm"] = llm
    llm.setdefault("provider", "anthropic")
    llm.setdefault("openai_api_type", "responses")
    payload["generation"] = _normalize_generation_config(generation, llm)
    generation = payload["generation"]
    generation.setdefault("enable_chapter_memory", True)
    generation.setdefault("review_batch_size", 25)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    # Reset engine so it picks up new TTS settings on next use
    project_manager.reset_engine()
    return {"status": "saved"}

class _HTMLTextExtractor(HTMLParser):
    """Strip HTML tags from EPUB content, preserving block-level structure."""
    BLOCK_TAGS = frozenset({
        'p', 'div', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
        'li', 'blockquote', 'br', 'hr', 'tr', 'section', 'article',
    })
    SKIP_TAGS = frozenset({'style', 'script'})

    def __init__(self):
        super().__init__()
        self.parts = []
        self._pending_newline = False
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in self.SKIP_TAGS:
            self._skip_depth += 1
        elif tag in self.BLOCK_TAGS:
            self._pending_newline = True

    def handle_endtag(self, tag):
        if tag.lower() in self.SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data):
        if self._skip_depth > 0:
            return
        if self._pending_newline and self.parts:
            self.parts.append('\n')
            self._pending_newline = False
        self.parts.append(data)

    def get_text(self):
        return ''.join(self.parts)


def extract_epub_text(epub_path: str) -> str:
    """Extract plain text from an EPUB file, ordered by spine (reading order).

    Parses the EPUB ZIP structure directly using stdlib only:
    META-INF/container.xml -> .opf manifest+spine -> XHTML content files.
    """
    with zipfile.ZipFile(epub_path, 'r') as zf:
        # 1. Find the OPF file path from container.xml
        container_xml = zf.read('META-INF/container.xml')
        container = ET.fromstring(container_xml)
        ns = {'c': 'urn:oasis:names:tc:opendocument:xmlns:container'}
        rootfile_el = container.find('.//c:rootfile', ns)
        if rootfile_el is None:
            raise ValueError("Invalid EPUB: no rootfile found in container.xml")
        opf_path = rootfile_el.get('full-path')

        # 2. Parse the OPF to get manifest (id->href) and spine (reading order)
        opf_xml = zf.read(opf_path)
        opf = ET.fromstring(opf_xml)
        # Detect OPF namespace (varies between EPUB 2 and 3)
        opf_ns = opf.tag.split('}')[0] + '}' if '}' in opf.tag else ''

        # Build manifest: id -> href (resolve relative to OPF directory)
        opf_dir = opf_path.rsplit('/', 1)[0] + '/' if '/' in opf_path else ''
        manifest = {}
        for item in opf.findall(f'.//{opf_ns}item'):
            item_id = item.get('id')
            href = item.get('href')
            media_type = item.get('media-type', '')
            if item_id and href and 'html' in media_type:
                manifest[item_id] = opf_dir + href

        # Get spine order
        spine_ids = []
        for itemref in opf.findall(f'.//{opf_ns}itemref'):
            idref = itemref.get('idref')
            if idref:
                spine_ids.append(idref)

        # 3. Extract text from each spine item in order
        chapters = []
        for item_id in spine_ids:
            href = manifest.get(item_id)
            if href is None:
                continue
            try:
                html_bytes = zf.read(href)
            except KeyError:
                continue
            html_content = html_bytes.decode('utf-8', errors='replace')
            extractor = _HTMLTextExtractor()
            extractor.feed(html_content)
            text = extractor.get_text().strip()
            if text:
                chapters.append(text)

    return '\n\n'.join(chapters)


async def _read_uploaded_source_text(file: UploadFile) -> tuple[str, str]:
    safe_filename = os.path.basename(file.filename or "source.txt")
    content = await file.read()
    if safe_filename.lower().endswith(".epub"):
        tmp_path = ""
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".epub") as tmp:
                tmp.write(content)
                tmp_path = tmp.name
            text = extract_epub_text(tmp_path)
        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.remove(tmp_path)
        return text, safe_filename
    try:
        return content.decode("utf-8"), safe_filename
    except UnicodeDecodeError:
        return content.decode("utf-8", errors="replace"), safe_filename


def _chapter_title_key(value: Any) -> str:
    text = str(value or "").strip().casefold()
    fullwidth_digits = str.maketrans("０１２３４５６７８９", "0123456789")
    text = text.translate(fullwidth_digits)
    return re.sub(r"[\s:：、.．。\-—－_/／【】\[\]（）()]+", "", text)


def _chinese_number_to_int(value: str) -> Optional[int]:
    raw = str(value or "").strip()
    if not raw:
        return None
    raw = raw.translate(str.maketrans("０１２３４５６７８９", "0123456789"))
    if raw.isdigit():
        return int(raw)
    numerals = {
        "零": 0, "〇": 0,
        "一": 1, "壹": 1,
        "二": 2, "两": 2, "贰": 2,
        "三": 3, "叁": 3,
        "四": 4, "肆": 4,
        "五": 5, "伍": 5,
        "六": 6, "陆": 6,
        "七": 7, "柒": 7,
        "八": 8, "捌": 8,
        "九": 9, "玖": 9,
    }
    units = {"十": 10, "拾": 10, "百": 100, "佰": 100, "千": 1000, "仟": 1000, "万": 10000}
    total = 0
    section = 0
    number = 0
    saw = False
    for char in raw:
        if char.isdigit():
            number = number * 10 + int(char)
            saw = True
            continue
        if char in numerals:
            number = numerals[char]
            saw = True
            continue
        unit = units.get(char)
        if not unit:
            return None
        saw = True
        if unit == 10000:
            section = (section + (number or 0)) * unit
            total += section
            section = 0
        else:
            section += (number or 1) * unit
        number = 0
    if not saw:
        return None
    return total + section + number


def _chapter_ordinal(value: Any) -> Optional[int]:
    title = str(value or "").strip()
    if not title:
        return None
    patterns = [
        r"第\s*([零〇一二两三四五六七八九十百千万壹贰叁肆伍陆柒捌玖拾佰仟\d０-９]+)\s*[章节回]",
        r"^\s*([零〇一二两三四五六七八九十百千万壹贰叁肆伍陆柒捌玖拾佰仟\d０-９]+)\s*[、.．:：\-—－]",
        r"^\s*(?:chapter|chap\.?)\s*(\d+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, title, re.IGNORECASE)
        if match:
            return _chinese_number_to_int(match.group(1))
    return None


def _append_chapter_plan(current_records: list[dict], incoming_chapters: list[dict]) -> dict[str, Any]:
    if not current_records:
        raise HTTPException(status_code=400, detail="No current chapters found. Upload the source file first.")
    if not incoming_chapters:
        raise HTTPException(status_code=400, detail="No chapters found in the uploaded source.")

    current_titles = [str(chapter.get("title") or chapter.get("chapter_title") or "") for chapter in current_records]
    incoming_titles = [str(chapter.get("title") or "") for chapter in incoming_chapters]
    current_keys = [_chapter_title_key(title) for title in current_titles]
    incoming_keys = [_chapter_title_key(title) for title in incoming_titles]
    current_key_set = {key for key in current_keys if key}

    matched_existing_count = 0
    append_mode = "full_source"
    max_prefix = min(len(current_keys), len(incoming_keys))
    while matched_existing_count < max_prefix and current_keys[matched_existing_count] == incoming_keys[matched_existing_count]:
        matched_existing_count += 1

    if matched_existing_count < len(current_keys):
        duplicate_titles = sorted({
            incoming_titles[idx]
            for idx, key in enumerate(incoming_keys)
            if key and key in current_key_set
        })
        first_ordinal = _chapter_ordinal(incoming_titles[0])
        existing_ordinals = [
            ordinal
            for ordinal in (_chapter_ordinal(title) for title in current_titles)
            if ordinal is not None
        ]
        last_existing_ordinal = max(existing_ordinals) if existing_ordinals else None
        if duplicate_titles:
            raise HTTPException(
                status_code=400,
                detail=f"Uploaded source overlaps existing chapters but does not match from the beginning: {', '.join(duplicate_titles[:5])}",
            )
        if first_ordinal is None or last_existing_ordinal is None or first_ordinal <= last_existing_ordinal:
            expected = current_titles[matched_existing_count] if matched_existing_count < len(current_titles) else current_titles[-1]
            got = incoming_titles[matched_existing_count] if matched_existing_count < len(incoming_titles) else incoming_titles[0]
            raise HTTPException(
                status_code=400,
                detail=f"Uploaded source does not look like a full updated source or a tail-only continuation. Expected around '{expected}', got '{got}'.",
            )
        append_mode = "tail_only"
        append_start = 0
    else:
        append_start = matched_existing_count

    appended = incoming_chapters[append_start:]
    return {
        "append_mode": append_mode,
        "matched_existing_count": matched_existing_count,
        "append_start_index": append_start,
        "current_chapter_count": len(current_records),
        "incoming_chapter_count": len(incoming_chapters),
        "append_count": len(appended),
        "appended_chapters": appended,
    }


def _append_chapters_to_current_book(appended_chapters: list[dict], source_filename: str) -> tuple[dict, str]:
    manifest = load_current_chapters_manifest()
    records = _load_chapter_records(manifest)
    existing_ids = {str(record.get("chapter_id") or "") for record in records}
    base_count = len(records)
    for offset, chapter in enumerate(appended_chapters, start=1):
        index = base_count + offset
        chapter_id = f"chapter_{index:04d}"
        suffix = 2
        while chapter_id in existing_ids:
            chapter_id = f"chapter_{index:04d}_{suffix}"
            suffix += 1
        existing_ids.add(chapter_id)
        title = str(chapter.get("title") or chapter_id).strip() or chapter_id
        content = str(chapter.get("content") or "").strip()
        records.append({
            "chapter_id": chapter_id,
            "title": title,
            "chapter_title": title,
            "filename": safe_chapter_filename(index),
            "path": f"chapters/{safe_chapter_filename(index)}",
            "char_count": len(content),
            "start_line": chapter.get("start_line"),
            "content": content,
        })

    updated_manifest = _write_chapter_records(records, generated_at=manifest.get("generated_at"))
    safe_name = os.path.splitext(os.path.basename(source_filename or "source"))[0]
    rebuilt_source_filename = _write_source_from_chapter_records(records, safe_name, updated_manifest)
    return updated_manifest, rebuilt_source_filename


def save_chapters_for_current_book(text: str) -> dict:
    chapters_dir = _current_chapters_dir()
    if os.path.exists(chapters_dir):
        shutil.rmtree(chapters_dir)
    os.makedirs(chapters_dir, exist_ok=True)

    chapters = split_text_into_chapters(text, default_title="全文")
    chapter_items = []
    for chapter in chapters:
        chapter_path = os.path.join(chapters_dir, chapter["filename"])
        with open(chapter_path, "w", encoding="utf-8") as f:
            f.write(chapter["content"])
        item = {key: value for key, value in chapter.items() if key != "content"}
        item["path"] = f"chapters/{chapter['filename']}"
        chapter_items.append(item)

    manifest = {
        "generated_at": _now_iso(),
        "chapter_count": len(chapter_items),
        "total_chars": sum(item["char_count"] for item in chapter_items),
        "chapters": chapter_items,
    }
    with open(_current_chapters_manifest_path(), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
    return manifest


def preview_chapters_from_text(text: str, *, sample_limit: int = 8) -> dict:
    chapters = split_text_into_chapters(text, default_title="全文")
    chapter_items = []
    for chapter in chapters:
        item = {key: value for key, value in chapter.items() if key != "content"}
        item["path"] = f"chapters/{chapter['filename']}"
        chapter_items.append(item)

    return {
        "chapter_count": len(chapter_items),
        "total_chars": sum(item["char_count"] for item in chapter_items),
        "sample_chapters": chapter_items[:sample_limit],
    }


def load_current_chapters_manifest() -> dict:
    path = _current_chapters_manifest_path()
    if not os.path.exists(path):
        return {"chapter_count": 0, "total_chars": 0, "chapters": []}
    try:
        with open(path, "r", encoding="utf-8") as f:
            manifest = json.load(f)
        manifest.setdefault("chapter_count", len(manifest.get("chapters") or []))
        manifest.setdefault("total_chars", sum((c.get("char_count") or 0) for c in manifest.get("chapters") or []))
        manifest.setdefault("chapters", [])
        return manifest
    except (json.JSONDecodeError, ValueError):
        return {"chapter_count": 0, "total_chars": 0, "chapters": []}

def save_current_chapters_manifest(manifest: dict) -> dict:
    chapters = manifest.get("chapters") or []
    manifest["chapter_count"] = len(chapters)
    manifest["total_chars"] = sum((chapter.get("char_count") or 0) for chapter in chapters)
    manifest["updated_at"] = _now_iso()
    os.makedirs(_current_chapters_dir(), exist_ok=True)
    with open(_current_chapters_manifest_path(), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
    return manifest

def _find_chapter(manifest: dict, chapter_id: str) -> dict:
    for chapter in manifest.get("chapters") or []:
        if str(chapter.get("chapter_id") or "") == chapter_id:
            return chapter
    raise HTTPException(status_code=404, detail=f"Chapter '{chapter_id}' not found")

def _chapter_file_path(chapter: dict) -> str:
    rel_path = chapter.get("path") or f"chapters/{chapter.get('filename') or ''}"
    if not rel_path:
        raise HTTPException(status_code=400, detail="Chapter has no file path")
    path = os.path.normpath(os.path.join(_current_book_dir(), rel_path))
    chapters_root = os.path.abspath(_current_chapters_dir())
    if not os.path.abspath(path).startswith(chapters_root + os.sep):
        raise HTTPException(status_code=400, detail="Invalid chapter path")
    return path

def _chapter_has_script(chapter_id: str) -> bool:
    return any(
        str(entry.get("chapter_id") or "") == chapter_id
        for entry in _load_script_entries(_current_script_path())
    )

def _chapter_has_chunks(chapter_id: str) -> bool:
    return any(
        isinstance(chunk, dict) and str(chunk.get("chapter_id") or "") == chapter_id
        for chunk in project_manager.load_chunks()
    )


def _safe_chapter_slug(value: str) -> str:
    slug = re.sub(r"[^\w\-]+", "_", str(value or "").strip()).strip("_").lower()
    return slug or "chapter"


def _read_json_list(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
    except (json.JSONDecodeError, ValueError):
        pass
    return []


def _write_json(path: str, payload: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    os.replace(tmp_path, path)


def _load_chapter_records(manifest: dict) -> list[dict]:
    records: list[dict] = []
    for chapter in manifest.get("chapters") or []:
        if not isinstance(chapter, dict):
            continue
        chapter_copy = dict(chapter)
        path = _chapter_file_path(chapter_copy)
        if os.path.exists(path):
            chapter_copy["content"] = _read_text_file(path)
        else:
            chapter_copy["content"] = ""
        records.append(chapter_copy)
    return records


def _normalize_chapter_records(records: list[dict]) -> list[dict]:
    seen_ids: set[str] = set()
    seen_filenames: set[str] = set()
    normalized: list[dict] = []
    for index, chapter in enumerate(records, start=1):
        item = dict(chapter)
        chapter_id = str(item.get("chapter_id") or "").strip()
        if not chapter_id:
            chapter_id = f"chapter_{index:04d}"
        base_id = chapter_id
        suffix = 2
        while chapter_id in seen_ids:
            chapter_id = f"{base_id}_{suffix}"
            suffix += 1
        seen_ids.add(chapter_id)

        title = str(item.get("title") or item.get("chapter_title") or chapter_id).strip() or chapter_id
        content = str(item.get("content") or "")
        filename = str(item.get("filename") or "").strip()
        path = str(item.get("path") or "").strip()
        if not filename and path:
            filename = os.path.basename(path)
        filename = os.path.basename(filename)
        if not filename:
            filename = _safe_chapter_slug(chapter_id) + ".txt"
        if not os.path.splitext(filename)[1]:
            filename = f"{filename}.txt"
        base_filename = filename
        stem, ext = os.path.splitext(base_filename)
        suffix = 2
        while filename in seen_filenames:
            filename = f"{stem}_{suffix}{ext or '.txt'}"
            suffix += 1
        seen_filenames.add(filename)
        path = f"chapters/{filename}"

        item["chapter_id"] = chapter_id
        item["index"] = index
        item["title"] = title
        item["chapter_title"] = title
        item["filename"] = filename
        item["path"] = path
        item["char_count"] = len(content)
        item["content"] = content
        normalized.append(item)
    return normalized


def _write_chapter_records(records: list[dict], *, generated_at: Optional[str] = None) -> dict:
    chapters_dir = _current_chapters_dir()
    os.makedirs(chapters_dir, exist_ok=True)
    normalized = _normalize_chapter_records(records)
    chapter_items: list[dict] = []
    for chapter in normalized:
        path = _chapter_file_path(chapter)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(str(chapter.get("content") or ""))
        item = {key: value for key, value in chapter.items() if key != "content"}
        chapter_items.append(item)

    manifest = {
        "generated_at": generated_at or _now_iso(),
        "updated_at": _now_iso(),
        "chapter_count": len(chapter_items),
        "total_chars": sum(item.get("char_count") or 0 for item in chapter_items),
        "chapters": chapter_items,
    }
    _write_json(_current_chapters_manifest_path(), manifest)
    return manifest


def _chapter_snapshot_from_manifest(manifest: dict) -> dict:
    records = _load_chapter_records(manifest)
    snapshot_chapters: list[dict[str, Any]] = []
    for record in records:
        chapter_id = str(record.get("chapter_id") or "").strip()
        if not chapter_id:
            continue
        content = str(record.get("content") or "")
        title = str(record.get("title") or record.get("chapter_title") or chapter_id).strip() or chapter_id
        snapshot_chapters.append({
            "chapter_id": chapter_id,
            "index": record.get("index"),
            "title": title,
            "chapter_title": title,
            "filename": record.get("filename") or "",
            "path": record.get("path") or "",
            "char_count": len(content),
            "start_line": record.get("start_line"),
            "content": content,
        })

    manifest_copy = dict(manifest)
    manifest_copy["chapters"] = [
        {key: value for key, value in chapter.items() if key != "content"}
        for chapter in snapshot_chapters
    ]
    manifest_copy["chapter_count"] = len(snapshot_chapters)
    manifest_copy["total_chars"] = sum(chapter.get("char_count") or 0 for chapter in snapshot_chapters)
    return {
        "version": 1,
        "saved_at": _now_iso(),
        "manifest": manifest_copy,
        "chapters": snapshot_chapters,
    }


def _chapter_records_from_snapshot(snapshot: Any) -> tuple[list[dict], str]:
    if not isinstance(snapshot, dict):
        return [], ""
    manifest = snapshot.get("manifest") if isinstance(snapshot.get("manifest"), dict) else {}
    manifest_by_id = {
        str(chapter.get("chapter_id") or ""): dict(chapter)
        for chapter in (manifest.get("chapters") or [])
        if isinstance(chapter, dict) and str(chapter.get("chapter_id") or "")
    }
    records: list[dict] = []
    for index, raw_chapter in enumerate(snapshot.get("chapters") or [], start=1):
        if not isinstance(raw_chapter, dict):
            continue
        chapter_id = str(raw_chapter.get("chapter_id") or "").strip()
        item = dict(manifest_by_id.get(chapter_id, {}))
        item.update(raw_chapter)
        if not chapter_id:
            chapter_id = f"chapter_{index:04d}"
        title = str(item.get("title") or item.get("chapter_title") or chapter_id).strip() or chapter_id
        filename = os.path.basename(str(item.get("filename") or item.get("path") or "").strip())
        if not filename:
            filename = _safe_chapter_filename(index)
        item["chapter_id"] = chapter_id
        item["index"] = index
        item["title"] = title
        item["chapter_title"] = title
        item["filename"] = filename
        item["path"] = f"chapters/{filename}"
        item["content"] = str(item.get("content") or "")
        item["char_count"] = len(item["content"])
        records.append(item)
    generated_at = str(manifest.get("generated_at") or snapshot.get("saved_at") or "")
    return records, generated_at


def _write_source_from_chapter_records(records: list[dict], safe_name: str, manifest: dict) -> str:
    source_dir = os.path.join(_current_book_dir(), "source")
    os.makedirs(source_dir, exist_ok=True)
    source_filename = f"{safe_name or 'saved_script'}_chapters.txt"
    source_path = os.path.join(source_dir, source_filename)
    content = "\n\n".join(str(record.get("content") or "").strip() for record in records if str(record.get("content") or "").strip())
    with open(source_path, "w", encoding="utf-8") as f:
        f.write(content)

    state = {}
    state_path = _current_state_path()
    if os.path.exists(state_path):
        try:
            with open(state_path, "r", encoding="utf-8") as f:
                state = json.load(f)
        except (json.JSONDecodeError, ValueError):
            state = {}
    state["input_file_path"] = source_path
    state["chapters_manifest_path"] = _current_chapters_manifest_path()
    state["chapter_count"] = manifest.get("chapter_count", 0)
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
    return source_filename


def _assign_entries_to_current_chapters(entries: list[dict]) -> tuple[list[dict], int]:
    manifest = load_current_chapters_manifest()
    records = _load_chapter_records(manifest)
    if not records or not entries:
        return entries, 0
    if any(str(entry.get("chapter_id") or "").strip() for entry in entries if isinstance(entry, dict)):
        return entries, 0

    chapter_texts = [str(record.get("content") or "") for record in records]
    chapter_positions = [0 for _ in records]
    total_chars = sum(max(1, len(text)) for text in chapter_texts) or len(entries)
    assigned: list[dict] = []
    current_index = 0
    updated = 0

    def fallback_index(entry_index: int) -> int:
        if len(entries) <= 1:
            return 0
        target = (entry_index / max(1, len(entries) - 1)) * total_chars
        cumulative = 0
        for idx, text in enumerate(chapter_texts):
            cumulative += max(1, len(text))
            if target <= cumulative:
                return idx
        return len(records) - 1

    for entry_index, entry in enumerate(entries):
        item = dict(entry)
        text = str(item.get("text") or "").strip()
        matched_index: Optional[int] = None
        if text:
            snippet = re.sub(r"\s+", "", text)
            if len(snippet) > 80:
                snippet = snippet[:80]
            if snippet:
                for idx in range(current_index, len(records)):
                    chapter_compact = re.sub(r"\s+", "", chapter_texts[idx])
                    start = chapter_positions[idx]
                    pos = chapter_compact.find(snippet, start)
                    if pos < 0 and idx == current_index:
                        pos = chapter_compact.find(snippet)
                    if pos >= 0:
                        matched_index = idx
                        chapter_positions[idx] = pos + len(snippet)
                        current_index = idx
                        break
        if matched_index is None:
            matched_index = max(current_index, fallback_index(entry_index))
            matched_index = min(matched_index, len(records) - 1)
            current_index = matched_index

        chapter = records[matched_index]
        item["chapter_id"] = str(chapter.get("chapter_id") or "")
        item["chapter_index"] = chapter.get("index") or matched_index + 1
        item["chapter_title"] = chapter.get("title") or item["chapter_id"]
        assigned.append(item)
        updated += 1
    return assigned, updated


def _chapter_generated_artifacts(chapter_ids: list[str]) -> dict[str, Any]:
    chapter_set = {str(chapter_id or "") for chapter_id in chapter_ids if str(chapter_id or "")}
    script_entries = _read_json_list(_current_script_path())
    partial_entries = _read_json_list(os.path.join(_current_book_dir(), "annotated_script.partial.json"))
    chunks = _read_json_list(_current_chunks_path())

    def _count(items: list[dict]) -> int:
        return sum(1 for item in items if str(item.get("chapter_id") or "") in chapter_set)

    return {
        "script_entries": _count(script_entries),
        "partial_entries": _count(partial_entries),
        "chunks": _count(chunks),
        "has_script": any(str(entry.get("chapter_id") or "") in chapter_set for entry in script_entries),
        "has_chunks": any(str(chunk.get("chapter_id") or "") in chapter_set for chunk in chunks),
    }


def _invalidate_generated_outputs(chapter_ids: list[str]) -> dict[str, Any]:
    chapter_set = {str(chapter_id or "") for chapter_id in chapter_ids if str(chapter_id or "")}
    if not chapter_set:
        return {
            "removed_script_entries": 0,
            "removed_partial_entries": 0,
            "removed_chunks": 0,
            "removed_audio_files": [],
            "removed_character_analysis_chapters": 0,
        }

    removed_script_entries = 0
    removed_partial_entries = 0
    removed_chunks = 0
    removed_audio_files: list[str] = []

    script_path = _current_script_path()
    script_entries = _read_json_list(script_path)
    if script_entries:
        filtered = [entry for entry in script_entries if str(entry.get("chapter_id") or "") not in chapter_set]
        removed_script_entries = len(script_entries) - len(filtered)
        if filtered != script_entries:
            _write_json(script_path, filtered)

    partial_path = os.path.join(_current_book_dir(), "annotated_script.partial.json")
    partial_entries = _read_json_list(partial_path)
    if partial_entries:
        filtered = [entry for entry in partial_entries if str(entry.get("chapter_id") or "") not in chapter_set]
        removed_partial_entries = len(partial_entries) - len(filtered)
        if filtered != partial_entries:
            _write_json(partial_path, filtered)

    chunks_path = _current_chunks_path()
    chunks = _read_json_list(chunks_path)
    if chunks:
        removed_chunk_audio_paths = [
            chunk.get("audio_path")
            for chunk in chunks
            if str(chunk.get("chapter_id") or "") in chapter_set
        ]
        filtered_chunks = [chunk for chunk in chunks if str(chunk.get("chapter_id") or "") not in chapter_set]
        removed_chunks = len(chunks) - len(filtered_chunks)
        if filtered_chunks != chunks:
            for index, chunk in enumerate(filtered_chunks):
                chunk["id"] = index
                chunk.setdefault("status", "pending")
                chunk.setdefault("audio_path", None)
            _write_json(chunks_path, filtered_chunks)
            removed_audio_files.extend(_remove_unreferenced_audio_files(_current_book_dir(), removed_chunk_audio_paths, filtered_chunks))

    memory = _load_chapter_memory()
    memory_chapters = memory.get("chapters") if isinstance(memory.get("chapters"), dict) else {}
    for chapter_id in chapter_set:
        memory_chapters.pop(chapter_id, None)
    memory["chapters"] = memory_chapters
    _save_chapter_memory(memory)

    issues = _load_script_issues()
    issue_chapters = issues.get("chapters") if isinstance(issues.get("chapters"), dict) else {}
    for chapter_id in chapter_set:
        issue_chapters.pop(chapter_id, None)
    issues["chapters"] = issue_chapters
    _save_script_issues(issues)
    _mark_memory_stale_after(list(chapter_set), "章节正文或结构已修改，后续章节记忆可能需要刷新。")
    _update_script_generation_state_for_changes(
        list(chapter_set),
        status="missing",
        source="invalidated",
    )
    character_analysis_invalidated = _invalidate_character_analysis_state(list(chapter_set))

    chapter_audio_dir = os.path.join(_current_book_dir(), "chapter_audio")
    for chapter_id in chapter_set:
        safe_id = _safe_chapter_slug(chapter_id)
        chapter_audio_path = os.path.join(chapter_audio_dir, f"{safe_id}.mp3")
        if os.path.exists(chapter_audio_path):
            os.remove(chapter_audio_path)
            removed_audio_files.append(chapter_audio_path)

    for path in (_current_audiobook_path(), _current_m4b_path()):
        if os.path.exists(path):
            os.remove(path)
            removed_audio_files.append(path)

    return {
        "removed_script_entries": removed_script_entries,
        "removed_partial_entries": removed_partial_entries,
        "removed_chunks": removed_chunks,
        "removed_audio_files": removed_audio_files,
        "removed_character_analysis_chapters": character_analysis_invalidated.get("removed_character_analysis_chapters", 0),
    }


def _clear_generation_state_for_resplit(old_chapter_ids: list[str]) -> dict[str, Any]:
    invalidated = _invalidate_generated_outputs(old_chapter_ids)

    script_path = _current_script_path()
    remaining_script_entries = _read_json_list(script_path)
    if remaining_script_entries:
        invalidated["removed_script_entries"] += len(remaining_script_entries)
        os.remove(script_path)

    partial_path = os.path.join(_current_book_dir(), "annotated_script.partial.json")
    remaining_partial_entries = _read_json_list(partial_path)
    if remaining_partial_entries:
        invalidated["removed_partial_entries"] += len(remaining_partial_entries)
        os.remove(partial_path)

    chunks_path = _current_chunks_path()
    remaining_chunks = _read_json_list(chunks_path)
    if remaining_chunks:
        invalidated["removed_chunks"] += len(remaining_chunks)
        candidate_audio_paths = [chunk.get("audio_path") for chunk in remaining_chunks if isinstance(chunk, dict)]
        invalidated["removed_audio_files"].extend(_remove_unreferenced_audio_files(_current_book_dir(), candidate_audio_paths, []))
        os.remove(chunks_path)

    chapter_audio_dir = os.path.join(_current_book_dir(), "chapter_audio")
    if os.path.isdir(chapter_audio_dir):
        for name in os.listdir(chapter_audio_dir):
            path = os.path.join(chapter_audio_dir, name)
            if os.path.isfile(path):
                try:
                    os.remove(path)
                    invalidated["removed_audio_files"].append(path)
                except OSError as exc:
                    logger.warning("Failed to remove chapter audio %s during resplit: %s", path, exc)

    for path in (_current_audiobook_path(), _current_m4b_path()):
        if os.path.exists(path):
            os.remove(path)
            invalidated["removed_audio_files"].append(path)

    _save_chapter_memory({"chapters": {}})
    _save_script_issues({"chapters": {}})
    _invalidate_character_analysis_state()
    _save_script_generation_state({
        "engine": "chapter_pipeline",
        "status": "missing",
        "source": "chapter_resplit",
        "updated_at": _now_iso(),
        "chapters": {},
    })
    return invalidated


def _script_entries_to_chunks(entries: list[dict]) -> list[dict]:
    chunks = group_into_chunks(entries)
    for idx, chunk in enumerate(chunks):
        chunk["id"] = idx
        chunk.setdefault("status", "pending")
        chunk["audio_path"] = None
    return chunks


def _chapter_import_meta(chapter: dict) -> dict:
    return {
        "chapter_id": str(chapter.get("chapter_id") or ""),
        "chapter_index": chapter.get("index"),
        "chapter_title": chapter.get("title") or chapter.get("chapter_title") or chapter.get("chapter_id") or "",
    }


def _parse_tagged_script_with_manifest(
    content: str,
    manifest: dict,
    default_instruct: str = "",
) -> tuple[list[dict], list[dict]]:
    chapters = manifest.get("chapters") or []
    if not chapters:
        return parse_tagged_script_text(content, default_instruct=default_instruct)

    chapter_by_id = {str(chapter.get("chapter_id") or ""): chapter for chapter in chapters}
    title_lookup = {
        str(chapter.get("title") or chapter.get("chapter_title") or "").strip().casefold(): chapter
        for chapter in chapters
        if str(chapter.get("title") or chapter.get("chapter_title") or "").strip()
    }
    current_chapter: Optional[dict] = None
    buffer: list[str] = []
    entries: list[dict] = []
    issues: list[dict] = []

    def flush_buffer() -> None:
        nonlocal buffer
        text = "\n".join(buffer).strip()
        if not text:
            buffer = []
            return
        chapter_meta = _chapter_import_meta(current_chapter) if current_chapter else None
        parsed_entries, parsed_issues = parse_tagged_script_text(
            text,
            default_instruct=default_instruct,
            chapter_meta=chapter_meta,
        )
        entries.extend(parsed_entries)
        issues.extend(parsed_issues)
        buffer = []

    normalized = str(content or "").replace("\r\n", "\n").replace("\r", "\n")
    for line in normalized.split("\n"):
        stripped = line.strip()
        heading_match = re.match(r"^#\s*(?:\[(?P<id>[^\]]+)\])?\s*(?P<title>.*)$", stripped)
        if heading_match:
            chapter_id = str(heading_match.group("id") or "").strip()
            title = str(heading_match.group("title") or "").strip()
            chapter = chapter_by_id.get(chapter_id) if chapter_id else None
            if chapter is None and title:
                chapter = title_lookup.get(title.casefold())
            if chapter is not None:
                flush_buffer()
                current_chapter = chapter
                continue
        buffer.append(line)
    flush_buffer()
    return entries, issues


def _validate_imported_script_by_chapter(
    entries: list[dict],
    parse_issues: list[dict],
    manifest: dict,
    target_chapter_ids: Optional[set[str]] = None,
) -> dict:
    book_dir = _current_book_dir()
    character_book = _load_character_book(book_dir)
    target_chapter_ids = {str(chapter_id or "") for chapter_id in (target_chapter_ids or set()) if str(chapter_id or "")}

    entries_by_chapter: dict[str, list[dict]] = {}
    for entry in entries:
        chapter_id = str(entry.get("chapter_id") or "").strip()
        if not chapter_id:
            chapter_id = "__unscoped__"
        if target_chapter_ids and chapter_id not in target_chapter_ids:
            continue
        entries_by_chapter.setdefault(chapter_id, []).append(entry)

    reports: dict[str, dict] = {}
    for chapter_id, chapter_entries in entries_by_chapter.items():
        if chapter_id == "__unscoped__":
            chapter_meta = {
                "chapter_id": chapter_id,
                "index": None,
                "title": "未归属章节",
            }
        else:
            try:
                chapter_meta = _find_chapter(manifest, chapter_id)
            except HTTPException:
                chapter_meta = {
                    "chapter_id": chapter_id,
                    "index": None,
                    "title": chapter_id,
                }
        report = _validate_script_entries(
            None if chapter_id == "__unscoped__" else chapter_meta,
            chapter_entries,
            character_book,
            [],
        )
        if chapter_id == "__unscoped__":
            report["chapter_id"] = chapter_id
            report["chapter_index"] = None
            report["chapter_title"] = "未归属章节"
            report["issues"] = [
                {
                    "severity": "warning",
                    "code": "unscoped_import",
                    "message": "导入内容未匹配到章节；建议使用导出的 # [chapter_id] 章节标题格式，或选择单章导入。",
                },
                *(report.get("issues") or []),
            ]
            report["issue_count"] = len(report["issues"])
            report["warning_count"] = report.get("warning_count", 0) + 1
        report["entry_count"] = len(chapter_entries)
        reports[chapter_id] = report

    if parse_issues:
        import_report = {
            "chapter_id": "__import__",
            "chapter_index": None,
            "chapter_title": "导入格式",
            "issue_count": len(parse_issues),
            "error_count": sum(1 for issue in parse_issues if issue.get("level") == "error"),
            "warning_count": sum(1 for issue in parse_issues if issue.get("level") == "warning"),
            "unknown_speaker_count": 0,
            "unknown_speakers": [],
            "entry_count": 0,
            "issues": [
                {
                    "severity": issue.get("level") or "warning",
                    "code": "tagged_parse",
                    "message": str(issue.get("message") or "Tagged script parse issue"),
                    "line": issue.get("line"),
                }
                for issue in parse_issues
                if isinstance(issue, dict)
            ],
            "updated_at": _now_iso(),
        }
        reports["__import__"] = import_report
    return reports


def _revalidate_current_script_issues(character_book: Optional[dict] = None) -> dict[str, Any]:
    manifest = load_current_chapters_manifest()
    script_entries = _load_script_entries(_current_script_path())
    existing_issues = _load_script_issues()
    existing_chapters = existing_issues.get("chapters") if isinstance(existing_issues.get("chapters"), dict) else {}
    character_book = character_book or _load_character_book(_current_book_dir())

    chapters_by_id = {
        str(chapter.get("chapter_id") or ""): chapter
        for chapter in manifest.get("chapters") or []
        if str(chapter.get("chapter_id") or "")
    }
    entries_by_chapter: dict[str, list[dict]] = {}
    for entry in script_entries:
        chapter_id = str(entry.get("chapter_id") or "").strip()
        entries_by_chapter.setdefault(chapter_id or "__unscoped__", []).append(entry)

    refreshed: dict[str, dict] = {}
    for chapter_id, entries in entries_by_chapter.items():
        if chapter_id == "__unscoped__":
            report = _validate_script_entries(None, entries, character_book, [])
            report.update({
                "chapter_id": "__unscoped__",
                "chapter_title": "未归属章节",
            })
            if entries:
                report["issues"] = [
                    {
                        "severity": "warning",
                        "code": "unscoped_import",
                        "message": "导入内容未匹配到章节；建议使用导出的 # [chapter_id] 章节标题格式，或选择单章导入。",
                    },
                    *(report.get("issues") or []),
                ]
                report["issue_count"] = len(report["issues"])
                report["warning_count"] = report.get("warning_count", 0) + 1
            refreshed[chapter_id] = report
        else:
            chapter = chapters_by_id.get(chapter_id)
            refreshed[chapter_id] = _validate_script_entries(chapter, entries, character_book, [])

    for chapter_id, report in existing_chapters.items():
        if chapter_id == "__import__" and isinstance(report, dict):
            refreshed[chapter_id] = report

    return _save_script_issues({"chapters": refreshed})


def _sync_chunks_for_script_entries(final_entries: list[dict], selected_chapter_ids: Optional[set[str]] = None) -> list[dict]:
    selected_chapter_ids = selected_chapter_ids or set()
    fresh_chunks = _script_entries_to_chunks(final_entries)

    if not selected_chapter_ids:
        existing_chunks = _read_json_list(_current_chunks_path())
        _remove_unreferenced_audio_files(
            _current_book_dir(),
            [chunk.get("audio_path") for chunk in existing_chunks],
            fresh_chunks,
        )
        return fresh_chunks

    existing_chunks = _read_json_list(_current_chunks_path())
    if not existing_chunks:
        return fresh_chunks

    old_by_chapter: dict[str, list[dict]] = {}
    old_unscoped: list[dict] = []
    for chunk in existing_chunks:
        chapter_id = str(chunk.get("chapter_id") or "")
        if chapter_id:
            old_by_chapter.setdefault(chapter_id, []).append(chunk)
        else:
            old_unscoped.append(chunk)

    fresh_by_chapter: dict[str, list[dict]] = {}
    chapter_order: list[str] = []
    fresh_unscoped: list[dict] = []
    for chunk in fresh_chunks:
        chapter_id = str(chunk.get("chapter_id") or "")
        if not chapter_id:
            fresh_unscoped.append(chunk)
            continue
        if chapter_id not in fresh_by_chapter:
            chapter_order.append(chapter_id)
        fresh_by_chapter.setdefault(chapter_id, []).append(chunk)

    merged_chunks: list[dict] = []
    replaced_audio_paths: list[Any] = []
    for chapter_id in chapter_order:
        if chapter_id in selected_chapter_ids:
            replaced_audio_paths.extend(chunk.get("audio_path") for chunk in old_by_chapter.get(chapter_id, []))
            merged_chunks.extend(fresh_by_chapter.get(chapter_id, []))
        else:
            merged_chunks.extend(old_by_chapter.get(chapter_id) or fresh_by_chapter.get(chapter_id, []))

    known_ids = set(chapter_order)
    for chapter_id, chunks in old_by_chapter.items():
        if chapter_id not in known_ids and chapter_id not in selected_chapter_ids:
            merged_chunks.extend(chunks)

    merged_chunks.extend(old_unscoped if selected_chapter_ids else fresh_unscoped)

    for idx, chunk in enumerate(merged_chunks):
        chunk["id"] = idx
        if str(chunk.get("chapter_id") or "") in selected_chapter_ids:
            chunk["status"] = "pending"
            chunk["audio_path"] = None
        else:
            chunk.setdefault("status", "pending")
            chunk.setdefault("audio_path", None)
    _remove_unreferenced_audio_files(_current_book_dir(), replaced_audio_paths, merged_chunks)
    return merged_chunks


def _current_source_file_path() -> str:
    state_path = _current_state_path()
    if not os.path.exists(state_path):
        return ""
    try:
        with open(state_path, "r", encoding="utf-8") as f:
            state = json.load(f)
        input_file = str(state.get("input_file_path") or "")
        return input_file if os.path.exists(input_file) else ""
    except (json.JSONDecodeError, ValueError):
        return ""


def _read_text_file(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except UnicodeDecodeError:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()


@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...)):
    book = _ensure_current_book()
    source_dir = os.path.join(_book_dir(book["id"]), "source")
    os.makedirs(source_dir, exist_ok=True)
    safe_filename = os.path.basename(file.filename)
    file_path = os.path.join(source_dir, safe_filename)
    async with aiofiles.open(file_path, 'wb') as out_file:
        content = await file.read()
        await out_file.write(content)

    # Convert EPUB to plain text
    if file.filename.lower().endswith('.epub'):
        try:
            text = extract_epub_text(file_path)
        except Exception as e:
            os.remove(file_path)
            raise HTTPException(status_code=400, detail=f"Failed to process EPUB: {e}")
        if not text.strip():
            os.remove(file_path)
            raise HTTPException(status_code=400, detail="No readable text content found in EPUB.")
        txt_path = file_path.rsplit('.', 1)[0] + '.txt'
        with open(txt_path, 'w', encoding='utf-8') as f:
            f.write(text)
        file_path = txt_path

    # Save input path to the book-local state.json
    state_path = _current_state_path()
    state = {}
    if os.path.exists(state_path):
        with open(state_path, "r", encoding="utf-8") as f:
            try:
                state = json.load(f)
            except (json.JSONDecodeError, ValueError):
                pass

    state["input_file_path"] = file_path
    source_text = _read_text_file(file_path)
    chapters_manifest = save_chapters_for_current_book(source_text)
    state["chapters_manifest_path"] = _current_chapters_manifest_path()
    state["chapter_count"] = chapters_manifest.get("chapter_count", 0)
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)

    _touch_book(
        book["id"],
        source_filename=safe_filename,
        chapter_count=chapters_manifest.get("chapter_count", 0),
        char_count=chapters_manifest.get("total_chars", 0),
    )
    return {"filename": file.filename, "path": file_path, "chapters": chapters_manifest}


@app.get("/api/chapters")
async def get_chapters():
    book = _ensure_current_book()
    manifest = load_current_chapters_manifest()
    if not manifest.get("chapters"):
        source_path = _current_source_file_path()
        if source_path:
            manifest = save_chapters_for_current_book(_read_text_file(source_path))
            _touch_book(
                book["id"],
                chapter_count=manifest.get("chapter_count", 0),
                char_count=manifest.get("total_chars", 0),
            )
    return manifest

@app.post("/api/chapters/resplit")
async def resplit_chapters(request: Optional[ChapterResplitRequest] = None):
    request = request or ChapterResplitRequest()
    if process_state["audio"]["running"] or process_state["script"]["running"] or process_state["review"]["running"]:
        raise HTTPException(status_code=409, detail="Cannot resplit chapters while generation is running.")

    source_path = _current_source_file_path()
    if not source_path:
        raise HTTPException(status_code=400, detail="No source file available for chapter splitting.")

    source_text = _read_text_file(source_path)
    if not source_text.strip():
        raise HTTPException(status_code=400, detail="Source file is empty.")

    current_manifest = load_current_chapters_manifest()
    old_chapter_ids = [
        str(chapter.get("chapter_id") or "")
        for chapter in current_manifest.get("chapters") or []
        if str(chapter.get("chapter_id") or "")
    ]
    artifacts = _chapter_generated_artifacts(old_chapter_ids)
    has_outputs = any(artifacts.get(key) for key in ("script_entries", "partial_entries", "chunks"))
    preview = preview_chapters_from_text(source_text)
    preview_payload = {
        "current_chapter_count": current_manifest.get("chapter_count", 0),
        "current_total_chars": current_manifest.get("total_chars", 0),
        "preview": preview,
        "generated_artifacts": artifacts,
        "will_invalidate": has_outputs,
    }
    if request.dry_run:
        return {
            "status": "preview",
            "message": "Chapter split preview only. No files or generated outputs were changed.",
            **preview_payload,
        }
    if has_outputs and not request.confirm_invalidate:
        return {
            "status": "needs_confirmation",
            "message": "Resplitting chapters will clear existing script entries, chunks, chapter memory, issue reports, and merged audio.",
            **preview_payload,
        }

    invalidated = _clear_generation_state_for_resplit(old_chapter_ids)
    manifest = save_chapters_for_current_book(source_text)

    state = {}
    state_path = _current_state_path()
    if os.path.exists(state_path):
        try:
            with open(state_path, "r", encoding="utf-8") as f:
                state = json.load(f)
        except (json.JSONDecodeError, ValueError):
            state = {}
    state["input_file_path"] = source_path
    state["chapters_manifest_path"] = _current_chapters_manifest_path()
    state["chapter_count"] = manifest.get("chapter_count", 0)
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)

    book = _ensure_current_book()
    _touch_book(
        book["id"],
        chapter_count=manifest.get("chapter_count", 0),
        char_count=manifest.get("total_chars", 0),
    )
    return {
        "status": "resplit",
        "chapters": manifest,
        "invalidated": invalidated,
        "generated_artifacts": artifacts,
    }

@app.post("/api/chapters/append")
async def append_chapters(file: UploadFile = File(...), dry_run: bool = Form(False)):
    if process_state["audio"]["running"] or process_state["script"]["running"] or process_state["review"]["running"]:
        raise HTTPException(status_code=409, detail="Cannot append chapters while generation is running.")

    book = _ensure_current_book()
    current_manifest = load_current_chapters_manifest()
    current_records = _load_chapter_records(current_manifest)
    if not current_records:
        raise HTTPException(status_code=400, detail="No current chapters found. Upload the source file first.")

    source_text, safe_filename = await _read_uploaded_source_text(file)
    if not source_text.strip():
        raise HTTPException(status_code=400, detail="Uploaded source is empty.")

    incoming_chapters = split_text_into_chapters(source_text, default_title="全文")
    plan = _append_chapter_plan(current_records, incoming_chapters)

    appended_chapters = plan["appended_chapters"]
    sample_chapters = []
    for offset, chapter in enumerate(appended_chapters[:8], start=1):
        item = {key: value for key, value in chapter.items() if key != "content"}
        item["index"] = plan["current_chapter_count"] + offset
        item["path"] = f"chapters/{safe_chapter_filename(item['index'])}"
        sample_chapters.append(item)

    preview_payload = {
        "append_mode": plan["append_mode"],
        "current_chapter_count": plan["current_chapter_count"],
        "incoming_chapter_count": plan["incoming_chapter_count"],
        "matched_existing_count": plan["matched_existing_count"],
        "append_count": plan["append_count"],
        "sample_chapters": sample_chapters,
        "will_preserve_generated_outputs": True,
    }

    if dry_run:
        return {
            "status": "preview",
            "message": "Chapter append preview only. Existing script, chunks, voices, and audio were not changed.",
            **preview_payload,
        }

    if plan["append_count"] == 0:
        return {
            "status": "no_new_chapters",
            "message": "No new chapters were found after the existing chapters.",
            "chapters": current_manifest,
            **preview_payload,
        }

    updated_manifest, rebuilt_source_filename = _append_chapters_to_current_book(appended_chapters, safe_filename)
    appended_ids = [
        str(chapter.get("chapter_id") or "")
        for chapter in (updated_manifest.get("chapters") or [])[plan["current_chapter_count"]:]
        if str(chapter.get("chapter_id") or "")
    ]
    _update_script_generation_state_for_changes(
        appended_ids,
        status="missing",
        source="chapter_append",
    )
    _touch_book(
        book["id"],
        source_filename=rebuilt_source_filename,
        chapter_count=updated_manifest.get("chapter_count", 0),
        char_count=updated_manifest.get("total_chars", 0),
    )
    return {
        "status": "appended",
        "message": "New chapters were appended. Existing generated outputs were preserved.",
        "chapters": updated_manifest,
        "source_filename": rebuilt_source_filename,
        "appended_chapter_ids": appended_ids,
        **preview_payload,
    }

@app.get("/api/chapters/progress")
async def get_chapter_progress():
    return _chapter_tts_progress()

@app.get("/api/chapters/{chapter_id}")
async def get_chapter(chapter_id: str):
    manifest = load_current_chapters_manifest()
    chapters = manifest.get("chapters") or []
    chapter_index = next((idx for idx, item in enumerate(chapters) if str(item.get("chapter_id") or "") == chapter_id), None)
    chapter = _find_chapter(manifest, chapter_id)
    path = _chapter_file_path(chapter)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Chapter file not found")
    content = _read_text_file(path)
    artifacts = _chapter_generated_artifacts([chapter_id])
    next_chapter = None
    prev_chapter = None
    if chapter_index is not None:
        if chapter_index + 1 < len(chapters):
            next_item = chapters[chapter_index + 1]
            next_chapter = {
                "chapter_id": next_item.get("chapter_id"),
                "chapter_index": next_item.get("index"),
                "chapter_title": next_item.get("title") or next_item.get("chapter_id"),
            }
        if chapter_index - 1 >= 0:
            prev_item = chapters[chapter_index - 1]
            prev_chapter = {
                "chapter_id": prev_item.get("chapter_id"),
                "chapter_index": prev_item.get("index"),
                "chapter_title": prev_item.get("title") or prev_item.get("chapter_id"),
            }
    return {
        **chapter,
        "content": content,
        "needs_regeneration": artifacts["has_script"] or artifacts["has_chunks"],
        "generated_artifacts": artifacts,
        "next_chapter": next_chapter,
        "prev_chapter": prev_chapter,
    }

@app.post("/api/chapters/{chapter_id}")
async def update_chapter(chapter_id: str, request: ChapterUpdateRequest):
    manifest = load_current_chapters_manifest()
    chapter = _find_chapter(manifest, chapter_id)
    path = _chapter_file_path(chapter)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Chapter file not found")

    original_title = str(chapter.get("title") or chapter.get("chapter_title") or "").strip()
    original_content = _read_text_file(path)
    changed = False

    if request.title is not None:
        title = request.title.strip()
        if not title:
            raise HTTPException(status_code=400, detail="Chapter title cannot be empty")
        if title != original_title:
            chapter["title"] = title
            chapter["chapter_title"] = title
            changed = True

    if request.content is not None:
        content = request.content.strip()
        if not content:
            raise HTTPException(status_code=400, detail="Chapter content cannot be empty")
        if content != original_content.strip():
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            chapter["char_count"] = len(content)
            changed = True

    manifest = save_current_chapters_manifest(manifest)
    book = _ensure_current_book()
    _touch_book(
        book["id"],
        chapter_count=manifest.get("chapter_count", 0),
        char_count=manifest.get("total_chars", 0),
    )
    invalidated = _invalidate_generated_outputs([chapter_id]) if changed else {
        "removed_script_entries": 0,
        "removed_partial_entries": 0,
        "removed_chunks": 0,
        "removed_audio_files": [],
    }
    artifacts = _chapter_generated_artifacts([chapter_id])
    return {
        "status": "saved",
        "chapter": chapter,
        "manifest": manifest,
        "needs_regeneration": artifacts["has_script"] or artifacts["has_chunks"],
        "generated_artifacts": artifacts,
        "invalidated": invalidated,
    }

@app.post("/api/chapters/{chapter_id}/split")
async def split_chapter(chapter_id: str, request: ChapterSplitRequest):
    manifest = load_current_chapters_manifest()
    if not manifest.get("chapters"):
        raise HTTPException(status_code=404, detail="No chapters found")

    records = _load_chapter_records(manifest)
    chapter_index = next((idx for idx, item in enumerate(records) if str(item.get("chapter_id") or "") == chapter_id), None)
    if chapter_index is None:
        raise HTTPException(status_code=404, detail=f"Chapter '{chapter_id}' not found")

    source = records[chapter_index]
    content = str(source.get("content") or "")
    if request.content_before is not None and request.content_after is not None:
        first_part = str(request.content_before).rstrip()
        second_part = str(request.content_after).lstrip()
    else:
        if request.split_at is None:
            raise HTTPException(status_code=400, detail="Split position is required")
        split_at = int(request.split_at)
        if split_at <= 0 or split_at >= len(content):
            raise HTTPException(status_code=400, detail="Split position must be inside the chapter content")
        first_part = content[:split_at].rstrip()
        second_part = content[split_at:].lstrip()
    if not first_part or not second_part:
        raise HTTPException(status_code=400, detail="Split would create an empty chapter")

    original_title = (
        request.title.strip()
        or str(source.get("title") or source.get("chapter_title") or chapter_id).strip()
        or chapter_id
    )
    new_title = request.new_title.strip() or f"{original_title}（下）"
    new_id_base = f"{chapter_id}_split"
    new_id = new_id_base
    suffix = 2
    existing_ids = {str(item.get("chapter_id") or "") for item in records}
    while new_id in existing_ids:
        new_id = f"{new_id_base}_{suffix}"
        suffix += 1
    new_filename = f"{_safe_chapter_slug(new_id)}.txt"

    source["content"] = first_part
    source["char_count"] = len(first_part)
    source["title"] = original_title
    source["chapter_title"] = original_title
    source["start_line"] = source.get("start_line") or 1

    new_record = {
        "chapter_id": new_id,
        "title": new_title,
        "chapter_title": new_title,
        "filename": new_filename,
        "path": f"chapters/{new_filename}",
        "char_count": len(second_part),
        "start_line": source.get("start_line") or 1,
        "content": second_part,
    }

    records.insert(chapter_index + 1, new_record)
    updated_manifest = _write_chapter_records(records, generated_at=manifest.get("generated_at"))
    invalidated = _invalidate_generated_outputs([chapter_id])
    updated_source = _find_chapter(updated_manifest, chapter_id)
    updated_new_chapter = _find_chapter(updated_manifest, new_id)

    book = _ensure_current_book()
    _touch_book(
        book["id"],
        chapter_count=updated_manifest.get("chapter_count", 0),
        char_count=updated_manifest.get("total_chars", 0),
    )

    return {
        "status": "split",
        "chapter_id": chapter_id,
        "new_chapter_id": new_id,
        "chapter": {
            "chapter_id": updated_source["chapter_id"],
            "index": updated_source["index"],
            "title": updated_source["title"],
            "chapter_title": updated_source["chapter_title"],
            "char_count": updated_source["char_count"],
        },
        "new_chapter": {
            "chapter_id": updated_new_chapter["chapter_id"],
            "index": updated_new_chapter["index"],
            "title": updated_new_chapter["title"],
            "chapter_title": updated_new_chapter["chapter_title"],
            "char_count": updated_new_chapter["char_count"],
        },
        "manifest": updated_manifest,
        "invalidated": invalidated,
        "affected_chapter_ids": [chapter_id, new_id],
        "needs_regeneration": True,
    }

@app.post("/api/chapters/{chapter_id}/merge_next")
async def merge_chapter_with_next(chapter_id: str, request: Optional[ChapterMergeNextRequest] = None):
    manifest = load_current_chapters_manifest()
    if not manifest.get("chapters"):
        raise HTTPException(status_code=404, detail="No chapters found")

    records = _load_chapter_records(manifest)
    chapter_index = next((idx for idx, item in enumerate(records) if str(item.get("chapter_id") or "") == chapter_id), None)
    if chapter_index is None:
        raise HTTPException(status_code=404, detail=f"Chapter '{chapter_id}' not found")
    if chapter_index + 1 >= len(records):
        raise HTTPException(status_code=400, detail="This is already the last chapter")

    current = records[chapter_index]
    next_chapter = records[chapter_index + 1]
    current_title = str(current.get("title") or current.get("chapter_title") or chapter_id).strip() or chapter_id
    merged_title = (request.title.strip() if request and request.title else current_title) or current_title

    left = str(request.content if request and request.content is not None else current.get("content") or "").rstrip()
    right = str(next_chapter.get("content") or "").lstrip()
    merged_content = left if not right else (left + ("\n\n" if left else "") + right)

    current["content"] = merged_content
    current["char_count"] = len(merged_content)
    current["title"] = merged_title
    current["chapter_title"] = merged_title

    removed_id = str(next_chapter.get("chapter_id") or "")
    removed_path = _chapter_file_path(next_chapter)
    current_path = _chapter_file_path(current)
    records.pop(chapter_index + 1)
    updated_manifest = _write_chapter_records(records, generated_at=manifest.get("generated_at"))
    if os.path.exists(removed_path) and os.path.abspath(removed_path) != os.path.abspath(current_path):
        os.remove(removed_path)

    invalidated = _invalidate_generated_outputs([chapter_id, removed_id])
    updated_chapter = _find_chapter(updated_manifest, chapter_id)
    book = _ensure_current_book()
    _touch_book(
        book["id"],
        chapter_count=updated_manifest.get("chapter_count", 0),
        char_count=updated_manifest.get("total_chars", 0),
    )

    return {
        "status": "merged",
        "chapter_id": chapter_id,
        "removed_chapter_id": removed_id,
        "chapter": {
            "chapter_id": updated_chapter["chapter_id"],
            "index": updated_chapter["index"],
            "title": updated_chapter["title"],
            "chapter_title": updated_chapter["chapter_title"],
            "char_count": updated_chapter["char_count"],
        },
        "manifest": updated_manifest,
        "invalidated": invalidated,
        "affected_chapter_ids": [chapter_id, removed_id],
        "needs_regeneration": True,
    }

@app.get("/api/script_progress")
async def get_script_progress():
    manifest = load_current_chapters_manifest()
    if not manifest.get("chapters"):
        source_path = _current_source_file_path()
        if source_path:
            manifest = save_chapters_for_current_book(_read_text_file(source_path))
    return _script_chapter_progress(manifest)

@app.get("/api/script_issues")
async def get_script_issues(chapter_id: Optional[str] = None):
    issues = _load_script_issues()
    if chapter_id:
        chapter_issues = (issues.get("chapters") or {}).get(chapter_id)
        if not chapter_issues:
            return {
                "chapter_id": chapter_id,
                "issue_count": 0,
                "error_count": 0,
                "warning_count": 0,
                "unknown_speaker_count": 0,
                "unknown_speakers": [],
                "issues": [],
            }
        return chapter_issues
    chapters = issues.get("chapters") or {}
    return {
        **issues,
        "summary": {
            "chapter_count": len(chapters),
            "issue_chapters": sum(1 for item in chapters.values() if isinstance(item, dict) and item.get("issue_count")),
            "total_issues": sum((item.get("issue_count") or 0) for item in chapters.values() if isinstance(item, dict)),
            "error_count": sum((item.get("error_count") or 0) for item in chapters.values() if isinstance(item, dict)),
            "warning_count": sum((item.get("warning_count") or 0) for item in chapters.values() if isinstance(item, dict)),
            "unknown_speaker_chapters": sum(1 for item in chapters.values() if isinstance(item, dict) and item.get("unknown_speaker_count")),
        },
    }

@app.get("/api/chapter_memory")
async def get_chapter_memory(chapter_id: Optional[str] = None):
    memory = _load_chapter_memory()
    if chapter_id:
        chapter_memory = (memory.get("chapters") or {}).get(chapter_id)
        if not chapter_memory:
            return {"chapter_id": chapter_id, "available": False}
        return {**chapter_memory, "available": True}
    chapters = memory.get("chapters") or {}
    return {
        **memory,
        "summary": {
            "chapter_count": len(chapters),
            "stale_chapters": sum(1 for item in chapters.values() if isinstance(item, dict) and item.get("stale")),
            "available_chapters": sum(1 for item in chapters.values() if isinstance(item, dict)),
        },
    }

@app.get("/api/script_outputs")
async def get_script_outputs():
    return _script_output_files(_ensure_current_book())

@app.get("/api/story_bible")
async def get_story_bible():
    _ensure_current_book()
    path = _current_story_bible_path()
    if not os.path.exists(path):
        return {
            "available": False,
            "path": "story_bible.json",
            "message": "story_bible.json 尚未建立。生成脚本完成后会自动建立，也可以手动重建。",
        }
    payload = _read_json_payload(path, {})
    if not isinstance(payload, dict):
        raise HTTPException(status_code=500, detail="story_bible.json is not a JSON object")
    return {"available": True, "path": "story_bible.json", "story_bible": payload}

@app.post("/api/story_bible/rebuild")
async def rebuild_story_bible():
    bible = _build_story_bible_for_current_book()
    return {
        "status": "rebuilt",
        "available": True,
        "path": "story_bible.json",
        "chapter_count": bible.get("chapter_count", 0),
        "character_count": len(bible.get("characters") or []),
        "story_bible": bible,
    }

@app.get("/api/script_action_items")
async def get_script_action_items():
    _ensure_current_book()
    return _script_action_items()

@app.get("/api/script_generation_snapshot")
async def get_script_generation_snapshot():
    _ensure_current_book()
    snapshot = _latest_generation_snapshot()
    return {"available": bool(snapshot), "snapshot": snapshot}

@app.post("/api/generate_script")
async def generate_script(background_tasks: BackgroundTasks, request: Optional[ScriptGenerateRequest] = None):
    book_dir = _current_book_dir()
    mode = ((request.mode if request else None) or "script").strip().lower()
    if mode not in {"script", "characters"}:
        raise HTTPException(status_code=400, detail="mode must be script or characters")
    engine = "character_pipeline" if mode == "characters" else "chapter_pipeline"
    reuse_character_book = bool(request and request.reuse_character_book and mode == "script")

    # Get input file from book-local state.json
    state_path = os.path.join(book_dir, "state.json")
    if not os.path.exists(state_path):
        raise HTTPException(status_code=400, detail="No input file selected")

    with open(state_path, "r", encoding="utf-8") as f:
        state = json.load(f)
        input_file = state.get("input_file_path")

    if not input_file:
         raise HTTPException(status_code=400, detail="No input file found in state")

    if process_state["script"]["running"]:
         raise HTTPException(status_code=400, detail="Script generation already running")

    manifest = load_current_chapters_manifest()
    if not manifest.get("chapters") and input_file and os.path.exists(input_file):
        manifest = save_chapters_for_current_book(_read_text_file(input_file))
        book = _ensure_current_book()
        _touch_book(
            book["id"],
            chapter_count=manifest.get("chapter_count", 0),
            char_count=manifest.get("total_chars", 0),
        )
    if not manifest.get("chapters"):
        raise HTTPException(status_code=400, detail="No chapters found. Upload the source file again.")

    known_ids = {str(chapter.get("chapter_id") or "") for chapter in manifest.get("chapters") or []}
    requested_chapter_ids = []
    if request and request.missing_only:
        requested_filter = {chapter_id for chapter_id in (request.chapter_ids or []) if chapter_id}
        missing_ids = sorted(requested_filter - known_ids)
        if missing_ids:
            raise HTTPException(status_code=400, detail=f"Unknown chapter IDs: {', '.join(missing_ids)}")
        progress = _script_chapter_progress(manifest)
        requested_chapter_ids = [
            chapter["chapter_id"]
            for chapter in progress.get("chapters") or []
            if chapter.get("chapter_id")
            and (not requested_filter or chapter.get("chapter_id") in requested_filter)
            and (
                not chapter.get("generated")
                or chapter.get("failed")
                or chapter.get("cancelled")
                or chapter.get("interrupted")
            )
        ]
        if not requested_chapter_ids:
            raise HTTPException(status_code=400, detail="No missing chapters to generate.")
    elif request and request.chapter_ids:
        requested_chapter_ids = [chapter_id for chapter_id in request.chapter_ids if chapter_id]
        missing_ids = sorted(set(requested_chapter_ids) - known_ids)
        if missing_ids:
            raise HTTPException(status_code=400, detail=f"Unknown chapter IDs: {', '.join(missing_ids)}")
        if not requested_chapter_ids:
            raise HTTPException(status_code=400, detail="No valid chapter IDs provided.")

    if reuse_character_book and not _has_reusable_character_book(book_dir):
        raise HTTPException(
            status_code=400,
            detail="当前人物池为空。请先点击“仅分析人物池”，或在人物池页面导入/新增角色后再复用人物池生成标注脚本。",
        )

    config = await get_config()
    generation = config.get("generation") if isinstance(config.get("generation"), dict) else {}
    enable_chapter_memory = bool(generation.get("enable_chapter_memory") and mode == "script")
    estimate = _script_generation_estimate(
        manifest,
        requested_chapter_ids,
        mode=mode,
        reuse_character_book=reuse_character_book,
        enable_chapter_memory=enable_chapter_memory,
        generation=generation,
    )

    if request and request.dry_run:
        return {
            "status": "dry_run",
            "engine": engine,
            "mode": mode,
            "reuse_character_book": reuse_character_book,
            "enable_chapter_memory": enable_chapter_memory,
            "chapter_count": manifest.get("chapter_count", 0),
            "selected_chapter_ids": requested_chapter_ids,
            "estimate": estimate,
        }

    script_name = "generate_script_chapters.py"
    command = [sys.executable, "-u", script_name, input_file, "--workspace-dir", book_dir]
    if requested_chapter_ids:
        command.extend(["--chapter-ids", ",".join(requested_chapter_ids)])
    if mode == "characters":
        command.extend(["--mode", "characters"])
    elif reuse_character_book:
        command.append("--reuse-character-book")
    if enable_chapter_memory:
        command.append("--enable-chapter-memory")
    snapshot = _create_generation_snapshot(
        f"{mode} generation started",
        requested_chapter_ids or estimate.get("target_chapter_ids") or [],
    )
    process_state["script"]["running"] = True
    process_state["script"]["logs"] = []
    background_tasks.add_task(run_process, command, "script")
    return {
        "status": "started",
        "engine": engine,
        "mode": mode,
        "reuse_character_book": reuse_character_book,
        "enable_chapter_memory": enable_chapter_memory,
        "chapter_count": manifest.get("chapter_count", 0),
        "selected_chapter_ids": requested_chapter_ids,
        "estimate": estimate,
        "snapshot": snapshot,
    }

@app.post("/api/review_script")
async def review_script(background_tasks: BackgroundTasks, request: Optional[ScriptReviewRequest] = None):
    book_dir = _current_book_dir()
    script_path = os.path.join(book_dir, "annotated_script.json")
    if not os.path.exists(script_path):
        raise HTTPException(status_code=400, detail="No annotated script found. Generate a script first.")

    if process_state["review"]["running"]:
        raise HTTPException(status_code=400, detail="Script review already running")

    if request and request.dry_run:
        entries = _load_script_entries(script_path)
        config = await get_config()
        generation = config.get("generation") if isinstance(config.get("generation"), dict) else {}
        batch_size = int(generation.get("review_batch_size") or 25)
        batch_count = (len(entries) + max(batch_size, 1) - 1) // max(batch_size, 1)
        return {
            "status": "dry_run",
            "engine": "lightweight_review",
            "entry_count": len(entries),
            "batch_size": batch_size,
            "batch_count": batch_count,
        }

    process_state["review"]["running"] = True
    process_state["review"]["logs"] = []
    background_tasks.add_task(run_process, [sys.executable, "-u", "review_script.py", "--workspace-dir", book_dir], "review")
    return {"status": "started", "engine": "lightweight_review"}


@app.post("/api/cancel_script")
async def cancel_script():
    process = running_processes.get("script")
    if not process or process.poll() is not None:
        return {"status": "not_running"}
    process_state["script"]["logs"].append("[CANCEL] Script cancellation requested")
    process.terminate()
    return {"status": "cancelling"}

@app.get("/api/annotated_script")
async def get_annotated_script():
    """Return the current working script, reflecting any chunk editor changes."""
    script_entries = _current_script_entries_for_export()
    if not script_entries:
        raise HTTPException(status_code=404, detail="No annotated script found")
    return script_entries

@app.get("/api/annotated_script/tagged")
async def get_tagged_script(chapter_id: Optional[str] = None):
    """Return the current script as lightweight <speaker:>text lines."""
    script_entries = _current_script_entries_for_export()
    if not script_entries:
        raise HTTPException(status_code=404, detail="No annotated script found")
    if chapter_id:
        script_entries = [entry for entry in script_entries if str(entry.get("chapter_id") or "") == chapter_id]
        if not script_entries:
            raise HTTPException(status_code=404, detail=f"No script entries found for chapter '{chapter_id}'")
    return {
        "chapter_id": chapter_id or "",
        "entry_count": len(script_entries),
        "content": entries_to_tagged_text(script_entries),
    }

@app.post("/api/annotated_script/tagged")
async def import_tagged_script(request: TaggedScriptImportRequest):
    """Import lightweight <speaker:>text lines into annotated_script.json and rebuild chunks."""
    if process_state["audio"]["running"] or process_state["script"]["running"] or process_state["review"]["running"]:
        raise HTTPException(status_code=409, detail="Cannot import script while generation is running.")

    scope = (request.replace_scope or "all").strip().lower()
    if scope not in {"all", "chapter"}:
        raise HTTPException(status_code=400, detail="replace_scope must be all or chapter")

    manifest = load_current_chapters_manifest()
    chapter_meta = None
    target_chapter_id = (request.chapter_id or "").strip()
    if scope == "chapter":
        if not target_chapter_id:
            raise HTTPException(status_code=400, detail="chapter_id is required when replace_scope=chapter")
        chapter = _find_chapter(manifest, target_chapter_id)
        chapter_meta = {
            "chapter_id": target_chapter_id,
            "chapter_index": chapter.get("index"),
            "chapter_title": chapter.get("title") or target_chapter_id,
        }

    if scope == "chapter":
        imported_entries, parse_issues = parse_tagged_script_text(
            request.content,
            default_instruct=request.default_instruct,
            chapter_meta=chapter_meta,
        )
    else:
        imported_entries, parse_issues = _parse_tagged_script_with_manifest(
            request.content,
            manifest,
            request.default_instruct,
        )
    if not imported_entries:
        raise HTTPException(status_code=400, detail="No valid tagged script entries found")

    character_book = _load_character_book(_current_book_dir())
    imported_entries, speaker_updates = _normalize_entries_for_character_book(imported_entries, character_book)
    preview = tagged_script_preview(imported_entries, parse_issues)
    if request.dry_run:
        return {
            "status": "dry_run",
            "replace_scope": scope,
            "chapter_id": target_chapter_id,
            "speaker_updates": speaker_updates,
            **preview,
        }

    existing_entries = _load_script_entries(_current_script_path())
    if scope == "chapter":
        final_entries = []
        inserted = False
        for entry in existing_entries:
            entry_chapter_id = str(entry.get("chapter_id") or "")
            if entry_chapter_id == target_chapter_id:
                if not inserted:
                    final_entries.extend(imported_entries)
                    inserted = True
                continue
            final_entries.append(entry)
        if not inserted:
            chapter_order = [str(chapter.get("chapter_id") or "") for chapter in manifest.get("chapters") or []]
            inserted = False
            for idx, entry in enumerate(final_entries):
                entry_chapter_id = str(entry.get("chapter_id") or "")
                if (
                    entry_chapter_id
                    and target_chapter_id in chapter_order
                    and entry_chapter_id in chapter_order
                    and chapter_order.index(entry_chapter_id) > chapter_order.index(target_chapter_id)
                ):
                    final_entries[idx:idx] = imported_entries
                    inserted = True
                    break
            if not inserted:
                final_entries.extend(imported_entries)
    else:
        final_entries = imported_entries

    _write_json(_current_script_path(), final_entries)
    chunks = _sync_chunks_for_script_entries(
        final_entries,
        {target_chapter_id} if scope == "chapter" and target_chapter_id else set(),
    )
    _write_json(_current_chunks_path(), chunks)

    partial_path = os.path.join(_current_book_dir(), "annotated_script.partial.json")
    if os.path.exists(partial_path):
        os.remove(partial_path)

    validation_reports = _validate_imported_script_by_chapter(
        imported_entries,
        parse_issues,
        manifest,
        {target_chapter_id} if scope == "chapter" and target_chapter_id else None,
    )
    script_issues = _load_script_issues()
    issue_chapters = script_issues.get("chapters") if isinstance(script_issues.get("chapters"), dict) else {}
    if scope == "chapter" and target_chapter_id:
        issue_chapters[target_chapter_id] = validation_reports.get(
            target_chapter_id,
            _validate_script_entries(chapter, imported_entries, character_book, parse_issues),
        )
        if "__import__" in validation_reports:
            issue_chapters["__import__"] = validation_reports["__import__"]
        script_issues["chapters"] = issue_chapters
        _save_script_issues(script_issues)
        _mark_memory_stale_after([target_chapter_id], "该章节脚本已手动导入，后续章节记忆可能需要刷新。")
        _update_script_generation_state_for_changes(
            [target_chapter_id],
            status="done",
            source="manual_import",
            entry_counts={target_chapter_id: len(imported_entries)},
        )
    else:
        _save_script_issues({"chapters": validation_reports})
        _save_chapter_memory({"chapters": {}})
        _sync_script_generation_state_from_entries(
            final_entries,
            source="manual_import",
            manifest=manifest,
        )

    voice_defaults = _ensure_voice_config_for_script(_current_book_dir(), write=False)
    return {
        "status": "imported",
        "replace_scope": scope,
        "chapter_id": target_chapter_id,
        "imported_entries": len(imported_entries),
        "total_entries": len(final_entries),
        "total_chunks": len(chunks),
        "speaker_updates": speaker_updates,
        "voice_config_added": len(voice_defaults.get("added") or []),
        "voice_config_updated": len(voice_defaults.get("updated") or []),
        "voice_config_total": voice_defaults.get("total", 0),
        "preview": preview,
    }


@app.get("/api/characters")
async def get_characters(sort_by: Optional[str] = None, sort_order: Optional[str] = None):
    """Return the current book's character pool merged from agent output and script speakers."""
    book_dir = _current_book_dir()
    script_path = _current_script_path()
    character_book = _load_character_book(book_dir)
    voice_config = _ensure_voice_config_for_script(book_dir, write=False).get("voice_config") or {}
    stored_voice_names = _stored_voice_config_names(book_dir)
    characters_by_name = {}
    alias_lookup = _character_lookup(character_book)
    source = "character_book" if character_book.get("characters") else "empty"
    narrator_style = character_book.get("narrator_style", "")
    genre = character_book.get("genre", "")
    key_terms = character_book.get("key_terms") or []
    speaker_counts = {}
    speaker_char_counts = {}
    narrator_speakers = set()

    for item in character_book.get("characters") or []:
        name = str(item.get("canonical") or "").strip()
        if not name:
            continue
        config = voice_config.get(name, {})
        characters_by_name[name] = {
            "name": name,
            "aliases": item.get("aliases") or [],
            "traits": item.get("traits") or "",
            "voice_profile": item.get("voice_profile") or "",
            "confidence": item.get("confidence"),
            "source": "character_book",
            "config": config,
            "line_count": 0,
            "char_count": 0,
            "raw_speakers": [],
            **_voice_config_status_for_speaker(
                name,
                config,
                character_book,
                raw_config_exists=name in stored_voice_names,
            ),
        }

    chunk_entries = _read_json_list(_current_chunks_path())
    speaker_entries = chunk_entries or _read_json_list(script_path)
    for entry in speaker_entries:
        speaker = str(entry.get("speaker") or entry.get("type") or "").strip()
        if not speaker:
            continue
        char_count = _script_text_char_count(entry.get("text"))
        if speaker.upper() in NARRATOR_NAMES:
            narrator_speakers.add(speaker)
            speaker_counts["NARRATOR"] = speaker_counts.get("NARRATOR", 0) + 1
            speaker_char_counts["NARRATOR"] = speaker_char_counts.get("NARRATOR", 0) + char_count
            continue
        canonical = alias_lookup.get(speaker.casefold(), speaker)
        speaker_counts[canonical] = speaker_counts.get(canonical, 0) + 1
        speaker_char_counts[canonical] = speaker_char_counts.get(canonical, 0) + char_count
        if canonical not in characters_by_name:
            config = voice_config.get(canonical, {})
            characters_by_name[canonical] = {
                "name": canonical,
                "aliases": [],
                "traits": "",
                "voice_profile": "",
                "confidence": None,
                "source": "script",
                "config": config,
                "line_count": 0,
                "char_count": 0,
                "raw_speakers": [],
                **_voice_config_status_for_speaker(
                    canonical,
                    config,
                    character_book,
                    raw_config_exists=canonical in stored_voice_names,
                ),
            }
        if speaker not in characters_by_name[canonical]["raw_speakers"]:
            characters_by_name[canonical]["raw_speakers"].append(speaker)
    if source == "empty" and characters_by_name:
        source = "script"

    if narrator_speakers:
        narrator_config = voice_config.get("NARRATOR") or voice_config.get("旁白") or {}
        characters_by_name["NARRATOR"] = {
            "name": "NARRATOR",
            "aliases": ["旁白"],
            "traits": "",
            "voice_profile": narrator_style,
            "confidence": None,
            "source": "script",
            "config": narrator_config,
            "line_count": 0,
            "char_count": 0,
            "raw_speakers": sorted(narrator_speakers),
            "is_narrator": True,
            **_voice_config_status_for_speaker(
                "NARRATOR",
                narrator_config,
                character_book,
                raw_config_exists="NARRATOR" in stored_voice_names or "旁白" in stored_voice_names,
            ),
        }

    for name, count in speaker_counts.items():
        if name in characters_by_name:
            characters_by_name[name]["line_count"] = count
            characters_by_name[name]["char_count"] = speaker_char_counts.get(name, 0)

    characters = sorted(characters_by_name.values(), key=lambda item: item["name"])
    characters = _sort_speaker_rows(characters, sort_by, sort_order)
    return {
        "source": source,
        "total": len(characters),
        "characters": characters,
        "narrator_style": narrator_style,
        "genre": genre,
        "key_terms": key_terms,
    }

@app.post("/api/characters")
async def save_characters(request: CharacterBookSaveRequest):
    """Save the editable character pool for the current book."""
    book_dir = _current_book_dir()
    payload = {
        "characters": [
            {
                "canonical": item.name,
                "aliases": item.aliases,
                "traits": item.traits,
                "voice_profile": item.voice_profile,
                **({"confidence": item.confidence} if item.confidence is not None else {}),
            }
            for item in request.characters
        ],
        "narrator_style": request.narrator_style,
        "genre": request.genre,
        "key_terms": request.key_terms,
    }
    character_book = _save_character_book(book_dir, payload)
    normalization = (
        _normalize_script_speakers_for_book(book_dir, character_book)
        if request.normalize_script_speakers
        else {
            "script_speaker_updates": 0,
            "chunk_speaker_updates": 0,
            "voice_config_updates": 0,
        }
    )
    saved_voice_config = _save_voice_config_mapping(
        book_dir,
        request.voice_config,
        character_book=character_book,
        normalize_names=request.normalize_script_speakers,
    )
    invalidated = _invalidate_voice_audio(
        book_dir,
        saved_voice_config.get("changed_names", []),
        character_book=character_book,
    )
    script_sync = _sync_current_script_from_chunks(source="character_pool")
    refreshed_issues = _revalidate_current_script_issues(character_book)
    issue_reports = refreshed_issues.get("chapters") if isinstance(refreshed_issues.get("chapters"), dict) else {}
    voice_defaults = _ensure_voice_config_for_script(book_dir, write=False)
    return {
        "status": "saved",
        "total": len(character_book.get("characters") or []),
        "character_book": character_book,
        "script_entry_count": script_sync["entry_count"],
        "script_issue_reports": len(issue_reports),
        "script_issues_total": sum(
            report.get("issue_count") or 0
            for report in issue_reports.values()
            if isinstance(report, dict)
        ),
        "voice_config_added": len(voice_defaults.get("added") or []),
        "voice_config_updated": len(voice_defaults.get("updated") or []),
        "voice_config_saved": saved_voice_config.get("saved", 0),
        "voice_config_saved_names": saved_voice_config.get("saved_names", []),
        "voice_config_changed": saved_voice_config.get("changed", 0),
        "voice_config_changed_names": saved_voice_config.get("changed_names", []),
        "voice_config_removed": saved_voice_config.get("removed", 0),
        "voice_config_removed_names": saved_voice_config.get("removed_names", []),
        **invalidated,
        "voice_config_total": voice_defaults.get("total", 0),
        **normalization,
    }

@app.post("/api/characters/compact")
async def compact_characters():
    """Compact the current character pool in-place without running LLM analysis."""
    if process_state["audio"]["running"] or process_state["script"]["running"] or process_state["review"]["running"]:
        raise HTTPException(status_code=409, detail="Cannot compact character book while generation is running.")

    book_dir = _current_book_dir()
    raw_path = os.path.join(book_dir, "character_book.json")
    raw_book = _read_json_payload(raw_path, _default_character_book())
    if not isinstance(raw_book, dict):
        raw_book = _default_character_book()
    before_characters = raw_book.get("characters") if isinstance(raw_book.get("characters"), list) else []
    before_count = len(before_characters)
    before_traits_chars = sum(len(_character_traits(item.get("traits"))) for item in before_characters if isinstance(item, dict))
    before_voice_chars = sum(len(_character_traits(item.get("voice_profile"))) for item in before_characters if isinstance(item, dict))

    character_book = _save_character_book(book_dir, raw_book)
    after_characters = character_book.get("characters") or []
    voice_refresh = _refresh_saved_character_voice_styles(book_dir, character_book)
    voice_defaults = _ensure_voice_config_for_script(book_dir, write=False)

    return {
        "status": "compacted",
        "before_total": before_count,
        "after_total": len(after_characters),
        "removed_characters": max(0, before_count - len(after_characters)),
        "before_traits_chars": before_traits_chars,
        "after_traits_chars": sum(len(character.get("traits") or "") for character in after_characters),
        "before_voice_profile_chars": before_voice_chars,
        "after_voice_profile_chars": sum(len(character.get("voice_profile") or "") for character in after_characters),
        "character_book": character_book,
        "voice_style_updated": voice_refresh.get("total", 0),
        "voice_style_updated_names": voice_refresh.get("updated", []),
        "voice_config_added": len(voice_defaults.get("added") or []),
        "voice_config_updated": len(voice_defaults.get("updated") or []),
        "voice_config_total": voice_defaults.get("total", 0),
    }

@app.post("/api/characters/import")
async def import_characters(request: CharacterBookImportRequest):
    """Import an LLM-produced character_book JSON payload into the current book."""
    if process_state["audio"]["running"] or process_state["script"]["running"] or process_state["review"]["running"]:
        raise HTTPException(status_code=409, detail="Cannot import character book while generation is running.")

    try:
        parsed = _json_payload_from_text(request.content)
        incoming = _coerce_character_import_payload(parsed)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    book_dir = _current_book_dir()
    existing = _load_character_book(book_dir)
    final_book = _merge_character_books(existing, incoming) if request.merge else incoming
    if request.dry_run:
        existing_names = {
            str(character.get("canonical") or "").casefold()
            for character in existing.get("characters") or []
            if str(character.get("canonical") or "").strip()
        }
        imported_names = [
            str(character.get("canonical") or "").strip()
            for character in incoming.get("characters") or []
            if str(character.get("canonical") or "").strip()
        ]
        final_names = [
            str(character.get("canonical") or "").strip()
            for character in final_book.get("characters") or []
            if str(character.get("canonical") or "").strip()
        ]
        return {
            "status": "dry_run",
            "merge": request.merge,
            "imported_count": len(imported_names),
            "added_count": sum(1 for name in imported_names if name.casefold() not in existing_names),
            "final_count": len(final_names),
            "characters": final_book.get("characters") or [],
            "narrator_style": final_book.get("narrator_style") or "",
            "genre": final_book.get("genre") or "",
            "key_terms": final_book.get("key_terms") or [],
        }

    character_book = _save_character_book(book_dir, final_book)
    normalization = (
        _normalize_script_speakers_for_book(book_dir, character_book)
        if request.normalize_script_speakers
        else {
            "script_speaker_updates": 0,
            "chunk_speaker_updates": 0,
            "voice_config_updates": 0,
        }
    )
    script_sync = _sync_current_script_from_chunks(source="character_import")
    refreshed_issues = _revalidate_current_script_issues(character_book)
    issue_reports = refreshed_issues.get("chapters") if isinstance(refreshed_issues.get("chapters"), dict) else {}
    voice_defaults = _ensure_voice_config_for_script(book_dir, write=False)
    return {
        "status": "imported",
        "merge": request.merge,
        "imported_count": len(incoming.get("characters") or []),
        "total": len(character_book.get("characters") or []),
        "character_book": character_book,
        "script_entry_count": script_sync["entry_count"],
        "script_issue_reports": len(issue_reports),
        "script_issues_total": sum(
            report.get("issue_count") or 0
            for report in issue_reports.values()
            if isinstance(report, dict)
        ),
        "voice_config_added": len(voice_defaults.get("added") or []),
        "voice_config_updated": len(voice_defaults.get("updated") or []),
        **normalization,
    }

@app.post("/api/characters/apply_voice_style")
async def apply_character_voice_style(request: CharacterVoiceApplyRequest):
    """Copy character voice profiles into voice config character_style fields."""
    book_dir = _current_book_dir()
    character_book = _load_character_book(book_dir)
    selected = {name for name in (request.names or []) if str(name).strip()}
    mode = (request.mode or "missing").strip().lower()
    if mode not in {"missing", "overwrite", "append"}:
        raise HTTPException(status_code=400, detail="mode must be missing, overwrite, or append")

    voice_config_path = _current_voice_config_path()
    voice_config = {}
    if os.path.exists(voice_config_path):
        try:
            with open(voice_config_path, "r", encoding="utf-8") as f:
                voice_config = json.load(f)
        except (json.JSONDecodeError, ValueError):
            voice_config = {}

    speakers_by_character = _script_speakers_by_character(book_dir, character_book)
    updated = []
    for character in character_book.get("characters") or []:
        name = str(character.get("canonical") or "").strip()
        if not name or (selected and name not in selected):
            continue
        style = _character_voice_style(character)
        if not style:
            continue
        target_names = {name, *(speakers_by_character.get(name) or set())}
        changed = False
        for target_name in target_names:
            config = voice_config.get(target_name)
            if not isinstance(config, dict) or not config:
                continue
            previous_signature = _voice_config_effective_signature(config)
            existing = str(config.get("character_style") or config.get("default_style") or "").strip()
            if mode == "missing" and existing:
                continue
            if mode == "append" and existing:
                config["character_style"] = f"{existing}；{style}" if style not in existing else existing
            else:
                config["character_style"] = style
            if previous_signature != _voice_config_effective_signature(config):
                changed = True
        if changed:
            updated.append(name)

    with open(voice_config_path, "w", encoding="utf-8") as f:
        json.dump(voice_config, f, indent=2, ensure_ascii=False)
    voice_defaults = _ensure_voice_config_for_script(book_dir, write=False)
    invalidated = _invalidate_voice_audio(book_dir, updated, character_book=character_book)

    return {
        "status": "saved",
        "updated": updated,
        "total": len(updated),
        "voice_config_added": len(voice_defaults.get("added") or []),
        "voice_config_updated": len(voice_defaults.get("updated") or []),
        **invalidated,
    }

def _chapter_tts_progress() -> dict[str, Any]:
    """Return per-chapter TTS completion state for the current book."""
    book_dir = _current_book_dir()
    manifest = load_current_chapters_manifest()
    chapters = manifest.get("chapters") or []
    chunks = project_manager.load_chunks()

    stats_by_id = {}
    orphan_stats = None

    def make_stats(chapter_id="", chapter_index=None, chapter_title="", char_count=0):
        return {
            "chapter_id": chapter_id,
            "chapter_index": chapter_index,
            "chapter_title": chapter_title,
            "char_count": char_count,
            "total_chunks": 0,
            "done_chunks": 0,
            "pending_chunks": 0,
            "generating_chunks": 0,
            "error_chunks": 0,
            "audio_chunks": 0,
            "missing_audio_chunks": 0,
            "complete": False,
        }

    for chapter in chapters:
        chapter_id = str(chapter.get("chapter_id") or "")
        if not chapter_id:
            continue
        stats_by_id[chapter_id] = make_stats(
            chapter_id=chapter_id,
            chapter_index=chapter.get("index"),
            chapter_title=chapter.get("title") or chapter_id,
            char_count=chapter.get("char_count") or 0,
        )

    for chunk in chunks:
        chapter_id = str(chunk.get("chapter_id") or "")
        if chapter_id:
            stats = stats_by_id.setdefault(
                chapter_id,
                make_stats(
                    chapter_id=chapter_id,
                    chapter_index=chunk.get("chapter_index"),
                    chapter_title=chunk.get("chapter_title") or chapter_id,
                ),
            )
        else:
            if orphan_stats is None:
                orphan_stats = make_stats(chapter_id="", chapter_title="未归属章节")
            stats = orphan_stats

        stats["total_chunks"] += 1
        status = str(chunk.get("status") or "pending")
        if status == "done":
            stats["done_chunks"] += 1
        elif status == "error":
            stats["error_chunks"] += 1
        elif status == "generating":
            stats["generating_chunks"] += 1
        else:
            stats["pending_chunks"] += 1

        if _audio_file_exists(book_dir, chunk.get("audio_path")):
            stats["audio_chunks"] += 1

    chapter_progress = []
    for chapter in chapters:
        chapter_id = str(chapter.get("chapter_id") or "")
        if chapter_id in stats_by_id:
            chapter_progress.append(stats_by_id[chapter_id])

    manifest_ids = {str(chapter.get("chapter_id") or "") for chapter in chapters}
    extra_progress = [
        stats for chapter_id, stats in stats_by_id.items()
        if chapter_id and chapter_id not in manifest_ids
    ]
    extra_progress.sort(key=lambda item: (
        item.get("chapter_index") is None,
        item.get("chapter_index") or 0,
        item.get("chapter_title") or item.get("chapter_id") or "",
    ))
    chapter_progress.extend(extra_progress)
    if orphan_stats and orphan_stats["total_chunks"]:
        chapter_progress.append(orphan_stats)

    for stats in chapter_progress:
        stats["missing_audio_chunks"] = max(stats["total_chunks"] - stats["audio_chunks"], 0)
        stats["complete"] = (
            stats["total_chunks"] > 0
            and stats["done_chunks"] == stats["total_chunks"]
            and stats["audio_chunks"] == stats["total_chunks"]
        )

    summary = {
        "total_chapters": len(chapter_progress),
        "complete_chapters": sum(1 for stats in chapter_progress if stats["complete"]),
        "incomplete_chapters": sum(1 for stats in chapter_progress if not stats["complete"]),
        "total_chunks": sum(stats["total_chunks"] for stats in chapter_progress),
        "done_chunks": sum(stats["done_chunks"] for stats in chapter_progress),
        "audio_chunks": sum(stats["audio_chunks"] for stats in chapter_progress),
        "pending_chunks": sum(stats["pending_chunks"] for stats in chapter_progress),
        "generating_chunks": sum(stats["generating_chunks"] for stats in chapter_progress),
        "error_chunks": sum(stats["error_chunks"] for stats in chapter_progress),
        "missing_audio_chunks": sum(stats["missing_audio_chunks"] for stats in chapter_progress),
    }

    return {
        "summary": summary,
        "chapters": chapter_progress,
    }


def _chapter_title_for_render_scope(chapter_id: str, scoped_chunks: list[dict]) -> str:
    if not chapter_id:
        return ""
    manifest = load_current_chapters_manifest()
    for chapter in manifest.get("chapters") or []:
        if str(chapter.get("chapter_id") or "") == chapter_id:
            return str(chapter.get("title") or chapter_id)
    for chunk in scoped_chunks:
        title = str(chunk.get("chapter_title") or "").strip()
        if title:
            return title
    return chapter_id


def _render_plan(request: RenderPlanRequest) -> dict[str, Any]:
    """Return the exact chunk indices that should be rendered for a TTS scope."""
    book_dir = _current_book_dir()
    voice_defaults = _ensure_voice_config_for_script(book_dir, write=False)
    chunks = project_manager.load_chunks()
    chapter_id = (request.chapter_id or "").strip()
    scoped_pairs = [
        (index, chunk) for index, chunk in enumerate(chunks)
        if not chapter_id or str(chunk.get("chapter_id") or "") == chapter_id
    ]
    scoped_chunks = [chunk for _, chunk in scoped_pairs]
    non_empty = [chunk for chunk in scoped_chunks if str(chunk.get("text") or "").strip()]
    skipped_empty = len(scoped_chunks) - len(non_empty)

    chunk_rows = [
        {
            "chunk": chunk,
            "index": int(chunk.get("id")) if isinstance(chunk.get("id"), int) else index,
            "has_audio": _audio_file_exists(book_dir, chunk.get("audio_path")),
            "status": str(chunk.get("status") or "pending"),
        }
        for index, chunk in scoped_pairs
        if str(chunk.get("text") or "").strip()
    ]

    if request.regenerate_all:
        selected_rows = chunk_rows
    else:
        selected_rows = [
            row for row in chunk_rows
            if row["status"] != "done" or not row["has_audio"]
        ]

    indices = [row["index"] for row in selected_rows]
    missing_audio_indices = [
        row["index"] for row in chunk_rows
        if not row["has_audio"]
    ]
    stale_done_indices = [
        row["index"] for row in chunk_rows
        if row["status"] == "done" and not row["has_audio"]
    ]
    pending_indices = [
        row["index"] for row in chunk_rows
        if row["status"] not in {"done", "generating", "error"}
    ]
    error_indices = [
        row["index"] for row in chunk_rows
        if row["status"] == "error"
    ]
    generating_indices = [
        row["index"] for row in chunk_rows
        if row["status"] == "generating"
    ]

    done_count = sum(1 for row in chunk_rows if row["status"] == "done")
    audio_count = sum(1 for row in chunk_rows if row["has_audio"])
    chapter_title = _chapter_title_for_render_scope(chapter_id, scoped_chunks)

    return {
        "chapter_id": chapter_id,
        "selected_chapter_id": chapter_id,
        "chapter_title": chapter_title,
        "scope_label": chapter_title or ("当前章节" if chapter_id else "全书"),
        "regenerate_all": request.regenerate_all,
        "selection_mode": "regenerate_all" if request.regenerate_all else "missing_only",
        "indices": indices,
        "total_selected": len(indices),
        "selected_count": len(indices),
        "total_scoped": len(scoped_chunks),
        "non_empty": len(non_empty),
        "non_empty_count": len(non_empty),
        "skipped_empty": skipped_empty,
        "empty_skipped_count": skipped_empty,
        "done_count": done_count,
        "audio_count": audio_count,
        "missing_audio_count": len(missing_audio_indices),
        "missing_audio_indices": missing_audio_indices,
        "stale_done_count": len(stale_done_indices),
        "stale_done_indices": stale_done_indices,
        "pending_count": len(pending_indices),
        "pending_indices": pending_indices,
        "generating_count": len(generating_indices),
        "generating_indices": generating_indices,
        "error_count": len(error_indices),
        "error_indices": error_indices,
        "regenerate_count": len(indices) if request.regenerate_all else 0,
        "voice_config_added": len(voice_defaults.get("added") or []),
        "voice_config_updated": len(voice_defaults.get("updated") or []),
    }


def _explicit_indices_render_plan(indices: list[int], request: BatchGenerateRequest) -> dict[str, Any]:
    """Build a render-plan-shaped response for legacy explicit-index batch calls."""
    voice_defaults = _ensure_voice_config_for_script(_current_book_dir(), write=False)
    unique_indices = []
    seen = set()
    for index in indices or []:
        try:
            value = int(index)
        except (TypeError, ValueError):
            continue
        if value < 0 or value in seen:
            continue
        seen.add(value)
        unique_indices.append(value)

    return {
        "chapter_id": (request.chapter_id or "").strip(),
        "selected_chapter_id": (request.chapter_id or "").strip(),
        "chapter_title": "",
        "scope_label": "选中片段",
        "regenerate_all": bool(request.regenerate_all),
        "selection_mode": "explicit_indices",
        "indices": unique_indices,
        "total_selected": len(unique_indices),
        "selected_count": len(unique_indices),
        "total_scoped": len(unique_indices),
        "non_empty": len(unique_indices),
        "non_empty_count": len(unique_indices),
        "skipped_empty": 0,
        "empty_skipped_count": 0,
        "done_count": 0,
        "audio_count": 0,
        "missing_audio_count": 0,
        "missing_audio_indices": [],
        "stale_done_count": 0,
        "stale_done_indices": [],
        "pending_count": 0,
        "pending_indices": [],
        "generating_count": 0,
        "generating_indices": [],
        "error_count": 0,
        "error_indices": [],
        "regenerate_count": len(unique_indices) if request.regenerate_all else 0,
        "voice_config_added": len(voice_defaults.get("added") or []),
        "voice_config_updated": len(voice_defaults.get("updated") or []),
    }


def _batch_render_plan(request: BatchGenerateRequest) -> dict[str, Any]:
    if request.indices:
        return _explicit_indices_render_plan(request.indices, request)
    return _render_plan(RenderPlanRequest(
        chapter_id=request.chapter_id,
        regenerate_all=request.regenerate_all,
    ))

@app.get("/api/status/{task_name}")
async def get_status(task_name: str):
    task_name = _resolve_process_task_name(task_name)
    if task_name not in process_state:
        raise HTTPException(status_code=404, detail="Task not found")
    return process_state[task_name]


@app.get("/api/events/{task_name}")
async def stream_task_events(task_name: str):
    task_name = _resolve_process_task_name(task_name)
    if task_name not in process_state:
        raise HTTPException(status_code=404, detail="Task not found")

    async def event_generator():
        seen = 0
        while True:
            logs = process_state[task_name].get("logs", [])
            while seen < len(logs):
                line = logs[seen]
                seen += 1
                if not line.startswith("[EVENT] "):
                    continue
                payload = line[len("[EVENT] "):]
                try:
                    event = json.loads(payload)
                    event_type = event.get("type") or "message"
                except (json.JSONDecodeError, ValueError):
                    event_type = "message"
                    payload = json.dumps({"type": "message", "data": {"line": line}}, ensure_ascii=False)
                yield f"event: {event_type}\ndata: {payload}\n\n"
            if not process_state[task_name].get("running"):
                yield 'event: stream_end\ndata: {"type":"stream_end","data":{}}\n\n'
                break
            await asyncio.sleep(0.25)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

@app.get("/api/voices")
async def get_voices(sort_by: Optional[str] = None, sort_order: Optional[str] = None):
    # Parse voices directly from current script and chunks so manual editor
    # speaker changes appear immediately without waiting for script regeneration.
    # Actual script speaker names stay separate; a missing alias config remains
    # missing instead of silently using the canonical character's voice.
    book_dir = _current_book_dir()
    voice_metadata = _voice_speaker_metadata(book_dir)
    voices_list = list(voice_metadata.keys())
    if voices_list:
        with open(VOICES_PATH, "w", encoding="utf-8") as f:
            json.dump(voices_list, f, indent=2, ensure_ascii=False)

    if not voices_list:
        return []

    # Combine with saved config only. Missing speakers stay unconfigured until
    # the user explicitly chooses a voice.
    character_book = _load_character_book(book_dir)
    voice_config = _ensure_voice_config_for_script(book_dir, write=False).get("voice_config") or {}
    stored_voice_names = _stored_voice_config_names(book_dir)

    result = []
    for voice_name in voices_list:
        config = voice_config.get(voice_name, {})
        metadata = voice_metadata.get(voice_name) or {}
        status = _voice_config_status_for_speaker(
            voice_name,
            config,
            character_book,
            raw_config_exists=voice_name in stored_voice_names,
        )
        result.append({
            "name": voice_name,
            "config": config,
            "metadata": metadata,
            "source": metadata.get("source") or "script",
            "aliases": metadata.get("aliases") or [],
            "raw_speakers": metadata.get("raw_speakers") or [],
            "inherited_speakers": metadata.get("inherited_speakers") or [],
            "voice_profile": metadata.get("voice_profile") or "",
            "voice_profile_source": metadata.get("voice_profile_source") or "",
            "line_count": metadata.get("line_count") or 0,
            "char_count": metadata.get("char_count") or 0,
            "is_narrator": bool(metadata.get("is_narrator")),
            "has_character_book": bool(metadata.get("has_character_book")),
            **status,
        })
    return _sort_speaker_rows(result, sort_by, sort_order)

@app.post("/api/parse_voices", include_in_schema=False)
async def parse_voices(background_tasks: BackgroundTasks):
    if process_state["voices"]["running"]:
         raise HTTPException(status_code=400, detail="Voice parsing already running")

    background_tasks.add_task(run_process, [sys.executable, "-u", "parse_voices.py"], "voices")
    return {"status": "started"}

@app.post("/api/save_voice_config")
async def save_voice_config(config_data: Dict[str, VoiceConfigItem]):
    book_dir = _current_book_dir()
    character_book = _load_character_book(book_dir)
    result = _save_voice_config_mapping(book_dir, config_data, character_book=character_book)
    invalidated = _invalidate_voice_audio(book_dir, result.get("changed_names", []), character_book=character_book)
    return {
        "status": "saved",
        "saved": result.get("saved", 0),
        "saved_names": result.get("saved_names", []),
        "changed": result.get("changed", 0),
        "changed_names": result.get("changed_names", []),
        "removed": result.get("removed", 0),
        "removed_names": result.get("removed_names", []),
        **invalidated,
        "voice_config": result.get("voice_config", {}),
        "total": result.get("total", 0),
    }

@app.get("/api/audiobook")
async def get_audiobook():
    audiobook_path = _current_audiobook_path()
    if not os.path.exists(audiobook_path):
        raise HTTPException(status_code=404, detail="Audiobook not found")
    book = _ensure_current_book()
    filename = f"{_sanitize_book_id(book['title'])}.mp3"
    return FileResponse(audiobook_path, filename=filename, media_type="audio/mpeg")


@app.post("/api/chapters/{chapter_id}/merge_audio")
async def merge_chapter_audio(chapter_id: str):
    if process_state["audio"]["running"]:
        raise HTTPException(status_code=400, detail="Audio generation already running")
    success, msg = project_manager.merge_chapter_audio(chapter_id)
    if not success:
        raise HTTPException(status_code=400, detail=msg)
    audio_url = f"/api/chapters/{quote(chapter_id, safe='')}/audiobook"
    return {
        "status": "ok",
        "audio_path": msg,
        "audio_url": audio_url,
        "filename": project_manager.chapter_audio_filename(chapter_id),
    }


@app.get("/api/chapters/{chapter_id}/audiobook")
async def get_chapter_audiobook(chapter_id: str):
    path = project_manager.chapter_audio_path(chapter_id)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Chapter audiobook not found. Merge it first.")
    return FileResponse(path, filename=project_manager.chapter_audio_filename(chapter_id), media_type="audio/mpeg")

# --- Chunk Management Endpoints ---

@app.get("/api/chunks")
async def get_chunks():
    chunks = project_manager.load_chunks()
    return chunks

def _voice_sync_meta(prefix: str = "_") -> dict[str, Any]:
    voice_defaults = _ensure_voice_config_for_script(_current_book_dir(), write=False)
    return {
        f"{prefix}voice_config_added": len(voice_defaults.get("added") or []),
        f"{prefix}voice_config_updated": len(voice_defaults.get("updated") or []),
        f"{prefix}voice_config_total": voice_defaults.get("total", 0),
    }

class ChunkRestoreRequest(BaseModel):
    chunk: dict
    at_index: int

@app.post("/api/chunks/restore")
async def restore_chunk(request: ChunkRestoreRequest):
    """Re-insert a previously deleted chunk at a specific index."""
    chunks = project_manager.restore_chunk(request.at_index, request.chunk)
    if chunks is None:
        raise HTTPException(status_code=400, detail="Failed to restore chunk")
    script_sync = _sync_current_script_from_chunks()
    return {
        "status": "ok",
        "total": len(chunks),
        "script_entry_count": script_sync["entry_count"],
        **_voice_sync_meta(),
    }

@app.post("/api/chunks/{index}")
async def update_chunk(index: int, update: ChunkUpdate):
    data = update.model_dump(exclude_unset=True)
    logger.info(f"Updating chunk {index} with data: {data}")
    before_chunks = project_manager.load_chunks()
    old_audio_path = (
        before_chunks[index].get("audio_path")
        if 0 <= index < len(before_chunks) and any(field in data for field in ("text", "instruct", "speaker"))
        else None
    )
    chunk = project_manager.update_chunk(index, data)
    if not chunk:
        raise HTTPException(status_code=404, detail="Chunk not found")
    if old_audio_path:
        _remove_unreferenced_audio_files(_current_book_dir(), [old_audio_path], project_manager.load_chunks())
    logger.info(f"Chunk {index} updated, instruct is now: '{chunk.get('instruct', '')}'")
    if any(field in data for field in ("text", "instruct", "speaker", "pause_after")):
        script_sync = _sync_current_script_from_chunks()
        chunk = {**chunk, "_script_entry_count": script_sync["entry_count"]}
    if "speaker" in data:
        chunk = {**chunk, **_voice_sync_meta()}
    return chunk

@app.post("/api/chunks/{index}/insert")
async def insert_chunk(index: int):
    """Insert an empty chunk after the given index."""
    chunks = project_manager.insert_chunk(index)
    if chunks is None:
        raise HTTPException(status_code=404, detail="Invalid chunk index")
    script_sync = _sync_current_script_from_chunks()
    return {
        "status": "ok",
        "total": len(chunks),
        "script_entry_count": script_sync["entry_count"],
        **_voice_sync_meta(),
    }

@app.delete("/api/chunks/{index}")
async def delete_chunk(index: int):
    """Delete a chunk at the given index."""
    result = project_manager.delete_chunk(index)
    if result is None:
        raise HTTPException(status_code=400, detail="Cannot delete chunk (invalid index or last remaining chunk)")
    deleted, chunks = result
    script_sync = _sync_current_script_from_chunks()
    return {"status": "ok", "deleted": deleted, "total": len(chunks), "script_entry_count": script_sync["entry_count"]}

@app.post("/api/chunks/{index}/generate")
async def generate_chunk_endpoint(index: int, background_tasks: BackgroundTasks):
    manager = project_manager
    chunks = manager.load_chunks()
    if not (0 <= index < len(chunks)):
        raise HTTPException(status_code=404, detail="Invalid chunk index")
    if not chunks[index].get("text", "").strip():
        raise HTTPException(status_code=400, detail="Cannot generate audio for an empty line")
    _ensure_voice_config_for_script(write=False)

    def task():
        manager.generate_chunk_audio(index)

    background_tasks.add_task(task)
    return {"status": "started"}

@app.post("/api/merge")
async def merge_audio_endpoint(background_tasks: BackgroundTasks):
    # Reuse audio process state for merge if possible, or just background it
    # For simplicity, we just background it and frontend will assume it works
    # Or we can link it to process_state["audio"]

    if process_state["audio"]["running"]:
        raise HTTPException(status_code=400, detail="Audio generation already running")
    manager = project_manager
    process_state["audio"]["running"] = True
    process_state["audio"]["logs"] = ["Starting merge..."]

    def task():
        try:
            success, msg = manager.merge_audio()
            if success:
                process_state["audio"]["logs"].append(f"Merge complete: {msg}")
            else:
                process_state["audio"]["logs"].append(f"Merge failed: {msg}")
        except Exception as e:
            process_state["audio"]["logs"].append(f"Merge error: {e}")
        finally:
            process_state["audio"]["running"] = False

    background_tasks.add_task(task)
    return {"status": "started"}

@app.post("/api/export_audacity")
async def export_audacity_endpoint(background_tasks: BackgroundTasks):
    if process_state["audacity_export"]["running"]:
        raise HTTPException(status_code=400, detail="Audacity export already running")
    manager = project_manager
    process_state["audacity_export"]["running"] = True
    process_state["audacity_export"]["logs"] = ["Starting Audacity export..."]

    def task():
        try:
            success, msg = manager.export_audacity()
            if success:
                process_state["audacity_export"]["logs"].append(f"Export complete: {msg}")
            else:
                process_state["audacity_export"]["logs"].append(f"Export failed: {msg}")
        except Exception as e:
            process_state["audacity_export"]["logs"].append(f"Export error: {e}")
        finally:
            process_state["audacity_export"]["running"] = False

    background_tasks.add_task(task)
    return {"status": "started"}

@app.get("/api/export_audacity")
async def get_audacity_export():
    zip_path = os.path.join(_current_book_dir(), "audacity_export.zip")
    if not os.path.exists(zip_path):
        raise HTTPException(status_code=404, detail="Audacity export not found. Generate it first.")
    book = _ensure_current_book()
    return FileResponse(zip_path, filename=f"{_sanitize_book_id(book['title'])}_audacity_export.zip", media_type="application/zip")

class M4bExportRequest(BaseModel):
    per_chunk_chapters: bool = False
    title: str = ""
    author: str = ""
    narrator: str = ""
    year: str = ""
    description: str = ""

@app.post("/api/merge_m4b")
async def merge_m4b_endpoint(request: M4bExportRequest, background_tasks: BackgroundTasks):
    if process_state["m4b_export"]["running"]:
        raise HTTPException(status_code=400, detail="M4B export already running")
    manager = project_manager
    cover_path = _current_cover_path()
    process_state["m4b_export"]["running"] = True
    process_state["m4b_export"]["logs"] = ["Starting M4B export..."]

    def task():
        try:
            meta = {
                "title": request.title,
                "author": request.author,
                "narrator": request.narrator,
                "year": request.year,
                "description": request.description,
                "cover_path": cover_path if os.path.exists(cover_path) else "",
            }
            success, msg = manager.merge_m4b(per_chunk_chapters=request.per_chunk_chapters, metadata=meta)
            if success:
                process_state["m4b_export"]["logs"].append(f"Export complete: {msg}")
            else:
                process_state["m4b_export"]["logs"].append(f"Export failed: {msg}")
        except Exception as e:
            process_state["m4b_export"]["logs"].append(f"Export error: {e}")
        finally:
            process_state["m4b_export"]["running"] = False

    background_tasks.add_task(task)
    return {"status": "started"}

@app.get("/api/audiobook_m4b")
async def get_audiobook_m4b():
    m4b_path = _current_m4b_path()
    if not os.path.exists(m4b_path):
        raise HTTPException(status_code=404, detail="M4B audiobook not found. Export it first.")
    book = _ensure_current_book()
    return FileResponse(m4b_path, filename=f"{_sanitize_book_id(book['title'])}.m4b", media_type="audio/mp4")

@app.post("/api/m4b_cover")
async def upload_m4b_cover(file: UploadFile = File(...)):
    """Upload a cover image for M4B export."""
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="File must be an image")
    cover_path = _current_cover_path()
    content = await file.read()
    with open(cover_path, "wb") as f:
        f.write(content)
    return {"status": "uploaded", "path": cover_path}

@app.delete("/api/m4b_cover")
async def delete_m4b_cover():
    """Remove the uploaded cover image."""
    cover_path = _current_cover_path()
    if os.path.exists(cover_path):
        os.remove(cover_path)
    return {"status": "removed"}

@app.post("/api/render_plan")
async def get_render_plan(request: RenderPlanRequest):
    """Return the exact chunk indices that would be rendered for a scope."""
    return _render_plan(request)

@app.post("/api/generate_batch")
async def generate_batch_endpoint(request: BatchGenerateRequest, background_tasks: BackgroundTasks):
    """Generate multiple chunks in parallel using configured worker count."""
    plan = _batch_render_plan(request)
    indices = plan.get("indices") or []

    # Load worker count from config
    workers = 2
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                workers = max(1, cfg.get("tts", {}).get("parallel_workers", 2))
        except (json.JSONDecodeError, ValueError):
            pass

    if request.dry_run:
        return {
            "status": "dry_run",
            "workers": workers,
            **plan,
        }
    if process_state["audio"]["running"]:
        raise HTTPException(status_code=400, detail="Audio generation already running")
    if not indices:
        raise HTTPException(status_code=400, detail="No renderable non-empty chunks found for this scope.")

    total = len(indices)
    manager = project_manager
    process_state["audio"]["running"] = True
    process_state["audio"]["cancel"] = False
    process_state["audio"]["logs"] = [
        f"Starting parallel generation of {total} chunks with {workers} workers..."
    ]
    if plan.get("voice_config_added") or plan.get("voice_config_updated"):
        process_state["audio"]["logs"].append(
            f"Voice config synchronized: {plan.get('voice_config_added') or 0} added, "
            f"{plan.get('voice_config_updated') or 0} updated"
        )

    def progress_callback(completed, failed, total):
        """Update logs with progress."""
        process_state["audio"]["logs"].append(
            f"Progress: {completed + failed}/{total} ({completed} done, {failed} failed)"
        )

    def cancel_check():
        return process_state["audio"]["cancel"]

    def task():
        try:
            results = manager.generate_chunks_parallel(
                indices, workers, progress_callback, cancel_check=cancel_check
            )
            completed = len(results["completed"])
            failed = len(results["failed"])
            cancelled = results.get("cancelled", 0)
            msg = f"Batch generation complete: {completed} succeeded, {failed} failed"
            if cancelled:
                msg += f", {cancelled} cancelled"
            process_state["audio"]["logs"].append(msg)
            if results["failed"]:
                for idx, err in results["failed"]:
                    process_state["audio"]["logs"].append(f"  Chunk {idx} failed: {err}")
        except Exception as e:
            logger.error(f"Batch generation error: {e}")
            process_state["audio"]["logs"].append(f"Batch generation error: {e}")
        finally:
            process_state["audio"]["running"] = False
            process_state["audio"]["cancel"] = False

    background_tasks.add_task(task)
    return {
        "status": "started",
        "workers": workers,
        "total_chunks": total,
        **plan,
    }

@app.post("/api/generate_batch_fast")
async def generate_batch_fast_endpoint(request: BatchGenerateRequest, background_tasks: BackgroundTasks):
    """Generate multiple chunks using batch TTS API with single seed. Faster but less flexible.
    Requires custom Qwen3-TTS with /generate_batch endpoint."""
    plan = _batch_render_plan(request)
    indices = plan.get("indices") or []

    # Load batch_seed and batch_size from config
    batch_seed = -1
    batch_size = 4
    batch_group_by_type = False
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                tts_cfg = cfg.get("tts", {})
                seed_val = tts_cfg.get("batch_seed")
                if seed_val is not None and seed_val != "":
                    batch_seed = int(seed_val)
                batch_size = max(1, tts_cfg.get("parallel_workers", 4))
                batch_group_by_type = tts_cfg.get("batch_group_by_type", False)
        except (json.JSONDecodeError, ValueError):
            pass

    if request.dry_run:
        return {
            "status": "dry_run",
            "batch_seed": batch_seed,
            "batch_size": batch_size,
            "batch_group_by_type": batch_group_by_type,
            **plan,
        }
    if process_state["audio"]["running"]:
        raise HTTPException(status_code=400, detail="Audio generation already running")
    if not indices:
        raise HTTPException(status_code=400, detail="No renderable non-empty chunks found for this scope.")

    total = len(indices)
    manager = project_manager
    process_state["audio"]["running"] = True
    process_state["audio"]["cancel"] = False
    process_state["audio"]["logs"] = [
        f"Starting batch generation of {total} chunks (batch_size={batch_size}, seed={batch_seed})..."
    ]
    if plan.get("voice_config_added") or plan.get("voice_config_updated"):
        process_state["audio"]["logs"].append(
            f"Voice config synchronized: {plan.get('voice_config_added') or 0} added, "
            f"{plan.get('voice_config_updated') or 0} updated"
        )

    def progress_callback(completed, failed, total):
        process_state["audio"]["logs"].append(
            f"Progress: {completed + failed}/{total} ({completed} done, {failed} failed)"
        )

    def cancel_check():
        return process_state["audio"]["cancel"]

    def task():
        try:
            results = manager.generate_chunks_batch(
                indices, batch_seed, batch_size, progress_callback,
                batch_group_by_type=batch_group_by_type,
                cancel_check=cancel_check,
            )
            completed = len(results["completed"])
            failed = len(results["failed"])
            cancelled = results.get("cancelled", 0)
            msg = f"Batch generation complete: {completed} succeeded, {failed} failed"
            if cancelled:
                msg += f", {cancelled} cancelled"
            process_state["audio"]["logs"].append(msg)
            if results["failed"]:
                for idx, err in results["failed"]:
                    process_state["audio"]["logs"].append(f"  Chunk {idx} failed: {err}")
        except Exception as e:
            logger.error(f"Batch generation error: {e}")
            process_state["audio"]["logs"].append(f"Batch generation error: {e}")
        finally:
            process_state["audio"]["running"] = False
            process_state["audio"]["cancel"] = False

    background_tasks.add_task(task)
    return {
        "status": "started",
        "batch_seed": batch_seed,
        "batch_size": batch_size,
        "total_chunks": total,
        **plan,
    }

@app.post("/api/cancel_audio")
async def cancel_audio():
    """Cancel ongoing audio generation and reset in-progress chunks."""
    if process_state["audio"]["running"]:
        process_state["audio"]["cancel"] = True
        process_state["audio"]["logs"].append("[CANCEL] Cancellation requested")
        return {"status": "cancelling"}
    # Not running — still reset any stuck "generating" chunks (e.g. from a crash)
    chunks = project_manager.load_chunks()
    if chunks:
        reset_count = 0
        for chunk in chunks:
            if chunk.get("status") == "generating":
                chunk["status"] = "pending"
                reset_count += 1
        if reset_count:
            project_manager.save_chunks(chunks)
    return {"status": "not_running", "reset_chunks": reset_count if chunks else 0}

## ── Saved Scripts ──────────────────────────────────────────────

SAVED_SCRIPT_COMPANION_SUFFIXES = (
    ".voice_config.json",
    ".chunks.json",
    ".character_book.json",
    ".character_analysis_state.json",
    ".chapter_memory.json",
    ".script_issues.json",
    ".chapters.json",
    ".meta.json",
)
SAVED_CHUNK_STATUSES = {"pending", "generating", "done", "error"}

def _sanitize_name(name: str) -> str:
    """Make a string safe for use as a filename."""
    name = re.sub(r'[^\w\- ]', '', name).strip()
    name = re.sub(r'\s+', '_', name)
    return name.lower()

def _saved_script_paths(name: str) -> dict[str, str]:
    safe_name = _sanitize_name(name)
    return {
        "name": safe_name,
        "script": os.path.join(SCRIPTS_DIR, f"{safe_name}.json"),
        "voice_config": os.path.join(SCRIPTS_DIR, f"{safe_name}.voice_config.json"),
        "chunks": os.path.join(SCRIPTS_DIR, f"{safe_name}.chunks.json"),
        "character_book": os.path.join(SCRIPTS_DIR, f"{safe_name}.character_book.json"),
        "character_analysis_state": os.path.join(SCRIPTS_DIR, f"{safe_name}.character_analysis_state.json"),
        "chapter_memory": os.path.join(SCRIPTS_DIR, f"{safe_name}.chapter_memory.json"),
        "script_issues": os.path.join(SCRIPTS_DIR, f"{safe_name}.script_issues.json"),
        "chapters": os.path.join(SCRIPTS_DIR, f"{safe_name}.chapters.json"),
        "meta": os.path.join(SCRIPTS_DIR, f"{safe_name}.meta.json"),
    }

def _read_json_payload(path: str, fallback: Any = None) -> Any:
    if not os.path.exists(path):
        return fallback
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, ValueError):
        return fallback

def _count_json_list(path: str) -> int:
    data = _read_json_payload(path, [])
    return len(data) if isinstance(data, list) else 0

def _saved_script_list_item(name: str, script_path: str) -> dict[str, Any]:
    paths = _saved_script_paths(name)
    meta = _read_json_payload(paths["meta"], {})
    if not isinstance(meta, dict):
        meta = {}
    has_voice_config = os.path.exists(paths["voice_config"])
    has_chunks = os.path.exists(paths["chunks"])
    has_character_book = os.path.exists(paths["character_book"])
    has_character_analysis_state = os.path.exists(paths["character_analysis_state"])
    has_chapter_memory = os.path.exists(paths["chapter_memory"])
    has_script_issues = os.path.exists(paths["script_issues"])
    has_chapters = os.path.exists(paths["chapters"])
    entry_count = meta.get("entry_count")
    chunk_count = meta.get("chunk_count")
    chapter_count = meta.get("chapter_count")
    if not isinstance(entry_count, int):
        entry_count = _count_json_list(script_path)
    if not isinstance(chunk_count, int):
        chunk_count = _count_json_list(paths["chunks"]) if has_chunks else 0
    if not isinstance(chapter_count, int):
        chapter_snapshot = _read_json_payload(paths["chapters"], {}) if has_chapters else {}
        chapter_count = len(chapter_snapshot.get("chapters") or []) if isinstance(chapter_snapshot, dict) else 0
    return {
        "name": name,
        "created": os.path.getmtime(script_path),
        "saved_at": meta.get("saved_at") or "",
        "source_book_id": meta.get("book_id") or "",
        "source_book_title": meta.get("book_title") or "",
        "entry_count": entry_count,
        "chunk_count": chunk_count,
        "chapter_count": chapter_count,
        "has_voice_config": has_voice_config,
        "has_chunks": has_chunks,
        "has_character_book": has_character_book,
        "has_character_analysis_state": has_character_analysis_state,
        "has_chapter_memory": has_chapter_memory,
        "has_script_issues": has_script_issues,
        "has_chapters": has_chapters,
    }

def _saved_chunk_audio_exists(book_dir: str, audio_path: Optional[str]) -> bool:
    if not audio_path or os.path.isabs(str(audio_path)):
        return False
    return _audio_file_exists(book_dir, str(audio_path))

def _sanitize_saved_chunks(chunks: Any, book_dir: str) -> tuple[list[dict], int, int]:
    if not isinstance(chunks, list):
        return [], 0, 0
    restored = []
    reset_audio_count = 0
    reset_status_count = 0
    for raw_chunk in chunks:
        if not isinstance(raw_chunk, dict):
            continue
        chunk = dict(raw_chunk)
        chunk["id"] = len(restored)
        status = str(chunk.get("status") or "pending").strip().lower()
        if status not in SAVED_CHUNK_STATUSES:
            status = "pending"
            reset_status_count += 1

        audio_path = chunk.get("audio_path")
        audio_path = str(audio_path).strip() if audio_path is not None else ""
        audio_path = audio_path or None
        audio_exists = _saved_chunk_audio_exists(book_dir, audio_path)

        if status == "generating":
            status = "pending"
            audio_path = None
            reset_status_count += 1
        elif status == "done" and not audio_exists:
            status = "pending"
            audio_path = None
            reset_audio_count += 1
        elif audio_path and not audio_exists:
            audio_path = None
            reset_audio_count += 1

        chunk["status"] = status
        chunk["audio_path"] = audio_path
        restored.append(chunk)
    return restored, reset_audio_count, reset_status_count

@app.get("/api/scripts")
async def list_saved_scripts():
    """List all saved scripts in the scripts/ directory."""
    scripts = []
    for f in os.listdir(SCRIPTS_DIR):
        if f.endswith(".json") and not f.endswith(SAVED_SCRIPT_COMPANION_SUFFIXES):
            name = f[:-5]  # strip .json
            filepath = os.path.join(SCRIPTS_DIR, f)
            scripts.append(_saved_script_list_item(name, filepath))
    scripts.sort(key=lambda x: x["created"], reverse=True)
    return scripts

class ScriptSaveRequest(BaseModel):
    name: str

@app.post("/api/scripts/save")
async def save_script(request: ScriptSaveRequest):
    """Save the current annotated script, chunks, and voice config under a reusable package name."""
    _current_script_entries_for_export()
    script_path = _current_script_path()
    if not os.path.exists(script_path):
        raise HTTPException(status_code=404, detail="No annotated script to save. Generate a script first.")

    paths = _saved_script_paths(request.name)
    safe_name = paths["name"]
    if not safe_name:
        raise HTTPException(status_code=400, detail="Invalid script name.")

    shutil.copyfile(script_path, paths["script"])

    voice_config_path = _current_voice_config_path()
    if os.path.exists(voice_config_path):
        shutil.copyfile(voice_config_path, paths["voice_config"])
    elif os.path.exists(paths["voice_config"]):
        os.remove(paths["voice_config"])

    chunks_path = _current_chunks_path()
    if os.path.exists(chunks_path):
        shutil.copyfile(chunks_path, paths["chunks"])
    elif os.path.exists(paths["chunks"]):
        os.remove(paths["chunks"])

    character_book_path = os.path.join(_current_book_dir(), "character_book.json")
    if os.path.exists(character_book_path):
        shutil.copyfile(character_book_path, paths["character_book"])
    elif os.path.exists(paths["character_book"]):
        os.remove(paths["character_book"])

    character_analysis_state_path = _current_character_analysis_state_path()
    if os.path.exists(character_analysis_state_path):
        shutil.copyfile(character_analysis_state_path, paths["character_analysis_state"])
    elif os.path.exists(paths["character_analysis_state"]):
        os.remove(paths["character_analysis_state"])

    chapter_memory_path = _current_chapter_memory_path()
    if os.path.exists(chapter_memory_path):
        shutil.copyfile(chapter_memory_path, paths["chapter_memory"])
    elif os.path.exists(paths["chapter_memory"]):
        os.remove(paths["chapter_memory"])

    script_issues_path = _current_script_issues_path()
    if os.path.exists(script_issues_path):
        shutil.copyfile(script_issues_path, paths["script_issues"])
    elif os.path.exists(paths["script_issues"]):
        os.remove(paths["script_issues"])

    book = _ensure_current_book()
    manifest = load_current_chapters_manifest()
    if manifest.get("chapters"):
        _write_json(paths["chapters"], _chapter_snapshot_from_manifest(manifest))
    elif os.path.exists(paths["chapters"]):
        os.remove(paths["chapters"])

    meta = {
        "name": safe_name,
        "saved_at": _now_iso(),
        "book_id": book.get("id") or "",
        "book_title": book.get("title") or "",
        "entry_count": _count_json_list(script_path),
        "chunk_count": _count_json_list(chunks_path) if os.path.exists(chunks_path) else 0,
        "chapter_count": manifest.get("chapter_count") or len(manifest.get("chapters") or []),
        "has_voice_config": os.path.exists(paths["voice_config"]),
        "has_chunks": os.path.exists(paths["chunks"]),
        "has_character_book": os.path.exists(paths["character_book"]),
        "has_character_analysis_state": os.path.exists(paths["character_analysis_state"]),
        "has_chapter_memory": os.path.exists(paths["chapter_memory"]),
        "has_script_issues": os.path.exists(paths["script_issues"]),
        "has_chapters": os.path.exists(paths["chapters"]),
    }
    _write_json(paths["meta"], meta)

    logger.info(f"Script saved as '{safe_name}'")
    return {"status": "saved", "name": safe_name, **meta}

class ScriptLoadRequest(BaseModel):
    name: str

@app.post("/api/scripts/load")
async def load_script(request: ScriptLoadRequest):
    """Load a saved script package, replacing the current script and restoring chunks when available."""
    if process_state["audio"]["running"] or process_state["script"]["running"] or process_state["review"]["running"]:
        raise HTTPException(status_code=409, detail="Cannot load a script while generation or review is running.")

    paths = _saved_script_paths(request.name)
    safe_name = paths["name"]
    if not safe_name:
        raise HTTPException(status_code=400, detail="Invalid script name.")
    if not os.path.exists(paths["script"]):
        raise HTTPException(status_code=404, detail=f"Saved script '{request.name}' not found.")

    shutil.copyfile(paths["script"], _current_script_path())

    voice_config_loaded = False
    if os.path.exists(paths["voice_config"]):
        shutil.copyfile(paths["voice_config"], _current_voice_config_path())
        voice_config_loaded = True
    else:
        _clear_voice_config(_current_book_dir())

    character_book_loaded = False
    normalization = {
        "script_speaker_updates": 0,
        "chunk_speaker_updates": 0,
        "voice_config_updates": 0,
    }
    if os.path.exists(paths["character_book"]):
        character_book = _normalize_character_book(_read_json_payload(paths["character_book"], _default_character_book()))
        _save_character_book(_current_book_dir(), character_book)
        character_book_loaded = True
    else:
        character_book = _clear_character_book(_current_book_dir())

    character_analysis_state_loaded = False
    if os.path.exists(paths["character_analysis_state"]):
        character_analysis_state = _read_json_payload(paths["character_analysis_state"], {"chapters": {}})
        _save_character_analysis_state(character_analysis_state if isinstance(character_analysis_state, dict) else {"chapters": {}})
        character_analysis_state_loaded = True
    else:
        _save_character_analysis_state({"chapters": {}})

    chapter_memory_loaded = False
    if os.path.exists(paths["chapter_memory"]):
        chapter_memory = _read_json_payload(paths["chapter_memory"], {"chapters": {}})
        _save_chapter_memory(chapter_memory if isinstance(chapter_memory, dict) else {"chapters": {}})
        chapter_memory_loaded = True
    else:
        _save_chapter_memory({"chapters": {}})

    script_issues_loaded = False
    script_issues_revalidated = False
    if os.path.exists(paths["script_issues"]):
        script_issues = _read_json_payload(paths["script_issues"], {"chapters": {}})
        _save_script_issues(script_issues if isinstance(script_issues, dict) else {"chapters": {}})
        script_issues_loaded = True

    chapters_loaded = False
    chapter_count = 0
    source_filename = None
    if os.path.exists(paths["chapters"]):
        chapter_records, generated_at = _chapter_records_from_snapshot(_read_json_payload(paths["chapters"], {}))
        if chapter_records:
            chapters_dir = _current_chapters_dir()
            if os.path.exists(chapters_dir):
                shutil.rmtree(chapters_dir)
            chapter_manifest = _write_chapter_records(chapter_records, generated_at=generated_at or None)
            source_filename = _write_source_from_chapter_records(chapter_records, safe_name, chapter_manifest)
            chapters_loaded = True
            chapter_count = chapter_manifest.get("chapter_count", 0)
        else:
            logger.warning("Saved script '%s' has an invalid empty chapter snapshot", safe_name)

    chunks_restored = False
    chunk_count = 0
    reset_audio_count = 0
    reset_status_count = 0
    chunks_path = _current_chunks_path()
    if os.path.exists(paths["chunks"]):
        restored_chunks, reset_audio_count, reset_status_count = _sanitize_saved_chunks(
            _read_json_payload(paths["chunks"], []),
            _current_book_dir(),
        )
        _write_json(chunks_path, restored_chunks)
        chunks_restored = True
        chunk_count = len(restored_chunks)
    elif os.path.exists(chunks_path):
        # Legacy saved scripts have no chunk package; let ProjectManager rebuild chunks from script.
        os.remove(chunks_path)

    if character_book_loaded:
        normalization = _normalize_script_speakers_for_book(_current_book_dir(), character_book)

    assigned_chapter_entries = 0
    loaded_entries = _load_script_entries(_current_script_path())
    loaded_entries, assigned_chapter_entries = _assign_entries_to_current_chapters(loaded_entries)
    if assigned_chapter_entries:
        _write_json(_current_script_path(), loaded_entries)
        if chunks_restored:
            restored_chunks, chunk_chapter_updates = _assign_entries_to_current_chapters(project_manager.load_chunks())
            if chunk_chapter_updates:
                _write_json(_current_chunks_path(), restored_chunks)
                chunk_count = len(restored_chunks)
        elif os.path.exists(chunks_path):
            os.remove(chunks_path)

    if not script_issues_loaded:
        _revalidate_current_script_issues(_load_character_book(_current_book_dir()))
        script_issues_revalidated = True

    _sync_script_generation_state_from_entries(
        loaded_entries,
        source="script_package",
    )
    voice_defaults = _ensure_voice_config_for_script(_current_book_dir(), write=False)
    voice_config_added = len(voice_defaults.get("added") or [])
    voice_config_updated = len(voice_defaults.get("updated") or [])

    book = _ensure_current_book()
    touch_fields = {}
    if chapters_loaded:
        touch_fields = {
            "chapter_count": chapter_count,
            "char_count": load_current_chapters_manifest().get("total_chars", 0),
        }
        if source_filename:
            touch_fields["source_filename"] = source_filename
    _touch_book(book["id"], **touch_fields)

    logger.info(f"Script '{safe_name}' loaded")
    return {
        "status": "loaded",
        "name": safe_name,
        "voice_config_loaded": voice_config_loaded,
        "character_book_loaded": character_book_loaded,
        "voice_config_cleared": not voice_config_loaded and not (voice_config_added or voice_config_updated),
        "voice_config_added": voice_config_added,
        "voice_config_updated": voice_config_updated,
        "voice_config_total": voice_defaults.get("total", 0),
        "character_book_cleared": not character_book_loaded,
        "character_analysis_state_loaded": character_analysis_state_loaded,
        "character_analysis_state_cleared": not character_analysis_state_loaded,
        "chapter_memory_loaded": chapter_memory_loaded,
        "script_issues_loaded": script_issues_loaded,
        "chapter_memory_cleared": not chapter_memory_loaded,
        "script_issues_revalidated": script_issues_revalidated,
        "chapters_loaded": chapters_loaded,
        "chapter_count": chapter_count,
        "chunks_restored": chunks_restored,
        "chunk_count": chunk_count,
        "assigned_chapter_entries": assigned_chapter_entries,
        "reset_audio_count": reset_audio_count,
        "reset_status_count": reset_status_count,
        **normalization,
    }

@app.delete("/api/scripts/{name}")
async def delete_script(name: str):
    """Delete a saved script."""
    paths = _saved_script_paths(name)
    safe_name = paths["name"]
    if not safe_name:
        raise HTTPException(status_code=400, detail="Invalid script name.")
    if not os.path.exists(paths["script"]):
        raise HTTPException(status_code=404, detail=f"Saved script '{name}' not found.")

    for path in (
        paths["script"],
        paths["voice_config"],
        paths["chunks"],
        paths["character_book"],
        paths["character_analysis_state"],
        paths["chapter_memory"],
        paths["script_issues"],
        paths["chapters"],
        paths["meta"],
    ):
        if os.path.exists(path):
            os.remove(path)

    logger.info(f"Script '{safe_name}' deleted")
    return {"status": "deleted", "name": safe_name}

## ── Voice Designer ──────────────────────────────────────────────

DESIGNED_VOICES_MANIFEST = os.path.join(DESIGNED_VOICES_DIR, "manifest.json")

def _load_manifest(path):
    """Load a JSON manifest file, returning [] on missing or corrupt file."""
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, ValueError):
            pass
    return []

def _save_manifest(path, manifest):
    """Write a JSON manifest file."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

@app.post("/api/voice/preview")
async def voice_preview(request: VoicePreviewRequest):
    """Generate a TTS preview for any voice type."""
    engine = project_manager.get_engine()
    if not engine:
        raise HTTPException(status_code=500, detail="TTS engine not initialized")

    previews_dir = os.path.join(VOICELINES_DIR, "previews")
    os.makedirs(previews_dir, exist_ok=True)
    filename = f"preview_{int(time.time() * 1000)}.wav"
    output_path = os.path.join(previews_dir, filename)

    config = {request.voice_name: request.voice_config}
    instruct = request.voice_config.get("character_style", "") or request.voice_config.get("default_style", "")

    try:
        success = engine.generate_voice(
            text=request.text,
            instruct_text=instruct,
            speaker=request.voice_name,
            voice_config=config,
            output_path=output_path,
        )
        if not success:
            raise HTTPException(status_code=500, detail="TTS generation returned no audio")
        return {"status": "ok", "audio_url": f"/voicelines/previews/{filename}"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Voice preview failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/voice_design/preview")
async def voice_design_preview(request: VoiceDesignPreviewRequest):
    """Generate a preview voice from a text description."""
    engine = project_manager.get_engine()
    if not engine:
        raise HTTPException(status_code=500, detail="Failed to initialize TTS engine")

    try:
        wav_path, sr = engine.generate_voice_design(
            description=request.description,
            sample_text=request.sample_text,
            language=request.language,
        )
        # Return relative URL for the static mount
        filename = os.path.basename(wav_path)
        return {"status": "ok", "audio_url": f"/designed_voices/previews/{filename}"}
    except Exception as e:
        logger.error(f"Voice design preview failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/voice_design/save")
async def voice_design_save(request: VoiceDesignSaveRequest):
    """Save a preview voice as a permanent designed voice."""
    previews_dir = os.path.join(DESIGNED_VOICES_DIR, "previews")
    preview_path = os.path.join(previews_dir, request.preview_file)

    if not os.path.exists(preview_path):
        raise HTTPException(status_code=404, detail="Preview file not found")

    safe_name = _sanitize_name(request.name)
    if not safe_name:
        raise HTTPException(status_code=400, detail="Invalid voice name")

    # Generate unique ID
    voice_id = f"{safe_name}_{int(time.time())}"
    dest_filename = f"{voice_id}.wav"
    dest_path = os.path.join(DESIGNED_VOICES_DIR, dest_filename)

    shutil.copy2(preview_path, dest_path)

    # Update manifest
    manifest = _load_manifest(DESIGNED_VOICES_MANIFEST)
    manifest.append({
        "id": voice_id,
        "name": request.name,
        "description": request.description,
        "sample_text": request.sample_text,
        "filename": dest_filename,
    })
    _save_manifest(DESIGNED_VOICES_MANIFEST, manifest)

    logger.info(f"Designed voice saved: '{request.name}' as {dest_filename}")
    return {"status": "saved", "voice_id": voice_id}

@app.get("/api/voice_design/list")
async def voice_design_list():
    """List all saved designed voices."""
    return _load_manifest(DESIGNED_VOICES_MANIFEST)

@app.delete("/api/voice_design/{voice_id}")
async def voice_design_delete(voice_id: str):
    """Delete a saved designed voice."""
    manifest = _load_manifest(DESIGNED_VOICES_MANIFEST)
    entry = next((v for v in manifest if v["id"] == voice_id), None)
    if not entry:
        raise HTTPException(status_code=404, detail="Voice not found")

    # Delete WAV file
    wav_path = os.path.join(DESIGNED_VOICES_DIR, entry["filename"])
    if os.path.exists(wav_path):
        os.remove(wav_path)

    # Remove from manifest
    manifest = [v for v in manifest if v["id"] != voice_id]
    _save_manifest(DESIGNED_VOICES_MANIFEST, manifest)

    logger.info(f"Designed voice deleted: {voice_id}")
    return {"status": "deleted", "voice_id": voice_id}

## ── Clone Voice Uploads ───────────────────────────────────────

CLONE_VOICES_MANIFEST = os.path.join(CLONE_VOICES_DIR, "manifest.json")
ALLOWED_AUDIO_EXTS = {".wav", ".mp3", ".flac", ".ogg"}

@app.get("/api/edge_voices")
async def edge_voices_list():
    """List available Edge TTS voices, grouped by language and gender."""
    try:
        import edge_tts
        voices = await edge_tts.list_voices()
        result = []
        for v in sorted(voices, key=lambda x: x.get("ShortName", "")):
            locale = v.get("Locale", "")
            if not locale.startswith(("zh-", "en-", "ja-", "ko-")):
                continue
            result.append({
                "id": v.get("ShortName", ""),
                "name": v.get("ShortName", "").split("-")[-1].replace("Neural", ""),
                "locale": locale,
                "gender": v.get("Gender", "").lower(),
            })
        return result
    except ImportError:
        raise HTTPException(status_code=501, detail="edge-tts not installed")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/clone_voices/list")
async def clone_voices_list():
    """List all uploaded clone voices."""
    return _load_manifest(CLONE_VOICES_MANIFEST)

@app.post("/api/clone_voices/upload")
async def clone_voices_upload(file: UploadFile = File(...)):
    """Upload an audio file for voice cloning."""
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in ALLOWED_AUDIO_EXTS:
        raise HTTPException(status_code=400, detail=f"Unsupported format. Use: {', '.join(ALLOWED_AUDIO_EXTS)}")

    base_name = os.path.splitext(file.filename)[0]
    safe_name = _sanitize_name(base_name)
    if not safe_name:
        raise HTTPException(status_code=400, detail="Invalid filename")

    voice_id = f"{safe_name}_{int(time.time())}"
    dest_filename = f"{voice_id}{ext}"
    dest_path = os.path.join(CLONE_VOICES_DIR, dest_filename)

    async with aiofiles.open(dest_path, "wb") as out_file:
        content = await file.read()
        await out_file.write(content)

    manifest = _load_manifest(CLONE_VOICES_MANIFEST)
    manifest.append({
        "id": voice_id,
        "name": base_name,
        "filename": dest_filename,
    })
    _save_manifest(CLONE_VOICES_MANIFEST, manifest)

    logger.info(f"Clone voice uploaded: '{base_name}' as {dest_filename}")
    return {"status": "uploaded", "voice_id": voice_id, "filename": dest_filename}

@app.delete("/api/clone_voices/{voice_id}")
async def clone_voices_delete(voice_id: str):
    """Delete an uploaded clone voice."""
    manifest = _load_manifest(CLONE_VOICES_MANIFEST)
    entry = next((v for v in manifest if v["id"] == voice_id), None)
    if not entry:
        raise HTTPException(status_code=404, detail="Clone voice not found")

    wav_path = os.path.join(CLONE_VOICES_DIR, entry["filename"])
    if os.path.exists(wav_path):
        os.remove(wav_path)

    manifest = [v for v in manifest if v["id"] != voice_id]
    _save_manifest(CLONE_VOICES_MANIFEST, manifest)

    logger.info(f"Clone voice deleted: {voice_id}")
    return {"status": "deleted", "voice_id": voice_id}

## ── LoRA Training ──────────────────────────────────────────────

LORA_MODELS_MANIFEST = os.path.join(LORA_MODELS_DIR, "manifest.json")

def _load_builtin_lora_manifest():
    """Load built-in LoRA manifest from HF (with local fallback). Returns ALL entries with download status."""
    entries = fetch_builtin_manifest(BUILTIN_LORA_DIR)
    result = []
    for entry in entries:
        entry = dict(entry)  # avoid mutating cached list
        local_id = entry["id"] if entry["id"].startswith("builtin_") else f"builtin_{entry['id']}"
        downloaded = is_adapter_downloaded(local_id, BUILTIN_LORA_DIR)
        entry["id"] = local_id
        entry["builtin"] = True
        entry["downloaded"] = downloaded
        entry["adapter_path"] = f"builtin_lora/{local_id}" if downloaded else None
        result.append(entry)
    return result

@app.post("/api/lora/upload_dataset")
async def lora_upload_dataset(file: UploadFile = File(...)):
    """Upload a ZIP containing WAV files and metadata.jsonl."""
    if not file.filename.endswith(".zip"):
        raise HTTPException(status_code=400, detail="File must be a .zip archive")

    # Derive dataset name from ZIP filename
    dataset_name = re.sub(r'[^\w\- ]', '', os.path.splitext(file.filename)[0]).strip()
    dataset_name = re.sub(r'\s+', '_', dataset_name).lower()
    if not dataset_name:
        raise HTTPException(status_code=400, detail="Invalid dataset name from filename")

    dataset_dir = os.path.join(LORA_DATASETS_DIR, dataset_name)
    if os.path.exists(dataset_dir):
        raise HTTPException(status_code=400, detail=f"Dataset '{dataset_name}' already exists")

    # Save ZIP temporarily, then extract
    tmp_path = os.path.join(LORA_DATASETS_DIR, f"_tmp_{dataset_name}.zip")
    try:
        async with aiofiles.open(tmp_path, "wb") as out_file:
            content = await file.read()
            await out_file.write(content)

        os.makedirs(dataset_dir, exist_ok=True)
        with zipfile.ZipFile(tmp_path, "r") as zf:
            zf.extractall(dataset_dir)

        # Check for metadata.jsonl (may be inside a subdirectory)
        metadata_path = os.path.join(dataset_dir, "metadata.jsonl")
        if not os.path.exists(metadata_path):
            # Check one level deep
            for entry in os.listdir(dataset_dir):
                candidate = os.path.join(dataset_dir, entry, "metadata.jsonl")
                if os.path.isdir(os.path.join(dataset_dir, entry)) and os.path.exists(candidate):
                    # Move contents up
                    nested = os.path.join(dataset_dir, entry)
                    for item in os.listdir(nested):
                        shutil.move(os.path.join(nested, item), os.path.join(dataset_dir, item))
                    os.rmdir(nested)
                    metadata_path = os.path.join(dataset_dir, "metadata.jsonl")
                    break

        if not os.path.exists(metadata_path):
            shutil.rmtree(dataset_dir)
            raise HTTPException(status_code=400, detail="ZIP must contain metadata.jsonl")

        # Count samples
        sample_count = 0
        with open(metadata_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    sample_count += 1

        logger.info(f"LoRA dataset uploaded: '{dataset_name}' ({sample_count} samples)")
        return {"status": "uploaded", "dataset_id": dataset_name, "sample_count": sample_count}

    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

@app.post("/api/lora/generate_dataset")
async def lora_generate_dataset(request: LoraGenerateDatasetRequest, background_tasks: BackgroundTasks):
    """Generate a LoRA training dataset using Voice Designer.

    Generates multiple audio samples with the same voice description,
    saving them as a ready-to-train dataset.
    """
    if process_state["dataset_gen"]["running"]:
        raise HTTPException(status_code=400, detail="Dataset generation already running")

    # Build unified sample list from either format
    sample_list = []
    if request.samples:
        for s in request.samples:
            if s.text.strip():
                sample_list.append({"emotion": s.emotion.strip(), "text": s.text.strip()})
    elif request.texts:
        for t in request.texts:
            if t.strip():
                sample_list.append({"emotion": "", "text": t.strip()})

    if not sample_list:
        raise HTTPException(status_code=400, detail="Provide at least one sample text")

    safe_name = _sanitize_name(request.name)
    if not safe_name:
        raise HTTPException(status_code=400, detail="Invalid dataset name")

    dataset_dir = os.path.join(LORA_DATASETS_DIR, safe_name)
    if os.path.exists(dataset_dir):
        raise HTTPException(status_code=400, detail=f"Dataset '{safe_name}' already exists")

    total = len(sample_list)
    root_description = request.description.strip()

    def task():
        process_state["dataset_gen"]["running"] = True
        process_state["dataset_gen"]["logs"] = [
            f"Generating {total} samples with VoiceDesign..."
        ]
        try:
            engine = project_manager.get_engine()
            if not engine:
                process_state["dataset_gen"]["logs"].append("Error: TTS engine not initialized")
                return

            os.makedirs(dataset_dir, exist_ok=True)
            metadata_lines = []
            completed = 0

            for i, sample in enumerate(sample_list):
                text = sample["text"]
                emotion = sample["emotion"]
                # Build full description: root + emotion if provided
                description = f"{root_description}, {emotion}" if emotion else root_description

                process_state["dataset_gen"]["logs"].append(
                    f"[{i+1}/{total}] {('[' + emotion + '] ' if emotion else '')}\"{ text[:60]}{'...' if len(text) > 60 else ''}\""
                )
                try:
                    wav_path, sr = engine.generate_voice_design(
                        description=description,
                        sample_text=text,
                        language=request.language,
                    )
                    # Copy to dataset dir with sequential name
                    dest_filename = f"sample_{i:03d}.wav"
                    dest_path = os.path.join(dataset_dir, dest_filename)
                    shutil.copy2(wav_path, dest_path)

                    # Save first successful sample as ref.wav for consistent speaker embedding
                    if completed == 0:
                        shutil.copy2(wav_path, os.path.join(dataset_dir, "ref.wav"))

                    metadata_lines.append(json.dumps({
                        "audio_filepath": dest_filename,
                        "text": text,
                        "ref_audio": "ref.wav",
                    }, ensure_ascii=False))
                    completed += 1
                    process_state["dataset_gen"]["logs"].append(
                        f"  Saved {dest_filename}"
                    )
                except Exception as e:
                    process_state["dataset_gen"]["logs"].append(
                        f"  Failed: {e}"
                    )

            # Write metadata.jsonl
            metadata_path = os.path.join(dataset_dir, "metadata.jsonl")
            with open(metadata_path, "w", encoding="utf-8") as f:
                f.write("\n".join(metadata_lines) + "\n")

            process_state["dataset_gen"]["logs"].append(
                f"Dataset '{safe_name}' complete: {completed}/{total} samples generated."
            )
            logger.info(f"LoRA dataset generated: '{safe_name}' ({completed} samples)")

        except Exception as e:
            process_state["dataset_gen"]["logs"].append(f"Error: {e}")
            logger.error(f"Dataset generation error: {e}")
            # Clean up partial dataset on failure
            if os.path.exists(dataset_dir):
                shutil.rmtree(dataset_dir)
        finally:
            process_state["dataset_gen"]["running"] = False

    background_tasks.add_task(task)
    return {"status": "started", "dataset_id": safe_name, "total": total}

@app.get("/api/lora/datasets")
async def lora_list_datasets():
    """List uploaded LoRA training datasets."""
    datasets = []
    if not os.path.exists(LORA_DATASETS_DIR):
        return datasets

    for name in sorted(os.listdir(LORA_DATASETS_DIR)):
        dataset_dir = os.path.join(LORA_DATASETS_DIR, name)
        if not os.path.isdir(dataset_dir):
            continue
        metadata_path = os.path.join(dataset_dir, "metadata.jsonl")
        sample_count = 0
        if os.path.exists(metadata_path):
            with open(metadata_path, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        sample_count += 1
        datasets.append({"dataset_id": name, "sample_count": sample_count})
    return datasets

@app.delete("/api/lora/datasets/{dataset_id}")
async def lora_delete_dataset(dataset_id: str):
    """Delete an uploaded dataset."""
    dataset_dir = os.path.join(LORA_DATASETS_DIR, dataset_id)
    if not os.path.isdir(dataset_dir):
        raise HTTPException(status_code=404, detail="Dataset not found")

    shutil.rmtree(dataset_dir)
    logger.info(f"LoRA dataset deleted: {dataset_id}")
    return {"status": "deleted", "dataset_id": dataset_id}

@app.post("/api/lora/train")
async def lora_start_training(request: LoraTrainingRequest, background_tasks: BackgroundTasks):
    """Start LoRA training as a subprocess."""
    if process_state["lora_training"]["running"]:
        raise HTTPException(status_code=400, detail="LoRA training already running")

    # Validate dataset exists
    dataset_dir = os.path.join(LORA_DATASETS_DIR, request.dataset_id)
    if not os.path.isdir(dataset_dir):
        raise HTTPException(status_code=400, detail=f"Dataset '{request.dataset_id}' not found")

    # Build output directory
    safe_name = _sanitize_name(request.name)
    if not safe_name:
        raise HTTPException(status_code=400, detail="Invalid adapter name")

    adapter_id = f"{safe_name}_{int(time.time())}"
    output_dir = os.path.join(LORA_MODELS_DIR, adapter_id)

    # Unload TTS engine to free GPU
    if project_manager.engine is not None:
        logger.info("Unloading TTS engine for LoRA training...")
        project_manager.engine = None
        gc.collect()

    # Build subprocess command
    command = [
        sys.executable, "-u", "train_lora.py",
        "--data_dir", dataset_dir,
        "--output_dir", output_dir,
        "--epochs", str(request.epochs),
        "--lr", str(request.lr),
        "--batch_size", str(request.batch_size),
        "--lora_r", str(request.lora_r),
        "--lora_alpha", str(request.lora_alpha),
        "--gradient_accumulation_steps", str(request.gradient_accumulation_steps),
        "--language", request.language,
    ]

    def on_training_complete():
        """After training subprocess finishes, update manifest if adapter was saved."""
        run_process(command, "lora_training")

        # Check if training produced an adapter
        if os.path.isdir(output_dir) and os.path.exists(os.path.join(output_dir, "training_meta.json")):
            try:
                with open(os.path.join(output_dir, "training_meta.json"), "r") as f:
                    meta = json.load(f)

                manifest = _load_manifest(LORA_MODELS_MANIFEST)
                manifest.append({
                    "id": adapter_id,
                    "name": request.name,
                    "dataset_id": request.dataset_id,
                    "epochs": meta.get("epochs", request.epochs),
                    "final_loss": meta.get("final_loss"),
                    "sample_count": meta.get("num_samples"),
                    "lora_r": meta.get("lora_r"),
                    "lr": meta.get("lr"),
                    "created": time.time(),
                })
                _save_manifest(LORA_MODELS_MANIFEST, manifest)
                logger.info(f"LoRA adapter registered: {adapter_id}")
            except Exception as e:
                logger.error(f"Failed to update LoRA manifest: {e}")

    background_tasks.add_task(on_training_complete)
    return {"status": "started", "adapter_id": adapter_id}

@app.get("/api/lora/models")
async def lora_list_models():
    """List all LoRA adapters (built-in + user-trained)."""
    models = _load_builtin_lora_manifest() + _load_manifest(LORA_MODELS_MANIFEST)
    for m in models:
        is_builtin = m.get("builtin", False)
        is_downloaded = m.get("downloaded", True)  # user-trained are always downloaded

        if not is_downloaded:
            m["preview_audio_url"] = None
            continue

        if is_builtin:
            adapter_dir = os.path.join(BUILTIN_LORA_DIR, m["id"])
            url_prefix = f"/builtin_lora/{m['id']}"
        else:
            adapter_dir = os.path.join(LORA_MODELS_DIR, m["id"])
            url_prefix = f"/lora_models/{m['id']}"
        preview_path = os.path.join(adapter_dir, "preview_sample.wav")
        m["preview_audio_url"] = f"{url_prefix}/preview_sample.wav" if os.path.exists(preview_path) else None
    return models

@app.delete("/api/lora/models/{adapter_id}")
async def lora_delete_model(adapter_id: str):
    """Delete a trained LoRA adapter. Built-in adapters cannot be deleted."""
    builtin = _load_builtin_lora_manifest()
    if any(m["id"] == adapter_id for m in builtin):
        raise HTTPException(status_code=403, detail="Built-in adapters cannot be deleted")
    manifest = _load_manifest(LORA_MODELS_MANIFEST)
    entry = next((m for m in manifest if m["id"] == adapter_id), None)
    if not entry:
        raise HTTPException(status_code=404, detail="Adapter not found")

    # Delete adapter directory
    adapter_dir = os.path.join(LORA_MODELS_DIR, adapter_id)
    if os.path.isdir(adapter_dir):
        shutil.rmtree(adapter_dir)

    # Remove from manifest
    manifest = [m for m in manifest if m["id"] != adapter_id]
    _save_manifest(LORA_MODELS_MANIFEST, manifest)

    logger.info(f"LoRA adapter deleted: {adapter_id}")
    return {"status": "deleted", "adapter_id": adapter_id}

@app.post("/api/lora/download/{adapter_id}")
async def lora_download_builtin(adapter_id: str):
    """Download a built-in LoRA adapter from HuggingFace."""
    manifest = fetch_builtin_manifest(BUILTIN_LORA_DIR)
    hf_name = adapter_id.replace("builtin_", "", 1)
    entry = next((e for e in manifest if e["id"] == hf_name or e["id"] == adapter_id), None)
    if not entry:
        raise HTTPException(status_code=404, detail=f"Unknown built-in adapter: {adapter_id}")

    if is_adapter_downloaded(adapter_id, BUILTIN_LORA_DIR):
        return {"status": "already_downloaded", "adapter_id": adapter_id}

    try:
        download_builtin_adapter(adapter_id, BUILTIN_LORA_DIR)
        logger.info(f"Built-in adapter downloaded: {adapter_id}")
        return {"status": "downloaded", "adapter_id": adapter_id}
    except Exception as e:
        logger.error(f"Download failed for {adapter_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/lora/test")
async def lora_test_model(request: LoraTestRequest):
    """Generate test audio using a LoRA adapter (built-in or user-trained)."""
    # Check both manifests
    builtin = _load_builtin_lora_manifest()
    user_trained = _load_manifest(LORA_MODELS_MANIFEST)
    all_adapters = builtin + user_trained
    entry = next((m for m in all_adapters if m["id"] == request.adapter_id), None)
    if not entry:
        raise HTTPException(status_code=404, detail="Adapter not found")

    is_builtin = entry.get("builtin", False)
    if is_builtin:
        adapter_dir = os.path.join(BUILTIN_LORA_DIR, request.adapter_id)
        audio_url_prefix = f"/builtin_lora/{request.adapter_id}"
    else:
        adapter_dir = os.path.join(LORA_MODELS_DIR, request.adapter_id)
        audio_url_prefix = f"/lora_models/{request.adapter_id}"

    if not os.path.isdir(adapter_dir) and is_builtin:
        try:
            download_builtin_adapter(request.adapter_id, BUILTIN_LORA_DIR)
            adapter_dir = os.path.join(BUILTIN_LORA_DIR, request.adapter_id)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Auto-download failed: {e}")
    elif not os.path.isdir(adapter_dir):
        raise HTTPException(status_code=404, detail="Adapter files not found")

    engine = project_manager.get_engine()
    if not engine:
        raise HTTPException(status_code=500, detail="Failed to initialize TTS engine")

    try:
        output_filename = f"test_{request.adapter_id}_{int(time.time())}.wav"
        output_path = os.path.join(adapter_dir, output_filename)

        voice_data = {
            "type": "lora",
            "adapter_id": request.adapter_id,
            "adapter_path": adapter_dir,
        }
        voice_config = {"_lora_test_": voice_data}
        engine.generate_voice(
            text=request.text,
            instruct_text=request.instruct or "",
            speaker="_lora_test_",
            voice_config=voice_config,
            output_path=output_path,
        )

        return {
            "status": "ok",
            "audio_url": f"{audio_url_prefix}/{output_filename}",
        }
    except Exception as e:
        logger.error(f"LoRA test generation failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

LORA_PREVIEW_TEXT = "The ancient library stood at the crossroads of two forgotten paths, its weathered stone walls covered in ivy that had been growing for centuries."

@app.post("/api/lora/preview/{adapter_id}")
async def lora_preview(adapter_id: str):
    """Generate or return cached preview audio for a LoRA adapter."""
    builtin = _load_builtin_lora_manifest()
    user_trained = _load_manifest(LORA_MODELS_MANIFEST)
    all_adapters = builtin + user_trained
    entry = next((m for m in all_adapters if m["id"] == adapter_id), None)
    if not entry:
        raise HTTPException(status_code=404, detail="Adapter not found")

    is_builtin = entry.get("builtin", False)
    if is_builtin:
        adapter_dir = os.path.join(BUILTIN_LORA_DIR, adapter_id)
        url_prefix = f"/builtin_lora/{adapter_id}"
    else:
        adapter_dir = os.path.join(LORA_MODELS_DIR, adapter_id)
        url_prefix = f"/lora_models/{adapter_id}"

    if not os.path.isdir(adapter_dir) and is_builtin:
        try:
            download_builtin_adapter(adapter_id, BUILTIN_LORA_DIR)
            adapter_dir = os.path.join(BUILTIN_LORA_DIR, adapter_id)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Auto-download failed: {e}")
    elif not os.path.isdir(adapter_dir):
        raise HTTPException(status_code=404, detail="Adapter files not found")

    preview_path = os.path.join(adapter_dir, "preview_sample.wav")

    # Return cached if exists
    if os.path.exists(preview_path):
        return {"status": "cached", "audio_url": f"{url_prefix}/preview_sample.wav"}

    # Generate preview
    engine = project_manager.get_engine()
    if not engine:
        raise HTTPException(status_code=500, detail="Failed to initialize TTS engine")

    try:
        voice_data = {
            "type": "lora",
            "adapter_id": adapter_id,
            "adapter_path": adapter_dir,
        }
        voice_config = {"_lora_preview_": voice_data}
        engine.generate_voice(
            text=LORA_PREVIEW_TEXT,
            instruct_text="",
            speaker="_lora_preview_",
            voice_config=voice_config,
            output_path=preview_path,
        )
        return {"status": "generated", "audio_url": f"{url_prefix}/preview_sample.wav"}
    except Exception as e:
        logger.error(f"LoRA preview generation failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

## ── Dataset Builder ──────────────────────────────────────────

def _load_builder_state(name):
    """Load project state from dataset builder working directory."""
    state_path = os.path.join(DATASET_BUILDER_DIR, name, "state.json")
    if os.path.exists(state_path):
        try:
            with open(state_path, "r", encoding="utf-8") as f:
                state = json.load(f)
            # Ensure new fields exist for backward compat
            state.setdefault("description", "")
            state.setdefault("global_seed", "")
            state.setdefault("samples", [])
            return state
        except Exception:
            pass
    return {"description": "", "global_seed": "", "samples": []}

def _save_builder_state(name, state):
    """Save per-sample state to dataset builder working directory."""
    work_dir = os.path.join(DATASET_BUILDER_DIR, name)
    os.makedirs(work_dir, exist_ok=True)
    with open(os.path.join(work_dir, "state.json"), "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)

@app.get("/api/dataset_builder/list")
async def dataset_builder_list():
    """List existing dataset builder projects."""
    projects = []
    if os.path.isdir(DATASET_BUILDER_DIR):
        for name in sorted(os.listdir(DATASET_BUILDER_DIR)):
            state_path = os.path.join(DATASET_BUILDER_DIR, name, "state.json")
            if os.path.isfile(state_path):
                state = _load_builder_state(name)
                samples = state.get("samples", [])
                projects.append({
                    "name": name,
                    "description": state.get("description", ""),
                    "sample_count": len(samples),
                    "done_count": sum(1 for s in samples if s.get("status") == "done"),
                })
    return projects

@app.post("/api/dataset_builder/create")
async def dataset_builder_create(request: DatasetBuilderCreateRequest):
    """Create a new dataset builder project."""
    safe_name = _sanitize_name(request.name)
    if not safe_name:
        raise HTTPException(status_code=400, detail="Invalid dataset name")
    work_dir = os.path.join(DATASET_BUILDER_DIR, safe_name)
    if os.path.exists(work_dir):
        raise HTTPException(status_code=400, detail=f"Project '{safe_name}' already exists")
    _save_builder_state(safe_name, {"description": "", "global_seed": "", "samples": []})
    return {"name": safe_name}

@app.post("/api/dataset_builder/update_meta")
async def dataset_builder_update_meta(request: DatasetBuilderUpdateMetaRequest):
    """Update project description and global seed without touching samples."""
    safe_name = _sanitize_name(request.name)
    work_dir = os.path.join(DATASET_BUILDER_DIR, safe_name)
    if not os.path.exists(work_dir):
        raise HTTPException(status_code=404, detail="Project not found")
    state = _load_builder_state(safe_name)
    state["description"] = request.description
    state["global_seed"] = request.global_seed
    _save_builder_state(safe_name, state)
    return {"status": "ok"}

@app.post("/api/dataset_builder/update_rows")
async def dataset_builder_update_rows(request: DatasetBuilderUpdateRowsRequest):
    """Update row definitions, preserving existing generation status/audio."""
    safe_name = _sanitize_name(request.name)
    work_dir = os.path.join(DATASET_BUILDER_DIR, safe_name)
    if not os.path.exists(work_dir):
        raise HTTPException(status_code=404, detail="Project not found")
    state = _load_builder_state(safe_name)
    existing = state.get("samples", [])
    # Merge: keep status/audio_url from existing samples where text unchanged
    new_samples = []
    for i, row in enumerate(request.rows):
        sample = {
            "emotion": row.get("emotion", ""),
            "text": row.get("text", "").strip(),
            "seed": row.get("seed", ""),
            "status": "pending",
            "audio_url": None,
        }
        if i < len(existing):
            old = existing[i]
            # Preserve generation state if text unchanged (trimmed comparison)
            if old.get("text", "").strip() == sample["text"]:
                sample["status"] = old.get("status", "pending")
                sample["audio_url"] = old.get("audio_url")
        new_samples.append(sample)
    state["samples"] = new_samples
    _save_builder_state(safe_name, state)
    return {"status": "ok", "sample_count": len(new_samples)}

@app.post("/api/dataset_builder/generate_sample")
async def dataset_builder_generate_sample(request: DatasetSampleGenRequest):
    """Generate a single dataset sample using VoiceDesign."""
    engine = project_manager.get_engine()
    if not engine:
        raise HTTPException(status_code=500, detail="Failed to initialize TTS engine")

    work_dir = os.path.join(DATASET_BUILDER_DIR, request.dataset_name)
    os.makedirs(work_dir, exist_ok=True)

    try:
        wav_path, sr = engine.generate_voice_design(
            description=request.description,
            sample_text=request.text,
            seed=request.seed,
        )

        dest_filename = f"sample_{request.sample_index:03d}.wav"
        dest_path = os.path.join(work_dir, dest_filename)
        shutil.copy2(wav_path, dest_path)

        # Update state (cache-bust URL so browser loads fresh audio on regen)
        cache_bust = int(time.time())
        audio_url = f"/dataset_builder/{request.dataset_name}/{dest_filename}?t={cache_bust}"
        state = _load_builder_state(request.dataset_name)
        samples = state.get("samples", [])
        # Ensure list is large enough
        while len(samples) <= request.sample_index:
            samples.append({"status": "pending"})
        existing_sample = samples[request.sample_index] if request.sample_index < len(samples) else {}
        samples[request.sample_index] = {
            **existing_sample,
            "status": "done",
            "audio_url": audio_url,
            "text": request.text.strip(),
            "description": request.description,
        }
        state["samples"] = samples
        _save_builder_state(request.dataset_name, state)

        return {
            "status": "done",
            "sample_index": request.sample_index,
            "audio_url": audio_url,
        }
    except Exception as e:
        logger.error(f"Dataset builder sample generation failed: {e}")
        # Mark as error in state
        state = _load_builder_state(request.dataset_name)
        samples = state.get("samples", [])
        while len(samples) <= request.sample_index:
            samples.append({"status": "pending"})
        samples[request.sample_index] = {"status": "error", "error": str(e)}
        state["samples"] = samples
        _save_builder_state(request.dataset_name, state)
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/dataset_builder/generate_batch")
async def dataset_builder_generate_batch(request: DatasetBatchGenRequest):
    """Batch generate dataset samples as a background task."""
    if process_state["dataset_builder"]["running"]:
        raise HTTPException(status_code=400, detail="Dataset generation already running")

    if not request.samples or len(request.samples) == 0:
        raise HTTPException(status_code=400, detail="No samples provided")

    safe_name = _sanitize_name(request.name)
    if not safe_name:
        raise HTTPException(status_code=400, detail="Invalid dataset name")

    work_dir = os.path.join(DATASET_BUILDER_DIR, safe_name)
    os.makedirs(work_dir, exist_ok=True)
    root_desc = request.description.strip()

    # Determine which indices to generate
    if request.indices is not None:
        to_generate = request.indices
    else:
        to_generate = list(range(len(request.samples)))

    total = len(to_generate)

    # Snapshot request data for the thread (request object may not survive)
    samples_snapshot = [(s.emotion.strip(), s.text.strip()) for s in request.samples]
    global_seed = request.global_seed
    per_seeds = request.seeds

    def task():
        process_state["dataset_builder"]["running"] = True
        process_state["dataset_builder"]["logs"] = []
        process_state["dataset_builder"]["cancel"] = False

        engine = project_manager.get_engine()
        if not engine:
            process_state["dataset_builder"]["logs"].append("[ERROR] Failed to initialize TTS engine")
            process_state["dataset_builder"]["running"] = False
            return

        state = _load_builder_state(safe_name)
        samples_state = state.get("samples", [])
        # Ensure list is large enough for all samples
        while len(samples_state) < len(samples_snapshot):
            samples_state.append({"status": "pending"})

        completed = 0
        for i, idx in enumerate(to_generate):
            if process_state["dataset_builder"]["cancel"]:
                process_state["dataset_builder"]["logs"].append(f"[CANCEL] Stopped at {completed}/{total}")
                break

            emotion, text = samples_snapshot[idx]
            description = f"{root_desc}, {emotion}" if emotion else root_desc

            # Mark as generating (preserve existing fields like emotion, seed)
            existing_s = samples_state[idx] if idx < len(samples_state) else {}
            samples_state[idx] = {**existing_s, "status": "generating", "text": text, "emotion": emotion, "description": description}
            state["samples"] = samples_state
            _save_builder_state(safe_name, state)

            process_state["dataset_builder"]["logs"].append(
                f"[{i+1}/{total}] {('[' + emotion + '] ' if emotion else '')}\"{text[:60]}{'...' if len(text) > 60 else ''}\""
            )

            try:
                # Resolve seed: per-line > global > random
                seed = -1
                if per_seeds and idx < len(per_seeds) and per_seeds[idx] >= 0:
                    seed = per_seeds[idx]
                elif global_seed >= 0:
                    seed = global_seed

                wav_path, sr = engine.generate_voice_design(
                    description=description,
                    sample_text=text,
                    seed=seed,
                )
                dest_filename = f"sample_{idx:03d}.wav"
                dest_path = os.path.join(work_dir, dest_filename)
                shutil.copy2(wav_path, dest_path)

                samples_state[idx] = {
                    **samples_state[idx],
                    "status": "done",
                    "audio_url": f"/dataset_builder/{safe_name}/{dest_filename}?t={int(time.time())}",
                    "text": text,
                    "emotion": emotion,
                    "description": description,
                }
                completed += 1
            except Exception as e:
                logger.error(f"Dataset builder sample {idx} failed: {e}")
                process_state["dataset_builder"]["logs"].append(f"  Error: {e}")
                samples_state[idx] = {**samples_state[idx], "status": "error", "error": str(e), "text": text, "emotion": emotion}

            state["samples"] = samples_state
            _save_builder_state(safe_name, state)

        process_state["dataset_builder"]["logs"].append(
            f"[DONE] Generated {completed}/{total} samples"
        )
        process_state["dataset_builder"]["running"] = False

    threading.Thread(target=task, daemon=True).start()
    return {"status": "started", "dataset_name": safe_name, "total": total}

@app.post("/api/dataset_builder/cancel")
async def dataset_builder_cancel():
    """Cancel ongoing batch dataset generation."""
    if process_state["dataset_builder"]["running"]:
        process_state["dataset_builder"]["cancel"] = True
        return {"status": "cancelling"}
    return {"status": "not_running"}

@app.get("/api/dataset_builder/status/{name}")
async def dataset_builder_status(name: str):
    """Get per-sample generation status for a dataset builder project."""
    state = _load_builder_state(name)
    return {
        "description": state.get("description", ""),
        "global_seed": state.get("global_seed", ""),
        "samples": state.get("samples", []),
        "running": process_state["dataset_builder"]["running"],
        "logs": process_state["dataset_builder"]["logs"],
    }

@app.post("/api/dataset_builder/save")
async def dataset_builder_save(request: DatasetSaveRequest):
    """Finalize dataset builder project as a training dataset."""
    safe_name = _sanitize_name(request.name)
    if not safe_name:
        raise HTTPException(status_code=400, detail="Invalid dataset name")

    work_dir = os.path.join(DATASET_BUILDER_DIR, safe_name)
    if not os.path.exists(work_dir):
        raise HTTPException(status_code=404, detail="Dataset builder project not found")

    state = _load_builder_state(safe_name)
    samples = state.get("samples", [])

    # Collect completed samples
    done_samples = [(i, s) for i, s in enumerate(samples) if s.get("status") == "done"]
    if not done_samples:
        raise HTTPException(status_code=400, detail="No completed samples to save")

    # Check ref_index is valid
    ref_idx = request.ref_index
    ref_sample = next((s for i, s in done_samples if i == ref_idx), None)
    if ref_sample is None:
        # Fall back to first completed sample
        ref_idx = done_samples[0][0]
        ref_sample = done_samples[0][1]

    # Create training dataset directory
    dataset_dir = os.path.join(LORA_DATASETS_DIR, safe_name)
    if os.path.exists(dataset_dir):
        raise HTTPException(status_code=400, detail=f"Dataset '{safe_name}' already exists in training datasets")

    os.makedirs(dataset_dir, exist_ok=True)

    try:
        metadata_lines = []
        for i, sample in done_samples:
            src_filename = f"sample_{i:03d}.wav"
            src_path = os.path.join(work_dir, src_filename)
            if not os.path.exists(src_path):
                continue

            dest_filename = f"sample_{i:03d}.wav"
            shutil.copy2(src_path, os.path.join(dataset_dir, dest_filename))

            metadata_lines.append(json.dumps({
                "audio_filepath": dest_filename,
                "text": sample.get("text", ""),
                "ref_audio": "ref.wav",
            }, ensure_ascii=False))

        # Copy ref sample and save its text for correct clone prompt alignment
        ref_src = os.path.join(work_dir, f"sample_{ref_idx:03d}.wav")
        if os.path.exists(ref_src):
            shutil.copy2(ref_src, os.path.join(dataset_dir, "ref.wav"))
        ref_text = ref_sample.get("text", "")
        with open(os.path.join(dataset_dir, "ref_text.txt"), "w", encoding="utf-8") as f:
            f.write(ref_text)

        # Write metadata
        with open(os.path.join(dataset_dir, "metadata.jsonl"), "w", encoding="utf-8") as f:
            f.write("\n".join(metadata_lines) + "\n")

        sample_count = len(metadata_lines)
        logger.info(f"Dataset saved: '{safe_name}' ({sample_count} samples, ref=sample_{ref_idx:03d})")

        return {
            "status": "saved",
            "dataset_id": safe_name,
            "sample_count": sample_count,
        }
    except Exception as e:
        # Clean up on failure
        if os.path.exists(dataset_dir):
            shutil.rmtree(dataset_dir, ignore_errors=True)
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/dataset_builder/{name}")
async def dataset_builder_delete(name: str):
    """Discard a dataset builder working project."""
    work_dir = os.path.join(DATASET_BUILDER_DIR, name)
    if not os.path.exists(work_dir):
        raise HTTPException(status_code=404, detail="Dataset builder project not found")
    shutil.rmtree(work_dir, ignore_errors=True)
    logger.info(f"Dataset builder project discarded: {name}")
    return {"status": "deleted", "name": name}

if __name__ == "__main__":
    import uvicorn
    host = os.environ.get("VOC_STUDIO_HOST", "127.0.0.1")
    port = int(os.environ.get("VOC_STUDIO_PORT", "4200"))
    reload = os.environ.get("VOC_STUDIO_RELOAD", "").lower() in ("1", "true", "yes")
    if reload:
        uvicorn.run("app:app", host=host, port=port, access_log=False,
                     reload=True, reload_dirs=[BASE_DIR])
    else:
        uvicorn.run(app, host=host, port=port, access_log=False)
