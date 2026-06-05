#!/usr/bin/env python3
"""
workers.py — QThread workers (PyQt6).
DirectChat, CrewChat, RAGBuild, GroqChat, SmartChat, CodeRun.
All heavy work off the GUI thread.
"""
import copy
import os
import time

from PyQt6.QtCore import QMutex, QMutexLocker, QThread, pyqtSignal

from ollama_client import OllamaClient
from groq_client import GroqClient

_FLUSH_INTERVAL = 0.12

_SYSTEM_PROMPT = (
    "You are a direct, precise AI assistant. "
    "Answer without filler phrases like 'Sure!', 'Of course!', 'Great question!'. "
    "Write clean, correct, runnable code when asked. "
    "Use markdown formatting where it helps clarity. "
    "Be concise and complete — no meta-commentary, no apologies, "
    "no narration of what you are about to do."
)

_GROQ_VISION_KEYWORDS = (
    "vision", "llava", "llama-4", "llama4", "scout", "maverick"
)


# ── Mixin ─────────────────────────────────────────────────────────────────────
class _StopMixin:
    def __init__(self):
        self._mutex   = QMutex()
        self._running = True

    def stop(self):
        with QMutexLocker(self._mutex):
            self._running = False

    def is_running(self) -> bool:
        with QMutexLocker(self._mutex):
            return self._running


# ── Direct single-model chat ──────────────────────────────────────────────────
class DirectChatWorker(_StopMixin, QThread):
    token    = pyqtSignal(str)
    error    = pyqtSignal(str)
    finished = pyqtSignal(str, float, int)

    def __init__(self, model: str, messages: list[dict]):
        QThread.__init__(self)
        _StopMixin.__init__(self)
        self.model    = model
        self.messages = messages
        self._client  = OllamaClient()

    def run(self):
        t0       = time.time()
        response = ""
        buf      = ""
        last_flush = 0
        chunks   = 0
        try:
            for tok in self._client.chat_stream(self.model, self.messages):
                if not self.is_running():
                    break
                response   += tok
                buf        += tok
                chunks     += 1
                now = time.time()
                if now - last_flush >= _FLUSH_INTERVAL:
                    self.token.emit(buf)
                    buf        = ""
                    last_flush = now
            if buf:
                self.token.emit(buf)
            self.finished.emit(response.strip(), time.time() - t0, chunks)
        except Exception as e:
            self.error.emit(str(e))


# ── Multi-agent crew ──────────────────────────────────────────────────────────
class CrewChatWorker(_StopMixin, QThread):
    token    = pyqtSignal(str)
    error    = pyqtSignal(str)
    finished = pyqtSignal(str, float, int)

    def __init__(self, prompt: str, crew_config: list[dict],
                 history: list[dict],
                 api_key: str = "",
                 api_model_override: str = ""):
        QThread.__init__(self)
        _StopMixin.__init__(self)
        self.prompt          = prompt
        self.crew_config     = crew_config
        self.history         = history
        self._api_key        = api_key
        self._model_override = api_model_override

        if api_key:
            self._groq   = GroqClient(api_key=api_key)
            self._ollama = None
        else:
            self._ollama = OllamaClient()
            self._groq   = None

    def _run_agent(self, model: str, messages: list[dict]) -> str:
        response   = ""
        buf        = ""
        last_flush = 0
        use_model  = self._model_override if self._model_override else model
        stream     = (
            self._groq.chat_stream(use_model, messages)
            if self._groq
            else self._ollama.chat_stream(model, messages)
        )
        try:
            for tok in stream:
                if not self.is_running():
                    break
                response   += tok
                buf        += tok
                now = time.time()
                if now - last_flush >= _FLUSH_INTERVAL:
                    self.token.emit(buf)
                    buf        = ""
                    last_flush = now
        except Exception as e:
            self.error.emit(f"[{use_model} error: {e}]")
        if buf:
            self.token.emit(buf)
        return response.strip()

    def run(self):
        t0        = time.time()
        total_len = 0
        previous  = self.prompt

        header = f"# CREW REPORT\n\n**Request:** {self.prompt}\n\n---\n\n"
        self.token.emit(header)
        output = header

        user_hist = [m["content"] for m in self.history
                     if m["role"] == "user"][-6:]

        for i, agent in enumerate(self.crew_config, 1):
            if not self.is_running():
                break
            role    = agent.get("role", f"Agent {i}")
            model   = agent.get("model", "llama3.2:latest")
            sys_p   = agent.get("system_prompt", "").strip()
            inp_tpl = agent.get("input_prompt", "{previous}")

            label = f"\n**[{i}. {role} — {model}]**\n"
            self.token.emit(label)
            output += label

            inp = inp_tpl.format(previous=previous)
            if i == 1 and user_hist:
                inp += "\n\nPrior context:\n" + "\n".join(user_hist)

            msgs = []
            if sys_p:
                msgs.append({"role": "system", "content": sys_p})
            msgs.append({"role": "user", "content": inp})

            out = self._run_agent(model, msgs) or "[No response]"
            output    += out + "\n\n---\n\n"
            total_len += len(out)
            previous   = out

        self.finished.emit(output, time.time() - t0, total_len)


