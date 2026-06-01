#!/usr/bin/env python3
"""
Code block executor.
Extracts fenced code blocks from AI responses and runs them in a subprocess.
Supports: Python, JavaScript/Node, Bash/Shell, C, C++, Go, Rust, Ruby,
          PHP, Perl, Lua, R, Java.
"""
import os
import re
import subprocess
import tempfile
import time

TIMEOUT = 15   # seconds per execution

# Maps lowercase fence lang → (runner_key_or_cmd, file_extension)
_RUNNERS: dict = {
    'python':     ('python3',   '.py'),
    'python3':    ('python3',   '.py'),
    'py':         ('python3',   '.py'),
    'javascript': ('node',      '.js'),
    'js':         ('node',      '.js'),
    'node':       ('node',      '.js'),
    'typescript': ('_ts',       '.ts'),
    'ts':         ('_ts',       '.ts'),
    'bash':       ('bash',      '.sh'),
    'sh':         ('bash',      '.sh'),
    'shell':      ('bash',      '.sh'),
    'zsh':        ('zsh',       '.sh'),
    'ruby':       ('ruby',      '.rb'),
    'rb':         ('ruby',      '.rb'),
    'php':        ('php',       '.php'),
    'perl':       ('perl',      '.pl'),
    'lua':        ('lua',       '.lua'),
    'r':          ('Rscript',   '.r'),
    'go':         ('_go',       '.go'),
    'rust':       ('_rust',     '.rs'),
    'c':          ('_c',        '.c'),
    'cpp':        ('_cpp',      '.cpp'),
    'c++':        ('_cpp',      '.cpp'),
    'java':       ('_java',     '.java'),
}

SUPPORTED_LANGS = frozenset(_RUNNERS.keys())

# Patterns that indicate long-running / blocking code — skip auto-execution
_BLOCKING_PATTERNS = re.compile(
    r'\b('
    r'serve_forever|app\.run\s*\(|uvicorn\.run|hypercorn\.run|'
    r'server\.listen|asyncio\.run\s*\(.*loop|'
    r'while\s+True\s*:|'
    r'socketserver\.|HTTPServer|ThreadingHTTPServer|'
    r'tornado\.ioloop|flask\.run|fastapi|starlette\.run|'
    r'grpc\.server|zmq\.|pika\.|kafka|celery\.start|'
    r'websocket\.serve|websockets\.serve'
    r')\b',
    re.IGNORECASE,
)

def is_blocking_code(code: str) -> bool:
    """Return True if code is a server/daemon that would block indefinitely."""
    return bool(_BLOCKING_PATTERNS.search(code))


# ─────────────────────────────────────────────────────────────────────
def extract_code_blocks(text: str) -> list:
    """
    Extract (lang, code) pairs from markdown fenced blocks.
    Only returns blocks for supported/runnable languages.
    Deduplicates identical blocks.
    """
    pattern = re.compile(r'```([\w+#-]+)\n(.*?)```', re.DOTALL)
    seen = set()
    blocks = []
    for m in pattern.finditer(text):
        lang = m.group(1).strip().lower()
        code = m.group(2).strip()
        if lang in SUPPORTED_LANGS and code:
            key = (lang, code)
            if key not in seen:
                seen.add(key)
                blocks.append((lang, code))
    return blocks


def run_code(lang: str, code: str) -> dict:
    """
    Execute a code block.

    Returns dict:
        lang        str   — language name
        code        str   — original source
        stdout      str   — captured stdout (truncated at 5000 chars)
        stderr      str   — captured stderr (truncated at 2000 chars)
        returncode  int   — process exit code (-1 on internal error)
        elapsed     float — wall-clock seconds
        error       str|None — internal error message (not process error)
    """
    result = {
        'lang': lang, 'code': code,
        'stdout': '', 'stderr': '',
        'returncode': -1, 'elapsed': 0.0, 'error': None,
    }

    entry = _RUNNERS.get(lang)
    if not entry:
        result['error'] = f"Unsupported language: {lang}"
        return result

    runner, ext = entry
    t0 = time.time()

    with tempfile.TemporaryDirectory() as tmpdir:
        src = os.path.join(tmpdir, f"code{ext}")
        with open(src, 'w', encoding='utf-8') as f:
            f.write(code)
        try:
            proc = _dispatch(runner, src, tmpdir, code)
            if proc is not None:
                result['stdout']     = proc.stdout[:5000]
                result['stderr']     = proc.stderr[:2000]
                result['returncode'] = proc.returncode
        except subprocess.TimeoutExpired:
            result['error'] = f"Timed out after {TIMEOUT}s"
        except FileNotFoundError as e:
            result['error'] = f"Runtime not found: {e.filename}"
        except Exception as e:
            result['error'] = str(e)

    result['elapsed'] = time.time() - t0
    return result


# ─────────────────────────────────────────────────────────────────────
def _run(cmd: list, cwd: str = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, capture_output=True, text=True,
        timeout=TIMEOUT, cwd=cwd,
    )


def _compile(cmd: list, timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout,
    )


def _dispatch(runner: str, src: str, tmpdir: str, code: str):
    out_bin = os.path.join(tmpdir, 'out')

    if runner == '_go':
        return _run(['go', 'run', src], cwd=tmpdir)

    if runner == '_rust':
        cp = _compile(['rustc', src, '-o', out_bin])
        return cp if cp.returncode != 0 else _run([out_bin])

    if runner == '_c':
        cp = _compile(['gcc', src, '-o', out_bin, '-lm'])
        return cp if cp.returncode != 0 else _run([out_bin])

    if runner == '_cpp':
        cp = _compile(['g++', src, '-o', out_bin, '-std=c++17', '-lm'])
        return cp if cp.returncode != 0 else _run([out_bin])

    if runner == '_java':
        m = re.search(r'public\s+class\s+(\w+)', code)
        classname = m.group(1) if m else 'Main'
        java_src = os.path.join(tmpdir, f"{classname}.java")
        with open(java_src, 'w', encoding='utf-8') as f:
            f.write(code)
        cp = _compile(['javac', java_src])
        return cp if cp.returncode != 0 else _run(['java', classname], cwd=tmpdir)

    if runner == '_ts':
        # Try ts-node, fall back to npx ts-node
        for cmd in (['ts-node', src], ['npx', 'ts-node', src]):
            try:
                return _run(cmd)
            except FileNotFoundError:
                continue
        raise FileNotFoundError('ts-node')

    # Standard interpreter  (e.g. 'python3', 'node', 'bash', 'ruby' …)
    return _run(runner.split() + [src])
