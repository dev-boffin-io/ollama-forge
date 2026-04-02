# 🦙 Ollama Forge

A local AI toolkit for Linux — desktop GUI, multi-agent crews, RAG knowledge base,
model management, and an AI DevOps CLI, all powered by [Ollama](https://ollama.com).

No cloud. No telemetry. Runs entirely on your machine.

[![Ollama Forge](https://github.com/dev-boffin-io/ollama-forge/raw/main/ollama-forge.png)](https://github.com/dev-boffin-io/ollama-forge/blob/main/ollama-forge.png)

---

## Components

| Component | Description |
|-----------|-------------|
| **Ollama AI GUI** | Desktop chat + RAG + crews + model manager (PyQt5) |
| **dev-assist** | Personal AI DevOps CLI with web UI (Chainlit) |
| **ollama-main** | CLI tool to install / upgrade / manage Ollama itself |

---

## Project Layout

```
ollama-forge/
├── gui/
│   ├── main.py                 — main chat window
│   ├── ollama_manager.py       — standalone model manager window
│   ├── ollama_client.py        — Ollama HTTP API client
│   ├── database.py             — SQLite (conversations, crews)
│   ├── rag_engine.py           — FAISS RAG (no LangChain)
│   ├── workers.py              — QThread workers
│   ├── crew_dialogs.py         — crew config UI + templates
│   └── requirements.txt
├── dev-assist/                 — AI DevOps CLI subproject
│   ├── core/                   — AI engine, RAG, config, session
│   ├── modules/                — git, shell, indexer, tunnel, file tools
│   ├── plugins/                — optional Telegram, Makefile plugins
│   ├── web_chat.py             — Chainlit web UI
│   ├── main.py                 — CLI entry point
│   ├── build.sh                — PyInstaller build script
│   └── setup.sh                — venv + deps setup
├── builder/
│   ├── build-gui-bin.sh        — build GUI + Manager binaries
│   ├── build-main.sh           — build ollama-main CLI binary
│   ├── build-dev-assist.sh     — build dev-assist binary
│   └── install-deps-gui.sh     — install system deps (ARM64, run as root)
├── ollama-main.py              — CLI tool (install/upgrade/check Ollama)
├── dev-assist.mk               — Makefile include for dev-assist targets
├── dev-assist.md               — dev-assist documentation section
├── install.sh                  — unified installer
├── Makefile
├── pyproject.toml
└── LICENSE
```

---

## GUI — Ollama AI

### Features

- **Chat** — streaming responses with conversation history
- **Vision** — attach images for vision-capable models (LLaVA, Moondream, etc.)
- **RAG** — index local documents (PDF, DOCX, TXT, MD, HTML) and query them
- **Crews** — chain multiple agents with different roles and models
- **Model Manager** — pull, create, push, remove models; sign in to ollama.com
- **Server control** — start/stop Ollama serve from the GUI
- **Themes** — dark and light mode

### Requirements

**Runtime**

- Linux (x86\_64 or ARM64/aarch64)
- [Ollama](https://ollama.com) installed and at least one model pulled
- ARM64: system `python3-pyqt5`, `libgl1`, `libxcb-xinerama0`

**Python packages** (installed automatically on x86\_64)

| Package | Purpose |
|---------|---------|
| `PyQt5 >= 5.15` | GUI framework |
| `requests >= 2.31` | Ollama API calls |
| `numpy >= 1.24` | Vector operations |
| `faiss-cpu >= 1.7` | RAG vector index |
| `sentence-transformers >= 2.6` | Document embeddings |
| `pypdf >= 4.0` | PDF parsing |
| `python-docx >= 1.1` | DOCX parsing |
| `packaging >= 23.0` | Version comparison |

### Quick Start

**Option 1 — Pre-built binaries**

```bash
./install.sh          # user install  (~/.local)
sudo ./install.sh     # system install (/usr/local)
```

**Option 2 — Build from source**

```bash
# ARM64 only — install system deps once
sudo ./builder/install-deps-gui.sh

# Build GUI + Manager + CLI
make build

# Install
make install
# or: sudo make install
```

**Option 3 — Run as Python script (dev mode)**

```bash
cd gui
pip install -r requirements.txt
python main.py
```

### RAG Knowledge Base

1. Click **Files** or **Folder** to index documents
2. Choose an embedding model from the dropdown
3. Chat — the AI automatically retrieves relevant context

Supported formats: `.pdf` `.docx` `.txt` `.md` `.html`

Recommended embedding models (pull via Model Manager):

| Model | Size | Notes |
|-------|------|-------|
| `nomic-embed-text` | ~270 MB | Fast, good quality |
| `mxbai-embed-large` | ~670 MB | Higher quality |
| `all-MiniLM-L6-v2` | ~80 MB | sentence-transformers (offline) |

### Crews

Crews chain multiple AI agents sequentially — each agent gets the previous
agent's output as context.

1. Click **New** or **Template** in the Crews section
2. Add agents with roles, models, and system prompts
3. Enable **Crew Mode: ON** before sending

Built-in templates: Coding Crew, Research Crew, Writing Crew.

### Model Manager

Launched via the **Model Manager** button:

- Start / stop Ollama server
- Pull models by name (autocomplete with 60+ popular models)
- Create models from a Modelfile
- Push models to ollama.com (requires sign in)
- Remove local models

### Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| `Ctrl+Enter` | Send message |

---

## dev-assist — AI DevOps CLI

A personal AI DevOps assistant — terminal REPL and browser web UI.
Runs entirely local via Ollama, or with any OpenAI-compatible API.

### Features

- **Chat** — streaming AI responses, per-session conversation history
- **RAG** — index any local project folder and ask questions about your code
- **Code audit** — AI review of `git diff` output
- **Shell** — run commands with `!cmd` or `!run cmd` prefix
- **Web UI** — full browser chat with user auth, file upload, per-user history
- **Plugins** — drop `.py` files into `plugins/` to extend

### CLI Input Modes

| Input | Action |
|-------|--------|
| `<message>` | Chat with AI / built-in commands |
| `!<cmd>` | Run shell command (e.g. `!ls -la`) |
| `!run <cmd>` | Force shell run (e.g. `!run find . -name *.py`) |

### CLI Commands

| Command | Action |
|---------|--------|
| `index /path` | Index a project folder for RAG |
| `index status` | Show indexed files and chunk count |
| `index clear` | Wipe the index |
| `audit` | AI code review (git diff) |
| `model list` | List available AI models |
| `model set <name>` | Switch active model |
| `model engine ollama\|api` | Switch AI engine |
| `ollama on / off` | Start / stop Ollama server |
| `history` | View session history |
| `history clear` | Clear session history |
| `help` | Full command reference |

### Build

```bash
# Build binary (from project root)
make da-build

# Clean build artifacts (keeps .venv for faster rebuilds)
make da-clean

# Full rebuild from scratch (wipes .venv too)
make da-rebuild
```

The binary is placed at `./dev-assist` in the project root.

### Run

```bash
./dev-assist            # terminal CLI
./dev-assist --web      # browser web UI  (default: http://0.0.0.0:8000)
./dev-assist --web --port 8080
```

### Install

```bash
# Install CLI symlink to ~/.local/bin  (or /usr/local/bin if root)
make da-install
# or
./install.sh da-install

# Uninstall
./install.sh da-remove
```

### Web UI — SMTP / Email Verification

Copy `dev-assist/.env.example` to `dev-assist/.env`:

```bash
# AI API key (if using external API instead of Ollama)
export DEV_ASSIST_API_KEY="your-key"

# SMTP — Gmail example (use App Password, not your real password)
export SMTP_HOST="smtp.gmail.com"
export SMTP_PORT="587"
export SMTP_USER="you@gmail.com"
export SMTP_PASS="xxxx xxxx xxxx xxxx"
```

If SMTP is not configured, email verification is skipped automatically (development mode).

### dev-assist Dependencies

| Package | Purpose |
|---------|---------|
| `ollama` | Local AI inference |
| `chainlit >= 2.0` | Web UI framework |
| `pydantic >= 2.0` | Config validation |
| `bcrypt >= 4.0` | Password hashing (web UI) |
| `rich >= 13.0` | Terminal output |
| `jinja2 >= 3.1` | Prompt templates |

All dependencies are installed automatically into an isolated `.venv` by `build.sh`.

---

## ollama-main — Ollama CLI Manager

```bash
ollama-main install      # install Ollama
ollama-main upgrade      # upgrade to latest version
ollama-main update       # check for updates
ollama-main uninstall    # remove Ollama
```

---

## Makefile Reference

### GUI targets

| Command | Action |
|---------|--------|
| `make build` | Build GUI + Manager + ollama-main |
| `make install` | Install GUI (builds first if binaries missing) |
| `make uninstall` | Remove desktop entry and symlinks |
| `make rebuild` | Clean then build GUI |
| `make clean` | Remove GUI binaries |

### dev-assist targets

| Command | Action |
|---------|--------|
| `make da-build` | Build dev-assist binary |
| `make da-install` | Install dev-assist CLI symlink |
| `make da-uninstall` | Remove dev-assist CLI symlink |
| `make da-rebuild` | Clean + build dev-assist |
| `make da-clean` | Remove binary (keep `.venv`) |
| `make da-clean-all` | Remove binary + `.venv` |
| `make da-run` | Launch dev-assist CLI |
| `make da-web` | Launch dev-assist web UI |

### Combined targets

| Command | Action |
|---------|--------|
| `make build-all` | Build GUI + dev-assist |
| `make install-all` | Install GUI + dev-assist |

---

## install.sh Reference

```bash
./install.sh              # install all (binaries must exist)
./install.sh build        # build all + install
./install.sh build-gui    # build GUI only + install
./install.sh build-da     # build dev-assist + install
./install.sh da-install   # install dev-assist CLI symlink only
./install.sh da-remove    # remove dev-assist CLI symlink
./install.sh remove       # uninstall everything
sudo ./install.sh         # system-wide install (/usr/local)
```

---

## ARM64 / PRoot Notes

On ARM64 (Termux proot-Debian), install system deps before building the GUI:

```bash
sudo ./builder/install-deps-gui.sh
```

The GUI build script automatically passes `--system-site-packages` so that
the system PyQt5 package is visible inside the build venv.

`dev-assist` has no native GUI dependencies and builds cleanly on ARM64
without any additional system packages.

---

## License

MIT — see [LICENSE](https://github.com/dev-boffin-io/ollama-forge/blob/main/LICENSE).

> **PyQt5 note:** PyQt5 is licensed under GPL v3. If you distribute this software,
> the GPL terms apply to the GUI component. For commercial or proprietary distribution,
> obtain a PyQt5 commercial license from Riverbank Computing.
> `dev-assist` uses only MIT/Apache-licensed dependencies and is not affected by this.
