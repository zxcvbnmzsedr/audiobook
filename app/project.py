import os
import json
import shutil
import subprocess
import threading
import zipfile
import io
import re
import time
from tts import (
    TTSEngine,
    combine_audio_with_pauses,
    compute_timeline,
    sanitize_filename,
    DEFAULT_PAUSE_MS,
    SAME_SPEAKER_PAUSE_MS
)
from pydub import AudioSegment

MAX_CHUNK_CHARS = 500

def get_speaker(entry):
    """Get speaker from entry, checking both 'speaker' and 'type' fields."""
    return entry.get("speaker") or entry.get("type") or ""


def _is_structural_text(text):
    """Check if text is a title, chapter heading, dedication, or other structural fragment."""
    stripped = text.strip()
    if not stripped:
        return True
    # Very short and not a full sentence (no sentence-ending punctuation)
    if len(stripped) < 80 and stripped[-1] not in '.!?。！？':
        return True
    return False


CHAPTER_META_FIELDS = ("chapter_id", "chapter_index", "chapter_title")
NARRATOR_NAMES = {"NARRATOR", "旁白"}
DASHSCOPE_QWEN3_FLASH_MODEL = "qwen3-tts-flash"
DASHSCOPE_QWEN3_INSTRUCT_MODEL = "qwen3-tts-instruct-flash"
DASHSCOPE_QWEN3_FLASH_ONLY_VOICES = {
    "Jennifer", "Ryan", "Katerina", "Aiden", "Bodega", "Sonrisa",
    "Alek", "Dolce", "Sohee", "Ono Anna", "Lenn", "Emilien",
    "Andre", "Radio Gol", "Jada", "Dylan", "Li", "Marcus",
    "Roy", "Peter", "Sunny", "Eric", "Rocky", "Kiki",
}


def _resolve_app_config_path(root_dir):
    """Find the repository-level app/config.json from a book or repo directory."""
    current = os.path.abspath(root_dir)
    while True:
        candidate = os.path.join(current, "app", "config.json")
        if os.path.exists(candidate):
            return candidate
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    return os.path.join(root_dir, "app", "config.json")


def _tts_config_signature(config):
    tts_config = config.get("tts") if isinstance(config, dict) else {}
    if not isinstance(tts_config, dict):
        tts_config = {}
    return json.dumps(tts_config, sort_keys=True, ensure_ascii=False, default=str)


def _entry_chapter_meta(entry):
    return {field: entry.get(field) for field in CHAPTER_META_FIELDS if entry.get(field) is not None}


def _normalize_aliases(value):
    if isinstance(value, str):
        values = re.split(r"[、,，\n]", value)
    elif isinstance(value, list):
        values = value
    else:
        values = []

    aliases = []
    seen = set()
    for item in values:
        alias = str(item or "").strip()
        if not alias:
            continue
        key = alias.casefold()
        if key in seen:
            continue
        seen.add(key)
        aliases.append(alias)
    return aliases


def _normalize_voice_config_item(config):
    if not isinstance(config, dict):
        return {}
    normalized = dict(config)
    if normalized.get("type") == "dashscope":
        model = normalized.get("dashscope_model") or DASHSCOPE_QWEN3_INSTRUCT_MODEL
        voice = str(normalized.get("dashscope_voice") or "").strip()
        if model == DASHSCOPE_QWEN3_INSTRUCT_MODEL and voice in DASHSCOPE_QWEN3_FLASH_ONLY_VOICES:
            model = DASHSCOPE_QWEN3_FLASH_MODEL
        normalized["dashscope_model"] = model
    elif normalized.get("type") == "volcengine":
        normalized["volcengine_resource_id"] = normalized.get("volcengine_resource_id") or "seed-tts-2.0"
        normalized["volcengine_sample_rate"] = normalized.get("volcengine_sample_rate") or 24000
        normalized["volcengine_speech_rate"] = normalized.get("volcengine_speech_rate") or 0
        normalized["volcengine_loudness_rate"] = normalized.get("volcengine_loudness_rate") or 0
        normalized["volcengine_emotion_scale"] = normalized.get("volcengine_emotion_scale") or 4
    return normalized


def _voice_config_has_required_choice(config):
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


def _voice_config_effective_signature(config):
    if not isinstance(config, dict):
        return {}
    normalized = _normalize_voice_config_item(config)
    style = str(normalized.get("character_style") or normalized.get("default_style") or "").strip()
    normalized["character_style"] = style
    normalized.pop("default_style", None)
    normalized.pop("confirmed", None)
    config_type = str(normalized.get("type") or "custom")
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
    fields = fields_by_type.get(config_type, tuple(sorted(normalized.keys())))
    return {
        key: normalized.get(key)
        for key in fields
        if normalized.get(key) not in (None, "", [], {})
    }


def _character_traits(value):
    if isinstance(value, list):
        return "、".join(str(item).strip() for item in value if str(item).strip())
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return str(value or "").strip()


def _character_voice_style(character):
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


def _legacy_auto_voice_config_for_speaker(speaker, character_book=None):
    speaker = str(speaker or "").strip()
    character_book = character_book if isinstance(character_book, dict) else {}
    config = {"type": "custom", "voice": "Ryan", "seed": "-1"}
    style = ""

    if speaker.upper() in NARRATOR_NAMES:
        style = str(character_book.get("narrator_style") or "").strip()
    else:
        for character in character_book.get("characters") or []:
            if not isinstance(character, dict):
                continue
            canonical = str(character.get("canonical") or character.get("name") or "").strip()
            aliases = _normalize_aliases(character.get("aliases") or [])
            names = {canonical.casefold(), *(alias.casefold() for alias in aliases)}
            if speaker.casefold() in names:
                style = _character_voice_style(character)
                break

    if style:
        config["character_style"] = style
    return config


