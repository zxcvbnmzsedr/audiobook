# Voc Studio — AI Audiobook Studio

English | [中文](README_CN.md)

Turn any book or novel into a fully voice-acted audiobook with AI-driven script tagging and text-to-speech. Powered by a built-in Qwen3-TTS engine, with batch processing and an in-browser editor for line-by-line fine-tuning before export.

## 🎧 Listen to Samples

- **▶️ Sample playlist on YouTube:** https://www.youtube.com/watch?v=54TlmKga5Yo&list=PLL1mTdaXmcGr1pJxg6gt84OwE7kMutIcF&index=3

---

## Features

### AI-Driven Pipeline
- **Local & cloud LLM support** — works with any OpenAI-compatible API (LM Studio, Ollama, OpenAI, etc.)
- **Automatic script tagging** — the LLM parses raw text into structured JSON with speakers, dialogue, and TTS directions
- **LLM script review** — optional second-pass LLM validation that fixes common tagging mistakes
- **Smart chunking** — groups consecutive lines by speaker (up to 500 characters) to keep speech natural
- **Context retention** — passes the character roster and the last 3 script entries between chunks to keep characters and style consistent

### Voice Generation
- **Built-in TTS engine** — Qwen3-TTS runs locally, no external server required
- **Multilingual** — Chinese, English, French, German, Italian, Japanese, Korean, Portuguese, Russian, Spanish, or auto-detect
- **Preset voices** — 9 pre-trained voices with instruction-based emotion/tone control
- **Voice cloning** — clone any voice from just 5–15 seconds of reference audio
- **Voice designer** — create new voices from a text description (e.g. "a warm, deep male voice with a steady tone")
- **LoRA voice training** — fine-tune the base model on a custom voice dataset to create a persistent voice identity
- **Built-in LoRA presets** — ready-to-use pre-trained voice adapters
- **Dataset builder** — an interactive tool to build training datasets entry by entry, with preview
- **Batch processing** — generate dozens of voice chunks at once, 3–6× faster than real time
- **Codec compilation** — optional `torch.compile` optimization for 3–4× faster batch decoding

### Web UI Editor
- **Clean interface** — a 5-step core pipeline (Setup, Script, Voice, Editor, Result) plus advanced tools (Designer, Dataset, Training)
- **Chunk editing** — edit the speaker, text, and direction of any line
- **Selective regeneration** — re-render a single chunk on its own
- **Live progress** — real-time logs and status tracking for every operation
- **Audio preview** — play any chunk individually or preview the whole audiobook in order

### Export Options
- **Merged audiobook** — a single MP3 with all voices and natural pauses
- **Individual voice lines** — each line exported as a separate MP3, ready for editing in a DAW
- **Audacity export** — one-click ZIP with per-speaker WAV tracks, a LOF project file, and labels
- **M4B audiobook** — chaptered M4B (AAC) with auto-detected or per-chunk chapters, for Audiobookshelf, Apple Books, VLC, and more

---

## System Requirements

