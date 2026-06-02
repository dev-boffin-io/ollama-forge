#!/usr/bin/env python3
"""
chat_renderer.py
Converts markdown chat messages to styled HTML for QTextBrowser display.
Supports: headers, bold/italic, tables, fenced code blocks (with copy),
          inline code, bullet/numbered lists, blockquotes, hr.
"""
import html as _html
import re


# ------------------------------------------------------------------ #
#  Inline markdown                                                     #
# ------------------------------------------------------------------ #
def _inline(text: str) -> str:
    """Process inline markdown: bold, italic, inline code, strikethrough."""
    # Split on inline-code spans first so we don't process their contents
    parts = re.split(r'(`[^`\n]+?`)', text)
    out = []
    for part in parts:
        if part.startswith('`') and part.endswith('`') and len(part) > 2:
            code = _html.escape(part[1:-1])
            out.append(f'<code class="ic">{code}</code>')
        else:
            p = _html.escape(part)
            # bold+italic
            p = re.sub(r'\*{3}(.+?)\*{3}', r'<b><em>\1</em></b>', p)
            # bold
            p = re.sub(r'\*{2}(.+?)\*{2}', r'<b>\1</b>', p)
            p = re.sub(r'__(.+?)__',        r'<b>\1</b>', p)
            # italic
            p = re.sub(r'\*(.+?)\*', r'<em>\1</em>', p)
            p = re.sub(r'_(.+?)_',   r'<em>\1</em>', p)
            # strikethrough
            p = re.sub(r'~~(.+?)~~', r'<s>\1</s>', p)
            out.append(p)
    return ''.join(out)


# ------------------------------------------------------------------ #
#  Table parser                                                        #
# ------------------------------------------------------------------ #
def _parse_table(lines: list[str], start: int) -> tuple[str, int]:
    """Parse markdown table. Returns (html, lines_consumed)."""
    header_cells = [c.strip() for c in lines[start].strip('|').split('|')]
    sep_cells    = lines[start + 1].strip('|').split('|') if start + 1 < len(lines) else []

    aligns = []
    for cell in sep_cells:
        c = cell.strip()
        if c.startswith(':') and c.endswith(':'):
            aligns.append('center')
        elif c.endswith(':'):
            aligns.append('right')
        else:
            aligns.append('left')

    html = ['<table>',
            '<thead><tr>']
    for j, h in enumerate(header_cells):
        al = aligns[j] if j < len(aligns) else 'left'
        html.append(f'<th style="text-align:{al}">{_inline(h)}</th>')
    html.append('</tr></thead><tbody>')

    i = start + 2
    consumed = i - start
    while i < len(lines) and '|' in lines[i]:
        cells = [c.strip() for c in lines[i].strip('|').split('|')]
        html.append('<tr>')
        for j, c in enumerate(cells):
            al = aligns[j] if j < len(aligns) else 'left'
            html.append(f'<td style="text-align:{al}">{_inline(c)}</td>')
        html.append('</tr>')
        i += 1
        consumed += 1

    html.append('</tbody></table>')
    return '\n'.join(html), consumed


