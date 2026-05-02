"""Cross-SDK signature compat — Python side.

Two halves to this script (CLI subcommands):

  gen     Use a fixed Ed25519 seed, sign a fixed envelope, dump to vectors.json.
          We also include the JCS-canonical bytes (as base64) so the TS verifier
          can prove its canonicalizer produces the same byte string.

  verify  Read vectors.json (a TS-produced or Python-produced file), and
          run verify_message() against each entry. Exits non-zero on any
          failure.

Run:
  python gen_vectors_py.py gen   > vectors.py.json
  python gen_vectors_py.py verify vectors.ts.json
"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

# Make the repo importable regardless of cwd. Try a few well-known mounts
# so the same script works from the bash sandbox AND from the user's host.
_CANDIDATES = [
    Path.home() / "Desktop" / "p3ai" / "zyndai-agent",
    Path("/sessions/awesome-vigilant-volta/mnt/p3ai/zyndai-agent"),
]
for c in _CANDIDATES:
    if (c / "zyndai_agent" / "__init__.py").exists():
        sys.path.insert(0, str(c))
        break

from zyndai_agent.a2a.adapter import to_a2a_message  # noqa: E402
from zyndai_agent.a2a.auth import (  # noqa: E402
    ReplayCache,
    sign_message,
    verify_message,
)
from zyndai_agent.a2a.canonical import canonical_bytes  # noqa: E402
from zyndai_agent.ed25519_identity import keypair_from_private_bytes  # noqa: E402


# Fixed test seed — same in TS. 32 bytes of "ZYND-CROSS-SDK-COMPAT-VECTORS!!!"
SEED_BYTES = b"ZYND-CROSS-SDK-COMPAT-VECTORS!!!"
assert len(SEED_BYTES) == 32, len(SEED_BYTES)


def gen() -> dict:
    kp = keypair_from_private_bytes(SEED_BYTES)

    cases = []

    # Case 1: text-only
    msg1 = to_a2a_message(role="user", message_id="msg-1", text="hello cross-sdk")
    sign_message(msg1, kp, kp.entity_id)
    cases.append({"name": "text_only", "message": msg1})

    # Case 2: text + data + non-ASCII
    msg2 = to_a2a_message(
        role="user",
        message_id="msg-2",
        context_id="ctx-1",
        text="résumé — naïve façade",  # accents + em dash + non-ASCII
        data={"k": "v", "nested": {"a": 1, "b": [True, None, 0.5]}},
    )
    sign_message(msg2, kp, kp.entity_id, fqan="zns01.zynd.ai/acme/xlator")
    cases.append({"name": "non_ascii_with_data", "message": msg2})

    # Case 3: empty parts but rich metadata
    msg3 = to_a2a_message(role="user", message_id="msg-3", data={"only": "data"})
    sign_message(msg3, kp, kp.entity_id)
    cases.append({"name": "data_only", "message": msg3})

    # Round-trip self-check on emit side: sign/verify before we ship the
    # vector, so the file we produce isn't poisoned by a sign-side bug.
    cache = ReplayCache()
    for c in cases:
        verify_message(c["message"], replay_cache=cache)

    # Also embed the canonical bytes (b64) of each post-signing message
    # as a cross-checkable artifact.
    for c in cases:
        c["canonical_b64"] = base64.b64encode(canonical_bytes(c["message"])).decode()

    return {
        "generator": "python",
        "seed_b64": base64.b64encode(SEED_BYTES).decode(),
        "public_key": kp.public_key_string,
        "entity_id": kp.entity_id,
        "cases": cases,
    }


def verify(vector_path: str) -> int:
    data = json.loads(Path(vector_path).read_text())
    print(f"# verifying file from: {data.get('generator')}")
    print(f"#   public_key: {data.get('public_key')}")
    print(f"#   entity_id:  {data.get('entity_id')}")

    cache = ReplayCache()
    failures: list[str] = []
    for c in data["cases"]:
        name = c["name"]
        try:
            verify_message(c["message"], replay_cache=cache)
            # Also assert canonical_b64 matches what we re-canonicalize now.
            recanon = base64.b64encode(canonical_bytes(c["message"])).decode()
            if recanon != c["canonical_b64"]:
                raise ValueError(
                    f"canonical bytes diverge:\n  emitted: {c['canonical_b64']}\n  reproduced: {recanon}"
                )
            print(f"  PASS  {name}")
        except Exception as e:  # noqa: BLE001
            print(f"  FAIL  {name} :: {type(e).__name__}: {e}")
            failures.append(name)

    if failures:
        print(f"\n{len(failures)} failure(s): {failures}")
        return 1
    print("\nALL OK")
    return 0


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: gen_vectors_py.py gen | verify <path>", file=sys.stderr)
        return 2

    cmd = sys.argv[1]
    if cmd == "gen":
        out = gen()
        print(json.dumps(out, indent=2))
        return 0
    if cmd == "verify":
        if len(sys.argv) < 3:
            print("usage: gen_vectors_py.py verify <path>", file=sys.stderr)
            return 2
        return verify(sys.argv[2])
    print(f"unknown command: {cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
