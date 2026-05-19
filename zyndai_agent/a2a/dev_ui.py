"""Dev UI — Swagger-like test page mounted at GET / when ZYND_DEV=1.

Mirrors zyndai-ts-sdk/src/a2a/dev-ui.ts. The browser:
  1. fetches /.well-known/agent-card.json once on load,
  2. reads x-zynd.inputSchema / outputSchema / acceptsFiles,
  3. renders a form (text/number/bool/enum/file) + Raw JSON + Streaming tabs,
  4. POSTs message/send or message/stream JSON-RPC to a2a_path,
  5. shows the response, validates it loosely against outputSchema,
  6. offers Copy-as-curl and View-card.

Keep this file's CSS / CLIENT_JS strings in sync with the TS sibling so
both SDKs render identically.
"""

from __future__ import annotations

import json


def build_dev_ui_html(*, agent_name: str, a2a_path: str) -> str:
    """Return the self-contained HTML document for the dev UI."""
    name_html = _escape_html(agent_name)
    path_html = _escape_html(a2a_path)
    a2a_path_json = json.dumps(a2a_path)
    return (
        '<!DOCTYPE html>\n'
        '<html lang="en">\n'
        '<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
        f'<title>[dev] {name_html}</title>\n'
        '<style>\n'
        f'{CSS}\n'
        '</style>\n'
        '</head>\n'
        '<body>\n'
        '<div class="container">\n'
        '  <header>\n'
        '    <div class="title-row">\n'
        f'      <h1>{name_html} <span class="badge">dev</span></h1>\n'
        '      <div class="header-actions">\n'
        '        <button class="ghost" id="btn-view-card">View card</button>\n'
        '      </div>\n'
        '    </div>\n'
        '    <div class="meta">\n'
        f'      <span class="meta-item"><span class="meta-label">POST</span> <code id="meta-endpoint">{path_html}</code></span>\n'
        '      <span class="meta-item" id="meta-entity"></span>\n'
        '      <span class="meta-item" id="meta-status"></span>\n'
        '    </div>\n'
        '  </header>\n'
        '\n'
        '  <div class="card">\n'
        '    <div class="tabs">\n'
        '      <button class="tab active" data-tab="form">Form</button>\n'
        '      <button class="tab" data-tab="raw">Raw JSON</button>\n'
        '      <button class="tab" data-tab="stream">Streaming</button>\n'
        '    </div>\n'
        '\n'
        '    <div class="tab-panel active" data-panel="form">\n'
        '      <form id="form" enctype="multipart/form-data"></form>\n'
        '      <div id="form-empty" class="muted" style="display:none">No input fields discovered. Try the Raw JSON tab.</div>\n'
        '      <div class="actions">\n'
        '        <button id="btn-send">Send</button>\n'
        '        <button class="ghost" id="btn-copy-curl">Copy as curl</button>\n'
        '      </div>\n'
        '    </div>\n'
        '\n'
        '    <div class="tab-panel" data-panel="raw">\n'
        '      <p class="muted">JSON-RPC params.message.parts will be sent as-is.</p>\n'
        '      <textarea id="raw-json" rows="14" spellcheck="false"></textarea>\n'
        '      <div class="actions">\n'
        '        <button id="btn-send-raw">Send</button>\n'
        '        <button class="ghost" id="btn-copy-curl-raw">Copy as curl</button>\n'
        '      </div>\n'
        '    </div>\n'
        '\n'
        '    <div class="tab-panel" data-panel="stream">\n'
        '      <p class="muted">Sends via <code>message/stream</code>. SSE frames render below as they arrive.</p>\n'
        '      <form id="form-stream"></form>\n'
        '      <div class="actions">\n'
        '        <button id="btn-stream">Stream</button>\n'
        '        <button class="ghost" id="btn-stream-stop" disabled>Stop</button>\n'
        '      </div>\n'
        '      <div id="timeline" class="timeline"></div>\n'
        '    </div>\n'
        '  </div>\n'
        '\n'
        '  <div class="card" id="resp" style="display:none">\n'
        '    <div class="resp-header">\n'
        '      <p class="card-title">Response</p>\n'
        '      <div>\n'
        '        <span id="resp-validation" class="validation"></span>\n'
        '        <span id="resp-status"></span>\n'
        '      </div>\n'
        '    </div>\n'
        '    <pre id="resp-body"></pre>\n'
        '  </div>\n'
        '</div>\n'
        '\n'
        '<div class="modal-backdrop" id="modal-card" style="display:none">\n'
        '  <div class="modal">\n'
        '    <div class="modal-header">\n'
        '      <h2>Agent card</h2>\n'
        '      <button class="ghost" id="btn-close-modal">Close</button>\n'
        '    </div>\n'
        '    <pre id="modal-card-body"></pre>\n'
        '  </div>\n'
        '</div>\n'
        '\n'
        '<script>\n'
        f'const A2A_PATH = {a2a_path_json};\n'
        'const CARD_PATH = "/.well-known/agent-card.json";\n'
        f'{CLIENT_JS}\n'
        '</script>\n'
        '</body>\n'
        '</html>'
    )


