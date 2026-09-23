"""
The `*** Begin Patch ... *** End Patch` parser and applier behind `apply_patch`.

A Python port of opencode's patch module ([index.ts](https://github.com/sst/opencode),
Copyright (c) 2025 opencode, MIT License). The patch language is a stripped-down,
file-oriented diff format:

    *** Begin Patch
    *** Add File: <path>
    +line
    +line
    *** Update File: <path>
    *** Move to: <new-path>
    @@ context hint
    -old line
    +new line
     context line
    *** Delete File: <path>
    *** End Patch

Chunk matching is tolerant: exact lines first, then right-stripped, then fully
trimmed, then Unicode-punctuation-normalised — mirroring opencode's `seekSequence`.
"""

from __future__ import annotations

import os
import re
from typing import Any

from core.toolimpl.editors import join_bom, split_bom


class PatchError(Exception):
    """Raised for malformed patches / failed applies; the message goes to the model."""


class Hunk:
    type: str           # "add" | "delete" | "update"
    path: str           # relative path
    move_path: str | None = None
    contents: str = ""          # for add
    chunks: list[dict] = None   # for update: [{"old_lines", "new_lines", "context"}]


def _strip_heredoc(input_text: str) -> str:
    match = re.match(r"^(?:cat\s+)?<<['\"]?(\w+)['\"]?\s*\n([\s\S]*?)\n\1\s*$", input_text)
    if match:
        return match.group(2)
    return input_text


def parse_patch(patch_text: str) -> list[Hunk]:
    cleaned = _strip_heredoc(patch_text.strip())
    lines = cleaned.split("\n")
    hunks: list[Hunk] = []
    begin_marker = "*** Begin Patch"
    end_marker = "*** End Patch"

    begin_idx = next((i for i, ln in enumerate(lines) if ln.strip() == begin_marker), -1)
    end_idx = next((i for i, ln in enumerate(lines) if ln.strip() == end_marker), -1)

    if begin_idx == -1 or end_idx == -1 or begin_idx >= end_idx:
        raise PatchError("Invalid patch format: missing Begin/End markers")

    open_file_in_add = False
    open_add_contents: list[str] = []

    def _finish_add_file() -> None:
        nonlocal open_file_in_add
        content = "".join(open_add_contents)
        if content.endswith("\n"):
            content = content[:-1]
        add_hunk = Hunk()
        add_hunk.type = "add"
        add_hunk.path = open_file_path
        add_hunk.contents = content
        hunks.append(add_hunk)
        open_file_in_add = False
        open_add_contents.clear()

    def _start_update_chunk(i: int, current_hunk: Hunk) -> int:
        """lines[i] starts with @@ — begin a new chunk on the current update hunk."""
        if current_hunk is None:
            raise PatchError("@@ chunk outside of an Update File block")
        context = lines[i][2:].strip()
        i += 1
        old_lines: list[str] = []
        new_lines: list[str] = []
        is_end_of_file = False
        while i < len(lines) and not lines[i].startswith("@@") and not lines[i].startswith("***") and lines[i] != "*** End of File":
            change_line = lines[i]
            if change_line == "*** End of File":
                is_end_of_file = True
                i += 1
                break
            if change_line.startswith(" "):
                content = change_line[1:]
                old_lines.append(content)
                new_lines.append(content)
            elif change_line.startswith("-"):
                old_lines.append(change_line[1:])
            elif change_line.startswith("+"):
                new_lines.append(change_line[1:])
            i += 1
        current_hunk.chunks.append({
            "old_lines": old_lines,
            "new_lines": new_lines,
            "change_context": context or None,
            "is_end_of_file": is_end_of_file or None,
        })
        return i

    i = begin_idx + 1
    while i < end_idx:
        line = lines[i].strip()

        if line.startswith("*** Add File:"):
            if open_file_in_add:
                _finish_add_file()
            file_path = line[len("*** Add File:"):].strip()
            if not file_path:
                raise PatchError("Add File header missing a path")
            open_file_path = file_path
            open_file_in_add = True
            open_add_contents.clear()
            i += 1
            while i < end_idx and not lines[i].strip().startswith("***"):
                if lines[i].startswith("+"):
                    open_add_contents.append(lines[i][1:] + "\n")
                i += 1
            _finish_add_file()
            continue

        if line.startswith("*** Delete File:"):
            if open_file_in_add:
                _finish_add_file()
            file_path = line[len("*** Delete File:"):].strip()
            if not file_path:
                raise PatchError("Delete File header missing a path")
            del_hunk = Hunk()
            del_hunk.type = "delete"
            del_hunk.path = file_path
            hunks.append(del_hunk)
            i += 1
            continue

        if line.startswith("*** Update File:"):
            if open_file_in_add:
                _finish_add_file()
            file_path = line[len("*** Update File:"):].strip()
            if not file_path:
                raise PatchError("Update File header missing a path")
            upd_hunk = Hunk()
            upd_hunk.type = "update"
            upd_hunk.path = file_path
            upd_hunk.chunks = []
            hunks.append(upd_hunk)
            i += 1
            if i < end_idx and lines[i].strip().startswith("*** Move to:"):
                upd_hunk.move_path = lines[i].strip()[len("*** Move to:"):].strip()
                i += 1
            # Skip stray text until the first @@ chunk.
            while i < end_idx and not lines[i].strip().startswith("@@") \
                    and not lines[i].strip().startswith("***"):
                i += 1
            continue

        if line.startswith("@@") and hunks and hunks[-1].type == "update":
            i = _start_update_chunk(i, hunks[-1])
            continue

        i += 1

    if open_file_in_add:
        _finish_add_file()

    if not hunks:
        raise PatchError("apply_patch verification failed: no hunks found")
    return hunks


