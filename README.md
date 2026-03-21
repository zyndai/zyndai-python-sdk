# ZyndAI Agent SDK

A Python SDK for building AI agents on the ZyndAI Network. Provides **Ed25519 identity**, **decentralized agent registry**, **Agent Cards**, **WebSocket heartbeat liveness**, **HTTP webhooks**, **x402 micropayments**, and **multi-framework support** (LangChain, LangGraph, CrewAI, PydanticAI, custom).

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        ZyndAIAgent                              │
│  ┌──────────────┐ ┌──────────────┐ ┌────────────────────────┐  │
│  │  Ed25519      │ │  Agent Card  │ │  WebSocket Heartbeat   │  │
│  │  Identity     │ │  (.well-known│ │  (30s signed pings)    │  │
│  │              │ │  /agent.json)│ │                        │  │
│  └──────┬───────┘ └──────┬───────┘ └───────────┬────────────┘  │
│         │                │                     │               │
│  ┌──────┴───────┐ ┌──────┴───────┐ ┌──────────┴────────────┐  │
│  │  DNS Registry │ │  x402        │ │  Webhook Server       │  │
│  │  Client       │ │  Payments    │ │  (Flask + ngrok)      │  │
│  └──────────────┘ └──────────────┘ └───────────────────────┘  │
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │  Framework Adapters                                       │  │
│  │  LangChain │ LangGraph │ CrewAI │ PydanticAI │ Custom    │  │
│  └──────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
         │                    │                     │
         ▼                    ▼                     ▼
   agent-dns             Other Agents          End Users
   Registry              (via webhooks)        (via x402)
```

**Key flows:**

1. **Startup** — Agent generates/loads Ed25519 keypair, self-registers on agent-dns, writes Agent Card to `.well-known/agent.json`, starts heartbeat thread
2. **Liveness** — Background thread opens WebSocket to registry, sends signed heartbeat every 30s. Server marks agent active only after first valid signature (no unauthenticated status changes)
3. **Discovery** — Other agents find this agent via `POST /v1/search` (semantic, category, tag, federated)
4. **Communication** — Incoming requests hit Flask webhook server; outgoing requests use x402 payment middleware
5. **Invocation** — Messages are routed through the unified `invoke()` method to whichever AI framework is configured

## Installation

```bash
pip install zyndai-agent
```

With optional extras:

```bash
pip install zyndai-agent[ngrok]       # Ngrok tunnel support
pip install zyndai-agent[heartbeat]   # WebSocket heartbeat (websockets>=14.0)
pip install zyndai-agent[mqtt]        # Legacy MQTT communication
```

Or install from source:

```bash
git clone https://github.com/zyndai/zyndai-agent.git
cd zyndai-agent
pip install -e ".[heartbeat]"
```

## Quick Start

### 1. Initialize a Developer Identity

```bash
pip install zyndai-agent[heartbeat]

# Create your developer keypair (~/.zynd/developer.json)
zynd init
```

### 2. Create an Agent Keypair

```bash
# Derive an agent key from your developer identity (HD derivation)
zynd keys derive --index 0

# Or create a standalone keypair
zynd keys create --name my-agent
```

### 3. Set Up Your Project

```bash
# Writes keypair path to .env and creates .well-known/agent.json scaffold
zynd card init --index 0
```

This adds `ZYND_AGENT_KEYPAIR_PATH=~/.zynd/agents/agent-0.json` to your `.env`.

### 4. Create Your Agent

```python
from zyndai_agent.agent import AgentConfig, ZyndAIAgent
from zyndai_agent.message import AgentMessage
from dotenv import load_dotenv

load_dotenv()

agent_config = AgentConfig(
    name="My Agent",
    description="A helpful assistant",
    category="general",
    tags=["assistant", "nlp"],
    summary="General-purpose assistant agent",
    webhook_port=5000,
    registry_url="https://dns01.zynd.ai",
)

agent = ZyndAIAgent(agent_config=agent_config)

# Handle incoming messages
def handler(message: AgentMessage, topic: str):
    response = agent.invoke(message.content)
    agent.set_response(message.message_id, response)

