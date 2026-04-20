"""
Pydantic schema for agent request payloads.

AgentPayload is the default input contract for Zynd agents. Developers can
subclass it to declare typed fields (name, email, etc.) or required attachments;
the resulting JSON Schema is advertised at /.well-known/agent.json so callers
can discover what the agent accepts.

Backward compatibility: all fields have defaults, `extra="allow"` preserves
unknown keys, and the `prompt` alias continues to map onto `content`, so plain
`{"content": "hi"}` payloads parse unchanged.
"""

import base64
import binascii
from typing import Any, ClassVar, List, Optional, Type

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Attachment(BaseModel):
    """
    A file attached to an agent request. Handled entirely in memory by the
    SDK — nothing is persisted to disk.

    Exactly one of `data` or `url` must be set:

    - `data`: base64-encoded bytes, inline in the JSON payload
    - `url`: HTTP(S) URL the agent fetches into memory on demand
    """

    model_config = ConfigDict(extra="allow")

    filename: Optional[str] = None
    mime_type: Optional[str] = None
    size_bytes: Optional[int] = None
    data: Optional[str] = None
    url: Optional[str] = None

    @model_validator(mode="after")
    def _exactly_one_source(self) -> "Attachment":
        sources = [s for s in (self.data, self.url) if s is not None]
        if len(sources) == 0:
            raise ValueError("Attachment requires one of: data (base64) or url")
        if len(sources) > 1:
            raise ValueError("Attachment must specify only one of: data, url")
        return self

    def decode_data(self) -> bytes:
        """Return raw bytes for inline (base64) attachments."""
        if self.data is None:
            raise ValueError(
                "Attachment has no inline `data`; use the `url` source instead"
            )
        try:
            return base64.b64decode(self.data, validate=True)
        except (binascii.Error, ValueError) as e:
            raise ValueError(f"Invalid base64 in attachment `data`: {e}") from e

    def fetch_url(
        self,
        *,
        timeout: float = 30.0,
        max_size_bytes: Optional[int] = None,
        allowed_schemes: tuple = ("http", "https"),
    ) -> bytes:
        """Stream-download the URL contents and return them as bytes.

        Enforces scheme whitelist (to prevent file:// / gopher:// SSRF) and
        an optional size cap (checked via Content-Length up-front, then
        verified during chunked read in case the server lies).
        """
        if self.url is None:
            raise ValueError(
                "Attachment has no `url`; use decode_data() for inline `data` instead"
            )

        import requests
        from urllib.parse import urlparse

        scheme = (urlparse(self.url).scheme or "").lower()
        if scheme not in allowed_schemes:
            raise ValueError(
                f"URL scheme {scheme!r} not allowed; permitted: {allowed_schemes}"
            )

        with requests.get(self.url, stream=True, timeout=timeout) as resp:
            resp.raise_for_status()

            if max_size_bytes is not None:
                declared = resp.headers.get("Content-Length")
                if declared is not None and declared.isdigit() and int(declared) > max_size_bytes:
                    raise ValueError(
                        f"Remote file declares {declared} bytes, exceeds cap of {max_size_bytes}"
                    )

            chunks: list[bytes] = []
            running = 0
            for chunk in resp.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                running += len(chunk)
                if max_size_bytes is not None and running > max_size_bytes:
                    raise ValueError(
                        f"Download exceeded cap of {max_size_bytes} bytes before completion"
                    )
                chunks.append(chunk)
            return b"".join(chunks)


