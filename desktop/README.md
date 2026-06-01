# Voc Studio Desktop

Electron shell for the local Voc Studio/FastAPI application.

## Development

From the repository root:

```bash
pnpm install
pnpm desktop
```

The desktop shell starts the FastAPI backend on `127.0.0.1`, finds an available
port starting at `4200`, waits for `/api/config`, then opens the app window.
If `4200` is already in use, it automatically chooses the next free local port.
Only one Voc Studio desktop instance is allowed; opening the app again focuses
the existing window instead of starting another backend.

To attach the desktop shell to a backend that is already running, pass a local
backend URL. In this mode Electron does not start or stop the backend process:

```bash
pnpm desktop:attach
pnpm --filter voc-studio-desktop run dev -- --backend-url=http://127.0.0.1:4200
```

Runtime data is stored outside the app bundle:

- macOS: `~/Library/Application Support/Voc Studio/data`
- Windows: `%APPDATA%/Voc Studio/data`
- Linux: `~/.config/Voc Studio/data`

## Packaging

Build the web UI first so `app/static` is current:

```bash
pnpm frontend:build
pnpm --filter voc-studio-desktop run dist
```

After a directory package build, verify that runtime data was not bundled:

```bash
pnpm --filter voc-studio-desktop run check:package
```

For a non-destructive frontend build check that does not overwrite
`app/static`, use:

```bash
pnpm --filter frontend exec tsc -b
pnpm --filter frontend exec vite build -- --outDir ../.tmp/frontend-static --emptyOutDir true
```

The current package is a lightweight client. It can start the backend from the
repository or bundled app resources, but Python/Torch/FFmpeg/model availability
is still handled by the runtime capability checks.

## Runtime Capabilities

Voc Studio exposes optional capabilities through:

- `GET /api/desktop`
- `GET /api/modules`
- `GET /api/modules/install/status`
- `POST /api/modules/:id/install`
- `POST /api/modules/install/cancel`

The first automatic installer target is Hugging Face model snapshots for local
Qwen3-TTS variants. FFmpeg, cloud API keys, Python packages, and GPU readiness
are detected and reported, but they are not silently installed.

The desktop menu includes shortcuts for:

- opening the app data directory
- opening the backend log directory
- opening the model/cache directory
- opening the current local backend URL in the system browser
- copying the exact backend launch command

The renderer also exposes a desktop runtime panel through the preload bridge.
It reports the Electron version, platform, backend PID, active local URL, and
runtime directories. It also checks local commands such as `uv`, Python, and
FFmpeg so missing runtime dependencies are visible without opening a terminal.
Navigation is restricted to the active local backend; any external links are
handed to the system browser.
The same panel can copy a JSON diagnostics report for troubleshooting local
model cache, backend startup, and packaging issues.
