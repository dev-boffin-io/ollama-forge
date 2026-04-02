"""
Prompt Templates — Jinja2-based prompt management.

Falls back to plain string templates if Jinja2 is not installed.
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Any

TEMPLATES_DIR = Path(__file__).parent.parent / "prompts"

# ── Built-in templates (used when Jinja2 unavailable or template missing) ───

_BUILTIN: dict[str, str] = {
    "rag_ask": textwrap.dedent("""\
        You are an expert code assistant. You have been given relevant sections
        from a local codebase to answer the user's question.

        RELEVANT CODE CONTEXT:
        {% for chunk in chunks %}
        --- {{ chunk.filepath }} (lines {{ chunk.start_line|default('?') }}–{{ chunk.end_line|default('?') }}) ---
        {{ chunk.content }}

        {% endfor %}

        USER QUESTION: {{ query }}

        Instructions:
        - Answer based ONLY on the provided code context
        - Mention exact file names and function names when relevant
        - If you find bugs or issues, explain them clearly with fix suggestions
        - If asked for architecture explanation, describe what each component does
        - Use markdown formatting for code blocks
        - If the context doesn't contain enough information, say so clearly

        Answer:"""),

    "code_audit": textwrap.dedent("""\
        You are a senior code reviewer. Review the following git diff for:
        1. Bugs and logic errors
        2. Security vulnerabilities (SQL injection, XSS, secrets exposure, etc.)
        3. Performance issues
        4. Code style and best practice violations
        5. Missing error handling

        Be concise and actionable. Use bullet points.
        Format each finding as: `[SEVERITY] file.py: description`
        Severity levels: 🔴 CRITICAL  🟡 WARNING  🟢 INFO

        ```diff
        {{ diff }}
        ```

        Review:"""),

    "git_fix": textwrap.dedent("""\
        You are a git expert. The user has this git error:

        ```
        {{ error_output }}
        ```

        Context:
        - Branch: {{ branch }}
        - Remote: {{ remote|default('origin') }}
        - Operation: {{ operation|default('unknown') }}

        Provide:
        1. A clear explanation of WHY this error happened (1-2 sentences)
        2. The exact commands to fix it (in order)
        3. How to prevent it next time

        Be specific and safe — prefer non-destructive fixes.

        Fix:"""),

    "error_explain": textwrap.dedent("""\
        You are a helpful developer assistant. Explain this error in simple terms.

        ERROR:
        ```
        {{ error_text }}
        ```

        {% if context %}
        CONTEXT:
        {{ context }}
        {% endif %}

        Explain:
        1. What this error means (plain language)
        2. Most likely cause
        3. How to fix it (step by step)

        Keep it concise and practical.

        Explanation:"""),

    "shell_explain": textwrap.dedent("""\
        Explain what this shell command does in simple terms:

        ```bash
        {{ command }}
        ```

        {% if output %}
        Output:
        ```
        {{ output }}
        ```
        {% endif %}

        Explain each part of the command, any flags used, and what the output means.
        If there are risks or side effects, mention them.

        Explanation:"""),
}


def render(template_name: str, **kwargs: Any) -> str:
    """
    Render a named prompt template with given variables.

    Tries Jinja2 first (from prompts/*.j2 files), falls back to
    built-in string templates.
    """
    # Try loading from file
    tmpl_file = TEMPLATES_DIR / f"{template_name}.j2"

    try:
        from jinja2 import Environment, FileSystemLoader, StrictUndefined
        env = Environment(
            loader=FileSystemLoader(str(TEMPLATES_DIR)),
            undefined=StrictUndefined,
            trim_blocks=True,
            lstrip_blocks=True,
        )
        tmpl = env.get_template(f"{template_name}.j2")
        return tmpl.render(**kwargs)
    except ImportError:
        # Jinja2 not installed — use built-in with minimal substitution
        pass
    except Exception:
        pass

    # Fall back to built-in templates with simple substitution
    raw = _BUILTIN.get(template_name)
    if raw is None:
        raise KeyError(f"No prompt template named '{template_name}'")

    # Minimal Jinja2-compatible substitution for built-ins
    return _simple_render(raw, **kwargs)


def _simple_render(template: str, **kwargs: Any) -> str:
    """
    Very simple template renderer for when Jinja2 is unavailable.
    Handles: {{ var }}, {% for %}, {% if %}, {% endif %}, {% endfor %}
    """
    import re

    # Handle for loops: {% for item in list %}...{% endfor %}
    def replace_for(m: re.Match) -> str:
        var, iterable_name, body = m.group(1), m.group(2), m.group(3)
        items = kwargs.get(iterable_name, [])
        result = []
        for item in items:
            # substitute {{ var.attr }} inside body
            sub = body
            if isinstance(item, dict):
                for k, v in item.items():
                    sub = sub.replace(f"{{{{ {var}.{k} }}}}", str(v))
                    # handle |default filter
                    sub = re.sub(
                        rf"\{{{{  ?{var}\.{k}\|default\('[^']*'\)  ?\}}}}",
                        str(v), sub
                    )
            sub = re.sub(r"\{\{[^}]+\}\}", "", sub)  # clean leftover
            result.append(sub)
        return "".join(result)

    template = re.sub(
        r"\{%[-\s]*for (\w+) in (\w+)[-\s]*%\}(.*?)\{%[-\s]*endfor[-\s]*%\}",
        replace_for, template, flags=re.DOTALL
    )

    # Handle if blocks: {% if var %}...{% endif %}
    def replace_if(m: re.Match) -> str:
        cond, body = m.group(1).strip(), m.group(2)
        val = kwargs.get(cond)
        return body if val else ""

    template = re.sub(
        r"\{%[-\s]*if (\w+)[-\s]*%\}(.*?)\{%[-\s]*endif[-\s]*%\}",
        replace_if, template, flags=re.DOTALL
    )

    # Replace {{ var }} and {{ var|default('...') }}
    def replace_var(m: re.Match) -> str:
        expr = m.group(1).strip()
        # handle |default('fallback')
        dflt_match = re.match(r"(\w+)\|default\('([^']*)'\)", expr)
        if dflt_match:
            key, fallback = dflt_match.group(1), dflt_match.group(2)
            return str(kwargs.get(key, fallback))
        return str(kwargs.get(expr, ""))

    template = re.sub(r"\{\{\s*([^}]+)\s*\}\}", replace_var, template)

    return template.strip()


def list_templates() -> list[str]:
    """Return all available template names."""
    names = set(_BUILTIN.keys())
    if TEMPLATES_DIR.exists():
        names.update(f.stem for f in TEMPLATES_DIR.glob("*.j2"))
    return sorted(names)
