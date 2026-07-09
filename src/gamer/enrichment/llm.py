"""Optional local-LLM summaries for the digest (PLAN.md §4.4, M4).

The digest is perfectly usable without an LLM. When enabled, a local model turns
the day's top picks into a short, human-sounding blurb that is prepended to the
notification — "digest reads like a human wrote it".

Two backends are supported via ``settings.llm.api``: Ollama's native
``/api/generate`` (default) and any OpenAI-compatible ``/chat/completions`` server
(llama.cpp, vLLM, LM Studio, …).

Everything here is **feature-flagged and fail-open**: :meth:`LLMSummarizer.summarize_digest`
returns ``None`` when ``settings.llm.enabled`` is False *or* on any error (network,
timeout, bad response). Callers treat ``None`` as "no blurb" and render the digest
exactly as they do today, so the system MUST work with the LLM off or unreachable.

The prompt builder (:func:`build_summary_prompt`) is pure — no network — so it is
unit-testable in isolation.
"""

from __future__ import annotations

from typing import Any

from gamer.config import LLMSettings, get_settings
from gamer.logging import get_logger
from gamer.sources.http import PoliteClient

log = get_logger("enrichment.llm")

_SYSTEM_PREAMBLE = (
    "You are the voice of a gaming-stream advisor writing a daily digest for a "
    "Twitch streamer. Given the ranked picks below, write ONE short, punchy, "
    "human-sounding blurb (1-2 sentences, no more than ~40 words) that sets up the "
    "day's recommendations. Be warm and confident, never robotic. Do not use lists, "
    "markdown, hashtags, or emoji. Return only the blurb text."
)


def build_summary_prompt(items: list[str]) -> str:
    """Turn the day's pick/news lines into an Ollama prompt string.

    Pure and network-free so it can be unit-tested directly. Empty input still
    yields a valid prompt (the model is asked to write a gentle "quiet day" line).
    """
    if items:
        bullet_lines = "\n".join(f"- {item}" for item in items)
    else:
        bullet_lines = "- (no standout picks today)"
    return f"{_SYSTEM_PREAMBLE}\n\nToday's picks:\n{bullet_lines}\n\nBlurb:"


class LLMSummarizer:
    """Feature-flagged Ollama client that summarizes the digest into a blurb."""

    def __init__(self, settings: LLMSettings | None = None) -> None:
        self._settings = settings or get_settings().llm

    @property
    def enabled(self) -> bool:
        return self._settings.enabled

    async def summarize_digest(self, items: list[str]) -> str | None:
        """Produce a short human-sounding blurb for ``items``.

        Returns ``None`` when the LLM is disabled or on ANY error — the digest then
        renders exactly as it does without the LLM (fail-open, never raises).
        """
        if not self._settings.enabled:
            return None
        prompt = build_summary_prompt(items)
        try:
            if self._settings.api == "openai":
                return await self._generate_openai(prompt)
            return await self._generate_ollama(prompt)
        except Exception as exc:
            log.warning("llm_summary_failed", error=str(exc), model=self._settings.model)
            return None

    async def _generate_ollama(self, prompt: str) -> str | None:
        """POST to Ollama's ``/api/generate`` and return the reply text."""
        url = f"{self._settings.ollama_url.rstrip('/')}/api/generate"
        payload = {
            "model": self._settings.model,
            "prompt": prompt,
            "stream": False,
        }
        async with PoliteClient(rate=4, per=1.0, timeout=60.0) as client:
            resp = await client.request("POST", url, json=payload)
            resp.raise_for_status()
            data: Any = resp.json()
        text = data.get("response") if isinstance(data, dict) else None
        return self._clean(text, keys=data)

    async def _generate_openai(self, prompt: str) -> str | None:
        """POST to an OpenAI-compatible ``/chat/completions`` (llama.cpp, vLLM…)."""
        url = f"{self._settings.openai_base_url.rstrip('/')}/chat/completions"
        headers: dict[str, str] = {}
        key = self._settings.openai_api_key.get_secret_value()
        if key:
            headers["Authorization"] = f"Bearer {key}"
        payload = {
            "model": self._settings.model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "temperature": 0.7,
        }
        async with PoliteClient(rate=4, per=1.0, timeout=60.0) as client:
            resp = await client.request("POST", url, json=payload, headers=headers)
            resp.raise_for_status()
            data: Any = resp.json()
        text: Any = None
        if isinstance(data, dict):
            choices = data.get("choices")
            if isinstance(choices, list) and choices and isinstance(choices[0], dict):
                message = choices[0].get("message")
                if isinstance(message, dict):
                    text = message.get("content")
        return self._clean(text, keys=data)

    def _clean(self, text: Any, *, keys: Any) -> str | None:
        """Strip and validate the model's reply; ``None`` if empty/malformed."""
        if not isinstance(text, str):
            log.warning(
                "llm_summary_no_response",
                keys=list(keys) if isinstance(keys, dict) else None,
            )
            return None
        blurb = text.strip()
        return blurb or None