def _is_legacy_unconfirmed_auto_voice_config(speaker, config, character_book=None):
    if not isinstance(config, dict) or config.get("confirmed"):
        return False
    if str(config.get("type") or "custom") != "custom":
        return False
    legacy_auto = _legacy_auto_voice_config_for_speaker(speaker, character_book)
    return _voice_config_effective_signature(config) == _voice_config_effective_signature(legacy_auto)


def _make_chunk(speaker, text, instruct, pause_after=None, chapter_meta=None):
    """Build a chunk dict, omitting pause_after when None for clean JSON."""
    chunk = {"speaker": speaker, "text": text, "instruct": instruct}
    if chapter_meta:
        chunk.update(chapter_meta)
    if pause_after is not None:
        chunk["pause_after"] = pause_after
    return chunk


def group_into_chunks(script_entries, max_chars=MAX_CHUNK_CHARS):
    """Group consecutive entries by same speaker into chunks up to max_chars"""
    if not script_entries:
        return []

    chunks = []
    current_speaker = get_speaker(script_entries[0])
    current_text = script_entries[0].get("text", "")
    current_instruct = script_entries[0].get("instruct", "")
    current_pause_after = script_entries[0].get("pause_after")
    current_chapter_meta = _entry_chapter_meta(script_entries[0])

    for entry in script_entries[1:]:
        speaker = get_speaker(entry)
        text = entry.get("text", "")
        instruct = entry.get("instruct", "")
        chapter_meta = _entry_chapter_meta(entry)

        # Don't merge structural text (titles, chapter headings, dedications)
        if (speaker == current_speaker and instruct == current_instruct
                and chapter_meta == current_chapter_meta
                and not _is_structural_text(current_text)
                and not _is_structural_text(text)):
            combined = current_text + " " + text
            if len(combined) <= max_chars:
                current_text = combined
                # Last merged entry's pause_after wins
                current_pause_after = entry.get("pause_after", current_pause_after)
            else:
                chunks.append(_make_chunk(
                    current_speaker,
                    current_text,
                    current_instruct,
                    current_pause_after,
                    current_chapter_meta,
                ))
                current_text = text
                current_instruct = instruct
                current_pause_after = entry.get("pause_after")
                current_chapter_meta = chapter_meta
        else:
            chunks.append(_make_chunk(
                current_speaker,
                current_text,
                current_instruct,
                current_pause_after,
                current_chapter_meta,
            ))
            current_speaker = speaker
            current_text = text
            current_instruct = instruct
            current_pause_after = entry.get("pause_after")
            current_chapter_meta = chapter_meta

    # Don't forget the last chunk
    chunks.append(_make_chunk(
        current_speaker,
        current_text,
        current_instruct,
        current_pause_after,
        current_chapter_meta,
    ))

    return chunks