# ── RAG index builder ─────────────────────────────────────────────────────────
class RAGBuildWorker(_StopMixin, QThread):
    progress = pyqtSignal(int, int)
    message  = pyqtSignal(str)
    finished = pyqtSignal()
    error    = pyqtSignal(str)

    def __init__(self, paths: list[str], embed_model: str):
        QThread.__init__(self)
        _StopMixin.__init__(self)
        self.paths       = paths
        self.embed_model = embed_model

    def run(self):
        from rag_engine import RAGIndex
        try:
            idx = RAGIndex(embed_model=self.embed_model)
            idx.add_documents(
                self.paths,
                progress_cb=lambda d, t: self.progress.emit(d, t),
                message_cb=lambda m: self.message.emit(m),
                stop_cb=lambda: not self.is_running(),
            )
            if self.is_running():
                self.finished.emit()
            else:
                self.message.emit("⛔ Indexing stopped by user.")
                self.finished.emit()
        except Exception as e:
            self.error.emit(str(e))


# ── Groq API chat worker ──────────────────────────────────────────────────────
class GroqChatWorker(_StopMixin, QThread):
    token    = pyqtSignal(str)
    error    = pyqtSignal(str)
    finished = pyqtSignal(str, float, int)

    def __init__(self, model: str, messages: list[dict], api_key: str):
        QThread.__init__(self)
        _StopMixin.__init__(self)
        self.model    = model
        self.messages = messages
        self._client  = GroqClient(api_key=api_key)

    def run(self):
        t0       = time.time()
        response = ""
        buf      = ""
        last_flush = 0
        chunks   = 0
        try:
            for tok in self._client.chat_stream(self.model, self.messages):
                if not self.is_running():
                    break
                response   += tok
                buf        += tok
                chunks     += 1
                now = time.time()
                if now - last_flush >= _FLUSH_INTERVAL:
                    self.token.emit(buf)
                    buf        = ""
                    last_flush = now
            if buf:
                self.token.emit(buf)
            self.finished.emit(response.strip(), time.time() - t0, chunks)
        except Exception as e:
            self.error.emit(str(e))


# ── Smart Chat Worker ─────────────────────────────────────────────────────────
class SmartChatWorker(_StopMixin, QThread):
    token    = pyqtSignal(str)
    error    = pyqtSignal(str)
    finished = pyqtSignal(str, float, int)
    status   = pyqtSignal(str)

    def __init__(self, *, model: str, messages: list[dict],
                 images: list[str] = None,
                 text_injection: str = "",
                 api_mode: bool = False,
                 api_key: str = "",
                 available_models: list[dict] = None):
        QThread.__init__(self)
        _StopMixin.__init__(self)
        self.model            = model
        self.messages         = list(messages)
        self.images           = images or []
        self.text_injection   = text_injection
        self.api_mode         = api_mode
        self.api_key          = api_key
        self.available_models = available_models or []

        if api_mode:
            self._groq   = GroqClient(api_key=api_key)
            self._ollama = None
        else:
            self._ollama = OllamaClient()
            self._groq   = None

    def _is_vision_model(self, name: str) -> bool:
        if self.api_mode:
            lo = name.lower()
            return any(k in lo for k in _GROQ_VISION_KEYWORDS)
        for m in self.available_models:
            if m["name"] == name:
                return m.get("vision", False)
        lo = name.lower()
        return any(k in lo for k in (
            "llava", "vision", "bakllava", "moondream", "phi3-v", "minicpm-v"
        ))

    def _find_vision_model(self) -> str | None:
        for m in self.available_models:
            if m.get("vision") and not m.get("embed"):
                return m["name"]
        return None

    def _stream(self, model: str, messages: list[dict]):
        if self.api_mode:
            return self._groq.chat_stream(model, messages)
        return self._ollama.chat_stream(model, messages)

    def run(self):
        t0       = time.time()
        response = ""
        buf      = ""
        last_flush = 0
        chunks   = 0

        msgs = list(self.messages)
        if not msgs or msgs[0].get("role") != "system":
            msgs.insert(0, {"role": "system", "content": _SYSTEM_PROMPT})

        if self.text_injection:
            for i in range(len(msgs) - 1, -1, -1):
                if msgs[i]["role"] == "user":
                    msgs[i] = dict(msgs[i])
                    msgs[i]["content"] = (
                        msgs[i]["content"]
                        + "\n\n---\n**Attached files:**\n\n"
                        + self.text_injection
                    )
                    break

        model = self.model
        if self.images:
            if not self._is_vision_model(model):
                alt = self._find_vision_model()
                if alt:
                    self.status.emit(
                        f"🔭 Images detected — using vision model: {alt}"
                    )
                    model = alt
                else:
                    self.status.emit(
                        "⚠️ No vision model available — images will be skipped"
                    )
                    self.images = []

        if self.images and not self.api_mode:
            for i in range(len(msgs) - 1, -1, -1):
                if msgs[i]["role"] == "user":
                    msgs[i] = dict(msgs[i])
                    msgs[i]["images"] = self.images
                    break

        if self.images and self.api_mode:
            for i in range(len(msgs) - 1, -1, -1):
                if msgs[i]["role"] == "user":
                    text    = msgs[i]["content"]
                    content = [{"type": "text", "text": text}]
                    for b64 in self.images:
                        content.append({
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{b64}"}
                        })
                    msgs[i] = {"role": "user", "content": content}
                    break

        try:
            for tok in self._stream(model, msgs):
                if not self.is_running():
                    break
                response   += tok
                buf        += tok
                chunks     += 1
                now = time.time()
                if now - last_flush >= _FLUSH_INTERVAL:
                    self.token.emit(buf)
                    buf        = ""
                    last_flush = now
            if buf:
                self.token.emit(buf)
            self.finished.emit(response.strip(), time.time() - t0, chunks)
        except Exception as e:
            self.error.emit(str(e))


