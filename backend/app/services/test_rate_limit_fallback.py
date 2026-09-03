"""Rate-limit pacing + Ollama fallback health check.

Manual verification for the chunking change (needs a running backend, so it
isn't automated here):

    1. Delete the YouTube doc that previously indexed as ~60 chunks.
    2. Re-ingest the same URL with CHUNK_TOKEN_SIZE=1024.
    3. GET /api/v1/knowledge-base -> its chunk_count should be ~30, i.e. about
       half, and the ingest log should show no 429 retry warnings.
    4. Set OLLAMA_MODEL=does-not-exist and restart: the "is not pulled" warning
       must appear in the startup log, before any ingest runs.
"""

import asyncio
import time

import httpx

from app.services import lightrag_engine as le
from app.services.rate_limiter import TokenBucket, estimate_tokens


def test_bucket_paces_once_the_budget_is_spent():
    bucket = TokenBucket(tokens_per_minute=60)  # 1 token/sec

    async def run():
        await bucket.acquire(60)  # drains the full bucket instantly
        start = time.monotonic()
        await bucket.acquire(1)  # must wait ~1s for a refill
        return time.monotonic() - start

    waited = asyncio.run(run())
    assert 0.7 <= waited <= 2.0, waited


def test_oversized_request_drains_instead_of_hanging():
    bucket = TokenBucket(tokens_per_minute=100)
    asyncio.run(asyncio.wait_for(bucket.acquire(10_000), timeout=1))


def test_estimate_counts_prompt_and_response():
    assert estimate_tokens("x" * 400, "y" * 400, output_allowance=0) == 200


def _groq_429(message: str) -> httpx.HTTPStatusError:
    from openai import RateLimitError

    request = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    return RateLimitError(message, response=httpx.Response(429, request=request), body=None)


def test_429_body_resizes_the_budget_and_sets_the_wait():
    err = _groq_429(
        "Error code: 429 - Rate limit reached for model `llama-3.1-8b-instant` on "
        "tokens per minute (TPM): Limit 6000, Used 3286, Requested 2757. "
        "Please try again in 430ms."
    )
    le._extract_budget.resize(12000)

    le._learn_tpm_limit(err)

    assert le._extract_budget.capacity == 6000
    assert le._retry_after_seconds(err, default=20.0) == 0.43
    assert le._retry_after_seconds(_groq_429("try again in 18.8s"), 20.0) == 18.8
    assert le._retry_after_seconds(_groq_429("no hint here"), 20.0) == 20.0


def _fake_tags(monkeypatch, models):
    async def fake_get(self, url, **kwargs):
        return httpx.Response(
            200,
            json={"models": [{"name": m} for m in models]},
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)


def test_missing_ollama_model_is_caught_at_startup(monkeypatch, caplog):
    _fake_tags(monkeypatch, ["mistral:latest"])
    monkeypatch.setattr(le._settings, "ollama_model", "does-not-exist")

    assert asyncio.run(le.check_ollama_fallback()) is False
    assert "is not pulled" in caplog.text


def test_pulled_model_matches_despite_latest_tag(monkeypatch):
    _fake_tags(monkeypatch, ["llama3.2:latest"])
    monkeypatch.setattr(le._settings, "ollama_model", "llama3.2")

    assert asyncio.run(le.check_ollama_fallback()) is True


def test_unreachable_ollama_warns_instead_of_raising(monkeypatch, caplog):
    async def boom(self, url, **kwargs):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx.AsyncClient, "get", boom)

    assert asyncio.run(le.check_ollama_fallback()) is False
    assert "unreachable" in caplog.text


def _fake_models(monkeypatch, models):
    async def fake_get(self, url, **kwargs):
        return httpx.Response(
            200,
            json={"data": [{"id": m} for m in models]},
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)


def _heavy_settings(monkeypatch, key="sk-test", model="gpt-5"):
    monkeypatch.setattr(le._settings, "heavy_api_key", key)
    monkeypatch.setattr(le._settings, "heavy_model", model)


def test_heavy_provider_is_off_until_both_key_and_model_are_set(monkeypatch):
    _heavy_settings(monkeypatch, key="sk-test", model="")
    assert le.heavy_configured() is False
    assert le.heavy_budget() is None
    assert asyncio.run(le.check_heavy_provider()) is False

    _heavy_settings(monkeypatch)
    assert le.heavy_configured() is True
    assert le.heavy_budget() == le._settings.heavy_context_token_budget


def test_unknown_heavy_model_is_caught_at_startup(monkeypatch, caplog):
    _heavy_settings(monkeypatch, model="does-not-exist")
    _fake_models(monkeypatch, ["gpt-5", "claude-sonnet-4"])

    assert asyncio.run(le.check_heavy_provider()) is False
    assert "is not in this account's model list" in caplog.text


def test_reachable_heavy_provider_reports_ready(monkeypatch):
    _heavy_settings(monkeypatch)
    _fake_models(monkeypatch, ["gpt-5"])

    assert asyncio.run(le.check_heavy_provider()) is True


def test_rejected_heavy_key_warns_instead_of_raising(monkeypatch, caplog):
    """New API answers a bad key with 401, not a connection error."""
    _heavy_settings(monkeypatch)

    async def unauthorized(self, url, **kwargs):
        return httpx.Response(401, json={"error": {"message": "Invalid token"}},
                              request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.AsyncClient, "get", unauthorized)

    assert asyncio.run(le.check_heavy_provider()) is False
    assert "rejected the key or is unreachable" in caplog.text


def test_heavy_mode_is_scoped_to_its_block(monkeypatch):
    _heavy_settings(monkeypatch)
    assert le._heavy.get() is False
    with le.heavy():
        assert le._heavy.get() is True
    assert le._heavy.get() is False
