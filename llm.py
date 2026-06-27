"""
Thin Anthropic (Claude) client built on the stdlib only (urllib) so the bot has
no hard dependency on the anthropic SDK. Deterministic by design: temperature=0.

If ANTHROPIC_API_KEY is not set, `LLMClient.available` is False and callers
fall back to the deterministic template composer in composer.py.
"""
from __future__ import annotations

import json
import os
import urllib.request
import urllib.error

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"


class LLMClient:
    def __init__(self) -> None:
        self.api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
        # Sonnet is the default: frontier quality, fast enough for the 15-30s
        # per-call budget. Override with COMPOSER_MODEL if you want Opus/Haiku.
        self.model = os.getenv("COMPOSER_MODEL", "claude-sonnet-4-6").strip()
        self.timeout = float(os.getenv("LLM_TIMEOUT_SECONDS", "12"))

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def complete_json(self, system: str, user: str, max_tokens: int = 900) -> dict | None:
        """Call Claude and parse a single JSON object from the response.

        Returns None on any error so the caller can fall back. temperature=0
        keeps composition deterministic for the same inputs (challenge rule).
        """
        if not self.available:
            return None

        body = json.dumps({
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": 0,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }).encode("utf-8")

        req = urllib.request.Request(
            ANTHROPIC_URL,
            data=body,
            method="POST",
            headers={
                "content-type": "application/json",
                "x-api-key": self.api_key,
                "anthropic-version": ANTHROPIC_VERSION,
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError):
            return None
        except Exception:
            return None

        try:
            text = "".join(
                block.get("text", "")
                for block in data.get("content", [])
                if block.get("type") == "text"
            )
        except Exception:
            return None

        return _extract_json(text)


def _extract_json(text: str) -> dict | None:
    if not text:
        return None
    # Strip ```json fences if present, then find the first balanced object.
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                candidate = text[start:i + 1]
                try:
                    return json.loads(candidate)
                except json.JSONDecodeError:
                    return None
    return None
