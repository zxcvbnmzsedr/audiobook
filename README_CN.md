# Voc Studio — AI 有声书工作台

[English](README.md) | 中文

利用 AI 驱动的脚本标注和文本转语音技术，将任何书籍或小说转化为全配音有声书。可选用云端 TTS 服务 —— 阿里云千问 TTS（DashScope）、火山引擎（豆包）TTS、微软 Edge TTS —— 或内置的本地 Qwen3-TTS 引擎，支持批量处理，并提供浏览器端编辑器，可逐行精调后导出。

## 🎧 在线试听

- **▶️ YouTube 试听播放列表：** https://www.youtube.com/watch?v=54TlmKga5Yo&list=PLL1mTdaXmcGr1pJxg6gt84OwE7kMutIcF&index=3

---

## 主要功能

### AI 驱动流水线
- **本地与云端 LLM 支持** — 兼容任何 OpenAI 兼容 API（LM Studio、Ollama、OpenAI 等）
- **自动脚本标注** — LLM 将文本解析为包含说话人、对话和 TTS 指令的 JSON 格式
- **LLM 脚本审校** — 可选的二次 LLM 校验，修复常见标注错误
- **智能分块** — 按说话人连续分组（最多 500 字符），保持自然语流
- **上下文保持** — 在分块间传递角色名单和最后 3 条脚本条目，确保角色和风格连贯

### 语音生成
- **多种 TTS 后端** — 按项目需要自由选择：
  - **云端在线：** 阿里云千问 TTS（DashScope）、火山引擎（豆包）TTS、微软 Edge TTS —— 无需 GPU、无需下载模型
  - **本地引擎：** 内置 Qwen3-TTS（自定义音色 / 克隆 / 声音设计）—— 离线运行，无需外部服务器
- **多语言支持** — 中文、英语、法语、德语、意大利语、日语、韩语、葡萄牙语、俄语、西班牙语，或自动检测
- **预置声音** — 9 种预训练声音，支持基于指令的情感/语调控制
- **声音克隆** — 仅需 5-15 秒参考音频即可克隆任何声音
- **声音设计器** — 通过文字描述创建新声音（例如："温暖、低沉的男性声音，语调沉稳"）
- **LoRA 声音训练** — 在自定义语音数据集上微调 Base 模型，创建持久的声音身份
- **内置 LoRA 预设** — 开箱即用的预训练声音适配器
- **数据集构建器** — 交互式工具，逐条创建训练数据集，支持预览
- **批量处理** — 同时生成数十个语音块，吞吐量达实时速度的 3-6 倍
- **编解码器编译** — 可选的 `torch.compile` 优化，批量解码速度提升 3-4 倍

### Web UI 编辑器
- **简洁界面** — 5 步核心流水线（设置、脚本、声音、编辑器、结果）加高级工具（设计器、数据集、训练）
- **分块编辑** — 编辑任意行的说话人、文本和指令
- **选择性重新生成** — 单独重新渲染某一分块
- **实时进度** — 所有操作的实时日志和状态跟踪
- **音频预览** — 单独播放或按顺序预览整本有声书

### 导出选项
- **合并有声书** — 包含所有声音和自然停顿的单个 MP3 文件
- **单独语音行** — 每行单独导出 MP3，方便在 DAW 中编辑
- **Audacity 导出** — 一键导出 ZIP，包含按说话人分轨的 WAV 文件、LOF 项目文件和标签
- **M4B 有声书** — 带章节标记的 M4B 格式（AAC），支持自动检测章节或逐块章节，适用于 Audiobookshelf、Apple Books、VLC 等播放器

---

## 系统要求

