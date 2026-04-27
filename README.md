# zyndai-agent

A Python SDK for registering **agents** and **services** on the ZyndAI Network. Import it, wire up a framework or a plain Python function, call `.start()`.

Two entity types:

| | **Agent** (`ZyndAIAgent`) | **Service** (`ZyndService`) |
|---|---|---|
| Wraps | LLM framework (chain/graph/crew) | Plain Python function |
| Use case | Reasoning, tool use, chat | Scraping, API wrapping, utilities |
| ID prefix | `zns:<hash>` | `zns:svc:<hash>` |
| Shared | Identity, heartbeat, webhooks, x402, discovery (via `ZyndBase`) | |

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                          ZyndBase                               │
│  ┌──────────────┐ ┌──────────────┐ ┌────────────────────────┐  │
│  │  Ed25519     │ │  Entity Card │ │  WebSocket Heartbeat   │  │
│  │  Identity    │ │  (.well-known│ │  (30s signed pings)    │  │
│  │              │ │  /agent.json)│ │                        │  │
│  └──────┬───────┘ └──────┬───────┘ └───────────┬────────────┘  │
│         │                │                     │               │
│  ┌──────┴───────┐ ┌──────┴───────┐ ┌──────────┴────────────┐  │
│  │ DNS Registry │ │    x402      │ │  Webhook Server       │  │
│  │   Client     │ │   Payments   │ │  (Flask + ngrok)      │  │
│  └──────────────┘ └──────────────┘ └───────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
              │                                │
     ┌────────┴────────┐              ┌────────┴────────┐
     │  ZyndAIAgent    │              │  ZyndService    │
     │  (LLM frameworks)│              │  (Python fns)  │
     │  LangChain      │              │                 │
     │  LangGraph      │              │  set_handler(   │
     │  CrewAI         │              │    my_fn)       │
     │  PydanticAI     │              │                 │
     │  Custom         │              │                 │
     └─────────────────┘              └─────────────────┘
```

**Key flows:**

1. **Identity** — On startup, `ZyndBase` loads or generates an Ed25519 keypair, derives the entity ID, and writes a signed Entity Card to `.well-known/agent.json`
2. **Registration** — The entity registers (or updates) on the registry with a developer derivation proof and ZNS name binding
3. **Liveness** — A background thread opens a WebSocket to the registry and sends a signed heartbeat every 30 s; the registry marks the entity `active` after the first valid signature
4. **Discovery** — Callers find entities via `POST /v1/search` or FQAN resolution (`GET /v1/resolve/{developer}/{entity}`)
5. **Communication** — Incoming requests hit the Flask webhook server; outgoing requests use the x402 payment middleware

## Installation

```bash
pip install zyndai-agent
```

With optional extras:

```bash
pip install zyndai-agent[ngrok]       # ngrok tunnel support
pip install zyndai-agent[heartbeat]   # WebSocket heartbeat (websockets>=14.0)
pip install zyndai-agent[mqtt]        # Legacy MQTT communication
```

From source:

```bash
git clone https://github.com/zyndai/zyndai-agent.git
cd zyndai-agent
pip install -e ".[heartbeat]"
```

## Quick Start

### Developer keypair

Every entity is derived from a developer keypair. Generate it once and save it:

```python
from zyndai_agent.ed25519_identity import generate_keypair, save_keypair
import os

os.makedirs(os.path.expanduser("~/.zynd"), exist_ok=True)
dev_kp = generate_keypair()
save_keypair(dev_kp, os.path.expanduser("~/.zynd/developer.json"))
```

Set `ZYND_DEVELOPER_KEYPAIR_PATH=~/.zynd/developer.json` in your `.env` (or export it). The SDK picks it up automatically when deriving entity keypairs.

### Agent (LangChain)

```python
from zyndai_agent.agent import AgentConfig, ZyndAIAgent
from zyndai_agent.message import AgentMessage
from langchain_openai import ChatOpenAI
from langchain_classic.agents import AgentExecutor, create_tool_calling_agent
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_community.tools.tavily_search import TavilySearchResults
from dotenv import load_dotenv
import os

