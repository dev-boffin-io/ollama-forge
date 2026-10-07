# dev-assist → GUI feature parity

Every dev-assist capability, whether the GUI has it today, how to wire it (real
function), and effort (S = <60 lines, M = moderate, L = large). "In GUI today?"
claims are grep-verified (search strings listed inline); `step-x` prefixes match
the wire-up plan in the session TASK 3 (3.1–3.5).

Legend: ✅ present · 🟡 present-but-stronger-in-CLI · 🚫 missing · Σ = CLI-only
decision (never wired) with the reason.

## Agents, approvals, undo

| Capability | dev-assist symbol | In GUI today? | How to wire (real function) | Effort | CLI-only (why) |
|---|---|---|---|---|---|
| Agent run (plan → tools → synthesize) | `core.agent.run_agent(task, *, workdir, approver, on_event, max_steps, agent, extra_context)` | ✅ | Already: `gui/agent_bridge.run_agent` → `gui/workers.AgentWorker` (from `_send`, `main.py:1426`). | — | |
| Agent registry (build/coder/reviewer/explore/general/compaction + user) | `core.agents.all_agents()`, `core.agents.resolve(name)` | 🟡 | GUI hardcodes `agent_name="build"` (`workers.py:556`). Wire picker: populate combo from `all_agents()`, pass id into `AgentWorker` → `run_agent(agent=...)`. (step 3.1) | S | |
| Deterministic multi-agent routing | `core.agents.route(task, workdir)` | 🚫 | Off; build agent is plenty. If ever: map `RouteDecision.agent` to the picker. | M | |
| Approval prompts (y/n/always) | `modules.agent_mode.make_approver(...)`, `.Approver` | ✅ | `gui/agent_bridge.make_approver` + `gui/approval_dialog.ApprovalDialog`; auto-approve checkbox. | — | |
| Diff / edit preview before apply | `modules.agent_mode._unified_diff`, `_preview_edit/_write/_apply_patch` | ✅ | Approval dialog renders unified diff (`approval_dialog.py`, plain text block) — no rich color rendering. | — | |
| Permission rules from config | `modules.agent_mode._load_permission_rules()` | ✅ | `gui/agent_bridge.load_permission_rules`. | — | |
| Undo last run (file changes) | `modules.agent_mode.undo`, `core.change_tracker` | ✅ | Undo button → `agent_bridge.undo_changes` (`main.py`); apply_patch files tracked too. | — | |
| Readonly auto-approve / full auto-approve | `core.agent.auto_approve_readonly`, `approve_everything` | ✅ | Auto-approve checkbox → `agent_bridge.make_approver` (readonly default, everything with prompt). | — | |

## Sessions & context

| Capability | dev-assist symbol | In GUI today? | How to wire (real function) | Effort | CLI-only (why) |
|---|---|---|---|---|---|
| Conversation persistence | `core/session_store.py` SQLite | ✅ | GUI's own SQLite: `gui/database.py` `conversations`/`messages` (not the dev-assist store). | — | |
| Fresh session | `modules.slash_commands._cmd_new`, `session_store.new_session` | 🟡 | "New chat" button already clears UI; doesn't touch dev-assist store. | S | |
| List / resume / delete conversations | `modules.slash_commands._cmd_sessions` / `_cmd_resume` / `_cmd_delete` | 🚫 | List sidebar conversations from `db.list_conversations()`; resume = switch `current_conv_id` + reload. (step 3.3) | M | |
| History compaction (`/compact`) | `core.agents.compact_context`, `core.session` `_trim` | 🚫 | Trim `db.get_messages()` before send; `extra_context=` slot already exists in `AgentWorker`. (step 3.3) | S | |
| `extra_context` injection | `AgentWorker(..., extra_context=)` → `run_agent` | ✅ | Already plumbed (`workers.py:494–559`); exposed via sidebar context box. | — | |
| Dev-assist session store sync | `core.session_store` | 🚫 | Drop; GUI DB is the single source. Never wire both. | — | ✅ two competing session stores would fork history |
| CLI history (arrow recall) | `core.cli_history` | 🚫 | Never; GUI has its own input box, up-arrow recall could reuse `db` but adds little. | — | ✅ terminal-only affordance |

## Chat & UI