# ─────────────────────────────────────────────────────────────────────
# Applying
# ─────────────────────────────────────────────────────────────────────
def _normalize_unicode(text: str) -> str:
    """Normalize common Unicode punctuation to ASCII (port of edit.ts/rust)."""
    return (
        text.replace("‘", "'").replace("’", "'").replace("‚", "'").replace("‛", "'")
        .replace("“", '"').replace("”", '"').replace("„", '"').replace("‟", '"')
        .replace("‐", "-").replace("‑", "-").replace("‒", "-").replace("–", "-")
        .replace("—", "-").replace("―", "-").replace("…", "...")
    )


def _try_match(lines: list[str], pattern: list[str], start: int,
               compare, eof: bool) -> int:
    if eof:
        from_end = len(lines) - len(pattern)
        if from_end >= start and from_end >= 0:
            if all(compare(lines[from_end + j], pattern[j]) for j in range(len(pattern))):
                return from_end
    for i in range(start, len(lines) - len(pattern) + 1):
        if all(compare(lines[i + j], pattern[j]) for j in range(len(pattern))):
            return i
    return -1


def _seek_sequence(lines: list[str], pattern: list[str], start: int, eof: bool = False) -> int:
    if not pattern:
        return -1
    passes = [
        lambda a, b: a == b,
        lambda a, b: a.rstrip() == b.rstrip(),
        lambda a, b: a.strip() == b.strip(),
        lambda a, b: _normalize_unicode(a.strip()) == _normalize_unicode(b.strip()),
    ]
    for compare in passes:
        idx = _try_match(lines, pattern, start, compare, eof)
        if idx != -1:
            return idx
    return -1


