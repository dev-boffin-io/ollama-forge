#!/usr/bin/env python3
"""
QThread workers — DirectChat, CrewChat, RAGBuild.
All heavy work happens off the GUI thread.
"""
import copy
import os
import time

from PyQt5.QtCore import QMutex, QMutexLocker, QThread, pyqtSignal

from ollama_client import OllamaClient
from groq_client import GroqClient

_FLUSH_INTERVAL = 0.12   # seconds — balances smoothness vs. syscall overhead


# ------------------------------------------------------------------ #
#  Mixin                                                               #
# ------------------------------------------------------------------ #
class _StopMixin:
    def __init__(self):
        self._mutex = QMutex()
        self._running = True

    def stop(self):
        with QMutexLocker(self._mutex):
            self._running = False

    def is_running(self) -> bool:
        with QMutexLocker(self._mutex):
            return self._running


# ------------------------------------------------------------------ #
#  Direct single-model chat                                            #
# ------------------------------------------------------------------ #
class DirectChatWorker(_StopMixin, QThread):
    token    = pyqtSignal(str)
    error    = pyqtSignal(str)
    finished = pyqtSignal(str, float, int)   # response, elapsed, chunks

    def __init__(self, model: str, messages: list[dict]):
        QThread.__init__(self)
        _StopMixin.__init__(self)
        self.model = model
        self.messages = messages
        self._client = OllamaClient()

    def run(self):
        t0 = time.time()
        response = ""
        buf = ""
        last_flush = 0
        chunks = 0
        try:
            for tok in self._client.chat_stream(self.model, self.messages):
                if not self.is_running():
                    break
                response += tok
                buf += tok
                chunks += 1
                now = time.time()
                if now - last_flush >= _FLUSH_INTERVAL:
                    self.token.emit(buf)
                    buf = ""
                    last_flush = now
            if buf:
                self.token.emit(buf)
            self.finished.emit(response.strip(), time.time() - t0, chunks)
        except Exception as e:
            self.error.emit(str(e))


# ------------------------------------------------------------------ #
#  Multi-agent crew  (supports both Ollama and Groq backends)          #
# ------------------------------------------------------------------ #
class CrewChatWorker(_StopMixin, QThread):
    token    = pyqtSignal(str)
    error    = pyqtSignal(str)
    finished = pyqtSignal(str, float, int)

    def __init__(self, prompt: str, crew_config: list[dict],
                 history: list[dict],
                 api_key: str = "",
                 api_model_override: str = ""):
        """
        api_key            — set to use Groq backend instead of Ollama
        api_model_override — when set, ALL agents use this Groq model
                             (overrides per-agent model in crew_config)
        """
        QThread.__init__(self)
        _StopMixin.__init__(self)
        self.prompt            = prompt
        self.crew_config       = crew_config
        self.history           = history
        self._api_key          = api_key
        self._model_override   = api_model_override

        # Choose backend
        if api_key:
            self._groq   = GroqClient(api_key=api_key)
            self._ollama = None
        else:
            self._ollama = OllamaClient()
            self._groq   = None

    def _run_agent(self, model: str, messages: list[dict]) -> str:
        response = ""
        buf = ""
        last_flush = 0

        # Pick the right stream source
        use_model = self._model_override if self._model_override else model
        if self._groq:
            stream = self._groq.chat_stream(use_model, messages)
        else:
            stream = self._ollama.chat_stream(model, messages)

        try:
            for tok in stream:
                if not self.is_running():
                    break
                response += tok
                buf += tok
                now = time.time()
                if now - last_flush >= _FLUSH_INTERVAL:
                    self.token.emit(buf)
                    buf = ""
                    last_flush = now
        except Exception as e:
            self.error.emit(f"[{use_model} error: {e}]")
        if buf:
            self.token.emit(buf)
        return response.strip()

    def run(self):
        t0 = time.time()
        total_len = 0
        previous = self.prompt

        header = f"# CREW REPORT\n\n**Request:** {self.prompt}\n\n---\n\n"
        self.token.emit(header)
        output = header

        # Inject recent user history into first agent
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
            output += out + "\n\n---\n\n"
            total_len += len(out)
            previous = out

        elapsed = time.time() - t0
        self.finished.emit(output, elapsed, total_len)


# ------------------------------------------------------------------ #
#  RAG index builder                                                   #
# ------------------------------------------------------------------ #
class RAGBuildWorker(_StopMixin, QThread):
    progress = pyqtSignal(int, int)   # done, total
    message  = pyqtSignal(str)
    finished = pyqtSignal()
    error    = pyqtSignal(str)

    def __init__(self, paths: list[str], embed_model: str):
        QThread.__init__(self)
        _StopMixin.__init__(self)
        self.paths = paths
        self.embed_model = embed_model

    def run(self):
        from rag_engine import RAGIndex
        try:
            idx = RAGIndex(embed_model=self.embed_model)
            idx.add_documents(
                self.paths,
                progress_cb=lambda d, t: self.progress.emit(d, t),
                message_cb=lambda m: self.message.emit(m),
                stop_cb=lambda: not self.is_running(),   # return True → stop
            )
            if self.is_running():
                self.finished.emit()
            else:
                self.message.emit("⛔ Indexing stopped by user.")
                self.finished.emit()
        except Exception as e:
            self.error.emit(str(e))


# ------------------------------------------------------------------ #
#  Groq API chat worker                                                #
# ------------------------------------------------------------------ #
class GroqChatWorker(_StopMixin, QThread):
    token    = pyqtSignal(str)
    error    = pyqtSignal(str)
    finished = pyqtSignal(str, float, int)   # response, elapsed, chunks

    def __init__(self, model: str, messages: list[dict], api_key: str):
        QThread.__init__(self)
        _StopMixin.__init__(self)
        self.model    = model
        self.messages = messages
        self._client  = GroqClient(api_key=api_key)

    def run(self):
        t0 = time.time()
        response = ""
        buf = ""
        last_flush = 0
        chunks = 0
        try:
            for tok in self._client.chat_stream(self.model, self.messages):
                if not self.is_running():
                    break
                response += tok
                buf += tok
                chunks += 1
                now = time.time()
                if now - last_flush >= _FLUSH_INTERVAL:
                    self.token.emit(buf)
                    buf = ""
                    last_flush = now
            if buf:
                self.token.emit(buf)
            self.finished.emit(response.strip(), time.time() - t0, chunks)
        except Exception as e:
            self.error.emit(str(e))
