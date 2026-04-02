## 🤖 dev-assist — AI DevOps CLI

A personal AI DevOps assistant bundled as a self-contained binary.
Runs entirely local — no cloud, no telemetry.

```
ollama-forge/
└── dev-assist/         ← subproject source
    ├── core/           — AI engine, RAG, config, session
    ├── modules/        — git, shell, indexer, tunnel, file tools
    ├── plugins/        — optional Telegram, Makefile plugins
    ├── web_chat.py     — Chainlit web UI
    ├── main.py         — CLI entry point
    ├── build.sh        — PyInstaller build script
    └── setup.sh        — venv + deps setup
```

### Features

- **Chat** — streaming AI responses via Ollama (local) or any OpenAI-compatible API
- **RAG** — index any local project folder; ask questions about your codebase
- **Code audit** — AI review of `git diff` output
- **Shell** — run shell commands with `!cmd` or `!run cmd` prefix
- **Web UI** — full browser chat with auth, per-user history, file upload (Chainlit)
- **Plugins** — drop `.py` files into `plugins/` to extend

### Input Modes (CLI)

| Input | Action |
|-------|--------|
| `<message>` | Chat with AI / built-in commands |
| `!ls -la` | Run shell command |
| `!run find . -name *.py` | Force shell run |

### CLI Commands

| Command | Action |
|---------|--------|
| `index /path` | Index a project folder for RAG |
| `index status` | Show indexed files |
| `audit` | AI code review (git diff) |
| `model list` | List available AI models |
| `model set <name>` | Switch active model |
| `ollama on / off` | Start / stop Ollama server |
| `history` | View session history |
| `help` | Full command reference |

### Build

```bash
# Build binary (from project root)
make da-build

# Or directly
bash builder/build-dev-assist.sh

# Clean build (keeps .venv for faster rebuilds)
make da-clean

# Full rebuild from scratch
make da-rebuild
```

### Run

```bash
# CLI mode
./dev-assist

# Web UI (browser)
./dev-assist --web
./dev-assist --web --port 8080
```

### Install

```bash
# Install CLI to ~/.local/bin  (or /usr/local/bin if root)
make da-install
# or
./install.sh da-install

# Uninstall
./install.sh da-remove
```

### Web UI Auth & SMTP

The web UI requires registration. Email verification is optional — if `SMTP_*` vars
are not set, accounts are auto-verified (development mode).

Copy `dev-assist/.env.example` to `dev-assist/.env` and configure:

```bash
# AI API key (if using external API instead of Ollama)
export DEV_ASSIST_API_KEY="your-key"

# SMTP (Gmail example — use App Password)
export SMTP_HOST="smtp.gmail.com"
export SMTP_PORT="587"
export SMTP_USER="you@gmail.com"
export SMTP_PASS="xxxx xxxx xxxx xxxx"
```

### Requirements

| Package | Purpose |
|---------|---------|
| `ollama` | Local AI inference |
| `chainlit >= 2.0` | Web UI framework |
| `pydantic >= 2.0` | Config validation |
| `bcrypt >= 4.0` | Password hashing |
| `rich >= 13.0` | Terminal output |
| `jinja2 >= 3.1` | Prompt templates |

All dependencies are installed automatically by `build.sh` into an isolated `.venv`.
