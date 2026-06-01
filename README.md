# Voc Studio

Voc Studio 是一个本地优先的 AI 有声书工作台。它把小说或长文本拆成章节，调用外部 LLM 生成带说话人、对白和语音演绎指令的脚本，再通过 TTS 引擎生成多角色语音，最后合成为 MP3、M4B 或 Audacity 多轨素材。

当前项目包含三层：

- FastAPI 后端：负责书库、章节、脚本生成、人物池、声音配置、音频生成和导出。
- React Web 前端：提供书库、工作台、能力中心和设置页。
- Electron 桌面壳：启动或挂接本地后端，管理数据目录、日志目录和本地运行诊断。

## 主要能力

- 多书库管理：每本书有独立章节、脚本、人物池、声音配置、音频片段和导出结果。
- 章节级脚本生成：支持断点恢复、指定章节重跑、缺失章节修复和生成前 dry-run。
- 人物池与角色声音：自动识别角色，维护 canonical/aliases/traits/voice_profile，并为每个说话人配置独立音色。
- LLM 审校与诊断：脚本生成和审校都有状态、日志、结构化事件和用量信息。
- 多种 TTS 后端：本地 Qwen3-TTS、Edge TTS、DashScope、Volcengine、声音克隆、声音设计和 LoRA。
- 音频编辑闭环：可逐块试听、编辑文本/说话人/指令、单块重生、批量生成、章节合并和全书合并。
- 导出格式：MP3、章节 M4B、Audacity 多轨 ZIP、单独 voicelines。
- 桌面模式：Electron 自动选择本地端口，启动后端，限制外部导航，并提供运行诊断。

## 目录结构

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

## 环境要求

本地开发推荐：

- macOS、Linux 或 Windows
- Python `>=3.11,<3.14`
- `uv`
- Node.js 22+ 与 `pnpm`
- FFmpeg
- 至少一个 OpenAI-compatible LLM 服务，例如 LM Studio、Ollama、OpenAI API

语音生成要求取决于所选后端：

- 本地 Qwen3-TTS：推荐 NVIDIA GPU 和较大显存；CPU 可跑但速度很慢。
- Edge/DashScope/Volcengine：依赖网络和对应服务配置。
- LoRA 训练：需要更高显存和更长运行时间。

## 快速启动

安装依赖：

```bash
uv sync
pnpm install
```

启动后端：

```bash
uv run python app/app.py
```

后端默认监听：

```text
http://127.0.0.1:4200
```

开发前端：

```bash
pnpm frontend:dev
```

构建前端到后端静态目录：

```bash
pnpm frontend:build
```

启动桌面端：

```bash
pnpm desktop
```

如果已有后端在运行，可以让桌面端直接挂接：

```bash
pnpm desktop:attach
```

## 基本工作流

1. 在设置页配置 LLM。
   - 本地 LM Studio 通常是 `http://localhost:1234/v1`
   - Ollama OpenAI-compatible 入口通常是 `http://localhost:11434/v1`
   - 本地服务的 API Key 可以填 `local`

2. 创建或选择一本书。

3. 上传 `.txt`、`.md` 或 `.epub`。
   - 后端会拆分章节并生成章节清单。
   - 可以在章节页继续调整标题和内容。

4. 生成标注脚本。
   - LLM 输出 tagged script。
   - 后端解析为结构化脚本和 chunks。
   - 生成期间会写入状态、日志、章节记忆和脚本问题报告。

5. 审校脚本。
   - 可选步骤。
   - 用于修正说话人归属、旁白/对白混杂、过度拆分等问题。

6. 配置声音。
   - 为旁白和每个角色选择 TTS 类型、声音、参考音频、风格或 LoRA。
   - 当前运行时按“精确说话人名称”匹配声音配置。

7. 生成音频。
   - 可以生成全书缺失片段，也可以只处理当前章节。
   - 变更文本、说话人或声音配置后，可重新生成对应片段。

8. 合并与导出。
   - 合并章节 MP3
   - 合并全书 MP3
   - 导出 M4B
   - 导出 Audacity 多轨 ZIP

## 常用命令

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

## Docker

NVIDIA GPU 环境可尝试 Docker 部署：

```bash
docker compose up --build
```

服务启动后访问：

```text
http://127.0.0.1:4200
```