# ------------------------------------------------------------------ #
#  Block-level markdown → HTML                                        #
# ------------------------------------------------------------------ #
def md_to_html(text: str, code_store: list[str]) -> str:
    """
    Convert a markdown string to HTML.
    Code blocks are appended to code_store; a 'copy:N' anchor is emitted.
    """
    lines = text.split('\n')
    result = []
    i = 0
    in_ul = in_ol = False

    def close_lists():
        nonlocal in_ul, in_ol
        if in_ul:
            result.append('</ul>')
            in_ul = False
        if in_ol:
            result.append('</ol>')
            in_ol = False

    while i < len(lines):
        line = lines[i]

        # ── fenced code block ───────────────────────────────────────
        fence_m = re.match(r'^(`{3,}|~{3,})(.*)', line.rstrip())
        if fence_m:
            close_lists()
            fence_char = fence_m.group(1)[0]
            fence_len  = len(fence_m.group(1))
            lang       = fence_m.group(2).strip()
            code_lines = []
            i += 1
            while i < len(lines):
                closing = re.match(rf'^{re.escape(fence_char)}{{{fence_len},}}$', lines[i].rstrip())
                if closing:
                    i += 1
                    break
                code_lines.append(lines[i])
                i += 1
            code_text = '\n'.join(code_lines)
            idx = len(code_store)
            code_store.append(code_text)
            lang_html = f'<span class="clang">{_html.escape(lang)}</span>' if lang else '<span class="clang">code</span>'

            result.append(
                f'<div class="cb">'
                f'<div class="cbh">{lang_html}'
                f'<a href="copy:{idx}" class="cpbtn">📋 Copy</a>'
                f'</div>'
                f'<pre><code>{_html.escape(code_text)}</code></pre>'
                f'</div>'
            )
            continue

        # ── ATX heading ─────────────────────────────────────────────
        h_m = re.match(r'^(#{1,4})\s+(.*)', line)
        if h_m:
            close_lists()
            level = len(h_m.group(1))
            result.append(f'<h{level}>{_inline(h_m.group(2))}</h{level}>')
            i += 1
            continue

        # ── setext heading ──────────────────────────────────────────
        if (i + 1 < len(lines)
                and re.match(r'^=+\s*$', lines[i + 1])
                and line.strip()):
            close_lists()
            result.append(f'<h1>{_inline(line)}</h1>')
            i += 2
            continue
        if (i + 1 < len(lines)
                and re.match(r'^-+\s*$', lines[i + 1])
                and line.strip()):
            close_lists()
            result.append(f'<h2>{_inline(line)}</h2>')
            i += 2
            continue

        # ── horizontal rule ─────────────────────────────────────────
        if re.match(r'^([*\-_])\s*\1\s*\1[\s\1]*$', line.rstrip()):
            close_lists()
            result.append('<hr>')
            i += 1
            continue

        # ── blockquote ──────────────────────────────────────────────
        if line.startswith('>'):
            close_lists()
            content = line[1:].lstrip()
            result.append(f'<blockquote>{_inline(content)}</blockquote>')
            i += 1
            continue

        # ── bullet list ─────────────────────────────────────────────
        ul_m = re.match(r'^(\s{0,3})[-*+]\s+(.*)', line)
        if ul_m:
            if not in_ul:
                close_lists()
                result.append('<ul>')
                in_ul = True
            result.append(f'<li>{_inline(ul_m.group(2))}</li>')
            i += 1
            continue

        # ── ordered list ────────────────────────────────────────────
        ol_m = re.match(r'^(\s{0,3})\d+[.)]\s+(.*)', line)
        if ol_m:
            if not in_ol:
                close_lists()
                result.append('<ol>')
                in_ol = True
            result.append(f'<li>{_inline(ol_m.group(2))}</li>')
            i += 1
            continue

        # ── table ───────────────────────────────────────────────────
        if ('|' in line
                and i + 1 < len(lines)
                and re.match(r'^[\|: \-]+$', lines[i + 1].strip())):
            close_lists()
            tbl_html, consumed = _parse_table(lines, i)
            result.append(tbl_html)
            i += consumed
            continue

        # ── blank line ──────────────────────────────────────────────
        if not line.strip():
            close_lists()
            result.append('<p class="sp"></p>')
            i += 1
            continue

        # ── default paragraph / continuation ────────────────────────
        close_lists()
        result.append(f'<p>{_inline(line)}</p>')
        i += 1

    close_lists()
    return '\n'.join(result)


