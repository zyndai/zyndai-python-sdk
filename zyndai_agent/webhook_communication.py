"""Removed in the A2A migration.

The legacy ``WebhookCommunicationManager`` (Flask-based ``/webhook``
endpoint with bespoke signed envelopes) was retired when the SDK moved
to the A2A wire protocol. Its replacement is ``zyndai_agent.a2a.server``,
which exposes JSON-RPC 2.0 over HTTPS with SSE streaming, push
notifications, and ``x-zynd-auth`` per-message Ed25519 signatures.

This stub remains so imports of ``zyndai_agent.webhook_communication``
don't fail at import time. Instantiating ``WebhookCommunicationManager``
raises a clear migration error.

Mirrors the legacy-removal pass done in the TS SDK.
"""

from __future__ import annotations

from typing import Any


_REMOVED_MSG = (
    "WebhookCommunicationManager has been removed. The SDK now ships only "
    "A2A transport (zyndai_agent.a2a.server / .client). Use ZyndAIAgent or "
    "ZyndService — they install the A2A blueprint automatically."
)


class WebhookCommunicationManager:  # noqa: D401 — legacy shim
    """Removed. The A2A server in ``zyndai_agent.a2a.server`` replaces this."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        raise NotImplementedError(_REMOVED_MSG)


__all__ = ["WebhookCommunicationManager"]
