"""
Tests for core/providers.py — catalog, adapters, Anthropic/OpenAI conversion.

These tests never touch the network or the real `ollama` package:
HTTP plumbing (`core.providers._http_json`, `_sse_events`) is the only
thing that talks to the wire, and it is monkeypatched here.
"""

import sys
import os
import json

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core import providers as P
from core.providers import (
    PROVIDERS,
    PROVIDER_ORDER,
    ProviderError,
    _anthropic_convert_messages,
    _anthropic_response,
    _anthropic_tools,
    _sse_token,
    make_provider,
)


class TestCatalog:
    def test_order_covers_all_providers(self):
        assert set(PROVIDER_ORDER) == set(PROVIDERS)

    def test_every_provider_has_required_fields(self):
        for pid, prof in PROVIDERS.items():
            assert prof["label"]
            assert prof["kind"] in ("ollama", "openai_compat", "anthropic")
            assert prof["default_model"]

    def test_defaults_raw_is_jsonable_and_keyless(self):
        raw = P.provider_defaults_raw()
        json.dumps(raw)  # no crash
        assert all("api_key" not in v for v in raw.values())

    def test_azure_carries_endpoint_props(self):
        assert PROVIDERS["azure"]["azure"] is True
        assert PROVIDERS["azure"]["api_version"]


class TestConfigHelpers:
    def test_active_provider_legacy_ollama(self):
        assert P.active_provider({"ai_engine": "ollama"}) == "ollama"

    def test_active_provider_legacy_api_maps_to_groq(self):
        assert P.active_provider({"ai_engine": "api"}) == "groq"

    def test_active_provider_explicit_id_wins(self):
        assert P.active_provider({"active_provider": "mistral"}) == "mistral"

    def test_provider_profile_merges_catalog_defaults(self):
        prof = P.provider_profile({"ai_engine": "ollama"}, "ollama")
        assert prof["base_url"] == "http://localhost:11434"
        assert prof["label"]

    def test_provider_key_env_wins(self, monkeypatch):
        monkeypatch.setenv("GROQ_API_KEY", "env-secret")
        cfg = {"ai_engine": "api"}
        assert P.provider_key(cfg, "groq") == "env-secret"

    def test_provider_key_env_beats_stored(self, monkeypatch):
        monkeypatch.setenv("GROQ_API_KEY", "env-secret")
        cfg = {"ai_engine": "api", "providers": {"groq": {"api_key": "stored"}}}
        assert P.provider_key(cfg, "groq") == "env-secret"

    def test_provider_key_stored_profile(self):
        cfg = {"ai_engine": "api", "providers": {"groq": {"api_key": "stored"}}}
        assert P.provider_key(cfg, "groq") == "stored"

    def test_provider_key_dev_assist_fallback(self, monkeypatch):
        monkeypatch.setenv("DEV_ASSIST_API_KEY", "legacy")
        assert P.provider_key({}, "openai") == "legacy"

    def test_provider_key_legacy_api_engine_fallback(self):
        cfg = {"ai_engine": "api", "api_engine": {"api_key": "legacy-groq-key"}}
        assert P.provider_key(cfg, "groq") == "legacy-groq-key"

    def test_provider_key_empty_for_keyless(self):
        assert P.provider_key({}, "ollama") == ""


