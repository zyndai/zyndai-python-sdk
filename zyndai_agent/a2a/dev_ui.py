"""Dev UI HTML generator — served at GET / when ZYND_DEV=1."""

from __future__ import annotations

import json
from typing import Any, Optional, Type

from pydantic import BaseModel


def _extract_fields(model: Optional[Type[BaseModel]]) -> list[dict[str, Any]]:
    """Return a list of {name, type, required} dicts from a Pydantic model's JSON schema."""
    if model is None:
        return [{"name": "input", "type": "string", "required": True}]
    try:
        schema = model.model_json_schema()
    except Exception:
        return [{"name": "input", "type": "string", "required": True}]

    properties = schema.get("properties", {})
    required_set = set(schema.get("required", []))
    fields = []
    for name, prop in properties.items():
        # Resolve $ref or anyOf/allOf to a simple type string
        field_type = _resolve_type(prop, schema)
        fields.append({
            "name": name,
            "type": field_type,
            "required": name in required_set,
        })
    return fields or [{"name": "input", "type": "string", "required": True}]


def _resolve_type(prop: dict[str, Any], root_schema: dict[str, Any]) -> str:
    # anyOf / allOf — pick first non-null type
    for key in ("anyOf", "allOf", "oneOf"):
        if key in prop:
            for sub in prop[key]:
                if sub.get("type") and sub["type"] != "null":
                    return sub["type"]
    # $ref
    if "$ref" in prop:
        ref = prop["$ref"].lstrip("#/").split("/")
        node = root_schema
        for part in ref:
            node = node.get(part, {})
        return node.get("type", "object")
    return prop.get("type", "string")


def _field_to_html(field: dict[str, Any]) -> str:
    name = field["name"]
    ftype = field["type"]
    required = field["required"]
    label_text = name + (" *" if required else "")
    fid = f"field-{name}"
    is_main = name in ("input", "message", "query", "prompt", "text")
    req_attr = "required" if required else ""

    if ftype == "boolean":
        return (
            f'<div class="field checkbox-field">'
            f'<label class="checkbox-label">'
            f'<input type="checkbox" id="{fid}" name="{name}"> {name}'
            f"</label></div>"
        )
    if ftype in ("object", "array"):
        return (
            f'<div class="field">'
            f'<label for="{fid}">{label_text}</label>'
            f'<textarea id="{fid}" name="{name}" rows="4" placeholder="[]"></textarea>'
            f"</div>"
        )
    if ftype in ("integer", "number"):
        return (
            f'<div class="field">'
            f'<label for="{fid}">{label_text}</label>'
            f'<input type="number" id="{fid}" name="{name}" {req_attr} placeholder="0">'
            f"</div>"
        )
    # string
    if is_main:
        return (
            f'<div class="field">'
            f'<label for="{fid}">{label_text}</label>'
            f'<textarea id="{fid}" name="{name}" rows="5" {req_attr} '
            f'placeholder="Enter your message…"></textarea>'
            f"</div>"
        )
    return (
        f'<div class="field">'
        f'<label for="{fid}">{label_text}</label>'
        f'<input type="text" id="{fid}" name="{name}" {req_attr} placeholder="{name}">'
        f"</div>"
    )


def build_dev_ui_html(
    agent_name: str,
    a2a_path: str,
    payload_model: Optional[Type[BaseModel]] = None,
) -> str:
    fields = _extract_fields(payload_model)
    form_fields_html = "\n".join(_field_to_html(f) for f in fields)
    fields_json = json.dumps(fields)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>[dev] {agent_name}</title>
