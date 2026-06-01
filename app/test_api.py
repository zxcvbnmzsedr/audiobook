#!/usr/bin/env python3
"""Automated API test script for Voc Studio audiobook generator.

Usage:
    python test_api.py                    # Quick tests only
    python test_api.py --full             # Include TTS/LLM-dependent tests
    python test_api.py --url http://host:port
"""

import argparse
import asyncio
import contextlib
import io
import importlib.util
import json
import os
import re
import sys
import tempfile
import time
import types
from urllib.parse import quote
import requests

# ── Global state ─────────────────────────────────────────────

BASE_URL = ""
FULL_MODE = False
TEST_PREFIX = "_test_"

results = {"passed": 0, "failed": 0, "skipped": 0}
failures = []
shared = {}  # state shared between dependent tests


# ── Helpers ──────────────────────────────────────────────────

class TestFailure(Exception):
    pass


def section(title):
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}")


def run_test(name, func, requires_full=False):
    if requires_full and not FULL_MODE:
        print(f"  [ SKIP ] {name} (requires --full)")
        results["skipped"] += 1
        return
    try:
        func()
        print(f"  [ PASS ] {name}")
        results["passed"] += 1
    except TestFailure as e:
        msg = str(e)
        if msg.startswith("SKIP:"):
            print(f"  [ SKIP ] {name} ({msg[5:].strip()})")
            results["skipped"] += 1
        else:
            print(f"  [ FAIL ] {name}")
            print(f"           {msg}")
            results["failed"] += 1
            failures.append((name, msg))
    except Exception as e:
        print(f"  [ FAIL ] {name}")
        print(f"           {type(e).__name__}: {e}")
        results["failed"] += 1
        failures.append((name, str(e)))


def assert_status(resp, expected=200, msg=""):
    if resp.status_code != expected:
        body = resp.text[:500]
        raise TestFailure(
            f"Expected {expected}, got {resp.status_code}. {msg}\n"
            f"           Body: {body}"
        )


def assert_key(data, key):
    if key not in data:
        raise TestFailure(f"Missing key '{key}' in: {json.dumps(data)[:300]}")