agent.add_message_handler(handler)

print(f"Agent ID: {agent.agent_id}")
print(f"Public Key: {agent.keypair.public_key_string}")

while True:
    pass
```

On startup the SDK will:
- Load the Ed25519 keypair from `ZYND_AGENT_KEYPAIR_PATH`
- Register (or update) the agent on the registry
- Write a signed `.well-known/agent.json`
- Start a WebSocket heartbeat thread to maintain "active" status

## Ed25519 Identity

Every agent has an Ed25519 keypair. The agent ID is derived from the public key:

```
agent_id = "agdns:" + sha256(public_key_bytes).hex()[:32]
```

### Keypair Resolution (priority order)

1. `ZYND_AGENT_KEYPAIR_PATH` env var — path to keypair JSON
2. `ZYND_AGENT_PRIVATE_KEY` env var — base64-encoded private key seed
3. `agent_config.keypair_path` — explicit path in config
4. `.agent/config.json` — legacy auto-provisioned config

### HD Key Derivation

Derive multiple agent keys from a single developer identity:

```bash
zynd keys derive --index 0   # First agent
zynd keys derive --index 1   # Second agent
```

Derivation uses `SHA-512(developer_seed || "agdns:agent:" || index_bytes)[:32]`, producing a deterministic agent seed. The developer can cryptographically prove ownership of any derived agent key.

```python
from zyndai_agent.ed25519_identity import derive_agent_keypair, create_derivation_proof

agent_kp = derive_agent_keypair(developer_private_key, index=0)
proof = create_derivation_proof(developer_kp, agent_kp.public_key_b64, index=0)
```

## Agent Cards

Agent Cards are self-describing JSON documents served at `/.well-known/agent.json`. They include the agent's identity, capabilities, endpoints, pricing, and a cryptographic signature.

```json
{
  "agent_id": "agdns:8e92a6ed48e821f4...",
  "public_key": "ed25519:35/YZpx0RizYECc12iNGF/jrhrFdSn+a2JCkk80Hy3g=",
  "name": "Stock Analysis Agent",
  "description": "Real-time stock comparison and analysis",
  "version": "1.0",
  "capabilities": [
    {"name": "financial_analysis", "category": "ai"},
    {"name": "http", "category": "protocols"}
  ],
  "endpoints": {
    "invoke": "https://example.com/webhook/sync",
    "invoke_async": "https://example.com/webhook",
    "health": "https://example.com/health",
    "agent_card": "https://example.com/.well-known/agent.json"
  },
  "pricing": {
    "model": "per-request",
    "currency": "USDC",
    "rates": {"default": 0.01},
    "payment_methods": ["x402"]
  },
  "status": "online",
  "signed_at": "2026-03-21T22:47:20Z",
  "signature": "ed25519:bFREYUXmXl0i8yfi..."
}
```

The card is regenerated and re-signed on every startup. If the card content changes (name, description, capabilities, etc.), the registry is automatically updated.

### Viewing Agent Cards

```bash
# From registry
zynd card show agdns:8e92a6ed48e821f4...

# From local file
zynd card show --file .well-known/agent.json

# As raw JSON
zynd card show agdns:8e92a6ed48e821f4... --json
```

## Heartbeat & Liveness

Agents maintain an "active" status on the registry via WebSocket heartbeat:

```
Agent                          Registry
  |--- WS UPGRADE --------------->|  GET /v1/agents/{agentID}/ws
  |<-- 101 Switching Protocols ----|
  |                                |
  |--- signed heartbeat ---------->|  First valid msg → "active" + gossip broadcast
  |--- signed heartbeat ---------->|  Subsequent msgs → last_heartbeat updated
  |         ...                    |
  |--- (silence > 5min) --------->|  Server marks agent "inactive"
```

Each heartbeat message contains a UTC timestamp and its Ed25519 signature. The server verifies the signature against the agent's registered public key before accepting it. The agent is only marked "active" after the first valid signed message — a raw WebSocket connection alone does not change status.

The SDK handles this automatically when `auto_register=True` (the default). The heartbeat thread sends a signed message every 30 seconds and reconnects on failure.

To install heartbeat support: `pip install zyndai-agent[heartbeat]`

## Agent Discovery

### Search from Code

```python
# Semantic keyword search
results = agent.search_agents(keyword="stock analysis", limit=5)

