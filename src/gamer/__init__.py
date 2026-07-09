"""gamer — Steam-watching streamer recommendation service.

Modular monolith. Internal boundaries (see PLAN.md §4):
  sources/    — upstream adapters emitting normalized RawEvents
  catalog/    — platform-agnostic game registry
  signals/    — time-series metrics per game
  enrichment/ — embeddings, dedup, optional LLM summaries
  scoring/    — transparent weighted recommender
  notify/     — transport abstraction (Telegram first)
  bot/        — aiogram command surface
"""

__version__ = "0.1.0"