| Capability | dev-assist symbol | In GUI today? | How to wire (real function) | Effort | CLI-only (why) |
|---|---|---|---|---|---|
| Streaming replies | `web_app._stream_provider` (SSE) | ✅ | GUI OllamaStreamThread / provider streaming in `workers.py`. | — | |
| Slash commands (`/init /review /agents /compact /themes /provider /model /new /sessions /resume /rename /delete /about /status /help`) | `modules/slash_commands.list_commands(workdir)`, `.execute`, `.run` | 🚫 | Intercept user input starting with `/`; call `slash_commands.execute(name, args, workdir)`; render output to think buffer/status bubble. (step 3.2) | M | |
| Command palette (ctrl+p) | `modules.slash_commands.palette(prefill)` | 🚫 | `input()`-based → reimplement as QCompleter popup over `list_commands()`. (step 3.2) | S | |
| Think buffer / live agent status | `core.tui_status`, `modules.agent_mode._print_route` | ✅ | `chat_renderer.think_html` + `_on_agent_event` routes non-text events. (`main.py`) | — | |
| Dual light/dark themes | `core.theme`, `modules.slash_commands._cmd_themes` | 🟡 | `chat_renderer.py` has light/dark CSS + toggle; CLI can switch via `/themes` with `input()` picker. | S | |
| Banner / rich panels | `core.banner`, `main._show_help` | 🚫 | Include banner text in /about; skip rich panels in GUI. | — | ✅ cosmetic, duplicates GUI chrome |
| Key bindings (leader chords) | `main._build_key_bindings` | 🚫 | Never port; Qt handles shortcuts. | — | ✅ terminal-only ergonomics |
| Session tag in prompt (`(build)` etc.) | `main._session_tag` | 🟡 | Reuse: agent state strip shows routed agent (`agent_state_label`, `main.py`). | — | |

## Ollama & providers

| Capability | dev-assist symbol | In GUI today? | How to wire (real function) | Effort | CLI-only (why) |
|---|---|---|---|---|---|
| Ollama start / stop / status | `core.ollama_status.start_ollama/stop_ollama/get_status` | ✅ | GUI server button + `OllamaClient` (`main.py`, `ollama_client.py`). | — | |
| Provider ID list + switching | `core.providers` / `core.ai.switch_provider` | ✅ | `gui/providers.py` + `provider_sel` combo (`main.py:428`). | — | |
| Model list + selection | `core.ai.resolve_live_models`, `set_provider_model` | ✅ | Model manager dialog + `model_box` combo (`main.py:402`). | — | |
| `/model` / `/provider` slash behaviors | `modules.slash_commands._cmd_model/_cmd_provider` | 🟡 | GUI already has real combos; slash rows of step 3.2 just call `list_commands` for `/about`-style info, not model switching. | S | |

## Knowledge & RAG

| Capability | dev-assist symbol | In GUI today? | How to wire (real function) | Effort | CLI-only (why) |
|---|---|---|---|---|---|
| Folder indexing | `modules.indexer.run`/`index_folder` | 🟡 | GUI has own FAISS engine (`gui/rag_engine.py`) with its own chunking; doesn't reuse dev-assist vector_store. Keep separate. | — | ✅ different indexes/stores; merging = large, low value |
| RAG ask with context | `core.rag_engine.ask_with_context` (CLI) / `core.vector_store.search` | 🟡 | GUI: `rag_engine._import_faiss` + `_chunk_text`; kb-toggle adds retrieved context in `_send` (`main.py`). | — | |
| Index status display | `modules.indexer._show_status`, `vector_store.get_stats` | 🟡 | Main window shows kb status; `_set_rag_ui_busy` gates input during indexing. | — | |
| repo_map (project layout in plan) | `core.repo_map.build_repo_map` | ✅ | Runs automatically inside `core.agent.run_agent` (`agent.py:506`) on every agent turn. No extra wiring. | — | |
| Attachments (zip/image/text/pdf) | `core.attachment_handler` | ✅ | GUI reimplements (`gui/attachment_handler.py`); images/zip tree injected into prompt (`main.py`). | — | |

## Tools exposed to the agent

All `core/tools.py` executors (`read_file`, `list_dir`, `glob`, `grep`,
`write_file`, `edit_file`, `bash`, `run_tests`, `web_search`, `web_fetch`,
`todowrite`, `question`, `task`, `skill`, `apply_patch`, `about`,
`lsp_diagnostics`, `mcp__*`) reach the GUI **through the agent loop** — the GUI
does not add a separate tool surface. Two follow-ups:

| Capability | In GUI today? | Wire / decision | Effort |
|---|---|---|---|
| `about` (machine-readable capability JSON) | 🚫 | `/about` row in step 3.2 → call `execute_tool("about", {}, workdir)` and render JSON to think buffer. Any `/about` with `*_error` keys is a bug. | S |
| `question` tool interactivity | 🚫 | GUI has no handler → default is auto-skip (`set_question_auto_skip`). Wire a small QInputDialog handler via `tools.set_question_handler`. | S |
| `task` (subagents) | 🚫 | Leave agent-internal; it already works headless. Exposing in GUI adds UI complexity of sub-agent read-back. | — |
| provider model/tool schemas (MCP) | 🚫 | `_ensure_dynamic_tools` already registers mcp/lsp tools for agent turns. Nothing to wire. | — |

## Repo / dev-ops modules (router intents)

| Capability | dev-assist symbol | In GUI today? | Wire / decision | Effort |
|---|---|---|---|---|
| Git push/pull/rebase/conflict guidance | `modules.git_helper.run` | 🚫 | **Decided CLI-only (TASK 3):** `run()` dispatches by keyword to fixers with 4 `input()` prompts (86/107/137/172); its only un-prompted path is the default `_git_status()` status+log view. There is **no `diff` path**, so `/git status\|diff\|log` cannot go through it. A GUI `/git` would need an `ask` hook and a diff panel (see summary). Read-only git remains available to the agent via `core.shell.run_git` (its `bash` tool). | M | ✅ read-only status/log view exists but `diff` is missing and the fixers would hang a GUI on `input()`. Note: 4 `input()` sites are drift-guarded by `test_readme_drift.py`. |
| AI code audit of diff | `modules.code_audit.run` | 🚫 | One-shot `run('')` → shows diff summary + prompt; non-interactive, easy `/audit`. (step 3.5 candidate S) | S |
| Kill port | `modules.cmd_helper.fix_port` | 🚫 | `run_cmd(text)` non-interactive; could be a `task` tool prompt. Low value. | S |
| Tunnel (cloudflared/ngrok) | `modules.tunnel_helper.run` | 🚫 | Background supervisord-style process; GUI could spawn in thread + log to think buffer. Invasive. | L |
| Bulk rename/clean files | `modules.file_tool.run` | 🚫 | `input()` at 59/74; GUI file ops belong in a future file-manager view. | L |
| Shell with cwd (live `cd`) | `modules.shell_exec` | ✅ (bash tool) | Agent `bash` tool already runs with `workdir`; a persistent GUI shell is a separate feature. | L |

## Plugins & hooks

| Capability | dev-assist symbol | In GUI today? | Wire / decision | Effort |
|---|---|---|---|---|
| Plugin discovery on keyword (`makefile`, `telegram`) | `core.router._try_plugin` | 🚫 | Both call `input()` (makefile:40, telegram:41) and need a terminal for posting messages. Keep CLI-only. | — | ✅ interactive + needs terminal side-effects |
| Git pre-push audit hook | `hooks/pre-push` | 🚫 | Hooks are repo-level, not UI. Keep CLI/git-side. | — | ✅ operates outside GUI session |

## Interactive surface (CLI-only by nature)

`_pick_theme/_pick_provider/_pick_model` (`input()`), `palette` picker,
`Approver._interactive_ask`, plain/readline prompt loops, key bindings — all
terminal affordances superseded by Qt widgets (combo boxes, dialogs, keyboard
shortcuts). GUI equivalents already exist or are in steps 3.1–3.5.

## Wire-up plan summary (from TASK 3)

- **3.1** agent picker: combo ← `core.agents.all_agents()`; pass `agent=` to `AgentWorker`; show routed id in state strip. **S**
- **3.2** slash commands: prefix-dispatch to `modules.slash_commands.execute(...)`; `/about` via `about` tool; completions popup ← `list_commands(workdir)`. **M**
- **3.3** sessions: sidebar list/resume/new from `db.list_conversations`; `/compact` trims history + feeds `extra_context`. **M**
- **3.4** AGENTS.md indicator: `core.instructions.discover(workdir)` → "AGENTS.md ✓/✗" in state strip (context already injected by `run_agent`, `agent.py:523`). **S**
- **3.5** S/M leftovers in priority order: `/audit` (`code_audit.run`) → `question` tool → provider/model `/about` info; tunnel + file-tool + persistent shell deferred to L. **git help**: decided **CLI-only** (TASK 3) — no non-interactive diff API in `git_helper`.