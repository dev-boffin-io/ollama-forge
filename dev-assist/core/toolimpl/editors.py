"""
`edit_file` replacement engine — a Python port of opencode's edit tool.

The approaches in this module are sourced from https://github.com/sst/opencode
(copyright (c) 2025 opencode, MIT License) and, via opencode, from:
- https://github.com/cline/cline/blob/main/evals/diff-edits/diff-apply/diff-06-23-25.ts
- https://github.com/google-gemini/gemini-cli/blob/main/packages/core/src/utils/editCorrector.ts

The core idea: `replace()` tries a ladder of replacers, each yielding *exact*
substrings of the file that match `old_string` with increasing tolerance
(trimmed lines, block anchors, whitespace/indentation/escape normalization).
Only candidates that actually exist verbatim in the file are accepted, so the
match is applied on real characters — never on an approximation.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator

# Similarity thresholds for block-anchor fallback matching (port of edit.ts).
SINGLE_CANDIDATE_SIMILARITY_THRESHOLD = 0.65
MULTIPLE_CANDIDATES_SIMILARITY_THRESHOLD = 0.65

# Each replacer yields exact substrings of `content` that correspond to `find`.
Replacer = Callable[[str, str], Iterator[str]]


class EditError(Exception):
    """Raised when old_string cannot be applied; message goes back to the model."""


# ─────────────────────────────────────────────────────────────────────
# Line endings & BOM helpers
# ─────────────────────────────────────────────────────────────────────
def normalize_line_endings(text: str) -> str:
    return text.replace("\r\n", "\n")


def detect_line_ending(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def convert_to_line_ending(text: str, ending: str) -> str:
    if ending == "\n":
        return text
    return text.replace("\n", "\r\n")


def split_bom(text: str) -> tuple[bool, str]:
    """BOM splitter — returns (had_bom, text_without_bom)."""
    if text.startswith("\ufeff"):
        return True, text[len("\ufeff"):]
    return False, text


def join_bom(text: str, bom: bool) -> str:
    return ("\ufeff" if bom else "") + text


# ─────────────────────────────────────────────────────────────────────
# Distance
# ─────────────────────────────────────────────────────────────────────
def levenshtein(a: str, b: str) -> int:
    """Classic Levenshtein distance (port of edit.ts's DP matrix)."""
    if a == "" or b == "":
        return max(len(a), len(b))
    matrix = [[0] * (len(b) + 1) for _ in range(len(a) + 1)]
    for i in range(len(a) + 1):
        matrix[i][0] = i
    for j in range(len(b) + 1):
        matrix[0][j] = j
    for i in range(1, len(a) + 1):
        for j in range(1, len(b) + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            matrix[i][j] = min(
                matrix[i - 1][j] + 1,
                matrix[i][j - 1] + 1,
                matrix[i - 1][j - 1] + cost,
            )
    return matrix[len(a)][len(b)]


# ─────────────────────────────────────────────────────────────────────
# Replacers — each yields exact substrings of `content`
# ─────────────────────────────────────────────────────────────────────
def simple_replacer(content: str, find: str) -> Iterator[str]:
    yield find


def line_trimmed_replacer(content: str, find: str) -> Iterator[str]:
    original_lines = content.split("\n")
    search_lines = find.split("\n")
    if search_lines and search_lines[-1] == "":
        search_lines.pop()

    for i in range(len(original_lines) - len(search_lines) + 1):
        matches = all(
            original_lines[i + j].strip() == search_lines[j].strip()
            for j in range(len(search_lines))
        )
        if not matches:
            continue
        start = _line_byte_offset(original_lines, 0, i)
        end = _line_byte_offset(original_lines, i, i + len(search_lines))
        yield content[start:end]


def _line_byte_offset(lines: list[str], start: int, end: int) -> int:
    """Offset of lines[start:end], with one index per byte (len) *plus* newline."""
    total = 0
    for k in range(start, end):
        total += len(lines[k])
        if k < end - 1:
            total += 1  # the newline separating lines
    return total


def block_anchor_replacer(content: str, find: str) -> Iterator[str]:
    original_lines = content.split("\n")
    search_lines = find.split("\n")
    if len(search_lines) < 3:
        return
    if search_lines[-1] == "":
        search_lines.pop()

    first_line_search = search_lines[0].strip()
    last_line_search = search_lines[-1].strip()
    search_block_size = len(search_lines)
    max_line_delta = max(1, search_block_size // 4)

    candidates: list[tuple[int, int]] = []
    for i, line in enumerate(original_lines):
        if line.strip() != first_line_search:
            continue
        for j in range(i + 2, len(original_lines)):
            if original_lines[j].strip() == last_line_search:
                actual_block_size = j - i + 1
                if abs(actual_block_size - search_block_size) <= max_line_delta:
                    candidates.append((i, j))
                break  # only the first occurrence of the last line per anchor

    if not candidates:
        return

    def _block_similarity(start: int, end: int) -> float:
        actual_block_size = end - start + 1
        lines_to_check = min(search_block_size - 2, actual_block_size - 2)
        if lines_to_check <= 0:
            return 1.0
        similarity = 0.0
        for j in range(1, min(search_block_size - 1, actual_block_size - 1)):
            original_line = original_lines[start + j].strip()
            search_line = search_lines[j].strip()
            max_len = max(len(original_line), len(search_line))
            if max_len == 0:
                continue
            distance = levenshtein(original_line, search_line)
            similarity += (1 - distance / max_len) / lines_to_check
        return similarity

    if len(candidates) == 1:
        start, end = candidates[0]
        if _block_similarity(start, end) >= SINGLE_CANDIDATE_SIMILARITY_THRESHOLD:
            yield content[_line_byte_offset(original_lines, 0, start):_line_byte_offset(original_lines, start, end + 1)]
        return

    best: tuple[int, int] | None = None
    best_sim = -1.0
    for start, end in candidates:
        sim = _block_similarity(start, end)
        if sim > best_sim:
            best_sim = sim
            best = (start, end)
    if best and best_sim >= MULTIPLE_CANDIDATES_SIMILARITY_THRESHOLD:
        start, end = best
        yield content[_line_byte_offset(original_lines, 0, start):_line_byte_offset(original_lines, start, end + 1)]


def _normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def whitespace_normalized_replacer(content: str, find: str) -> Iterator[str]:
    normalized_find = _normalize_whitespace(find)
    lines = content.split("\n")

    for line in lines:
        if _normalize_whitespace(line) == normalized_find:
            yield line
        else:
            normalized_line = _normalize_whitespace(line)
            if normalized_find in normalized_line:
                words = find.strip().split()
                if words:
                    pattern = r"\s+".join(re.escape(w) for w in words)
                    match = re.search(pattern, line)
                    if match:
                        yield match.group(0)

    find_lines = find.split("\n")
    if len(find_lines) > 1:
        for i in range(len(lines) - len(find_lines) + 1):
            block = "\n".join(lines[i:i + len(find_lines)])
            if _normalize_whitespace(block) == normalized_find:
                yield block


def _remove_indentation(text: str) -> str:
    lines = text.split("\n")
    non_empty = [ln for ln in lines if ln.strip()]
    if not non_empty:
        return text
    min_indent = min(len(ln) - len(ln.lstrip()) for ln in non_empty)
    return "\n".join(ln if not ln.strip() else ln[min_indent:] for ln in lines)


def indentation_flexible_replacer(content: str, find: str) -> Iterator[str]:
    normalized_find = _remove_indentation(find)
    content_lines = content.split("\n")
    find_lines = find.split("\n")
    for i in range(len(content_lines) - len(find_lines) + 1):
        block = "\n".join(content_lines[i:i + len(find_lines)])
        if _remove_indentation(block) == normalized_find:
            yield block


_ESCAPES = {
    "n": "\n", "t": "\t", "r": "\r", "'": "'", '"': '"',
    "`": "`", "\\": "\\", "\n": "\n", "$": "$",
}


def _unescape_string(text: str) -> str:
    def _sub(match: re.Match) -> str:
        return _ESCAPES.get(match.group(1), match.group(0))

    return re.sub(r"\\(n|t|r|'|\"|`|\\|\n|\$)", _sub, text)


def escape_normalized_replacer(content: str, find: str) -> Iterator[str]:
    unescaped_find = _unescape_string(find)
    if unescaped_find in content:
        yield unescaped_find

    lines = content.split("\n")
    find_lines = unescaped_find.split("\n")
    for i in range(len(lines) - len(find_lines) + 1):
        block = "\n".join(lines[i:i + len(find_lines)])
        if _unescape_string(block) == unescaped_find:
            yield block


def trimmed_boundary_replacer(content: str, find: str) -> Iterator[str]:
    trimmed_find = find.strip()
    if trimmed_find == find:
        return
    if trimmed_find in content:
        yield trimmed_find
    lines = content.split("\n")
    find_lines = find.split("\n")
    for i in range(len(lines) - len(find_lines) + 1):
        block = "\n".join(lines[i:i + len(find_lines)])
        if block.strip() == trimmed_find:
            yield block


def context_aware_replacer(content: str, find: str) -> Iterator[str]:
    find_lines = find.split("\n")
    if len(find_lines) < 3:
        return
    if find_lines[-1] == "":
        find_lines.pop()

    content_lines = content.split("\n")
    first_line = find_lines[0].strip()
    last_line = find_lines[-1].strip()

    for i, ln in enumerate(content_lines):
        if ln.strip() != first_line:
            continue
        for j in range(i + 2, len(content_lines)):
            if content_lines[j].strip() != last_line:
                continue
            block_lines = content_lines[i:j + 1]
            block = "\n".join(block_lines)
            if len(block_lines) == len(find_lines):
                matching = total = 0
                for k in range(1, len(block_lines) - 1):
                    block_line = block_lines[k].strip()
                    find_line = find_lines[k].strip()
                    if block_line or find_line:
                        total += 1
                        if block_line == find_line:
                            matching += 1
                if total == 0 or matching / total >= 0.5:
                    yield block
                    break
            break


def multi_occurrence_replacer(content: str, find: str) -> Iterator[str]:
    start = 0
    while True:
        index = content.find(find, start)
        if index == -1:
            break
        yield find
        start = index + len(find)


REPLACERS: list[Replacer] = [
    simple_replacer,
    line_trimmed_replacer,
    block_anchor_replacer,
    whitespace_normalized_replacer,
    indentation_flexible_replacer,
    escape_normalized_replacer,
    trimmed_boundary_replacer,
    context_aware_replacer,
    multi_occurrence_replacer,
]


# ─────────────────────────────────────────────────────────────────────
# replace() — the public entry point
# ─────────────────────────────────────────────────────────────────────
def is_disproportionate_match(search: str, old_string: str) -> bool:
    old_lines = len(old_string.split("\n"))
    search_lines = len(search.split("\n"))
    if search_lines >= max(old_lines + 3, old_lines * 2):
        return True
    if old_lines == 1:
        return False
    return len(search.strip()) > max(len(old_string.strip()) + 500, len(old_string.strip()) * 4)


def replace(content: str, old_string: str, new_string: str, replace_all: bool = False) -> str:
    """Apply a single (or replace-all) text replacement to `content`.

    Mirrors opencode's edit logic: tries the replacer ladder, only accepts a
    candidate that exists verbatim in `content`, refuses disproportionate
    matches, and honours `replaceAll`. Raises EditError with model-facing
    guidance when the old string can't be applied.
    """
    if old_string == new_string:
        raise EditError("No changes to apply: oldString and newString are identical.")
    if old_string == "":
        raise EditError(
            "oldString cannot be empty when editing an existing file. Provide the exact text to replace, "
            "or use write for an intentional full-file replacement."
        )

    found_any = False
    for replacer in REPLACERS:
        for search in replacer(content, old_string):
            index = content.find(search)
            if index == -1:
                continue
            found_any = True
            if is_disproportionate_match(search, old_string):
                raise EditError(
                    "Refusing replacement because the matched span is much larger than oldString. "
                    "Re-read the file and provide the full exact oldString for the intended replacement."
                )
            if replace_all:
                return content.replace(search, new_string)
            last_index = content.rfind(search)
            if index != last_index:
                continue
            return content[:index] + new_string + content[index + len(search):]

    if not found_any:
        raise EditError(
            "Could not find oldString in the file. It must match exactly, including whitespace, "
            "indentation, and line endings."
        )
    raise EditError(
        "Found multiple matches for oldString. Provide more surrounding context to make the match unique."
    )


# ─────────────────────────────────────────────────────────────────────
# trim_diff — compact a unified diff by stripping common indentation
# ─────────────────────────────────────────────────────────────────────
def trim_diff(diff: str) -> str:
    lines = diff.split("\n")

    def _is_content(line: str) -> bool:
        return (
            line.startswith("+") or line.startswith("-") or line.startswith(" ")
        ) and not line.startswith("---") and not line.startswith("+++")

    content_lines = [ln for ln in lines if _is_content(ln)]
    if not content_lines:
        return diff

    indent = None
    for ln in content_lines:
        body = ln[1:]
        if body.strip():
            leading = len(body) - len(body.lstrip())
            indent = leading if indent is None else min(indent, leading)
    if indent is None or indent == 0:
        return diff

    out = []
    for ln in lines:
        if _is_content(ln):
            out.append(ln[0] + ln[1:][indent:])
        else:
            out.append(ln)
    return "\n".join(out)


def unified_patch(path: str, content_old: str, content_new: str) -> str:
    """Best-effort unified diff for a single file, compacted like opencode's."""
    import difflib

    def _has_changes(old: str, new: str) -> bool:
        return old != new

    if not _has_changes(content_old, content_new):
        return ""
    diff = "".join(
        difflib.unified_diff(
            content_old.splitlines(keepends=True),
            content_new.splitlines(keepends=True),
            fromfile=path,
            tofile=path,
            n=2,
        )
    )
    return trim_diff(diff.rstrip("\n"))