- [Pinokio](https://pinokio.computer/)
- An LLM server (pick one):
  - [LM Studio](https://lmstudio.ai/) (local) — Qwen3 or a similar model recommended
  - [Ollama](https://ollama.ai/) (local)
  - [OpenAI API](https://platform.openai.com/) (cloud)
  - Any OpenAI-compatible API
- **GPU:** 8 GB VRAM minimum, 16 GB+ recommended — see the compatibility table below
  - Each TTS model uses ~3.4 GB VRAM; the remaining VRAM determines batch size
  - CPU mode works on every platform but is noticeably slower
- **RAM:** 16 GB recommended (8 GB minimum)
- **Disk:** ~20 GB (8 GB venv/PyTorch + ~7 GB model weights + audio workspace)

### GPU Compatibility

| GPU | OS | Status | Driver requirement | Notes |
|-----|----|--------|--------------------|-------|
| **NVIDIA** | Windows | Full support | Driver 550+ (CUDA 12.8) | Includes Flash Attention accelerated encoding |
| **NVIDIA** | Linux | Full support | Driver 550+ (CUDA 12.8) | Includes Flash Attention + Triton |
| **AMD** | Linux | Full support | ROCm 6.3 | ROCm optimizations applied automatically |
| **AMD** | Windows | CPU only | N/A | No GPU acceleration — use Linux for AMD GPU acceleration |
| **Apple Silicon** | macOS | CPU only | N/A | MPS acceleration not yet supported; runs but is slow |

> **Tip:** No external TTS server is required. Voc Studio bundles the Qwen3-TTS engine, and model weights download automatically from Hugging Face on first use (~3.5 GB per model variant).

---

## Installation

### Option A: Pinokio (recommended)

1. Install [Pinokio](https://pinokio.computer/) if you haven't already
2. Open Voc Studio in Pinokio: **[Install via Pinokio](https://beta.pinokio.co/apps/github-com-zxcvbnmzsedr-audiobook)**
   - Or manually: click **Download** in Pinokio and paste `https://github.com/zxcvbnmzsedr/audiobook`
3. Click **Install** to install dependencies
4. Click **Start** to launch the web interface

### Option B: Google Colab (no install)

No GPU or an incompatible system? Run Voc Studio in your browser on a free T4 GPU:

[![Open in Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/zxcvbnmzsedr/audiobook/blob/main/voc_studio_colab.ipynb)

A free [ngrok account](https://dashboard.ngrok.com/signup) is needed to tunnel the Web UI. See the notebook for details.

---

## First Launch — Read This First

If this is your first time running Voc Studio, read this section carefully before doing anything.

### 1. You must start an LLM server first

Voc Studio **does not include** an LLM — it connects to an external one over an API. Before generating a script, start one of:

| Server | Default URL | How to install |
|--------|-------------|----------------|
| [LM Studio](https://lmstudio.ai/) | `http://localhost:1234/v1` | Download, load a model, start the server |
| [Ollama](https://ollama.ai/) | `http://localhost:11434/v1` | Install, then run `ollama run qwen3` |
| [OpenAI API](https://platform.openai.com/) | `https://api.openai.com/v1` | Get an API key |

If the LLM server isn't running when you click **Generate Tagged Script**, generation will fail. Check the Pinokio terminal for error details.

### 2. The first TTS generation downloads ~3.5 GB of models

TTS models are **not bundled** and download automatically from Hugging Face the first time you generate audio:

- **~3.5 GB per model variant** (CustomVoice, Base/Clone, VoiceDesign)
- Only the variants you use are downloaded (most users start with CustomVoice)
- Downloads run in the background — **watch progress in the Pinokio terminal**
- The Web UI may look unresponsive during this — that's normal, it's waiting for the download
- After the first download, models are cached locally and load in seconds

> **Tip:** If a download seems stuck, check your network. If it fails, restart the app and try again — it resumes from where it stopped.

> **Users in mainland China:** If Hugging Face is slow or unreachable, set a mirror before launch: set the `HF_ENDPOINT` environment variable to `https://hf-mirror.com`. You can also add it to the `env` field in start.js: `env: { HF_ENDPOINT: "https://hf-mirror.com" }`. If you hit rate limits, sign up for a free [Hugging Face account](https://huggingface.co/join) and set `HF_TOKEN` to your access token.

### 3. The first batch needs extra warm-up time

The first batch generation in each session is slower than the ones that follow:

- **MIOpen auto-tuning** (AMD GPU): the GPU kernel optimizer runs once per session, adding ~30–60 s
- **Codec compilation** (if enabled): a one-time ~30–60 s warm-up, after which all batches are 3–4× faster
- **This is normal.** Generation speed stabilizes after the first batch.

### 4. VRAM determines what you can do

| Available VRAM | What you can do |
|----------------|-----------------|
| 8 GB | One model at a time, small batches (2–5 chunks), may need CPU offload |
| 16 GB | Comfortable for most use cases, batches of 10–20 chunks |
| 24 GB+ | Full speed, batches of 40–60 chunks with codec compilation |

- If you run low on VRAM, lower **Parallel Tasks** in the config tab or reduce the script text chunk size
- Close other GPU apps (games, other AI tools) before generating
- Switching voice type (Custom → Clone → LoRA) unloads and reloads the model, temporarily freeing VRAM

### 5. Where to look when something goes wrong

The Web UI shows high-level status; **detailed logs live in the Pinokio terminal**:

- Click **Terminal** in the Pinokio sidebar for live output
- Model loading, download progress, VRAM estimates, and errors all show up here
- If a generation fails silently in the UI, the terminal shows why

---

## Quick Start

The interface is split into a **5-step core pipeline** (green numbered tabs) and **advanced tools** (blue unnumbered tabs). The core pipeline alone is enough to produce an audiobook.

### Core Pipeline

**Step 1 — Config**
Configure the LLM connection and TTS engine. At minimum:
- **Base URL:** `http://localhost:1234/v1` (LM Studio) or `http://localhost:11434/v1` (Ollama)
- **API Key:** your API key (`local` for local servers)
- **Model name:** the model to use (e.g. `qwen2.5-14b`)
- **TTS mode:** `local` (built-in engine, recommended) — loads the model directly, no external server
- Click **Save Config** when done

**Step 2 — Script**
- Pick a book file with the file selector (.txt, .md, or .epub) — it uploads automatically, and EPUB files convert to plain text
- Click **Generate Tagged Script** — sends the book to the LLM and splits it into tagged chunks with speaker labels and voice directions
- *(Optional)* If the generated script has issues, click **Review Script** — runs a second-pass LLM that fixes speaker-attribution and formatting errors
- Use the save controls below to keep the script for later

**Step 3 — Voice**
Each character detected in the script gets a voice card. For every speaker:
- Pick a voice type: Custom Voice (easiest), Voice Clone, LoRA Voice, or Voice Design
- With Custom Voice, choose from 9 presets (Ryan, Serena, Aiden, etc.) and optionally set a per-character style (e.g. "calm narrator tone")
- Changes save automatically — see [Voice Types](https://github.com/zxcvbnmzsedr/audiobook/wiki/Voice-Types) for details on each type

**Step 4 — Editor**
- Click **Render Pending** to batch-generate audio for all chunks
- Click a single chunk to preview it, or click **Play in Order** to preview sequentially
- Inline-edit any chunk's text, speaker, or direction, then regenerate it on its own
- When you're happy, click **Merge All** to combine everything into the final audiobook

**Step 5 — Result**
- Preview the finished audiobook in the browser
- Download the MP3, export **M4B** (with chapter markers), or click **Export to Audacity** for per-speaker WAV tracks
- M4B export supports title, author, and narrator metadata, and can embed cover art

### Advanced Tools (optional)

These tabs are for power users who want more voice control:

- **Designer** — create a new voice from a text description (e.g. "an older woman with a slightly raspy voice"). Once saved, it can be used as a clone reference in the Voice tab
- **Dataset** — interactively build a LoRA training dataset, entry by entry, with audio preview
- **Training** — train a LoRA adapter on a voice dataset to create a persistent, instruction-following voice identity

---

## For Developers

Want to run from source instead of Pinokio? Voc Studio is a three-layer, local-first workspace.

### Architecture

- **FastAPI backend** — library, chapters, script generation, character pool, voice config, audio generation, and export
- **React web frontend** — library, workbench, capability center, and settings pages
- **Electron desktop shell** — launches or attaches to the local backend, manages the data and log directories, and provides local run diagnostics

### Project Structure

```text
.
├── app/                     # FastAPI backend, script pipeline, TTS, test scripts
│   ├── app.py               # backend entry point
│   ├── generate_script_chapters.py
│   ├── review_script.py
│   ├── tts.py
│   └── test_api.py
├── frontend/                # React + Vite + Ant Design frontend
├── desktop/                 # Electron desktop shell
├── builtin_lora/            # built-in LoRA manifests
├── books/                   # local book runtime data, not committed by default
├── pyproject.toml           # Python/uv dependencies
├── package.json             # pnpm workspace scripts
├── Dockerfile
└── docker-compose.yml
```

### Run From Source

Requirements: Python `>=3.11,<3.14`, [`uv`](https://docs.astral.sh/uv/), Node.js 22+, `pnpm`, and FFmpeg.

```bash
# Install dependencies
uv sync
pnpm install

# Start the backend (default: http://127.0.0.1:4200)
uv run python app/app.py

# Develop the frontend
pnpm frontend:dev

# Build the frontend into the backend's static directory
pnpm frontend:build

# Launch the desktop shell
pnpm desktop

# Or attach the desktop shell to an already-running backend
pnpm desktop:attach
```

### Common Commands

```bash
# Quick backend API test (start the backend first)
uv run python app/test_api.py

# Full test with real LLM/TTS (slower)
uv run python app/test_api.py --full

# Frontend checks
pnpm frontend:typecheck
pnpm frontend:lint
pnpm frontend:build

# Desktop checks
pnpm desktop:check

# Desktop directory package
pnpm desktop:pack
```

### Docker

On an NVIDIA GPU host you can try the Docker deployment:

```bash
docker compose up --build
# then open http://127.0.0.1:4200
```

Docker Compose mounts runtime data under `data/` and uses a Docker volume to cache Hugging Face models.

### Runtime Configuration

The backend entry point supports these environment variables:

```bash
VOC_STUDIO_HOST=127.0.0.1
VOC_STUDIO_PORT=4200
VOC_STUDIO_RELOAD=1
VOC_STUDIO_DATA_DIR=/path/to/data
VOC_STUDIO_BUILTIN_LORA_HF_REPO=zxcvbnmzsedr/voc-studio
```

Attach the desktop shell to a running backend:

```bash
VOC_STUDIO_BACKEND_URL=http://127.0.0.1:4200 pnpm desktop
```

### Data Directories

By default the web/backend stores runtime data at the repo root: `books/`, `voicelines/`, `designed_voices/`, `clone_voices/`, `lora_models/`, `lora_datasets/`, `dataset_builder/`, `cache/`, and `logs/`.

In packaged desktop mode, runtime data goes to the system app-data directory:

- macOS: `~/Library/Application Support/Voc Studio/data`
- Windows: `%APPDATA%/Voc Studio/data`
- Linux: `~/.config/Voc Studio/data`

---

## FAQ

### Script generation fails
- Confirm the LLM server is running and reachable
- Verify the model name matches the loaded model
- Try a different model — some are poor at JSON output
- Chain-of-thought models (DeepSeek-R1, GLM4, etc.) can interfere with JSON output. To use one, add `<think>` to the **Banned Tokens** field in settings to disable thinking mode

### Model download fails or is very slow
- TTS models (~3.5 GB each) download from Hugging Face on first use
- **Users in mainland China:** set `HF_ENDPOINT=https://hf-mirror.com` to use a domestic mirror
- If rate-limited, sign up for a free [Hugging Face account](https://huggingface.co/join) and set `HF_TOKEN`
- Interrupted downloads resume automatically — just restart the app

### TTS generation fails
- Check the Pinokio terminal for model-loading errors
- Make sure you have enough VRAM (16 GB+ recommended for bfloat16)
- Check that every speaker's settings in voice_config.json are valid
- For voice cloning, confirm the reference audio exists and its transcript is accurate

### Generation is slow
- Enable **Compile Codec** in settings (3–4× faster after the first warm-up)
- Increase **Parallel Tasks** (batch size) if VRAM allows
- Use **Render Pending** for batch generation instead of generating one at a time
- A slow first batch is normal (see "First Launch" above)

### Out of VRAM / OOM errors
- Lower **Max Characters Per Batch** in settings (especially with clone/LoRA voices and long reference audio)
- Lower **Parallel Tasks** (batch size)
- Close other GPU-heavy apps
- If it still fails, try `device: cpu` (much slower)

### MP3 files are corrupt or tiny (428 bytes)
Conda's bundled ffmpeg often lacks the MP3 encoder (libmp3lame) on Windows. Voc Studio auto-detects this and falls back to WAV. For MP3 output:
- Install ffmpeg with MP3 support: `conda install -c conda-forge ffmpeg`
- Or remove conda's ffmpeg to use the system one: `conda remove ffmpeg`

### Tips for Chinese books
- Select "Chinese" or "Auto-detect" in the **TTS Language** dropdown in the config tab
- The default LLM prompts are written for English — for Chinese books, adapt the prompts in the "Prompt Customization" section of the config tab to Chinese dialogue conventions (e.g. 「」 quotation marks)
- The `default_prompts.txt` and `review_prompts.txt` files can be edited permanently; changes take effect immediately without a restart

---

## Recommended LLM Models

For script generation, non-thinking models work best:
- **Qwen3-next** (80B-A3B-instruct) — excellent JSON output and instruction following
- **Gemma3** (27B recommended) — excellent JSON output and instruction following
- **Qwen2.5** (any size) — reliable JSON output
- **Qwen3** (non-thinking variants)
- **Llama 3.1/3.2** — strong character distinction
- **Mistral/Mixtral** — fast and reliable

---

## More Documentation

- [中文 README](README_CN.md) — full Chinese documentation
- [Wiki](https://github.com/zxcvbnmzsedr/audiobook/wiki) — detailed guides: voice types, LoRA training, batch generation, and more

## License

MIT