- [Pinokio](https://pinokio.computer/)
- LLM 服务器（以下任选其一）：
  - [LM Studio](https://lmstudio.ai/)（本地）— 推荐使用 Qwen3 或类似模型
  - [Ollama](https://ollama.ai/)（本地）
  - [OpenAI API](https://platform.openai.com/)（云端）
  - 任何 OpenAI 兼容 API
- **GPU：** 最低 8 GB 显存，推荐 16 GB 以上 — 详见下方兼容性表格
  - 每个 TTS 模型占用约 3.4 GB 显存；剩余显存决定批量大小
  - 所有平台均可使用 CPU 模式，但速度明显较慢
- **内存：** 推荐 16 GB（最低 8 GB）
- **磁盘：** 约 20 GB（8 GB venv/PyTorch + 约 7 GB 模型权重 + 音频工作空间）

### GPU 兼容性

| GPU | 操作系统 | 状态 | 驱动要求 | 备注 |
|-----|---------|------|---------|------|
| **NVIDIA** | Windows | 完全支持 | 驱动 550+（CUDA 12.8） | 包含 Flash Attention 加速编码 |
| **NVIDIA** | Linux | 完全支持 | 驱动 550+（CUDA 12.8） | 包含 Flash Attention + Triton |
| **AMD** | Linux | 完全支持 | ROCm 6.3 | 自动应用 ROCm 优化 |
| **AMD** | Windows | 仅 CPU | 不适用 | 不支持 GPU 加速 — 如需 AMD GPU 加速请使用 Linux |
| **Apple Silicon** | macOS | 仅 CPU | 不适用 | 暂不支持 MPS 加速，可运行但速度较慢 |

> **提示：** GPU 和模型下载仅在使用**本地** Qwen3-TTS 引擎时才需要。如果使用**云端** TTS 后端（阿里云千问 / DashScope、火山引擎豆包、Edge TTS），则无需 GPU、无需下载模型、也无需外部 TTS 服务器，只需填写对应的 API Key。本地 Qwen3-TTS 引擎会在首次使用时从 Hugging Face 下载模型权重（每个模型变体约 3.5 GB）。

---

## 安装

### 方式 A：Pinokio（推荐）

1. 安装 [Pinokio](https://pinokio.computer/)（如尚未安装）
2. 在 Pinokio 中打开 Voc Studio：**[通过 Pinokio 安装](https://beta.pinokio.co/apps/github-com-zxcvbnmzsedr-audiobook)**
   - 或手动操作：在 Pinokio 中点击 **下载**，粘贴 `https://github.com/zxcvbnmzsedr/audiobook`
3. 点击 **安装** 安装依赖
4. 点击 **启动** 启动 Web 界面

### 方式 B：Google Colab（无需安装）

没有 GPU 或系统不兼容？在浏览器中使用免费 T4 GPU 运行 Voc Studio：

[![在 Colab 中打开](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/zxcvbnmzsedr/audiobook/blob/main/voc_studio_colab.ipynb)

需要免费的 [ngrok 账号](https://dashboard.ngrok.com/signup) 用于 Web UI 隧道。详细说明请参阅 notebook。

---

## 首次启动 — 必读

如果你是第一次运行 Voc Studio，请在操作前仔细阅读本节。

### 1. 必须先启动 LLM 服务器

Voc Studio **不包含** LLM — 它通过 API 连接到外部 LLM。在生成脚本之前，你必须先启动以下任一服务：

| 服务器 | 默认 URL | 安装方式 |
|--------|---------|---------|
| [LM Studio](https://lmstudio.ai/) | `http://localhost:1234/v1` | 下载安装，加载模型，启动服务器 |
| [Ollama](https://ollama.ai/) | `http://localhost:11434/v1` | 安装后运行 `ollama run qwen3` |
| [OpenAI API](https://platform.openai.com/) | `https://api.openai.com/v1` | 获取 API Key |

如果在点击 **生成标注脚本** 时 LLM 服务器未运行，生成将会失败。请查看 Pinokio 终端获取错误详情。

### 2. 首次 TTS 生成会下载约 3.5 GB 模型

TTS 模型**不包含在安装中**，首次生成音频时会自动从 Hugging Face 下载：

- **每个模型变体约 3.5 GB**（CustomVoice、Base/克隆、VoiceDesign）
- 只有你使用的变体才会下载（大多数用户从 CustomVoice 开始）
- 下载在后台进行 — **请在 Pinokio 终端中查看进度**
- 此时 Web UI 可能看起来没有响应，这是正常的 — 它在等待下载完成
- 首次下载后，模型将缓存在本地，后续加载只需几秒钟

> **提示：** 如果下载似乎卡住了，请检查网络连接。如果失败，重启应用再试 — 会从断点处继续下载。

> **中国大陆用户：** 如果 Hugging Face 下载缓慢或无法连接，请在启动前设置镜像：将环境变量 `HF_ENDPOINT` 设为 `https://hf-mirror.com`。也可以在 start.js 的 `env` 字段中添加：`env: { HF_ENDPOINT: "https://hf-mirror.com" }`。如果遇到速率限制，可注册免费的 [Hugging Face 账号](https://huggingface.co/join) 并设置 `HF_TOKEN` 为你的访问令牌。

### 3. 首批生成需要额外预热时间

每个会话中的首次批量生成比后续生成更慢：

- **MIOpen 自动调优**（AMD GPU）：GPU 核心优化器每个会话运行一次，增加约 30-60 秒
- **编解码器编译**（如已启用）：一次性约 30-60 秒预热，之后所有批次速度提升 3-4 倍
- **这是正常现象。** 首批之后，生成速度会稳定下来

### 4. 显存决定你能做什么

| 可用显存 | 可行操作 |
|---------|---------|
| 8 GB | 一次只能加载一个模型，小批量（2-5 个语音块），可能需要 CPU 卸载 |
| 16 GB | 大多数用例都能舒适运行，批量 10-20 个语音块 |
| 24 GB+ | 全速运行，批量 40-60 个语音块，搭配编解码器编译 |

- 如果显存不足，请在配置标签页中降低 **并行任务数** 或减小脚本文本分块大小
- 生成前关闭其他 GPU 应用程序（游戏、其他 AI 工具）
- 切换声音类型（Custom → Clone → LoRA）时会卸载并重新加载模型，暂时释放显存

### 5. 出问题时去哪里查看

Web UI 显示的是高层状态，**详细日志在 Pinokio 终端中**：

- 点击 Pinokio 侧栏中的 **终端** 查看实时输出
- 模型加载、下载进度、显存估算和错误信息都会显示在这里
- 如果 UI 中生成静默失败，终端会显示原因

---

## 快速入门

界面分为 **5 步核心流水线**（绿色标签页，带编号）和 **高级工具**（蓝色标签页，无编号）。只需核心流水线即可生成有声书。

### 核心流水线

**第 1 步 — 配置**
配置 LLM 连接和 TTS 引擎，至少需要：
- **Base URL**：`http://localhost:1234/v1`（LM Studio）或 `http://localhost:11434/v1`（Ollama）
- **API 密钥**：你的 API 密钥（本地服务器使用 `local`）
- **模型名称**：要使用的模型（例如 `qwen2.5-14b`）
- **TTS 后端**：可选 `local` 本地 Qwen3-TTS（内置、离线）或云端服务 —— 阿里云千问 TTS（DashScope）、火山引擎（豆包）TTS、Edge TTS；云端后端需填写对应 API Key，但无需 GPU
- 完成后点击 **保存配置**

**第 2 步 — 脚本**
- 使用文件选择器选取书籍文件（.txt、.md 或 .epub）— 选择后自动上传，EPUB 文件会自动转换为纯文本
- 点击 **生成标注脚本** — 将书籍发送给 LLM，分割为带有说话人标签和语音指令的标注块
- *（可选）* 如果生成的脚本有问题，点击 **审校脚本** — 运行二次 LLM 校验，修复说话人归属错误或格式问题
- 可以使用下方的保存功能将脚本保存供以后使用

**第 3 步 — 声音**
脚本中检测到的每个角色都会有一张声音卡片。为每个说话人：
- 选择声音类型：自定义声音（最简单）、声音克隆、LoRA 声音或声音设计
- 使用自定义声音时，从 9 个预设中选择（Ryan、Serena、Aiden 等），可选设置角色风格（例如"沉稳的旁白语调"）
- 更改自动保存 — 各类型详细说明参见 [Voice Types](https://github.com/zxcvbnmzsedr/audiobook/wiki/Voice-Types)

**第 4 步 — 编辑器**
- 点击 **渲染待处理** 批量生成所有语音块的音频
- 点击单个语音块试听，或点击 **顺序播放** 按顺序预览
- 可以内联编辑任何语音块的文本、说话人或指令，然后单独重新生成
- 满意后点击 **合并全部** 将所有内容合并为最终有声书

**第 5 步 — 结果**
- 在浏览器中试听完成的有声书
- 下载 MP3，导出 **M4B**（带章节标记），或点击 **导出到 Audacity** 导出按说话人分轨的 WAV 文件
- M4B 导出支持填写书名、作者、朗读者等元数据，并可嵌入封面图片

### 高级工具（可选）

这些标签页面向需要更多声音控制的高级用户：

- **设计器** — 通过文字描述创建新声音（例如"温和的年长女性，声音略带沙哑"）。保存后可在声音标签页中用作克隆参考
- **数据集** — 交互式构建 LoRA 训练数据集，逐条创建并支持音频预览
- **训练** — 在语音数据集上训练 LoRA 适配器，创建持久的声音身份，支持指令跟随

---

## 面向开发者

想从源码运行而不是用 Pinokio？Voc Studio 是一个本地优先、分三层的工作台。

### 项目架构

- **FastAPI 后端** — 负责书库、章节、脚本生成、人物池、声音配置、音频生成和导出
- **React Web 前端** — 提供书库、工作台、能力中心和设置页
- **Electron 桌面壳** — 启动或挂接本地后端，管理数据目录、日志目录和本地运行诊断

### 目录结构

```text
.
├── app/                     # FastAPI 后端、脚本流水线、TTS、测试脚本
│   ├── app.py               # 后端入口
│   ├── generate_script_chapters.py
│   ├── review_script.py
│   ├── tts.py
│   └── test_api.py
├── frontend/                # React + Vite + Ant Design 前端
├── desktop/                 # Electron 桌面壳
├── builtin_lora/            # 内置 LoRA manifest
├── books/                   # 本地书籍运行数据，默认不提交
├── pyproject.toml           # Python/uv 依赖
├── package.json             # pnpm workspace 脚本
├── Dockerfile
└── docker-compose.yml
```

### 从源码运行

环境要求：Python `>=3.11,<3.14`、[`uv`](https://docs.astral.sh/uv/)、Node.js 22+、`pnpm` 和 FFmpeg。

```bash
# 安装依赖
uv sync
pnpm install

# 启动后端（默认：http://127.0.0.1:4200）
uv run python app/app.py

# 开发前端
pnpm frontend:dev

# 构建前端到后端静态目录
pnpm frontend:build

# 启动桌面端
pnpm desktop

# 或挂接到已运行的后端
pnpm desktop:attach
```

### 常用命令

```bash
# 后端 API 快速测试，需要先启动后端
uv run python app/test_api.py

# 包含真实 LLM/TTS 的完整测试，耗时更长
uv run python app/test_api.py --full

# 前端检查
pnpm frontend:typecheck
pnpm frontend:lint
pnpm frontend:build

# 桌面端检查
pnpm desktop:check

# 桌面目录包
pnpm desktop:pack
```

### Docker

NVIDIA GPU 环境可尝试 Docker 部署：

```bash
docker compose up --build
# 然后访问 http://127.0.0.1:4200
```

Docker Compose 会挂载 `data/` 下的运行数据，并使用 Docker volume 缓存 Hugging Face 模型。

### 运行配置

后端入口支持这些环境变量：

```bash
VOC_STUDIO_HOST=127.0.0.1
VOC_STUDIO_PORT=4200
VOC_STUDIO_RELOAD=1
VOC_STUDIO_DATA_DIR=/path/to/data
VOC_STUDIO_BUILTIN_LORA_HF_REPO=zxcvbnmzsedr/voc-studio
```

挂接桌面端到已运行的后端：

```bash
VOC_STUDIO_BACKEND_URL=http://127.0.0.1:4200 pnpm desktop
```

### 数据目录

Web/后端默认把运行数据放在仓库根目录：`books/`、`voicelines/`、`designed_voices/`、`clone_voices/`、`lora_models/`、`lora_datasets/`、`dataset_builder/`、`cache/` 和 `logs/`。

桌面打包模式会把运行数据放到系统应用数据目录：

- macOS：`~/Library/Application Support/Voc Studio/data`
- Windows：`%APPDATA%/Voc Studio/data`
- Linux：`~/.config/Voc Studio/data`

---

## 常见问题

### 脚本生成失败
- 确认 LLM 服务器正在运行且可访问
- 验证模型名称与已加载模型一致
- 尝试使用其他模型 — 某些模型在 JSON 输出方面表现不佳
- 思维链模型（DeepSeek-R1、GLM4 等）可能干扰 JSON 输出。如需使用，请在设置中的 **Banned Tokens** 字段添加 `<think>` 以禁用思考模式

### 模型下载失败或速度很慢
- TTS 模型（每个约 3.5 GB）在首次使用时从 Hugging Face 下载
- **中国大陆用户**：设置环境变量 `HF_ENDPOINT=https://hf-mirror.com` 使用国内镜像
- 如遇速率限制，注册免费 [Hugging Face 账号](https://huggingface.co/join) 并设置 `HF_TOKEN`
- 下载中断后会自动续传 — 重启应用即可

### TTS 生成失败
- 查看 Pinokio 终端中的模型加载错误
- 确保有足够的显存（推荐 16 GB 以上 bfloat16）
- 检查 voice_config.json 中所有说话人的设置是否有效
- 克隆声音时，确认参考音频存在且转录文本准确

### 生成速度慢
- 在设置中启用 **Compile Codec**（首次预热后速度提升 3-4 倍）
- 如果显存允许，增加 **并行任务数**（批量大小）
- 使用 **渲染待处理** 批量生成，而不是逐条手动生成
- 首批生成较慢是正常现象（参见上方"首次启动"部分）

### 显存不足 / OOM 错误
- 在设置中降低 **每批最大字符数**（特别是使用克隆/LoRA 声音且参考音频较长时）
- 降低 **并行任务数**（批量大小）
- 关闭其他 GPU 密集型应用程序
- 如果仍然不行，尝试 `device: cpu`（速度会慢很多）

### MP3 文件损坏或很小（428 字节）
Conda 自带的 ffmpeg 在 Windows 上通常缺少 MP3 编码器（libmp3lame）。Voc Studio 会自动检测并回退到 WAV 格式。如需 MP3 输出：
- 安装带 MP3 支持的 ffmpeg：`conda install -c conda-forge ffmpeg`
- 或移除 conda 的 ffmpeg 以使用系统自带的：`conda remove ffmpeg`

### 中文书籍处理提示
- 在配置标签页的 **TTS 语言** 下拉菜单中选择"中文"或"自动检测"
- 默认 LLM 提示是为英文编写的 — 处理中文书籍时，建议在配置标签页的"提示词自定义"部分修改提示，使其适配中文对话约定（如使用「」引号等）
- 提示文件 `default_prompts.txt` 和 `review_prompts.txt` 可永久修改，更改即时生效无需重启

---

## 推荐 LLM 模型

用于脚本生成，非思维链模型效果最佳：
- **Qwen3-next**（80B-A3B-instruct）— JSON 输出和指令方向优秀
- **Gemma3**（推荐 27B）— JSON 输出和指令方向出色
- **Qwen2.5**（任意大小）— JSON 输出稳定
- **Qwen3**（非思维链变体）
- **Llama 3.1/3.2** — 角色区分能力强
- **Mistral/Mixtral** — 速度快，稳定可靠

---

## 更多文档

- [English README](README.md) — 完整英文文档
- [Wiki](https://github.com/zxcvbnmzsedr/audiobook/wiki) — 详细指南：声音类型、LoRA 训练、批量生成等

## 许可证

MIT