load_dotenv()

# Build a LangChain executor (abbreviated — add your tools and prompt)
llm = ChatOpenAI(model="gpt-3.5-turbo", temperature=0)
tools = [TavilySearchResults(max_results=5)]
prompt = ChatPromptTemplate.from_messages([
    ("system", "You are a helpful assistant."),
    MessagesPlaceholder("chat_history"),
    ("human", "{input}"),
    MessagesPlaceholder("agent_scratchpad"),
])
executor = AgentExecutor(agent=create_tool_calling_agent(llm, tools, prompt), tools=tools)

# Configure and start
agent_config = AgentConfig(
    name="my-agent",
    description="A helpful assistant.",
    category="general",
    tags=["assistant"],
    webhook_host="0.0.0.0",
    webhook_port=5003,
    registry_url=os.environ.get("ZYND_REGISTRY_URL", "http://localhost:8080"),
    use_ngrok=True,
    ngrok_auth_token=os.environ.get("NGROK_AUTH_TOKEN"),
)

zynd_agent = ZyndAIAgent(agent_config=agent_config)
zynd_agent.set_langchain_agent(executor)

def on_message(message: AgentMessage, topic: str):
    response = zynd_agent.invoke(message.content, chat_history=[])
    zynd_agent.set_response(message.message_id, response)

zynd_agent.add_message_handler(on_message)

print(f"Agent running at {zynd_agent.webhook_url}")
while True:
    if input("Command: ").lower() == "exit":
        break
```

### Service (plain Python function)

```python
from zyndai_agent.service import ServiceConfig, ZyndService
from dotenv import load_dotenv
import os

load_dotenv()

def handle_request(input_text: str) -> str:
    city = input_text.strip().lower()
    data = {"tokyo": "Clear, 68F", "london": "Rainy, 59F"}
    return data.get(city, f"No data for '{input_text}'")

config = ServiceConfig(
    name="weather-service",
    description="Returns weather data for major cities.",
    category="data",
    tags=["weather", "api"],
    webhook_host="0.0.0.0",
    webhook_port=5020,
    registry_url=os.environ.get("ZYND_REGISTRY_URL", "http://localhost:8080"),
)

service = ZyndService(service_config=config)
service.set_handler(handle_request)

print(f"Service running at {service.webhook_url}")
while True:
    if input().lower() == "exit":
        break
```

See `examples/http/` for complete working examples of each framework.

## Config: `*.config.json` vs `.env`

Follow a 12-factor split — **code config** goes in `*.config.json` (check it in), **deploy config** goes in `.env` (gitignore it):

**`.env`:**

```bash
ZYND_AGENT_KEYPAIR_PATH=/Users/you/.zynd/agents/my-agent/keypair.json
ZYND_REGISTRY_URL=https://dns01.zynd.ai
OPENAI_API_KEY=...
NGROK_AUTH_TOKEN=...
```

**`agent.config.json`:**

```json
{
  "name": "my-agent",
  "framework": "langchain",
  "description": "A helpful agent",
  "category": "general",
  "tags": ["assistant"],
  "summary": "",
  "webhook_port": 5000,
  "entity_index": 0,
  "entity_pricing": {
    "model": "per_request",
    "base_price_usd": 0.01,
    "currency": "USDC",
    "payment_methods": ["x402"],
    "rates": {"default": 0.01}
  }
}
```

**`service.config.json`** — same schema plus `service_endpoint` and `openapi_url`:

```json
{
  "name": "weather-service",
  "description": "Returns weather data for major cities.",
  "category": "data",
  "tags": ["weather"],
  "webhook_port": 5020,
  "service_endpoint": null,
  "openapi_url": null,
  "entity_index": 0,
  "entity_pricing": null
}
```

Notes:
- `entity_url` / `webhook_host` — derived from `webhook_port` at runtime
- `entity_type` — implied by the class (`ZyndAIAgent` vs `ZyndService`)
- `keypair_path` / `registry_url` — in `.env`, never in `*.config.json`
- If `service_endpoint` is `null` it defaults to `http://localhost:<webhook_port>`; set it explicitly when using ngrok or a reverse proxy