<style>
  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
  :root {{
    --bg: #0f1117; --surface: #1a1d27; --border: #2a2d3a;
    --accent: #8b5cf6; --accent2: #06b6d4;
    --text: #e2e8f0; --muted: #64748b; --error: #f87171; --ok: #34d399;
    --font: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    --mono: 'Fira Code', 'Cascadia Code', monospace;
  }}
  body {{ background: var(--bg); color: var(--text); font-family: var(--font);
    min-height: 100vh; padding: 2rem 1rem; }}
  .container {{ max-width: 720px; margin: 0 auto; }}
  header {{ margin-bottom: 2rem; }}
  h1 {{ font-size: 1.5rem; font-weight: 700; display: flex; align-items: center; gap: .6rem; }}
  .badge {{ background: var(--accent); color: #fff; font-size: .65rem;
    padding: .2rem .5rem; border-radius: 999px; font-weight: 600;
    letter-spacing: .08em; text-transform: uppercase; }}
  .endpoint {{ color: var(--muted); font-size: .8rem; margin-top: .4rem;
    font-family: var(--mono); }}
  .card {{ background: var(--surface); border: 1px solid var(--border);
    border-radius: .75rem; padding: 1.5rem; margin-bottom: 1.5rem; }}
  .card-title {{ font-size: .7rem; font-weight: 600; text-transform: uppercase;
    letter-spacing: .1em; color: var(--muted); margin-bottom: 1rem; }}
  .field {{ display: flex; flex-direction: column; gap: .4rem; margin-bottom: 1rem; }}
  .field:last-child {{ margin-bottom: 0; }}
  label {{ font-size: .8rem; font-weight: 500; color: var(--muted); }}
  input[type=text], input[type=number], textarea {{
    background: var(--bg); border: 1px solid var(--border); border-radius: .4rem;
    color: var(--text); font-family: var(--font); font-size: .9rem;
    padding: .6rem .8rem; width: 100%; outline: none; resize: vertical; }}
  input:focus, textarea:focus {{ border-color: var(--accent); }}
  textarea {{ min-height: 80px; }}
  .checkbox-field {{ flex-direction: row; align-items: center; }}
  .checkbox-label {{ display: flex; align-items: center; gap: .5rem;
    font-size: .9rem; cursor: pointer; }}
  input[type=checkbox] {{ width: 1rem; height: 1rem; accent-color: var(--accent); }}
  button[type=submit] {{
    background: var(--accent); border: none; border-radius: .5rem;
    color: #fff; cursor: pointer; font-size: .9rem; font-weight: 600;
    padding: .7rem 1.4rem; width: 100%; margin-top: .5rem; transition: opacity .15s; }}
  button[type=submit]:hover {{ opacity: .85; }}
  button[type=submit]:disabled {{ opacity: .4; cursor: not-allowed; }}
  #resp {{ display: none; }}
  .resp-header {{ display: flex; align-items: center; justify-content: space-between;
    margin-bottom: .75rem; }}
  .status-ok  {{ color: var(--ok);  font-size: .8rem; font-weight: 600; }}
  .status-err {{ color: var(--error); font-size: .8rem; font-weight: 600; }}
  pre {{ background: var(--bg); border: 1px solid var(--border); border-radius: .4rem;
    color: var(--text); font-family: var(--mono); font-size: .8rem;
    overflow-x: auto; padding: 1rem; white-space: pre-wrap; word-break: break-word; }}
  .spinner {{ display: inline-block; width: .9rem; height: .9rem;
    border: 2px solid var(--border); border-top-color: var(--accent);
    border-radius: 50%; animation: spin .6s linear infinite; }}
  @keyframes spin {{ to {{ transform: rotate(360deg); }} }}
</style>
</head>
<body>
<div class="container">
  <header>
    <h1>{agent_name} <span class="badge">dev</span></h1>
    <p class="endpoint">POST {a2a_path} &nbsp;&middot;&nbsp; auth: open</p>
  </header>

  <div class="card">
    <p class="card-title">Request</p>
    <form id="form">
{form_fields_html}
      <button type="submit" id="btn">Send</button>
    </form>
  </div>

  <div class="card" id="resp">
    <div class="resp-header">
      <p class="card-title">Response</p>
      <span id="resp-status"></span>
    </div>
    <pre id="resp-body"></pre>
  </div>
</div>

<script>
const FIELDS = {fields_json};
const A2A_PATH = {json.dumps(a2a_path)};

document.getElementById('form').addEventListener('submit', async (e) => {{
  e.preventDefault();
  const form = e.target;
  const btn = document.getElementById('btn');
  const respDiv = document.getElementById('resp');
  const respStatus = document.getElementById('resp-status');
  const respBody = document.getElementById('resp-body');

  const values = {{}};
  for (const f of FIELDS) {{
    const el = form.elements[f.name];
    if (!el) continue;
    if (f.type === 'boolean') values[f.name] = el.checked;
    else if (f.type === 'integer' || f.type === 'number')
      values[f.name] = el.value === '' ? null : Number(el.value);
    else if (f.type === 'object' || f.type === 'array') {{
      try {{ values[f.name] = JSON.parse(el.value || 'null'); }} catch {{ values[f.name] = el.value; }}
    }} else {{
      values[f.name] = el.value;
    }}
  }}

  const mainField = FIELDS.find(f => ['input','message','query','prompt','text'].includes(f.name));
  const text = mainField ? (values[mainField.name] ?? '') : JSON.stringify(values);
  const otherValues = Object.fromEntries(Object.entries(values).filter(([k]) => k !== mainField?.name));
  const finalText = Object.keys(otherValues).length
    ? JSON.stringify({{ input: text, ...otherValues }})
    : String(text);

  const body = {{
    jsonrpc: '2.0',
    method: 'message/send',
    params: {{
      message: {{
        role: 'user',
        parts: [{{ kind: 'text', text: finalText }}],
        messageId: 'dev-' + Date.now(),
      }}
    }},
    id: 'dev-' + Date.now(),
  }};

  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span>';
  respDiv.style.display = 'none';

  try {{
    const res = await fetch(A2A_PATH, {{
      method: 'POST',
      headers: {{ 'Content-Type': 'application/json' }},
      body: JSON.stringify(body),
    }});
    const json = await res.json().catch(() => null);
    respDiv.style.display = 'block';
    respStatus.className = res.ok ? 'status-ok' : 'status-err';
    respStatus.textContent = res.ok ? '✓ ' + res.status : '✗ ' + res.status;
    respBody.textContent = JSON.stringify(json, null, 2);
  }} catch (err) {{
    respDiv.style.display = 'block';
    respStatus.className = 'status-err';
    respStatus.textContent = '✗ Network error';
    respBody.textContent = String(err);
  }} finally {{
    btn.disabled = false;
    btn.textContent = 'Send';
  }}
}});
</script>
</body>
</html>"""
