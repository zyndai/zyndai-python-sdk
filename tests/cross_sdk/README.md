# Cross-SDK signature compatibility test

Verifies the Python SDK and the TypeScript SDK produce/verify byte-identical
A2A signatures.

Both scripts use a fixed Ed25519 seed so the same `entity_id` /
`public_key` derive on both sides — that lets each side verify what the
other emitted without a side-channel.

## Run

```
cd zyndai-agent/tests/cross_sdk

# Build the TS SDK first if you haven't:
( cd ../../../zyndai-ts-sdk && pnpm build )

# Python emits, TS verifies:
python3 gen_vectors_py.py gen > vectors.py.json
node gen_vectors_ts.mjs verify vectors.py.json

# TS emits, Python verifies:
node gen_vectors_ts.mjs gen > vectors.ts.json
python3 gen_vectors_py.py verify vectors.ts.json
```

Both directions must end with `ALL OK`.

## What's checked

- Ed25519 key derivation (same seed → same `entity_id` / `public_key`
  across SDKs).
- `x-zynd-auth.signature` round-trips through both verifiers.
- JCS canonical bytes are byte-identical (the script base64s
  `canonicalBytes(message)` on emit and re-checks on verify).
- Non-ASCII content (accents, em dashes) survives canonicalization.
- Nested arrays / booleans / nulls / floats survive canonicalization.