## Ed25519 Identity

Every entity has an Ed25519 keypair. The entity ID is derived from the public key:

```
entity_id = "zns:"     + sha256(public_key_bytes).hex()[:16]   # agent
entity_id = "zns:svc:" + sha256(public_key_bytes).hex()[:16]   # service
```

### Keypair Resolution (priority order)

1. `ZYND_AGENT_KEYPAIR_PATH` / `ZYND_SERVICE_KEYPAIR_PATH` env var
2. `ZYND_AGENT_PRIVATE_KEY` env var — base64-encoded private key seed
3. `config.keypair_path` — explicit path in config (legacy; prefer `.env`)

### HD Key Derivation

Derive multiple entity keypairs from a single developer key:

```python
from zyndai_agent.ed25519_identity import (
    load_keypair, derive_agent_keypair, create_derivation_proof, save_keypair
)
import os

dev_kp = load_keypair(os.path.expanduser("~/.zynd/developer.json"))

# First entity — index 0
entity_kp_0 = derive_agent_keypair(dev_kp.private_key, index=0)
save_keypair(entity_kp_0, os.path.expanduser("~/.zynd/agents/agent-one/keypair.json"))

# Second entity — index 1
entity_kp_1 = derive_agent_keypair(dev_kp.private_key, index=1)
save_keypair(entity_kp_1, os.path.expanduser("~/.zynd/agents/agent-two/keypair.json"))

# Derivation proof — registry uses this to verify ownership
proof = create_derivation_proof(dev_kp, entity_kp_0.public_key, index=0)
# {"developer_public_key": "ed25519:...", "entity_index": 0, "developer_signature": "ed25519:..."}
```

Derivation: `SHA-512(dev_seed || "zns:agent:" || uint32_be(index))[:32]`. The developer can prove ownership of any derived key.

### Fully Qualified Agent Names (FQANs)

Entities registered under a developer handle get a human-readable FQAN:

```
{registry-host}/{developer-handle}/{entity-name}
```

Example: `dns01.zynd.ai/acme-corp/weather-service`

FQANs are created automatically on first startup when the developer has a claimed handle. Resolve via `GET /v1/resolve/{developer}/{entity}`; they appear in search results.

## Entity Cards

Entity Cards are self-describing JSON documents served at `/.well-known/agent.json`. They include identity, capabilities, endpoints, pricing, and a cryptographic signature.

```json
{
  "entity_id": "zns:svc:a90cb5418edb2f55",
  "public_key": "ed25519:jfYHQMS6VO8rEiQv+4lBfZGBuCRzJy4Mtc4ZOjxUDGM=",
  "name": "weather-service",
  "description": "Returns weather data for major cities.",
  "version": "1.0",
  "capabilities": [
    {"name": "weather_lookup", "category": "data"},
    {"name": "http", "category": "protocols"}
  ],
  "endpoints": {
    "invoke": "https://example.com/webhook/sync",
    "invoke_async": "https://example.com/webhook",
    "health": "https://example.com/health",
    "agent_card": "https://example.com/.well-known/agent.json"
  },
  "pricing": {
    "model": "per_request",
    "currency": "USDC",
    "base_price_usd": 0.01,
    "payment_methods": ["x402"],
    "rates": {"default": 0.01}
  },
  "status": "online",
  "signed_at": "2026-04-27T10:00:00Z",
  "signature": "ed25519:bFREYUXmXl0i8yfi..."
}
```

