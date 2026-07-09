from __future__ import annotations

import httpx
import respx

from gamer.config import LLMSettings
from gamer.enrichment.llm import LLMSummarizer, build_summary_prompt

_OLLAMA = "http://localhost:11434"
_GENERATE = f"{_OLLAMA}/api/generate"


def _enabled() -> LLMSettings:
    return LLMSettings(enabled=True, ollama_url=_OLLAMA, model="llama3.1:8b")


# ── pure prompt builder (no network) ─────────────────────────────────────────


def test_prompt_builder_includes_items() -> None:
    prompt = build_summary_prompt(["Hades II", "Celeste"])
    assert "- Hades II" in prompt
    assert "- Celeste" in prompt
    assert prompt.rstrip().endswith("Blurb:")


def test_prompt_builder_handles_empty() -> None:
    prompt = build_summary_prompt([])
    # Still a valid, non-empty prompt with a graceful "no picks" line.
    assert "no standout picks" in prompt
    assert "Blurb:" in prompt


# ── summarize_digest (mocked Ollama HTTP) ────────────────────────────────────


@respx.mock
async def test_summarize_returns_blurb() -> None:
    route = respx.post(_GENERATE).mock(
        return_value=httpx.Response(200, json={"response": "  Big day for roguelikes.  "})
    )
    out = await LLMSummarizer(_enabled()).summarize_digest(["Hades II"])
    assert out == "Big day for roguelikes."  # stripped
    assert route.called
    # The prompt actually carried the item through to Ollama.
    sent = route.calls.last.request
    assert b"Hades II" in sent.content


async def test_summarize_disabled_returns_none() -> None:
    settings = LLMSettings(enabled=False, ollama_url=_OLLAMA, model="llama3.1:8b")
    # No respx mock registered — if it tried the network it would raise, proving
    # the disabled flag short-circuits before any HTTP.
    out = await LLMSummarizer(settings).summarize_digest(["Hades II"])
    assert out is None


@respx.mock
async def test_summarize_http_error_returns_none() -> None:
    # 400 is non-retryable → raise_for_status raises immediately (fast, no backoff).
    respx.post(_GENERATE).mock(return_value=httpx.Response(400, json={"error": "boom"}))
    out = await LLMSummarizer(_enabled()).summarize_digest(["Hades II"])
    assert out is None  # fail-open on error


@respx.mock
async def test_summarize_missing_response_field_returns_none() -> None:
    respx.post(_GENERATE).mock(return_value=httpx.Response(200, json={"unexpected": "shape"}))
    out = await LLMSummarizer(_enabled()).summarize_digest(["Hades II"])
    assert out is None


@respx.mock
async def test_summarize_blank_response_returns_none() -> None:
    respx.post(_GENERATE).mock(return_value=httpx.Response(200, json={"response": "   "}))
    out = await LLMSummarizer(_enabled()).summarize_digest(["Hades II"])
    assert out is None


# ── OpenAI-compatible backend (llama.cpp / vLLM) ─────────────────────────────

_OPENAI_BASE = "http://llm.test/v1"
_CHAT = f"{_OPENAI_BASE}/chat/completions"


def _openai_enabled(*, key: str = "") -> LLMSettings:
    return LLMSettings(
        enabled=True,
        api="openai",
        openai_base_url=_OPENAI_BASE,
        openai_api_key=key,
        model="Qwen3.6-27B",
    )


@respx.mock
async def test_openai_backend_returns_blurb() -> None:
    route = respx.post(_CHAT).mock(
        return_value=httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "content": "  Roguelike day.  "}}]},
        )
    )
    out = await LLMSummarizer(_openai_enabled()).summarize_digest(["Hades II"])
    assert out == "Roguelike day."  # stripped
    sent = route.calls.last.request
    assert b"Hades II" in sent.content  # prompt carried through in the messages
    assert b"messages" in sent.content  # chat-completions shape, not /api/generate


@respx.mock
async def test_openai_backend_sends_bearer_when_key_set() -> None:
    route = respx.post(_CHAT).mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "hi"}}]})
    )
    await LLMSummarizer(_openai_enabled(key="secret-key")).summarize_digest(["X"])
    assert route.calls.last.request.headers.get("Authorization") == "Bearer secret-key"


@respx.mock
async def test_openai_backend_no_auth_header_without_key() -> None:
    route = respx.post(_CHAT).mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "hi"}}]})
    )
    await LLMSummarizer(_openai_enabled()).summarize_digest(["X"])
    assert "Authorization" not in route.calls.last.request.headers


@respx.mock
async def test_openai_backend_malformed_response_returns_none() -> None:
    respx.post(_CHAT).mock(return_value=httpx.Response(200, json={"choices": []}))
    out = await LLMSummarizer(_openai_enabled()).summarize_digest(["X"])
    assert out is None


@respx.mock
async def test_openai_backend_http_error_returns_none() -> None:
    respx.post(_CHAT).mock(return_value=httpx.Response(400))
    out = await LLMSummarizer(_openai_enabled()).summarize_digest(["X"])
    assert out is None
