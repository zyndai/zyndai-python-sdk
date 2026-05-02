"""Removed in the A2A migration.

The MQTT-based ``AgentCommunicationManager`` was retired when the SDK moved
to the A2A wire protocol (see ``zyndai_agent.a2a``). This stub remains so
imports of ``zyndai_agent.communication`` don't blow up at import time,
but instantiating any of the symbols below raises a clear migration
error pointing callers to ``ZyndAIAgent``/``ZyndService``.

Mirrors the legacy-removal pass done in the TS SDK.
"""

from __future__ import annotations

from typing import Any


_REMOVED_MSG = (
    "AgentCommunicationManager has been removed. The SDK now ships only "
    "A2A transport — instantiate ZyndAIAgent or ZyndService instead. See "
    "zyndai_agent.a2a for the new types/server/client surfaces."
)


class AgentCommunicationManager:  # noqa: D401 — legacy shim
    """Removed. Use ``ZyndAIAgent`` / ``ZyndService`` (A2A) instead."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        raise NotImplementedError(_REMOVED_MSG)


# Historical alias from the MQTT era — kept so a `from zyndai_agent.communication
# import MQTTMessage` line in old code still resolves.
from zyndai_agent.message import AgentMessage as MQTTMessage  # noqa: E402

__all__ = ["AgentCommunicationManager", "MQTTMessage"]