class AgentPayload(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    # Fields listed here are excluded from the advertised input_schema on
    # /.well-known/agent.json. They exist on the model for parsing and routing
    # but aren't things a caller needs to set — identity is handled by the
    # transport, and IDs are auto-generated.
    INTERNAL_FIELDS: ClassVar[frozenset] = frozenset({
        "sender_id",
        "sender_public_key",
        "sender_did",
        "receiver_id",
        "message_id",
        "conversation_id",
        "in_reply_to",
        "message_type",
    })

    content: str = ""
    sender_id: str = "unknown"
    sender_did: Optional[dict] = None
    sender_public_key: Optional[str] = None
    receiver_id: Optional[str] = None
    message_type: str = "query"
    message_id: Optional[str] = None
    conversation_id: Optional[str] = None
    in_reply_to: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    # No `attachments` field by default — opt in by declaring one in your
    # RequestPayload subclass (any name, any mime restriction). Agents
    # without such a declaration advertise `accepts_files: false`.

    @model_validator(mode="before")
    @classmethod
    def _prompt_aliases_content(cls, data: Any) -> Any:
        # `prompt` takes precedence over `content` for legacy callers that
        # still send the older field name.
        if isinstance(data, dict) and data.get("prompt") is not None:
            data = {**data, "content": data["prompt"]}
        return data


def _trim_schema(node: Any) -> Any:
    """Strip redundant Pydantic-generated fields from a JSON Schema dict.

    Reduces the advertised schema size ~4x without losing semantic info:
    - drops `title` (auto-derived from attribute name),
    - drops `default: null` (absence already signals "unprovided"),
    - drops `additionalProperties: true` (that's the JSON Schema default),
    - collapses `anyOf: [{type: X}, {type: "null"}]` into `type: [X, "null"]`,
    - shortens multi-line descriptions to their first line.
    """
    if isinstance(node, dict):
        node.pop("title", None)
        if "default" in node and node["default"] is None:
            del node["default"]
        if node.get("additionalProperties") is True:
            del node["additionalProperties"]

        any_of = node.get("anyOf")
        if isinstance(any_of, list) and len(any_of) == 2:
            type_pairs = [
                item.get("type") for item in any_of if isinstance(item, dict)
            ]
            non_null = [t for t in type_pairs if t and t != "null"]
            has_null = any(t == "null" for t in type_pairs)
            if has_null and len(non_null) == 1 and len(type_pairs) == 2:
                node["type"] = [non_null[0], "null"]
                del node["anyOf"]

        desc = node.get("description")
        if isinstance(desc, str) and "\n" in desc:
            node["description"] = desc.split("\n", 1)[0].strip()

        for v in list(node.values()):
            _trim_schema(v)
    elif isinstance(node, list):
        for item in node:
            _trim_schema(item)
    return node


def build_payload_card_fields(
    input_model: Type[AgentPayload],
    output_model: Optional[Type[BaseModel]] = None,
) -> dict:
    """
    Extract agent-card advertisement fields from a payload model.

    Produces `input_schema` (JSON Schema for incoming requests), and derives
    `accepts_files` / `accepted_mime_types` from the `attachments` field so
    callers can discover file support without reading the schema themselves.

    Developers can add a `json_schema_extra={"accepted_mime_types": [...]}`
    to their `attachments` field to publish a mime-type whitelist.
    """
    input_schema = input_model.model_json_schema()

    # Strip SDK-internal routing/identity fields so the advertised schema only
    # shows what a caller actually fills in. The full model is still used
    # unchanged for parsing/validation inside the agent.
    internal = getattr(input_model, "INTERNAL_FIELDS", frozenset())
    if internal:
        props = input_schema.get("properties") or {}
        input_schema["properties"] = {
            k: v for k, v in props.items() if k not in internal
        }
        required = input_schema.get("required") or []
        filtered_required = [k for k in required if k not in internal]
        if filtered_required:
            input_schema["required"] = filtered_required
        elif "required" in input_schema:
            del input_schema["required"]

    fields: dict = {"input_schema": input_schema}

    accepts_files = False
    mime_types: list[str] = []
    attachment_ref = "#/$defs/Attachment"

    for prop_schema in (input_schema.get("properties") or {}).values():
        if not isinstance(prop_schema, dict):
            continue
        # A field is an attachment carrier if it is either a direct ref to
        # Attachment or an array of Attachments.
        is_attachment = (
            prop_schema.get("$ref") == attachment_ref
            or (
                prop_schema.get("type") == "array"
                and isinstance(prop_schema.get("items"), dict)
                and prop_schema["items"].get("$ref") == attachment_ref
            )
        )
        if not is_attachment:
            continue
        accepts_files = True
        for m in prop_schema.get("accepted_mime_types") or []:
            if m not in mime_types:
                mime_types.append(m)

    fields["accepts_files"] = accepts_files
    # When the agent accepts files, it accepts them via multipart too — the
    # SDK's /webhook endpoint auto-handles both application/json (inline
    # base64) and multipart/form-data (raw binary parts), so callers can
    # pick whichever is efficient for their file size.
    if accepts_files:
        fields["accepts_multipart"] = True
    if mime_types:
        fields["accepted_mime_types"] = mime_types

    if output_model is not None:
        fields["output_schema"] = output_model.model_json_schema()

    # Trim AFTER mime-type extraction so the walker still sees the full
    # pydantic-generated shape.
    _trim_schema(fields["input_schema"])
    if "output_schema" in fields:
        _trim_schema(fields["output_schema"])

    return fields
