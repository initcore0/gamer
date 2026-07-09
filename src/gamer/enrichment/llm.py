"""Optional Ollama LLM summaries for the digest (PLAN.md §4.4, M4).

The digest is perfectly usable without an LLM. When enabled, a local Ollama model
(e.g. Llama 3.1 8B) turns the day's top picks into a short, human-sounding blurb
that is prepended to the notification — "digest reads like a human wrote it".

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
            return await self._generate(prompt)
        except Exception as exc:
            log.warning("llm_summary_failed", error=str(exc), model=self._settings.model)
            return None

    async def _generate(self, prompt: str) -> str | None:
        """POST the prompt to Ollama's ``/api/generate`` and return the reply text."""
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
        if not isinstance(text, str):
            log.warning(
                "llm_summary_no_response",
                keys=list(data) if isinstance(data, dict) else None,
            )
            return None
        blurb = text.strip()
        return blurb or None
