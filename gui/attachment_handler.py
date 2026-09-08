#!/usr/bin/env python3
"""
Attachment processor — reads any file (code, text, image, zip) into
model-ready content for injection or vision.
"""
import base64
import os
import zipfile

from PyQt6.QtCore import QBuffer, QIODevice, Qt
from PyQt6.QtGui import QImage

IMAGE_EXTS = frozenset({
    '.png', '.jpg', '.jpeg', '.webp', '.bmp', '.gif', '.tiff', '.ico'
})
MAX_TEXT_BYTES = 150_000   # 150 KB per text file
MAX_ZIP_FILES  = 30        # cap files extracted from zip

# Long-side cap (px) before sending an image to a vision model. Most vision
# models (LLaVA, Groq vision, etc.) don't benefit from anything larger, and
# oversized photos slow down base64 encoding, network transfer, and inference.
MAX_IMAGE_DIMENSION = 1568
JPEG_QUALITY = 85

_LANG_MAP = {
    'sh': 'bash', 'bash': 'bash', 'zsh': 'bash',
    'hpp': 'cpp', 'hxx': 'cpp', 'cc': 'cpp',
    'h': 'c',
    'yml': 'yaml',
    'rb': 'ruby',
    'rs': 'rust',
    'kt': 'kotlin',
    'ts': 'typescript',
    'tsx': 'tsx',
    'jsx': 'jsx',
    'pl': 'perl',
    'lua': 'lua',
    'r': 'r',
}


class AttachmentResult:
    """
    Holds processed attachment data ready for model injection.

    images      — list of base64-encoded image strings (for vision models)
    text_blocks — list of (filename, text_content) for code/text files
    summary     — one-line human-readable description
    file_count  — total number of files processed
    """

    def __init__(self):
        self.images:      list[str]                = []
        self.text_blocks: list[tuple[str, str]]    = []
        self.summary:     str                       = ""
        self.file_count:  int                       = 0

    def has_images(self) -> bool:
        return bool(self.images)

    def has_text(self) -> bool:
        return bool(self.text_blocks)

    def build_text_injection(self) -> str:
        """
        Build a markdown-formatted string to inject into the user message.
        Each text block becomes a fenced code block with correct language tag.
        """
        if not self.text_blocks:
            return ""
        parts = []
        for fname, content in self.text_blocks:
            raw_ext = os.path.splitext(fname)[1].lstrip('.').lower()
            lang = _LANG_MAP.get(raw_ext, raw_ext) or 'text'
            parts.append(f"### `{fname}`\n```{lang}\n{content}\n```")
        return "\n\n".join(parts)


# ─────────────────────────────────────────────────────────────────────
def _ext(name: str) -> str:
    return os.path.splitext(name)[1].lower()


def _read_bytes(source) -> bytes:
    if isinstance(source, (str, os.PathLike)):
        with open(source, 'rb') as f:
            return f.read(MAX_TEXT_BYTES)
    return bytes(source)[:MAX_TEXT_BYTES]


def _decode(raw: bytes) -> str:
    try:
        return raw.decode('utf-8')
    except UnicodeDecodeError:
        return raw.decode('latin-1', errors='replace')


def _to_b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def _optimize_image(data: bytes) -> bytes:
    """
    Downscale oversized images and re-encode to a smaller format before
    base64-encoding. Uses Qt's built-in QImage — already a dependency via
    PyQt6, so this needs no extra library (no OpenCV/Pillow install, no
    binary-size hit on the PyInstaller build).

    Falls back to the original bytes whenever decoding fails or the
    "optimized" result isn't actually smaller, so a bad/unsupported image
    never blocks the attachment.
    """
    try:
        img = QImage()
        if not img.loadFromData(data):
            return data  # not a format Qt can decode — send as-is

        w, h = img.width(), img.height()
        longest = max(w, h)
        needs_resize = longest > MAX_IMAGE_DIMENSION

        if needs_resize:
            scale = MAX_IMAGE_DIMENSION / longest
            new_w, new_h = max(1, round(w * scale)), max(1, round(h * scale))
            img = img.scaled(
                new_w, new_h,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )

        # Keep transparency where it exists (PNG); otherwise JPEG is far
        # smaller for photos and vision models don't need the alpha channel.
        has_alpha = img.hasAlphaChannel()
        buf = QBuffer()
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        if has_alpha:
            img.save(buf, "PNG")
        else:
            img.save(buf, "JPEG", JPEG_QUALITY)
        out = bytes(buf.data())

        if not out:
            return data
        if needs_resize or len(out) < len(data):
            return out
        return data
    except Exception:
        return data


