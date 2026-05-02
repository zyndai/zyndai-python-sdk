"""A2A-shaped Agent Card builder.

Mirrors `zyndai-ts-sdk/src/a2a/card.ts`. Card is published at
`/.well-known/agent-card.json` and signed using a JWS-detached
signature over JCS-canonicalized bytes. Matches A2A spec which
references RFC 7515 + RFC 8785.
"""

import base64
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional, Type

from pydantic import BaseModel

from zyndai_agent.a2a.canonical import canonical_bytes
from zyndai_agent.ed25519_identity import Ed25519Keypair, sign as ed_sign


_DEFAULT_PROTOCOL_VERSION = "0.3.0"
_DEFAULT_A2A_PATH = "/a2a/v1"


@dataclass
class AgentCardSkill:
    id: str
    name: str
    description: Optional[str] = None
    tags: Optional[list[str]] = None
    examples: Optional[list[str]] = None
    inputModes: Optional[list[str]] = None
    outputModes: Optional[list[str]] = None


@dataclass
class AgentCardProvider:
    organization: str
    url: Optional[str] = None


@dataclass
class AgentCardCapabilities:
    streaming: Optional[bool] = None
    pushNotifications: Optional[bool] = None
    stateTransitionHistory: Optional[bool] = None


@dataclass
class AgentCardSecurityScheme:
    type: str
    scheme: Optional[str] = None
    bearerFormat: Optional[str] = None
    description: Optional[str] = None


@dataclass
class BuildCardOptions:
    name: str
    description: str
    version: str
    base_url: str
    keypair: Ed25519Keypair
    entity_id: str
    protocol_version: str = _DEFAULT_PROTOCOL_VERSION
    a2a_path: str = _DEFAULT_A2A_PATH
    provider: Optional[AgentCardProvider] = None
    icon_url: Optional[str] = None
    documentation_url: Optional[str] = None
    capabilities: Optional[AgentCardCapabilities] = None
    default_input_modes: Optional[list[str]] = None
    default_output_modes: Optional[list[str]] = None
    skills: Optional[list[AgentCardSkill]] = None
    security_schemes: Optional[dict[str, AgentCardSecurityScheme]] = None
    security: Optional[list[dict[str, list[str]]]] = None
    payload_model: Optional[Type[BaseModel]] = None
    output_model: Optional[Type[BaseModel]] = None
    fqan: Optional[str] = None
    registry: Optional[str] = None
    pricing: Optional[dict[str, Any]] = None
    trust_score: Optional[float] = None
    status: Optional[str] = None
    developer_proof: Optional[dict[str, Any]] = None
    category: Optional[str] = None
    tags: Optional[list[str]] = None
    summary: Optional[str] = None


def build_agent_card(opts: BuildCardOptions) -> dict[str, Any]:
    """Build a full signed A2A agent card dict."""
    base_url = opts.base_url.rstrip("/")
    a2a_url = f"{base_url}{opts.a2a_path}"

    # Default capabilities — we serve message/stream + push.
    caps_in = opts.capabilities or AgentCardCapabilities()
    capabilities = {
        "streaming": caps_in.streaming if caps_in.streaming is not None else True,
        "pushNotifications": (
            caps_in.pushNotifications if caps_in.pushNotifications is not None else True
        ),
        "stateTransitionHistory": (
            caps_in.stateTransitionHistory
            if caps_in.stateTransitionHistory is not None
            else False
        ),
    }

    # Schemas + default modes derived from payload models.
    schema_ad = _zod_like_advertisement(opts.payload_model, opts.output_model)
    accepts_files = bool(schema_ad.get("accepts_files"))

    default_input_modes = opts.default_input_modes or _derive_default_modes(
        "input", accepts_files
    )
    default_output_modes = opts.default_output_modes or _derive_default_modes(
        "output", accepts_files
    )

    # Default skill if none supplied.
    if opts.skills and len(opts.skills) > 0:
        skills = [
            {k: v for k, v in s.__dict__.items() if v is not None} for s in opts.skills
        ]
    else:
        default_skill: dict[str, Any] = {
            "id": "default",
            "name": opts.name,
            "description": opts.description,
            "inputModes": default_input_modes,
            "outputModes": default_output_modes,
        }
        if opts.tags:
            default_skill["tags"] = opts.tags
        skills = [default_skill]

    security_schemes = _security_schemes_to_dict(opts.security_schemes) or {
        "zyndSig": {
            "type": "http",
            "scheme": "ed25519-envelope",
            "description": (
                "Per-message Ed25519 signature in Message.metadata['x-zynd-auth']. "
                "See zynd-a2a-communication spec."
            ),
        }
    }
    security = opts.security or [{"zyndSig": []}]

    # x-zynd extension block.
    x_zynd: dict[str, Any] = {
        "version": 1,
        "entityId": opts.entity_id,
        "publicKey": opts.keypair.public_key_string,
        "status": opts.status or "online",
        "lastUpdatedAt": _now_iso(),
    }
    for k, v in (
        ("fqan", opts.fqan),
        ("registry", opts.registry),
        ("pricing", opts.pricing),
        ("trustScore", opts.trust_score),
        ("developerProof", opts.developer_proof),
        ("category", opts.category),
        ("tags", opts.tags),
        ("summary", opts.summary),
    ):
        if v is not None:
            x_zynd[k] = v
    if schema_ad.get("input_schema"):
        x_zynd["inputSchema"] = schema_ad["input_schema"]
    if schema_ad.get("output_schema"):
        x_zynd["outputSchema"] = schema_ad["output_schema"]
    if "accepts_files" in schema_ad:
        x_zynd["acceptsFiles"] = schema_ad["accepts_files"]

    unsigned: dict[str, Any] = {
        "protocolVersion": opts.protocol_version,
        "name": opts.name,
        "description": opts.description,
        "version": opts.version,
        "url": a2a_url,
        "preferredTransport": "JSONRPC",
        "capabilities": capabilities,
        "defaultInputModes": default_input_modes,
        "defaultOutputModes": default_output_modes,
        "skills": skills,
        "securitySchemes": security_schemes,
        "security": security,
        "x-zynd": x_zynd,
    }
    if opts.provider:
        unsigned["provider"] = {
            k: v for k, v in opts.provider.__dict__.items() if v is not None
        }
    if opts.icon_url:
        unsigned["iconUrl"] = opts.icon_url
    if opts.documentation_url:
        unsigned["documentationUrl"] = opts.documentation_url

    return sign_agent_card(unsigned, opts.keypair)