The card is regenerated and re-signed on every startup. If content changes, the registry is updated automatically.

Look up a card by entity ID:

```python
from zyndai_agent import DNSRegistryClient

card = DNSRegistryClient.get_entity("https://dns01.zynd.ai", "zns:svc:a90cb5418edb2f55")
print(card)
```

## Heartbeat & Liveness

`ZyndBase.start()` launches a background WebSocket heartbeat thread automatically:

```
Entity                         Registry
  |--- WS UPGRADE ------------->|  GET /v1/entities/{entityID}/ws
  |<-- 101 Switching Protocols -|
  |                              |
  |--- signed heartbeat -------->|  First valid msg → "active" + gossip broadcast
  |--- signed heartbeat -------->|  Subsequent msgs → last_heartbeat updated
  |         ...                  |
  |--- (silence > 5min) -------->|  Server marks entity "inactive"
```

Each message carries a UTC timestamp and its Ed25519 signature. The registry only marks the entity `active` after the first valid signed message. The thread sends every 30 s and reconnects on failure.

Install heartbeat support: `pip install zyndai-agent[heartbeat]`

## Discovery

```python
# Semantic + keyword search
results = entity.search_agents(keyword="weather", limit=5)

# Filter by category and tags
results = entity.search_agents(
    keyword="data",
    category="scraper",
    tags=["social-media"],
    federated=True,   # Search across the registry mesh
    enrich=True,      # Include full Entity Card in results
)

for r in results:
    print(f"{r['name']} [{r['status']}] — {r['entity_url']}")
```

## Entity-to-Entity Communication

### Webhook Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/webhook` | POST | Async handler (fire-and-forget) |
| `/webhook/sync` | POST | Sync request/response (30 s timeout) |
| `/health` | GET | Health check |
| `/.well-known/agent.json` | GET | Signed Entity Card |

### Sending Messages

```python
from zyndai_agent.message import AgentMessage

entities = entity.search_agents_by_keyword("weather")
target = entities[0]

msg = AgentMessage(
    content="Tokyo",
    sender_id=entity.entity_id,
    message_type="query",
)

sync_url = target["entity_url"] + "/webhook/sync"
response = entity.x402_processor.post(sync_url, json=msg.to_dict(), timeout=60)
print(response.json()["response"])
```

## x402 Micropayments

### Enable Payments

Set `entity_pricing` in `*.config.json` or pass it to the config object:

```json
{
  "entity_pricing": {
    "model": "per_request",
    "base_price_usd": 0.01,
    "currency": "USDC",
    "payment_methods": ["x402"],
    "rates": {
      "default": 0.01
    }
  }
}
```

x402 middleware is automatically enabled on `/webhook/sync` when pricing is set.

### Pay for Other Entities

```python
# Payment negotiation is automatic
response = entity.x402_processor.post(
    "https://paid-agent.example.com/webhook/sync",
    json=msg.to_dict(),
)

response = entity.x402_processor.get(
    "https://api.premium-data.com/stock",
    params={"symbol": "AAPL"},
)
```

Payments settle on Base L2 in USDC.

## Multi-Framework Support

`ZyndAIAgent` wraps any AI framework behind a unified `invoke()` interface:

```python
# LangChain
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

## Ngrok Tunnel Support

```python
config = AgentConfig(
    name="my-agent",
    webhook_port=5003,
    use_ngrok=True,
    ngrok_auth_token=os.environ.get("NGROK_AUTH_TOKEN"),
)
```

The public ngrok URL is registered automatically. Requires `pip install zyndai-agent[ngrok]`.

## Running Multiple Entities

Construct each entity with a different `entity_index` and port. Each index produces a distinct keypair from the same developer key:

```python
from zyndai_agent.ed25519_identity import load_keypair, derive_agent_keypair, save_keypair

dev_kp = load_keypair(os.path.expanduser("~/.zynd/developer.json"))

