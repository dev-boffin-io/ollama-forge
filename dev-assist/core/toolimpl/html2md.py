"""
HTML -> text / markdown conversion for the `web_fetch` tool.

A small, dependency-free subset of what `turndown` gives opencode: headings,
links, lists, code blocks, emphasis, blockquotes, images and horizontal rules
are converted to GitHub-ish markdown; script/style/nav/footer clutter is
dropped for the text form.
"""

from __future__ import annotations

import html as _html
import re
from html.parser import HTMLParser

_SKIP_TAGS = {"script", "style", "noscript", "iframe", "object", "embed"}
_BLOCK_TAGS = {
    "div", "section", "article", "header", "footer", "nav", "aside", "main",
    "p", "blockquote", "pre", "ul", "ol", "table", "tr", "h1", "h2", "h3",
    "h4", "h5", "h6",
}

_HEADING_LEVELS = {"h1": "1", "h2": "2", "h3": "3", "h4": "4", "h5": "5", "h6": "6"}


class MarkdownParser(HTMLParser):
    """Convert HTML into GitHub-ish markdown using only the stdlib."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0
        self._in_pre = 0
        self._in_code = 0
        self._lists: list[str] = []
        self._li_index: list[int] = []
        self._anchor_href = ""
        self._anchor_buf: list[str] = []
        self._in_a = 0
        self._pending_heading: str | None = None
        self._pending_image: list[str] | None = None  # [src, alt]
        self._just_started_block = True

    # ── output helpers ──────────────────────────────────────────────
    def _flush_image(self) -> None:
        if self._pending_image is None:
            return
        src, alt = self._pending_image
        self._pending_image = None
        if self._in_code > 0:
            return
        self._raw(f"![{alt}]({src})")

    def _raw(self, s: str) -> None:
        self.parts.append(s)

    def _inline(self, s: str) -> None:
        self._flush_image()
        self._raw(s)

    def _newline(self) -> None:
        if self.parts and self.parts[-1] != "\n":
            self._raw("\n")

    # ── parser callbacks ──────────────────────────────────────────────
    def handle_starttag(self, tag: str, attrs: list) -> None:
        a = dict(attrs)

        if tag in _SKIP_TAGS:
            self._skip_depth += 1
            return

        if tag in ("br",):
            self._newline()
            return

        if tag == "hr":
            self._newline()
            self._raw("---\n")
            return

        if tag == "img":
            self._pending_image = [a.get("src", ""), a.get("alt", "")]
            return

        if tag in _HEADING_LEVELS:
            self._newline()
            self._newline()
            self._pending_heading = _HEADING_LEVELS[tag]
            return

        if tag == "a":
            if self._in_a == 0:
                self._anchor_href = a.get("href", "")
                self._anchor_buf = []
            self._in_a += 1
            return

        if tag == "pre":
            self._in_pre += 1
            self._newline()
            self._newline()
            self._raw("```\n")
            return

        if tag == "code" and self._in_pre == 0:
            self._in_code += 1
            return

        if tag in ("strong", "b"):
            self._inline("**")
            return
        if tag in ("em", "i"):
            self._inline("*")
            return
        if tag in ("del", "s"):
            self._inline("~~")
            return

        if tag in ("ul", "ol"):
            self._lists.append(tag)
            self._li_index.append(0)
            self._newline()
            return
        if tag == "li":
            self._newline()
            self._li_index[-1] += 1
            kind = self._lists[-1] if self._lists else "ul"
            self._raw(f"{self._li_index[-1]}. " if kind == "ol" else "- ")
            return

        if tag == "blockquote":
            self._newline()
            self._raw("> ")
            return

        if tag in _BLOCK_TAGS:
            self._newline()
            self._newline()

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS:
            if self._skip_depth > 0:
                self._skip_depth -= 1
            return

        if tag in _HEADING_LEVELS:
            self._newline()
            self._newline()
            self._pending_heading = None
            return

        if tag == "a":
            if self._in_a > 0:
                self._in_a -= 1
            if self._in_a == 0:
                text = "".join(self._anchor_buf)
                href = self._anchor_href
                self._anchor_buf = []
                if text.strip():
                    self._raw(f"[{text.strip()}]({href})")
            return

        if tag == "pre":
            if self._in_pre > 0:
                self._in_pre -= 1
            self._raw("\n```\n")
            self._newline()
            return

        if tag == "code" and self._in_pre == 0:
            if self._in_code > 0:
                self._in_code -= 1
            return

        if tag in ("strong", "b"):
            self._inline("**")
            return
        if tag in ("em", "i"):
            self._inline("*")
            return
        if tag in ("del", "s"):
            self._inline("~~")
            return

        if tag in ("ul", "ol"):
            if self._lists:
                self._lists.pop()
                self._li_index.pop()
            self._newline()
            return

        if tag in _BLOCK_TAGS:
            self._newline()

    def handle_data(self, data: str) -> None:
        if self._skip_depth > 0:
            return

        if self._in_a > 0:
            self._anchor_buf.append(data)
            return

        if self._pending_heading is not None:
            self._raw(f"{'#' * int(self._pending_heading)} ")
            self._pending_heading = None

        self._flush_image()

        if self._in_code > 0 or self._in_pre > 0:
            self._raw(data)
        else:
            self._raw(data)

    def handle_startendtag(self, tag: str, attrs: list) -> None:
        a = dict(attrs)
        if tag == "img":
            self._pending_image = [a.get("src", ""), a.get("alt", "")]
            self._flush_image()
        elif tag == "br":
            self._newline()


def html_to_markdown(html_text: str) -> str:
    parser = MarkdownParser()
    parser.feed(html_text)
    parser.close()
    text = "".join(parser.parts)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def html_to_text(html_text: str) -> str:
    # Drop script/style/iframe content wholesale, keep everything else as text.
    body = re.sub(
        r"<\s*(?:script|style|noscript|iframe|object|embed)\b[^>]*>.*?"
        r"<\s*/\s*(?:script|style|noscript|iframe|object|embed)\s*>",
        " ",
        html_text,
        flags=re.I | re.S,
    )
    body = re.sub(
        r"<\s*(?:p|div|li|h[1-6]|br|tr|blockquote|pre)\b[^>]*>",
        "\n",
        body,
        flags=re.I,
    )
    body = re.sub(r"<[^>]+>", "", body)
    body = _html.unescape(body)
    body = re.sub(r"[ \t]+", " ", body)
    body = re.sub(r"[ \t]*\n[ \t]*", "\n", body)
    body = re.sub(r"\n{3,}", "\n\n", body)
    return body.strip()