def sign_agent_card(card: dict[str, Any], keypair: Ed25519Keypair) -> dict[str, Any]:
    """Sign an unsigned card. Signature covers JCS-canonical bytes of
    the card with `signatures` omitted, formatted as a JWS-detached
    entry inside a `signatures` array.
    """
    stripped = {k: v for k, v in card.items() if k not in ("signatures", "signature")}

    protected_header = {"alg": "EdDSA", "typ": "agent-card+jcs+jws"}
    protected_b64 = _b64url_json(protected_header)
    payload_bytes = canonical_bytes(stripped)

    sig_input = (
        protected_b64.encode("ascii") + b"." + payload_bytes
    )

    signature = ed_sign(keypair.private_key, sig_input)
    raw_sig = (
        signature[len("ed25519:") :] if signature.startswith("ed25519:") else signature
    )
    sig_b64url = _b64_to_b64url(raw_sig)

    return {
        **card,
        "signatures": [
            {
                "protected": protected_b64,
                "signature": sig_b64url,
                "header": {"kid": keypair.public_key_string},
            }
        ],
    }


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------


def _b64url_json(obj: Any) -> str:
    return _b64_to_b64url(
        base64.b64encode(json.dumps(obj, ensure_ascii=False).encode("utf-8")).decode("ascii")
    )


def _b64_to_b64url(b64: str) -> str:
    return b64.rstrip("=").replace("+", "-").replace("/", "_")


def _security_schemes_to_dict(
    schemes: Optional[dict[str, AgentCardSecurityScheme]],
) -> Optional[dict[str, dict[str, Any]]]:
    if not schemes:
        return None
    return {
        k: {kk: vv for kk, vv in s.__dict__.items() if vv is not None}
        for k, s in schemes.items()
    }


def _derive_default_modes(side: str, accepts_files: bool) -> list[str]:
    out = ["text/plain", "application/json"]
    if side == "input" and accepts_files:
        out.append("multipart/form-data")
    return out


def _zod_like_advertisement(
    payload_model: Optional[Type[BaseModel]],
    output_model: Optional[Type[BaseModel]],
) -> dict[str, Any]:
    """Build the input_schema/output_schema/accepts_files block from
    Pydantic models. Equivalent to `zodSchemaAdvertisement` on TS side
    but uses Pydantic's model_json_schema().
    """
    ad: dict[str, Any] = {}
    if payload_model is not None:
        try:
            ad["input_schema"] = payload_model.model_json_schema()
        except Exception:
            pass
    if output_model is not None:
        try:
            ad["output_schema"] = output_model.model_json_schema()
        except Exception:
            pass
    if payload_model is not None and _detects_attachments(payload_model):
        ad["accepts_files"] = True
    return ad


def _detects_attachments(model: Type[BaseModel]) -> bool:
    """Walk model fields and report if any is a list of Attachment-like
    objects (filename + mime_type + (data|url)). Mirrors the TS
    detectsAttachments helper.
    """
    try:
        schema = model.model_json_schema()
    except Exception:
        return False
    return _scan_for_attachment_array(schema)


def _scan_for_attachment_array(node: Any) -> bool:
    if isinstance(node, dict):
        if node.get("type") == "array":
            items = node.get("items")
            if isinstance(items, dict):
                # Resolve $ref if present.
                props = items.get("properties") or {}
                keys = set(props.keys())
                if (
                    "filename" in keys
                    and "mime_type" in keys
                    and ("data" in keys or "url" in keys)
                ):
                    return True
        for v in node.values():
            if _scan_for_attachment_array(v):
                return True
    elif isinstance(node, list):
        for item in node:
            if _scan_for_attachment_array(item):
                return True
    return False


def _now_iso() -> str:
    return (
        datetime.now(tz=timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )
