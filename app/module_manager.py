"""Optional capability detection and installation helpers for Voc Studio."""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Any, Callable


PackageRequirement = tuple[str, str]


@dataclass(frozen=True)
class ModuleDefinition:
    id: str
    name: str
    category: str
    summary: str
    install_kind: str = "none"
    disk_estimate_gb: float = 0
    package_requirements: tuple[PackageRequirement, ...] = field(default_factory=tuple)
    executable_requirements: tuple[str, ...] = field(default_factory=tuple)
    config_requirements: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    model_id: str | None = None
    manual_hint: str = ""


MODULES: tuple[ModuleDefinition, ...] = (
    ModuleDefinition(
        id="llm.openai_compatible",
        name="OpenAI 兼容 LLM",
        category="LLM",
        summary="连接 OpenAI、LM Studio、Ollama 或其他兼容服务。",
        config_requirements=(("llm", "base_url"), ("llm", "model_name")),
    ),
    ModuleDefinition(
        id="tts.edge",
        name="Edge TTS",
        category="TTS",
        summary="使用 Edge 在线语音生成轻量音频。",
        package_requirements=(("edge_tts", "edge-tts"),),
    ),
    ModuleDefinition(
        id="tts.dashscope",
        name="DashScope Qwen TTS",
        category="TTS",
        summary="使用 DashScope 云端 Qwen TTS 音色。",
        package_requirements=(("dashscope", "dashscope"),),
        config_requirements=(("tts", "dashscope_api_key"),),
    ),
    ModuleDefinition(
        id="tts.volcengine",
        name="火山 TTS",
        category="TTS",
        summary="使用火山引擎在线语音服务。",
        package_requirements=(("requests", "requests"),),
        config_requirements=(("tts", "volcengine_api_key"),),
    ),
    ModuleDefinition(
        id="tts.qwen3.custom",
        name="Qwen3-TTS 自定义音色",
        category="本地模型",
        summary="本地加载 CustomVoice 模型，适合常规多人声有声书生成。",
        install_kind="hf_snapshot",
        disk_estimate_gb=3.5,
        package_requirements=(
            ("qwen_tts", "qwen-tts"),
            ("torch", "torch"),
            ("transformers", "transformers"),
            ("huggingface_hub", "huggingface_hub"),
        ),
        model_id="Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice",
    ),
    ModuleDefinition(
        id="tts.qwen3.clone",
        name="Qwen3-TTS 声音克隆",
        category="本地模型",
        summary="本地加载 Base 模型，使用参考音频克隆角色声线。",
        install_kind="hf_snapshot",
        disk_estimate_gb=3.5,
        package_requirements=(
            ("qwen_tts", "qwen-tts"),
            ("torch", "torch"),
            ("transformers", "transformers"),
            ("huggingface_hub", "huggingface_hub"),
        ),
        model_id="Qwen/Qwen3-TTS-12Hz-1.7B-Base",
    ),
    ModuleDefinition(
        id="tts.qwen3.design",
        name="Qwen3-TTS 声音设计",
        category="本地模型",
        summary="本地加载 VoiceDesign 模型，通过文字描述生成新声音。",
        install_kind="hf_snapshot",
        disk_estimate_gb=3.5,
        package_requirements=(
            ("qwen_tts", "qwen-tts"),
            ("torch", "torch"),
            ("transformers", "transformers"),
            ("huggingface_hub", "huggingface_hub"),
        ),
        model_id="Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign",
    ),
    ModuleDefinition(
        id="voice.lora_training",
        name="LoRA 声音训练",
        category="训练",
        summary="启用本地 LoRA 微调和适配器预览。",
        package_requirements=(
            ("qwen_tts", "qwen-tts"),
            ("torch", "torch"),
            ("peft", "peft"),
            ("soundfile", "soundfile"),
        ),
        manual_hint="需要可用 GPU 时才建议启用训练任务。",
    ),
    ModuleDefinition(
        id="audio.ffmpeg",
        name="FFmpeg 音频导出",
        category="工具",
        summary="启用 MP3、M4B、Audacity 多轨导出等音频处理。",
        install_kind="manual",
        executable_requirements=("ffmpeg",),
        manual_hint="请通过系统包管理器、conda-forge 或官方构建安装带 MP3 编码支持的 ffmpeg。",
    ),
)