def _search_lines(text: str, bom: bool) -> list[str]:
    _, body = split_bom(text)
    lines = body.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def _compute_replacements(original_lines: list[str], file_path: str,
                          chunks: list[dict]) -> list[tuple[int, int, list[str]]]:
    replacements: list[tuple[int, int, list[str]]] = []
    line_index = 0
    for chunk in chunks:
        context = chunk.get("change_context")
        if context:
            context_idx = _seek_sequence(original_lines, [context], line_index)
            if context_idx == -1:
                raise PatchError(f"Failed to find context '{context}' in {file_path}")
            line_index = context_idx + 1

        old_lines: list[str] = chunk["old_lines"]
        new_lines: list[str] = chunk["new_lines"]
        eof = bool(chunk.get("is_end_of_file"))

        if not old_lines:
            insertion_idx = (
                len(original_lines) - 1
                if original_lines and original_lines[-1] == ""
                else len(original_lines)
            )
            replacements.append((insertion_idx, 0, new_lines))
            continue

        pattern = list(old_lines)
        new_slice = list(new_lines)
        found = _seek_sequence(original_lines, pattern, line_index, eof)
        if found == -1 and pattern and pattern[-1] == "":
            pattern = pattern[:-1]
            if new_slice and new_slice[-1] == "":
                new_slice = new_slice[:-1]
            found = _seek_sequence(original_lines, pattern, line_index, eof)
        if found != -1:
            replacements.append((found, len(pattern), new_slice))
            line_index = found + len(pattern)
        else:
            raise PatchError(
                f"Failed to find expected lines in {file_path}:\n" + "\n".join(old_lines)
            )

    replacements.sort(key=lambda r: r[0])
    return replacements


def _apply_replacements(lines: list[str], replacements: list[tuple[int, int, list[str]]]) -> list[str]:
    result = list(lines)
    for start_idx, old_len, new_segment in reversed(replacements):
        del result[start_idx:start_idx + old_len]
        for j, item in enumerate(new_segment):
            result.insert(start_idx + j, item)
    return result


def derive_new_contents(path: str, chunks: list[dict], original_text: str) -> tuple[str, bool]:
    """Apply update chunks to the original text. Returns (new_text, desired_bom)."""
    bom, _ = split_bom(original_text)
    original_lines = _search_lines(original_text, bom)

    replacements = _compute_replacements(original_lines, path, chunks)
    new_lines = _apply_replacements(original_lines, replacements)

    if not new_lines or new_lines[-1] != "":
        new_lines.append("")
    new_text = "\n".join(new_lines)
    next_bom, new_text_body = split_bom(new_text)
    return new_text_body, bom or next_bom


def apply_patch(patch_text: str, workdir: str) -> dict[str, Any]:
    """Parse and apply a patch under `workdir`. Returns an affected-paths summary.

    Raises PatchError on any parse/apply problem.
    """
    hunks = parse_patch(patch_text)
    added: list[str] = []
    modified: list[str] = []
    deleted: list[str] = []

    for hunk in hunks:
        target = os.path.join(workdir, hunk.move_path if (hunk.type == "update" and hunk.move_path) else hunk.path)
        target = os.path.normpath(target)

        if hunk.type == "add":
            os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
            with open(target, "w", encoding="utf-8") as f:
                f.write(_add_file_contents(hunk.contents))
            added.append(target)

        elif hunk.type == "delete":
            if not os.path.isfile(target):
                raise PatchError(f"apply_patch verification failed: Failed to read file to delete: {target}")
            os.remove(target)
            deleted.append(target)

        else:  # update
            source = os.path.join(workdir, hunk.path)
            source = os.path.normpath(source)
            if not os.path.isfile(source):
                raise PatchError(f"apply_patch verification failed: Failed to read file to update: {source}")
            with open(source, encoding="utf-8") as f:
                original_text = f.read()

            new_text, bom = derive_new_contents(source, hunk.chunks, original_text)

            if hunk.move_path:
                os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
                with open(target, "w", encoding="utf-8") as f:
                    f.write(join_bom(new_text, bom))
                if os.path.abspath(source) != os.path.abspath(target):
                    os.remove(source)
                modified.append(target)
            else:
                with open(target, "w", encoding="utf-8") as f:
                    f.write(join_bom(new_text, bom))
                modified.append(target)

    return {"added": added, "modified": modified, "deleted": deleted}


def _add_file_contents(contents: str) -> str:
    """opencode ensures a trailing newline on add hunks."""
    if contents == "" or contents.endswith("\n"):
        return contents
    return contents + "\n"


def hunk_paths(patch_text: str) -> list[str]:
    """Return the relative file paths referenced by a patch, in order.

    Used by the change tracker so a patch's files can be snapshotted up-front,
    mirroring how write/edit snapshots happen before execution.
    """
    try:
        return [h.path for h in parse_patch(patch_text)]
    except PatchError:
        return []