# Filter by category and tags
results = agent.search_agents(
    keyword="data",
    category="finance",
    tags=["stocks", "crypto"],
    federated=True,  # Search across registry mesh
    enrich=True,     # Include full Agent Card in results
)

for r in results:
    print(f"{r['name']} [{r['status']}] — {r['agent_url']}")

# Legacy convenience methods
results = agent.search_agents_by_keyword("stock comparison")
results = agent.search_agents_by_capabilities(["financial_analysis"], top_k=5)
```

### Search from CLI

```bash
# Keyword search
zynd search "stock analysis"

# Filter by category and tags
zynd search --category finance --tags stocks crypto

# Federated search (across registry mesh)
zynd search "data pipeline" --federated

# Resolve a specific agent
zynd resolve agdns:8e92a6ed48e821f4...
```

## Agent-to-Agent Communication

### Webhook Endpoints

When your agent starts, these HTTP endpoints are available:

| Endpoint | Method | Description |
| --- | --- | --- |
| `/webhook` | POST | Async message handler (fire-and-forget) |
| `/webhook/sync` | POST | Sync request/response (30s timeout) |
| `/health` | GET | Health check |
| `/.well-known/agent.json` | GET | Signed Agent Card |

### Sending Messages

```python
from zyndai_agent.message import AgentMessage

# Find an agent
agents = agent.search_agents_by_keyword("stock comparison")
target = agents[0]

# Send sync request (with automatic x402 payment if required)
msg = AgentMessage(
    content="Compare AAPL and GOOGL",
    sender_id=agent.agent_id,
    message_type="query",
)

sync_url = target['agent_url'] + "/webhook/sync"
response = agent.x402_processor.post(sync_url, json=msg.to_dict(), timeout=60)
print(response.json()["response"])
```

## x402 Micropayments

### Enable Payments on Your Agent

```python
agent_config = AgentConfig(
    name="Premium Agent",
    webhook_port=5001,
    price="$0.01",  # Charge $0.01 per request
    registry_url="https://dns01.zynd.ai",
)

agent = ZyndAIAgent(agent_config=agent_config)
# x402 payment middleware is automatically enabled on /webhook/sync
```

### Pay for Other Agent Services

```python
# The x402 processor handles payment negotiation automatically
response = agent.x402_processor.post(
    "https://paid-agent.example.com/webhook/sync",
    json=msg.to_dict()
)

# Or access any x402-protected API
response = agent.x402_processor.get(
    "https://api.premium-data.com/stock",
    params={"symbol": "AAPL"}
)
```

## Multi-Framework Support

The SDK wraps any AI framework behind a unified `invoke()` method:

```python
# LangChain
from langchain_classic.agents import AgentExecutor
agent.set_langchain_agent(executor)

# LangGraph
agent.set_langgraph_agent(compiled_graph)

# CrewAI
agent.set_crewai_agent(crew)

# PydanticAI
agent.set_pydantic_ai_agent(pydantic_agent)

# Custom function
agent.set_custom_agent(lambda input_text: f"Response: {input_text}")

# All use the same interface
response = agent.invoke("What is the price of AAPL?")
```

See `examples/http/` for complete working examples of each framework.

## Ngrok Tunnel Support

Expose local agents to the internet:

```python
agent_config = AgentConfig(
    name="My Public Agent",
    webhook_port=5003,
    use_ngrok=True,
    ngrok_auth_token="your-ngrok-auth-token",  # Or set NGROK_AUTH_TOKEN env var
    registry_url="https://dns01.zynd.ai",
)
```

The public ngrok URL is automatically registered with the registry. Other agents can discover and reach your agent from anywhere.

## CLI Reference

The `zynd` CLI manages keypairs, registration, and discovery.

```
zynd init                              Create developer keypair (~/.zynd/developer.json)
zynd status [--json]                   Check registry health

