"""Dev UI HTML — served at GET / when ZYND_DEV=1."""

from __future__ import annotations

import json


def build_dev_ui_html(agent_name: str, a2a_path: str) -> str:
    a2a_path_json = json.dumps(a2a_path)

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
    --accent: #8b5cf6; --text: #e2e8f0; --muted: #64748b;
    --error: #f87171; --ok: #34d399;
    --font: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    --mono: 'Fira Code', 'Cascadia Code', monospace;
  }}
  body {{ background: var(--bg); color: var(--text); font-family: var(--font);
    min-height: 100vh; padding: 2rem 1rem; }}
  .container {{ max-width: 680px; margin: 0 auto; }}
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
  textarea {{
    background: var(--bg); border: 1px solid var(--border); border-radius: .4rem;
    color: var(--text); font-family: var(--font); font-size: .95rem;
    padding: .75rem .9rem; width: 100%; outline: none; resize: vertical;
    min-height: 120px; line-height: 1.5; }}
  textarea:focus {{ border-color: var(--accent); }}
  button {{
    background: var(--accent); border: none; border-radius: .5rem;
    color: #fff; cursor: pointer; font-size: .9rem; font-weight: 600;
    padding: .7rem 1.4rem; width: 100%; margin-top: .75rem; transition: opacity .15s; }}
  button:hover {{ opacity: .85; }}
  button:disabled {{ opacity: .4; cursor: not-allowed; }}
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
    <p class="card-title">Message</p>
    <textarea id="msg" placeholder="Enter your message…" autofocus></textarea>
    <button id="btn" onclick="send()">Send</button>
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
async function send() {{
  const msg = document.getElementById('msg').value.trim();
  if (!msg) return;

  const btn = document.getElementById('btn');
  const respDiv = document.getElementById('resp');
  const respStatus = document.getElementById('resp-status');
  const respBody = document.getElementById('resp-body');

  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span>';
  respDiv.style.display = 'none';

  const body = {{
    jsonrpc: '2.0',
    method: 'message/send',
    params: {{
      message: {{
        role: 'user',
        parts: [{{ kind: 'text', text: msg }}],
        messageId: 'dev-test',
      }}
    }},
    id: 'dev-1',
  }};

  try {{
    const res = await fetch({a2a_path_json}, {{
      method: 'POST',
      headers: {{ 'Content-Type': 'application/json' }},
      body: JSON.stringify(body),
    }});
    const json = await res.json().catch(() => null);
    respDiv.style.display = 'block';
    respStatus.className = res.ok ? 'status-ok' : 'status-err';
    respStatus.textContent = res.ok ? '\\u2713 ' + res.status : '\\u2717 ' + res.status;
    respBody.textContent = JSON.stringify(json, null, 2);
  }} catch (err) {{
    respDiv.style.display = 'block';
    respStatus.className = 'status-err';
    respStatus.textContent = '\\u2717 Network error';
    respBody.textContent = String(err);
  }} finally {{
    btn.disabled = false;
    btn.textContent = 'Send';
  }}
}}

document.getElementById('msg').addEventListener('keydown', (e) => {{
  if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) send();
}});
</script>
</body>
</html>"""