class TestKeylessBehaviour:
    """Keys are never required up front; failures happen at chat time."""

    def test_missing_key_is_ok_until_chat(self, monkeypatch):
        monkeypatch.delenv("GROQ_API_KEY", raising=False)
        prov = make_provider("groq", dict(PROVIDERS["groq"]))
        assert prov.list_models()  # static fallback, no key needed
        with pytest.raises(ProviderError):
            list(prov.chat([{"role": "user", "content": "hi"}]))

    def test_openai_compat_no_key_stream_raises(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        prov = make_provider("openai", dict(PROVIDERS["openai"]))
        with pytest.raises(ProviderError):
            list(prov.stream([{"role": "user", "content": "hi"}]))


class TestOpenAICompatAdapter:
    def _client(self):
        return make_provider("groq", dict(PROVIDERS["groq"]), api_key="k")

    def test_chat_url(self):
        assert self._client()._chat_url("llama-3.3-70b-versatile").endswith("/chat/completions")

    def test_chat_passes_tools_and_parses(self, monkeypatch):
        prov = self._client()

        def fake_json(url, payload=None, headers=None, timeout=None):
            assert payload["model"] == "llama-3.3-70b-versatile"
            assert payload["stream"] is False
            assert payload["tools"] == [{"type": "function"}]
            assert headers["Authorization"] == "Bearer k"
            return {"choices": [{"message": {"role": "assistant", "content": "yo",
                                             "tool_calls": []}}]}

        monkeypatch.setattr(P, "_http_json", fake_json)
        out = prov.chat([{"role": "user", "content": "q"}], tools=[{"type": "function"}])
        assert out["content"] == "yo"

    def test_stream_yields_tokens(self, monkeypatch):
        prov = self._client()
        events = [
            {"choices": [{"delta": {"content": "hi"}}]},
            {"choices": [{"delta": {"content": "!"}}]},
        ]

        def fake_sse(url, payload, headers):
            assert payload["stream"] is True
            yield from [e["choices"][0]["delta"]["content"] for e in events]

        monkeypatch.setattr(P, "_sse_events", fake_sse)
        assert list(prov.stream([{"role": "user", "content": "q"}])) == ["hi", "!"]

    def test_azure_chat_url(self):
        prov = make_provider("azure", dict(PROVIDERS["azure"]),
                             api_key="k", )
        url = prov._chat_url("gpt-4o")
        assert "deployments/gpt-4o/chat/completions" in url
        assert "api-version=2024-06-01" in url

    def test_azure_uses_api_key_header(self):
        prov = make_provider("azure", dict(PROVIDERS["azure"]), api_key="az")
        assert prov._headers() == {"Content-Type": "application/json", "api-key": "az"}


class TestAnthropicAdapter:
    def _client(self):
        return make_provider("anthropic", dict(PROVIDERS["anthropic"]), api_key="ak")

    def test_headers(self):
        h = self._client()._headers()
        assert h["x-api-key"] == "ak"
        assert "anthropic-version" in h

    def test_body_split_system(self):
        prov = self._client()
        body = prov._body([{"role": "system", "content": "sys"},
                           {"role": "user", "content": "q"}])
        assert body["system"] == "sys"
        assert body["max_tokens"] > 0
        assert body["messages"] == [{"role": "user",
                                     "content": [{"type": "text", "text": "q"}]}]

    def test_chat_converts_tool_use(self, monkeypatch):
        prov = self._client()

        def fake_json(url, payload=None, headers=None, timeout=None):
            assert payload["tools"] == [{"name": "grep", "description": "",
                                         "input_schema": {"type": "object"}}]
            return {"content": [
                {"type": "tool_use", "id": "tu1", "name": "grep", "input": {"q": "x"}},
            ], "stop_reason": "tool_use"}

        monkeypatch.setattr(P, "_http_json", fake_json)
        out = prov.chat([{"role": "user", "content": "find"},
                         {"role": "assistant", "content": "", "tool_calls": [
                             {"id": "c1", "function": {"name": "grep", "arguments": "{\"q\":\"x\"}"}}
                         ]}], tools=[{"type": "function",
                                      "function": {"name": "grep", "parameters": {"type": "object"}}}])
        assert out["content"] == ""
        assert out["tool_calls"][0]["function"]["name"] == "grep"


class TestConversion:
    def test_anthropic_image_blocks(self):
        system, msgs = _anthropic_convert_messages(
            [{"role": "user", "content": "look", "images": ["AAAA"]}])
        assert msgs[0]["content"][0]["type"] == "text"
        assert msgs[0]["content"][1]["source"]["media_type"] == "image/jpeg"

    def test_anthropic_openai_vision_parts(self):
        system, msgs = _anthropic_convert_messages([{"role": "user", "content": [
            {"type": "text", "text": "caption"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,BBBB"}},
        ]}])
        assert msgs[0]["content"][0] == {"type": "text", "text": "caption"}
        assert msgs[0]["content"][1]["source"]["media_type"] == "image/png"

    def test_anthropic_assistant_tool_turn_rebuilt(self):
        system, msgs = _anthropic_convert_messages([{
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "c1", "function": {"name": "read_file",
                                                     "arguments": json.dumps({"path": "a.py"})}}],
        }, {"role": "tool", "tool_call_id": "c1", "content": "contents"}])
        assert msgs[0]["role"] == "assistant"
        assert msgs[0]["content"][0]["type"] == "tool_use"
        assert msgs[0]["content"][0]["name"] == "read_file"
        assert msgs[0]["content"][0]["input"] == {"path": "a.py"}
        assert msgs[1]["role"] == "user"
        assert msgs[1]["content"][0]["type"] == "tool_result"
        assert msgs[1]["content"][0]["content"] == "contents"

    def test_anthropic_response_normalized(self):
        out = _anthropic_response({"content": [
            {"type": "text", "text": "a"},
            {"type": "tool_use", "id": "tu2", "name": "write_file", "input": {"path": "b"}},
        ], "stop_reason": "tool_use"})
        assert out["content"] == "a"
        assert out["finish_reason"] == "tool_calls"
        assert json.loads(out["tool_calls"][0]["function"]["arguments"]) == {"path": "b"}

    def test_anthropic_tools_from_openai_schema(self):
        tools = _anthropic_tools([{
            "type": "function",
            "function": {"name": "foo", "description": "d",
                         "parameters": {"type": "object", "properties": {}}},
        }])
        assert tools[0]["name"] == "foo"
        assert tools[0]["input_schema"] == {"type": "object", "properties": {}}

    def test_sse_token_openai_and_anthropic(self):
        assert _sse_token({"choices": [{"delta": {"content": "x"}}]}) == "x"
        assert _sse_token({"type": "content_block_delta",
                           "delta": {"type": "text_delta", "text": "y"}}) == "y"


class TestOllamaAdapter:
    def test_static_list_when_server_missing(self, monkeypatch):
        prov = make_provider("ollama", dict(PROVIDERS["ollama"]))

        def client():
            raise ProviderError("ollama not installed")

        monkeypatch.setattr(prov, "_make_client", client)
        assert prov.list_models() == PROVIDERS["ollama"]["models"]

    def test_factory_kind_dispatch(self):
        assert make_provider("ollama", dict(PROVIDERS["ollama"])).kind == "ollama"
        assert make_provider("anthropic", dict(PROVIDERS["anthropic"])).kind == "anthropic"
        assert make_provider("custom", dict(PROVIDERS["custom"])).kind == "openai_compat"