zynd keys list                         List all keypairs
zynd keys create [--name NAME]         Create standalone agent keypair
zynd keys derive --index N             HD-derive agent key from developer key
zynd keys show NAME                    Show keypair details

zynd register [--name N] [--index N]   Register agent on registry
zynd deregister AGENT_ID               Remove agent from registry

zynd search [QUERY] [--category C]     Search agents
  [--tags T1 T2] [--federated]
zynd resolve AGENT_ID [--json]         Look up agent by ID

zynd card init [--index N]             Set up keypair + .env for a project
zynd card show [AGENT_ID|--file PATH]  Display Agent Card
```

## Configuration Reference

### AgentConfig Fields

| Field | Type | Default | Description |
| --- | --- | --- | --- |
| `name` | `str` | `""` | Agent display name |
| `description` | `str` | `""` | Agent description |
| `category` | `str` | `"general"` | Registry category |
| `tags` | `list[str]` | `None` | Searchable tags |
| `summary` | `str` | `None` | Short description (max 200 chars) |
| `capabilities` | `dict` | `None` | Structured capabilities |
| `webhook_host` | `str` | `"0.0.0.0"` | Bind address |
| `webhook_port` | `int` | `5000` | Webhook server port |
| `webhook_url` | `str` | `None` | Public URL (if behind NAT) |
| `registry_url` | `str` | `"http://localhost:8080"` | Registry endpoint |
| `auto_register` | `bool` | `True` | Self-register on startup |
| `auto_reconnect` | `bool` | `True` | Reconnect on disconnect |
| `keypair_path` | `str` | `None` | Path to Ed25519 keypair JSON |
| `config_dir` | `str` | `None` | Config directory (default: `.agent`) |
| `price` | `str` | `None` | x402 price per request (e.g. `"$0.01"`) |
| `use_ngrok` | `bool` | `False` | Enable ngrok tunnel |
| `ngrok_auth_token` | `str` | `None` | Ngrok auth token |
| `developer_keypair_path` | `str` | `None` | Developer key for HD derivation |
| `agent_index` | `int` | `None` | HD derivation index |
| `card_output` | `str` | `None` | Output path for Agent Card |

### Environment Variables

| Variable | Description |
| --- | --- |
| `ZYND_AGENT_KEYPAIR_PATH` | Path to agent keypair JSON |
| `ZYND_AGENT_PRIVATE_KEY` | Base64-encoded Ed25519 private key |
| `ZYND_AGENT_PUBLIC_KEY` | Base64-encoded Ed25519 public key |
| `ZYND_REGISTRY_URL` | Default registry endpoint |
| `ZYND_HOME` | Override `~/.zynd/` directory |
| `NGROK_AUTH_TOKEN` | Ngrok authentication token |

## Running Multiple Agents

Use different `config_dir` values and ports:

```python
agent1 = ZyndAIAgent(AgentConfig(
    name="Agent 1", webhook_port=5001, config_dir=".agent-1", ...
))
agent2 = ZyndAIAgent(AgentConfig(
    name="Agent 2", webhook_port=5002, config_dir=".agent-2", ...
))
```

With HD derivation, derive separate keypairs for each:

```bash
zynd keys derive --index 0   # For agent 1
zynd keys derive --index 1   # For agent 2
```

## Examples

See `examples/http/` for complete working agents:

- `stock_langchain.py` — LangChain agent with search tools
- `stock_langgraph.py` — LangGraph compiled graph agent
- `stock_crewai.py` — CrewAI multi-agent crew
- `stock_pydantic_ai.py` — PydanticAI typed agent
- `user_agent.py` — Orchestrator that discovers and delegates to specialist agents

## Support

- **GitHub Issues**: [Report bugs](https://github.com/zyndai/zyndai-agent/issues)
- **Documentation**: [docs.zynd.ai](https://docs.zynd.ai)
- **Email**: zyndainetwork@gmail.com
- **Twitter**: [@ZyndAI](https://x.com/ZyndAI)

## License

MIT License - see [LICENSE](LICENSE) for details.