def _escape_html(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


# -----------------------------------------------------------------------------
# CSS — mirrors zyndai-ts-sdk/src/a2a/dev-ui.ts CSS constant
# -----------------------------------------------------------------------------

CSS = """
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
:root {
  --bg: #0f1117; --surface: #1a1d27; --surface-2: #232633; --border: #2a2d3a;
  --accent: #8B5CF6; --accent-2: #06B6D4; --text: #e2e8f0; --muted: #64748b;
  --error: #f87171; --ok: #34d399; --warn: #fbbf24;
  --font: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  --mono: 'Fira Code', 'Cascadia Code', 'Menlo', monospace;
}
body { background: var(--bg); color: var(--text); font-family: var(--font);
  min-height: 100vh; padding: 2rem 1rem; }
.container { max-width: 820px; margin: 0 auto; }
header { margin-bottom: 1.5rem; }
.title-row { display: flex; align-items: center; justify-content: space-between; gap: 1rem; }
h1 { font-size: 1.5rem; font-weight: 700; display: flex; align-items: center; gap: .6rem; }
.badge { background: var(--accent); color: #fff; font-size: .65rem;
  padding: .2rem .5rem; border-radius: 999px; font-weight: 600;
  letter-spacing: .08em; text-transform: uppercase; }
.meta { display: flex; flex-wrap: wrap; gap: 1rem; margin-top: .6rem;
  color: var(--muted); font-size: .8rem; font-family: var(--mono); }
.meta-label { color: var(--accent-2); font-weight: 600; }
.meta-item code { color: var(--text); }
.card { background: var(--surface); border: 1px solid var(--border);
  border-radius: .75rem; padding: 1.5rem; margin-bottom: 1.5rem; }
.card-title { font-size: .7rem; font-weight: 600; text-transform: uppercase;
  letter-spacing: .1em; color: var(--muted); }
.tabs { display: flex; gap: .25rem; margin-bottom: 1.25rem; border-bottom: 1px solid var(--border); }
.tab { background: transparent; border: none; color: var(--muted); padding: .6rem 1rem;
  font-size: .85rem; font-weight: 500; cursor: pointer; border-bottom: 2px solid transparent;
  margin-bottom: -1px; }
.tab:hover { color: var(--text); }
.tab.active { color: var(--text); border-bottom-color: var(--accent); }
.tab-panel { display: none; }
.tab-panel.active { display: block; }
.field { display: flex; flex-direction: column; gap: .4rem; margin-bottom: 1rem; }
.field-label { display: flex; align-items: baseline; gap: .5rem; font-size: .8rem;
  font-weight: 500; color: var(--muted); }
.field-label .req { color: var(--accent); font-weight: 700; }
.field-hint { font-size: .7rem; color: var(--muted); font-family: var(--mono); }
input[type=text], input[type=number], input[type=email], input[type=url], textarea, select {
  background: var(--bg); border: 1px solid var(--border); border-radius: .4rem;
  color: var(--text); font-family: var(--font); font-size: .9rem;
  padding: .65rem .85rem; width: 100%; outline: none; resize: vertical; }
input:focus, textarea:focus, select:focus { border-color: var(--accent); }
textarea { min-height: 72px; line-height: 1.5; font-family: var(--mono); font-size: .82rem; }
#raw-json { min-height: 320px; }
.checkbox-field { flex-direction: row; align-items: center; }
.checkbox-label { display: flex; align-items: center; gap: .5rem; font-size: .9rem; cursor: pointer; }
input[type=checkbox] { width: 1rem; height: 1rem; accent-color: var(--accent); }
.file-input { background: var(--bg); border: 1px dashed var(--border); border-radius: .4rem;
  padding: 1rem; cursor: pointer; }
.file-input input[type=file] { color: var(--muted); width: 100%; cursor: pointer; }
.file-list { margin-top: .5rem; display: flex; flex-direction: column; gap: .25rem; }
.file-item { font-family: var(--mono); font-size: .75rem; color: var(--text);
  background: var(--surface-2); padding: .35rem .5rem; border-radius: .3rem;
  display: flex; justify-content: space-between; }
.file-item.bad { color: var(--error); }
.actions { display: flex; gap: .5rem; margin-top: .75rem; }
button { background: var(--accent); border: none; border-radius: .5rem;
  color: #fff; cursor: pointer; font-size: .85rem; font-weight: 600;
  padding: .65rem 1.2rem; transition: opacity .15s; }
button:hover { opacity: .85; }
button:disabled { opacity: .4; cursor: not-allowed; }
button.ghost { background: transparent; color: var(--muted); border: 1px solid var(--border); }
button.ghost:hover { color: var(--text); border-color: var(--accent); opacity: 1; }
.resp-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: .75rem; }
.status-ok  { color: var(--ok);  font-size: .8rem; font-weight: 600; font-family: var(--mono); }
.status-err { color: var(--error); font-size: .8rem; font-weight: 600; font-family: var(--mono); }
.validation { font-size: .7rem; padding: .15rem .5rem; border-radius: 999px; margin-right: .5rem;
  font-family: var(--mono); }
.validation.ok { background: rgba(52,211,153,.15); color: var(--ok); }
.validation.bad { background: rgba(248,113,113,.15); color: var(--error); }
pre { background: var(--bg); border: 1px solid var(--border); border-radius: .4rem;
  color: var(--text); font-family: var(--mono); font-size: .78rem;
  overflow-x: auto; padding: 1rem; white-space: pre-wrap; word-break: break-word;
  max-height: 60vh; overflow-y: auto; }
.spinner { display: inline-block; width: .9rem; height: .9rem;
  border: 2px solid var(--border); border-top-color: var(--accent);
  border-radius: 50%; animation: spin .6s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
.timeline { margin-top: 1rem; display: flex; flex-direction: column; gap: .5rem; }
.tl-item { background: var(--surface-2); border-left: 3px solid var(--accent);
  border-radius: .35rem; padding: .6rem .8rem; font-family: var(--mono); font-size: .78rem; }
.tl-item.final { border-left-color: var(--ok); }
.tl-item.error { border-left-color: var(--error); }
.tl-kind { color: var(--accent-2); font-weight: 600; }
.tl-state { color: var(--warn); }
.muted { color: var(--muted); font-size: .82rem; margin-bottom: .75rem; }
.modal-backdrop { position: fixed; inset: 0; background: rgba(0,0,0,.7);
  display: flex; align-items: center; justify-content: center; z-index: 100; padding: 2rem; }
.modal { background: var(--surface); border: 1px solid var(--border); border-radius: .75rem;
  padding: 1.5rem; max-width: 760px; width: 100%; max-height: 80vh; display: flex;
  flex-direction: column; }
.modal-header { display: flex; justify-content: space-between; align-items: center;
  margin-bottom: 1rem; }
.modal h2 { font-size: 1.1rem; font-weight: 700; }
.modal pre { flex: 1; max-height: none; }
.header-actions { display: flex; gap: .5rem; }
.toast { position: fixed; bottom: 1.5rem; left: 50%; transform: translateX(-50%);
  background: var(--surface-2); color: var(--text); padding: .5rem 1rem;
  border-radius: .4rem; border: 1px solid var(--border); font-size: .85rem;
  z-index: 200; opacity: 0; transition: opacity .2s; }
.toast.show { opacity: 1; }
"""

# -----------------------------------------------------------------------------
# CLIENT_JS — mirrors zyndai-ts-sdk/src/a2a/dev-ui.ts CLIENT_JS constant
# -----------------------------------------------------------------------------

CLIENT_JS = r"""
const STATE = { card: null, inputSchema: null, outputSchema: null, acceptsFiles: false };

const ATTACHMENT_KEYS = ["filename", "mime_type", "data"];

(async function boot() {
  try {
    const res = await fetch(CARD_PATH, { headers: { Accept: "application/json" } });
    STATE.card = await res.json();
    const xz = (STATE.card && STATE.card["x-zynd"]) || {};
    STATE.inputSchema = xz.inputSchema || null;
    STATE.outputSchema = xz.outputSchema || null;
    STATE.acceptsFiles = !!xz.acceptsFiles;

    document.getElementById("meta-entity").innerHTML = xz.entityId
      ? '<span class="meta-label">ENTITY</span> <code>' + truncate(xz.entityId, 24) + '</code>'
      : '';
    document.getElementById("meta-status").innerHTML = xz.status
      ? '<span class="meta-label">STATUS</span> <code>' + escape(xz.status) + '</code>'
      : '';
  } catch (e) {
    showToast("Couldn't load agent card: " + e.message);
  }

  renderForm("form", STATE.inputSchema);
  renderForm("form-stream", STATE.inputSchema);
  document.getElementById("raw-json").value = buildExampleParams(STATE.inputSchema);

  wireTabs();
  wireSubmit();
  wireStream();
  wireCurl();
  wireViewCard();
})();

function renderForm(formId, schema) {
  const form = document.getElementById(formId);
  form.innerHTML = "";
  const properties = (schema && schema.properties) || {};
  const required = new Set((schema && schema.required) || []);
  const keys = Object.keys(properties);

  if (keys.length === 0) {
    if (formId === "form") document.getElementById("form-empty").style.display = "block";
    form.appendChild(buildField("__msg__", { type: "string", description: "Message to send" }, false));
    return;
  }
  for (const name of keys) {
    form.appendChild(buildField(name, properties[name], required.has(name)));
  }
}

function buildField(name, prop, req) {
  const wrap = document.createElement("div");
  wrap.className = "field";
  const isFile = isAttachmentField(prop);

  if (prop.type === "boolean") {
    wrap.classList.add("checkbox-field");
    const label = document.createElement("label");
    label.className = "checkbox-label";
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.name = name;
    cb.dataset.kind = "boolean";
    label.appendChild(cb);
    label.appendChild(document.createTextNode(" " + name));
    wrap.appendChild(label);
    return wrap;
  }

  const labelRow = document.createElement("div");
  labelRow.className = "field-label";
  const labelText = document.createElement("span");
  labelText.textContent = name;
  labelRow.appendChild(labelText);
  if (req) {
    const r = document.createElement("span");
    r.className = "req";
    r.textContent = "required";
    labelRow.appendChild(r);
  }
  if (prop.description) {
    const hint = document.createElement("span");
    hint.className = "field-hint";
    hint.textContent = prop.description;
    labelRow.appendChild(hint);
  }
  wrap.appendChild(labelRow);

  if (isFile) {
    const mimes = prop.accepted_mime_types || [];
    const drop = document.createElement("div");
    drop.className = "file-input";
    const input = document.createElement("input");
    input.type = "file";
    input.multiple = true;
    input.name = name;
    input.dataset.kind = "file";
    if (mimes.length) input.accept = mimes.join(",");
    drop.appendChild(input);
    const list = document.createElement("div");
    list.className = "file-list";
    drop.appendChild(list);
    input.addEventListener("change", () => renderFileList(input, list, mimes));
    wrap.appendChild(drop);
    return wrap;
  }

  if (prop.enum) {
    const sel = document.createElement("select");
    sel.name = name;
    sel.dataset.kind = "enum";
    if (!req) sel.appendChild(opt("", "(none)"));
    for (const v of prop.enum) sel.appendChild(opt(String(v), String(v)));
    wrap.appendChild(sel);
    return wrap;
  }

  if (prop.type === "integer" || prop.type === "number") {
    const inp = document.createElement("input");
    inp.type = "number";
    inp.name = name;
    inp.dataset.kind = prop.type;
    if (typeof prop.minimum === "number") inp.min = String(prop.minimum);
    if (typeof prop.maximum === "number") inp.max = String(prop.maximum);
    if (prop.type === "integer") inp.step = "1";
    wrap.appendChild(inp);
    return wrap;
  }

  if (prop.type === "object" || prop.type === "array") {
    const ta = document.createElement("textarea");
    ta.name = name;
    ta.dataset.kind = prop.type;
    ta.rows = 3;
    ta.placeholder = prop.type === "array" ? "[]" : "{}";
    wrap.appendChild(ta);
    return wrap;
  }

  if (isLongTextField(name) || (prop.maxLength && prop.maxLength > 80)) {
    const ta = document.createElement("textarea");
    ta.name = name;
    ta.dataset.kind = "string";
    ta.rows = 4;
    ta.placeholder = name === "__msg__" ? "Enter your message…" : name;
    if (req) ta.required = true;
    wrap.appendChild(ta);
  } else {
    const inp = document.createElement("input");
    inp.type = pickInputType(prop.format);
    inp.name = name;
    inp.dataset.kind = "string";
    inp.placeholder = name;
    if (req) inp.required = true;
    if (prop.pattern) inp.pattern = prop.pattern;
    wrap.appendChild(inp);
  }
  return wrap;
}

function opt(value, label) {
  const o = document.createElement("option");
  o.value = value;
  o.textContent = label;
  return o;
}

function pickInputType(format) {
  if (format === "email") return "email";
  if (format === "uri" || format === "url") return "url";
  return "text";
}

function isLongTextField(name) {
  return ["input", "message", "query", "prompt", "text", "content", "body"].indexOf(name) !== -1;
}

function isAttachmentField(prop) {
  if (!prop) return false;
  if (prop.$ref && /Attachment/.test(prop.$ref)) return true;
  if (prop.type === "array" && prop.items) {
    const ip = prop.items;
    if (ip.$ref && /Attachment/.test(ip.$ref)) return true;
    if (ip.properties) {
      const keys = Object.keys(ip.properties);
      const hasAll = ATTACHMENT_KEYS.every((k) => keys.indexOf(k) !== -1);
      if (hasAll) return true;
    }
  }
  return false;
}

function renderFileList(input, list, mimes) {
  list.innerHTML = "";
  const files = Array.from(input.files || []);
  for (const f of files) {
    const item = document.createElement("div");
    item.className = "file-item";
    const ok = mimes.length === 0 || mimes.some((m) => mimeMatches(f.type, m));
    if (!ok) item.classList.add("bad");
    const right = ok ? formatBytes(f.size) : "rejected: " + (f.type || "unknown mime");
    item.innerHTML = "<span>" + escape(f.name) + "</span><span>" + right + "</span>";
    list.appendChild(item);
  }
}

function mimeMatches(actual, pattern) {
  if (!actual) return false;
  if (pattern.endsWith("/*")) return actual.startsWith(pattern.slice(0, -1));
  return actual === pattern;
}

async function collectFormPayload(formEl) {
  const data = {};
  const attachments = [];
  let textPart = "";

  for (const el of formEl.querySelectorAll("[name]")) {
    const name = el.name;
    const kind = el.dataset.kind;
    if (kind === "boolean") {
      data[name] = el.checked;
    } else if (kind === "integer" || kind === "number") {
      data[name] = el.value === "" ? null : Number(el.value);
    } else if (kind === "enum") {
      if (el.value !== "") data[name] = el.value;
    } else if (kind === "object" || kind === "array") {
      try { data[name] = JSON.parse(el.value || (kind === "array" ? "[]" : "{}")); }
      catch { data[name] = el.value; }
    } else if (kind === "file") {
      const files = Array.from(el.files || []);
      const encoded = [];
      for (const f of files) {
        encoded.push({
          filename: f.name,
          mime_type: f.type || "application/octet-stream",
          data: await readAsBase64(f),
        });
      }
      data[name] = encoded;
      for (const a of encoded) attachments.push(a);
    } else {
      const v = el.value;
      if (name === "__msg__") {
        textPart = v;
      } else {
        data[name] = v;
      }
      if (isLongTextField(name) && typeof v === "string" && v.trim()) textPart = v.trim();
    }
  }

  const parts = [];
  const textOnly = Object.keys(data).length === 0 && textPart;
  if (textOnly) {
    parts.push({ kind: "text", text: textPart });
  } else {
    parts.push({ kind: "text", text: textPart || JSON.stringify(data) });
    if (Object.keys(data).length > 0) parts.push({ kind: "data", data });
  }
  return { parts, data, attachments };
}

function readAsBase64(file) {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => {
      const result = r.result;
      const i = result.indexOf(",");
      resolve(i >= 0 ? result.slice(i + 1) : result);
    };
    r.onerror = () => reject(r.error);
    r.readAsDataURL(file);
  });
}

function buildRpcBody(parts, method) {
  return {
    jsonrpc: "2.0",
    method: method || "message/send",
    params: {
      message: { role: "user", parts, messageId: "dev-" + Math.random().toString(36).slice(2, 10) },
    },
    id: "dev-" + Date.now(),
  };
}

function wireTabs() {
  for (const tab of document.querySelectorAll(".tab")) {
    tab.addEventListener("click", () => {
      for (const t of document.querySelectorAll(".tab")) t.classList.remove("active");
      for (const p of document.querySelectorAll(".tab-panel")) p.classList.remove("active");
      tab.classList.add("active");
      document.querySelector('[data-panel="' + tab.dataset.tab + '"]').classList.add("active");
    });
  }
}

function wireSubmit() {
  document.getElementById("btn-send").addEventListener("click", async () => {
    const formEl = document.getElementById("form");
    const { parts } = await collectFormPayload(formEl);
    const body = buildRpcBody(parts, "message/send");
    await sendRpc(body, "btn-send");
  });

  document.getElementById("btn-send-raw").addEventListener("click", async () => {
    const txt = document.getElementById("raw-json").value;
    let parsed;
    try { parsed = JSON.parse(txt); }
    catch (e) { showResponse(null, "Invalid JSON: " + e.message, true); return; }
    let body;
    if (parsed && parsed.jsonrpc === "2.0" && parsed.method) {
      body = parsed;
    } else if (Array.isArray(parsed)) {
      body = buildRpcBody(parsed, "message/send");
    } else if (parsed && Array.isArray(parsed.parts)) {
      body = buildRpcBody(parsed.parts, "message/send");
    } else {
      body = buildRpcBody([{ kind: "data", data: parsed }], "message/send");
    }
    await sendRpc(body, "btn-send-raw");
  });
}

async function sendRpc(body, btnId) {
  const btn = document.getElementById(btnId);
  const orig = btn.textContent;
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span>';
  try {
    const res = await fetch(A2A_PATH, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const json = await res.json().catch(() => null);
    showResponse(res, json, !res.ok);
  } catch (e) {
    showResponse(null, String(e), true);
  } finally {
    btn.disabled = false;
    btn.textContent = orig;
  }
}

function showResponse(res, json, err) {
  const div = document.getElementById("resp");
  const status = document.getElementById("resp-status");
  const body = document.getElementById("resp-body");
  const validation = document.getElementById("resp-validation");
  div.style.display = "block";
  validation.textContent = "";
  validation.className = "validation";

  if (res) {
    status.className = res.ok ? "status-ok" : "status-err";
    status.textContent = (res.ok ? "✓ " : "✗ ") + res.status;
  } else {
    status.className = "status-err";
    status.textContent = err ? "✗ error" : "✗ network";
  }
  body.textContent = typeof json === "string" ? json : JSON.stringify(json, null, 2);

  if (!err && STATE.outputSchema && json && json.result) {
    const out = pluckArtifactData(json.result);
    if (out !== undefined) {
      const errors = validateAgainst(STATE.outputSchema, out);
      if (errors.length === 0) {
        validation.className = "validation ok";
        validation.textContent = "matches output_schema";
      } else {
        validation.className = "validation bad";
        validation.textContent = errors.length + " schema issue" + (errors.length > 1 ? "s" : "");
        validation.title = errors.join("\n");
      }
    }
  }
}

function pluckArtifactData(task) {
  if (!task || !Array.isArray(task.artifacts)) return undefined;
  for (const art of task.artifacts) {
    if (!Array.isArray(art.parts)) continue;
    for (const p of art.parts) {
      if (p.kind === "data" && p.data) return p.data;
    }
  }
  return undefined;
}

let streamAbort = null;

function wireStream() {
  document.getElementById("btn-stream").addEventListener("click", async () => {
    const formEl = document.getElementById("form-stream");
    const { parts } = await collectFormPayload(formEl);
    const body = buildRpcBody(parts, "message/stream");
    await openStream(body);
  });
  document.getElementById("btn-stream-stop").addEventListener("click", () => {
    if (streamAbort) streamAbort.abort();
  });
}

async function openStream(body) {
  const timeline = document.getElementById("timeline");
  timeline.innerHTML = "";
  const btn = document.getElementById("btn-stream");
  const stopBtn = document.getElementById("btn-stream-stop");
  btn.disabled = true;
  stopBtn.disabled = false;

  streamAbort = new AbortController();
  try {
    const res = await fetch(A2A_PATH, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
      body: JSON.stringify(body),
      signal: streamAbort.signal,
    });
    if (!res.ok) {
      timeline.appendChild(tlItem("HTTP " + res.status, "error"));
      return;
    }
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let idx;
      while ((idx = buf.indexOf("\n\n")) !== -1) {
        const raw = buf.slice(0, idx);
        buf = buf.slice(idx + 2);
        const frame = parseSseFrame(raw);
        if (frame) renderStreamFrame(timeline, frame);
      }
    }
  } catch (e) {
    if (e.name !== "AbortError") timeline.appendChild(tlItem("error: " + e.message, "error"));
  } finally {
    btn.disabled = false;
    stopBtn.disabled = true;
    streamAbort = null;
  }
}

function parseSseFrame(raw) {
  const dataLines = [];
  for (const line of raw.split("\n")) {
    if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
  }
  if (dataLines.length === 0) return null;
  try { return JSON.parse(dataLines.join("\n")); }
  catch { return { _raw: dataLines.join("\n") }; }
}

function renderStreamFrame(timeline, frame) {
  const ev = (frame && frame.result) || frame;
  const cls = (ev && ev.final) ? "final" : "";
  const kind = (ev && ev.kind) || "?";
  const state = ev && ev.status && ev.status.state;
  const summary = state ? '<span class="tl-kind">' + escape(kind) + '</span> <span class="tl-state">' + escape(state) + '</span>' : '<span class="tl-kind">' + escape(kind) + '</span>';
  const item = document.createElement("div");
  item.className = "tl-item " + cls;
  item.innerHTML = summary + "<pre style=\"margin-top:.4rem;background:transparent;border:0;padding:0\">" + escape(JSON.stringify(ev, null, 2)) + "</pre>";
  timeline.appendChild(item);
}

function tlItem(text, cls) {
  const d = document.createElement("div");
  d.className = "tl-item " + (cls || "");
  d.textContent = text;
  return d;
}

function wireCurl() {
  document.getElementById("btn-copy-curl").addEventListener("click", async () => {
    const { parts } = await collectFormPayload(document.getElementById("form"));
    copyCurl(buildRpcBody(parts, "message/send"));
  });
  document.getElementById("btn-copy-curl-raw").addEventListener("click", () => {
    const txt = document.getElementById("raw-json").value;
    let body;
    try { body = JSON.parse(txt); }
    catch { body = buildRpcBody([{ kind: "text", text: txt }], "message/send"); }
    copyCurl(body);
  });
}

function copyCurl(body) {
  const json = JSON.stringify(body);
  const escaped = json.replace(/'/g, "'\\''");
  const cmd = "curl -X POST " + location.origin + A2A_PATH +
    " -H 'Content-Type: application/json'" +
    " -d '" + escaped + "'";
  if (navigator.clipboard) {
    navigator.clipboard.writeText(cmd).then(() => showToast("curl command copied"));
  } else {
    showToast("clipboard unavailable; check console");
    console.log(cmd);
  }
}

function wireViewCard() {
  document.getElementById("btn-view-card").addEventListener("click", () => {
    document.getElementById("modal-card-body").textContent = JSON.stringify(STATE.card, null, 2);
    document.getElementById("modal-card").style.display = "flex";
  });
  document.getElementById("btn-close-modal").addEventListener("click", () => {
    document.getElementById("modal-card").style.display = "none";
  });
  document.getElementById("modal-card").addEventListener("click", (e) => {
    if (e.target.id === "modal-card") document.getElementById("modal-card").style.display = "none";
  });
}

function validateAgainst(schema, value) {
  const errors = [];
  walk(schema, value, "$", errors);
  return errors;
}

function walk(schema, value, path, errors) {
  if (!schema || typeof schema !== "object") return;
  const t = schema.type;
  if (Array.isArray(t)) {
    if (!t.some((tt) => typeMatches(tt, value))) errors.push(path + ": expected " + t.join("|"));
    return;
  }
  if (typeof t === "string" && !typeMatches(t, value)) {
    errors.push(path + ": expected " + t + ", got " + jsType(value));
    return;
  }
  if (schema.enum && schema.enum.indexOf(value) === -1) {
    errors.push(path + ": not in enum");
  }
  if (t === "object" && schema.properties && value && typeof value === "object") {
    for (const r of schema.required || []) {
      if (!(r in value)) errors.push(path + "." + r + ": missing");
    }
    for (const k of Object.keys(schema.properties)) {
      if (k in value) walk(schema.properties[k], value[k], path + "." + k, errors);
    }
  }
  if (t === "array" && schema.items && Array.isArray(value)) {
    value.forEach((v, i) => walk(schema.items, v, path + "[" + i + "]", errors));
  }
}

function typeMatches(t, v) {
  if (t === "null") return v === null;
  if (t === "string") return typeof v === "string";
  if (t === "boolean") return typeof v === "boolean";
  if (t === "integer") return Number.isInteger(v);
  if (t === "number") return typeof v === "number";
  if (t === "array") return Array.isArray(v);
  if (t === "object") return v !== null && typeof v === "object" && !Array.isArray(v);
  return true;
}

function jsType(v) {
  if (v === null) return "null";
  if (Array.isArray(v)) return "array";
  return typeof v;
}

function buildExampleParams(schema) {
  const example = exampleFor(schema || { type: "object" });
  const body = {
    jsonrpc: "2.0",
    method: "message/send",
    params: {
      message: {
        role: "user",
        parts: [
          { kind: "data", data: example },
        ],
        messageId: "dev-example",
      },
    },
    id: "dev-1",
  };
  return JSON.stringify(body, null, 2);
}

function exampleFor(schema) {
  if (!schema || typeof schema !== "object") return null;
  if (schema.enum && schema.enum.length) return schema.enum[0];
  const t = Array.isArray(schema.type) ? schema.type.find((x) => x !== "null") : schema.type;
  if (t === "string") return schema.format === "email" ? "user@example.com" : "";
  if (t === "integer" || t === "number") return 0;
  if (t === "boolean") return false;
  if (t === "array") return schema.items ? [exampleFor(schema.items)] : [];
  if (t === "object" || (schema.properties && !t)) {
    const obj = {};
    for (const k of Object.keys(schema.properties || {})) obj[k] = exampleFor(schema.properties[k]);
    return obj;
  }
  return null;
}

function escape(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function truncate(s, n) {
  s = String(s);
  return s.length > n ? s.slice(0, n - 1) + "…" : s;
}

function formatBytes(n) {
  if (n < 1024) return n + " B";
  if (n < 1024 * 1024) return (n / 1024).toFixed(1) + " KB";
  return (n / (1024 * 1024)).toFixed(2) + " MB";
}

function showToast(msg) {
  let el = document.querySelector(".toast");
  if (!el) {
    el = document.createElement("div");
    el.className = "toast";
    document.body.appendChild(el);
  }
  el.textContent = msg;
  el.classList.add("show");
  clearTimeout(el._t);
  el._t = setTimeout(() => el.classList.remove("show"), 1800);
}
"""
