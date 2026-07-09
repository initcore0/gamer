"""redact_secrets must strip credential query params from error strings/URLs."""

from __future__ import annotations

from gamer.logging import redact_secrets


def test_redacts_steam_key_in_httpx_style_error() -> None:
    msg = (
        "Client error '403 Forbidden' for url "
        "'https://api.steampowered.com/x/v1/?appid=570&key=FAKEFAKE0000FAKE'"
    )
    out = redact_secrets(msg)
    assert "FAKEFAKE0000FAKE" not in out
    assert "key=***" in out
    assert "appid=570" in out  # non-secret params untouched


def test_redacts_common_secret_params_case_insensitive() -> None:
    msg = "token=aaa&API_KEY=bbb&client_secret=ccc&access_token=ddd"
    out = redact_secrets(msg)
    for leaked in ("aaa", "bbb", "ccc", "ddd"):
        assert leaked not in out


def test_plain_text_unchanged() -> None:
    msg = "TimeoutError: timed out after 20s"
    assert redact_secrets(msg) == msg
