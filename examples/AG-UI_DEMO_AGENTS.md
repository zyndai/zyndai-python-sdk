# AG-UI Demo Agents

Three production-ready reference agents demonstrating AG-UI streaming capabilities.

## Quick Start

### 1. Stock Ticker Agent

Real-time stock chart streaming with live market data.

```bash
python examples/stock_ticker_agent.py
```

**What it does:**
- Fetches 3-month historical stock data from Yahoo Finance
- Streams status updates
- Renders line chart via Recharts (CUSTOM: chart widget)
- Streams price metrics and analysis

**Test:**
```bash
curl -X POST http://localhost:5000/webhook/sync \
  -H 'Content-Type: application/json' \
  -d '{
    "content": "AAPL",
    "sender_id": "test",
    "conversation_id": "stock-demo-1"
  }'
```

**Stream at:** `http://localhost:5000/ui/stream/stock-demo-1`

---

### 2. Researcher Agent

Research assistant with live citations and tool calls.

```bash
python examples/researcher_agent.py
```

**What it does:**
- Accepts research queries
- Emits TOOL_CALL events (search_hector_rag)
- Streams citations one-by-one as TEXT_MESSAGE
- Updates state snapshot with results

**Test:**
```bash
curl -X POST http://localhost:5001/webhook/sync \
  -H 'Content-Type: application/json' \
  -d '{
    "content": "quantum computing",
    "sender_id": "test",
    "conversation_id": "research-demo-1"
  }'
```

**Stream at:** `http://localhost:5001/ui/stream/research-demo-1`

---

### 3. Form Filler Agent

Dynamic forms with validation and approval workflows.

```bash
python examples/form_filler_agent.py
```

**What it does:**
- Streams form widget (CUSTOM: form) with validation
- Shows approval widget (CUSTOM: approval) example
- Demonstrates STATE_DELTA updates
- Handles form submission simulation

**Test Form:**
```bash
curl -X POST http://localhost:5002/webhook/sync \
  -H 'Content-Type: application/json' \
  -d '{
    "content": "show form",
    "sender_id": "test",
    "conversation_id": "form-demo-1"
  }'
```

**Test Approval:**
```bash
curl -X POST http://localhost:5002/webhook/sync \
  -H 'Content-Type: application/json' \
  -d '{
    "content": "approve",
    "sender_id": "test",
    "conversation_id": "form-demo-2"
  }'
```

**Stream at:**
- Form: `http://localhost:5002/ui/stream/form-demo-1`
- Approval: `http://localhost:5002/ui/stream/form-demo-2`

---

## AG-UI Event Flow

Each agent demonstrates the full event lifecycle:

1. **RUN_STARTED** — Task begins
2. **TEXT_MESSAGE_CONTENT** — Streaming text updates
3. **TOOL_CALL_* ** — Tool invocations (researcher only)
4. **CUSTOM** — Widget rendering (chart, form, approval)
5. **STATE_DELTA** / **STATE_SNAPSHOT** — State updates
6. **RUN_FINISHED** — Task complete with elapsed time

---

## Watching Live Streams

### Option 1: Dashboard Browser (if connected)
Navigate to `/agents/[id]/stream` in the dashboard.

### Option 2: Direct Stream URL
Open SSE stream directly:
```bash
curl -N http://localhost:5000/ui/stream/stock-demo-1 | jq .
```

---

## Widget Reference

### Chart Widget
```javascript
await ui.custom("chart", {
  type: "line",      // or "bar", "area"
  title: "Title",
  data: [{...}],     // Array of objects
  dataKey: "value",  // Field to plot
  xAxis: "name",     // X-axis field
  height: 400,
})
```

### Form Widget
```javascript
await ui.custom("form", {
  title: "Form Title",
  description: "Instructions",
  fields: [
    {
      name: "field_id",
      label: "Display Label",
      type: "text|email|number|checkbox|select|textarea",
      required: true,
      placeholder: "...",
      options: [{label, value}],  // For select
    }
  ],
  submitLabel: "Submit",
  cancelLabel: "Cancel",
})
```

### Approval Widget
```javascript
await ui.custom("approval", {
  title: "Decision Required",
  description: "Review below",
  details: {...},  // Key-value pairs
  approveLabel: "Approve",
  rejectLabel: "Reject",
  requireReason: true,
})
```

---

## Dependencies

```bash
# Install AG-UI SDK
pip install zyndai-agent[ui]

# Install demo dependencies
pip install yfinance requests
```

---

## Production Deployment

Each agent can be deployed independently:

1. **Register on registry:** POST /agents with `generativeUi: true`
2. **Expose webhook:** Use ngrok or reverse proxy for public URL
3. **Dashboard discovery:** Agent appears in /agents list with "Try Live" button
4. **Stream anywhere:** Dashboard, n8n, MCP, or custom client

---

## Troubleshooting

**Form submission doesn't trigger workflow:**
- Forms are rendered UI-only in this demo. Real integration requires webhook handler that processes the `onSubmit` callback.

**Chart not rendering:**
- Ensure Recharts is installed in dashboard: `npm install recharts`

**Hector-rag service unreachable:**
- Researcher agent gracefully falls back to mock data if service is down.

**Stream times out at 5 minutes:**
- Timeout is configurable in `AGUIClient` (dashboard) or SDK config.

---

## Next Steps

1. **Customize widgets** — Fork these agents, add your own logic
2. **Integrate with workflows** — Call from n8n, LangGraph, etc.
3. **Add to marketplace** — Register on agent registry for discovery
4. **Deploy to production** — Use Docker + reverse proxy

See [AG-UI Integration Plan](../../AG-UI-INTEGRATION-PLAN.md) for architecture overview.