class ProjectManager:
    def __init__(self, root_dir):
        self.root_dir = root_dir
        self.script_path = os.path.join(root_dir, "annotated_script.json")
        self.chunks_path = os.path.join(root_dir, "chunks.json")
        self.voicelines_dir = os.path.join(root_dir, "voicelines")
        self.voice_config_path = os.path.join(root_dir, "voice_config.json")
        self.config_path = _resolve_app_config_path(root_dir)

        # Ensure voicelines dir exists
        os.makedirs(self.voicelines_dir, exist_ok=True)

        self.engine = None
        self._engine_config_signature = None
        self._chunks_lock = threading.Lock()  # Thread-safe file writes

    def _load_app_config(self):
        config = {}
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    config = json.load(f)
            except (json.JSONDecodeError, ValueError):
                pass
        return config

    def reset_engine(self):
        self.engine = None
        self._engine_config_signature = None

    def get_engine(self):
        config = self._load_app_config()
        signature = _tts_config_signature(config)
        if self.engine and self._engine_config_signature == signature:
            return self.engine
        if self.engine:
            print("TTS config changed; reinitializing engine.")
            self.engine = None

        try:
            self.engine = TTSEngine(config)
            self._engine_config_signature = signature
            print(f"TTS engine initialized (mode={self.engine.mode})")
            return self.engine
        except Exception as e:
            print(f"Failed to initialize TTS engine: {e}")
            return None

    def _load_tts_config(self):
        """Load TTS config section from config.json for pause defaults."""
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                return json.load(f).get("tts", {})
        except Exception:
            return {}

    def _load_json_dict(self, path):
        if not os.path.exists(path):
            return {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, ValueError):
            return {}

    def _load_character_book(self):
        return self._load_json_dict(os.path.join(self.root_dir, "character_book.json"))

    def _load_voice_config(self):
        return self._load_json_dict(self.voice_config_path)

    def _voice_config_for_chunks(self, chunks=None):
        """Return saved voice config for runtime TTS without alias inheritance."""
        character_book = self._load_character_book()
        return {
            str(name): normalized
            for name, config in self._load_voice_config().items()
            if isinstance(config, dict)
            for normalized in [_normalize_voice_config_item(config)]
            if _voice_config_has_required_choice(normalized)
            and not _is_legacy_unconfirmed_auto_voice_config(str(name), normalized, character_book)
        }

    def load_chunks(self):
        if os.path.exists(self.chunks_path):
            try:
                with open(self.chunks_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, ValueError) as e:
                print(f"WARNING: chunks.json is corrupted ({e}). Regenerating from script...")
                os.remove(self.chunks_path)

        # If no chunks (or corrupted), generate from script
        if os.path.exists(self.script_path):
            try:
                with open(self.script_path, "r", encoding="utf-8") as f:
                    script = json.load(f)
            except (json.JSONDecodeError, ValueError) as e:
                print(f"WARNING: annotated_script.json is also corrupted ({e}). Starting with empty chunks.")
                return []

            chunks = group_into_chunks(script)

            # Initialize chunk status
            for i, chunk in enumerate(chunks):
                chunk["id"] = i
                chunk["status"] = "pending" # pending, generating, done, error
                chunk["audio_path"] = None

            self.save_chunks(chunks)
            return chunks

        return []

    def _atomic_json_write(self, data, target_path, max_retries=5):
        """Atomically write JSON data with retry logic for Windows file locking."""
        tmp_path = target_path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        for attempt in range(max_retries):
            try:
                os.replace(tmp_path, target_path)
                return
            except OSError as e:
                if attempt < max_retries - 1 and (
                    e.errno == 5 or "Access is denied" in str(e) or "being used by another process" in str(e)
                ):
                    delay = 0.05 * (2 ** attempt)
                    time.sleep(delay)
                    continue
                raise

    def save_chunks(self, chunks):
        with self._chunks_lock:
            self._atomic_json_write(chunks, self.chunks_path)

    def _update_chunk_fields(self, index, **fields):
        """Atomically update fields on a single chunk (thread-safe read-modify-write).

        Unlike load_chunks() + modify + save_chunks(), this holds the lock for the
        entire read-modify-write cycle, preventing concurrent threads from
        overwriting each other's updates.
        """
        with self._chunks_lock:
            if not os.path.exists(self.chunks_path):
                return None
            with open(self.chunks_path, "r", encoding="utf-8") as f:
                chunks = json.load(f)
            if not (0 <= index < len(chunks)):
                return None
            chunks[index].update(fields)
            self._atomic_json_write(chunks, self.chunks_path)
            return chunks[index]

    def insert_chunk(self, after_index):
        """Insert an empty chunk after the given index. Returns the new chunk list."""
        with self._chunks_lock:
            if not os.path.exists(self.chunks_path):
                return None
            with open(self.chunks_path, "r", encoding="utf-8") as f:
                chunks = json.load(f)
            if not (0 <= after_index < len(chunks)):
                return None

            # Copy speaker from the row we're splitting from
            source = chunks[after_index]
            new_chunk = {
                "id": after_index + 1,
                "speaker": source.get("speaker", "NARRATOR"),
                "text": "",
                "instruct": "",
                "status": "pending",
                "audio_path": None
            }
            for field in CHAPTER_META_FIELDS:
                if source.get(field) is not None:
                    new_chunk[field] = source.get(field)
            chunks.insert(after_index + 1, new_chunk)

            # Re-number all IDs
            for i, chunk in enumerate(chunks):
                chunk["id"] = i

            self._atomic_json_write(chunks, self.chunks_path)
            return chunks

    def delete_chunk(self, index):
        """Delete a chunk at the given index. Returns (deleted_chunk, updated_chunks) or None."""
        with self._chunks_lock:
            if not os.path.exists(self.chunks_path):
                return None
            with open(self.chunks_path, "r", encoding="utf-8") as f:
                chunks = json.load(f)
            if not (0 <= index < len(chunks)):
                return None
            if len(chunks) <= 1:
                return None  # don't allow deleting the last chunk

            deleted = chunks.pop(index)

            # Re-number all IDs
            for i, chunk in enumerate(chunks):
                chunk["id"] = i

            self._atomic_json_write(chunks, self.chunks_path)
            return deleted, chunks

    def restore_chunk(self, at_index, chunk_data):
        """Re-insert a chunk at a specific index. Returns the updated chunk list."""
        with self._chunks_lock:
            if not os.path.exists(self.chunks_path):
                return None
            with open(self.chunks_path, "r", encoding="utf-8") as f:
                chunks = json.load(f)

            at_index = max(0, min(at_index, len(chunks)))
            chunks.insert(at_index, chunk_data)

            # Re-number all IDs
            for i, chunk in enumerate(chunks):
                chunk["id"] = i

            self._atomic_json_write(chunks, self.chunks_path)
            return chunks

    def update_chunk(self, index, data):
        chunks = self.load_chunks()
        if 0 <= index < len(chunks):
            chunk = chunks[index]
            # Update fields
            if "text" in data: chunk["text"] = data["text"]
            if "instruct" in data: chunk["instruct"] = data["instruct"]
            if "speaker" in data: chunk["speaker"] = data["speaker"]

            # pause_after: set or clear (None removes the key)
            if "pause_after" in data:
                if data["pause_after"] is not None:
                    chunk["pause_after"] = max(0, int(data["pause_after"]))
                else:
                    chunk.pop("pause_after", None)

            # If text/instruct/speaker changed, reset status and detach stale audio.
            # pause_after is timing-only and can safely keep the existing render.
            if "text" in data or "instruct" in data or "speaker" in data:
                chunk["status"] = "pending"
                chunk["audio_path"] = None

            print(f"update_chunk({index}): instruct='{chunk.get('instruct', '')}', speaker='{chunk.get('speaker', '')}'")
            self.save_chunks(chunks)
            return chunk
        return None

    def generate_chunk_audio(self, index):
        chunks = self.load_chunks()
        if not (0 <= index < len(chunks)):
            return False, "Invalid chunk index"

        chunk = chunks[index]
        self._update_chunk_fields(index, status="generating")

        try:
            engine = self.get_engine()
            if not engine:
                self._update_chunk_fields(index, status="error")
                return False, "TTS engine not initialized"

            speaker = chunk["speaker"]
            text = chunk["text"]
            instruct = chunk.get("instruct", "")
            voice_config = self._voice_config_for_chunks([chunk])

            print(f"Generating chunk {index}: speaker={speaker}, instruct='{instruct}', text='{text[:50]}...'")

            # Generate to temp file (unique per chunk for parallel processing)
            temp_path = os.path.join(self.root_dir, f"temp_chunk_{index}.wav")

            success = engine.generate_voice(text, instruct, speaker, voice_config, temp_path)

            if success:
                # Check file size
                if not os.path.exists(temp_path) or os.path.getsize(temp_path) == 0:
                     self._update_chunk_fields(index, status="error")
                     return False, "Generated audio file is missing or empty"

                print(f"Generated WAV size: {os.path.getsize(temp_path)} bytes")

                # Try to convert to mp3, fallback to wav if ffmpeg missing
                filename_base = f"voiceline_{index+1:04d}_{sanitize_filename(speaker)}"
                audio_path = None

                try:
                    segment = AudioSegment.from_wav(temp_path)

                    if len(segment) == 0:
                         self._update_chunk_fields(index, status="error")
                         return False, "Generated audio has 0 duration"

                    mp3_filename = f"{filename_base}.mp3"
                    mp3_filepath = os.path.join(self.voicelines_dir, mp3_filename)

                    # This might fail if ffmpeg is missing or lacks MP3 encoder
                    segment.export(mp3_filepath, format="mp3")

                    # Validate: conda ffmpeg often lacks libmp3lame, producing
                    # a tiny (~428 byte) header-only file without raising an error
                    mp3_size = os.path.getsize(mp3_filepath) if os.path.exists(mp3_filepath) else 0
                    if mp3_size < 1024:
                        print(f"MP3 export produced invalid file ({mp3_size} bytes) — ffmpeg likely lacks MP3 encoder (libmp3lame). Falling back to WAV.")
                        os.remove(mp3_filepath)
                        raise RuntimeError("MP3 export produced invalid file")

                    audio_path = f"voicelines/{mp3_filename}"

                except Exception as e:
                    if "invalid file" not in str(e).lower():
                        print(f"MP3 conversion failed (ffmpeg missing?): {e}")
                    # Fallback: copy WAV
                    wav_filename = f"{filename_base}.wav"
                    wav_filepath = os.path.join(self.voicelines_dir, wav_filename)
                    shutil.copy(temp_path, wav_filepath)

                    audio_path = f"voicelines/{wav_filename}"

                self._update_chunk_fields(index, status="done", audio_path=audio_path)

                # Cleanup with retry (may be locked by pydub/ffmpeg on Windows)
                if os.path.exists(temp_path):
                    for attempt in range(3):
                        try:
                            os.remove(temp_path)
                            break
                        except OSError:
                            if attempt < 2:
                                time.sleep(0.1 * (attempt + 1))
                            else:
                                print(f"Warning: Could not delete temp file {temp_path}")

                return True, audio_path
            else:
                self._update_chunk_fields(index, status="error")
                return False, "Generation failed"

        except Exception as e:
            try:
                self._update_chunk_fields(index, status="error")
            except Exception as update_err:
                print(f"Warning: Failed to update chunk {index} status to error: {update_err}")
            return False, str(e)

    def _load_pause_defaults(self):
        """Return (pause_between_speakers_ms, pause_same_speaker_ms) from config."""
        tts_cfg = self._load_tts_config()
        return (
            tts_cfg.get("pause_between_speakers_ms", DEFAULT_PAUSE_MS),
            tts_cfg.get("pause_same_speaker_ms", SAME_SPEAKER_PAUSE_MS),
        )

    def _load_chunks_with_audio(self):
        """Load chunks and pair each with its AudioSegment. Returns list of (chunk, segment)."""
        chunks = self.load_chunks()
        result = []
        for chunk in chunks:
            path = chunk.get("audio_path")
            if not path:
                continue
            full_path = os.path.join(self.root_dir, path)
            if not os.path.exists(full_path):
                continue
            try:
                segment = AudioSegment.from_file(full_path)
                result.append((chunk, segment))
            except Exception as e:
                print(f"Error loading audio segment {path}: {e}")
        return result

    def chapter_audio_filename(self, chapter_id):
        safe_id = sanitize_filename(chapter_id or "chapter")
        return f"{safe_id}.mp3"

    def chapter_audio_relative_path(self, chapter_id):
        return f"chapter_audio/{self.chapter_audio_filename(chapter_id)}"

    def chapter_audio_path(self, chapter_id):
        return os.path.join(self.root_dir, "chapter_audio", self.chapter_audio_filename(chapter_id))

    def merge_audio(self):
        chunks_with_audio = self._load_chunks_with_audio()
        if not chunks_with_audio:
            return False, "No audio segments found"

        pause_ms, same_speaker_pause_ms = self._load_pause_defaults()
        timeline = compute_timeline(chunks_with_audio, pause_ms, same_speaker_pause_ms)

        # Build final audio from timeline
        audio_segments = [seg for _, seg, _ in timeline]
        speakers = [chunk["speaker"] for chunk, _, _ in timeline]
        pause_overrides = [chunk.get("pause_after") for chunk, _, _ in timeline]

        final_audio = combine_audio_with_pauses(
            audio_segments, speakers, pause_ms, same_speaker_pause_ms, pause_overrides
        )
        output_filename = "cloned_audiobook.mp3"
        output_path = os.path.join(self.root_dir, output_filename)
        final_audio.export(output_path, format="mp3")

        return True, output_filename

    def merge_chapter_audio(self, chapter_id):
        chunks_with_audio = [
            (chunk, segment)
            for chunk, segment in self._load_chunks_with_audio()
            if chunk.get("chapter_id") == chapter_id
        ]
        if not chunks_with_audio:
            return False, "No audio segments found for this chapter"

        pause_ms, same_speaker_pause_ms = self._load_pause_defaults()
        timeline = compute_timeline(chunks_with_audio, pause_ms, same_speaker_pause_ms)
        audio_segments = [seg for _, seg, _ in timeline]
        speakers = [chunk["speaker"] for chunk, _, _ in timeline]
        pause_overrides = [chunk.get("pause_after") for chunk, _, _ in timeline]

        final_audio = combine_audio_with_pauses(
            audio_segments, speakers, pause_ms, same_speaker_pause_ms, pause_overrides
        )
        output_dir = os.path.join(self.root_dir, "chapter_audio")
        os.makedirs(output_dir, exist_ok=True)
        output_path = self.chapter_audio_path(chapter_id)
        final_audio.export(output_path, format="mp3")
        return True, self.chapter_audio_relative_path(chapter_id)

    def export_audacity(self):
        """Export project as an Audacity-compatible zip with per-speaker WAV tracks,
        a LOF file for auto-import, and a labels file for chunk annotations."""
        chunks_with_audio = self._load_chunks_with_audio()
        if not chunks_with_audio:
            return False, "No audio segments found"

        # Phase 1 — Compute timeline
        pause_ms, same_speaker_pause_ms = self._load_pause_defaults()
        timeline = compute_timeline(chunks_with_audio, pause_ms, same_speaker_pause_ms)

        if not timeline:
            return False, "No audio segments found"

        # Total duration = last chunk's start + its length
        last_chunk, last_seg, last_start = timeline[-1]
        total_duration_ms = last_start + len(last_seg)

        # Phase 2 — Build per-speaker WAV tracks
        speakers_ordered = []
        seen = set()
        for chunk, segment, start_ms in timeline:
            if chunk["speaker"] not in seen:
                speakers_ordered.append(chunk["speaker"])
                seen.add(chunk["speaker"])

        speaker_tracks = {}
        for speaker in speakers_ordered:
            track_cursor = 0
            track = AudioSegment.empty()

            for chunk, segment, start_ms in timeline:
                if chunk["speaker"] != speaker:
                    continue
                # Insert silence gap from current track position to this chunk's start
                gap = start_ms - track_cursor
                if gap > 0:
                    track += AudioSegment.silent(duration=gap)
                track += segment
                track_cursor = start_ms + len(segment)

            # Pad to total duration so all tracks are equal length
            remaining = total_duration_ms - track_cursor
            if remaining > 0:
                track += AudioSegment.silent(duration=remaining)

            speaker_tracks[speaker] = track

        # Phase 3 — Build LOF and labels content
        lof_lines = []
        for speaker in speakers_ordered:
            safe_name = sanitize_filename(speaker)
            lof_lines.append(f'file "{safe_name}.wav"')
        lof_content = "\n".join(lof_lines) + "\n"

        label_lines = []
        for chunk, segment, start_ms in timeline:
            start_sec = start_ms / 1000.0
            end_sec = (start_ms + len(segment)) / 1000.0
            text_preview = chunk.get("text", "")[:80]
            label = f"[{chunk['speaker']}] {text_preview}"
            label_lines.append(f"{start_sec:.6f}\t{end_sec:.6f}\t{label}")
        labels_content = "\n".join(label_lines) + "\n"

        # Phase 4 — Zip everything
        zip_path = os.path.join(self.root_dir, "audacity_export.zip")
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("project.lof", lof_content)
            zf.writestr("labels.txt", labels_content)

            for speaker in speakers_ordered:
                safe_name = sanitize_filename(speaker)
                wav_buffer = io.BytesIO()
                speaker_tracks[speaker].export(wav_buffer, format="wav")
                zf.writestr(f"{safe_name}.wav", wav_buffer.getvalue())

        return True, zip_path

    def merge_m4b(self, per_chunk_chapters=False, metadata=None):
        """Merge audio chunks into an M4B audiobook with chapter markers.

        Args:
            per_chunk_chapters: If True, each chunk is a chapter. If False,
                detect chapter headings and group chunks into sections.
            metadata: Optional dict with keys: title, author, narrator, year,
                description, cover_path (absolute path to cover image).

        Returns:
            tuple: (success: bool, message: str)
        """
        metadata = metadata or {}
        chunks_with_audio = self._load_chunks_with_audio()
        if not chunks_with_audio:
            return False, "No audio segments found"

        # Phase 1 — Compute timeline
        pause_ms, same_speaker_pause_ms = self._load_pause_defaults()
        timeline = compute_timeline(chunks_with_audio, pause_ms, same_speaker_pause_ms)

        if not timeline:
            return False, "No audio segments found"

        # Phase 2 — Build chapters
        chapters = self._build_m4b_chapters(timeline, per_chunk_chapters)
        print(f"  M4B: {len(chapters)} chapters")

        # Phase 3 — Combine audio and export to temp WAV
        audio_segments = [seg for _, seg, _ in timeline]
        speakers = [chunk["speaker"] for chunk, _, _ in timeline]
        pause_overrides = [chunk.get("pause_after") for chunk, _, _ in timeline]
        final_audio = combine_audio_with_pauses(
            audio_segments, speakers, pause_ms, same_speaker_pause_ms, pause_overrides
        )

        temp_wav = os.path.join(self.root_dir, "temp_m4b_combined.wav")
        meta_path = os.path.join(self.root_dir, "temp_m4b_meta.txt")
        output_path = os.path.join(self.root_dir, "audiobook.m4b")

        try:
            final_audio.export(temp_wav, format="wav")

            # Phase 4 — Write FFmpeg metadata file with book metadata
            meta_lines = [";FFMETADATA1"]
            meta_lines.append(f"title={self._escape_ffmeta(metadata.get('title') or 'Audiobook')}")
            meta_lines.append(f"artist={self._escape_ffmeta(metadata.get('author') or '')}")
            meta_lines.append(f"album_artist={self._escape_ffmeta(metadata.get('narrator') or '')}")
            meta_lines.append(f"date={self._escape_ffmeta(metadata.get('year') or '')}")
            meta_lines.append(f"comment={self._escape_ffmeta(metadata.get('description') or '')}")
            meta_lines.append("genre=Audiobook")
            meta_lines.append("")
            for title, start_ms, end_ms in chapters:
                safe_title = self._escape_ffmeta(title)
                meta_lines.append("[CHAPTER]")
                meta_lines.append("TIMEBASE=1/1000")
                meta_lines.append(f"START={start_ms}")
                meta_lines.append(f"END={end_ms}")
                meta_lines.append(f"title={safe_title}")
                meta_lines.append("")

            with open(meta_path, "w", encoding="utf-8") as f:
                f.write("\n".join(meta_lines))

            # Phase 5 — FFmpeg: WAV + chapters → M4B (AAC)
            cover_path = metadata.get("cover_path") or ""
            has_cover = cover_path and os.path.exists(cover_path)

            cmd = ["ffmpeg", "-y", "-i", temp_wav]
            if has_cover:
                cmd += ["-i", cover_path]
            cmd += ["-i", meta_path, "-map_metadata", "2" if has_cover else "1"]
            # Map audio stream
            cmd += ["-map", "0:a"]
            if has_cover:
                # Map cover as attached picture
                cmd += ["-map", "1:v", "-c:v", "copy", "-disposition:v:0", "attached_pic"]
            cmd += [
                "-c:a", "aac",
                "-b:a", "128k",
                "-movflags", "+faststart",
                output_path
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
            if result.returncode != 0:
                print(f"FFmpeg stderr: {result.stderr[-500:]}")
                return False, f"FFmpeg failed (exit {result.returncode})"

        finally:
            for tmp in [temp_wav, meta_path]:
                if os.path.exists(tmp):
                    try:
                        os.remove(tmp)
                    except OSError:
                        pass

        return True, "audiobook.m4b"

    @staticmethod
    def _escape_ffmeta(text):
        """Escape special characters for FFmpeg metadata format."""
        text = text.replace("\\", "\\\\")
        text = text.replace("=", "\\=")
        text = text.replace(";", "\\;")
        text = text.replace("#", "\\#")
        text = text.replace("\n", " ")
        return text

    # Regex for detecting chapter/section headings in chunk text
    _HEADING_RE = re.compile(
        r'^(chapter|part|book|volume|prologue|epilogue|introduction|conclusion|act|section)\b',
        re.IGNORECASE
    )

    def _build_m4b_chapters(self, timeline, per_chunk_chapters):
        """Build chapter list from timeline entries.

        Returns:
            list of (title, start_ms, end_ms) tuples
        """
        if per_chunk_chapters:
            chapters = []
            for chunk, segment, start_ms in timeline:
                end_ms = start_ms + len(segment)
                text_preview = chunk.get("text", "")[:80]
                title = chunk.get("chapter_title") or f"[{chunk['speaker']}] {text_preview}"
                chapters.append((title, start_ms, end_ms))
            return chapters

        chapter_groups = []
        current = None
        for chunk, segment, start_ms in timeline:
            chapter_id = chunk.get("chapter_id")
            if not chapter_id:
                current = None
                continue
            end_ms = start_ms + len(segment)
            if current and current["chapter_id"] == chapter_id:
                current["end_ms"] = end_ms
            else:
                current = {
                    "chapter_id": chapter_id,
                    "title": chunk.get("chapter_title") or chapter_id,
                    "start_ms": start_ms,
                    "end_ms": end_ms,
                }
                chapter_groups.append(current)

        if chapter_groups:
            return [
                (group["title"], group["start_ms"], group["end_ms"])
                for group in chapter_groups
            ]

        # Smart grouping: detect chapter headings
        heading_indices = []
        for i, (chunk, segment, start_ms) in enumerate(timeline):
            text = chunk.get("text", "").strip()
            # Short structural text (likely a heading) or starts with heading keyword
            if self._HEADING_RE.match(text):
                heading_indices.append(i)
            elif len(text) < 80 and '"' not in text and text and self._HEADING_RE.search(text):
                heading_indices.append(i)

        # If no headings detected, fall back to per-chunk
        if not heading_indices:
            print("  M4B: No chapter headings detected, falling back to per-chunk chapters")
            return self._build_m4b_chapters(timeline, per_chunk_chapters=True)

        chapters = []

        # Pre-heading chunks → "Introduction"
        if heading_indices[0] > 0:
            start_ms = timeline[0][2]
            last_before = heading_indices[0] - 1
            end_ms = timeline[last_before][2] + len(timeline[last_before][1])
            chapters.append(("Introduction", start_ms, end_ms))

        # Each heading starts a chapter that runs until the next heading
        for idx, head_i in enumerate(heading_indices):
            title = timeline[head_i][0].get("text", "").strip()
            # Truncate long titles
            if len(title) > 120:
                title = title[:117] + "..."

            start_ms = timeline[head_i][2]

            # End = start of next heading, or end of last chunk
            if idx + 1 < len(heading_indices):
                next_head_i = heading_indices[idx + 1]
                last_in_group = next_head_i - 1
            else:
                last_in_group = len(timeline) - 1

            end_ms = timeline[last_in_group][2] + len(timeline[last_in_group][1])
            chapters.append((title, start_ms, end_ms))

        return chapters

    def generate_chunks_parallel(self, indices, max_workers=2, progress_callback=None,
                                  cancel_check=None):
        """Generate multiple chunks in parallel using ThreadPoolExecutor.

        Uses individual TTS API calls with per-speaker voice settings.

        Args:
            indices: List of chunk indices to generate
            max_workers: Number of concurrent TTS workers
            progress_callback: Optional callback(completed, failed, total) for progress updates
            cancel_check: Optional callable returning True when cancellation is requested

        Returns:
            dict with 'completed', 'failed', and 'cancelled' keys
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        results = {"completed": [], "failed": [], "cancelled": 0}

        # Filter out empty-text chunks
        chunks = self.load_chunks()
        if chunks:
            indices = [i for i in indices if 0 <= i < len(chunks) and chunks[i].get("text", "").strip()]

        total = len(indices)

        if total == 0:
            return results

        print(f"Starting parallel generation of {total} chunks with {max_workers} workers...")

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(self.generate_chunk_audio, idx): idx
                for idx in indices
            }

            cancelled = False
            for future in as_completed(futures):
                if cancel_check and cancel_check():
                    cancelled = True
                    print("[CANCEL] Cancellation requested — stopping parallel generation")
                    executor.shutdown(wait=False, cancel_futures=True)
                    break

                idx = futures[future]
                try:
                    success, msg = future.result()
                    if success:
                        results["completed"].append(idx)
                        print(f"Chunk {idx} completed: {msg}")
                    else:
                        results["failed"].append((idx, msg))
                        print(f"Chunk {idx} failed: {msg}")
                except Exception as e:
                    results["failed"].append((idx, str(e)))
                    print(f"Chunk {idx} error: {e}")

                if progress_callback:
                    progress_callback(len(results["completed"]), len(results["failed"]), total)

            # Reset remaining "generating" chunks to "pending"
            if cancelled:
                done_indices = set(results["completed"]) | {idx for idx, _ in results["failed"]}
                chunks = self.load_chunks()
                if chunks:
                    for idx in indices:
                        if idx not in done_indices and 0 <= idx < len(chunks) and chunks[idx].get("status") == "generating":
                            chunks[idx]["status"] = "pending"
                            results["cancelled"] += 1
                    self.save_chunks(chunks)

        print(f"Parallel generation complete: {len(results['completed'])} succeeded, "
              f"{len(results['failed'])} failed, {results['cancelled']} cancelled")
        return results

    def _group_indices_by_voice_type(self, indices, chunks, voice_config):
        """Reorder indices so chunks with the same voice type are contiguous.

        Grouping key matches how tts.py routes batches:
        - "custom" for custom voices (all batched together)
        - "clone:{speaker}" for clone voices (batched per speaker)
        - "lora:{adapter}" for LoRA voices (batched per adapter)
        - "design" for voice design (always sequential)

        Within each group, original order is preserved.
        """
        from collections import OrderedDict
        groups = OrderedDict()

        for idx in indices:
            if not (0 <= idx < len(chunks)):
                groups.setdefault("custom", []).append(idx)
                continue

            speaker = chunks[idx].get("speaker", "")
            voice_data = voice_config.get(speaker, {})
            voice_type = voice_data.get("type", "custom")

            if voice_type == "clone":
                key = f"clone:{speaker}"
            elif voice_type in ("lora", "builtin_lora"):
                adapter_id = voice_data.get("adapter_id", "")
                key = f"lora:{adapter_id}"
            elif voice_type == "design":
                key = "design"
            elif voice_type == "edge":
                key = "edge"
            else:
                key = "custom"

            groups.setdefault(key, []).append(idx)

        reordered = []
        for key, group_indices in groups.items():
            print(f"  Voice group '{key}': {len(group_indices)} chunks")
            reordered.extend(group_indices)

        return reordered

    def generate_chunks_batch(self, indices, batch_seed=-1, batch_size=4, progress_callback=None,
                               batch_group_by_type=False, cancel_check=None):
        """Generate multiple chunks using batch TTS API with a single seed.

        Args:
            indices: List of chunk indices to generate
            batch_seed: Single seed for all generations (-1 for random)
            batch_size: Number of chunks per batch request
            progress_callback: Optional callback(completed, failed, total) for progress updates
            batch_group_by_type: Group indices by voice type before batching for
                GPU efficiency. When False, indices are batched in sequential order.
            cancel_check: Optional callable returning True when cancellation is requested

        Returns:
            dict with 'completed', 'failed', and 'cancelled' keys
        """
        results = {"completed": [], "failed": [], "cancelled": 0}

        # Load chunks and voice config
        chunks = self.load_chunks()

        # Filter out empty-text chunks
        if chunks:
            indices = [i for i in indices if 0 <= i < len(chunks) and chunks[i].get("text", "").strip()]

        total = len(indices)

        if total == 0:
            return results

        print(f"Starting batch generation of {total} chunks (batch_size={batch_size}, seed={batch_seed}, "
              f"group_by_type={batch_group_by_type})...")
        voice_config = self._voice_config_for_chunks(chunks)

        # Get TTS engine
        engine = self.get_engine()
        if not engine:
            for idx in indices:
                results["failed"].append((idx, "TTS engine not initialized"))
            return results

        # Mark all chunks as generating
        for idx in indices:
            if 0 <= idx < len(chunks):
                chunks[idx]["status"] = "generating"
        self.save_chunks(chunks)

        # Optionally reorder indices so same voice-type chunks are contiguous.
        # This produces larger homogeneous batches (e.g. all custom voices
        # together) instead of fragmenting each batch across voice types.
        if batch_group_by_type:
            indices = self._group_indices_by_voice_type(indices, chunks, voice_config)

        # Split indices into batches
        batches = [indices[i:i + batch_size] for i in range(0, len(indices), batch_size)]
        print(f"Processing {len(batches)} batches...")

        cancelled = False
        for batch_num, batch_indices in enumerate(batches):
            if cancel_check and cancel_check():
                cancelled = True
                print(f"[CANCEL] Cancellation requested before batch {batch_num + 1}")
                break

            print(f"Batch {batch_num + 1}/{len(batches)}: {len(batch_indices)} chunks")

            # Build batch request data
            batch_chunks = []
            for idx in batch_indices:
                if 0 <= idx < len(chunks):
                    chunk = chunks[idx]
                    batch_chunks.append({
                        "index": idx,
                        "text": chunk.get("text", ""),
                        "instruct": chunk.get("instruct", ""),
                        "speaker": chunk.get("speaker", "")
                    })

            # Call batch TTS with single seed
            batch_results = engine.generate_batch(batch_chunks, voice_config, self.root_dir, batch_seed)

            # Process completed chunks - convert to MP3 and update status
            chunks = self.load_chunks()  # Reload for each batch

            for idx in batch_results["completed"]:
                if not (0 <= idx < len(chunks)):
                    print(f"Chunk {idx} skipped: index out of range (chunks changed during generation?)")
                    results["failed"].append((idx, "Index out of range after reload"))
                    continue

                temp_path = os.path.join(self.root_dir, f"temp_batch_{idx}.wav")

                if not os.path.exists(temp_path):
                    results["failed"].append((idx, "Temp audio file not found"))
                    chunks[idx]["status"] = "error"
                    continue

                try:
                    chunk = chunks[idx]
                    speaker = chunk.get("speaker", "unknown")
                    filename_base = f"voiceline_{idx+1:04d}_{sanitize_filename(speaker)}"

                    try:
                        segment = AudioSegment.from_file(temp_path)
                        if len(segment) == 0:
                            results["failed"].append((idx, "Audio has 0 duration"))
                            chunks[idx]["status"] = "error"
                            continue

                        mp3_filename = f"{filename_base}.mp3"
                        mp3_filepath = os.path.join(self.voicelines_dir, mp3_filename)
                        segment.export(mp3_filepath, format="mp3")

                        # Validate: conda ffmpeg often lacks libmp3lame, producing
                        # a tiny (~428 byte) header-only file without raising an error
                        mp3_size = os.path.getsize(mp3_filepath) if os.path.exists(mp3_filepath) else 0
                        if mp3_size < 1024:
                            print(f"MP3 export produced invalid file ({mp3_size} bytes) for chunk {idx} — ffmpeg likely lacks MP3 encoder (libmp3lame). Falling back to WAV.")
                            os.remove(mp3_filepath)
                            raise RuntimeError("MP3 export produced invalid file")

                        chunks[idx]["audio_path"] = f"voicelines/{mp3_filename}"

                    except Exception as e:
                        if "invalid file" not in str(e).lower():
                            print(f"MP3 conversion failed for chunk {idx}: {e}")
                        wav_filename = f"{filename_base}.wav"
                        wav_filepath = os.path.join(self.voicelines_dir, wav_filename)
                        shutil.copy(temp_path, wav_filepath)
                        chunks[idx]["audio_path"] = f"voicelines/{wav_filename}"

                    chunks[idx]["status"] = "done"
                    results["completed"].append(idx)
                    print(f"Chunk {idx} completed: {chunks[idx]['audio_path']}")

                    if os.path.exists(temp_path):
                        for attempt in range(3):
                            try:
                                os.remove(temp_path)
                                break
                            except OSError:
                                if attempt < 2:
                                    time.sleep(0.1 * (attempt + 1))
                                else:
                                    print(f"Warning: Could not delete temp file {temp_path}")

                except Exception as e:
                    print(f"Error processing chunk {idx}: {e}")
                    results["failed"].append((idx, str(e)))
                    chunks[idx]["status"] = "error"

            for idx, error in batch_results["failed"]:
                if 0 <= idx < len(chunks):
                    chunks[idx]["status"] = "error"
                results["failed"].append((idx, error))

            self.save_chunks(chunks)

            if progress_callback:
                progress_callback(len(results["completed"]), len(results["failed"]), total)

        # Reset remaining "generating" chunks to "pending" on cancel or completion
        done_indices = set(results["completed"]) | {idx for idx, _ in results["failed"]}
        chunks = self.load_chunks()
        if chunks:
            for idx in indices:
                if idx not in done_indices and 0 <= idx < len(chunks) and chunks[idx].get("status") == "generating":
                    chunks[idx]["status"] = "pending"
                    results["cancelled"] += 1
            if results["cancelled"]:
                self.save_chunks(chunks)

        print(f"Batch generation complete: {len(results['completed'])} succeeded, "
              f"{len(results['failed'])} failed, {results['cancelled']} cancelled")
        return results