def module_definition(module_id: str) -> ModuleDefinition | None:
    return next((module for module in MODULES if module.id == module_id), None)


def _package_installed(import_name: str) -> bool:
    return importlib.util.find_spec(import_name) is not None


def _executable_version(command: str) -> str:
    path = shutil.which(command)
    if not path:
        return ""
    try:
        result = subprocess.run(
            [path, "-version"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        first_line = (result.stdout or result.stderr or "").splitlines()[0:1]
        return first_line[0] if first_line else path
    except Exception:
        return path


def _model_cached(model_id: str | None) -> tuple[bool, str]:
    if not model_id:
        return True, ""
    if not _package_installed("huggingface_hub"):
        return False, ""
    try:
        from huggingface_hub import try_to_load_from_cache

        cached = try_to_load_from_cache(model_id, "config.json")
        if isinstance(cached, str):
            return True, os.path.dirname(cached)
    except Exception:
        return False, ""
    return False, ""


def _config_value(config: dict[str, Any], section: str, key: str) -> Any:
    value = config.get(section)
    if not isinstance(value, dict):
        return None
    return value.get(key)


def _desktop_paths(data_dir: str, cache_dir: str) -> dict[str, str]:
    return {
        "data_dir": data_dir,
        "cache_dir": cache_dir,
        "hf_home": os.environ.get("HF_HOME", ""),
        "python": sys.executable,
    }


def module_status(
    module: ModuleDefinition,
    *,
    config: dict[str, Any],
    data_dir: str,
    cache_dir: str,
) -> dict[str, Any]:
    missing_packages = [
        {"import_name": import_name, "package": package}
        for import_name, package in module.package_requirements
        if not _package_installed(import_name)
    ]
    missing_executables = [
        command for command in module.executable_requirements if not shutil.which(command)
    ]
    missing_config = [
        {"section": section, "key": key}
        for section, key in module.config_requirements
        if not _config_value(config, section, key)
    ]
    model_cached, model_path = _model_cached(module.model_id)
    installed = not missing_packages and not missing_executables and model_cached

    if missing_packages or missing_executables:
        state = "unavailable"
    elif module.model_id and not model_cached:
        state = "not_installed"
    elif missing_config:
        state = "needs_config"
    else:
        state = "ready"

    executable_versions = {
        command: _executable_version(command)
        for command in module.executable_requirements
        if shutil.which(command)
    }

    return {
        "id": module.id,
        "name": module.name,
        "category": module.category,
        "summary": module.summary,
        "state": state,
        "installed": installed,
        "install_kind": module.install_kind,
        "installable": module.install_kind == "hf_snapshot",
        "disk_estimate_gb": module.disk_estimate_gb,
        "model_id": module.model_id,
        "model_cached": model_cached,
        "model_path": model_path,
        "missing_packages": missing_packages,
        "missing_executables": missing_executables,
        "missing_config": missing_config,
        "executable_versions": executable_versions,
        "manual_hint": module.manual_hint,
        "paths": _desktop_paths(data_dir, cache_dir),
    }


def all_module_statuses(
    *,
    config: dict[str, Any],
    data_dir: str,
    cache_dir: str,
    install_task: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    modules = [
        module_status(module, config=config, data_dir=data_dir, cache_dir=cache_dir)
        for module in MODULES
    ]
    if install_task and install_task.get("running"):
        active_id = install_task.get("module_id")
        for module in modules:
            if module["id"] == active_id:
                module["state"] = "installing"
                module["installing"] = True
    return modules


def install_huggingface_snapshot(
    module: ModuleDefinition,
    *,
    log: Callable[[str], None],
    should_cancel: Callable[[], bool] | None = None,
) -> None:
    if module.install_kind != "hf_snapshot" or not module.model_id:
        raise RuntimeError(f"Module {module.id} does not support automatic installation.")
    if not _package_installed("huggingface_hub"):
        raise RuntimeError("Missing Python package: huggingface_hub")

    from huggingface_hub import snapshot_download

    if should_cancel and should_cancel():
        raise RuntimeError("Install cancelled.")
    log(f"Downloading {module.model_id}...")
    local_path = snapshot_download(repo_id=module.model_id)
    if should_cancel and should_cancel():
        raise RuntimeError("Install cancelled.")
    log(f"Model snapshot ready: {local_path}")
