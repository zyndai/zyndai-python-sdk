# Third Party Licenses and Attributions

This document acknowledges the open-source projects and protocols used in the ZyndAI Agent SDK.

## AG-UI Protocol

**License**: Apache License 2.0
**Source**: https://github.com/ag-ui/protocol
**Description**: The AG-UI Protocol defines a standard for generative UI event streaming via Server-Sent Events (SSE). It enables agents to stream real-time UI updates including text messages, tool calls, state changes, and custom widgets.

### AG-UI Event Types

The AG-UI protocol defines the following event types, all of which are supported by the ZyndAI Agent SDK:

- **RUN_STARTED** — Indicates the agent has started processing a task
- **TEXT_MESSAGE_CONTENT** — Streaming text responses from the agent
- **TOOL_CALL_START** — Agent is calling an external tool or function
- **TOOL_CALL_END** — Tool call has completed with a result
- **STATE_DELTA** — Incremental state update (JSON Patch format)
- **STATE_SNAPSHOT** — Full state snapshot at a point in time
- **CUSTOM** — Custom widget rendering (charts, forms, approvals, etc.)
- **RUN_FINISHED** — Agent has completed processing
- **RUN_ERROR** — Agent encountered an error

### Usage in ZyndAI Agent SDK

The AG-UI Protocol is integrated into the ZyndAI Agent SDK through:

1. **`zyndai_agent.ui.emitter.UIEmitter`** — Provides async methods to emit AG-UI events
2. **`zyndai_agent.ui.sse.SSEHandler`** — Handles Server-Sent Events transport
3. **`/ui/stream/<conversation_id>`** — Flask route that streams events to clients
4. **`zyndai_agent.ui.metrics`** — Tracks AG-UI streaming metrics

### Configuration

Agents enable AG-UI streaming by setting `generative_ui=True` in `AgentConfig`:

```python
config = AgentConfig(
    name="My Agent",
    generative_ui=True,  # Enable AG-UI streaming
)
```

When enabled, agents can use the `ui` parameter in message handlers:

```python
@agent.register_handler
async def handle_message(message, ui):
    await ui.text("Processing...")
    await ui.custom("chart", {...})
    await ui.run_finished()
```

## x402 Protocol

**License**: BSD 3-Clause (as part of the x402 library)
**Source**: https://github.com/x402/x402
**Description**: x402 implements HTTP 402 Payment Required micropayments for agent-to-agent communication on EVM blockchains (Base Sepolia, Ethereum, etc.).

## Flask

**License**: BSD 3-Clause
**Source**: https://github.com/pallets/flask
**Description**: Used for the embedded webhook server that receives incoming agent messages and streams AG-UI events.

## Pydantic

**License**: MIT
**Source**: https://github.com/pydantic/pydantic
**Description**: Used for runtime type validation and configuration management in the SDK.

## Dependencies

### Core Dependencies

- **flask** — Web framework for webhook server
- **pydantic** — Data validation and configuration
- **requests** — HTTP client for agent-to-agent calls
- **x402** — HTTP 402 micropayment support
- **ag-ui-protocol** — AG-UI event type definitions (optional, installed with `pip install zyndai-agent[ui]`)
- **sseclient-py** — Server-Sent Events client (optional, used by OpenClaw skill)

### Optional Features

- **pyngrok** — ngrok tunnel support for public webhook exposure (install with `pip install zyndai-agent[ngrok]`)

## License Compliance

All dependencies are used in compliance with their respective licenses. The ZyndAI Agent SDK itself is distributed under the Apache License 2.0.

For questions about license compatibility or third-party usage, please refer to the main LICENSE file in the repository root.

## Attribution

This SDK is built upon the open-source agent ecosystem and incorporates designs from:

- Apache License 2.0 projects (AG-UI Protocol, ZyndAI platform)
- MIT licensed projects (Pydantic, various utilities)
- BSD 3-Clause licensed projects (Flask, x402)

We are grateful to the open-source community for these foundational projects.
