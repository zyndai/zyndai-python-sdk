/**
 * Cross-SDK signature compat — TypeScript side.
 *
 * Two subcommands:
 *   gen     Sign a fixed envelope with a fixed seed, dump JSON to stdout.
 *   verify  Read a vectors JSON file, verify each entry, exit non-zero on failure.
 *
 * Run:
 *   node gen_vectors_ts.mjs gen   > vectors.ts.json
 *   node gen_vectors_ts.mjs verify vectors.py.json
 */

import { readFileSync } from "node:fs";
import * as path from "node:path";
import { pathToFileURL } from "node:url";

// Resolve the locally-built SDK without depending on `npm install`.
const candidates = [
  path.resolve(process.env.HOME || "", "Desktop/p3ai/zyndai-ts-sdk/dist/index.mjs"),
  "/sessions/awesome-vigilant-volta/mnt/p3ai/zyndai-ts-sdk/dist/index.mjs",
];
let SDK_DIR = null;
for (const c of candidates) {
  try {
    readFileSync(c);
    SDK_DIR = c;
    break;
  } catch {}
}
if (!SDK_DIR) throw new Error(`SDK build not found in ${candidates.join(" or ")}`);
const sdk = await import(pathToFileURL(SDK_DIR).href);

const {
  keypairFromPrivateBytes,
  signMessage,
  verifyMessage,
  canonicalBytes,
} = sdk;

// Fixed seed — same in Python.
const SEED_BYTES = new TextEncoder().encode("ZYND-CROSS-SDK-COMPAT-VECTORS!!!");
if (SEED_BYTES.byteLength !== 32) {
  throw new Error(`bad seed length: ${SEED_BYTES.byteLength}`);
}

function makeMessage(opts) {
  const msg = {
    kind: "message",
    role: "user",
    messageId: opts.messageId,
    parts: [],
  };
  if (opts.contextId) msg.contextId = opts.contextId;
  if (opts.data) msg.parts.push({ kind: "data", data: opts.data });
  if (opts.text) msg.parts.push({ kind: "text", text: opts.text });
  return msg;
}

function gen() {
  const kp = keypairFromPrivateBytes(SEED_BYTES);

  const cases = [];

  // 1. text-only
  const m1 = makeMessage({ messageId: "msg-1", text: "hello cross-sdk" });
  signMessage(m1, { keypair: kp, entityId: kp.entityId });
  cases.push({ name: "text_only", message: m1 });

  // 2. text + data + non-ASCII
  const m2 = makeMessage({
    messageId: "msg-2",
    contextId: "ctx-1",
    text: "résumé — naïve façade",
    data: { k: "v", nested: { a: 1, b: [true, null, 0.5] } },
  });
  signMessage(m2, {
    keypair: kp,
    entityId: kp.entityId,
    fqan: "zns01.zynd.ai/acme/xlator",
  });
  cases.push({ name: "non_ascii_with_data", message: m2 });

  // 3. data-only (no text part)
  const m3 = makeMessage({ messageId: "msg-3", data: { only: "data" } });
  signMessage(m3, { keypair: kp, entityId: kp.entityId });
  cases.push({ name: "data_only", message: m3 });

  // Self-check pre-emit so a sign-side bug doesn't poison the file.
  for (const c of cases) verifyMessage(c.message);

  for (const c of cases) {
    c.canonical_b64 = Buffer.from(canonicalBytes(c.message)).toString("base64");
  }

  return {
    generator: "typescript",
    seed_b64: Buffer.from(SEED_BYTES).toString("base64"),
    public_key: kp.publicKeyString,
    entity_id: kp.entityId,
    cases,
  };
}

function verify(filepath) {
  const data = JSON.parse(readFileSync(filepath, "utf-8"));
  console.log(`# verifying file from: ${data.generator}`);
  console.log(`#   public_key: ${data.public_key}`);
  console.log(`#   entity_id:  ${data.entity_id}`);

  const failures = [];
  for (const c of data.cases) {
    try {
      verifyMessage(c.message);
      const recanon = Buffer.from(canonicalBytes(c.message)).toString("base64");
      if (recanon !== c.canonical_b64) {
        throw new Error(
          `canonical bytes diverge:\n  emitted: ${c.canonical_b64}\n  reproduced: ${recanon}`,
        );
      }
      console.log(`  PASS  ${c.name}`);
    } catch (e) {
      console.log(`  FAIL  ${c.name} :: ${e?.name ?? "?"}: ${e?.message ?? e}`);
      failures.push(c.name);
    }
  }
  if (failures.length) {
    console.log(`\n${failures.length} failure(s): ${failures.join(", ")}`);
    return 1;
  }
  console.log("\nALL OK");
  return 0;
}

const cmd = process.argv[2];
if (cmd === "gen") {
  process.stdout.write(JSON.stringify(gen(), null, 2));
} else if (cmd === "verify") {
  const code = verify(process.argv[3]);
  process.exit(code);
} else {
  console.error("usage: gen_vectors_ts.mjs gen | verify <path>");
  process.exit(2);
}
