"""LLM provider abstraction (Phase 7.15). No hard-coded vendor.

`LLMProvider`: generate_structured(messages, schema_hint) -> dict,
model_name/model_version/health_check. `OpenAICompatibleProvider` talks to
any OpenAI-style /chat/completions endpoint (local Ollama included) via
stdlib urllib — no new dependencies, no SDK lock-in, keys only from env.
`FakeProvider` is deterministic and TEST-ONLY (also the explicit fallback
name when no provider is configured? No — unconfigured means UNAVAILABLE,
never fake output; see service.py).
"""
from __future__ import annotations

import json
import os
import time
import urllib.request
from typing import Protocol


class LLMUnavailableError(Exception):
    """Provider unreachable, misconfigured, timed out, or returned garbage."""


class LLMProvider(Protocol):
    model_name: str
    model_version: str

    def generate_structured(self, messages: list[dict], timeout_s: int) -> dict:
        """Return the parsed JSON object. Raise LLMUnavailableError on failure."""
        ...

    def health_check(self) -> bool:
        ...


def _timeout() -> int:
    from app.core.config import get_settings

    try:
        return int(os.environ.get("LLM_TIMEOUT_S") or get_settings().LLM_TIMEOUT_S)
    except Exception:
        return 90


class OpenAICompatibleProvider:
    """Any {base_url}/chat/completions endpoint with JSON mode."""

    def __init__(self, base_url: str | None = None, api_key: str | None = None,
                 model: str | None = None):
        from app.core.config import get_settings

        s = get_settings()
        self.base_url = (base_url or os.environ.get("LLM_BASE_URL") or s.LLM_BASE_URL).rstrip("/")
        self.api_key = api_key or os.environ.get("LLM_API_KEY") or s.LLM_API_KEY or ""
        self.model_name = model or os.environ.get("LLM_MODEL") or s.LLM_MODEL
        self.model_version = self.model_name  # provider models are versioned by name

    def _post(self, payload: dict, timeout_s: int) -> dict:
        url = f"{self.base_url}/chat/completions"
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                     headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                return json.load(resp)
        except Exception as e:
            raise LLMUnavailableError(f"LLM request failed ({url}): {e}") from e

    def generate_structured(self, messages: list[dict], timeout_s: int = 0) -> dict:
        timeout_s = timeout_s or _timeout()
        try:
            data = self._post({"model": self.model_name, "messages": messages,
                               "temperature": 0.0, "response_format": {"type": "json_object"}},
                              timeout_s)
            content = data["choices"][0]["message"]["content"]
            parsed = json.loads(content) if isinstance(content, str) else content
            if not isinstance(parsed, dict):
                raise ValueError("top-level JSON is not an object")
            return parsed
        except LLMUnavailableError:
            raise
        except Exception as e:
            raise LLMUnavailableError(f"LLM returned unusable output: {e}") from e

    def health_check(self) -> bool:
        try:
            self._post({"model": self.model_name,
                        "messages": [{"role": "user", "content": "ping"}],
                        "max_tokens": 1}, 10)
            return True
        except Exception:
            return False


class FakeProvider:
    """Deterministic canned responses for tests/eval. NEVER a production path."""

    model_name = "fake-test-llm"
    model_version = "fake-v0"

    def __init__(self, mode: str = "good"):
        self.mode = mode  # good|nested|malformed|bad_citation|unsupported_claim|down

    def health_check(self) -> bool:
        return self.mode != "down"

    def generate_structured(self, messages: list[dict], timeout_s: int = 0) -> dict:
        if self.mode == "down":
            raise LLMUnavailableError("Fake provider is down (test mode).")
        if self.mode == "malformed":
            raise LLMUnavailableError("LLM returned unusable output: not JSON.")
        if self.mode == "bad_citation":
            return {"summary": "s", "issue": "i", "why_it_matters": "w",
                    "evidence_summary": "e [99]", "policy_comparison": "p",
                    "template_comparison": "t", "historical_context": "h",
                    "recommendation": "r", "limitations": "l",
                    "source_references": [{"source_index": 99, "quote": "x"}]}
        if self.mode == "unsupported_claim":
            return {"summary": "This contract is illegal and violates the law.",
                    "issue": "i", "why_it_matters": "w", "evidence_summary": "e [1]",
                    "policy_comparison": "p", "template_comparison": "t",
                    "historical_context": "h", "recommendation": "r", "limitations": "l",
                    "source_references": [{"source_index": 1, "quote": "y"}]}
        # good: quotes a real substring of source [1] (echo-aware, deterministic)
        quote = _first_phrase(messages)
        base = {"summary": "The flagged clause may warrant legal review.",
                "issue": "Potential exposure identified by rule.",
                "why_it_matters": "The available evidence indicates added exposure.",
                "policy_comparison": "Appears inconsistent with the configured company policy.",
                "template_comparison": "The clause differs from the approved template.",
                "historical_context": "Similar historical clauses exist.",
                "recommendation": "Have counsel review before signing.",
                "limitations": "Explanation only; not legal advice.",
                "source_references": [{"source_index": 1, "quote": quote}]}
        if self.mode == "nested":  # real models sometimes nest sections: must coerce
            base["evidence_summary"] = {"quote": quote, "note": "see [1]"}
            base["policy_comparison"] = {"verdict": base["policy_comparison"]}
        else:
            base["evidence_summary"] = f"The contract clause [1] states {quote!r}."
        return base


def _first_phrase(messages: list[dict]) -> str:
    """First ~60 chars of source [1]'s body within EVIDENCE SOURCES
    (exact substring, test echo)."""
    import re

    blob = " ".join(m.get("content", "") for m in messages if isinstance(m, dict))
    m = re.search(r"EVIDENCE SOURCES:\s*\[1\][^\n]*\n(.{12,200})", blob)
    return (m.group(1).strip()[:60] if m else "contract clause term")


_PROVIDER: LLMProvider | None = None
_PROVIDER_KEY: str | None = None


def get_llm_provider() -> LLMProvider:
    """Factory from env/config. LLM_PROVIDER=fake is honoured ONLY when
    explicitly set (tests); default is the OpenAI-compatible provider."""
    global _PROVIDER, _PROVIDER_KEY
    which = os.environ.get("LLM_PROVIDER", "openai-compatible")
    if _PROVIDER is None or _PROVIDER_KEY != which:
        if which == "fake":
            _PROVIDER = FakeProvider(mode=os.environ.get("FAKE_LLM_MODE", "good"))
        else:
            _PROVIDER = OpenAICompatibleProvider()
        _PROVIDER_KEY = which
    return _PROVIDER


def reset_llm_provider() -> None:
    """Test hook."""
    global _PROVIDER, _PROVIDER_KEY
    _PROVIDER, _PROVIDER_KEY = None, None