# ─────────────────────────────────────────────────────────────────────
def process_attachment(path: str) -> AttachmentResult:
    """
    Process a single non-zip file (any type).
    Returns an AttachmentResult with images and/or text blocks populated.
    """
    result = AttachmentResult()
    result.file_count = 1
    ext   = _ext(path)
    fname = os.path.basename(path)

    if ext in IMAGE_EXTS:
        with open(path, 'rb') as f:
            raw = f.read()
        optimized = _optimize_image(raw)
        result.images.append(_to_b64(optimized))
        if len(optimized) < len(raw):
            result.summary = f"🖼️ {fname} ({len(raw)//1024}KB → {len(optimized)//1024}KB)"
        else:
            result.summary = f"🖼️ {fname}"
    else:
        try:
            content = _decode(_read_bytes(path))
            result.text_blocks.append((fname, content))
            result.summary = f"📄 {fname}"
        except Exception as e:
            result.summary = f"⚠️ Cannot read {fname}: {e}"

    return result


def list_zip_entries(zip_path: str) -> list[str]:
    """Return list of file entries in a zip (no directories)."""
    try:
        with zipfile.ZipFile(zip_path, 'r') as zf:
            return [n for n in zf.namelist() if not n.endswith('/')]
    except Exception:
        return []


def process_zip_selected(zip_path: str, selected: list[str]) -> AttachmentResult:
    """
    Process only the user-selected entries from a zip file.
    """
    result = AttachmentResult()
    result.file_count = len(selected)
    fname_zip = os.path.basename(zip_path)

    try:
        with zipfile.ZipFile(zip_path, 'r') as zf:
            for name in selected:
                ext = _ext(name)
                try:
                    data = zf.read(name)
                    if ext in IMAGE_EXTS:
                        result.images.append(_to_b64(_optimize_image(data)))
                    else:
                        result.text_blocks.append(
                            (name, _decode(data[:MAX_TEXT_BYTES]))
                        )
                except Exception:
                    pass
    except zipfile.BadZipFile:
        result.summary = "⚠️ Invalid ZIP file"
        return result

    n = len(selected)
    result.summary = f"📦 {fname_zip} ({n} file{'s' if n != 1 else ''} selected)"
    return result


# ─────────────────────────────────────────────────────────────────────
def build_zip_tree(entries: list[str]) -> str:
    """
    Build a compact directory-tree string from a list of zip entry paths.
    Example output:
        ollama-forge/
        ├── gui/
        │   ├── main.py
        │   └── workers.py
        └── README.md
    """
    from collections import defaultdict

    # Build nested dict
    def make_tree():
        return defaultdict(make_tree)

    root = make_tree()
    for path in entries:
        parts = path.replace('\\', '/').split('/')
        node = root
        for p in parts:
            node = node[p]

    lines = []
    def _render(node, prefix=""):
        keys = sorted(node.keys())
        for i, key in enumerate(keys):
            is_last = (i == len(keys) - 1)
            connector = "└── " if is_last else "├── "
            child = node[key]
            is_dir = bool(child)
            label = key + "/" if is_dir else key
            lines.append(prefix + connector + label)
            if is_dir:
                extension = "    " if is_last else "│   "
                _render(child, prefix + extension)

    _render(root)
    return "\n".join(lines)


def fetch_zip_file(zip_path: str, entry_name: str) -> tuple[str, str] | None:
    """
    Fetch a single file from a zip by entry name.
    Returns (filename, content) or None on error.
    Image files return (filename, "<binary image>") — not injected as text.
    """
    try:
        with zipfile.ZipFile(zip_path, 'r') as zf:
            data = zf.read(entry_name)
            ext = _ext(entry_name)
            if ext in IMAGE_EXTS:
                return (entry_name, f"<image file — {len(data)} bytes>")
            return (entry_name, _decode(data[:MAX_TEXT_BYTES]))
    except Exception as e:
        return None


def fetch_zip_files(zip_path: str, entry_names: list[str]) -> list[tuple[str, str]]:
    """Fetch multiple entries from a zip. Skips binary/image entries as text."""
    results = []
    try:
        with zipfile.ZipFile(zip_path, 'r') as zf:
            for name in entry_names:
                ext = _ext(name)
                if ext in IMAGE_EXTS:
                    continue
                try:
                    data = zf.read(name)
                    results.append((name, _decode(data[:MAX_TEXT_BYTES])))
                except Exception:
                    pass
    except Exception:
        pass
    return results