def load_chapter_pipeline_module():
    app_dir = os.path.dirname(os.path.abspath(__file__))
    module_path = os.path.join(app_dir, "generate_script_chapters.py")
    added_path = False
    if app_dir not in sys.path:
        sys.path.insert(0, app_dir)
        added_path = True
    try:
        spec = importlib.util.spec_from_file_location("generate_script_chapters_under_test", module_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        if added_path:
            try:
                sys.path.remove(app_dir)
            except ValueError:
                pass


def load_project_module(*, stub_tts=False):
    app_dir = os.path.dirname(os.path.abspath(__file__))
    module_path = os.path.join(app_dir, "project.py")
    added_path = False
    old_tts = sys.modules.get("tts")
    old_pydub = sys.modules.get("pydub")
    if app_dir not in sys.path:
        sys.path.insert(0, app_dir)
        added_path = True
    try:
        if stub_tts:
            fake_tts = types.ModuleType("tts")
            fake_tts.TTSEngine = object
            fake_tts.combine_audio_with_pauses = lambda *args, **kwargs: None
            fake_tts.compute_timeline = lambda *args, **kwargs: []
            fake_tts.sanitize_filename = lambda value: re.sub(r'[^\w\-]', '_', str(value or "")).lower()
            fake_tts.DEFAULT_PAUSE_MS = 500
            fake_tts.SAME_SPEAKER_PAUSE_MS = 250
            sys.modules["tts"] = fake_tts
            fake_pydub = types.ModuleType("pydub")
            fake_pydub.AudioSegment = object
            sys.modules["pydub"] = fake_pydub
        spec = importlib.util.spec_from_file_location("project_under_test", module_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        if stub_tts:
            if old_tts is None:
                sys.modules.pop("tts", None)
            else:
                sys.modules["tts"] = old_tts
            if old_pydub is None:
                sys.modules.pop("pydub", None)
            else:
                sys.modules["pydub"] = old_pydub
        if added_path:
            try:
                sys.path.remove(app_dir)
            except ValueError:
                pass


def load_app_module():
    app_dir = os.path.dirname(os.path.abspath(__file__))
    module_path = os.path.join(app_dir, "app.py")
    added_path = False
    if app_dir not in sys.path:
        sys.path.insert(0, app_dir)
        added_path = True
    try:
        spec = importlib.util.spec_from_file_location("app_under_test", module_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        if added_path:
            try:
                sys.path.remove(app_dir)
            except ValueError:
                pass


def load_tts_module():
    app_dir = os.path.dirname(os.path.abspath(__file__))
    module_path = os.path.join(app_dir, "tts.py")
    added_path = False
    old_numpy = sys.modules.get("numpy")
    old_soundfile = sys.modules.get("soundfile")
    old_pydub = sys.modules.get("pydub")
    old_gradio_client = sys.modules.get("gradio_client")
    if app_dir not in sys.path:
        sys.path.insert(0, app_dir)
        added_path = True
    try:
        fake_numpy = types.ModuleType("numpy")
        fake_numpy.concatenate = lambda values: values[0] if values else []
        fake_numpy.array = lambda value: value
        fake_numpy.ndarray = object
        sys.modules["numpy"] = fake_numpy
        fake_soundfile = types.ModuleType("soundfile")
        fake_soundfile.read = lambda *args, **kwargs: ([], 24000)
        fake_soundfile.write = lambda *args, **kwargs: None
        sys.modules["soundfile"] = fake_soundfile
        fake_pydub = types.ModuleType("pydub")
        fake_pydub.AudioSegment = object
        sys.modules["pydub"] = fake_pydub
        fake_gradio_client = types.ModuleType("gradio_client")
        fake_gradio_client.Client = object
        sys.modules["gradio_client"] = fake_gradio_client
        spec = importlib.util.spec_from_file_location("tts_under_test", module_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        if old_numpy is None:
            sys.modules.pop("numpy", None)
        else:
            sys.modules["numpy"] = old_numpy
        if old_soundfile is None:
            sys.modules.pop("soundfile", None)
        else:
            sys.modules["soundfile"] = old_soundfile
        if old_pydub is None:
            sys.modules.pop("pydub", None)
        else:
            sys.modules["pydub"] = old_pydub
        if old_gradio_client is None:
            sys.modules.pop("gradio_client", None)
        else:
            sys.modules["gradio_client"] = old_gradio_client
        if added_path:
            try:
                sys.path.remove(app_dir)
            except ValueError:
                pass


def wait_for_task(task, timeout=120, poll_interval=2):
    """Poll /api/status/{task} until it stops running or timeout is reached."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = requests.get(f"{BASE_URL}/api/status/{task}", timeout=10)
        if r.status_code == 200 and not r.json().get("running"):
            return True
        time.sleep(poll_interval)
    return False


def get(path, **kwargs):
    return requests.get(f"{BASE_URL}{path}", timeout=kwargs.pop("timeout", 30), **kwargs)


def post(path, **kwargs):
    return requests.post(f"{BASE_URL}{path}", timeout=kwargs.pop("timeout", 30), **kwargs)


def delete(path, **kwargs):
    return requests.delete(f"{BASE_URL}{path}", timeout=30, **kwargs)


def active_generation_locks():
    """Return active generation tasks that block book/script mutations."""
    active = []
    for task in ("audio", "script", "review"):
        try:
            r = get(f"/api/status/{task}", timeout=10)
        except requests.RequestException as exc:
            raise TestFailure(f"Could not read {task} status before mutation tests: {exc}")
        assert_status(r, 200, msg=f"read {task} status before mutation tests")
        data = r.json()
        if data.get("running"):
            active.append(task)
    return active


def skip_if_generation_locked():
    active = active_generation_locks()
    if active:
        raise TestFailure(f"SKIP: generation lock active ({', '.join(active)})")


# ── Section 1: Server ───────────────────────────────────────

def test_server_reachable():
    r = get("/")
    assert_status(r, 200)
    if "text/html" not in r.headers.get("content-type", ""):
        raise TestFailure(f"Expected HTML, got {r.headers.get('content-type')}")


# ── Section 2: Config ───────────────────────────────────────

def test_get_config():
    r = get("/api/config")
    assert_status(r, 200)
    data = r.json()
    assert_key(data, "llm")
    assert_key(data, "tts")
    # current_file should always be present (may be null)
    assert_key(data, "current_file")


def test_desktop_metadata():
    r = get("/api/desktop")
    assert_status(r, 200)
    data = r.json()
    for key in ("app_name", "data_dir", "cache_dir", "python"):
        assert_key(data, key)
    if data.get("app_name") != "Voc Studio":
        raise TestFailure(f"Expected Voc Studio app name, got {data.get('app_name')}")


def test_modules_status():
    r = get("/api/modules")
    assert_status(r, 200)
    data = r.json()
    assert_key(data, "modules")
    modules = data.get("modules")
    if not isinstance(modules, list) or not modules:
        raise TestFailure(f"Expected non-empty modules list, got {modules}")
    ids = {module.get("id") for module in modules if isinstance(module, dict)}
    required = {"llm.openai_compatible", "tts.qwen3.custom", "audio.ffmpeg"}
    missing = required - ids
    if missing:
        raise TestFailure(f"Missing expected module ids: {sorted(missing)}")
    custom = next(module for module in modules if module.get("id") == "tts.qwen3.custom")
    if custom.get("install_kind") != "hf_snapshot" or not custom.get("installable"):
        raise TestFailure(f"Qwen custom module should be installable HF snapshot, got {custom}")
    ffmpeg = next(module for module in modules if module.get("id") == "audio.ffmpeg")
    if ffmpeg.get("installable"):
        raise TestFailure(f"FFmpeg should not be auto-installable, got {ffmpeg}")


def test_module_install_rejects_manual_module():
    r = post("/api/modules/audio.ffmpeg/install")
    assert_status(r, 400)


def test_module_install_status_and_cancel_idle():
    r = get("/api/modules/install/status")
    assert_status(r, 200)
    data = r.json()
    if "running" not in data or "logs" not in data:
        raise TestFailure(f"Module install status missing expected keys: {data}")

    r = post("/api/modules/install/cancel")
    assert_status(r, 200)
    data = r.json()
    if data.get("status") != "idle":
        raise TestFailure(f"Expected idle cancel response, got {data}")


def test_save_config_roundtrip():
    # Read original
    r = get("/api/config")
    assert_status(r, 200)
    original = r.json()
    shared["original_config"] = original

    # Build test config with modified language
    test_config = {
        "llm": original["llm"],
        "tts": {**original.get("tts", {}), "language": "_test_roundtrip_lang"},
        "prompts": original.get("prompts"),
        "generation": original.get("generation"),
    }
    test_config["tts"].setdefault("mode", "external")
    test_config["tts"].setdefault("url", "http://127.0.0.1:7860")
    test_config["tts"].setdefault("device", "auto")

    # Save modified
    r = post("/api/config", json=test_config)
    assert_status(r, 200)

    # Read back and verify
    r = get("/api/config")
    assert_status(r, 200)
    readback = r.json()
    if readback.get("tts", {}).get("language") != "_test_roundtrip_lang":
        raise TestFailure("Config round-trip failed: language not persisted")

    # Verify generation section persists
    if original.get("generation") and not readback.get("generation"):
        raise TestFailure("Config round-trip failed: generation section dropped")

    # Verify review prompts persist through config save
    readback_prompts = readback.get("prompts", {})
    if original.get("prompts", {}).get("review_system_prompt"):
        if not readback_prompts.get("review_system_prompt"):
            raise TestFailure("Config round-trip failed: review_system_prompt dropped")

    # Restore original
    restore = {
        "llm": original["llm"],
        "tts": original.get("tts", {"mode": "external", "url": "http://127.0.0.1:7860", "device": "auto"}),
        "prompts": original.get("prompts"),
        "generation": original.get("generation"),
    }
    post("/api/config", json=restore)


def test_save_pause_config_roundtrip():
    # Read original
    r = get("/api/config")
    assert_status(r, 200)
    original = r.json()

    # Save with custom pause values
    test_config = {
        "llm": original["llm"],
        "tts": {
            **original.get("tts", {}),
            "pause_between_speakers_ms": 1000,
            "pause_same_speaker_ms": 400,
        },
        "prompts": original.get("prompts"),
        "generation": original.get("generation"),
    }
    test_config["tts"].setdefault("mode", "external")
    test_config["tts"].setdefault("url", "http://127.0.0.1:7860")
    test_config["tts"].setdefault("device", "auto")

    r = post("/api/config", json=test_config)
    assert_status(r, 200)

    # Read back and verify
    r = get("/api/config")
    assert_status(r, 200)
    readback = r.json()
    tts = readback.get("tts", {})
    if tts.get("pause_between_speakers_ms") != 1000:
        raise TestFailure(f"pause_between_speakers_ms not persisted: {tts.get('pause_between_speakers_ms')}")
    if tts.get("pause_same_speaker_ms") != 400:
        raise TestFailure(f"pause_same_speaker_ms not persisted: {tts.get('pause_same_speaker_ms')}")

    # Restore original
    restore = {
        "llm": original["llm"],
        "tts": original.get("tts", {"mode": "external", "url": "http://127.0.0.1:7860", "device": "auto"}),
        "prompts": original.get("prompts"),
        "generation": original.get("generation"),
    }
    post("/api/config", json=restore)


def test_pause_config_defaults():
    """Verify pause fields have sensible defaults when not explicitly set."""
    r = get("/api/config")
    assert_status(r, 200)
    tts = r.json().get("tts", {})
    pause_between = tts.get("pause_between_speakers_ms")
    pause_same = tts.get("pause_same_speaker_ms")
    if pause_between is None:
        raise TestFailure("pause_between_speakers_ms missing from config response")
    if pause_same is None:
        raise TestFailure("pause_same_speaker_ms missing from config response")
    if not isinstance(pause_between, int) or pause_between < 0:
        raise TestFailure(f"Invalid pause_between_speakers_ms: {pause_between}")
    if not isinstance(pause_same, int) or pause_same < 0:
        raise TestFailure(f"Invalid pause_same_speaker_ms: {pause_same}")


def test_save_review_prompts_roundtrip():
    # Read current config
    r = get("/api/config")
    assert_status(r, 200)
    original = r.json()

    # Save config with custom review prompts
    test_config = {
        "llm": original["llm"],
        "tts": original.get("tts", {"mode": "local", "url": "http://127.0.0.1:7860", "device": "auto"}),
        "prompts": {
            **(original.get("prompts") or {}),
            "review_system_prompt": f"{TEST_PREFIX}review_sys",
            "review_user_prompt": f"{TEST_PREFIX}review_usr",
        },
        "generation": original.get("generation"),
    }
    r = post("/api/config", json=test_config)
    assert_status(r, 200)

    # Read back and verify
    r = get("/api/config")
    assert_status(r, 200)
    readback = r.json()
    prompts = readback.get("prompts", {})
    if prompts.get("review_system_prompt") != f"{TEST_PREFIX}review_sys":
        raise TestFailure(f"review_system_prompt not persisted: {prompts.get('review_system_prompt')}")
    if prompts.get("review_user_prompt") != f"{TEST_PREFIX}review_usr":
        raise TestFailure(f"review_user_prompt not persisted: {prompts.get('review_user_prompt')}")

    # Restore original
    restore = {
        "llm": original["llm"],
        "tts": original.get("tts", {"mode": "local", "url": "http://127.0.0.1:7860", "device": "auto"}),
        "prompts": original.get("prompts"),
        "generation": original.get("generation"),
    }
    post("/api/config", json=restore)


def test_get_default_prompts():
    r = get("/api/default_prompts")
    assert_status(r, 200)
    data = r.json()
    assert_key(data, "system_prompt")
    assert_key(data, "user_prompt")
    if not data["system_prompt"]:
        raise TestFailure("system_prompt is empty")
    assert_key(data, "review_system_prompt")
    assert_key(data, "review_user_prompt")
    if not data["review_system_prompt"]:
        raise TestFailure("review_system_prompt is empty")
    if not data["review_user_prompt"]:
        raise TestFailure("review_user_prompt is empty")
    generation_prompts = f"{data.get('system_prompt') or ''}\n{data.get('user_prompt') or ''}"
    if "<旁白:>" not in generation_prompts:
        raise TestFailure("Default generation prompt should require tagged text with <旁白:>")
    if "Output ONLY valid JSON arrays" in generation_prompts:
        raise TestFailure("Default generation prompt still contains legacy JSON-array wording")
    if "You are an audiobook script adapter and annotator." in generation_prompts:
        raise TestFailure("Default generation prompt still contains legacy English wording")
    review_prompts = f"{data.get('review_system_prompt') or ''}\n{data.get('review_user_prompt') or ''}"
    if "你是有声书 TTS 流水线的脚本审校助手" not in review_prompts:
        raise TestFailure("Default review prompt should be Chinese")
    if "You are a script reviewer for an audiobook TTS pipeline." in review_prompts:
        raise TestFailure("Default review prompt still contains legacy English wording")


def test_legacy_generation_prompt_migrates_to_tagged():
    r = get("/api/config")
    assert_status(r, 200)
    original = r.json()
    try:
        test_config = {
            "llm": original["llm"],
            "tts": original.get("tts", {"mode": "local", "url": "http://127.0.0.1:7860", "device": "auto"}),
            "prompts": {
                **(original.get("prompts") or {}),
                "system_prompt": 'Output ONLY valid JSON arrays with "speaker", "text", and "instruct" fields.',
                "user_prompt": "{chunk}",
            },
            "generation": original.get("generation"),
        }
        r = post("/api/config", json=test_config)
        assert_status(r, 200)
        r = get("/api/config")
        assert_status(r, 200)
        prompts = r.json().get("prompts") or {}
        generation_prompts = f"{prompts.get('system_prompt') or ''}\n{prompts.get('user_prompt') or ''}"
        if "<旁白:>" not in generation_prompts:
            raise TestFailure(f"Legacy generation prompt was not migrated to tagged text: {generation_prompts[:300]}")
        if "Output ONLY valid JSON arrays" in generation_prompts:
            raise TestFailure("Legacy JSON prompt text remained after config migration")
    finally:
        restore = {
            "llm": original["llm"],
            "tts": original.get("tts", {"mode": "local", "url": "http://127.0.0.1:7860", "device": "auto"}),
            "prompts": original.get("prompts"),
            "generation": original.get("generation"),
        }
        post("/api/config", json=restore)


def test_cache_unfriendly_generation_prompt_migrates_to_default():
    r = get("/api/config")
    assert_status(r, 200)
    original = r.json()
    try:
        test_config = {
            "llm": original["llm"],
            "tts": original.get("tts", {"mode": "local", "url": "http://127.0.0.1:7860", "device": "auto"}),
            "prompts": {
                **(original.get("prompts") or {}),
                "system_prompt": "You are an audiobook script annotator.",
                "user_prompt": "前文连续性上下文：\n{context}\n\n任务：标注\n\n当前章节：{chunk}",
            },
            "generation": original.get("generation"),
        }
        r = post("/api/config", json=test_config)
        assert_status(r, 200)
        r = get("/api/config")
        assert_status(r, 200)
        user_prompt = (r.json().get("prompts") or {}).get("user_prompt") or ""
        if user_prompt.startswith("前文连续性上下文"):
            raise TestFailure("Cache-unfriendly generation prompt was not migrated")
        if user_prompt.find("{context}") < user_prompt.find("任务"):
            raise TestFailure(f"Dynamic context still appears before stable task instructions: {user_prompt[:300]}")
    finally:
        restore = {
            "llm": original["llm"],
            "tts": original.get("tts", {"mode": "local", "url": "http://127.0.0.1:7860", "device": "auto"}),
            "prompts": original.get("prompts"),
            "generation": original.get("generation"),
        }
        post("/api/config", json=restore)


def test_legacy_english_prompts_migrate_to_chinese_defaults():
    r = get("/api/config")
    assert_status(r, 200)
    original = r.json()
    try:
        test_config = {
            "llm": original["llm"],
            "tts": original.get("tts", {"mode": "local", "url": "http://127.0.0.1:7860", "device": "auto"}),
            "prompts": {
                **(original.get("prompts") or {}),
                "system_prompt": "You are an audiobook script adapter and annotator. The novel is the source of truth. TTS-friendly text.",
                "user_prompt": "FORMAT:\n{chunk}",
                "review_system_prompt": "You are a script reviewer for an audiobook TTS pipeline. CRITICAL RULES",
                "review_user_prompt": "SCRIPT ENTRIES TO REVIEW:\n{batch}",
            },
            "generation": original.get("generation"),
        }
        r = post("/api/config", json=test_config)
        assert_status(r, 200)
        r = get("/api/config")
        assert_status(r, 200)
        prompts = r.json().get("prompts") or {}
        generation_prompts = f"{prompts.get('system_prompt') or ''}\n{prompts.get('user_prompt') or ''}"
        review_prompts = f"{prompts.get('review_system_prompt') or ''}\n{prompts.get('review_user_prompt') or ''}"
        if "You are an audiobook script adapter and annotator." in generation_prompts:
            raise TestFailure("Legacy English generation prompt was not migrated")
        if "你是有声书脚本改编与标注助手" not in generation_prompts:
            raise TestFailure("Generation prompt was not migrated to Chinese default")
        if "You are a script reviewer for an audiobook TTS pipeline." in review_prompts:
            raise TestFailure("Legacy English review prompt was not migrated")
        if "你是有声书 TTS 流水线的脚本审校助手" not in review_prompts:
            raise TestFailure("Review prompt was not migrated to Chinese default")
    finally:
        restore = {
            "llm": original["llm"],
            "tts": original.get("tts", {"mode": "local", "url": "http://127.0.0.1:7860", "device": "auto"}),
            "prompts": original.get("prompts"),
            "generation": original.get("generation"),
        }
        post("/api/config", json=restore)


def test_legacy_generation_fields_are_trimmed():
    r = get("/api/config")
    assert_status(r, 200)
    original = r.json()
    legacy_keys = {
        "agent_model",
        "agent_provider",
        "enable_review_agent_planning",
        "scout_chunk_size",
        "enable_emotion_enricher",
        "enable_coherence_checker",
    }
    try:
        legacy_config = {
            "llm": {**original["llm"], "model_name": "codex-lightweight-model"},
            "tts": original.get("tts", {"mode": "local", "url": "http://127.0.0.1:7860", "device": "auto"}),
            "prompts": original.get("prompts"),
            "generation": {
                **(original.get("generation") or {}),
                "model_name": "",
                "agent_model": "codex-legacy-agent-model",
                "agent_provider": "anthropic",
                "enable_review_agent_planning": True,
                "scout_chunk_size": 99999,
                "enable_emotion_enricher": True,
                "enable_coherence_checker": True,
            },
        }
        r = post("/api/config", json=legacy_config)
        assert_status(r, 200, "save config with legacy generation fields")
        r = get("/api/config")
        assert_status(r, 200, "read migrated generation config")
        generation = r.json().get("generation") or {}
        leaked = sorted(key for key in legacy_keys if key in generation)
        if leaked:
            raise TestFailure(f"Legacy generation fields leaked from /api/config: {leaked}")
        if generation.get("model_name") != "codex-lightweight-model":
            raise TestFailure(f"Generation model should fall back to llm.model_name after legacy fields are trimmed: {generation}")
    finally:
        restore = {
            "llm": original["llm"],
            "tts": original.get("tts", {"mode": "local", "url": "http://127.0.0.1:7860", "device": "auto"}),
            "prompts": original.get("prompts"),
            "generation": original.get("generation"),
        }
        post("/api/config", json=restore)


def test_chapter_memory_config_roundtrip():
    r = get("/api/config")
    assert_status(r, 200)
    original = r.json()
    try:
        generation = {**(original.get("generation") or {}), "enable_chapter_memory": True}
        r = post("/api/config", json={
            "llm": original["llm"],
            "tts": original.get("tts", {"mode": "local", "url": "http://127.0.0.1:7860", "device": "auto"}),
            "prompts": original.get("prompts"),
            "generation": generation,
        })
        assert_status(r, 200, "save config with chapter memory enabled")
        r = get("/api/config")
        assert_status(r, 200, "read config with chapter memory enabled")
        if r.json().get("generation", {}).get("enable_chapter_memory") is not True:
            raise TestFailure(f"enable_chapter_memory=true did not persist: {r.json().get('generation')}")

        generation["enable_chapter_memory"] = False
        r = post("/api/config", json={
            "llm": original["llm"],
            "tts": original.get("tts", {"mode": "local", "url": "http://127.0.0.1:7860", "device": "auto"}),
            "prompts": original.get("prompts"),
            "generation": generation,
        })
        assert_status(r, 200, "save config with chapter memory disabled")
        r = get("/api/config")
        assert_status(r, 200, "read config with chapter memory disabled")
        if r.json().get("generation", {}).get("enable_chapter_memory") is not False:
            raise TestFailure(f"enable_chapter_memory=false did not persist: {r.json().get('generation')}")
    finally:
        restore = {
            "llm": original["llm"],
            "tts": original.get("tts", {"mode": "local", "url": "http://127.0.0.1:7860", "device": "auto"}),
            "prompts": original.get("prompts"),
            "generation": original.get("generation"),
        }
        post("/api/config", json=restore)


# ── Section 3: Upload ───────────────────────────────────────

def test_upload_file():
    skip_if_generation_locked()
    original_book_id = ""
    temp_book_id = ""

    current = get("/api/books/current")
    assert_status(current, 200, "read current book before upload test")
    original_book_id = current.json().get("id") or ""

    try:
        title = f"codex_api_test_upload_{int(time.time() * 1000)}"
        r = post("/api/books", json={"title": title})
        assert_status(r, 200, "create temporary book for upload test")
        temp_book_id = (r.json().get("book") or {}).get("id") or ""
        if not temp_book_id:
            raise TestFailure(f"Temporary book response did not include an id: {r.json()}")

        content = b"Chapter One\nIt was a dark and stormy night.\nThe end."
        files = {"file": (f"{TEST_PREFIX}upload.txt", io.BytesIO(content), "text/plain")}
        r = post("/api/upload", files=files)
        assert_status(r, 200)
        data = r.json()
        assert_key(data, "filename")
        assert_key(data, "path")
        if data["filename"] != f"{TEST_PREFIX}upload.txt":
            raise TestFailure(f"Unexpected filename: {data['filename']}")
    finally:
        if original_book_id:
            try:
                post("/api/books/select", json={"book_id": original_book_id})
            except Exception:
                pass
        if temp_book_id:
            try:
                delete(f"/api/books/{temp_book_id}")
            except Exception:
                pass


def test_upload_chapter_split_variants():
    """Upload splitting should recognize common web-novel chapter title variants."""
    chapter_module = load_chapter_pipeline_module()
    split_text_into_chapters = chapter_module.split_text_into_chapters

    direct_source = (
        "正文前说明\n"
        "这一天，第一场雨落下。\n"
        "第一节课铃响了。\n\n"
        "卷一\n\n"
        "# 第壹章 夜雨\n"
        "雨声落在瓦上。\n\n"
        "【第十二章：归途】\n"
        "他沿着旧路回城。\n\n"
        "第七节 密议\n"
        "灯下有人压低声音。\n\n"
        "第八幕- 风起\n"
        "帷幕终于升起。\n\n"
        "第三场：重逢\n"
        "旧友在桥上重逢。\n\n"
        "外传：旧王城\n"
        "旧王城仍有余火。\n\n"
        "番外篇 第三章 重逢\n"
        "梦里灯火未熄。\n\n"
        "番外二 旧梦\n"
        "梦里灯火未熄。\n\n"
        "Volume 2 Return\n"
        "The road opened again.\n"
    )
    direct_chapters = split_text_into_chapters(direct_source, default_title="全文")
    direct_titles = [chapter.get("title") for chapter in direct_chapters]
    direct_expected = [
        "前言",
        "卷一 / 第壹章 夜雨",
        "第十二章：归途",
        "第七节 密议",
        "第八幕- 风起",
        "第三场：重逢",
        "外传：旧王城",
        "番外篇 第三章 重逢",
        "番外二 旧梦",
        "Volume 2 Return",
    ]
    if direct_titles != direct_expected:
        raise TestFailure(f"Direct chapter splitter titles are wrong: {direct_titles}")
    if any("这一天" in (chapter.get("title") or "") for chapter in direct_chapters):
        raise TestFailure(f"Body text was misclassified as a chapter title: {direct_titles}")

    original_book_id = ""
    temp_book_id = ""

    current = get("/api/books/current")
    assert_status(current, 200, "read current book before chapter split test")
    original_book_id = current.json().get("id") or ""

    try:
        title = f"codex_api_test_chapter_split_{int(time.time() * 1000)}"
        r = post("/api/books", json={"title": title})
        assert_status(r, 200, "create temporary book for chapter split test")
        temp_book_id = (r.json().get("book") or {}).get("id") or ""
        if not temp_book_id:
            raise TestFailure(f"Temporary book response did not include an id: {r.json()}")

        source_text = (
            "序章\n"
            "风从旧城吹过。\n\n"
            "01. 风起\n"
            "第一场雨落下。\n\n"
            "第1卷 第2章 归人\n"
            "归人站在门前。\n\n"
            "# Chapter 3 Return\n"
            "The road opened.\n\n"
            "【第十二章：归途】\n"
            "他沿着旧路回城。\n\n"
            "第七节 密议\n"
            "灯下有人压低声音。\n\n"
            "第八幕- 风起\n"
            "帷幕终于升起。\n\n"
            "第三场：重逢\n"
            "旧友在桥上重逢。\n\n"
            "外传：旧王城\n"
            "旧王城仍有余火。\n\n"
            "番外篇 第三章 重逢\n"
            "梦里灯火未熄。\n\n"
            "番外二 旧梦\n"
            "梦里灯火未熄。\n\n"
            "尾声\n"
            "灯火终于熄灭。\n"
        )
        files = {
            "file": (
                f"{title}.txt",
                io.BytesIO(source_text.encode("utf-8")),
                "text/plain",
            )
        }
        r = post("/api/upload", files=files)
        assert_status(r, 200, "upload source for chapter split test")
        chapters = ((r.json().get("chapters") or {}).get("chapters") or [])
        titles = [chapter.get("title") for chapter in chapters]
        expected = [
            "序章",
            "01. 风起",
            "第1卷 第2章 归人",
            "Chapter 3 Return",
            "第十二章：归途",
            "第七节 密议",
            "第八幕- 风起",
            "第三场：重逢",
            "外传：旧王城",
            "番外篇 第三章 重逢",
            "番外二 旧梦",
            "尾声",
        ]
        if titles != expected:
            raise TestFailure(f"Unexpected chapter split titles: {titles}")
        if any(not chapter.get("path") or not chapter.get("char_count") for chapter in chapters):
            raise TestFailure(f"Chapter records missing path/char_count: {chapters}")
    finally:
        if original_book_id:
            try:
                post("/api/books/select", json={"book_id": original_book_id})
            except Exception:
                pass
        if temp_book_id:
            try:
                delete(f"/api/books/{temp_book_id}")
            except Exception:
                pass


def test_resplit_chapters_dry_run_does_not_write():
    """Resplit dry-run should preview the source split without changing edited chapter files."""
    skip_if_generation_locked()
    original_book_id = ""
    temp_book_id = ""

    current = get("/api/books/current")
    assert_status(current, 200, "read current book before resplit dry-run test")
    original_book_id = current.json().get("id") or ""

    try:
        title = f"codex_api_test_resplit_preview_{int(time.time() * 1000)}"
        r = post("/api/books", json={"title": title})
        assert_status(r, 200, "create temporary book for resplit dry-run test")
        temp_book_id = (r.json().get("book") or {}).get("id") or ""
        if not temp_book_id:
            raise TestFailure(f"Temporary book response did not include an id: {r.json()}")

        source_text = (
            "第一章 原标题\n"
            "风从旧城吹过。\n\n"
            "第二章 新标题\n"
            "雨落在瓦上。\n"
        )
        files = {
            "file": (
                f"{title}.txt",
                io.BytesIO(source_text.encode("utf-8")),
                "text/plain",
            )
        }
        r = post("/api/upload", files=files)
        assert_status(r, 200, "upload source for resplit dry-run test")
        chapters = ((r.json().get("chapters") or {}).get("chapters") or [])
        if len(chapters) != 2:
            raise TestFailure(f"Expected 2 chapters before dry-run, got {chapters}")

        first_id = chapters[0]["chapter_id"]
        edited_title = "手动改过的标题"
        r = post(f"/api/chapters/{first_id}", json={"title": edited_title})
        assert_status(r, 200, "edit chapter before dry-run")

        r = post("/api/chapters/resplit", json={"dry_run": True})
        assert_status(r, 200, "dry-run resplit")
        preview = r.json()
        if preview.get("status") != "preview":
            raise TestFailure(f"Expected status=preview, got {preview}")
        split_preview = preview.get("preview") or {}
        if split_preview.get("chapter_count") != 2:
            raise TestFailure(f"Unexpected dry-run chapter count: {preview}")
        sample_titles = [chapter.get("title") for chapter in split_preview.get("sample_chapters") or []]
        if sample_titles[:2] != ["第一章 原标题", "第二章 新标题"]:
            raise TestFailure(f"Dry-run preview should reflect source split titles: {sample_titles}")

        r = get("/api/chapters")
        assert_status(r, 200, "read chapters after dry-run")
        after_titles = [chapter.get("title") for chapter in (r.json().get("chapters") or [])]
        if after_titles[0] != edited_title:
            raise TestFailure(f"Dry-run resplit changed persisted chapters: {after_titles}")
    finally:
        if original_book_id:
            try:
                post("/api/books/select", json={"book_id": original_book_id})
            except Exception:
                pass
        if temp_book_id:
            try:
                delete(f"/api/books/{temp_book_id}")
            except Exception:
                pass


def test_resplit_chapters_clears_generated_outputs():
    """Resplitting from source should require confirmation and clear stale script state."""
    skip_if_generation_locked()
    original_book_id = ""
    temp_book_id = ""

    current = get("/api/books/current")
    assert_status(current, 200, "read current book before resplit test")
    original_book_id = current.json().get("id") or ""

    try:
        title = f"codex_api_test_resplit_{int(time.time() * 1000)}"
        r = post("/api/books", json={"title": title})
        assert_status(r, 200, "create temporary book for resplit test")
        temp_book_id = (r.json().get("book") or {}).get("id") or ""
        if not temp_book_id:
            raise TestFailure(f"Temporary book response did not include an id: {r.json()}")

        source_text = (
            "第一章 旧章\n"
            "旧城门开了。\n\n"
            "第二章 新章\n"
            "风声追上来。\n"
        )
        files = {
            "file": (
                f"{title}.txt",
                io.BytesIO(source_text.encode("utf-8")),
                "text/plain",
            )
        }
        r = post("/api/upload", files=files)
        assert_status(r, 200, "upload source for resplit test")
        chapters = ((r.json().get("chapters") or {}).get("chapters") or [])
        if len(chapters) != 2:
            raise TestFailure(f"Expected 2 chapters before resplit, got {chapters}")

        tagged_content = (
            f"# [{chapters[0]['chapter_id']}] {chapters[0].get('title') or ''}\n"
            "<旁白:>旧城门开了。\n"
            f"# [{chapters[1]['chapter_id']}] {chapters[1].get('title') or ''}\n"
            "<旁白:>风声追上来。\n"
        )
        r = post("/api/annotated_script/tagged", json={
            "content": tagged_content,
            "replace_scope": "all",
        })
        assert_status(r, 200, "import tagged script before resplit")

        r = post("/api/chapters/resplit", json={"confirm_invalidate": False})
        assert_status(r, 200, "preview resplit")
        preview = r.json()
        if preview.get("status") != "needs_confirmation":
            raise TestFailure(f"Resplit should require confirmation when outputs exist: {preview}")
        artifacts = preview.get("generated_artifacts") or {}
        if artifacts.get("script_entries", 0) < 1 or artifacts.get("chunks", 0) < 1:
            raise TestFailure(f"Resplit preview did not report generated outputs: {preview}")

        r = post("/api/chapters/resplit", json={"confirm_invalidate": True})
        assert_status(r, 200, "confirmed resplit")
        result = r.json()
        if result.get("status") != "resplit":
            raise TestFailure(f"Expected status=resplit, got {result}")
        if (result.get("chapters") or {}).get("chapter_count") != 2:
            raise TestFailure(f"Unexpected chapter count after resplit: {result}")
        invalidated = result.get("invalidated") or {}
        if invalidated.get("removed_script_entries", 0) < 1 or invalidated.get("removed_chunks", 0) < 1:
            raise TestFailure(f"Resplit did not clear generated outputs: {invalidated}")

        r = get("/api/annotated_script")
        if r.status_code != 404:
            raise TestFailure(f"Annotated script should be cleared after resplit, got {r.status_code}: {r.text[:300]}")
        r = get("/api/chunks")
        assert_status(r, 200, "read chunks after resplit")
        if r.json():
            raise TestFailure(f"Chunks should be cleared after resplit: {r.json()}")
    finally:
        if original_book_id:
            try:
                post("/api/books/select", json={"book_id": original_book_id})
            except Exception:
                pass
        if temp_book_id:
            try:
                delete(f"/api/books/{temp_book_id}")
            except Exception:
                pass


def test_append_chapters_preserves_existing_outputs():
    """Appending chapters should keep existing tagged script entries and chunks intact."""
    skip_if_generation_locked()
    original_book_id = ""
    temp_book_id = ""

    current = get("/api/books/current")
    assert_status(current, 200, "read current book before append-preserve test")
    original_book_id = current.json().get("id") or ""

    try:
        title = f"codex_api_test_append_preserve_{int(time.time() * 1000)}"
        r = post("/api/books", json={"title": title})
        assert_status(r, 200, "create temporary book for append-preserve test")
        temp_book_id = (r.json().get("book") or {}).get("id") or ""
        if not temp_book_id:
            raise TestFailure(f"Temporary book response did not include an id: {r.json()}")

        source_text = (
            "第一章 雨夜\n"
            "雨落在城门上。\n\n"
            "第二章 晨钟\n"
            "钟声从山门传来。\n"
        )
        r = post("/api/upload", files={
            "file": (
                f"{title}.txt",
                io.BytesIO(source_text.encode("utf-8")),
                "text/plain",
            )
        })
        assert_status(r, 200, "upload source for append-preserve test")
        chapters = ((r.json().get("chapters") or {}).get("chapters") or [])
        if len(chapters) != 2:
            raise TestFailure(f"Expected 2 chapters before append, got {chapters}")

        tagged_content = (
            f"# [{chapters[0]['chapter_id']}] {chapters[0].get('title') or ''}\n"
            "<旁白:>雨落在城门上。 {instruct=沉稳旁白}\n"
            f"# [{chapters[1]['chapter_id']}] {chapters[1].get('title') or ''}\n"
            "<旁白:>钟声从山门传来。 {instruct=清晨叙事}\n"
        )
        r = post("/api/annotated_script/tagged", json={
            "content": tagged_content,
            "replace_scope": "all",
        })
        assert_status(r, 200, "import tagged script before append")

        r = get("/api/annotated_script")
        assert_status(r, 200, "read script before append")
        script_before = r.json()
        r = get("/api/chunks")
        assert_status(r, 200, "read chunks before append")
        chunks_before = r.json()
        if len(script_before) != 2 or len(chunks_before) != 2:
            raise TestFailure(f"Expected generated state before append, script={script_before}, chunks={chunks_before}")

        updated_source = (
            source_text
            + "\n\n第三章 归途\n"
            + "他沿着旧路回城。\n"
        )
        append_files = {
            "file": (
                f"{title}_updated.txt",
                io.BytesIO(updated_source.encode("utf-8")),
                "text/plain",
            )
        }
        r = post("/api/chapters/append", data={"dry_run": "true"}, files=append_files)
        assert_status(r, 200, "preview full-source append")
        preview = r.json()
        if preview.get("status") != "preview" or preview.get("append_mode") != "full_source" or preview.get("append_count") != 1:
            raise TestFailure(f"Unexpected append preview: {preview}")
        if preview.get("matched_existing_count") != 2:
            raise TestFailure(f"Full-source append should match both existing chapters: {preview}")

        append_files = {
            "file": (
                f"{title}_updated.txt",
                io.BytesIO(updated_source.encode("utf-8")),
                "text/plain",
            )
        }
        r = post("/api/chapters/append", data={"dry_run": "false"}, files=append_files)
        assert_status(r, 200, "confirm full-source append")
        result = r.json()
        if result.get("status") != "appended" or result.get("append_count") != 1:
            raise TestFailure(f"Unexpected append result: {result}")
        if (result.get("chapters") or {}).get("chapter_count") != 3:
            raise TestFailure(f"Append did not produce 3 chapters: {result}")

        r = get("/api/annotated_script")
        assert_status(r, 200, "read script after append")
        script_after = r.json()
        if script_after != script_before:
            raise TestFailure(f"Append changed existing script entries:\nBefore={script_before}\nAfter={script_after}")

        r = get("/api/chunks")
        assert_status(r, 200, "read chunks after append")
        chunks_after = r.json()
        if chunks_after != chunks_before:
            raise TestFailure(f"Append changed existing chunks:\nBefore={chunks_before}\nAfter={chunks_after}")

        r = get("/api/script_progress")
        assert_status(r, 200, "read script progress after append")
        progress = r.json()
        progress_by_id = {item.get("chapter_id"): item for item in progress.get("chapters") or []}
        if len(progress_by_id) != 3:
            raise TestFailure(f"Expected 3 chapter progress rows after append: {progress}")
        for chapter in chapters:
            item = progress_by_id.get(chapter["chapter_id"]) or {}
            if item.get("status") != "generated" or item.get("entry_count") != 1:
                raise TestFailure(f"Existing chapter should remain generated after append: {progress}")
        appended_ids = result.get("appended_chapter_ids") or []
        if len(appended_ids) != 1:
            raise TestFailure(f"Append result did not report one new chapter id: {result}")
        appended_progress = progress_by_id.get(appended_ids[0]) or {}
        if appended_progress.get("status") != "missing" or appended_progress.get("entry_count") != 0:
            raise TestFailure(f"New chapter should be missing script after append: {progress}")

        r = post("/api/generate_script", json={"missing_only": True, "dry_run": True})
        assert_status(r, 200, "dry-run missing-only generation after append")
        dry_run = r.json()
        if dry_run.get("selected_chapter_ids") != appended_ids:
            raise TestFailure(f"Missing-only dry-run should target only appended chapter: {dry_run}")

        r = post("/api/generate_script", json={"chapter_ids": appended_ids, "dry_run": True})
        assert_status(r, 200, "dry-run explicit appended chapter generation")
        dry_run = r.json()
        if dry_run.get("engine") != "chapter_pipeline" or dry_run.get("selected_chapter_ids") != appended_ids:
            raise TestFailure(f"Explicit appended-chapter dry-run should target only appended chapter: {dry_run}")
    finally:
        if original_book_id:
            try:
                post("/api/books/select", json={"book_id": original_book_id})
            except Exception:
                pass
        if temp_book_id:
            try:
                delete(f"/api/books/{temp_book_id}")
            except Exception:
                pass


def test_append_chapters_tail_only_and_rejects_overlap():
    """Append should accept tail-only continuation files and reject overlapping fragments."""
    skip_if_generation_locked()
    original_book_id = ""
    temp_book_id = ""

    current = get("/api/books/current")
    assert_status(current, 200, "read current book before tail-only append test")
    original_book_id = current.json().get("id") or ""

    try:
        title = f"codex_api_test_append_tail_{int(time.time() * 1000)}"
        r = post("/api/books", json={"title": title})
        assert_status(r, 200, "create temporary book for tail-only append test")
        temp_book_id = (r.json().get("book") or {}).get("id") or ""
        if not temp_book_id:
            raise TestFailure(f"Temporary book response did not include an id: {r.json()}")

        source_text = (
            "第一章 起程\n"
            "车马离开旧城。\n\n"
            "第二章 夜宿\n"
            "灯火落在窗前。\n"
        )
        r = post("/api/upload", files={
            "file": (
                f"{title}.txt",
                io.BytesIO(source_text.encode("utf-8")),
                "text/plain",
            )
        })
        assert_status(r, 200, "upload source for tail-only append test")
        chapters = ((r.json().get("chapters") or {}).get("chapters") or [])
        if len(chapters) != 2:
            raise TestFailure(f"Expected 2 chapters before tail-only append, got {chapters}")

        tail_source = (
            "第三章 风雪\n"
            "风雪压住山路。\n\n"
            "第四章 天明\n"
            "天光从云后落下。\n"
        )
        r = post("/api/chapters/append", data={"dry_run": "true"}, files={
            "file": (
                f"{title}_tail.txt",
                io.BytesIO(tail_source.encode("utf-8")),
                "text/plain",
            )
        })
        assert_status(r, 200, "preview tail-only append")
        preview = r.json()
        if preview.get("append_mode") != "tail_only" or preview.get("append_count") != 2:
            raise TestFailure(f"Unexpected tail-only preview: {preview}")

        r = post("/api/chapters/append", data={"dry_run": "false"}, files={
            "file": (
                f"{title}_tail.txt",
                io.BytesIO(tail_source.encode("utf-8")),
                "text/plain",
            )
        })
        assert_status(r, 200, "confirm tail-only append")
        result = r.json()
        if result.get("status") != "appended" or result.get("append_count") != 2:
            raise TestFailure(f"Unexpected tail-only append result: {result}")
        updated_chapters = (result.get("chapters") or {}).get("chapters") or []
        if [chapter.get("index") for chapter in updated_chapters] != [1, 2, 3, 4]:
            raise TestFailure(f"Appended chapters should be indexed sequentially: {updated_chapters}")
        if [chapter.get("chapter_id") for chapter in updated_chapters] != ["chapter_0001", "chapter_0002", "chapter_0003", "chapter_0004"]:
            raise TestFailure(f"Appended chapters should use sequential ids: {updated_chapters}")

        overlap_source = (
            "第二章 夜宿\n"
            "灯火落在窗前。\n\n"
            "第五章 误入\n"
            "他们走错了岔路。\n"
        )
        r = post("/api/chapters/append", data={"dry_run": "true"}, files={
            "file": (
                f"{title}_overlap.txt",
                io.BytesIO(overlap_source.encode("utf-8")),
                "text/plain",
            )
        })
        assert_status(r, 400, "overlapping append should be rejected")
        if "overlaps existing chapters" not in r.text:
            raise TestFailure(f"Overlap append returned unclear error: {r.text[:300]}")
    finally:
        if original_book_id:
            try:
                post("/api/books/select", json={"book_id": original_book_id})
            except Exception:
                pass
        if temp_book_id:
            try:
                delete(f"/api/books/{temp_book_id}")
            except Exception:
                pass


# ── Section 4: Annotated Script ─────────────────────────────

def test_get_annotated_script():
    r = get("/api/annotated_script")
    if r.status_code == 404:
        shared["has_script"] = False
        return  # acceptable — no script loaded
    assert_status(r, 200)
    data = r.json()
    if not isinstance(data, list):
        raise TestFailure(f"Expected list, got {type(data).__name__}")
    shared["has_script"] = True


def test_tagged_generation_retries_json_response():
    """The chapter LLM path must require tagged text, not silently accept JSON as narration."""
    module = load_chapter_pipeline_module()

    class FakeMessage:
        def __init__(self, content):
            self.content = content

    class FakeModel:
        def __init__(self):
            self.calls = []
            self.responses = [
                '[{"speaker":"NARRATOR","text":"雨落在城门上。","instruct":"稳重旁白"}]',
                "<旁白:>雨落在城门上。 {instruct=稳重旁白}\n<阿景:>走。 {instruct=低声}",
            ]

        def invoke(self, messages):
            self.calls.append(messages)
            return FakeMessage(self.responses.pop(0))

    model = FakeModel()
    entries, issues, tagged_text = module.invoke_tagged_entries(
        model,
        "只输出 tagged 文本。",
        "把本章转为 tagged 文本。",
        {
            "chapter_id": "chapter_0001",
            "chapter_index": 1,
            "chapter_title": "第一章 雨夜",
        },
    )

    if len(model.calls) != 2:
        raise TestFailure(f"Expected JSON response to trigger one retry, got {len(model.calls)} calls")
    if len(entries) != 2:
        raise TestFailure(f"Expected 2 tagged entries after retry, got {entries}")
    if entries[0].get("speaker") != "NARRATOR" or entries[1].get("speaker") != "阿景":
        raise TestFailure(f"Tagged speakers were parsed incorrectly: {entries}")
    if any(entry.get("chapter_id") != "chapter_0001" for entry in entries):
        raise TestFailure(f"Chapter metadata was not attached to tagged entries: {entries}")
    if tagged_text.strip().startswith("["):
        raise TestFailure(f"JSON response was accepted instead of retrying: {tagged_text}")
    if issues:
        raise TestFailure(f"Final tagged response should not report parse issues: {issues}")


def test_openai_chapter_model_enables_prompt_cache():
    """The active chapter pipeline should pass prompt cache parameters to OpenAI."""
    module = load_chapter_pipeline_module()
    captured = {}
    original_chat_openai = module.ChatOpenAI

    class FakeChatOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    try:
        module.ChatOpenAI = FakeChatOpenAI
        model = module.build_chat_model({
            "llm": {
                "provider": "openai",
                "api_key": "test-key",
                "model_name": "gpt-test",
                "openai_api_type": "responses",
            },
            "generation": {
                "temperature": 0.2,
                "max_tokens": 1234,
            },
        })
    finally:
        module.ChatOpenAI = original_chat_openai

    if not isinstance(model, FakeChatOpenAI):
        raise TestFailure("Expected build_chat_model to instantiate ChatOpenAI for OpenAI provider")
    model_kwargs = captured.get("model_kwargs")
    if not isinstance(model_kwargs, dict):
        raise TestFailure(f"Expected model_kwargs with prompt cache settings, got {captured}")
    if model_kwargs.get("prompt_cache_key") != module.OPENAI_PROMPT_CACHE_KEY:
        raise TestFailure(f"Unexpected prompt_cache_key: {model_kwargs}")
    if model_kwargs.get("prompt_cache_retention") != module.OPENAI_PROMPT_CACHE_RETENTION:
        raise TestFailure(f"Unexpected prompt_cache_retention: {model_kwargs}")
    if captured.get("use_responses_api") is not True:
        raise TestFailure(f"Expected Responses API to stay enabled, got {captured}")


def test_llm_usage_log_reports_cache_read():
    module = load_chapter_pipeline_module()

    class FakeMessage:
        usage_metadata = {
            "input_tokens": 2048,
            "output_tokens": 64,
            "total_tokens": 2112,
            "input_token_details": {"cache_read": 1024},
        }

    stream = io.StringIO()
    with contextlib.redirect_stdout(stream):
        module.log_usage_metadata("unit", FakeMessage())
    out = stream.getvalue()
    if "cache_read=1024" not in out:
        raise TestFailure(f"Expected usage log to include cache_read, got: {out}")


def test_streaming_llm_output_is_logged_and_assembled():
    module = load_chapter_pipeline_module()

    class FakeChunk:
        def __init__(self, content, usage_metadata=None):
            self.content = content
            self.usage_metadata = usage_metadata

    class FakeStreamingModel:
        def stream(self, messages):
            yield FakeChunk('{"summary": "雨夜')
            yield FakeChunk('归来",\n"items": [')
            yield FakeChunk('"阿景"]}', {"input_token_details": {"cache_read": 128}})

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        value = module.invoke_json(FakeStreamingModel(), "system", "user", expected="object")
    if value.get("summary") != "雨夜归来":
        raise TestFailure(f"Streaming JSON was not assembled correctly: {value}")
    logs = out.getvalue()
    if '"type": "llm_stream"' not in logs:
        raise TestFailure(f"Expected llm_stream events in logs, got: {logs}")
    if "雨夜归来" not in logs and "雨夜" not in logs:
        raise TestFailure(f"Expected streamed content in logs, got: {logs}")
    if "cache_read=128" not in logs:
        raise TestFailure(f"Expected usage metadata from final stream chunk, got: {logs}")


def test_annotation_prompt_templates_tagged_and_migrates_legacy():
    module = load_chapter_pipeline_module()
    character_book = {
        "characters": [{
            "canonical": "阿景",
            "aliases": ["萧景"],
            "traits": "禁军旧部，谨慎克制",
            "voice_profile": "青年男声，低沉克制",
        }],
        "narrator_style": "沉稳旁白",
        "genre": "古风",
        "key_terms": ["赦书"],
    }
    chapter = {"title": "第一章 雨夜", "content": "雨落在城门上。\n阿景说：走。"}
    context = {"recent_entries": [{"speaker": "NARRATOR", "text": "前夜未央。"}]}

    custom_system = "CUSTOM_TAGGED_MARKER system {chapter_title}"
    custom_user = (
        "CUSTOM_TAGGED_MARKER user {chapter_title}\n"
        "角色={character_book}\n"
        "上下文={context}\n"
        "正文={chunk}\n"
        "<旁白:>必须输出 tagged 文本"
    )
    system, user = module.annotation_prompt(
        character_book,
        chapter,
        context,
        {"system_prompt": custom_system, "user_prompt": custom_user},
    )
    rendered = f"{system}\n{user}"
    for expected in ("CUSTOM_TAGGED_MARKER", "第一章 雨夜", "阿景", "雨落在城门上", "recent_entries"):
        if expected not in rendered:
            raise TestFailure(f"Custom tagged prompt placeholder was not rendered: missing {expected}")
    if not user.startswith(module.ANNOTATION_CACHE_PROMPT_MARKER):
        raise TestFailure("Custom annotation prompt should be wrapped in a cache-friendly stable preamble")
    if user.find("recent_entries") < user.find("当前章节输入"):
        raise TestFailure("Dynamic context appeared before the cache-friendly dynamic input section")
    if user.find("雨落在城门上") < user.find("当前章节输入"):
        raise TestFailure("Chapter text appeared before the cache-friendly dynamic input section")
    if "第一章 雨夜" in system:
        raise TestFailure("Dynamic chapter title should not be rendered inside the system prompt")

    legacy_system = 'Output ONLY valid JSON arrays with "speaker", "text", and "instruct" fields.'
    legacy_user = "{chunk}"
    system, user = module.annotation_prompt(
        character_book,
        chapter,
        context,
        {"system_prompt": legacy_system, "user_prompt": legacy_user},
    )
    migrated = f"{system}\n{user}"
    if "Output ONLY valid JSON arrays" in migrated:
        raise TestFailure("Legacy JSON prompt was not replaced by tagged defaults")
    if "<旁白:>" not in migrated:
        raise TestFailure("Migrated annotation prompt should require tagged text")


def test_cache_friendly_prompt_layouts_keep_dynamic_inputs_late():
    module = load_chapter_pipeline_module()
    character_book = {
        "characters": [{
            "canonical": "阿景",
            "aliases": ["萧景"],
            "traits": "禁军旧部",
            "voice_profile": "青年男声",
        }],
        "narrator_style": "沉稳旁白",
        "genre": "古风",
        "key_terms": ["赦书"],
    }
    chapter = {"title": "第一章 雨夜", "content": "雨落在城门上。\n阿景说：走。"}
    context = {"previous_chapter_memory": [{"summary": "前夜未央。"}]}
    entries = [{"speaker": "阿景", "text": "走。", "instruct": "低声"}]

    _, user = module.annotation_prompt(
        character_book,
        chapter,
        context,
        {"user_prompt": "前文连续性上下文：\n{context}\n\n任务：标注\n\n当前章节：{chunk}"},
    )
    dynamic_start = user.find("当前章节输入")
    if dynamic_start < 1000:
        raise TestFailure(f"Annotation dynamic section starts too early for prompt cache: {dynamic_start}")
    for value in ("前夜未央", "雨落在城门上", "阿景"):
        if user.find(value) < dynamic_start:
            raise TestFailure(f"Dynamic annotation value appeared before stable preamble: {value}")

    _, character_user = module.character_analysis_prompt(character_book, chapter)
    dynamic_start = character_user.find("当前章节输入")
    if dynamic_start < 1800:
        raise TestFailure(f"Character analysis dynamic section starts too early: {dynamic_start}")
    if character_user.find("雨落在城门上") < dynamic_start:
        raise TestFailure("Character analysis chapter content appeared before dynamic section")

    _, memory_user = module.chapter_memory_prompt(character_book, chapter, entries, context)
    dynamic_start = memory_user.find("当前章节输入")
    if dynamic_start < 1800:
        raise TestFailure(f"Chapter memory dynamic section starts too early: {dynamic_start}")
    for value in ("前夜未央", "雨落在城门上", "走。"):
        if memory_user.find(value) < dynamic_start:
            raise TestFailure(f"Dynamic memory value appeared before stable preamble: {value}")


def test_character_book_merge_compacts_repeated_profiles():
    module = load_chapter_pipeline_module()
    merged = {
        "characters": [{
            "canonical": "沈照微",
            "aliases": ["照微"],
            "traits": "沈成之女，熟悉县衙文书流程；本章未直接出场；为陆闻舟奔走查证春赦副抄和刑签问题",
            "voice_profile": "年轻女性声线，疲惫压抑，语速不快但字字清楚；本章追问时更冷",
        }],
        "narrator_style": "清晰、稳定、叙事感强。本章为五年后番外，整体节奏应比正篇更缓、更空，像旧案沉入日常后的回声。",
        "genre": "悬疑",
        "key_terms": [f"线索{i}" for i in range(140)],
    }
    for _ in range(8):
        merged = module.merge_character_books(merged, {
            "characters": [{
                "canonical": "沈照微",
                "aliases": ["沈姑娘", "照微"],
                "traits": "沈成之女，熟悉县衙文书流程；此前曾替父送文；为陆闻舟奔走查证春赦副抄和刑签问题；性格克制倔强，悲痛中仍坚持追问事实和时辰",
                "voice_profile": "年轻女性声线，疲惫压抑，语速不快但字字清楚；追问时冷静锋利；当前记忆中带旧案痛感",
            }],
            "narrator_style": "清晰、稳定、叙事感强。本章为五年后番外，整体节奏应比正篇更缓、更空。",
            "genre": "悬疑",
            "key_terms": [f"线索{i}" for i in range(160)],
        })

    character = merged["characters"][0]
    if len(character["traits"]) > module.MAX_CHARACTER_TRAITS_CHARS:
        raise TestFailure(f"traits was not compacted: {len(character['traits'])} chars")
    if len(character["voice_profile"]) > module.MAX_VOICE_PROFILE_CHARS:
        raise TestFailure(f"voice_profile was not compacted: {len(character['voice_profile'])} chars")
    if "本章" in character["traits"] or "此前" in character["traits"] or "当前记忆中" in character["voice_profile"]:
        raise TestFailure(f"Temporary chapter wording leaked into character book: {character}")
    if set(character.get("aliases") or []) != {"沈姑娘", "照微"}:
        raise TestFailure(f"Aliases were not preserved during compaction: {character}")
    if len(merged.get("narrator_style") or "") > module.MAX_NARRATOR_STYLE_CHARS:
        raise TestFailure(f"narrator_style was not compacted: {merged.get('narrator_style')}")
    if len(merged.get("key_terms") or []) != module.MAX_KEY_TERMS:
        raise TestFailure(f"key_terms were not capped: {len(merged.get('key_terms') or [])}")


def test_character_book_normalize_filters_empty_shell_characters():
    module = load_chapter_pipeline_module()
    normalized = module.normalize_character_book({
        "characters": [
            {"canonical": "小吏", "aliases": [], "traits": "", "voice_profile": ""},
            {"canonical": "NARRATOR", "aliases": ["旁白"], "traits": "旁白", "voice_profile": "旁白"},
            {"canonical": "萧景", "aliases": ["景哥"], "traits": "禁军旧部，谨慎克制", "voice_profile": "青年男声，低沉克制"},
        ],
        "key_terms": ["赦书", "赦书", ""],
    })
    names = [character.get("canonical") for character in normalized.get("characters") or []]
    if names != ["萧景"]:
        raise TestFailure(f"Unexpected normalized character set: {normalized}")
    if normalized.get("key_terms") != ["赦书"]:
        raise TestFailure(f"key_terms were not normalized: {normalized.get('key_terms')}")


def test_api_character_book_merge_uses_same_compaction_rules():
    module = load_app_module()
    merged = {
        "characters": [{
            "canonical": "陆闻舟",
            "aliases": ["闻舟"],
            "traits": "被卷入青川县粮案；本章未直接出场；临刑前仍安抚沈照微",
            "voice_profile": "青年男性读书人声线，清正温和；本章主要通过遗言存在",
        }],
        "narrator_style": "清晰、稳定、叙事感强。本章为五年后番外，整体节奏应比正篇更缓、更空。",
        "genre": "悬疑",
        "key_terms": [f"术语{i}" for i in range(140)],
    }
    for _ in range(8):
        merged = module._merge_character_books(merged, {
            "characters": [{
                "canonical": "陆闻舟",
                "aliases": ["陆公子", "闻舟"],
                "traits": "被卷入青川县粮案；此前曾替人抄契；与沈照微互相牵挂；临刑前仍安抚沈照微；性格清正温和",
                "voice_profile": "青年男性读书人声线，清正温和，疲惫沙哑却咬字清楚；当前记忆中带临刑克制",
            }],
            "narrator_style": "清晰、稳定、叙事感强。本章为五年后番外，整体节奏应比正篇更缓。",
            "genre": "悬疑",
            "key_terms": [f"术语{i}" for i in range(160)],
        })

    character = merged["characters"][0]
    if len(character["traits"]) > module.MAX_CHARACTER_TRAITS_CHARS:
        raise TestFailure(f"API traits was not compacted: {len(character['traits'])} chars")
    if len(character["voice_profile"]) > module.MAX_VOICE_PROFILE_CHARS:
        raise TestFailure(f"API voice_profile was not compacted: {len(character['voice_profile'])} chars")
    if "本章" in character["traits"] or "此前" in character["traits"] or "当前记忆中" in character["voice_profile"]:
        raise TestFailure(f"Temporary chapter wording leaked through API merge: {character}")
    if set(character.get("aliases") or []) != {"陆公子", "闻舟"}:
        raise TestFailure(f"API merge did not preserve aliases: {character}")
    if len(merged.get("key_terms") or []) != module.MAX_KEY_TERMS:
        raise TestFailure(f"API key_terms were not capped: {len(merged.get('key_terms') or [])}")


def test_api_character_book_compact_endpoint_is_local_only():
    module = load_app_module()
    with tempfile.TemporaryDirectory() as tmpdir:
        old_current_book_dir = module._current_book_dir
        old_process_state = module.process_state
        try:
            module._current_book_dir = lambda: tmpdir
            module.process_state = {
                "script": {"running": False, "logs": []},
                "voices": {"running": False, "logs": []},
                "audio": {"running": False, "logs": [], "cancel": False},
                "review": {"running": False, "logs": []},
            }
            script_entries = [{"speaker": "陆闻舟", "text": "我未见实粮。", "instruct": ""}]
            with open(os.path.join(tmpdir, "annotated_script.json"), "w", encoding="utf-8") as f:
                json.dump(script_entries, f, ensure_ascii=False)
            with open(os.path.join(tmpdir, "character_book.json"), "w", encoding="utf-8") as f:
                json.dump({
                    "characters": [
                        {
                            "canonical": "陆闻舟",
                            "aliases": ["闻舟"],
                            "traits": "被卷入青川县粮案；本章未直接出场；此前曾替人抄契；与沈照微互相牵挂；临刑前仍安抚沈照微；性格清正温和",
                            "voice_profile": "青年男性读书人声线，清正温和；当前记忆中带临刑克制",
                        },
                        {"canonical": "小吏", "aliases": [], "traits": "", "voice_profile": ""},
                    ],
                    "narrator_style": "清晰、稳定、叙事感强。本章为五年后番外，整体节奏应比正篇更缓。",
                    "genre": "悬疑",
                    "key_terms": [f"术语{i}" for i in range(140)],
                }, f, ensure_ascii=False)

            result = asyncio.run(module.compact_characters())
            if result.get("status") != "compacted":
                raise TestFailure(f"Compact endpoint returned wrong status: {result}")
            if result.get("removed_characters") != 1:
                raise TestFailure(f"Compact endpoint did not remove empty character: {result}")
            character = result.get("character_book", {}).get("characters", [{}])[0]
            if "本章" in character.get("traits", "") or "此前" in character.get("traits", ""):
                raise TestFailure(f"Compact endpoint kept temporary wording: {character}")
            if len(character.get("traits", "")) > module.MAX_CHARACTER_TRAITS_CHARS:
                raise TestFailure(f"Compact endpoint did not cap traits: {character}")
            with open(os.path.join(tmpdir, "annotated_script.json"), "r", encoding="utf-8") as f:
                if json.load(f) != script_entries:
                    raise TestFailure("Compact endpoint changed annotated_script.json")
        finally:
            module._current_book_dir = old_current_book_dir
            module.process_state = old_process_state


def test_partial_chapter_merge_keeps_orphans_once():
    """Partial chapter regeneration should preserve orphan legacy entries without duplicating them."""
    module = load_chapter_pipeline_module()
    existing_entries = [
        {"chapter_id": "chapter_0001", "speaker": "NARRATOR", "text": "第一章旧旁白", "instruct": ""},
        {"chapter_id": "chapter_0002", "speaker": "阿景", "text": "第二章旧台词", "instruct": ""},
        {"chapter_id": "legacy_extra", "speaker": "NARRATOR", "text": "历史孤儿章节", "instruct": ""},
        {"speaker": "NARRATOR", "text": "未归属片段", "instruct": ""},
    ]
    generated_by_chapter = {
        "chapter_0002": [
            {"chapter_id": "chapter_0002", "speaker": "阿景", "text": "第二章新台词", "instruct": "低声"},
        ]
    }
    all_chapters = [
        {"chapter_id": "chapter_0001", "index": 1, "title": "第一章"},
        {"chapter_id": "chapter_0002", "index": 2, "title": "第二章"},
    ]

    merged = module.merge_generated_chapters(existing_entries, generated_by_chapter, all_chapters)
    texts = [entry.get("text") for entry in merged]
    if texts != ["第一章旧旁白", "第二章新台词", "历史孤儿章节", "未归属片段"]:
        raise TestFailure(f"Partial merge produced wrong order or duplicates: {merged}")
    if texts.count("历史孤儿章节") != 1:
        raise TestFailure(f"Orphan legacy chapter was duplicated: {merged}")
    if "第二章旧台词" in texts:
        raise TestFailure(f"Regenerated chapter kept old entries: {merged}")


def test_script_checkpoint_removes_unfinished_target_chapters():
    """Checkpoint files should only keep completed target chapters so resume can detect unfinished work."""
    module = load_chapter_pipeline_module()
    existing_entries = [
        {"chapter_id": "chapter_0001", "speaker": "NARRATOR", "text": "第一章旧旁白", "instruct": ""},
        {"chapter_id": "chapter_0002", "speaker": "阿景", "text": "第二章旧台词", "instruct": ""},
        {"chapter_id": "chapter_0003", "speaker": "NARRATOR", "text": "第三章旧旁白", "instruct": ""},
        {"chapter_id": "legacy_extra", "speaker": "NARRATOR", "text": "历史孤儿章节", "instruct": ""},
    ]
    generated_by_chapter = {
        "chapter_0001": [
            {"chapter_id": "chapter_0001", "speaker": "NARRATOR", "text": "第一章新旁白", "instruct": ""},
        ]
    }
    all_chapters = [
        {"chapter_id": "chapter_0001", "index": 1, "title": "第一章"},
        {"chapter_id": "chapter_0002", "index": 2, "title": "第二章"},
        {"chapter_id": "chapter_0003", "index": 3, "title": "第三章"},
    ]

    merged = module.merge_checkpoint_entries(
        existing_entries,
        generated_by_chapter,
        all_chapters,
        {"chapter_0001", "chapter_0002"},
    )
    texts = [entry.get("text") for entry in merged]
    if texts != ["第一章新旁白", "第三章旧旁白", "历史孤儿章节"]:
        raise TestFailure(f"Checkpoint merge kept an unfinished target chapter or changed ordering: {merged}")

    full_book_targets = module.target_chapter_ids_for_checkpoint(set(), all_chapters)
    merged = module.merge_checkpoint_entries(
        existing_entries,
        generated_by_chapter,
        all_chapters,
        full_book_targets,
    )
    texts = [entry.get("text") for entry in merged]
    if texts != ["第一章新旁白", "历史孤儿章节"]:
        raise TestFailure(f"Full-book checkpoint should drop unfinished manifest chapters: {merged}")


def test_tagged_chunk_character_sync_pipeline():
    """Verify the lightweight novel pipeline stays coherent without LLM/TTS calls."""
    skip_if_generation_locked()
    original_book_id = ""
    temp_book_id = ""

    current = get("/api/books/current")
    assert_status(current, 200, "read current book before temp test")
    original_book_id = current.json().get("id") or ""

    try:
        title = f"codex_api_test_tagged_sync_{int(time.time() * 1000)}"
        r = post("/api/books", json={"title": title})
        assert_status(r, 200, "create temporary book")
        created = r.json().get("book") or {}
        temp_book_id = created.get("id") or ""
        if not temp_book_id:
            raise TestFailure(f"Temporary book response did not include an id: {created}")

        source_text = (
            "第一章 归来\n"
            "夜色压着城门。\n"
            "阿景说：我回来了。\n\n"
            "第二章 风起\n"
            "风从檐下掠过。\n"
            "阿景又说：继续走。\n"
        )
        files = {
            "file": (
                f"{title}.txt",
                io.BytesIO(source_text.encode("utf-8")),
                "text/plain",
            )
        }
        r = post("/api/upload", files=files)
        assert_status(r, 200, "upload temporary source")
        chapters = ((r.json().get("chapters") or {}).get("chapters") or [])
        if len(chapters) < 2:
            raise TestFailure(f"Expected at least 2 chapters after upload, got {chapters}")
        first_chapter_id = chapters[0]["chapter_id"]
        second_chapter_id = chapters[1]["chapter_id"]

        tagged_content = (
            f"# [{first_chapter_id}] {chapters[0].get('title') or ''}\n"
            "<旁白:>夜色压着城门。 {instruct=稳重旁白}\n"
            "<阿景:>我回来了。 {instruct=低声克制}\n"
            f"# [{second_chapter_id}] {chapters[1].get('title') or ''}\n"
            "<旁白:>风从檐下掠过。 {instruct=稳定叙事}\n"
            "<阿景:>继续走。 {instruct=压低声音}\n"
        )
        r = post("/api/annotated_script/tagged", json={
            "content": tagged_content,
            "replace_scope": "all",
        })
        assert_status(r, 200, "import tagged script")
        imported = r.json()
        if imported.get("status") != "imported":
            raise TestFailure(f"Expected tagged import status=imported, got {imported}")
        if imported.get("voice_config_added", 0) or imported.get("voice_config_updated", 0):
            raise TestFailure(f"Tagged import should not create voice config for unset speakers: {imported}")

        r = get("/api/voices")
        assert_status(r, 200, "read voices after tagged import")
        voice_rows = r.json()
        voice_names = {item.get("name") for item in voice_rows}
        if {"NARRATOR", "阿景"} - voice_names:
            raise TestFailure(f"Expected NARRATOR and 阿景 voices, got {sorted(voice_names)}")
        for item in voice_rows:
            if item.get("name") in {"NARRATOR", "阿景"} and item.get("has_voice_config"):
                raise TestFailure(f"Unset voice should not count as saved config: {item}")

        r = get("/api/chunks")
        assert_status(r, 200, "read chunks after tagged import")
        chunks = r.json()
        ajing_index = next((idx for idx, chunk in enumerate(chunks) if chunk.get("speaker") == "阿景"), None)
        if ajing_index is None:
            raise TestFailure(f"Could not find 阿景 chunk in {chunks}")

        r = post(f"/api/chunks/{ajing_index}", json={
            "speaker": "阿景",
            "text": "我已经回来了。",
            "instruct": "低声确认",
        })
        assert_status(r, 200, "update tagged chunk")
        updated = r.json()
        if updated.get("_script_entry_count", 0) < 1:
            raise TestFailure(f"Chunk update did not sync script entries: {updated}")

        r = get("/api/annotated_script/tagged")
        assert_status(r, 200, "export tagged script after chunk edit")
        exported_tagged = r.json().get("content") or ""
        if "<阿景:>我已经回来了。 {instruct=低声确认}" not in exported_tagged:
            raise TestFailure(f"Tagged export did not reflect chunk edit:\n{exported_tagged}")

        r = get("/api/annotated_script")
        assert_status(r, 200, "read annotated script after chunk edit")
        script_entries = r.json()
        if not any(
            entry.get("speaker") == "阿景"
            and entry.get("text") == "我已经回来了。"
            and entry.get("instruct") == "低声确认"
            for entry in script_entries
        ):
            raise TestFailure(f"annotated_script.json did not reflect chunk edit: {script_entries}")

        r = post("/api/characters", json={
            "characters": [
                {
                    "name": "萧景",
                    "aliases": ["阿景"],
                    "traits": "禁军旧部，谨慎克制",
                    "voice_profile": "青年男声，低沉克制",
                    "confidence": 0.9,
                }
            ],
            "narrator_style": "稳重旁白",
            "genre": "古风",
            "key_terms": ["赦书"],
            "normalize_script_speakers": True,
        })
        assert_status(r, 200, "save character pool")
        saved = r.json()
        if saved.get("chunk_speaker_updates", 0) < 1 or saved.get("script_speaker_updates", 0) < 1:
            raise TestFailure(f"Character save did not normalize script/chunks: {saved}")
        if saved.get("voice_config_updates", 0):
            raise TestFailure(f"Character save should not migrate unsaved voice config: {saved}")

        r = get("/api/chunks")
        assert_status(r, 200, "read chunks after character normalization")
        chunks = r.json()
        chunk_speakers = {chunk.get("speaker") for chunk in chunks}
        if "阿景" in chunk_speakers or "萧景" not in chunk_speakers:
            raise TestFailure(f"Chunks were not normalized to 萧景: {chunks}")

        r = get("/api/annotated_script")
        assert_status(r, 200, "read script after character normalization")
        script_entries = r.json()
        script_speakers = {entry.get("speaker") for entry in script_entries}
        if "阿景" in script_speakers or "萧景" not in script_speakers:
            raise TestFailure(f"Script entries were not normalized to 萧景: {script_entries}")

        r = get("/api/annotated_script/tagged")
        assert_status(r, 200, "export tagged script after character normalization")
        exported_tagged = r.json().get("content") or ""
        if "<萧景:>" not in exported_tagged or "<阿景:>" in exported_tagged:
            raise TestFailure(f"Tagged export did not use canonical speaker:\n{exported_tagged}")

        r = get("/api/voices")
        assert_status(r, 200, "read voices after character normalization")
        voices = {item.get("name"): item.get("config") or {} for item in r.json()}
        if "阿景" in voices or "萧景" not in voices:
            raise TestFailure(f"Voices were not migrated to 萧景: {voices}")
        if voices["萧景"]:
            raise TestFailure(f"Unset canonical speaker should not get an implicit voice config: {voices['萧景']}")

        r = get("/api/characters")
        assert_status(r, 200, "read character pool after save")
        characters = {item.get("name"): item for item in r.json().get("characters", [])}
        xiao_jing = characters.get("萧景")
        if (
            not xiao_jing
            or "阿景" not in (xiao_jing.get("aliases") or [])
            or "禁军旧部" not in (xiao_jing.get("traits") or "")
            or "青年男声" not in (xiao_jing.get("voice_profile") or "")
        ):
            raise TestFailure(f"Character pool did not persist canonical alias: {characters}")
    finally:
        if original_book_id:
            try:
                post("/api/books/select", json={"book_id": original_book_id})
            except Exception:
                pass
        if temp_book_id:
            try:
                delete(f"/api/books/{temp_book_id}")
            except Exception:
                pass


def test_tagged_chapter_replace_preserves_other_chapters():
    """Replacing one chapter's tagged script should not disturb existing chapter assets."""
    skip_if_generation_locked()
    original_book_id = ""
    temp_book_id = ""

    current = get("/api/books/current")
    assert_status(current, 200, "read current book before chapter replace test")
    original_book_id = current.json().get("id") or ""

    try:
        title = f"codex_api_test_chapter_replace_{int(time.time() * 1000)}"
        r = post("/api/books", json={"title": title})
        assert_status(r, 200, "create temporary book for chapter replace test")
        temp_book_id = (r.json().get("book") or {}).get("id") or ""
        if not temp_book_id:
            raise TestFailure(f"Temporary book response did not include an id: {r.json()}")

        source_text = (
            "第一章 雨夜\n"
            "雨落在城墙上。\n"
            "阿景说：先走。\n\n"
            "第二章 晨钟\n"
            "钟声从山门传来。\n"
            "阿宁说：我留下。\n"
        )
        files = {
            "file": (
                f"{title}.txt",
                io.BytesIO(source_text.encode("utf-8")),
                "text/plain",
            )
        }
        r = post("/api/upload", files=files)
        assert_status(r, 200, "upload source for chapter replace test")
        chapters = ((r.json().get("chapters") or {}).get("chapters") or [])
        if len(chapters) != 2:
            raise TestFailure(f"Expected 2 chapters after upload, got {chapters}")
        first_chapter_id = chapters[0]["chapter_id"]
        second_chapter_id = chapters[1]["chapter_id"]

        tagged_content = (
            f"# [{first_chapter_id}] {chapters[0].get('title') or ''}\n"
            "<旁白:>雨落在城墙上。 {instruct=沉稳旁白}\n"
            "<阿景:>先走。 {instruct=低声催促}\n"
            f"# [{second_chapter_id}] {chapters[1].get('title') or ''}\n"
            "<旁白:>钟声从山门传来。 {instruct=清晨叙事}\n"
            "<阿宁:>我留下。 {instruct=平静坚定}\n"
        )
        r = post("/api/annotated_script/tagged", json={
            "content": tagged_content,
            "replace_scope": "all",
        })
        assert_status(r, 200, "import full tagged script before chapter replace")
        first_import = r.json()
        if first_import.get("status") != "imported":
            raise TestFailure(f"Initial tagged import failed: {first_import}")

        r = post("/api/characters", json={
            "characters": [
                {
                    "name": "萧景",
                    "aliases": ["阿景"],
                    "traits": "青年男声，低沉克制",
                    "confidence": 0.95,
                },
                {
                    "name": "宁婉",
                    "aliases": ["阿宁"],
                    "traits": "青年女声，清冷平稳",
                    "confidence": 0.9,
                },
            ],
            "narrator_style": "沉稳旁白",
            "genre": "古风",
            "key_terms": ["晨钟"],
            "normalize_script_speakers": True,
        })
        assert_status(r, 200, "save character pool before chapter replace")

        r = post("/api/save_voice_config", json={
            "NARRATOR": {
                "type": "custom",
                "voice": "Ryan",
                "character_style": "保留旁白音色",
                "seed": "111",
            },
            "萧景": {
                "type": "custom",
                "voice": "Ryan",
                "character_style": "保留萧景音色",
                "seed": "222",
            },
            "宁婉": {
                "type": "custom",
                "voice": "Ryan",
                "character_style": "保留宁婉音色",
                "seed": "333",
            },
        })
        assert_status(r, 200, "save voice config before chapter replace")

        r = get("/api/annotated_script")
        assert_status(r, 200, "read script before chapter replace")
        before_script = r.json()
        first_before = [entry for entry in before_script if entry.get("chapter_id") == first_chapter_id]
        second_before = [entry for entry in before_script if entry.get("chapter_id") == second_chapter_id]
        if not first_before or not second_before:
            raise TestFailure(f"Script entries missing before chapter replace: {before_script}")

        r = get("/api/chunks")
        assert_status(r, 200, "read chunks before chapter replace")
        chunks_before = r.json()
        first_chunks_before = [chunk for chunk in chunks_before if chunk.get("chapter_id") == first_chapter_id]
        second_chunks_before = [chunk for chunk in chunks_before if chunk.get("chapter_id") == second_chapter_id]
        if not first_chunks_before or not second_chunks_before:
            raise TestFailure(f"Chunks missing before chapter replace: {chunks_before}")

        r = get(f"/api/script_issues?chapter_id={first_chapter_id}")
        assert_status(r, 200, "read first chapter issues before replace")
        first_issues_before = r.json()
        r = get(f"/api/script_issues?chapter_id={second_chapter_id}")
        assert_status(r, 200, "read second chapter issues before replace")
        second_issues_before = r.json()
        if first_issues_before.get("chapter_id") != first_chapter_id or second_issues_before.get("chapter_id") != second_chapter_id:
            raise TestFailure(f"Script issue reports missing before replace: {first_issues_before}, {second_issues_before}")

        replacement = (
            "<旁白:>晨钟第二遍响起。 {instruct=清晨叙事}\n"
            "<阿宁:>我会守到天亮。 {instruct=平静坚定}\n"
        )
        r = post("/api/annotated_script/tagged", json={
            "content": replacement,
            "chapter_id": second_chapter_id,
            "replace_scope": "chapter",
        })
        assert_status(r, 200, "replace second chapter tagged script")
        replaced = r.json()
        if replaced.get("status") != "imported" or replaced.get("chapter_id") != second_chapter_id:
            raise TestFailure(f"Chapter replace did not report selected chapter: {replaced}")
        if replaced.get("imported_entries") != 2:
            raise TestFailure(f"Chapter replace imported wrong entry count: {replaced}")

        r = get("/api/annotated_script")
        assert_status(r, 200, "read script after chapter replace")
        after_script = r.json()
        first_after = [entry for entry in after_script if entry.get("chapter_id") == first_chapter_id]
        second_after = [entry for entry in after_script if entry.get("chapter_id") == second_chapter_id]
        if first_after != first_before:
            raise TestFailure(f"First chapter script changed during second chapter replace:\nBefore={first_before}\nAfter={first_after}")
        second_texts = [entry.get("text") for entry in second_after]
        if second_texts != ["晨钟第二遍响起。", "我会守到天亮。"]:
            raise TestFailure(f"Second chapter script was not replaced cleanly: {second_after}")
        if any(entry.get("text") == "钟声从山门传来。" for entry in after_script):
            raise TestFailure(f"Old second chapter entry remained after replace: {after_script}")

        r = get("/api/chunks")
        assert_status(r, 200, "read chunks after chapter replace")
        chunks_after = r.json()
        first_chunks_after = [chunk for chunk in chunks_after if chunk.get("chapter_id") == first_chapter_id]
        second_chunks_after = [chunk for chunk in chunks_after if chunk.get("chapter_id") == second_chapter_id]
        comparable_first_before = [
            {
                "speaker": chunk.get("speaker"),
                "text": chunk.get("text"),
                "instruct": chunk.get("instruct"),
                "chapter_id": chunk.get("chapter_id"),
                "chapter_title": chunk.get("chapter_title"),
                "status": chunk.get("status"),
                "audio_path": chunk.get("audio_path"),
            }
            for chunk in first_chunks_before
        ]
        comparable_first_after = [
            {
                "speaker": chunk.get("speaker"),
                "text": chunk.get("text"),
                "instruct": chunk.get("instruct"),
                "chapter_id": chunk.get("chapter_id"),
                "chapter_title": chunk.get("chapter_title"),
                "status": chunk.get("status"),
                "audio_path": chunk.get("audio_path"),
            }
            for chunk in first_chunks_after
        ]
        if comparable_first_after != comparable_first_before:
            raise TestFailure(f"First chapter chunks changed during second chapter replace:\nBefore={comparable_first_before}\nAfter={comparable_first_after}")
        if [chunk.get("text") for chunk in second_chunks_after] != ["晨钟第二遍响起。", "我会守到天亮。"]:
            raise TestFailure(f"Second chapter chunks were not rebuilt from replacement: {second_chunks_after}")
        if any(chunk.get("text") == "钟声从山门传来。" for chunk in chunks_after):
            raise TestFailure(f"Old second chapter chunk remained after replace: {chunks_after}")

        r = get(f"/api/script_issues?chapter_id={first_chapter_id}")
        assert_status(r, 200, "read first chapter issues after replace")
        first_issues_after = r.json()
        if first_issues_after.get("chapter_id") != first_chapter_id or first_issues_after.get("issue_count") != first_issues_before.get("issue_count"):
            raise TestFailure(f"First chapter issue report was disturbed: before={first_issues_before}, after={first_issues_after}")

        r = get(f"/api/script_issues?chapter_id={second_chapter_id}")
        assert_status(r, 200, "read second chapter issues after replace")
        second_issues_after = r.json()
        if second_issues_after.get("entry_count") != 2:
            raise TestFailure(f"Second chapter issue report did not update entry count: {second_issues_after}")
        if second_issues_after.get("source_coverage_ratio") is None:
            raise TestFailure(f"Second chapter issue report did not include source coverage: {second_issues_after}")
        if "source_uncovered_samples" not in second_issues_after:
            raise TestFailure(f"Second chapter issue report did not include uncovered source samples: {second_issues_after}")
        if "source_coverage_category_summary" not in second_issues_after:
            raise TestFailure(f"Second chapter issue report did not include coverage categories: {second_issues_after}")

        r = get("/api/script_progress")
        assert_status(r, 200, "read script progress after replace")
        progress = r.json()
        progress_by_id = {item.get("chapter_id"): item for item in progress.get("chapters") or []}
        if progress_by_id.get(first_chapter_id, {}).get("status") != "generated":
            raise TestFailure(f"First chapter progress should remain generated: {progress}")
        if progress_by_id.get(second_chapter_id, {}).get("status") != "generated":
            raise TestFailure(f"Second chapter progress should remain generated after manual replace: {progress}")
        if progress_by_id.get(second_chapter_id, {}).get("entry_count") != 2:
            raise TestFailure(f"Second chapter progress did not reflect replacement entry count: {progress}")

        r = get("/api/characters")
        assert_status(r, 200, "read character pool after chapter replace")
        characters = {item.get("name"): item for item in r.json().get("characters", [])}
        if not {"萧景", "宁婉"}.issubset(characters):
            raise TestFailure(f"Character pool was disturbed by chapter replace: {characters}")

        r = get("/api/voices")
        assert_status(r, 200, "read voices after chapter replace")
        voices = {item.get("name"): item.get("config") or {} for item in r.json()}
        if voices.get("萧景", {}).get("seed") != "222" or voices.get("宁婉", {}).get("seed") != "333":
            raise TestFailure(f"Voice config was disturbed by chapter replace: {voices}")

        r = get(f"/api/annotated_script/tagged?chapter_id={first_chapter_id}")
        assert_status(r, 200, "export first chapter tagged after replace")
        first_tagged = r.json().get("content") or ""
        if "<萧景:>先走。 {instruct=低声催促}" not in first_tagged:
            raise TestFailure(f"First chapter tagged export was disturbed:\n{first_tagged}")

        r = get(f"/api/annotated_script/tagged?chapter_id={second_chapter_id}")
        assert_status(r, 200, "export second chapter tagged after replace")
        second_tagged = r.json().get("content") or ""
        if "<宁婉:>我会守到天亮。 {instruct=平静坚定}" not in second_tagged or "我留下" in second_tagged:
            raise TestFailure(f"Second chapter tagged export did not reflect replacement:\n{second_tagged}")
    finally:
        if original_book_id:
            try:
                post("/api/books/select", json={"book_id": original_book_id})
            except Exception:
                pass
        if temp_book_id:
            try:
                delete(f"/api/books/{temp_book_id}")
            except Exception:
                pass


def test_character_book_json_import_syncs_script_and_voices():
    """LLM-style character_book JSON import should drive local speaker and voice sync."""
    skip_if_generation_locked()
    original_book_id = ""
    temp_book_id = ""

    current = get("/api/books/current")
    assert_status(current, 200, "read current book before character import test")
    original_book_id = current.json().get("id") or ""

    try:
        title = f"codex_api_test_character_import_{int(time.time() * 1000)}"
        r = post("/api/books", json={"title": title})
        assert_status(r, 200, "create temporary book for character import test")
        temp_book_id = (r.json().get("book") or {}).get("id") or ""
        if not temp_book_id:
            raise TestFailure(f"Temporary book response did not include an id: {r.json()}")

        source_text = (
            "第一章 雨声\n"
            "雨声遮住脚步。\n"
            "阿景说：跟上。\n"
        )
        files = {
            "file": (
                f"{title}.txt",
                io.BytesIO(source_text.encode("utf-8")),
                "text/plain",
            )
        }
        r = post("/api/upload", files=files)
        assert_status(r, 200, "upload source for character import test")
        chapters = ((r.json().get("chapters") or {}).get("chapters") or [])
        if len(chapters) != 1:
            raise TestFailure(f"Expected 1 chapter after upload, got {chapters}")
        chapter_id = chapters[0]["chapter_id"]

        tagged_content = (
            f"# [{chapter_id}] {chapters[0].get('title') or ''}\n"
            "<旁白:>雨声遮住脚步。 {instruct=沉稳旁白}\n"
            "<阿景:>跟上。 {instruct=低声催促}\n"
        )
        r = post("/api/annotated_script/tagged", json={
            "content": tagged_content,
            "replace_scope": "all",
        })
        assert_status(r, 200, "import tagged script before character import")

        r = post("/api/save_voice_config", json={
            "阿景": {
                "type": "custom",
                "voice": "Ryan",
                "character_style": "旧阿景音色",
                "seed": "777",
            }
        })
        assert_status(r, 200, "save alias voice config before character import")

        character_json = json.dumps({
            "characters": [
                {
                    "canonical": "萧景",
                    "aliases": ["阿景", "景哥"],
                    "traits": "禁军旧部，谨慎克制",
                    "voice_profile": "青年男声，低沉克制",
                    "confidence": 0.96,
                }
            ],
            "narrator_style": "沉稳克制",
            "genre": "古风",
            "key_terms": ["赦书"],
        }, ensure_ascii=False)

        r = post("/api/characters/import", json={
            "content": character_json,
            "merge": True,
            "dry_run": True,
        })
        assert_status(r, 200, "preview character book import")
        preview = r.json()
        if preview.get("status") != "dry_run" or preview.get("imported_count") != 1 or preview.get("final_count") != 1:
            raise TestFailure(f"Character import preview counts are wrong: {preview}")

        r = post("/api/characters/import", json={
            "content": f"```json\n{character_json}\n```",
            "merge": True,
            "normalize_script_speakers": True,
        })
        assert_status(r, 200, "import character book")
        imported = r.json()
        if imported.get("status") != "imported" or imported.get("total") != 1:
            raise TestFailure(f"Character import did not persist expected pool: {imported}")
        if imported.get("script_speaker_updates", 0) < 1 or imported.get("chunk_speaker_updates", 0) < 1:
            raise TestFailure(f"Character import did not normalize script/chunks: {imported}")
        if imported.get("voice_config_updates", 0) < 1:
            raise TestFailure(f"Character import did not migrate alias voice config: {imported}")

        r = get("/api/annotated_script")
        assert_status(r, 200, "read script after character import")
        script_entries = r.json()
        speakers = {entry.get("speaker") for entry in script_entries}
        if "阿景" in speakers or "萧景" not in speakers:
            raise TestFailure(f"Script speakers were not normalized by character import: {script_entries}")

        r = get("/api/chunks")
        assert_status(r, 200, "read chunks after character import")
        chunks = r.json()
        chunk_speakers = {chunk.get("speaker") for chunk in chunks}
        if "阿景" in chunk_speakers or "萧景" not in chunk_speakers:
            raise TestFailure(f"Chunk speakers were not normalized by character import: {chunks}")

        r = get("/api/characters")
        assert_status(r, 200, "read character pool after import")
        characters = {item.get("name"): item for item in r.json().get("characters", [])}
        xiao_jing = characters.get("萧景")
        if (
            not xiao_jing
            or "阿景" not in (xiao_jing.get("aliases") or [])
            or "禁军旧部" not in (xiao_jing.get("traits") or "")
            or "青年男声" not in (xiao_jing.get("voice_profile") or "")
        ):
            raise TestFailure(f"Imported character pool is wrong: {characters}")

        r = get("/api/voices")
        assert_status(r, 200, "read voices after character import")
        voices = {item.get("name"): item.get("config") or {} for item in r.json()}
        if "阿景" in voices or voices.get("萧景", {}).get("seed") != "777":
            raise TestFailure(f"Voice config alias was not migrated to canonical speaker: {voices}")
        if voices.get("萧景", {}).get("character_style") != "旧阿景音色":
            raise TestFailure(f"Existing voice style should not be overwritten on import: {voices}")

        r = post("/api/characters/apply_voice_style", json={"mode": "overwrite", "names": ["萧景"]})
        assert_status(r, 200, "apply imported voice_profile to voice config")
        r = get("/api/voices")
        assert_status(r, 200, "read voices after applying imported voice_profile")
        voices = {item.get("name"): item.get("config") or {} for item in r.json()}
        style = voices.get("萧景", {}).get("character_style") or ""
        if "青年男声" not in style or "禁军旧部" in style or voices.get("萧景", {}).get("seed") != "777":
            raise TestFailure(f"voice_profile was not applied cleanly to voice config: {voices}")

        r = get(f"/api/script_issues?chapter_id={chapter_id}")
        assert_status(r, 200, "read script issues after character import")
        issues = r.json()
        if issues.get("unknown_speaker_count", 0) != 0:
            raise TestFailure(f"Character import should clear unknown speaker warnings: {issues}")
    finally:
        if original_book_id:
            try:
                post("/api/books/select", json={"book_id": original_book_id})
            except Exception:
                pass
        if temp_book_id:
            try:
                delete(f"/api/books/{temp_book_id}")
            except Exception:
                pass


def test_character_pool_merges_multiple_unknown_speakers():
    """Saving one character with multiple aliases should normalize all unknown speakers."""
    skip_if_generation_locked()
    original_book_id = ""
    temp_book_id = ""

    current = get("/api/books/current")
    assert_status(current, 200, "read current book before multi-alias character test")
    original_book_id = current.json().get("id") or ""

    try:
        title = f"codex_api_test_character_multi_alias_{int(time.time() * 1000)}"
        r = post("/api/books", json={"title": title})
        assert_status(r, 200, "create temporary book for multi-alias character test")
        temp_book_id = (r.json().get("book") or {}).get("id") or ""
        if not temp_book_id:
            raise TestFailure(f"Temporary book response did not include an id: {r.json()}")

        source_text = (
            "第一章 别名\n"
            "阿景、景哥和小景其实都是同一个人。\n"
        )
        r = post("/api/upload", files={
            "file": (
                f"{title}.txt",
                io.BytesIO(source_text.encode("utf-8")),
                "text/plain",
            )
        })
        assert_status(r, 200, "upload source for multi-alias character test")
        chapters = ((r.json().get("chapters") or {}).get("chapters") or [])
        if len(chapters) != 1:
            raise TestFailure(f"Expected 1 chapter after upload, got {chapters}")
        chapter_id = chapters[0]["chapter_id"]

        tagged_content = (
            f"# [{chapter_id}] {chapters[0].get('title') or ''}\n"
            "<旁白:>夜色压住城门。 {instruct=沉稳旁白}\n"
            "<阿景:>先走。 {instruct=低声}\n"
            "<景哥:>别回头。 {instruct=压低声音}\n"
            "<小景:>我知道。 {instruct=短促回应}\n"
        )
        r = post("/api/annotated_script/tagged", json={
            "content": tagged_content,
            "replace_scope": "all",
        })
        assert_status(r, 200, "import tagged script with multiple unknown speakers")

        r = get(f"/api/script_issues?chapter_id={chapter_id}")
        assert_status(r, 200, "read unknown speaker issues before alias merge")
        issues_before = r.json()
        if set(issues_before.get("unknown_speakers") or []) != {"阿景", "景哥", "小景"}:
            raise TestFailure(f"Expected three unknown speakers before merge: {issues_before}")

        r = post("/api/save_voice_config", json={
            "NARRATOR": {
                "type": "custom",
                "voice": "Ryan",
                "character_style": "旁白旧配置",
                "seed": "101",
            },
            "景哥": {
                "type": "custom",
                "voice": "Serena",
                "character_style": "旧景哥音色",
                "seed": "999",
            },
        })
        assert_status(r, 200, "save alias voice config before multi-alias merge")

        r = post("/api/characters", json={
            "characters": [
                {
                    "name": "萧景",
                    "aliases": ["阿景", "景哥", "小景"],
                    "traits": "禁军旧部，谨慎克制",
                    "voice_profile": "青年男声，低沉克制",
                    "confidence": 0.95,
                }
            ],
            "narrator_style": "沉稳旁白",
            "genre": "古风",
            "key_terms": ["赦书"],
            "normalize_script_speakers": True,
        })
        assert_status(r, 200, "save multi-alias character pool")
        saved = r.json()
        if saved.get("script_speaker_updates", 0) < 3 or saved.get("chunk_speaker_updates", 0) < 3:
            raise TestFailure(f"Saving multi-alias character did not normalize all speakers: {saved}")
        if saved.get("voice_config_updates", 0) < 1:
            raise TestFailure(f"Saving multi-alias character did not migrate alias voice config: {saved}")

        r = get("/api/annotated_script")
        assert_status(r, 200, "read script after multi-alias merge")
        script_entries = r.json()
        speakers = [entry.get("speaker") for entry in script_entries if entry.get("speaker") != "NARRATOR"]
        if speakers != ["萧景", "萧景", "萧景"]:
            raise TestFailure(f"Script speakers were not all normalized to 萧景: {script_entries}")

        r = get("/api/chunks")
        assert_status(r, 200, "read chunks after multi-alias merge")
        chunks = r.json()
        chunk_speakers = [chunk.get("speaker") for chunk in chunks if chunk.get("speaker") != "NARRATOR"]
        if chunk_speakers != ["萧景", "萧景", "萧景"]:
            raise TestFailure(f"Chunk speakers were not all normalized to 萧景: {chunks}")

        r = get("/api/characters")
        assert_status(r, 200, "read character pool after multi-alias merge")
        characters = {item.get("name"): item for item in r.json().get("characters", [])}
        xiao_jing = characters.get("萧景")
        if not xiao_jing or not {"阿景", "景哥", "小景"}.issubset(set(xiao_jing.get("aliases") or [])):
            raise TestFailure(f"Character aliases were not preserved: {characters}")

        r = get("/api/voices")
        assert_status(r, 200, "read voices after multi-alias merge")
        voices = {item.get("name"): item.get("config") or {} for item in r.json()}
        if any(alias in voices for alias in ("阿景", "景哥", "小景")):
            raise TestFailure(f"Alias voice configs should be migrated away: {voices}")
        if voices.get("萧景", {}).get("seed") != "999" or voices.get("萧景", {}).get("voice") != "Serena":
            raise TestFailure(f"Canonical voice config did not migrate from normalized alias config: {voices}")

        r = get(f"/api/script_issues?chapter_id={chapter_id}")
        assert_status(r, 200, "read issues after multi-alias merge")
        issues_after = r.json()
        if issues_after.get("unknown_speaker_count", 0) != 0:
            raise TestFailure(f"Unknown speaker warnings should be cleared after multi-alias merge: {issues_after}")
    finally:
        if original_book_id:
            try:
                post("/api/books/select", json={"book_id": original_book_id})
            except Exception:
                pass
        if temp_book_id:
            try:
                delete(f"/api/books/{temp_book_id}")
            except Exception:
                pass


def test_voice_metadata_keeps_alias_speakers_without_rewriting_script():
    """Voice cards should keep actual alias speakers separate when the script is not normalized."""
    skip_if_generation_locked()
    original_book_id = ""
    temp_book_id = ""

    current = get("/api/books/current")
    assert_status(current, 200, "read current book before alias voice metadata test")
    original_book_id = current.json().get("id") or ""

    try:
        title = f"codex_api_test_voice_alias_metadata_{int(time.time() * 1000)}"
        r = post("/api/books", json={"title": title})
        assert_status(r, 200, "create temporary book for alias voice metadata test")
        temp_book_id = (r.json().get("book") or {}).get("id") or ""
        if not temp_book_id:
            raise TestFailure(f"Temporary book response did not include an id: {r.json()}")

        source_text = (
            "第一章 旧称\n"
            "阿景与景哥其实是同一个人。\n"
        )
        r = post("/api/upload", files={
            "file": (
                f"{title}.txt",
                io.BytesIO(source_text.encode("utf-8")),
                "text/plain",
            )
        })
        assert_status(r, 200, "upload source for alias voice metadata test")
        chapters = ((r.json().get("chapters") or {}).get("chapters") or [])
        if len(chapters) != 1:
            raise TestFailure(f"Expected 1 chapter after upload, got {chapters}")
        chapter_id = chapters[0]["chapter_id"]

        tagged_content = (
            f"# [{chapter_id}] {chapters[0].get('title') or ''}\n"
            "<旁白:>夜雨未停。 {instruct=沉稳旁白}\n"
            "<阿景:>我先去。 {instruct=低声}\n"
            "<景哥:>别惊动他们。 {instruct=压低声音}\n"
        )
        r = post("/api/annotated_script/tagged", json={
            "content": tagged_content,
            "replace_scope": "all",
        })
        assert_status(r, 200, "import tagged script before alias voice metadata")

        r = post("/api/save_voice_config", json={
            "景哥": {
                "type": "custom",
                "voice": "Serena",
                "character_style": "旧景哥音色",
                "seed": "909",
            }
        })
        assert_status(r, 200, "save alias voice config before alias metadata")

        r = post("/api/characters", json={
            "characters": [
                {
                    "name": "萧景",
                    "aliases": ["阿景", "景哥"],
                    "traits": "禁军旧部，谨慎克制",
                    "voice_profile": "青年男声，低沉克制",
                    "confidence": 0.95,
                }
            ],
            "narrator_style": "沉稳旁白",
            "genre": "古风",
            "key_terms": ["赦书"],
            "normalize_script_speakers": False,
        })
        assert_status(r, 200, "save character pool without rewriting speakers")
        saved = r.json()
        if saved.get("script_speaker_updates") or saved.get("chunk_speaker_updates") or saved.get("voice_config_updates"):
            raise TestFailure(f"Character save should not rewrite speakers/config when normalize=false: {saved}")

        r = get("/api/annotated_script")
        assert_status(r, 200, "read script after non-normalizing character save")
        script_speakers = [entry.get("speaker") for entry in r.json()]
        if "阿景" not in script_speakers or "景哥" not in script_speakers or "萧景" in script_speakers:
            raise TestFailure(f"Script speakers should remain aliases: {script_speakers}")

        r = get("/api/voices")
        assert_status(r, 200, "read voices after non-normalizing alias character save")
        voice_items = {item.get("name"): item for item in r.json()}
        if not {"萧景", "阿景", "景哥"}.issubset(voice_items):
            raise TestFailure(f"Voice list should keep actual alias speakers separately: {voice_items}")
        if voice_items["景哥"].get("config", {}).get("seed") != "909" or voice_items["景哥"].get("config", {}).get("voice") != "Serena":
            raise TestFailure(f"Alias voice config should stay on 景哥: {voice_items['景哥']}")
        if voice_items["阿景"].get("config"):
            raise TestFailure(f"Unconfigured alias speaker 阿景 should remain missing: {voice_items['阿景']}")
        xiao_voice = voice_items["萧景"]
        if xiao_voice.get("config"):
            raise TestFailure(f"Canonical character should not inherit saved alias config: {xiao_voice}")
        aliases = set(xiao_voice.get("aliases") or [])
        if not {"阿景", "景哥"}.issubset(aliases):
            raise TestFailure(f"Voice metadata did not expose character aliases: {xiao_voice}")
        if not xiao_voice.get("has_character_book") or xiao_voice.get("source") != "character_book":
            raise TestFailure(f"Voice metadata should mark character-pool source: {xiao_voice}")

        r = get(f"/api/script_issues?chapter_id={chapter_id}")
        assert_status(r, 200, "read script issues after alias character pool save")
        issues = r.json()
        if issues.get("unknown_speaker_count", 0) != 0:
            raise TestFailure(f"Character aliases should clear unknown speaker warnings without rewriting script: {issues}")
    finally:
        if original_book_id:
            try:
                post("/api/books/select", json={"book_id": original_book_id})
            except Exception:
                pass
        if temp_book_id:
            try:
                delete(f"/api/books/{temp_book_id}")
            except Exception:
                pass


def test_character_book_voices_available_before_script():
    """Character pool entries should be configurable as voices before script generation."""
    skip_if_generation_locked()
    original_book_id = ""
    temp_book_id = ""

    current = get("/api/books/current")
    assert_status(current, 200, "read current book before character voices test")
    original_book_id = current.json().get("id") or ""

    try:
        title = f"codex_api_test_character_voices_{int(time.time() * 1000)}"
        r = post("/api/books", json={"title": title})
        assert_status(r, 200, "create temporary book for character voices test")
        temp_book_id = (r.json().get("book") or {}).get("id") or ""
        if not temp_book_id:
            raise TestFailure(f"Temporary book response did not include an id: {r.json()}")

        r = post("/api/characters/import", json={
            "content": json.dumps({
                "characters": [
                    {
                        "canonical": "萧景",
                        "aliases": ["阿景"],
                        "traits": "禁军旧部，谨慎克制",
                        "voice_profile": "青年男声，低沉克制",
                    },
                    {
                        "canonical": "宁婉",
                        "aliases": ["阿宁"],
                        "traits": "医女，冷静果决",
                        "voice_profile": "青年女声，清冷平稳",
                    },
                ],
                "narrator_style": "沉稳旁白",
                "genre": "古风",
                "key_terms": ["赦书"],
            }, ensure_ascii=False),
            "merge": True,
        })
        assert_status(r, 200, "import character book before script")
        imported = r.json()
        if imported.get("status") != "imported" or imported.get("total") != 2:
            raise TestFailure(f"Character import before script failed: {imported}")

        r = get("/api/annotated_script")
        if r.status_code != 404:
            raise TestFailure(f"Temporary book should not have a script yet, got {r.status_code}: {r.text[:300]}")
        r = get("/api/chunks")
        assert_status(r, 200, "read chunks before script")
        if r.json():
            raise TestFailure(f"Temporary book should not have chunks yet: {r.json()}")

        r = get("/api/voices")
        assert_status(r, 200, "read voices from character pool before script")
        voice_rows = r.json()
        voices = {item.get("name"): item.get("config") or {} for item in voice_rows}
        voice_statuses = {item.get("name"): item for item in voice_rows}
        expected = {"NARRATOR", "萧景", "宁婉"}
        if not expected.issubset(voices):
            raise TestFailure(f"Character pool voices missing before script. expected={expected}, got={voices}")
        for name in expected:
            row = voice_statuses.get(name) or {}
            if row.get("voice_config_status") != "missing" or row.get("has_voice_config") or row.get("has_custom_voice_config"):
                raise TestFailure(f"Unset character voice should remain missing for {name}: {row}")
            if voices.get(name):
                raise TestFailure(f"Unset character voice should expose an empty config for {name}: {voices[name]}")

        r = post("/api/save_voice_config", json={
            "萧景": {
                "type": "custom",
                "voice": "Serena",
                "character_style": "已手动配置萧景",
                "seed": "444",
            }
        })
        assert_status(r, 200, "save character voice before script")

        r = get("/api/voices")
        assert_status(r, 200, "read voices after manual pre-script config")
        voice_rows = r.json()
        voices = {item.get("name"): item.get("config") or {} for item in voice_rows}
        voice_statuses = {item.get("name"): item for item in voice_rows}
        if voices.get("萧景", {}).get("voice") != "Serena" or voices.get("萧景", {}).get("seed") != "444":
            raise TestFailure(f"Manual pre-script voice config was not preserved: {voices}")
        xiao_voice = voice_statuses.get("萧景") or {}
        if xiao_voice.get("voice_config_status") != "customized" or not xiao_voice.get("has_custom_voice_config"):
            raise TestFailure(f"Manual pre-script voice config should count as customized: {xiao_voice}")

        r = get("/api/characters")
        assert_status(r, 200, "read character cards after manual pre-script voice config")
        characters = {item.get("name"): item for item in r.json().get("characters", [])}
        xiao_jing = characters.get("萧景") or {}
        xiao_config = xiao_jing.get("config") or {}
        if xiao_config.get("voice") != "Serena" or xiao_config.get("seed") != "444" or not xiao_jing.get("has_voice_config"):
            raise TestFailure(f"Character pool did not expose saved voice mapping: {xiao_jing}")
        if xiao_jing.get("voice_config_status") != "customized" or not xiao_jing.get("has_custom_voice_config"):
            raise TestFailure(f"Character pool should mark saved voice mapping as customized: {xiao_jing}")
    finally:
        if original_book_id:
            try:
                post("/api/books/select", json={"book_id": original_book_id})
            except Exception:
                pass
        if temp_book_id:
            try:
                delete(f"/api/books/{temp_book_id}")
            except Exception:
                pass


def test_character_pool_save_accepts_voice_config_inline():
    """Saving the character pool should persist voice mappings in the same request."""
    skip_if_generation_locked()
    original_book_id = ""
    temp_book_id = ""

    current = get("/api/books/current")
    assert_status(current, 200, "read current book before inline character voice test")
    original_book_id = current.json().get("id") or ""

    try:
        title = f"codex_api_test_character_inline_voice_{int(time.time() * 1000)}"
        r = post("/api/books", json={"title": title})
        assert_status(r, 200, "create temporary book for inline character voice test")
        temp_book_id = (r.json().get("book") or {}).get("id") or ""
        if not temp_book_id:
            raise TestFailure(f"Temporary book response did not include an id: {r.json()}")

        source_text = (
            "第一章 雨夜\n"
            "阿景说：先走。\n"
        )
        r = post("/api/upload", files={
            "file": (
                f"{title}.txt",
                io.BytesIO(source_text.encode("utf-8")),
                "text/plain",
            )
        })
        assert_status(r, 200, "upload source for inline character voice test")
        chapters = ((r.json().get("chapters") or {}).get("chapters") or [])
        if len(chapters) != 1:
            raise TestFailure(f"Expected 1 chapter after upload, got {chapters}")
        chapter_id = chapters[0]["chapter_id"]

        r = post("/api/annotated_script/tagged", json={
            "content": (
                f"# [{chapter_id}] {chapters[0].get('title') or ''}\n"
                "<旁白:>雨声压住脚步。 {instruct=沉稳旁白}\n"
                "<阿景:>先走。 {instruct=低声催促}\n"
            ),
            "replace_scope": "all",
        })
        assert_status(r, 200, "import tagged script before inline character voice save")

        r = post("/api/characters", json={
            "characters": [
                {
                    "name": "萧景",
                    "aliases": ["阿景"],
                    "traits": "禁军旧部，谨慎克制",
                    "voice_profile": "青年男声，低沉克制",
                    "confidence": 0.9,
                }
            ],
            "narrator_style": "稳重旁白",
            "genre": "古风",
            "key_terms": ["赦书"],
            "normalize_script_speakers": True,
            "voice_config": {
                "阿景": {
                    "type": "custom",
                    "voice": "Serena",
                    "character_style": "手动映射的阿景音色",
                    "seed": "606",
                }
            },
        })
        assert_status(r, 200, "save character pool with inline voice config")
        saved = r.json()
        if saved.get("status") != "saved" or saved.get("voice_config_saved", 0) != 1:
            raise TestFailure(f"Inline voice config was not reported as saved: {saved}")
        if "萧景" not in (saved.get("voice_config_saved_names") or []):
            raise TestFailure(f"Inline alias voice config should be saved under canonical speaker: {saved}")
        if saved.get("script_speaker_updates", 0) < 1 or saved.get("chunk_speaker_updates", 0) < 1:
            raise TestFailure(f"Inline character save did not normalize script/chunks: {saved}")

        r = get("/api/voices")
        assert_status(r, 200, "read voices after inline character voice save")
        voice_rows = r.json()
        voices = {item.get("name"): item.get("config") or {} for item in voice_rows}
        voice_statuses = {item.get("name"): item for item in voice_rows}
        if "阿景" in voices or "萧景" not in voices:
            raise TestFailure(f"Voice list did not canonicalize inline alias mapping: {voices}")
        xiao_config = voices.get("萧景") or {}
        if (
            xiao_config.get("voice") != "Serena"
            or xiao_config.get("seed") != "606"
            or xiao_config.get("character_style") != "手动映射的阿景音色"
        ):
            raise TestFailure(f"Inline voice mapping was not persisted: {voices}")
        xiao_voice = voice_statuses.get("萧景") or {}
        if xiao_voice.get("voice_config_status") != "customized" or not xiao_voice.get("has_custom_voice_config"):
            raise TestFailure(f"Inline voice mapping should count as customized: {xiao_voice}")

        r = get("/api/characters")
        assert_status(r, 200, "read characters after inline voice save")
        characters = {item.get("name"): item for item in r.json().get("characters", [])}
        xiao_jing = characters.get("萧景") or {}
        config = xiao_jing.get("config") or {}
        if config.get("voice") != "Serena" or not xiao_jing.get("has_voice_config"):
            raise TestFailure(f"Character card did not expose inline voice mapping: {xiao_jing}")
        if xiao_jing.get("voice_config_status") != "customized" or not xiao_jing.get("has_custom_voice_config"):
            raise TestFailure(f"Character card should mark inline voice mapping as customized: {xiao_jing}")
    finally:
        if original_book_id:
            try:
                post("/api/books/select", json={"book_id": original_book_id})
            except Exception:
                pass
        if temp_book_id:
            try:
                delete(f"/api/books/{temp_book_id}")
            except Exception:
                pass


def test_generate_script_characters_dry_run():
    """Generation dry-runs should estimate work without creating script outputs."""
    skip_if_generation_locked()
    original_book_id = ""
    temp_book_id = ""

    def assert_estimate(data, expected_breakdown, expected_calls, expected_target_ids, label):
        estimate = data.get("estimate") or {}
        if estimate.get("breakdown") != expected_breakdown:
            raise TestFailure(f"{label} returned wrong estimate breakdown: {estimate}")
        if estimate.get("estimated_llm_calls") != expected_calls:
            raise TestFailure(f"{label} returned wrong LLM call estimate: {estimate}")
        if estimate.get("retry_call_ceiling") != expected_calls * 3:
            raise TestFailure(f"{label} returned wrong retry ceiling: {estimate}")
        if estimate.get("target_chapter_ids") != expected_target_ids:
            raise TestFailure(f"{label} returned wrong target chapter ids: {estimate}")
        if estimate.get("output_token_budget") is None or estimate.get("output_token_budget") < 0:
            raise TestFailure(f"{label} did not return an output token budget: {estimate}")

    current = get("/api/books/current")
    assert_status(current, 200, "read current book before characters dry-run test")
    original_book_id = current.json().get("id") or ""

    try:
        title = f"codex_api_test_characters_dry_run_{int(time.time() * 1000)}"
        r = post("/api/books", json={"title": title})
        assert_status(r, 200, "create temporary book for characters dry-run test")
        temp_book_id = (r.json().get("book") or {}).get("id") or ""
        if not temp_book_id:
            raise TestFailure(f"Temporary book response did not include an id: {r.json()}")

        source_text = (
            "第一章 雨夜\n"
            "雨落在城门上。\n"
            "阿景说：先走。\n\n"
            "第二章 晨钟\n"
            "钟声从山门传来。\n"
            "阿宁说：我留下。\n"
        )
        files = {
            "file": (
                f"{title}.txt",
                io.BytesIO(source_text.encode("utf-8")),
                "text/plain",
            )
        }
        r = post("/api/upload", files=files)
        assert_status(r, 200, "upload source for characters dry-run test")
        chapters = ((r.json().get("chapters") or {}).get("chapters") or [])
        if len(chapters) != 2:
            raise TestFailure(f"Expected 2 chapters after upload, got {chapters}")

        r = post("/api/generate_script", json={"mode": "characters", "dry_run": True})
        assert_status(r, 200, "characters-only dry-run")
        data = r.json()
        if data.get("status") != "dry_run":
            raise TestFailure(f"Expected dry_run status, got {data}")
        if data.get("engine") != "character_pipeline" or data.get("mode") != "characters":
            raise TestFailure(f"Characters dry-run returned wrong mode/engine: {data}")
        if data.get("chapter_count") != 2:
            raise TestFailure(f"Characters dry-run returned wrong chapter count: {data}")
        if data.get("selected_chapter_ids") != []:
            raise TestFailure(f"Full-book characters dry-run should not select specific chapters: {data}")
        if data.get("enable_chapter_memory") is not False:
            raise TestFailure(f"Characters dry-run should keep chapter memory disabled: {data}")
        all_chapter_ids = [chapter["chapter_id"] for chapter in chapters]
        assert_estimate(
            data,
            {"character_analysis": 2, "script_annotation": 0, "chapter_memory": 0},
            2,
            all_chapter_ids,
            "full-book characters dry-run",
        )

        selected_id = chapters[0]["chapter_id"]
        r = post("/api/generate_script", json={
            "mode": "characters",
            "dry_run": True,
            "chapter_ids": [selected_id],
        })
        assert_status(r, 200, "single-chapter characters dry-run")
        data = r.json()
        if data.get("engine") != "character_pipeline" or data.get("selected_chapter_ids") != [selected_id]:
            raise TestFailure(f"Single-chapter characters dry-run returned wrong selection: {data}")
        assert_estimate(
            data,
            {"character_analysis": 1, "script_annotation": 0, "chapter_memory": 0},
            1,
            [selected_id],
            "single-chapter characters dry-run",
        )

        r = post("/api/generate_script", json={
            "reuse_character_book": True,
            "dry_run": True,
            "chapter_ids": [selected_id],
        })
        assert_status(r, 400, "reuse-character-book dry-run should reject empty character pool")
        if "人物池为空" not in r.text:
            raise TestFailure(f"Empty character pool reuse returned unclear error: {r.text[:300]}")

        r = post("/api/characters/import", json={
            "content": json.dumps({
                "characters": [
                    {
                        "canonical": "阿景",
                        "aliases": ["萧景"],
                        "traits": "禁军旧部，谨慎克制",
                        "voice_profile": "青年男声，低沉克制",
                        "confidence": 0.9,
                    }
                ],
                "narrator_style": "沉稳旁白",
                "genre": "古风",
                "key_terms": [],
            }, ensure_ascii=False),
            "merge": True,
            "normalize_script_speakers": True,
        })
        assert_status(r, 200, "seed character book before reuse dry-run")

        original_config = get("/api/config")
        assert_status(original_config, 200, "read config before enabling chapter memory")
        original_config = original_config.json()
        try:
            generation = {**(original_config.get("generation") or {}), "enable_chapter_memory": True}
            r = post("/api/config", json={
                "llm": original_config["llm"],
                "tts": original_config.get("tts", {"mode": "local", "url": "http://127.0.0.1:7860", "device": "auto"}),
                "prompts": original_config.get("prompts"),
                "generation": generation,
            })
            assert_status(r, 200, "enable chapter memory for dry-run plan")

            r = post("/api/generate_script", json={
                "reuse_character_book": True,
                "dry_run": True,
                "chapter_ids": [selected_id],
            })
            assert_status(r, 200, "script dry-run with chapter memory enabled")
            data = r.json()
            if data.get("engine") != "chapter_pipeline" or data.get("mode") != "script":
                raise TestFailure(f"Reuse-character-book dry-run returned wrong mode/engine: {data}")
            if data.get("reuse_character_book") is not True:
                raise TestFailure(f"Reuse-character-book dry-run did not preserve reuse flag: {data}")
            if data.get("enable_chapter_memory") is not True:
                raise TestFailure(f"Script dry-run should reflect enabled chapter memory: {data}")
            if data.get("selected_chapter_ids") != [selected_id]:
                raise TestFailure(f"Reuse-character-book dry-run returned wrong selection: {data}")
            assert_estimate(
                data,
                {"character_analysis": 0, "script_annotation": 1, "chapter_memory": 1},
                2,
                [selected_id],
                "script dry-run with chapter memory enabled",
            )

            generation["enable_chapter_memory"] = False
            r = post("/api/config", json={
                "llm": original_config["llm"],
                "tts": original_config.get("tts", {"mode": "local", "url": "http://127.0.0.1:7860", "device": "auto"}),
                "prompts": original_config.get("prompts"),
                "generation": generation,
            })
            assert_status(r, 200, "disable chapter memory for dry-run plan")

            r = post("/api/generate_script", json={
                "reuse_character_book": True,
                "dry_run": True,
                "chapter_ids": [selected_id],
            })
            assert_status(r, 200, "script dry-run with chapter memory disabled")
            data = r.json()
            if data.get("enable_chapter_memory") is not False:
                raise TestFailure(f"Script dry-run should preserve explicit chapter memory disable: {data}")
            assert_estimate(
                data,
                {"character_analysis": 0, "script_annotation": 1, "chapter_memory": 0},
                1,
                [selected_id],
                "script dry-run with chapter memory disabled",
            )

            generation["enable_chapter_memory"] = True
            r = post("/api/config", json={
                "llm": original_config["llm"],
                "tts": original_config.get("tts", {"mode": "local", "url": "http://127.0.0.1:7860", "device": "auto"}),
                "prompts": original_config.get("prompts"),
                "generation": generation,
            })
            assert_status(r, 200, "re-enable chapter memory for missing-only plan")

            r = post("/api/generate_script", json={
                "reuse_character_book": True,
                "missing_only": True,
                "chapter_ids": [selected_id],
                "dry_run": True,
            })
            assert_status(r, 200, "missing-only selected-chapter dry-run with existing character book")
            data = r.json()
            if data.get("selected_chapter_ids") != [selected_id]:
                raise TestFailure(f"Missing-only with explicit chapter ids should only target selected missing chapter: {data}")
            assert_estimate(
                data,
                {"character_analysis": 0, "script_annotation": 1, "chapter_memory": 1},
                2,
                [selected_id],
                "missing-only selected-chapter dry-run with chapter memory enabled",
            )

            r = post("/api/generate_script", json={
                "reuse_character_book": True,
                "missing_only": True,
                "dry_run": True,
            })
            assert_status(r, 200, "missing-only reuse-character-book dry-run with existing character book")
            data = r.json()
            if data.get("reuse_character_book") is not True or data.get("selected_chapter_ids") != all_chapter_ids:
                raise TestFailure(f"Missing-only reuse-character-book dry-run returned wrong plan: {data}")
            assert_estimate(
                data,
                {"character_analysis": 0, "script_annotation": 2, "chapter_memory": 2},
                4,
                all_chapter_ids,
                "missing-only script dry-run with chapter memory enabled",
            )

            r = post("/api/generate_script", json={
                "mode": "characters",
                "dry_run": True,
            })
            assert_status(r, 200, "characters dry-run with chapter memory config enabled")
            data = r.json()
            if data.get("enable_chapter_memory") is not False:
                raise TestFailure(f"Characters mode should ignore chapter memory config: {data}")
            assert_estimate(
                data,
                {"character_analysis": 2, "script_annotation": 0, "chapter_memory": 0},
                2,
                all_chapter_ids,
                "characters dry-run with chapter memory config enabled",
            )
        finally:
            post("/api/config", json={
                "llm": original_config["llm"],
                "tts": original_config.get("tts", {"mode": "local", "url": "http://127.0.0.1:7860", "device": "auto"}),
                "prompts": original_config.get("prompts"),
                "generation": original_config.get("generation"),
            })

        r = post("/api/generate_script", json={
            "mode": "characters",
            "reuse_character_book": True,
            "dry_run": True,
        })
        assert_status(r, 200, "characters dry-run should ignore reuse flag")
        data = r.json()
        if data.get("engine") != "character_pipeline" or data.get("reuse_character_book") is not False:
            raise TestFailure(f"Characters mode should ignore reuse_character_book: {data}")

        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        book_dir = os.path.join(repo_root, "books", temp_book_id)
        state_path = os.path.join(book_dir, "script_generation_state.json")
        character_analysis_state_path = os.path.join(book_dir, "character_analysis_state.json")
        with open(character_analysis_state_path, "w", encoding="utf-8") as f:
            json.dump({
                "status": "done",
                "updated_at": "2026-01-01T00:00:00+00:00",
                "chapters": {
                    chapter["chapter_id"]: {
                        "chapter_id": chapter["chapter_id"],
                        "chapter_index": chapter.get("index"),
                        "chapter_title": chapter.get("title"),
                        "status": "done",
                        "characters": 1,
                        "updated_at": "2026-01-01T00:00:00+00:00",
                    }
                    for chapter in chapters
                },
            }, f, indent=2, ensure_ascii=False)
        with open(state_path, "w", encoding="utf-8") as f:
            json.dump({
                "engine": "chapter_pipeline",
                "mode": "script",
                "status": "script_package",
                "chapters": {
                    chapter["chapter_id"]: {
                        "chapter_id": chapter["chapter_id"],
                        "chapter_index": chapter.get("index"),
                        "chapter_title": chapter.get("title"),
                        "status": "missing",
                        "entry_count": 0,
                    }
                    for chapter in chapters
                },
            }, f, indent=2, ensure_ascii=False)

        r = get("/api/script_progress")
        assert_status(r, 200, "read progress after independent character analysis state")
        progress = r.json()
        summary = progress.get("summary") or {}
        if summary.get("generated_chapters") != 0 or summary.get("character_analyzed_chapters") != 2:
            raise TestFailure(f"Characters-only progress summary is wrong: {summary}")
        progress_by_id = {item.get("chapter_id"): item for item in progress.get("chapters") or []}
        analyzed = [progress_by_id.get(chapter["chapter_id"], {}).get("character_analyzed") for chapter in chapters]
        generated = [progress_by_id.get(chapter["chapter_id"], {}).get("generated") for chapter in chapters]
        if analyzed != [True, True] or generated != [False, False]:
            raise TestFailure(f"Characters-only chapter progress flags are wrong: {progress.get('chapters')}")

        completed_id = chapters[0]["chapter_id"]
        interrupted_id = chapters[1]["chapter_id"]
        script_path = os.path.join(book_dir, "annotated_script.json")
        chunks_path = os.path.join(book_dir, "chunks.json")
        completed_entry = {
            "chapter_id": completed_id,
            "chapter_index": chapters[0].get("index"),
            "chapter_title": chapters[0].get("title"),
            "speaker": "NARRATOR",
            "text": "第一章已完成",
            "instruct": "",
        }
        with open(script_path, "w", encoding="utf-8") as f:
            json.dump([completed_entry], f, indent=2, ensure_ascii=False)
        with open(chunks_path, "w", encoding="utf-8") as f:
            json.dump([{
                **completed_entry,
                "id": 0,
                "status": "pending",
                "audio_path": None,
            }], f, indent=2, ensure_ascii=False)
        with open(state_path, "w", encoding="utf-8") as f:
            json.dump({
                "engine": "chapter_pipeline",
                "mode": "script",
                "status": "running",
                "started_at": "2026-01-01T00:00:00+00:00",
                "finished_at": "",
                "chapters": {
                    completed_id: {
                        "chapter_id": completed_id,
                        "chapter_index": chapters[0].get("index"),
                        "chapter_title": chapters[0].get("title"),
                        "status": "done",
                        "entry_count": 1,
                    },
                    interrupted_id: {
                        "chapter_id": interrupted_id,
                        "chapter_index": chapters[1].get("index"),
                        "chapter_title": chapters[1].get("title"),
                        "status": "running",
                        "entry_count": 0,
                    },
                },
            }, f, indent=2, ensure_ascii=False)

        r = get("/api/script_progress")
        assert_status(r, 200, "read progress after interrupted script generation")
        progress = r.json()
        summary = progress.get("summary") or {}
        if summary.get("generated_chapters") != 1 or summary.get("interrupted_chapters") != 1:
            raise TestFailure(f"Interrupted progress summary is wrong: {summary}")
        progress_by_id = {item.get("chapter_id"): item for item in progress.get("chapters") or []}
        interrupted = progress_by_id.get(interrupted_id) or {}
        if interrupted.get("status") != "interrupted" or interrupted.get("interrupted") is not True:
            raise TestFailure(f"Interrupted chapter was not reported correctly: {progress.get('chapters')}")
        with open(state_path, "r", encoding="utf-8") as f:
            saved_state = json.load(f)
        saved_chapter = ((saved_state.get("chapters") or {}).get(interrupted_id) or {})
        if saved_state.get("status") != "interrupted" or saved_chapter.get("status") != "interrupted":
            raise TestFailure(f"Stale running state was not persisted as interrupted: {saved_state}")

        r = post("/api/generate_script", json={"missing_only": True, "dry_run": True})
        assert_status(r, 200, "missing-only dry-run should resume interrupted chapter")
        data = r.json()
        if data.get("selected_chapter_ids") != [interrupted_id]:
            raise TestFailure(f"Missing-only dry-run should target only interrupted chapter: {data}")

        with open(script_path, "w", encoding="utf-8") as f:
            json.dump([
                completed_entry,
                {
                    "chapter_id": interrupted_id,
                    "chapter_index": chapters[1].get("index"),
                    "chapter_title": chapters[1].get("title"),
                    "speaker": "NARRATOR",
                    "text": "第二章旧内容",
                    "instruct": "",
                },
            ], f, indent=2, ensure_ascii=False)
        with open(state_path, "w", encoding="utf-8") as f:
            json.dump({
                "engine": "chapter_pipeline",
                "mode": "script",
                "status": "running",
                "started_at": "2026-01-01T00:00:00+00:00",
                "finished_at": "",
                "chapters": {
                    completed_id: {
                        "chapter_id": completed_id,
                        "chapter_index": chapters[0].get("index"),
                        "chapter_title": chapters[0].get("title"),
                        "status": "done",
                        "entry_count": 1,
                    },
                    interrupted_id: {
                        "chapter_id": interrupted_id,
                        "chapter_index": chapters[1].get("index"),
                        "chapter_title": chapters[1].get("title"),
                        "status": "pending",
                        "entry_count": 0,
                    },
                },
            }, f, indent=2, ensure_ascii=False)
        r = post("/api/generate_script", json={"missing_only": True, "dry_run": True})
        assert_status(r, 200, "missing-only dry-run should not treat old entries as completed")
        data = r.json()
        if data.get("selected_chapter_ids") != [interrupted_id]:
            raise TestFailure(f"Old entries for an unfinished target chapter should not block resume: {data}")

        r = post("/api/generate_script", json={"mode": "invalid", "dry_run": True})
        assert_status(r, 400, "invalid generation mode should be rejected")

    finally:
        if original_book_id:
            try:
                post("/api/books/select", json={"book_id": original_book_id})
            except Exception:
                pass
        if temp_book_id:
            try:
                delete(f"/api/books/{temp_book_id}")
            except Exception:
                pass


def test_script_operations_story_bible_snapshot_and_actions():
    """Script operations endpoints should work from local files without calling the LLM."""
    skip_if_generation_locked()
    original_book_id = ""
    temp_book_id = ""

    current = get("/api/books/current")
    assert_status(current, 200, "read current book before script operations test")
    original_book_id = current.json().get("id") or ""

    try:
        title = f"codex_api_test_script_ops_{int(time.time() * 1000)}"
        r = post("/api/books", json={"title": title})
        assert_status(r, 200, "create temporary book for script operations test")
        temp_book_id = (r.json().get("book") or {}).get("id") or ""
        if not temp_book_id:
            raise TestFailure(f"Temporary book response did not include an id: {r.json()}")

        source_text = (
            "第一章 雨夜\n"
            "雨落在旧城墙上，檐下的灯笼被风吹得左右摇晃，巡夜人的脚步声一阵近一阵远。\n"
            "阿景攥着半枚铜符，记起白天在茶楼听见的密语，也记起宁婉临别时没有说完的那句话。\n"
            "远处钟声忽然响起，巷口的影子停住，像是在等他先开口，又像是在逼他回头。\n"
            "他知道这一夜不能再拖，必须把铜符送到山门，否则城中所有暗线都会暴露。\n"
        )
        files = {
            "file": (
                f"{title}.txt",
                io.BytesIO(source_text.encode("utf-8")),
                "text/plain",
            )
        }
        r = post("/api/upload", files=files)
        assert_status(r, 200, "upload source for script operations test")
        chapters = ((r.json().get("chapters") or {}).get("chapters") or [])
        if len(chapters) != 1:
            raise TestFailure(f"Expected 1 chapter after upload, got {chapters}")
        chapter_id = chapters[0]["chapter_id"]

        r = get("/api/story_bible")
        assert_status(r, 200, "read missing story bible before script import")
        if r.json().get("available") is not False:
            raise TestFailure(f"Story Bible should not exist before rebuild: {r.json()}")

        tagged_content = (
            f"# [{chapter_id}] {chapters[0].get('title') or ''}\n"
            "<旁白:>阿景在雨夜出城。 {instruct=低声压抑，节奏紧凑}\n"
        )
        r = post("/api/annotated_script/tagged", json={
            "content": tagged_content,
            "replace_scope": "all",
        })
        assert_status(r, 200, "import low-coverage tagged script")
        imported = r.json()
        if imported.get("status") != "imported" or imported.get("imported_entries") != 1:
            raise TestFailure(f"Tagged import did not create one entry: {imported}")

        r = get(f"/api/script_issues?chapter_id={chapter_id}")
        assert_status(r, 200, "read low-coverage script issues")
        issues = r.json()
        if issues.get("source_coverage_ratio") is None or float(issues.get("source_coverage_ratio") or 0) >= 0.55:
            raise TestFailure(f"Expected low source coverage ratio, got: {issues}")
        issue_codes = {item.get("code") for item in issues.get("issues") or []}
        if "low_source_coverage" not in issue_codes:
            raise TestFailure(f"Low source coverage warning was not recorded: {issues}")
        if "missing_source_information_points" not in issue_codes:
            raise TestFailure(f"Missing source information warning was not recorded: {issues}")
        if not issues.get("source_uncovered_samples"):
            raise TestFailure(f"Low source coverage report did not include uncovered samples: {issues}")
        if not issues.get("source_coverage_findings"):
            raise TestFailure(f"Low source coverage report did not include information-point findings: {issues}")
        if int(issues.get("source_critical_missing_count") or 0) <= 0:
            raise TestFailure(f"Low source coverage report did not mark high-weight missing points: {issues}")
        category_summary = issues.get("source_coverage_category_summary") or {}
        if not any((item or {}).get("missing") for item in category_summary.values()):
            raise TestFailure(f"Coverage category summary did not expose missing counts: {issues}")

        r = get("/api/script_progress")
        assert_status(r, 200, "read script progress with coverage")
        progress = r.json()
        summary = progress.get("summary") or {}
        if (
            summary.get("generated_chapters") != 1
            or summary.get("low_source_coverage_chapters") != 1
            or summary.get("critical_source_missing_chapters") != 1
        ):
            raise TestFailure(f"Progress summary did not expose coverage state: {summary}")
        chapter_progress = (progress.get("chapters") or [{}])[0]
        if chapter_progress.get("source_coverage_ratio") is None:
            raise TestFailure(f"Chapter progress did not include source coverage: {chapter_progress}")
        if int(chapter_progress.get("source_critical_missing_count") or 0) <= 0:
            raise TestFailure(f"Chapter progress did not include critical source missing count: {chapter_progress}")

        r = get("/api/script_action_items")
        assert_status(r, 200, "read script action items")
        actions = r.json()
        action_codes = {item.get("code") for item in actions.get("items") or []}
        if (
            "low_source_coverage" not in action_codes
            or "missing_source_information_points" not in action_codes
            or "missing_story_bible" not in action_codes
        ):
            raise TestFailure(f"Action items did not include coverage and Story Bible actions: {actions}")

        r = get("/api/script_outputs")
        assert_status(r, 200, "read script output files")
        outputs = r.json()
        files_by_path = {item.get("path"): item for item in outputs.get("files") or []}
        for rel_path in ("annotated_script.json", "chunks.json", "script_issues.json"):
            if not files_by_path.get(rel_path, {}).get("exists"):
                raise TestFailure(f"Expected generated file {rel_path} to exist: {outputs}")
        if files_by_path.get("story_bible.json", {}).get("exists"):
            raise TestFailure(f"Story Bible file should not exist before rebuild: {outputs}")

        r = get("/api/script_generation_snapshot")
        assert_status(r, 200, "read missing script generation snapshot")
        if r.json().get("available") is not False:
            raise TestFailure(f"Snapshot should be unavailable before state file is created: {r.json()}")

        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        book_dir = os.path.join(repo_root, "books", temp_book_id)
        snapshots_dir = os.path.join(book_dir, "generation_snapshots")
        snapshot_dir = os.path.join(snapshots_dir, "codex_test_snapshot")
        os.makedirs(snapshot_dir, exist_ok=True)
        snapshot_state = {
            "snapshot_id": "codex_test_snapshot",
            "status": "created",
            "created_at": "2026-01-01T00:00:00+00:00",
            "reason": "api test",
            "book_id": temp_book_id,
            "book_title": title,
            "selected_chapter_ids": [chapter_id],
            "snapshot_dir": "generation_snapshots/codex_test_snapshot",
            "files": [{"path": "annotated_script.json", "existed": True}],
        }
        with open(os.path.join(snapshots_dir, "latest.json"), "w", encoding="utf-8") as f:
            json.dump(snapshot_state, f, indent=2, ensure_ascii=False)

        r = get("/api/script_generation_snapshot")
        assert_status(r, 200, "read script generation snapshot")
        snapshot = r.json()
        if snapshot.get("available") is not True or (snapshot.get("snapshot") or {}).get("snapshot_id") != "codex_test_snapshot":
            raise TestFailure(f"Snapshot endpoint did not return latest snapshot state: {snapshot}")

        r = post("/api/story_bible/rebuild", json={})
        assert_status(r, 200, "rebuild story bible")
        rebuilt = r.json()
        if rebuilt.get("status") != "rebuilt" or rebuilt.get("chapter_count") != 1:
            raise TestFailure(f"Story Bible rebuild returned wrong summary: {rebuilt}")
        bible_chapters = ((rebuilt.get("story_bible") or {}).get("chapters") or [])
        if not bible_chapters or bible_chapters[0].get("source_coverage_ratio") is None:
            raise TestFailure(f"Story Bible did not include chapter coverage: {rebuilt}")
        if int(bible_chapters[0].get("source_critical_missing_count") or 0) <= 0:
            raise TestFailure(f"Story Bible did not include critical source missing count: {rebuilt}")

        r = get("/api/story_bible")
        assert_status(r, 200, "read rebuilt story bible")
        story = r.json()
        if story.get("available") is not True or (story.get("story_bible") or {}).get("chapter_count") != 1:
            raise TestFailure(f"Story Bible endpoint did not return rebuilt file: {story}")

        r = get("/api/script_outputs")
        assert_status(r, 200, "read script output files after Story Bible rebuild")
        outputs_after = r.json()
        files_after = {item.get("path"): item for item in outputs_after.get("files") or []}
        if not files_after.get("story_bible.json", {}).get("exists"):
            raise TestFailure(f"Story Bible file was not listed after rebuild: {outputs_after}")
    finally:
        if original_book_id:
            try:
                post("/api/books/select", json={"book_id": original_book_id})
            except Exception:
                pass
        if temp_book_id:
            try:
                delete(f"/api/books/{temp_book_id}")
            except Exception:
                pass


def test_review_script_dry_run():
    """Script review dry-run should plan lightweight review without calling the LLM."""
    skip_if_generation_locked()
    original_book_id = ""
    temp_book_id = ""

    current = get("/api/books/current")
    assert_status(current, 200, "read current book before review dry-run test")
    original_book_id = current.json().get("id") or ""

    try:
        title = f"codex_api_test_review_dry_run_{int(time.time() * 1000)}"
        r = post("/api/books", json={"title": title})
        assert_status(r, 200, "create temporary book for review dry-run test")
        temp_book_id = (r.json().get("book") or {}).get("id") or ""
        if not temp_book_id:
            raise TestFailure(f"Temporary book response did not include an id: {r.json()}")

        source_text = (
            "第一章 雨夜\n"
            "雨落在城门上。\n"
            "阿景说：走。\n"
        )
        files = {
            "file": (
                f"{title}.txt",
                io.BytesIO(source_text.encode("utf-8")),
                "text/plain",
            )
        }
        r = post("/api/upload", files=files)
        assert_status(r, 200, "upload source for review dry-run test")
        chapters = ((r.json().get("chapters") or {}).get("chapters") or [])
        if len(chapters) != 1:
            raise TestFailure(f"Expected 1 chapter after upload, got {chapters}")
        chapter_id = chapters[0]["chapter_id"]

        tagged_content = (
            f"# [{chapter_id}] {chapters[0].get('title') or ''}\n"
            "<旁白:>雨落在城门上。 {instruct=沉稳旁白}\n"
            "<阿景:>走。 {instruct=低声}\n"
        )
        r = post("/api/annotated_script/tagged", json={
            "content": tagged_content,
            "replace_scope": "all",
        })
        assert_status(r, 200, "import tagged script before review dry-run")

        r = get("/api/annotated_script")
        assert_status(r, 200, "read imported script before review dry-run")
        entries = r.json()
        if len(entries) != 2:
            raise TestFailure(f"Expected 2 imported script entries, got {entries}")

        r = post("/api/review_script", json={"dry_run": True})
        assert_status(r, 200, "review dry-run")
        data = r.json()
        if data.get("status") != "dry_run" or data.get("engine") != "lightweight_review":
            raise TestFailure(f"Review dry-run returned wrong status/engine: {data}")
        if data.get("entry_count") != len(entries):
            raise TestFailure(f"Review dry-run returned wrong entry count: {data}")
        if int(data.get("batch_size") or 0) < 1 or int(data.get("batch_count") or 0) < 1:
            raise TestFailure(f"Review dry-run returned invalid batch plan: {data}")

        r = get("/api/status/review")
        assert_status(r, 200, "read review status after dry-run")
        if r.json().get("running"):
            raise TestFailure("Review dry-run should not start a background task")
    finally:
        if original_book_id:
            try:
                post("/api/books/select", json={"book_id": original_book_id})
            except Exception:
                pass
        if temp_book_id:
            try:
                delete(f"/api/books/{temp_book_id}")
            except Exception:
                pass


def test_script_package_roundtrip_with_chapter_assets():
    """Saved script packages should restore chapters, chunks, characters, memory, issues, and voices."""
    skip_if_generation_locked()
    original_book_id = ""
    temp_book_id = ""
    package_name = f"{TEST_PREFIX}chapter_package_{int(time.time() * 1000)}"

    current = get("/api/books/current")
    assert_status(current, 200, "read current book before script package test")
    original_book_id = current.json().get("id") or ""

    try:
        title = f"codex_api_test_package_{int(time.time() * 1000)}"
        r = post("/api/books", json={"title": title})
        assert_status(r, 200, "create temporary book for script package test")
        temp_book_id = (r.json().get("book") or {}).get("id") or ""
        if not temp_book_id:
            raise TestFailure(f"Temporary book response did not include an id: {r.json()}")

        source_text = (
            "第一章 雨夜\n"
            "雨落在城门上。\n"
            "阿景说：走。\n\n"
            "第二章 归途\n"
            "马蹄踏过长街。\n"
            "阿景说：回家。\n"
        )
        files = {
            "file": (
                f"{title}.txt",
                io.BytesIO(source_text.encode("utf-8")),
                "text/plain",
            )
        }
        r = post("/api/upload", files=files)
        assert_status(r, 200, "upload source for package test")
        chapters = ((r.json().get("chapters") or {}).get("chapters") or [])
        if len(chapters) != 2:
            raise TestFailure(f"Expected 2 chapters before package save, got {chapters}")
        first_chapter_id = chapters[0]["chapter_id"]
        second_chapter_id = chapters[1]["chapter_id"]

        tagged_content = (
            f"# [{first_chapter_id}] {chapters[0].get('title') or ''}\n"
            "<旁白:>雨落在城门上。 {instruct=沉稳旁白}\n"
            "<阿景:>走。 {instruct=低声}\n"
            f"# [{second_chapter_id}] {chapters[1].get('title') or ''}\n"
            "<旁白:>马蹄踏过长街。 {instruct=稳重叙事}\n"
            "<阿景:>回家。 {instruct=克制}\n"
        )
        r = post("/api/annotated_script/tagged", json={
            "content": tagged_content,
            "replace_scope": "all",
        })
        assert_status(r, 200, "import tagged script before package save")

        r = post("/api/characters", json={
            "characters": [
                {
                    "name": "萧景",
                    "aliases": ["阿景"],
                    "traits": "禁军旧部，谨慎克制",
                    "voice_profile": "青年男声，低沉克制",
                    "confidence": 0.95,
                }
            ],
            "narrator_style": "沉稳旁白",
            "genre": "古风",
            "key_terms": ["归途"],
            "normalize_script_speakers": True,
        })
        assert_status(r, 200, "save character pool before package save")

        r = get("/api/script_issues")
        assert_status(r, 200, "ensure script issues exist before package save")
        r = post("/api/characters/apply_voice_style", json={"mode": "overwrite", "names": ["萧景"]})
        assert_status(r, 200, "apply character style before package save")

        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        character_analysis_state_path = os.path.join(repo_root, "books", temp_book_id, "character_analysis_state.json")
        with open(character_analysis_state_path, "w", encoding="utf-8") as f:
            json.dump({
                "status": "done",
                "updated_at": "2026-01-01T00:00:00+00:00",
                "chapters": {
                    chapter["chapter_id"]: {
                        "chapter_id": chapter["chapter_id"],
                        "chapter_index": chapter.get("index"),
                        "chapter_title": chapter.get("title"),
                        "status": "done",
                        "characters": 1,
                        "updated_at": "2026-01-01T00:00:00+00:00",
                    }
                    for chapter in chapters
                },
            }, f, indent=2, ensure_ascii=False)

        r = post("/api/scripts/save", json={"name": package_name})
        assert_status(r, 200, "save script package")
        saved = r.json()
        required_flags = [
            "has_voice_config",
            "has_chunks",
            "has_character_book",
            "has_character_analysis_state",
            "has_chapter_memory",
            "has_script_issues",
            "has_chapters",
        ]
        missing_flags = [flag for flag in required_flags if not saved.get(flag)]
        if missing_flags:
            raise TestFailure(f"Saved package missing companion assets {missing_flags}: {saved}")
        if saved.get("chapter_count") != 2 or saved.get("chunk_count", 0) < 2:
            raise TestFailure(f"Saved package counts are wrong: {saved}")

        r = post("/api/chapters/resplit", json={"confirm_invalidate": True})
        assert_status(r, 200, "clear current generated outputs before package load")
        r = get("/api/annotated_script")
        if r.status_code != 404:
            raise TestFailure(f"Script should be cleared before package load, got {r.status_code}: {r.text[:300]}")

        r = post("/api/scripts/load", json={"name": package_name})
        assert_status(r, 200, "load script package")
        loaded = r.json()
        if not loaded.get("voice_config_loaded") or not loaded.get("character_book_loaded"):
            raise TestFailure(f"Package did not restore voice config and character book: {loaded}")
        if not loaded.get("character_analysis_state_loaded"):
            raise TestFailure(f"Package did not restore character analysis state: {loaded}")
        if not loaded.get("chapter_memory_loaded") or not loaded.get("script_issues_loaded"):
            raise TestFailure(f"Package did not restore memory/issues: {loaded}")
        if not loaded.get("chapters_loaded") or loaded.get("chapter_count") != 2:
            raise TestFailure(f"Package did not restore chapters: {loaded}")
        if not loaded.get("chunks_restored") or loaded.get("chunk_count", 0) < 2:
            raise TestFailure(f"Package did not restore chunks: {loaded}")

        r = get("/api/chapters")
        assert_status(r, 200, "read restored chapters")
        restored_chapters = r.json().get("chapters") or []
        restored_titles = [chapter.get("title") for chapter in restored_chapters]
        if restored_titles != ["第一章 雨夜", "第二章 归途"]:
            raise TestFailure(f"Restored chapter titles are wrong: {restored_titles}")

        r = get("/api/characters")
        assert_status(r, 200, "read restored characters")
        characters = {item.get("name"): item for item in r.json().get("characters", [])}
        xiao_jing = characters.get("萧景")
        if (
            not xiao_jing
            or "阿景" not in (xiao_jing.get("aliases") or [])
            or "禁军旧部" not in (xiao_jing.get("traits") or "")
            or "青年男声" not in (xiao_jing.get("voice_profile") or "")
        ):
            raise TestFailure(f"Restored character pool is wrong: {characters}")

        r = get("/api/voices")
        assert_status(r, 200, "read restored voices")
        voices = {item.get("name"): item.get("config") or {} for item in r.json()}
        if "萧景" not in voices:
            raise TestFailure(f"Restored voices missing 萧景: {voices}")
        if voices["萧景"]:
            raise TestFailure(f"Restored unset voice should remain unconfigured: {voices['萧景']}")

        r = get("/api/annotated_script/tagged")
        assert_status(r, 200, "export restored tagged script")
        restored_tagged = r.json().get("content") or ""
        if "# [chapter_0001] 第一章 雨夜" not in restored_tagged or "<萧景:>走。" not in restored_tagged:
            raise TestFailure(f"Restored tagged script is wrong:\n{restored_tagged}")

        r = get("/api/script_issues")
        assert_status(r, 200, "read restored script issues")
        issue_summary = r.json().get("summary") or {}
        if issue_summary.get("chapter_count") != 2:
            raise TestFailure(f"Restored script issues summary is wrong: {issue_summary}")

        r = get("/api/script_progress")
        assert_status(r, 200, "read restored script progress")
        progress_summary = r.json().get("summary") or {}
        if progress_summary.get("character_analyzed_chapters") != 2:
            raise TestFailure(f"Restored character analysis progress is wrong: {progress_summary}")
    finally:
        try:
            delete(f"/api/scripts/{package_name}")
        except Exception:
            pass
        if original_book_id:
            try:
                post("/api/books/select", json={"book_id": original_book_id})
            except Exception:
                pass
        if temp_book_id:
            try:
                delete(f"/api/books/{temp_book_id}")
            except Exception:
                pass


# ── Section 5: Scripts CRUD ─────────────────────────────────

def test_save_script():
    if not shared.get("has_script"):
        raise TestFailure("SKIP: no annotated script loaded")
    r = post("/api/scripts/save", json={"name": f"{TEST_PREFIX}script"})
    assert_status(r, 200)
    data = r.json()
    if data.get("status") != "saved":
        raise TestFailure(f"Expected status=saved, got {data}")


def test_list_scripts():
    r = get("/api/scripts")
    assert_status(r, 200)
    data = r.json()
    if not isinstance(data, list):
        raise TestFailure(f"Expected list, got {type(data).__name__}")
    if shared.get("has_script"):
        names = [s["name"] for s in data]
        if f"{TEST_PREFIX}script" not in names:
            raise TestFailure(f"Saved script not in list: {names}")


def test_load_script():
    if not shared.get("has_script"):
        raise TestFailure("SKIP: no annotated script loaded")
    r = post("/api/scripts/load", json={"name": f"{TEST_PREFIX}script"})
    assert_status(r, 200)
    data = r.json()
    if data.get("status") != "loaded":
        raise TestFailure(f"Expected status=loaded, got {data}")


def test_delete_script():
    if not shared.get("has_script"):
        raise TestFailure("SKIP: no annotated script loaded")
    r = delete(f"/api/scripts/{TEST_PREFIX}script")
    assert_status(r, 200)
    data = r.json()
    if data.get("status") != "deleted":
        raise TestFailure(f"Expected status=deleted, got {data}")


def test_delete_script_404():
    r = delete(f"/api/scripts/{TEST_PREFIX}nonexistent_xyz")
    assert_status(r, 404)


# ── Section 6: Voices ───────────────────────────────────────

def test_get_voices():
    r = get("/api/voices")
    assert_status(r, 200)
    data = r.json()
    if not isinstance(data, list):
        raise TestFailure(f"Expected list, got {type(data).__name__}")


def test_voice_and_character_count_sorting():
    """Voice and character endpoints should expose line/char counts and sort by them."""
    skip_if_generation_locked()
    original_book_id = ""
    temp_book_id = ""

    current = get("/api/books/current")
    assert_status(current, 200, "read current book before line-count sort test")
    original_book_id = current.json().get("id") or ""

    try:
        title = f"codex_api_test_count_sort_{int(time.time() * 1000)}"
        r = post("/api/books", json={"title": title})
        assert_status(r, 200, "create temporary book for count sort test")
        temp_book_id = (r.json().get("book") or {}).get("id") or ""
        if not temp_book_id:
            raise TestFailure(f"Temporary book response did not include an id: {r.json()}")

        source_text = (
            "第一章 排序\n"
            "甲乙丙轮流说话。\n"
        )
        r = post("/api/upload", files={
            "file": (
                f"{title}.txt",
                io.BytesIO(source_text.encode("utf-8")),
                "text/plain",
            )
        })
        assert_status(r, 200, "upload source for count sort test")
        chapters = ((r.json().get("chapters") or {}).get("chapters") or [])
        if len(chapters) != 1:
            raise TestFailure(f"Expected 1 chapter after upload, got {chapters}")
        chapter_id = chapters[0]["chapter_id"]

        tagged_content = (
            f"# [{chapter_id}] {chapters[0].get('title') or ''}\n"
            "<甲:>一二三四五六七八九十。 {instruct=平稳}\n"
            "<乙:>一。 {instruct=平稳}\n"
            "<乙:>二。 {instruct=平稳}\n"
            "<丙:>一。 {instruct=平稳}\n"
            "<丙:>二。 {instruct=平稳}\n"
            "<丙:>三。 {instruct=平稳}\n"
        )
        r = post("/api/annotated_script/tagged", json={
            "content": tagged_content,
            "replace_scope": "all",
        })
        assert_status(r, 200, "import tagged script for line-count sort test")

        def assert_counts_sorted(path, field, reverse=False):
            r = get(path)
            assert_status(r, 200, f"read sorted rows from {path}")
            payload = r.json()
            rows = payload.get("characters") if isinstance(payload, dict) else payload
            if not isinstance(rows, list) or len(rows) < 3:
                raise TestFailure(f"Expected sortable row list from {path}, got {payload}")
            missing = [item.get("name") for item in rows if field not in item]
            if missing:
                raise TestFailure(f"Rows from {path} are missing {field}: {missing}")
            counts = [int(item.get(field) or 0) for item in rows]
            expected = sorted(counts, reverse=reverse)
            if counts != expected:
                raise TestFailure(f"Rows from {path} are not sorted by {field}. counts={counts}, expected={expected}")

        assert_counts_sorted("/api/voices?sort_by=line_count&sort_order=asc", "line_count")
        assert_counts_sorted("/api/voices?sort_by=line_count&sort_order=desc", "line_count", reverse=True)
        assert_counts_sorted("/api/voices?sort_by=char_count&sort_order=asc", "char_count")
        assert_counts_sorted("/api/voices?sort_by=char_count&sort_order=desc", "char_count", reverse=True)
        assert_counts_sorted("/api/characters?sort_by=line_count&sort_order=asc", "line_count")
        assert_counts_sorted("/api/characters?sort_by=line_count&sort_order=desc", "line_count", reverse=True)
        assert_counts_sorted("/api/characters?sort_by=char_count&sort_order=asc", "char_count")
        assert_counts_sorted("/api/characters?sort_by=char_count&sort_order=desc", "char_count", reverse=True)
    finally:
        if original_book_id:
            try:
                post("/api/books/select", json={"book_id": original_book_id})
            except Exception:
                pass
        if temp_book_id:
            try:
                delete(f"/api/books/{temp_book_id}")
            except Exception:
                pass


def test_save_voice_config():
    r = post("/api/save_voice_config", json={
        f"{TEST_PREFIX}voice": {
            "type": "custom",
            "voice": "Ryan",
            "character_style": "",
            "seed": "-1",
            "confirmed": True,
        }
    })
    assert_status(r, 200)
    data = r.json()
    if data.get("status") != "saved":
        raise TestFailure(f"Expected status=saved, got {data}")


def test_save_dashscope_voice_config_roundtrip():
    r = post("/api/save_voice_config", json={
        f"{TEST_PREFIX}dashscope_voice": {
            "type": "dashscope",
            "dashscope_model": "qwen3-tts-instruct-flash",
            "dashscope_voice": "Cherry",
            "character_style": "轻声，压抑",
            "seed": "-1",
            "confirmed": True,
        }
    })
    assert_status(r, 200)
    data = r.json()
    config = (data.get("voice_config") or {}).get(f"{TEST_PREFIX}dashscope_voice") or {}
    if config.get("dashscope_model") != "qwen3-tts-instruct-flash" or config.get("dashscope_voice") != "Cherry":
        raise TestFailure(f"DashScope voice fields were not persisted: {config}")


def test_save_dashscope_flash_only_voice_normalizes_model():
    r = post("/api/save_voice_config", json={
        f"{TEST_PREFIX}dashscope_aiden": {
            "type": "dashscope",
            "dashscope_model": "qwen3-tts-instruct-flash",
            "dashscope_voice": "Aiden",
            "character_style": "温暖，克制",
            "seed": "-1",
            "confirmed": True,
        }
    })
    assert_status(r, 200)
    data = r.json()
    config = (data.get("voice_config") or {}).get(f"{TEST_PREFIX}dashscope_aiden") or {}
    if config.get("dashscope_model") != "qwen3-tts-flash" or config.get("dashscope_voice") != "Aiden":
        raise TestFailure(f"Flash-only DashScope voice model was not normalized: {config}")


def test_save_dashscope_expanded_flash_only_voice_normalizes_model():
    r = post("/api/save_voice_config", json={
        f"{TEST_PREFIX}dashscope_bodega": {
            "type": "dashscope",
            "dashscope_model": "qwen3-tts-instruct-flash",
            "dashscope_voice": "Bodega",
            "character_style": "热情，开朗",
            "seed": "-1",
            "confirmed": True,
        }
    })
    assert_status(r, 200)
    data = r.json()
    config = (data.get("voice_config") or {}).get(f"{TEST_PREFIX}dashscope_bodega") or {}
    if config.get("dashscope_model") != "qwen3-tts-flash" or config.get("dashscope_voice") != "Bodega":
        raise TestFailure(f"Expanded flash-only DashScope voice model was not normalized: {config}")


def test_save_volcengine_voice_config_roundtrip():
    r = post("/api/save_voice_config", json={
        f"{TEST_PREFIX}volcengine_voice": {
            "type": "volcengine",
            "volcengine_resource_id": "seed-tts-2.0",
            "volcengine_speaker": "zh_female_vv_uranus_bigtts",
            "volcengine_sample_rate": 24000,
            "volcengine_speech_rate": 8,
            "volcengine_loudness_rate": 0,
            "volcengine_emotion": "",
            "volcengine_emotion_scale": 4,
            "character_style": "沉稳，叙述感强",
            "confirmed": True,
        }
    })
    assert_status(r, 200)
    data = r.json()
    config = (data.get("voice_config") or {}).get(f"{TEST_PREFIX}volcengine_voice") or {}
    if (
        config.get("volcengine_resource_id") != "seed-tts-2.0"
        or config.get("volcengine_speaker") != "zh_female_vv_uranus_bigtts"
        or config.get("volcengine_sample_rate") != 24000
        or config.get("volcengine_speech_rate") != 8
    ):
        raise TestFailure(f"Volcengine voice fields were not persisted: {config}")


def test_volcengine_voice_list_includes_gaolengyujie():
    r = get("/api/volcengine/voices", timeout=45)
    assert_status(r, 200, "read Volcengine voice list")
    data = r.json()
    voices = data.get("voices") or {}
    tts2 = voices.get("seed-tts-2.0") or []
    if not isinstance(tts2, list):
        raise TestFailure(f"Volcengine seed-tts-2.0 voices should be a list: {data}")
    values = {item.get("value") for item in tts2 if isinstance(item, dict)}
    if "zh_female_gaolengyujie_uranus_bigtts" not in values:
        raise TestFailure(f"Volcengine voice list missing 高冷御姐 2.0: {data}")


def test_voice_config_change_invalidates_matching_audio():
    skip_if_generation_locked()
    original_book_id = ""
    temp_book_id = ""

    current = get("/api/books/current")
    assert_status(current, 200, "read current book before voice invalidation test")
    original_book_id = current.json().get("id") or ""

    try:
        title = f"codex_api_test_voice_invalidation_{int(time.time() * 1000)}"
        r = post("/api/books", json={"title": title})
        assert_status(r, 200, "create temporary book for voice invalidation test")
        temp_book_id = (r.json().get("book") or {}).get("id") or ""
        if not temp_book_id:
            raise TestFailure(f"Temporary book response did not include an id: {r.json()}")

        tagged_content = (
            "# [chapter_0001] 声音测试\n"
            "<阿景:>第一句。 {instruct=低声}\n"
            "<旁白:>旁白句子。 {instruct=平稳}\n"
        )
        r = post("/api/annotated_script/tagged", json={
            "content": tagged_content,
            "replace_scope": "all",
        })
        assert_status(r, 200, "import tagged script for voice invalidation test")

        r = post("/api/characters", json={
            "characters": [
                {
                    "name": "萧景",
                    "aliases": ["阿景"],
                    "traits": "禁军旧部，谨慎克制",
                    "voice_profile": "青年男声，低沉克制",
                }
            ],
            "narrator_style": "沉稳旁白",
            "genre": "古风",
            "key_terms": [],
            "normalize_script_speakers": False,
        })
        assert_status(r, 200, "save alias character pool before voice invalidation test")

        r = get("/api/chunks")
        assert_status(r, 200, "read chunks before voice invalidation mutation")
        chunks = r.json()
        ajing_index = next((idx for idx, chunk in enumerate(chunks) if chunk.get("speaker") == "阿景"), None)
        narrator_index = next((idx for idx, chunk in enumerate(chunks) if chunk.get("speaker") == "NARRATOR"), None)
        if ajing_index is None or narrator_index is None:
            raise TestFailure(f"Expected 阿景 and NARRATOR chunks, got {chunks}")

        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        book_dir = os.path.join(repo_root, "books", temp_book_id)
        voicelines_dir = os.path.join(book_dir, "voicelines")
        os.makedirs(voicelines_dir, exist_ok=True)
        for filename in ("ajing.mp3", "narrator.mp3"):
            with open(os.path.join(voicelines_dir, filename), "wb") as f:
                f.write(b"fake-audio")

        chunks[ajing_index].update({"status": "done", "audio_path": "voicelines/ajing.mp3"})
        chunks[narrator_index].update({"status": "done", "audio_path": "voicelines/narrator.mp3"})
        with open(os.path.join(book_dir, "chunks.json"), "w", encoding="utf-8") as f:
            json.dump(chunks, f, indent=2, ensure_ascii=False)

        initial_config = {
            "type": "dashscope",
            "dashscope_model": "qwen3-tts-instruct-flash",
            "dashscope_voice": "Cherry",
            "character_style": "轻声",
            "seed": "-1",
            "confirmed": True,
        }
        r = post("/api/save_voice_config", json={"萧景": initial_config})
        assert_status(r, 200, "save initial 萧景 voice")
        initial = r.json()
        if initial.get("invalidated_chunks") != 0 or "萧景" not in (initial.get("changed_names") or []):
            raise TestFailure(f"Canonical voice save should not invalidate alias audio: {initial}")
        if not os.path.exists(os.path.join(voicelines_dir, "ajing.mp3")):
            raise TestFailure("阿景 audio file should remain when only 萧景 voice changes")
        if not os.path.exists(os.path.join(voicelines_dir, "narrator.mp3")):
            raise TestFailure("Narrator audio should not be removed when 阿景 voice changes")

        r = get("/api/chunks")
        assert_status(r, 200, "read chunks after initial voice invalidation")
        chunks_after = r.json()
        if chunks_after[ajing_index].get("audio_path") != "voicelines/ajing.mp3":
            raise TestFailure(f"阿景 chunk should keep audio when only 萧景 voice changes: {chunks_after[ajing_index]}")
        if chunks_after[narrator_index].get("audio_path") != "voicelines/narrator.mp3":
            raise TestFailure(f"Narrator chunk should keep audio: {chunks_after[narrator_index]}")

        with open(os.path.join(voicelines_dir, "ajing-new.mp3"), "wb") as f:
            f.write(b"fake-audio-new")
        chunks_after[ajing_index].update({"status": "done", "audio_path": "voicelines/ajing-new.mp3"})
        with open(os.path.join(book_dir, "chunks.json"), "w", encoding="utf-8") as f:
            json.dump(chunks_after, f, indent=2, ensure_ascii=False)

        r = post("/api/save_voice_config", json={"萧景": {**initial_config, "confirmed": False}})
        assert_status(r, 200, "save confirmed-only 萧景 voice change")
        unchanged = r.json()
        if unchanged.get("invalidated_chunks") != 0:
            raise TestFailure(f"Confirmed-only voice save should not invalidate audio: {unchanged}")
        if not os.path.exists(os.path.join(voicelines_dir, "ajing-new.mp3")):
            raise TestFailure("Confirmed-only voice save should keep existing 阿景 audio file")

        changed_config = {**initial_config, "dashscope_voice": "Serena"}
        r = post("/api/save_voice_config", json={"阿景": changed_config})
        assert_status(r, 200, "save changed 阿景 alias voice")
        changed = r.json()
        if changed.get("invalidated_chunks") != 1 or "阿景" not in (changed.get("changed_names") or []):
            raise TestFailure(f"Changed alias voice save should invalidate only 阿景 audio: {changed}")
        if os.path.exists(os.path.join(voicelines_dir, "ajing-new.mp3")):
            raise TestFailure("Changed voice save did not remove stale 阿景 audio file")
    finally:
        if original_book_id:
            try:
                post("/api/books/select", json={"book_id": original_book_id})
            except Exception:
                pass
        if temp_book_id:
            try:
                delete(f"/api/books/{temp_book_id}")
            except Exception:
                pass


def test_character_pool_voice_config_invalidates_matching_audio():
    skip_if_generation_locked()
    original_book_id = ""
    temp_book_id = ""

    current = get("/api/books/current")
    assert_status(current, 200, "read current book before character pool voice invalidation test")
    original_book_id = current.json().get("id") or ""

    try:
        title = f"codex_api_test_character_pool_voice_invalidation_{int(time.time() * 1000)}"
        r = post("/api/books", json={"title": title})
        assert_status(r, 200, "create temporary book for character pool voice invalidation test")
        temp_book_id = (r.json().get("book") or {}).get("id") or ""
        if not temp_book_id:
            raise TestFailure(f"Temporary book response did not include an id: {r.json()}")

        tagged_content = (
            "# [chapter_0001] 人物池声音测试\n"
            "<阿景:>先走。 {instruct=低声}\n"
            "<旁白:>夜色沉沉。 {instruct=沉稳}\n"
        )
        r = post("/api/annotated_script/tagged", json={
            "content": tagged_content,
            "replace_scope": "all",
        })
        assert_status(r, 200, "import tagged script for character pool voice invalidation test")

        r = get("/api/chunks")
        assert_status(r, 200, "read chunks before character pool voice invalidation mutation")
        chunks = r.json()
        ajing_index = next((idx for idx, chunk in enumerate(chunks) if chunk.get("speaker") == "阿景"), None)
        narrator_index = next((idx for idx, chunk in enumerate(chunks) if chunk.get("speaker") == "NARRATOR"), None)
        if ajing_index is None or narrator_index is None:
            raise TestFailure(f"Expected 阿景 and NARRATOR chunks, got {chunks}")

        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        book_dir = os.path.join(repo_root, "books", temp_book_id)
        voicelines_dir = os.path.join(book_dir, "voicelines")
        os.makedirs(voicelines_dir, exist_ok=True)
        for filename in ("ajing-pool.mp3", "narrator-pool.mp3"):
            with open(os.path.join(voicelines_dir, filename), "wb") as f:
                f.write(b"fake-audio")
        chunks[ajing_index].update({"status": "done", "audio_path": "voicelines/ajing-pool.mp3"})
        chunks[narrator_index].update({"status": "done", "audio_path": "voicelines/narrator-pool.mp3"})
        with open(os.path.join(book_dir, "chunks.json"), "w", encoding="utf-8") as f:
            json.dump(chunks, f, indent=2, ensure_ascii=False)

        voice_config = {
            "萧景": {
                "type": "dashscope",
                "dashscope_model": "qwen3-tts-instruct-flash",
                "dashscope_voice": "Cherry",
                "character_style": "低声克制",
                "seed": "-1",
                "confirmed": True,
            }
        }
        payload = {
            "characters": [
                {
                    "name": "萧景",
                    "aliases": ["阿景"],
                    "traits": "禁军旧部，谨慎克制",
                    "voice_profile": "青年男声，低沉克制",
                }
            ],
            "narrator_style": "沉稳旁白",
            "genre": "古风",
            "key_terms": [],
            "normalize_script_speakers": True,
            "voice_config": voice_config,
        }
        r = post("/api/characters", json=payload)
        assert_status(r, 200, "save character pool voice config")
        saved = r.json()
        if saved.get("invalidated_chunks") != 1 or "萧景" not in (saved.get("voice_config_changed_names") or []):
            raise TestFailure(f"Character pool voice save should invalidate matching audio: {saved}")
        if os.path.exists(os.path.join(voicelines_dir, "ajing-pool.mp3")):
            raise TestFailure("Character pool voice save did not remove stale 阿景 audio")
        if not os.path.exists(os.path.join(voicelines_dir, "narrator-pool.mp3")):
            raise TestFailure("Narrator audio should not be removed by character pool voice save")

        r = get("/api/chunks")
        assert_status(r, 200, "read chunks after character pool voice invalidation")
        chunks_after = r.json()
        if chunks_after[ajing_index].get("speaker") != "萧景":
            raise TestFailure(f"Character pool save should normalize alias speaker to 萧景: {chunks_after}")
        if chunks_after[ajing_index].get("audio_path") is not None or chunks_after[ajing_index].get("status") != "pending":
            raise TestFailure(f"萧景 chunk was not reset after character pool voice change: {chunks_after[ajing_index]}")

        with open(os.path.join(voicelines_dir, "ajing-pool-new.mp3"), "wb") as f:
            f.write(b"fake-audio-new")
        chunks_after[ajing_index].update({"status": "done", "audio_path": "voicelines/ajing-pool-new.mp3"})
        with open(os.path.join(book_dir, "chunks.json"), "w", encoding="utf-8") as f:
            json.dump(chunks_after, f, indent=2, ensure_ascii=False)

        r = post("/api/characters", json=payload)
        assert_status(r, 200, "save unchanged character pool voice config")
        unchanged = r.json()
        if unchanged.get("invalidated_chunks") != 0:
            raise TestFailure(f"Unchanged character pool voice save should not invalidate audio: {unchanged}")
        if not os.path.exists(os.path.join(voicelines_dir, "ajing-pool-new.mp3")):
            raise TestFailure("Unchanged character pool voice save should keep existing audio")
    finally:
        if original_book_id:
            try:
                post("/api/books/select", json={"book_id": original_book_id})
            except Exception:
                pass
        if temp_book_id:
            try:
                delete(f"/api/books/{temp_book_id}")
            except Exception:
                pass


def test_explicit_voice_config_roundtrip():
    """A saved explicit voice config should be marked confirmed."""
    original_book_id = ""
    temp_book_id = ""

    current = get("/api/books/current")
    assert_status(current, 200, "read current book before confirmed voice test")
    original_book_id = current.json().get("id") or ""

    try:
        title = f"codex_api_test_confirmed_voice_{int(time.time() * 1000)}"
        r = post("/api/books", json={"title": title})
        assert_status(r, 200, "create temporary book for confirmed voice test")
        temp_book_id = (r.json().get("book") or {}).get("id") or ""
        if not temp_book_id:
            raise TestFailure(f"Temporary book response did not include an id: {r.json()}")

        source_text = "第一章\n萧景说：先走。\n"
        r = post("/api/upload", files={
            "file": (
                f"{title}.txt",
                io.BytesIO(source_text.encode("utf-8")),
                "text/plain",
            )
        })
        assert_status(r, 200, "upload source for confirmed voice test")

        r = post("/api/characters", json={
            "characters": [
                {
                    "name": "萧景",
                    "aliases": [],
                    "traits": "禁军旧部",
                    "voice_profile": "青年男声",
                }
            ],
            "normalize_script_speakers": True,
            "voice_config": {
                "萧景": {
                    "type": "custom",
                    "voice": "Ryan",
                    "character_style": "青年男声",
                    "seed": "-1",
                    "confirmed": True,
                }
            },
        })
        assert_status(r, 200, "save explicit voice config")

        r = get("/api/voices")
        assert_status(r, 200, "read voices after explicit voice save")
        voices = {item.get("name"): item for item in r.json()}
        row = voices.get("萧景") or {}
        if row.get("voice_config_status") != "confirmed" or not row.get("has_confirmed_voice_config"):
            raise TestFailure(f"Explicit voice should be marked confirmed: {row}")

        r = get("/api/characters")
        assert_status(r, 200, "read characters after explicit voice save")
        characters = {item.get("name"): item for item in r.json().get("characters", [])}
        card = characters.get("萧景") or {}
        if card.get("voice_config_status") != "confirmed" or not card.get("has_confirmed_voice_config"):
            raise TestFailure(f"Character card should mark explicit voice: {card}")
    finally:
        if original_book_id:
            try:
                post("/api/books/select", json={"book_id": original_book_id})
            except Exception:
                pass
        if temp_book_id:
            try:
                delete(f"/api/books/{temp_book_id}")
            except Exception:
                pass


def test_runtime_voice_config_requires_exact_speaker_config():
    """Runtime TTS config should not let aliases inherit canonical character voices."""
    module = load_project_module(stub_tts=True)
    with tempfile.TemporaryDirectory(prefix="voc-studio_voice_cfg_") as tmpdir:
        with open(os.path.join(tmpdir, "character_book.json"), "w", encoding="utf-8") as f:
            json.dump({
                "characters": [
                    {
                        "canonical": "萧景",
                        "aliases": ["阿景", "景哥"],
                        "traits": "禁军旧部",
                        "voice_profile": "青年男声，低沉克制",
                    }
                ],
                "narrator_style": "沉稳克制旁白",
                "genre": "古风",
                "key_terms": [],
            }, f, ensure_ascii=False)
        saved_config = {
            "萧景": {
                "type": "custom",
                "voice": "Serena",
                "character_style": "保留萧景音色",
                "seed": "222",
            },
            "NARRATOR": {
                "type": "custom",
                "voice": "Ryan",
                "character_style": "保留旁白音色",
                "seed": "111",
            },
        }
        config_path = os.path.join(tmpdir, "voice_config.json")
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(saved_config, f, ensure_ascii=False)

        manager = module.ProjectManager(tmpdir)
        chunks = [
            {"speaker": "阿景", "text": "跟上。"},
            {"speaker": "景哥", "text": "别怕。"},
            {"speaker": "旁白", "text": "雨声渐密。"},
            {"speaker": "新角色", "text": "我是谁？"},
        ]
        runtime_config = manager._voice_config_for_chunks(chunks)

        for alias in ("阿景", "景哥"):
            if alias in runtime_config:
                raise TestFailure(f"Alias speaker {alias} should remain unconfigured at runtime: {runtime_config}")
        if "旁白" in runtime_config:
            raise TestFailure(f"旁白 should not inherit NARRATOR voice config at runtime: {runtime_config}")
        narrator_cfg = runtime_config.get("NARRATOR") or {}
        if narrator_cfg.get("seed") != "111" or narrator_cfg.get("character_style") != "保留旁白音色":
            raise TestFailure(f"NARRATOR saved config was not preserved: {runtime_config}")
        if "新角色" in runtime_config:
            raise TestFailure(f"Unknown speaker should remain unconfigured at runtime: {runtime_config}")

        with open(config_path, "r", encoding="utf-8") as f:
            persisted = json.load(f)
        if persisted != saved_config:
            raise TestFailure(f"Runtime voice config resolver should not rewrite saved config: {persisted}")


def test_batch_generation_rejects_missing_voice_config():
    """Batch generation should fail unconfigured speakers instead of treating them as default custom voices."""
    module = load_tts_module()
    engine = module.TTSEngine({"tts": {"mode": "external"}})
    called = {"custom": 0}

    def fail_if_called(*args, **kwargs):
        called["custom"] += 1
        return {"completed": [], "failed": []}

    engine._sequential_custom = fail_if_called
    result = engine.generate_batch(
        [{"index": 7, "speaker": "阿景", "text": "跟上。", "instruct": ""}],
        {"萧景": {"type": "custom", "voice": "Serena", "seed": "222"}},
        tempfile.gettempdir(),
    )

    if called["custom"]:
        raise TestFailure(f"Missing voice config should not reach custom generation: {result}")
    if result.get("completed"):
        raise TestFailure(f"Missing voice config should not complete: {result}")
    failed = result.get("failed") or []
    if failed != [(7, "No voice configuration for '阿景'")]:
        raise TestFailure(f"Missing voice config failure was not reported precisely: {result}")


def test_runtime_dashscope_model_fields_are_normalized():
    """Runtime should repair DashScope model fields without inventing a voice."""
    module = load_project_module(stub_tts=True)
    with tempfile.TemporaryDirectory(prefix="voc-studio_dashscope_cfg_") as tmpdir:
        with open(os.path.join(tmpdir, "character_book.json"), "w", encoding="utf-8") as f:
            json.dump({"characters": []}, f, ensure_ascii=False)

        with open(os.path.join(tmpdir, "voice_config.json"), "w", encoding="utf-8") as f:
            json.dump({
                "沈照微": {
                    "type": "dashscope",
                    "dashscope_voice": "Cherry",
                    "character_style": "轻声，压抑",
                    "seed": "-1",
                    "confirmed": True,
                },
                "陆闻舟": {
                    "type": "dashscope",
                    "dashscope_model": "qwen3-tts-instruct-flash",
                    "dashscope_voice": "Aiden",
                    "character_style": "温暖，克制",
                    "seed": "-1",
                    "confirmed": True,
                }
            }, f, ensure_ascii=False)

        manager = module.ProjectManager(tmpdir)
        runtime_config = manager._voice_config_for_chunks([{"speaker": "沈照微", "text": "你叫什么？"}])
        shen_config = runtime_config.get("沈照微") or {}
        if (
            shen_config.get("dashscope_model") != "qwen3-tts-instruct-flash"
            or shen_config.get("dashscope_voice") != "Cherry"
        ):
            raise TestFailure(f"DashScope runtime fields were not normalized: {runtime_config}")
        lu_config = runtime_config.get("陆闻舟") or {}
        if lu_config.get("dashscope_model") != "qwen3-tts-flash" or lu_config.get("dashscope_voice") != "Aiden":
            raise TestFailure(f"DashScope flash-only runtime voice was not normalized: {runtime_config}")


def test_runtime_volcengine_fields_are_normalized():
    """Runtime should repair Volcengine fields without inventing a speaker."""
    module = load_project_module(stub_tts=True)
    with tempfile.TemporaryDirectory(prefix="voc-studio_volcengine_cfg_") as tmpdir:
        with open(os.path.join(tmpdir, "character_book.json"), "w", encoding="utf-8") as f:
            json.dump({"characters": []}, f, ensure_ascii=False)

        with open(os.path.join(tmpdir, "voice_config.json"), "w", encoding="utf-8") as f:
            json.dump({
                "沈照微": {
                    "type": "volcengine",
                    "volcengine_speaker": "zh_female_vv_uranus_bigtts",
                    "character_style": "沉稳，叙述感强",
                    "confirmed": True,
                }
            }, f, ensure_ascii=False)

        manager = module.ProjectManager(tmpdir)
        runtime_config = manager._voice_config_for_chunks([{"speaker": "沈照微", "text": "你叫什么？"}])
        shen_config = runtime_config.get("沈照微") or {}
        if (
            shen_config.get("volcengine_resource_id") != "seed-tts-2.0"
            or shen_config.get("volcengine_speaker") != "zh_female_vv_uranus_bigtts"
            or shen_config.get("volcengine_sample_rate") != 24000
        ):
            raise TestFailure(f"Volcengine runtime fields were not normalized: {runtime_config}")


def test_chapter_audio_paths_use_project_manager_sanitizer():
    """Chapter merge and download paths should come from one ProjectManager rule."""
    module = load_project_module(stub_tts=True)
    with tempfile.TemporaryDirectory(prefix="voc-studio_chapter_audio_") as tmpdir:
        manager = module.ProjectManager(tmpdir)
        chapter_id = "Chapter 1: Rain & Wind"
        expected_filename = "chapter_1__rain___wind.mp3"
        if manager.chapter_audio_filename(chapter_id) != expected_filename:
            raise TestFailure(f"Unexpected chapter audio filename: {manager.chapter_audio_filename(chapter_id)}")
        if manager.chapter_audio_relative_path(chapter_id) != f"chapter_audio/{expected_filename}":
            raise TestFailure(f"Unexpected chapter audio relative path: {manager.chapter_audio_relative_path(chapter_id)}")
        if manager.chapter_audio_path(chapter_id) != os.path.join(tmpdir, "chapter_audio", expected_filename):
            raise TestFailure(f"Unexpected chapter audio absolute path: {manager.chapter_audio_path(chapter_id)}")


# ── Section 7: Chunks ───────────────────────────────────────

def test_get_chunks():
    r = get("/api/chunks")
    assert_status(r, 200)
    data = r.json()
    if not isinstance(data, list):
        raise TestFailure(f"Expected list, got {type(data).__name__}")
    shared["has_chunks"] = len(data) > 0
    if data:
        shared["chunk0_original"] = {
            "text": data[0].get("text", ""),
            "instruct": data[0].get("instruct", ""),
            "speaker": data[0].get("speaker", ""),
        }


def test_chapter_audiobook_download_uses_backend_path():
    """Chapter MP3 download should read the same path that chapter merging writes."""
    original_book_id = ""
    temp_book_id = ""

    current = get("/api/books/current")
    assert_status(current, 200, "read current book before chapter audio download test")
    original_book_id = current.json().get("id") or ""

    try:
        title = f"codex_api_test_chapter_audio_{int(time.time() * 1000)}"
        r = post("/api/books", json={"title": title})
        assert_status(r, 200, "create temporary book for chapter audio download test")
        temp_book_id = (r.json().get("book") or {}).get("id") or ""
        if not temp_book_id:
            raise TestFailure(f"Temporary book response did not include an id: {r.json()}")

        chapter_id = "Chapter 1: Rain & Wind"
        expected_filename = re.sub(r'[^\w\-]', '_', chapter_id).lower() + ".mp3"
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        chapter_audio_dir = os.path.join(repo_root, "books", temp_book_id, "chapter_audio")
        os.makedirs(chapter_audio_dir, exist_ok=True)
        expected_path = os.path.join(chapter_audio_dir, expected_filename)
        expected_bytes = b"fake-chapter-mp3"
        with open(expected_path, "wb") as f:
            f.write(expected_bytes)

        r = get(f"/api/chapters/{quote(chapter_id, safe='')}/audiobook")
        assert_status(r, 200, "download manually prepared chapter audio")
        if r.content != expected_bytes:
            raise TestFailure(f"Downloaded chapter audio bytes did not match: {r.content!r}")
        content_disposition = r.headers.get("content-disposition", "")
        if expected_filename not in content_disposition:
            raise TestFailure(f"Chapter audio response should use backend filename {expected_filename}: {content_disposition}")
    finally:
        if original_book_id:
            try:
                post("/api/books/select", json={"book_id": original_book_id})
            except Exception:
                pass
        if temp_book_id:
            try:
                delete(f"/api/books/{temp_book_id}")
            except Exception:
                pass


def test_render_plan_scopes_missing_audio_by_chapter():
    """Render planning should select missing audio by book/chapter without starting TTS."""
    skip_if_generation_locked()
    original_book_id = ""
    temp_book_id = ""

    current = get("/api/books/current")
    assert_status(current, 200, "read current book before render-plan test")
    original_book_id = current.json().get("id") or ""

    try:
        title = f"codex_api_test_render_plan_{int(time.time() * 1000)}"
        r = post("/api/books", json={"title": title})
        assert_status(r, 200, "create temporary book for render-plan test")
        temp_book_id = (r.json().get("book") or {}).get("id") or ""
        if not temp_book_id:
            raise TestFailure(f"Temporary book response did not include an id: {r.json()}")

        source_text = (
            "第一章 雨声\n"
            "雨声落在门外。\n"
            "阿景低声回答。\n\n"
            "第二章 灯火\n"
            "灯火从窗纸后亮起。\n"
            "巡夜人停下脚步。\n"
        )
        r = post("/api/upload", files={
            "file": (
                f"{title}.txt",
                io.BytesIO(source_text.encode("utf-8")),
                "text/plain",
            )
        })
        assert_status(r, 200, "upload source for render-plan test")
        chapters = ((r.json().get("chapters") or {}).get("chapters") or [])
        if len(chapters) != 2:
            raise TestFailure(f"Expected 2 chapters for render-plan test, got {chapters}")

        first_id = chapters[0]["chapter_id"]
        second_id = chapters[1]["chapter_id"]
        tagged_content = (
            f"# [{first_id}] {chapters[0].get('title') or ''}\n"
            "<旁白:>雨声落在门外。 {instruct=沉稳旁白}\n"
            "<阿景:>我听见了。 {instruct=低声回答}\n"
            f"# [{second_id}] {chapters[1].get('title') or ''}\n"
            "<旁白:>灯火从窗纸后亮起。 {instruct=夜色旁白}\n"
            "<巡夜人:>谁在那里？ {instruct=警惕询问}\n"
        )
        r = post("/api/annotated_script/tagged", json={
            "content": tagged_content,
            "replace_scope": "all",
        })
        assert_status(r, 200, "import tagged script for render-plan test")

        r = get("/api/chunks")
        assert_status(r, 200, "read chunks before render-plan mutation")
        chunks = r.json()
        if len(chunks) != 4:
            raise TestFailure(f"Expected 4 chunks from tagged script, got {chunks}")

        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        book_dir = os.path.join(repo_root, "books", temp_book_id)
        voicelines_dir = os.path.join(book_dir, "voicelines")
        os.makedirs(voicelines_dir, exist_ok=True)
        existing_audio = os.path.join(voicelines_dir, "existing.mp3")
        with open(existing_audio, "wb") as f:
            f.write(b"fake-audio")

        chunks[0].update({"id": 0, "status": "done", "audio_path": "voicelines/existing.mp3"})
        chunks[1].update({"id": 1, "status": "done", "audio_path": "voicelines/missing.mp3"})
        chunks[2].update({"id": 2, "status": "pending", "audio_path": None})
        chunks[3].update({"id": 3, "status": "error", "audio_path": None})
        chunks.append({
            "id": 4,
            "speaker": "NARRATOR",
            "text": "",
            "instruct": "",
            "status": "pending",
            "audio_path": None,
            "chapter_id": second_id,
            "chapter_index": chapters[1].get("index"),
            "chapter_title": chapters[1].get("title"),
        })
        chunks_path = os.path.join(book_dir, "chunks.json")
        with open(chunks_path, "w", encoding="utf-8") as f:
            json.dump(chunks, f, indent=2, ensure_ascii=False)

        r = post("/api/render_plan", json={"regenerate_all": False})
        assert_status(r, 200, "full-book missing render plan")
        plan = r.json()
        if plan.get("indices") != [1, 2, 3]:
            raise TestFailure(f"Full missing render plan should target stale/pending/error chunks only: {plan}")
        if (
            plan.get("total_scoped") != 5
            or plan.get("non_empty_count") != 4
            or plan.get("empty_skipped_count") != 1
            or plan.get("missing_audio_count") != 3
            or plan.get("stale_done_count") != 1
            or plan.get("pending_count") != 1
            or plan.get("error_count") != 1
        ):
            raise TestFailure(f"Full missing render plan counts are wrong: {plan}")

        r = post("/api/render_plan", json={"chapter_id": first_id, "regenerate_all": False})
        assert_status(r, 200, "first chapter missing render plan")
        first_plan = r.json()
        if first_plan.get("indices") != [1] or first_plan.get("stale_done_count") != 1:
            raise TestFailure(f"First chapter should only target missing done-audio chunk: {first_plan}")

        r = post("/api/render_plan", json={"chapter_id": second_id, "regenerate_all": False})
        assert_status(r, 200, "second chapter missing render plan")
        second_plan = r.json()
        if (
            second_plan.get("indices") != [2, 3]
            or second_plan.get("empty_skipped_count") != 1
            or second_plan.get("pending_count") != 1
            or second_plan.get("error_count") != 1
        ):
            raise TestFailure(f"Second chapter missing plan should target pending/error and skip empty: {second_plan}")

        r = post("/api/render_plan", json={"chapter_id": second_id, "regenerate_all": True})
        assert_status(r, 200, "second chapter regenerate-all render plan")
        regen_plan = r.json()
        if regen_plan.get("indices") != [2, 3] or regen_plan.get("regenerate_count") != 2:
            raise TestFailure(f"Second chapter regenerate-all should target non-empty chapter chunks: {regen_plan}")

        r = post("/api/generate_batch", json={"chapter_id": first_id, "dry_run": True})
        assert_status(r, 200, "dry-run normal batch render by first chapter")
        batch_plan = r.json()
        if batch_plan.get("status") != "dry_run" or batch_plan.get("indices") != [1] or not batch_plan.get("workers"):
            raise TestFailure(f"Normal batch dry-run should reuse chapter render plan without TTS: {batch_plan}")

        r = post("/api/generate_batch_fast", json={
            "chapter_id": second_id,
            "regenerate_all": True,
            "dry_run": True,
        })
        assert_status(r, 200, "dry-run fast batch render by second chapter")
        fast_plan = r.json()
        if (
            fast_plan.get("status") != "dry_run"
            or fast_plan.get("indices") != [2, 3]
            or fast_plan.get("regenerate_count") != 2
            or "batch_size" not in fast_plan
            or "batch_seed" not in fast_plan
        ):
            raise TestFailure(f"Fast batch dry-run should reuse chapter regenerate plan without TTS: {fast_plan}")

        r = post("/api/generate_batch", json={"indices": [3, 3, -1], "dry_run": True})
        assert_status(r, 200, "dry-run legacy explicit batch indices")
        explicit_plan = r.json()
        if explicit_plan.get("status") != "dry_run" or explicit_plan.get("indices") != [3]:
            raise TestFailure(f"Legacy explicit batch dry-run should dedupe valid indices: {explicit_plan}")

        r = get("/api/status/audio")
        assert_status(r, 200, "audio status after render dry-runs")
        if r.json().get("running"):
            raise TestFailure(f"Render dry-runs should not start audio generation: {r.json()}")
    finally:
        if original_book_id:
            try:
                post("/api/books/select", json={"book_id": original_book_id})
            except Exception:
                pass
        if temp_book_id:
            try:
                delete(f"/api/books/{temp_book_id}")
            except Exception:
                pass


def test_update_chunk():
    if not shared.get("has_chunks"):
        raise TestFailure("SKIP: no chunks available")

    r = post("/api/chunks/0", json={
        "text": f"{TEST_PREFIX}updated_text",
        "instruct": f"{TEST_PREFIX}instruct"
    })
    assert_status(r, 200)
    data = r.json()
    if data.get("text") != f"{TEST_PREFIX}updated_text":
        raise TestFailure(f"Chunk text not updated: {data.get('text')}")

    # Restore original
    orig = shared.get("chunk0_original", {})
    post("/api/chunks/0", json=orig)


def test_update_chunk_pause_after():
    """Setting pause_after on a chunk persists and does not reset status."""
    if not shared.get("has_chunks"):
        raise TestFailure("SKIP: no chunks available")

    # Read current chunk 0 status
    r = get("/api/chunks")
    assert_status(r, 200)
    original_status = r.json()[0].get("status")

    # Set pause_after
    r = post("/api/chunks/0", json={"pause_after": 3000})
    assert_status(r, 200)
    data = r.json()
    if data.get("pause_after") != 3000:
        raise TestFailure(f"pause_after not set: {data.get('pause_after')}")

    # Verify status was NOT reset (pause_after is merge-time only)
    if data.get("status") != original_status:
        raise TestFailure(
            f"Status changed from '{original_status}' to '{data.get('status')}' "
            f"— pause_after should not reset status"
        )

    # Read back via GET to confirm persistence
    r = get("/api/chunks")
    assert_status(r, 200)
    chunk0 = r.json()[0]
    if chunk0.get("pause_after") != 3000:
        raise TestFailure(f"pause_after not persisted on read-back: {chunk0.get('pause_after')}")

    # Clear pause_after by sending null
    r = post("/api/chunks/0", json={"pause_after": None})
    assert_status(r, 200)
    data = r.json()
    if data.get("pause_after") is not None:
        raise TestFailure(f"pause_after not cleared: {data.get('pause_after')}")

    # Verify key is removed from JSON (not just set to null)
    r = get("/api/chunks")
    assert_status(r, 200)
    chunk0 = r.json()[0]
    if "pause_after" in chunk0:
        raise TestFailure(f"pause_after key should be removed after clearing, got: {chunk0.get('pause_after')}")


def test_update_chunk_pause_after_zero():
    """pause_after=0 is a valid override (no silence)."""
    if not shared.get("has_chunks"):
        raise TestFailure("SKIP: no chunks available")

    r = post("/api/chunks/0", json={"pause_after": 0})
    assert_status(r, 200)
    data = r.json()
    if data.get("pause_after") != 0:
        raise TestFailure(f"pause_after=0 not set correctly: {data.get('pause_after')}")

    # Clean up
    post("/api/chunks/0", json={"pause_after": None})


def test_update_chunk_pause_after_negative():
    """Negative pause_after should be clamped to 0."""
    if not shared.get("has_chunks"):
        raise TestFailure("SKIP: no chunks available")

    r = post("/api/chunks/0", json={"pause_after": -500})
    assert_status(r, 200)
    data = r.json()
    if data.get("pause_after") != 0:
        raise TestFailure(f"Negative pause_after should clamp to 0, got: {data.get('pause_after')}")

    # Clean up
    post("/api/chunks/0", json={"pause_after": None})


def test_update_chunk_404():
    r = post("/api/chunks/99999", json={"text": "nope"})
    assert_status(r, 404)


def test_insert_chunk():
    if not shared.get("has_chunks"):
        raise TestFailure("SKIP: no chunks available")

    # Get initial count
    r = get("/api/chunks")
    assert_status(r, 200)
    initial_chunks = r.json()
    initial_count = len(initial_chunks)

    # Insert after index 0
    r = post("/api/chunks/0/insert")
    assert_status(r, 200)
    data = r.json()
    if data.get("status") != "ok":
        raise TestFailure(f"Expected status=ok, got {data}")
    if data.get("total") != initial_count + 1:
        raise TestFailure(f"Expected total={initial_count + 1}, got {data.get('total')}")

    # Verify the new chunk exists at index 1 with empty text
    r = get("/api/chunks")
    assert_status(r, 200)
    chunks = r.json()
    if len(chunks) != initial_count + 1:
        raise TestFailure(f"Chunk count mismatch: expected {initial_count + 1}, got {len(chunks)}")
    if chunks[1].get("text") != "":
        raise TestFailure(f"Inserted chunk should have empty text, got: {chunks[1].get('text')}")

    # Store index for cleanup in delete test
    shared["inserted_chunk_index"] = 1


def test_insert_chunk_404():
    r = post("/api/chunks/99999/insert")
    assert_status(r, 404)


def test_delete_chunk():
    if not shared.get("has_chunks"):
        raise TestFailure("SKIP: no chunks available")

    idx = shared.get("inserted_chunk_index")
    if idx is None:
        raise TestFailure("SKIP: no inserted chunk to delete")

    # Get count before delete
    r = get("/api/chunks")
    assert_status(r, 200)
    before_count = len(r.json())

    r = delete(f"/api/chunks/{idx}")
    assert_status(r, 200)
    data = r.json()
    assert_key(data, "deleted")
    assert_key(data, "total")
    if data["total"] != before_count - 1:
        raise TestFailure(f"Expected total={before_count - 1}, got {data['total']}")

    # Save deleted chunk for restore test
    shared["deleted_chunk"] = data["deleted"]
    shared["deleted_chunk_index"] = idx


def test_delete_chunk_invalid():
    r = delete("/api/chunks/99999")
    assert_status(r, 400)


def test_restore_chunk():
    if not shared.get("deleted_chunk"):
        raise TestFailure("SKIP: no deleted chunk to restore")

    r = get("/api/chunks")
    assert_status(r, 200)
    before_count = len(r.json())

    r = post("/api/chunks/restore", json={
        "chunk": shared["deleted_chunk"],
        "at_index": shared["deleted_chunk_index"]
    })
    assert_status(r, 200)
    data = r.json()
    if data.get("status") != "ok":
        raise TestFailure(f"Expected status=ok, got {data}")
    if data.get("total") != before_count + 1:
        raise TestFailure(f"Expected total={before_count + 1}, got {data.get('total')}")

    # Clean up: delete the restored chunk so we leave chunks as we found them
    delete(f"/api/chunks/{shared['deleted_chunk_index']}")


# ── Section 8: Status Polling ────────────────────────────────

def test_status_known_tasks():
    task_names = [
        "script", "voices", "audio", "audacity_export",
        "review", "lora_training", "dataset_gen", "dataset_builder",
        "module_install"
    ]
    for name in task_names:
        r = get(f"/api/status/{name}")
        assert_status(r, 200, msg=f"task={name}")
        data = r.json()
        if "running" not in data:
            raise TestFailure(f"Missing 'running' key for task '{name}'")
        if "logs" not in data:
            raise TestFailure(f"Missing 'logs' key for task '{name}'")


def test_status_legacy_script_generation_alias():
    r = get("/api/status/script_generation")
    assert_status(r, 200, msg="legacy script_generation alias")
    data = r.json()
    if "running" not in data or "logs" not in data:
        raise TestFailure(f"Legacy script_generation status did not return script task state: {data}")


def test_events_legacy_script_generation_alias():
    r = get("/api/events/script_generation", timeout=10)
    assert_status(r, 200, msg="legacy script_generation event alias")
    if "event: stream_end" not in r.text:
        raise TestFailure(f"Legacy script_generation event stream did not end cleanly: {r.text[:300]}")


def test_status_unknown_task():
    r = get(f"/api/status/{TEST_PREFIX}fake_task")
    assert_status(r, 404)


# ── Section 9: Voice Design ─────────────────────────────────

def test_voice_design_list():
    r = get("/api/voice_design/list")
    assert_status(r, 200)
    data = r.json()
    if not isinstance(data, list):
        raise TestFailure(f"Expected list, got {type(data).__name__}")


def test_voice_design_delete_404():
    r = delete(f"/api/voice_design/{TEST_PREFIX}fake_id")
    assert_status(r, 404)


def test_voice_design_preview():
    r = post("/api/voice_design/preview", json={
        "description": "A clear young male voice with a steady tone",
        "sample_text": "This is a test of voice design.",
    })
    assert_status(r, 200)
    data = r.json()
    assert_key(data, "audio_url")
    shared["preview_file"] = data["audio_url"].split("/")[-1]


def test_voice_design_save_and_delete():
    preview_file = shared.get("preview_file")
    if not preview_file:
        raise TestFailure("SKIP: no preview file from previous test")

    r = post("/api/voice_design/save", json={
        "name": f"{TEST_PREFIX}voice_design",
        "description": "Test voice",
        "sample_text": "Test text",
        "preview_file": preview_file
    })
    assert_status(r, 200)
    data = r.json()
    assert_key(data, "voice_id")
    voice_id = data["voice_id"]

    # Delete it
    r = delete(f"/api/voice_design/{voice_id}")
    assert_status(r, 200)


# ── Section 9b: Clone Voices ────────────────────────────────

def test_clone_voices_list():
    r = get("/api/clone_voices/list")
    assert_status(r, 200)
    data = r.json()
    if not isinstance(data, list):
        raise TestFailure(f"Expected list, got {type(data).__name__}")


def test_clone_voices_upload_bad_format():
    files = {"file": ("test.txt", b"not audio", "text/plain")}
    r = requests.post(f"{BASE_URL}/api/clone_voices/upload", files=files)
    assert_status(r, 400)


def test_clone_voices_delete_404():
    r = delete(f"/api/clone_voices/{TEST_PREFIX}fake_id")
    assert_status(r, 404)


def test_clone_voices_upload_and_delete():
    # Create a minimal WAV file (44-byte header + silence)
    import struct
    sample_rate = 16000
    num_samples = 16000  # 1 second
    data_size = num_samples * 2
    wav_header = struct.pack('<4sI4s4sIHHIIHH4sI',
        b'RIFF', 36 + data_size, b'WAVE',
        b'fmt ', 16, 1, 1, sample_rate, sample_rate * 2, 2, 16,
        b'data', data_size)
    wav_bytes = wav_header + b'\x00' * data_size

    files = {"file": (f"{TEST_PREFIX}clone_test.wav", wav_bytes, "audio/wav")}
    r = requests.post(f"{BASE_URL}/api/clone_voices/upload", files=files)
    assert_status(r, 200)
    data = r.json()
    assert_key(data, "voice_id")
    assert_key(data, "filename")
    voice_id = data["voice_id"]

    # Verify it appears in list
    r = get("/api/clone_voices/list")
    assert_status(r, 200)
    found = any(v["id"] == voice_id for v in r.json())
    if not found:
        raise TestFailure(f"Uploaded voice {voice_id} not found in list")

    # Delete it
    r = delete(f"/api/clone_voices/{voice_id}")
    assert_status(r, 200)

    # Verify it's gone
    r = get("/api/clone_voices/list")
    found = any(v["id"] == voice_id for v in r.json())
    if found:
        raise TestFailure(f"Deleted voice {voice_id} still in list")


# ── Section 10: LoRA Datasets ───────────────────────────────

def test_lora_list_datasets():
    r = get("/api/lora/datasets")
    assert_status(r, 200)
    data = r.json()
    if not isinstance(data, list):
        raise TestFailure(f"Expected list, got {type(data).__name__}")


def test_lora_delete_dataset_404():
    r = delete(f"/api/lora/datasets/{TEST_PREFIX}fake_ds")
    assert_status(r, 404)


def test_lora_upload_bad_file():
    files = {"file": (f"{TEST_PREFIX}bad.txt", io.BytesIO(b"not a zip"), "text/plain")}
    r = post("/api/lora/upload_dataset", files=files)
    # Should fail — not a valid zip
    if r.status_code < 400:
        raise TestFailure(f"Expected error for non-zip upload, got {r.status_code}")


# ── Section 11: LoRA Models ─────────────────────────────────

def test_lora_list_models():
    r = get("/api/lora/models")
    assert_status(r, 200)
    data = r.json()
    if not isinstance(data, list):
        raise TestFailure(f"Expected list, got {type(data).__name__}")
    # Verify built-in adapters have 'downloaded' field
    for m in data:
        if m.get("builtin"):
            if "downloaded" not in m:
                raise TestFailure(f"Built-in adapter {m['id']} missing 'downloaded' field")
    shared["lora_models"] = data


def test_lora_download_invalid():
    r = post(f"/api/lora/download/{TEST_PREFIX}fake_adapter", json={})
    if r.status_code < 400:
        raise TestFailure(f"Expected error for invalid adapter, got {r.status_code}")


def test_lora_delete_model_404():
    r = delete(f"/api/lora/models/{TEST_PREFIX}fake_model")
    assert_status(r, 404)


def test_lora_train_bad_dataset():
    r = post("/api/lora/train", json={
        "name": f"{TEST_PREFIX}model",
        "dataset_id": f"{TEST_PREFIX}nonexistent_ds"
    })
    # Should fail — dataset does not exist
    if r.status_code < 400:
        raise TestFailure(f"Expected error for bad dataset, got {r.status_code}")


def test_lora_preview_404():
    r = post(f"/api/lora/preview/{TEST_PREFIX}fake_adapter")
    assert_status(r, 404)


def test_lora_preview():
    models = shared.get("lora_models", [])
    if not models:
        raise TestFailure("SKIP: no LoRA models available")
    adapter = models[0]
    r = post(f"/api/lora/preview/{adapter['id']}", timeout=120)
    assert_status(r, 200)
    data = r.json()
    assert_key(data, "audio_url")


# ── Section 12: Dataset Builder CRUD ────────────────────────

def test_dataset_builder_list():
    r = get("/api/dataset_builder/list")
    assert_status(r, 200)
    data = r.json()
    if not isinstance(data, list):
        raise TestFailure(f"Expected list, got {type(data).__name__}")


def test_dataset_builder_create():
    r = post("/api/dataset_builder/create", json={
        "name": f"{TEST_PREFIX}builder_proj"
    })
    assert_status(r, 200)
    data = r.json()
    assert_key(data, "name")


def test_dataset_builder_update_meta():
    r = post("/api/dataset_builder/update_meta", json={
        "name": f"{TEST_PREFIX}builder_proj",
        "description": "A test voice description",
        "global_seed": "42"
    })
    assert_status(r, 200)


def test_dataset_builder_update_rows():
    r = post("/api/dataset_builder/update_rows", json={
        "name": f"{TEST_PREFIX}builder_proj",
        "rows": [
            {"emotion": "neutral", "text": "Hello world.", "seed": ""},
            {"emotion": "happy", "text": "Great to see you!", "seed": ""}
        ]
    })
    assert_status(r, 200)
    data = r.json()
    if data.get("sample_count") != 2:
        raise TestFailure(f"Expected sample_count=2, got {data.get('sample_count')}")


def test_dataset_builder_status():
    r = get(f"/api/dataset_builder/status/{TEST_PREFIX}builder_proj")
    assert_status(r, 200)
    data = r.json()
    assert_key(data, "description")
    assert_key(data, "samples")
    assert_key(data, "running")
    assert_key(data, "logs")
    if len(data["samples"]) != 2:
        raise TestFailure(f"Expected 2 samples, got {len(data['samples'])}")


def test_dataset_builder_cancel():
    r = post("/api/dataset_builder/cancel")
    assert_status(r, 200)
    data = r.json()
    if data.get("status") not in ("not_running", "cancelling"):
        raise TestFailure(f"Unexpected cancel status: {data}")


def test_dataset_builder_save_no_samples():
    r = post("/api/dataset_builder/save", json={
        "name": f"{TEST_PREFIX}builder_proj",
        "ref_index": 0
    })
    # Should fail — no completed samples
    if r.status_code < 400:
        raise TestFailure(f"Expected error for save with no samples, got {r.status_code}")


def test_dataset_builder_delete():
    r = delete(f"/api/dataset_builder/{TEST_PREFIX}builder_proj")
    assert_status(r, 200)
    data = r.json()
    if data.get("status") != "deleted":
        raise TestFailure(f"Expected status=deleted, got {data}")


def test_dataset_builder_delete_404():
    r = delete(f"/api/dataset_builder/{TEST_PREFIX}nonexistent")
    assert_status(r, 404)


# ── Section 13: Merge / Export ──────────────────────────────

def test_get_audiobook():
    r = get("/api/audiobook")
    if r.status_code == 404:
        return  # acceptable — no audiobook generated yet
    assert_status(r, 200)


def test_get_audacity_export():
    r = get("/api/export_audacity")
    if r.status_code == 404:
        return  # acceptable — no export generated yet
    assert_status(r, 200)


# ── Section 14: Full Tests — Generation ─────────────────────

def test_generate_script():
    r = post("/api/generate_script")
    if r.status_code == 400:
        raise TestFailure("SKIP: prerequisite not met (no uploaded file or already running)")
    assert_status(r, 200)
    data = r.json()
    if data.get("status") != "started":
        raise TestFailure(f"Expected status=started, got {data}")


def test_review_script():
    if not shared.get("has_script"):
        raise TestFailure("SKIP: no annotated script loaded")
    r = post("/api/review_script")
    if r.status_code == 400:
        raise TestFailure("SKIP: already running")
    assert_status(r, 200)
    data = r.json()
    if data.get("status") != "started":
        raise TestFailure(f"Expected status=started, got {data}")


def test_parse_voices():
    r = post("/api/parse_voices")
    if r.status_code == 400:
        raise TestFailure("SKIP: already running")
    assert_status(r, 200)
    data = r.json()
    if data.get("status") != "started":
        raise TestFailure(f"Expected status=started, got {data}")


def test_generate_chunk():
    if not shared.get("has_chunks"):
        raise TestFailure("SKIP: no chunks available")
    r = post("/api/chunks/0/generate")
    assert_status(r, 200)


def test_generate_batch():
    if not shared.get("has_chunks"):
        raise TestFailure("SKIP: no chunks available")
    r = post("/api/generate_batch", json={"indices": [0]})
    if r.status_code == 400:
        raise TestFailure("SKIP: audio generation already running")
    assert_status(r, 200)
    data = r.json()
    if data.get("status") != "started":
        raise TestFailure(f"Expected status=started, got {data}")
    # Wait for batch to finish so subsequent tests don't conflict
    if not wait_for_task("audio", timeout=120):
        raise TestFailure("generate_batch did not complete within 120s")


def test_generate_batch_fast():
    if not shared.get("has_chunks"):
        raise TestFailure("SKIP: no chunks available")
    # Wait for any prior generation to finish
    if not wait_for_task("audio", timeout=120):
        raise TestFailure("SKIP: prior audio generation did not finish in time")
    r = post("/api/generate_batch_fast", json={"indices": [0]})
    if r.status_code == 400:
        raise TestFailure("SKIP: audio generation already running")
    assert_status(r, 200)
    data = r.json()
    if data.get("status") != "started":
        raise TestFailure(f"Expected status=started, got {data}")


def test_cancel_audio():
    """Cancel endpoint works when nothing is running (resets stuck chunks)."""
    status = get("/api/status/audio")
    assert_status(status, 200, "read audio status before cancel test")
    if status.json().get("running"):
        raise TestFailure("SKIP: audio generation already running")
    r = post("/api/cancel_audio", json={})
    assert_status(r, 200)
    data = r.json()
    if data.get("status") not in ("not_running", "cancelling"):
        raise TestFailure(f"Expected status not_running or cancelling, got {data}")


def test_export_audacity():
    r = post("/api/export_audacity")
    if r.status_code == 400:
        raise TestFailure("SKIP: already running")
    assert_status(r, 200)
    data = r.json()
    if data.get("status") != "started":
        raise TestFailure(f"Expected status=started, got {data}")


def test_lora_test_model():
    models = shared.get("lora_models", [])
    if not models:
        raise TestFailure("SKIP: no LoRA models available")
    adapter = models[0]
    r = post("/api/lora/test", json={
        "adapter_id": adapter["id"],
        "text": "This is a test of the LoRA voice.",
        "instruct": "Neutral, even delivery."
    }, timeout=120)
    assert_status(r, 200)
    data = r.json()
    assert_key(data, "audio_url")


def test_lora_generate_dataset():
    r = post("/api/lora/generate_dataset", json={
        "name": f"{TEST_PREFIX}dataset",
        "description": "A clear young male voice",
        "samples": [
            {"emotion": "neutral", "text": "Hello, this is a test sample."},
            {"emotion": "happy", "text": "Great to see you today!"}
        ]
    })
    if r.status_code == 400:
        raise TestFailure("SKIP: already running or bad request")
    assert_status(r, 200)
    data = r.json()
    if data.get("status") != "started":
        raise TestFailure(f"Expected status=started, got {data}")


def test_dataset_builder_generate_sample():
    # Create a temp project for this test
    post("/api/dataset_builder/create", json={"name": f"{TEST_PREFIX}gen_proj"})
    post("/api/dataset_builder/update_rows", json={
        "name": f"{TEST_PREFIX}gen_proj",
        "rows": [{"emotion": "neutral", "text": "Hello world.", "seed": ""}]
    })

    r = post("/api/dataset_builder/generate_sample", json={
        "description": "A clear male voice",
        "text": "Hello world.",
        "dataset_name": f"{TEST_PREFIX}gen_proj",
        "sample_index": 0,
        "seed": -1
    })
    assert_status(r, 200)
    data = r.json()
    assert_key(data, "status")

    # Cleanup
    delete(f"/api/dataset_builder/{TEST_PREFIX}gen_proj")


# ── Run all tests ────────────────────────────────────────────

def run_all_tests():
    section("Server")
    run_test("server_reachable", test_server_reachable)

    section("Config")
    run_test("get_config", test_get_config)
    run_test("desktop_metadata", test_desktop_metadata)
    run_test("modules_status", test_modules_status)
    run_test("module_install_rejects_manual_module", test_module_install_rejects_manual_module)
    run_test("module_install_status_and_cancel_idle", test_module_install_status_and_cancel_idle)
    run_test("save_config_roundtrip", test_save_config_roundtrip)
    run_test("save_pause_config_roundtrip", test_save_pause_config_roundtrip)
    run_test("pause_config_defaults", test_pause_config_defaults)
    run_test("save_review_prompts_roundtrip", test_save_review_prompts_roundtrip)
    run_test("get_default_prompts", test_get_default_prompts)
    run_test("legacy_generation_prompt_migrates_to_tagged", test_legacy_generation_prompt_migrates_to_tagged)
    run_test("cache_unfriendly_generation_prompt_migrates_to_default", test_cache_unfriendly_generation_prompt_migrates_to_default)
    run_test("legacy_english_prompts_migrate_to_chinese_defaults", test_legacy_english_prompts_migrate_to_chinese_defaults)
    run_test("legacy_generation_fields_are_trimmed", test_legacy_generation_fields_are_trimmed)
    run_test("chapter_memory_config_roundtrip", test_chapter_memory_config_roundtrip)

    section("Upload")
    run_test("upload_file", test_upload_file)
    run_test("upload_chapter_split_variants", test_upload_chapter_split_variants)
    run_test("resplit_chapters_dry_run_does_not_write", test_resplit_chapters_dry_run_does_not_write)
    run_test("resplit_chapters_clears_generated_outputs", test_resplit_chapters_clears_generated_outputs)
    run_test("append_chapters_preserves_existing_outputs", test_append_chapters_preserves_existing_outputs)
    run_test("append_chapters_tail_only_and_rejects_overlap", test_append_chapters_tail_only_and_rejects_overlap)

    section("Annotated Script")
    run_test("get_annotated_script", test_get_annotated_script)
    run_test("tagged_generation_retries_json_response", test_tagged_generation_retries_json_response)
    run_test("openai_chapter_model_enables_prompt_cache", test_openai_chapter_model_enables_prompt_cache)
    run_test("llm_usage_log_reports_cache_read", test_llm_usage_log_reports_cache_read)
    run_test("streaming_llm_output_is_logged_and_assembled", test_streaming_llm_output_is_logged_and_assembled)
    run_test("annotation_prompt_templates_tagged_and_migrates_legacy", test_annotation_prompt_templates_tagged_and_migrates_legacy)
    run_test("cache_friendly_prompt_layouts_keep_dynamic_inputs_late", test_cache_friendly_prompt_layouts_keep_dynamic_inputs_late)
    run_test("character_book_merge_compacts_repeated_profiles", test_character_book_merge_compacts_repeated_profiles)
    run_test("character_book_normalize_filters_empty_shell_characters", test_character_book_normalize_filters_empty_shell_characters)
    run_test("api_character_book_merge_uses_same_compaction_rules", test_api_character_book_merge_uses_same_compaction_rules)
    run_test("api_character_book_compact_endpoint_is_local_only", test_api_character_book_compact_endpoint_is_local_only)
    run_test("partial_chapter_merge_keeps_orphans_once", test_partial_chapter_merge_keeps_orphans_once)
    run_test("script_checkpoint_removes_unfinished_target_chapters", test_script_checkpoint_removes_unfinished_target_chapters)
    run_test("tagged_chunk_character_sync_pipeline", test_tagged_chunk_character_sync_pipeline)
    run_test("tagged_chapter_replace_preserves_other_chapters", test_tagged_chapter_replace_preserves_other_chapters)
    run_test("character_book_json_import_syncs_script_and_voices", test_character_book_json_import_syncs_script_and_voices)
    run_test("character_pool_merges_multiple_unknown_speakers", test_character_pool_merges_multiple_unknown_speakers)
    run_test("character_book_voices_available_before_script", test_character_book_voices_available_before_script)
    run_test("character_pool_save_accepts_voice_config_inline", test_character_pool_save_accepts_voice_config_inline)
    run_test("generate_script_characters_dry_run", test_generate_script_characters_dry_run)
    run_test("script_operations_story_bible_snapshot_and_actions", test_script_operations_story_bible_snapshot_and_actions)
    run_test("review_script_dry_run", test_review_script_dry_run)
    run_test("script_package_roundtrip_with_chapter_assets", test_script_package_roundtrip_with_chapter_assets)

    section("Scripts CRUD")
    run_test("save_script", test_save_script)
    run_test("list_scripts", test_list_scripts)
    run_test("load_script", test_load_script)
    run_test("delete_script", test_delete_script)
    run_test("delete_script_404", test_delete_script_404)

    section("Voices")
    run_test("get_voices", test_get_voices)
    run_test("voice_and_character_count_sorting", test_voice_and_character_count_sorting)
    run_test("save_voice_config", test_save_voice_config)
    run_test("save_dashscope_voice_config_roundtrip", test_save_dashscope_voice_config_roundtrip)
    run_test("save_dashscope_flash_only_voice_normalizes_model", test_save_dashscope_flash_only_voice_normalizes_model)
    run_test("save_dashscope_expanded_flash_only_voice_normalizes_model", test_save_dashscope_expanded_flash_only_voice_normalizes_model)
    run_test("save_volcengine_voice_config_roundtrip", test_save_volcengine_voice_config_roundtrip)
    run_test("volcengine_voice_list_includes_gaolengyujie", test_volcengine_voice_list_includes_gaolengyujie)
    run_test("voice_config_change_invalidates_matching_audio", test_voice_config_change_invalidates_matching_audio)
    run_test("character_pool_voice_config_invalidates_matching_audio", test_character_pool_voice_config_invalidates_matching_audio)
    run_test("voice_metadata_keeps_alias_speakers_without_rewriting_script", test_voice_metadata_keeps_alias_speakers_without_rewriting_script)
    run_test("runtime_voice_config_requires_exact_speaker_config", test_runtime_voice_config_requires_exact_speaker_config)
    run_test("batch_generation_rejects_missing_voice_config", test_batch_generation_rejects_missing_voice_config)
    run_test("runtime_dashscope_model_fields_are_normalized", test_runtime_dashscope_model_fields_are_normalized)
    run_test("runtime_volcengine_fields_are_normalized", test_runtime_volcengine_fields_are_normalized)
    run_test("chapter_audio_paths_use_project_manager_sanitizer", test_chapter_audio_paths_use_project_manager_sanitizer)

    section("Chunks")
    run_test("get_chunks", test_get_chunks)
    run_test("chapter_audiobook_download_uses_backend_path", test_chapter_audiobook_download_uses_backend_path)
    run_test("render_plan_scopes_missing_audio_by_chapter", test_render_plan_scopes_missing_audio_by_chapter)
    run_test("update_chunk", test_update_chunk)
    run_test("update_chunk_pause_after", test_update_chunk_pause_after)
    run_test("update_chunk_pause_after_zero", test_update_chunk_pause_after_zero)
    run_test("update_chunk_pause_after_negative", test_update_chunk_pause_after_negative)
    run_test("update_chunk_404", test_update_chunk_404)
    run_test("insert_chunk", test_insert_chunk)
    run_test("insert_chunk_404", test_insert_chunk_404)
    run_test("delete_chunk", test_delete_chunk)
    run_test("delete_chunk_invalid", test_delete_chunk_invalid)
    run_test("restore_chunk", test_restore_chunk)

    section("Status Polling")
    run_test("status_known_tasks", test_status_known_tasks)
    run_test("status_legacy_script_generation_alias", test_status_legacy_script_generation_alias)
    run_test("events_legacy_script_generation_alias", test_events_legacy_script_generation_alias)
    run_test("status_unknown_task", test_status_unknown_task)

    section("Voice Design")
    run_test("voice_design_list", test_voice_design_list)
    run_test("voice_design_delete_404", test_voice_design_delete_404)
    run_test("voice_design_preview", test_voice_design_preview, requires_full=True)
    run_test("voice_design_save_and_delete", test_voice_design_save_and_delete, requires_full=True)

    section("Clone Voices")
    run_test("clone_voices_list", test_clone_voices_list)
    run_test("clone_voices_upload_bad_format", test_clone_voices_upload_bad_format)
    run_test("clone_voices_delete_404", test_clone_voices_delete_404)
    run_test("clone_voices_upload_and_delete", test_clone_voices_upload_and_delete)

    section("LoRA Datasets")
    run_test("lora_list_datasets", test_lora_list_datasets)
    run_test("lora_delete_dataset_404", test_lora_delete_dataset_404)
    run_test("lora_upload_bad_file", test_lora_upload_bad_file)

    section("LoRA Models")
    run_test("lora_list_models", test_lora_list_models)
    run_test("lora_download_invalid", test_lora_download_invalid)
    run_test("lora_delete_model_404", test_lora_delete_model_404)
    run_test("lora_train_bad_dataset", test_lora_train_bad_dataset)
    run_test("lora_preview_404", test_lora_preview_404)
    run_test("lora_preview", test_lora_preview, requires_full=True)

    section("Dataset Builder")
    run_test("dataset_builder_list", test_dataset_builder_list)
    run_test("dataset_builder_create", test_dataset_builder_create)
    run_test("dataset_builder_update_meta", test_dataset_builder_update_meta)
    run_test("dataset_builder_update_rows", test_dataset_builder_update_rows)
    run_test("dataset_builder_status", test_dataset_builder_status)
    run_test("dataset_builder_cancel", test_dataset_builder_cancel)
    run_test("dataset_builder_save_no_samples", test_dataset_builder_save_no_samples)
    run_test("dataset_builder_delete", test_dataset_builder_delete)
    run_test("dataset_builder_delete_404", test_dataset_builder_delete_404)

    section("Merge / Export")
    run_test("get_audiobook", test_get_audiobook)
    run_test("get_audacity_export", test_get_audacity_export)

    section("Generation (TTS/LLM)")
    run_test("generate_script", test_generate_script, requires_full=True)
    run_test("review_script", test_review_script, requires_full=True)
    run_test("parse_voices", test_parse_voices, requires_full=True)
    run_test("generate_chunk", test_generate_chunk, requires_full=True)
    run_test("generate_batch", test_generate_batch, requires_full=True)
    run_test("generate_batch_fast", test_generate_batch_fast, requires_full=True)
    run_test("cancel_audio", test_cancel_audio)
    run_test("export_audacity", test_export_audacity, requires_full=True)

    section("LoRA (TTS)")
    run_test("lora_test_model", test_lora_test_model, requires_full=True)
    run_test("lora_generate_dataset", test_lora_generate_dataset, requires_full=True)

    section("Dataset Builder Generate (TTS)")
    run_test("dataset_builder_generate_sample", test_dataset_builder_generate_sample, requires_full=True)


# ── Cleanup ──────────────────────────────────────────────────

def cleanup():
    print(f"\n--- Cleanup ---")
    items = []

    try:
        delete(f"/api/scripts/{TEST_PREFIX}script")
        items.append("test script")
    except Exception:
        pass

    try:
        delete(f"/api/dataset_builder/{TEST_PREFIX}builder_proj")
        items.append("builder project")
    except Exception:
        pass

    try:
        delete(f"/api/dataset_builder/{TEST_PREFIX}gen_proj")
        items.append("gen project")
    except Exception:
        pass

    try:
        delete(f"/api/lora/datasets/{TEST_PREFIX}dataset")
        items.append("test dataset")
    except Exception:
        pass

    try:
        r = get("/api/voice_design/list")
        if r.status_code == 200:
            for v in r.json():
                if v.get("id", "").startswith(TEST_PREFIX):
                    delete(f"/api/voice_design/{v['id']}")
                    items.append(f"voice {v['id']}")
    except Exception:
        pass

    if items:
        print(f"  Cleaned: {', '.join(items)}")
    else:
        print(f"  Nothing to clean")


# ── Main ─────────────────────────────────────────────────────

def main():
    global BASE_URL, FULL_MODE

    parser = argparse.ArgumentParser(description="Voc Studio API test suite")
    parser.add_argument("--url", default="http://127.0.0.1:4200",
                        help="Server URL (default: http://127.0.0.1:4200)")
    parser.add_argument("--full", action="store_true",
                        help="Include TTS/LLM-dependent tests")
    args = parser.parse_args()

    BASE_URL = args.url.rstrip("/")
    FULL_MODE = args.full

    print(f"Voc Studio API Tests")
    print(f"Server: {BASE_URL}")
    print(f"Mode:   {'FULL (includes TTS/LLM tests)' if FULL_MODE else 'QUICK (no TTS/LLM)'}")

    try:
        run_all_tests()
    finally:
        cleanup()

    # Summary
    total = results["passed"] + results["failed"] + results["skipped"]
    print(f"\n{'=' * 60}")
    print(f"  RESULTS: {results['passed']} passed, {results['failed']} failed, "
          f"{results['skipped']} skipped  (total: {total})")
    print(f"{'=' * 60}")

    if failures:
        print(f"\nFailed tests:")
        for name, err in failures:
            # Truncate long error messages
            short = err.split("\n")[0][:200]
            print(f"  - {name}: {short}")

    sys.exit(1 if results["failed"] > 0 else 0)


if __name__ == "__main__":
    main()
