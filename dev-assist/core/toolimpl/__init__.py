"""
core.toolimpl — implementations for the ported OpenCode-style tools.

Each module owns one concern and stays free of the model-calling loop:
  - store:      lightweight per-workdir session state (todos, task_id
                resume buffers, the question-handler hook).
  - editors:    the fuzzy edit replacer pipeline behind `edit_file`.
  - patch:      the `*** Begin Patch ... *** End Patch` parser/applier.
  - html2md:    stdlib-only HTML -> text/markdown conversion for web_fetch.
  - subagent:   the nested `task` tool loop (foreground sub-agents).
  - skills:     the SKILL.md loader behind the `skill` tool.

A verbatim port of the file-edit and apply_patch logic is adapted from the
MIT-licensed `opencode` project (https://github.com/sst/opencode,
Copyright (c) 2025 opencode) with the notice preserved in editors.py /
patch.py. dev-assist's core/tools.py is the public registry.
"""