Docker Compose 会挂载 `data/` 下的运行数据，并使用 Docker volume 缓存 Hugging Face 模型。

## 数据目录

Web/后端默认把运行数据放在仓库根目录。常见目录包括：

- `books/`：书籍、章节、脚本、人物池、音频和导出结果
- `voicelines/`：语音片段
- `designed_voices/`：声音设计结果
- `clone_voices/`：克隆声音参考音频
- `lora_models/`：训练后的 LoRA
- `lora_datasets/`：LoRA 数据集
- `dataset_builder/`：数据集构建器工作目录
- `cache/`：缓存
- `logs/`：日志

桌面打包模式会把运行数据放到系统应用数据目录：

- macOS：`~/Library/Application Support/Voc Studio/data`
- Windows：`%APPDATA%/Voc Studio/data`
- Linux：`~/.config/Voc Studio/data`

也可以通过环境变量指定数据目录：

```bash
VOC_STUDIO_DATA_DIR=/path/to/data uv run python app/app.py
```

## 运行配置

后端入口支持这些环境变量：

```bash
VOC_STUDIO_HOST=127.0.0.1
VOC_STUDIO_PORT=4200
VOC_STUDIO_RELOAD=1
VOC_STUDIO_DATA_DIR=/path/to/data
VOC_STUDIO_BUILTIN_LORA_HF_REPO=zxcvbnmzsedr/voc-studio
```

桌面端挂接已有后端：

```bash
VOC_STUDIO_BACKEND_URL=http://127.0.0.1:4200 pnpm desktop
```

中国大陆网络环境如果 Hugging Face 下载慢，可以设置：

```bash
HF_ENDPOINT=https://hf-mirror.com
```

## API 与状态流

常用接口：

- `GET /api/config`
- `POST /api/config`
- `GET /api/books`
- `POST /api/upload`
- `GET /api/chapters`
- `POST /api/generate_script`
- `POST /api/review_script`
- `GET /api/characters`
- `GET /api/voices`
- `GET /api/chunks`
- `POST /api/generate_batch`
- `POST /api/merge`
- `POST /api/merge_m4b`
- `GET /api/status/{task_name}`
- `GET /api/events/{task_name}`

长任务会写入 `process_state`，前端通过 `/api/status/{task_name}` 轮询状态，也可以通过 `/api/events/{task_name}` 读取结构化事件流。

## 当前限制

- LLM 不内置，需要自己启动或配置外部服务。
- 首次本地 TTS 会下载模型，耗时取决于网络和模型大小。
- 本地 Qwen3-TTS 对显存比较敏感，低显存机器需要降低并发和 batch 大小。
- 桌面端目前是本地工作台，不是完整的商业分发包；签名、公证和自动更新还需要补齐。
- 作为本机工具默认没有账号体系；不要直接暴露到公网。
- 内置 LoRA 远端仓库默认值已改为 `zxcvbnmzsedr/voc-studio`，如果模型资产还在别的 Hugging Face 仓库，需要用 `VOC_STUDIO_BUILTIN_LORA_HF_REPO` 指向真实仓库。

## 故障排查

- 生成脚本失败：先确认 LLM base URL、API Key 和模型名称是否正确。
- JSON 或脚本格式异常：换一个更稳的非 thinking 模型，或在设置里禁用 `<think>` 等输出。
- 音频生成失败：检查声音配置是否完整，尤其是克隆声音的参考音频和文本。
- Edge TTS 没有音频：确认文本语言和所选 Edge voice 的语言匹配。
- MP3 文件异常：检查 FFmpeg 是否支持 MP3 编码。
- 桌面端打不开：先用 `uv run python app/app.py` 单独启动后端，再用 `pnpm desktop:attach` 挂接排查。

## 开发注意

- `frontend` 构建产物输出到 `app/static`。
- `app/static/legacy.html` 是旧单页前端，仍保留作兼容参考。
- `books/`、`voicelines/`、`cache/`、`logs/`、模型和训练产物默认不提交。
- 修改脚本生成、人物池、声音配置或音频路径后，优先跑 `uv run python app/test_api.py`。
- 修改前端后，优先跑 `pnpm frontend:lint` 和 `pnpm frontend:build`。
- 修改桌面壳后，优先跑 `pnpm desktop:check`。
