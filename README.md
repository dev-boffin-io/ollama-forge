# ollama-forge

> **Forge Suite** · Local AI Toolkit for Linux

A privacy-first, offline-first collection of tools for running, managing, and
chatting with [Ollama](https://ollama.com) local AI models — from the terminal
or a full PyQt5 GUI. Part of the [dev-boffin-io](https://github.com/dev-boffin-io)
Forge Suite.

---

## What's inside

| Component | Binary | Description |
|-----------|--------|-------------|
| **ollama-main** | `ollama-main` | CLI manager — install, upgrade, update-check, and uninstall Ollama |
| **Ollama GUI** | `Ollama-ai-gui` + `Ollama-ai-manager` | PyQt5 desktop chat with RAG, conversation history, and Ollama service manager |
| **dev-assist** | `da` | AI-powered DevOps assistant with RAG, shell execution, web UI (Chainlit), and multi-provider AI support |

All three components are built into standalone **PyInstaller binaries** — no
Python or virtual environment required at runtime.

---

## Requirements

- Linux (Debian/Ubuntu recommended; ARM64 and proot-Termux supported)
- Python 3.10+ (build time only)
- Ollama installed and running for GUI and dev-assist
- `sudo` access for the system dependency installer

---

## Quick Start

### 1 · Install system dependencies (GUI only, ARM64/Debian)

```bash
sudo bash builder/install-deps-gui.sh
```

### 2 · Build all binaries

```bash
make all
```

Or build individual components:

```bash
make build-main          # → ./ollama-main
make build-gui           # → ./Ollama-ai-gui + ./Ollama-ai-manager
make build-dev-assist    # → ./da
```

---

## Components

### ollama-main · Ollama CLI Manager

Manages the Ollama binary lifecycle on Linux (install/upgrade/uninstall) via
the [official install script](https://ollama.com/install.sh) with automatic
version detection against the GitHub Releases API.

```bash
./ollama-main install     # Install Ollama
./ollama-main upgrade     # Upgrade to latest release
./ollama-main update      # Check for available updates
./ollama-main uninstall   # Remove Ollama from the system
```

**Dependencies:** `requests`, `packaging`

---

### Ollama GUI · PyQt5 Desktop Chat

A full-featured desktop chat application built with PyQt5.

**Features:**
- Multi-model chat — switch models at any time
- Conversation history stored in local SQLite
- RAG (Retrieval-Augmented Generation) — index PDF, DOCX, and text files using
  FAISS and `sentence-transformers` (no LangChain)
- Crew mode — multi-agent conversation templates
- Dark theme, 1800 × 900 default window size, large fonts
- `Ollama-ai-manager` — separate window for managing the Ollama service,
  model pulls/deletes, authentication, and binary updates

```bash
./Ollama-ai-gui        # Open chat window
./Ollama-ai-manager    # Open service manager window
```

**Dependencies:** PyQt5, requests, sentence-transformers, faiss-cpu, pypdf, python-docx

---

### dev-assist · AI DevOps Assistant

A personal AI assistant for developers and sysadmins, available as a
terminal REPL or a Chainlit web UI.

**Features:**
- Terminal REPL with `rich` formatting and `prompt-toolkit` readline history
- Chainlit web UI (`--web` flag) with Bengali locale support
- Shell execution — prefix commands with `!` to run them directly
- RAG engine — FAISS-backed code indexer for your project tree
- Multi-provider AI — Ollama (default), Groq, OpenAI (set via `.env`)
- Pydantic-validated config with environment variable overrides
- Jinja2 prompt templates for specialised tasks (git, code audit, tunnel, etc.)
- pytest test suite — RAG, router, and shell modules covered

```bash
./da                    # Terminal REPL (CLI mode)
./da --web              # Web UI on http://localhost:8000
./da --web --port 8080  # Custom port

# Shell commands inside REPL
da> !ls -la
da> !git log --oneline -10
```

**Environment variables:**

```bash
export DEV_ASSIST_API_KEY="gsk_..."   # Groq / OpenAI key (optional)
export DEV_ASSIST_DATA_DIR="..."      # Custom data directory
export DEV_ASSIST_CONFIG_DIR="..."    # Custom config directory
```

Copy `.env.example` to `dev-assist/.env` and fill in your values.

**Dependencies:** ollama, pydantic, bcrypt, rich, prompt-toolkit, jinja2, chainlit

---

## Development

### Install dependencies for local development

```bash
# Core CLI
pip install -e ".[dev]"

# With GUI extras
pip install -e ".[gui,dev]"

# With dev-assist extras
pip install -e ".[dev-assist,dev]"

# Everything
pip install -e ".[all,dev]"
```

### Run tests

```bash
make test
```

### Lint and format

```bash
make lint       # ruff + black --check
make format     # black + isort (auto-fix)
```

### Clean build artefacts

```bash
make clean
```

---

## Project layout

```
ollama-forge/
├── ollama-main.py          # CLI entry point (Ollama lifecycle manager)
├── ollama-forge.png        # Project icon
├── pyproject.toml
├── Makefile
├── LICENSE
├── README.md
│
├── gui/                    # PyQt5 GUI application
│   ├── main.py             # Main window
│   ├── ollama_manager.py   # Service/model manager window
│   ├── ollama_client.py    # Ollama REST API client
│   ├── database.py         # SQLite conversation store
│   ├── rag_engine.py       # FAISS RAG engine
│   ├── workers.py          # QThread workers
│   ├── crew_dialogs.py     # Crew config dialogs
│   ├── _syspath_patch.py   # PyInstaller frozen binary path fix
│   └── requirements.txt
│
├── dev-assist/             # AI DevOps assistant
│   ├── main.py             # CLI entry point
│   ├── web_chat.py         # Chainlit web UI
│   ├── core/               # Config, AI engine, RAG, router, session, shell
│   ├── modules/            # Shell exec, git, file tools, indexer, tunnel
│   ├── plugins/            # Makefile and Telegram integrations
│   ├── tests/              # pytest suite
│   ├── config/             # settings.json
│   ├── .chainlit/          # Chainlit translations + config
│   ├── .env.example
│   └── requirements.txt
│
└── builder/                # Build scripts (PyInstaller)
    ├── build-main.sh
    ├── build-gui-bin.sh
    ├── build-dev-assist.sh
    └── install-deps-gui.sh
```

---

## ARM64 / proot-Termux notes

All three components are tested on ARM64 Debian and proot-Termux environments.
Run `sudo bash builder/install-deps-gui.sh` before building the GUI on these
platforms to install required system Qt and Python binding packages.

---

## License

MIT — see [LICENSE](LICENSE).

Upstream projects retain their own licenses. See the Third-Party
Acknowledgements section in LICENSE for the full list.

---

## Forge Suite

`ollama-forge` is part of the [dev-boffin-io](https://github.com/dev-boffin-io)
Forge Suite — a collection of privacy-first, offline-first desktop and CLI tools
for Linux developers.