# ------------------------------------------------------------------ #
#  CSS                                                                 #
# ------------------------------------------------------------------ #
def _css(dark: bool) -> str:
    if dark:
        return """
        * { box-sizing: border-box; }
        body {
            background: #1e1e1e; color: #e8e8e8;
            font-family: 'DejaVu Sans', 'Segoe UI', sans-serif;
            font-size: 32px; margin: 10px; line-height: 1.75;
        }
        .msg { margin: 10px 0; border-radius: 8px; overflow: hidden; border: 1px solid #333; }
        .mh  { padding: 6px 14px; font-size: 30px; font-weight: bold; }
        .mb  { padding: 12px 16px; }
        .uh  { background: #1a3558; color: #7eb8f5; }
        .um  { border-color: #1a3558; }
        .um .mb { background: #1c2e40; white-space: pre-wrap; }
        .ah  { background: #1a3a1a; color: #7ec87e; }
        .am  { border-color: #1a3a1a; }
        .am .mb { background: #1c281c; }
        .st  { color: #888; font-size: 29px; font-style: italic;
               padding: 2px 8px; line-height: 1.5; }
        .st-cb {
            background: #111; border: 1px solid #2a2a2a;
            border-radius: 5px; margin: 3px 8px; overflow: hidden;
            font-style: normal;
        }
        .st-cb pre { margin:0; padding: 7px 12px; font-size: 22px;
                     font-family: 'Courier New', monospace; color: #aaa;
                     white-space: pre-wrap; }
        /* headings */
        h1 { color: #79b8ff; font-size: 1.45em; border-bottom: 1px solid #333;
             padding-bottom: 4px; margin: 10px 0 6px; }
        h2 { color: #79b8ff; font-size: 1.25em; margin: 9px 0 5px; }
        h3 { color: #85d3b0; font-size: 1.1em;  margin: 8px 0 4px; }
        h4 { color: #85d3b0; font-size: 1.0em;  margin: 7px 0 3px; }
        /* inline code */
        code.ic {
            background: #2a2a2a; color: #f08080;
            padding: 2px 7px; border-radius: 4px;
            font-family: 'Courier New', monospace; font-size: 28px;
        }
        /* fenced code block */
        .cb {
            background: #0d1117; border: 1px solid #30363d;
            border-radius: 7px; margin: 10px 0; overflow: hidden;
        }
        .cbh {
            background: #161b22; padding: 5px 14px;
            display: flex; justify-content: space-between; align-items: center;
        }
        .clang { color: #79b8ff; font-family: monospace; font-size: 26px; }
        .cpbtn {
            color: #58a6ff; text-decoration: none;
            font-size: 26px; cursor: pointer;
        }
        pre { margin: 0; padding: 14px 16px; overflow-x: auto; }
        pre code {
            color: #c9d1d9; font-family: 'Courier New', 'Consolas', monospace;
            font-size: 28px; line-height: 1.6; white-space: pre;
            background: transparent;
        }
        /* table */
        table { border-collapse: collapse; width: 100%; margin: 8px 0; }
        th { background: #21262d; color: #79b8ff; padding: 7px 14px;
             border: 1px solid #30363d; }
        td { padding: 6px 14px; border: 1px solid #30363d; }
        tr:nth-child(even) td { background: #161b22; }
        /* blockquote */
        blockquote {
            border-left: 3px solid #58a6ff; margin: 6px 0;
            padding: 4px 14px; color: #8b949e; background: #1c2128;
            border-radius: 0 4px 4px 0;
        }
        ul, ol { padding-left: 22px; margin: 4px 0; }
        li { margin: 3px 0; }
        hr { border: none; border-top: 1px solid #333; margin: 10px 0; }
        p  { margin: 4px 0; }
        p.sp { margin: 4px 0; height: 4px; }
        b, strong { color: #f0f0f0; }
        s { color: #888; }
        """
    else:
        return """
        * { box-sizing: border-box; }
        body {
            background: #ffffff; color: #24292e;
            font-family: 'DejaVu Sans', 'Segoe UI', sans-serif;
            font-size: 32px; margin: 10px; line-height: 1.75;
        }
        .msg { margin: 10px 0; border-radius: 8px; overflow: hidden; border: 1px solid #e1e4e8; }
        .mh  { padding: 6px 14px; font-size: 30px; font-weight: bold; }
        .mb  { padding: 12px 16px; }
        .uh  { background: #dbeafe; color: #1d4ed8; }
        .um  { border-color: #bfdbfe; }
        .um .mb { background: #eff6ff; white-space: pre-wrap; }
        .ah  { background: #dcfce7; color: #166534; }
        .am  { border-color: #bbf7d0; }
        .am .mb { background: #f0fdf4; }
        .st  { color: #888; font-size: 29px; font-style: italic;
               padding: 2px 8px; line-height: 1.5; }
        .st-cb {
            background: #f6f8fa; border: 1px solid #d0d7de;
            border-radius: 5px; margin: 3px 8px; overflow: hidden;
            font-style: normal;
        }
        .st-cb pre { margin:0; padding: 7px 12px; font-size: 22px;
                     font-family: 'Courier New', monospace; color: #666;
                     white-space: pre-wrap; }
        h1 { color: #1d4ed8; font-size: 1.45em; border-bottom: 1px solid #e1e4e8;
             padding-bottom: 4px; margin: 10px 0 6px; }
        h2 { color: #1d4ed8; font-size: 1.25em; margin: 9px 0 5px; }
        h3 { color: #0f766e; font-size: 1.1em;  margin: 8px 0 4px; }
        h4 { color: #0f766e; font-size: 1.0em;  margin: 7px 0 3px; }
        code.ic {
            background: #f3f4f6; color: #d73a49;
            padding: 2px 7px; border-radius: 4px;
            font-family: 'Courier New', monospace; font-size: 28px;
        }
        .cb {
            background: #f6f8fa; border: 1px solid #d0d7de;
            border-radius: 7px; margin: 10px 0; overflow: hidden;
        }
        .cbh {
            background: #eaeef2; padding: 5px 14px;
            display: flex; justify-content: space-between; align-items: center;
        }
        .clang { color: #0550ae; font-family: monospace; font-size: 26px; }
        .cpbtn {
            color: #0550ae; text-decoration: none;
            font-size: 26px; cursor: pointer;
        }
        pre { margin: 0; padding: 14px 16px; overflow-x: auto; }
        pre code {
            color: #24292e; font-family: 'Courier New', 'Consolas', monospace;
            font-size: 28px; line-height: 1.6; white-space: pre;
            background: transparent;
        }
        table { border-collapse: collapse; width: 100%; margin: 8px 0; }
        th { background: #f6f8fa; color: #0550ae; padding: 7px 14px;
             border: 1px solid #d0d7de; }
        td { padding: 6px 14px; border: 1px solid #d0d7de; }
        tr:nth-child(even) td { background: #f6f8fa; }
        blockquote {
            border-left: 3px solid #0550ae; margin: 6px 0;
            padding: 4px 14px; color: #57606a; background: #f6f8fa;
            border-radius: 0 4px 4px 0;
        }
        ul, ol { padding-left: 22px; margin: 4px 0; }
        li { margin: 3px 0; }
        hr { border: none; border-top: 1px solid #e1e4e8; margin: 10px 0; }
        p  { margin: 4px 0; }
        p.sp { margin: 4px 0; height: 4px; }
        s { color: #888; }
        """