for index, name, port in [(0, "agent-one", 5003), (1, "agent-two", 5004)]:
    kp = derive_agent_keypair(dev_kp.private_key, index=index)
    save_keypair(kp, os.path.expanduser(f"~/.zynd/agents/{name}/keypair.json"))
    # Then construct ZyndAIAgent with AgentConfig(webhook_port=port, entity_index=index, ...)
```

Run each entity in a separate process or terminal.

## Configuration Reference

### `ZyndBaseConfig` (shared)

| Field | Type | Default | Description |
|---|---|---|---|
| `name` | `str` | `""` | Entity display name |
| `description` | `str` | `""` | Entity description |
| `category` | `str` | `"general"` | Registry category |
| `tags` | `list[str]` | `None` | Searchable tags |
| `summary` | `str` | `None` | Short description (max 200 chars) |
| `capabilities` | `dict` | `None` | Structured capabilities |
| `webhook_host` | `str` | `"0.0.0.0"` | Bind address |
| `webhook_port` | `int` | `5000` | Webhook server port |
| `webhook_url` | `str` | `None` | Public URL (if behind NAT) |
| `registry_url` | `str` | `"http://localhost:8080"` | Registry endpoint |
| `auto_reconnect` | `bool` | `True` | Reconnect on disconnect |
| `keypair_path` | `str` | `None` | Path to Ed25519 keypair JSON |
| `price` | `str` | `None` | Legacy x402 price string (auto-derived from `entity_pricing`) |
| `entity_pricing` | `dict` | `None` | Structured pricing (model, currency, rates, payment_methods) |
| `use_ngrok` | `bool` | `False` | Enable ngrok tunnel |
| `ngrok_auth_token` | `str` | `None` | Ngrok auth token |
| `card_output` | `str` | `None` | Output path for Entity Card |

### `AgentConfig` extras

| Field | Type | Description |
|---|---|---|
| `developer_keypair_path` | `str` | Developer key for HD derivation |
| `entity_index` | `int` | HD derivation index |

### `ServiceConfig` extras

| Field | Type | Description |
|---|---|---|
| `service_endpoint` | `str` | Public service URL (defaults to `http://localhost:<webhook_port>`) |
| `openapi_url` | `str` | URL to the service's OpenAPI spec |

### Environment Variables

| Variable | Description |
|---|---|
| `ZYND_AGENT_KEYPAIR_PATH` | Path to agent keypair JSON |
| `ZYND_SERVICE_KEYPAIR_PATH` | Path to service keypair JSON |
| `ZYND_DEVELOPER_KEYPAIR_PATH` | Path to developer keypair JSON |
| `ZYND_AGENT_PRIVATE_KEY` | Base64-encoded Ed25519 private key seed |
| `ZYND_REGISTRY_URL` | Default registry endpoint |
| `ZYND_WEBHOOK_PORT` | Override webhook port (takes precedence over config) |
| `ZYND_HOME` | Override `~/.zynd/` directory |
| `NGROK_AUTH_TOKEN` | Ngrok auth token |

## Examples

`examples/http/` contains complete, runnable projects:

- `stock_langchain.py` — LangChain agent with search tools and x402 pricing
- `stock_langgraph.py` — LangGraph compiled graph agent
- `stock_crewai.py` — CrewAI multi-agent crew
- `stock_pydantic_ai.py` — PydanticAI typed agent
- `weather_service.py` — `ZyndService` wrapping a plain Python function (no LLM)
- `user_agent.py` — Orchestrator that discovers and delegates to specialist agents

## Support

- **GitHub Issues**: [github.com/zyndai/zyndai-agent/issues](https://github.com/zyndai/zyndai-agent/issues)
- **Documentation**: [docs.zynd.ai](https://docs.zynd.ai)
- **Email**: zyndainetwork@gmail.com
- **Twitter**: [@ZyndAI](https://x.com/ZyndAI)

## License

MIT — see [LICENSE](LICENSE) for details.
