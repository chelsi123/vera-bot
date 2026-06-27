"""
In-memory, versioned context store + conversation state.

Context store is idempotent on (scope, context_id, version): a re-post of the
same version is a no-op, a higher version replaces atomically, a lower version
is rejected as stale (the API layer turns this into the 409 contract).
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Conversation:
    conversation_id: str
    merchant_id: str | None = None
    customer_id: str | None = None
    trigger_id: str | None = None
    sent_bodies: list[str] = field(default_factory=list)  # anti-repetition
    turns: list[dict] = field(default_factory=list)
    ended: bool = False
    in_action_mode: bool = False  # flipped once merchant commits intent


class Store:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        # (scope, context_id) -> {"version": int, "payload": dict}
        self.contexts: dict[tuple[str, str], dict] = {}
        self.conversations: dict[str, Conversation] = {}
        # Dedup: suppression_keys we've already acted on.
        self.used_suppression_keys: set[str] = set()
        # Per-merchant auto-reply / opt-out tracking across conversations.
        self.merchant_auto_reply_count: dict[str, int] = {}
        self.opted_out_merchants: set[str] = set()

    # ---- context -------------------------------------------------------
    def put_context(self, scope: str, context_id: str, version: int, payload: dict) -> tuple[bool, str | None, int]:
        """Returns (accepted, reason, current_version)."""
        with self._lock:
            key = (scope, context_id)
            cur = self.contexts.get(key)
            if cur is not None:
                if version == cur["version"]:
                    # Idempotent re-post of same version -> no-op success.
                    return True, "idempotent", cur["version"]
                if version < cur["version"]:
                    return False, "stale_version", cur["version"]
            self.contexts[key] = {"version": version, "payload": payload}
            return True, None, version

    def get(self, scope: str, context_id: str | None) -> dict | None:
        if context_id is None:
            return None
        with self._lock:
            entry = self.contexts.get((scope, context_id))
            return entry["payload"] if entry else None

    def version_of(self, scope: str, context_id: str | None) -> int:
        if context_id is None:
            return 0
        with self._lock:
            entry = self.contexts.get((scope, context_id))
            return entry["version"] if entry else 0

    def counts(self) -> dict[str, int]:
        counts = {"category": 0, "merchant": 0, "customer": 0, "trigger": 0}
        with self._lock:
            for (scope, _), _ in self.contexts.items():
                counts[scope] = counts.get(scope, 0) + 1
        return counts

    # ---- conversations -------------------------------------------------
    def get_or_create_conversation(self, conv_id: str, **kw: Any) -> Conversation:
        with self._lock:
            conv = self.conversations.get(conv_id)
            if conv is None:
                conv = Conversation(conversation_id=conv_id)
                self.conversations[conv_id] = conv
            for k, v in kw.items():
                if v is not None and getattr(conv, k, None) in (None, "", []):
                    setattr(conv, k, v)
            return conv

    def get_conversation(self, conv_id: str) -> Conversation | None:
        with self._lock:
            return self.conversations.get(conv_id)

    def has_seen_suppression(self, key: str | None) -> bool:
        if not key:
            return False
        with self._lock:
            return key in self.used_suppression_keys

    def mark_suppression(self, key: str | None) -> None:
        if not key:
            return
        with self._lock:
            self.used_suppression_keys.add(key)

    def reset(self) -> None:
        with self._lock:
            self.contexts.clear()
            self.conversations.clear()
            self.used_suppression_keys.clear()
            self.merchant_auto_reply_count.clear()
            self.opted_out_merchants.clear()