# ------------------------------------------------------------------ #
#  Full chat HTML builder                                              #
# ------------------------------------------------------------------ #
def chat_html(messages: list[dict], code_store: list[str], dark: bool = True) -> str:
    """
    Build a full HTML document from a list of chat messages.
    Each message:  {type: 'user'|'ai'|'status', content: str, label?: str}
    """
    code_store.clear()
    parts = [f'<html><head><meta charset="utf-8"><style>{_css(dark)}</style></head><body>']

    for msg in messages:
        t       = msg.get('type', 'status')
        content = msg.get('content', '')

        if t == 'user':
            escaped = _html.escape(content)
            parts.append(
                f'<div class="msg um">'
                f'<div class="mh uh">🧑 You</div>'
                f'<div class="mb">{escaped}</div>'
                f'</div>'
            )

        elif t == 'ai':
            label   = _html.escape(msg.get('label', '🤖 AI'))
            rendered = md_to_html(content, code_store) if content.strip() else '<em style="color:#888">…</em>'
            parts.append(
                f'<div class="msg am">'
                f'<div class="mh ah">{label}</div>'
                f'<div class="mb">{rendered}</div>'
                f'</div>'
            )

        elif t == 'status':
            stripped = content.strip()
            if not stripped:
                continue
            # Render code blocks inside status messages as compact pre blocks
            cb_pattern = re.compile(r'```[\w+-]*\n(.*?)```', re.DOTALL)
            has_cb = cb_pattern.search(stripped)
            if has_cb:
                # Split on code fences; alternate: text, code, text, code …
                seg_pattern = re.compile(r'(```[\w+-]*\n.*?```)', re.DOTALL)
                segments = seg_pattern.split(stripped)
                out_parts = []
                for seg in segments:
                    if seg.startswith('```'):
                        inner = re.sub(r'^```[\w+-]*\n', '', seg).rstrip('`').strip()
                        escaped_inner = _html.escape(inner)
                        out_parts.append(
                            f'<div class="st-cb"><pre>{escaped_inner}</pre></div>'
                        )
                    else:
                        seg = seg.strip()
                        if seg:
                            out_parts.append(
                                f'<div class="st">{_html.escape(seg).replace(chr(10), "<br>")}</div>'
                            )
                parts.append(''.join(out_parts))
            else:
                escaped = _html.escape(stripped).replace('\n', '<br>')
                parts.append(f'<div class="st">{escaped}</div>')

    parts.append('</body></html>')
    return ''.join(parts)