# ── Code Run Worker ───────────────────────────────────────────────────────────
class CodeRunWorker(_StopMixin, QThread):
    run_result = pyqtSignal(dict)
    finished   = pyqtSignal()
    status     = pyqtSignal(str)

    def __init__(self, response_text: str, *, model: str,
                 api_mode: bool = False, api_key: str = ""):
        QThread.__init__(self)
        _StopMixin.__init__(self)
        self.response_text = response_text
        self.model         = model
        self.api_mode      = api_mode
        self.api_key       = api_key

        if api_mode:
            self._groq   = GroqClient(api_key=api_key)
            self._ollama = None
        else:
            self._ollama = OllamaClient()
            self._groq   = None

    def _get_debug_fix(self, lang: str, code: str, error_output: str) -> str:
        fix_prompt = (
            f"Fix this {lang} code. Return ONLY the corrected code in a single "
            f"fenced ```{lang}\\n...\\n``` block. No explanation.\n\n"
            f"Original code:\n```{lang}\n{code}\n```\n\n"
            f"Error output:\n```\n{error_output[:2000]}\n```"
        )
        msgs = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user",   "content": fix_prompt},
        ]
        collected = ""
        try:
            stream = (
                self._groq.chat_stream(self.model, msgs)
                if self.api_mode
                else self._ollama.chat_stream(self.model, msgs)
            )
            for tok in stream:
                if not self.is_running():
                    break
                collected += tok
        except Exception:
            pass
        import re
        m = re.search(r'```[\w+#-]*\n(.*?)```', collected, re.DOTALL)
        return m.group(1).strip() if m else ""

    def run(self):
        from code_runner import extract_code_blocks, run_code

        blocks = extract_code_blocks(self.response_text)
        if not blocks:
            self.finished.emit()
            return

        self.status.emit(
            f"⚙️ Running {len(blocks)} code block"
            f"{'s' if len(blocks) > 1 else ''}…"
        )

        for lang, code in blocks:
            if not self.is_running():
                break

            from code_runner import is_blocking_code
            if is_blocking_code(code):
                self.status.emit(
                    f"⏭ [{lang}] skipped — server/blocking code (run manually)"
                )
                continue

            self.status.emit(f"▶ {lang}…")
            res = run_code(lang, code)
            res["attempt"]           = 1
            res["debugged_code"]     = ""
            res["debug_stdout"]      = ""
            res["debug_stderr"]      = ""
            res["debug_returncode"]  = -1

            failed = (
                res["returncode"] != 0
                or res.get("error")
                or (res["stderr"] and not res["stdout"])
            )
            if failed and self.is_running():
                err_out = res["stderr"] or res.get("error", "") or res["stdout"]
                self.status.emit(f"🔧 Debugging {lang}…")
                fixed = self._get_debug_fix(lang, code, err_out)
                if fixed:
                    res2 = run_code(lang, fixed)
                    res["attempt"]          = 2
                    res["debugged_code"]    = fixed
                    res["debug_stdout"]     = res2["stdout"]
                    res["debug_stderr"]     = res2["stderr"]
                    res["debug_returncode"] = res2["returncode"]

            self.run_result.emit(res)

        self.finished.emit()
