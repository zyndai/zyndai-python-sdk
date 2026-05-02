"""Removed in the A2A migration.

The pre-A2A agent card builder lived here. It has been replaced by
``zyndai_agent.a2a.card.build_agent_card`` (A2A-shaped, JCS+JWS-signed)
plus the runtime helpers in ``zyndai_agent.entity_card_loader``
(``build_runtime_card``, ``compute_card_hash``, ``resolve_card_from_config``).

This stub remains so imports of ``zyndai_agent.entity_card`` don't fail
at import time. Calling any of the legacy builders below raises a clear
migration error.

Mirrors the legacy-removal pass done in the TS SDK.
"""

from __future__ import annotations

from typing import Any


_REMOVED_MSG = (
    "{name}() has been removed. Use zyndai_agent.a2a.card.build_agent_card "
    "(A2A-shaped, JCS+JWS-signed) or zyndai_agent.entity_card_loader."
    "build_runtime_card() instead."
)


def build_entity_card(*args: Any, **kwargs: Any) -> Any:
    raise NotImplementedError(_REMOVED_MSG.format(name="build_entity_card"))


def sign_entity_card(*args: Any, **kwargs: Any) -> Any:
    raise NotImplementedError(_REMOVED_MSG.format(name="sign_entity_card"))


def build_endpoints(*args: Any, **kwargs: Any) -> Any:
    raise NotImplementedError(_REMOVED_MSG.format(name="build_endpoints"))


__all__ = ["build_entity_card", "sign_entity_card", "build_endpoints"]
