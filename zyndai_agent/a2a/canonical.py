"""Deterministic JSON canonicalization for cross-language signing.

Spec target: RFC 8785 JSON Canonicalization Scheme (JCS), the same
scheme the TypeScript SDK uses. Output of `canonical_json` MUST be
byte-identical to `canonicalJson` in `zyndai-ts-sdk/src/a2a/canonical.ts`
for any input that's representable in both implementations — that's the
foundation for cross-SDK signature verification.

Implementation notes:
- Object keys are sorted by Python's str sort, which uses Unicode
  code-point order. For the strings we sign (UUIDs, FQANs, ed25519:b64,
  ISO timestamps, ASCII keys) this matches TypeScript's UTF-16 code-unit
  order exactly.
- Strings are serialized via `json.dumps(s, ensure_ascii=False)` — same
  escaping the TypeScript JSON.stringify uses for the BMP subset.
- Numbers go through `json.dumps` which uses Python's repr — same shape
  as ECMAScript's ToString for finite values. -0 normalized to 0. NaN /
  Infinity rejected (not representable in JCS).
- No whitespace.

If we ever need to sign user-controlled strings with non-BMP characters
or unusual numeric forms, swap to a full RFC 8785 implementation. For the
Zynd envelope (no such fields by schema), this matches the TS SDK exactly.
"""

import json
import math
from typing import Any


def canonical_json(value: Any) -> str:
    """Produce the canonical UTF-8 string representation of a value.

    Returns a Python str whose UTF-8 encoding matches the TS SDK's
    `canonicalJson(...)` output byte-for-byte for the same input.
    """
    return _serialize(value)


def canonical_bytes(value: Any) -> bytes:
    """Convenience wrapper — returns the UTF-8 bytes most signing
    primitives want directly.
    """
    return canonical_json(value).encode("utf-8")


def _serialize(v: Any) -> str:
    if v is None:
        return "null"

    if isinstance(v, bool):
        # bool MUST be checked before int — bool is a subclass of int in Python.
        return "true" if v else "false"

    if isinstance(v, (int, float)):
        if isinstance(v, float):
            if not math.isfinite(v):
                raise ValueError(
                    f"canonical_json: non-finite number not representable: {v!r}"
                )
            # Normalize -0.0 → 0
            if v == 0.0 and math.copysign(1.0, v) < 0:
                return "0"
        # json.dumps preserves -0 in some Python versions; we already
        # collapsed it above. Use ensure_ascii=False for parity with
        # TS's JSON.stringify of numbers (which is just .toString()).
        return json.dumps(v, ensure_ascii=False)

    if isinstance(v, str):
        # ensure_ascii=False keeps unicode unescaped — matches what
        # TS's JSON.stringify does for BMP characters.
        return json.dumps(v, ensure_ascii=False)

    if isinstance(v, (list, tuple)):
        items = [_serialize(item) for item in v]
        return "[" + ",".join(items) + "]"

    if isinstance(v, dict):
        # Sort keys by str order. All keys must be strings to match
        # JSON object semantics; reject anything else early.
        for k in v.keys():
            if not isinstance(k, str):
                raise TypeError(
                    f"canonical_json: object keys must be strings, got {type(k).__name__}"
                )
        sorted_items = sorted(v.items(), key=lambda kv: kv[0])
        out = [
            json.dumps(k, ensure_ascii=False) + ":" + _serialize(val)
            for k, val in sorted_items
        ]
        return "{" + ",".join(out) + "}"

    # Pydantic models, dataclasses, etc. — caller should pre-serialize
    # to dict. We could try `getattr(v, "model_dump", None)` here, but
    # making the caller explicit avoids surprises.
    raise TypeError(f"canonical_json: unsupported value type: {type(v).__name__}")
