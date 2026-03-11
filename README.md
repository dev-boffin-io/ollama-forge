# 🦙 Ollama Forge

A local AI desktop GUI for Linux — chat, RAG knowledge base, multi-agent crews,
and model management, all powered by [Ollama](https://ollama.com).

No cloud. No telemetry. Runs entirely on your machine.

![Ollama Forge](ollama-forge.png)

---

## Features

- **Chat** — streaming responses with conversation history
- **Vision** — attach images for vision-capable models (LLaVA, Moondream, etc.)
- **RAG** — index local documents (PDF, DOCX, TXT, MD, HTML) and query them
- **Crews** — chain multiple agents with different roles and models
- **Model Manager** — pull, create, push, remove models; sign in to ollama.com
- **Server control** — start/stop Ollama serve from the GUI
- **Themes** — dark and light mode

---

## Project Layout

```
ollama-forge/
├── gui/
│   ├── main.py              — main chat window
│   ├── ollama_manager.py    — standalone model manager window
│   ├── ollama_client.py     — Ollama HTTP API client
│   ├── database.py          — SQLite (conversations, crews)
│   ├── rag_engine.py        — FAISS RAG (no LangChain)
│   ├── workers.py           — QThread workers
│   ├── crew_dialogs.py      — crew config UI + templates
│   └── requirements.txt
├── builder/
│   ├── build-gui-bin.sh     — build GUI + Manager binaries
│   ├── build-main.sh        — build CLI binary
│   └── install-deps-gui.sh  — install system deps (run as root)
├── ollama-main.py           — CLI tool (install/upgrade/check Ollama)
├── install.sh               — install desktop entry + CLI symlink
├── Makefile
├── pyproject.toml
└── LICENSE
```

---

## Requirements

### Runtime
- Linux (x86\_64 or ARM64/aarch64)
- [Ollama](https://ollama.com) installed and at least one model pulled
- For ARM64: system `python3-pyqt5`, `libgl1`, `libxcb-xinerama0`

### Python (x86\_64, installed automatically)
| Package | Purpose |
|---|---|
| PyQt5 ≥ 5.15 | GUI framework |
| requests ≥ 2.31 | Ollama API calls |
| numpy ≥ 1.24 | Vector operations |
| faiss-cpu ≥ 1.7 | RAG vector index |
| sentence-transformers ≥ 2.6 | Document embeddings |
| pypdf ≥ 4.0 | PDF parsing |
| python-docx ≥ 1.1 | DOCX parsing |
| packaging ≥ 23.0 | Version comparison |

---

## Quick Start

### Option 1 — Pre-built binaries

Download the latest release, then:

```bash
./install.sh        # user install  (~/.local)
sudo ./install.sh   # system install (/usr/local)
```

### Option 2 — Build from source

```bash
# ARM64 only — install system deps once
sudo ./builder/install-deps-gui.sh

# Build all binaries
make build

# Install
make install
# or: sudo make install
```

### Option 3 — Run as Python script (dev mode)

```bash
cd gui
pip install -r requirements.txt
python main.py
```

---

## Makefile Commands

| Command | Action |
|---|---|
| `make build` | Build all binaries |
| `make install` | Install (auto-builds if binaries missing) |
| `make uninstall` | Remove desktop entry and CLI symlink |
| `make rebuild` | Clean then build |
| `make clean` | Remove built binaries |

---

## install.sh Commands

```bash
./install.sh build    # build + install
./install.sh          # install only (binaries must exist)
./install.sh remove   # uninstall
sudo ./install.sh     # system-wide install
```

---

## CLI Tool — `ollama-main`

```bash
ollama-main install    # install Ollama
ollama-main upgrade    # upgrade to latest
ollama-main update     # check for updates
ollama-main uninstall  # remove Ollama
```

---

## RAG Knowledge Base

1. Click **Files** or **Folder** to index documents
2. Choose an embedding model from the dropdown
3. Chat — the AI automatically retrieves relevant context

Supported formats: `.pdf` `.docx` `.txt` `.md` `.html`

Recommended embedding models (pull via Model Manager):
| Model | Size | Notes |
|---|---|---|
| `nomic-embed-text` | ~270 MB | Fast, good quality |
| `mxbai-embed-large` | ~670 MB | Higher quality |
| `all-MiniLM-L6-v2` | ~80 MB | sentence-transformers (offline) |

---

## Crews

Crews chain multiple AI agents sequentially — each agent gets the previous
agent's output as context.

1. Click **New** or **Template** in the Crews section
2. Add agents with roles, models, and system prompts
3. Enable **Crew Mode: ON** before sending

Built-in templates: Coding Crew, Research Crew, Writing Crew.

---

## Model Manager

Launched via the **Model Manager** button. Features:
- Start / stop Ollama server
- Pull models by name (autocomplete with 60+ popular models)
- Create models from a Modelfile
- Push models to ollama.com (requires sign in)
- Remove local models

---

## ARM64 / PRoot Notes

On ARM64 (Termux proot-Debian), install system deps first:

```bash
sudo ./builder/install-deps-gui.sh
```

The build script automatically uses `--system-site-packages` so PyQt5
from the system package is visible inside the build venv.

---

## Keyboard Shortcuts

| Shortcut | Action |
|---|---|
| `Ctrl+Enter` | Send message |

---

## License

MIT — see [LICENSE](LICENSE).

Note: PyQt5 is licensed under GPL v3. If you distribute this software,
the GPL terms apply. For commercial/proprietary distribution, obtain a
PyQt5 commercial license from Riverbank Computing.
