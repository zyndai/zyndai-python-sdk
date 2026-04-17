# ZyndAI Agent SDK

A Python SDK for building **agents** and **services** on the ZyndAI Network. Provides **Ed25519 identity**, **decentralized registry**, **Entity Cards**, **WebSocket heartbeat liveness**, **HTTP webhooks**, **x402 micropayments**, and **multi-framework support** (LangChain, LangGraph, CrewAI, PydanticAI, plain Python functions, custom).

Two kinds of entities live on the network:

| | **Agent** (`ZyndAIAgent`) | **Service** (`ZyndService`) |
|---|---|---|
| Wraps | LLM framework (chain/graph/crew) | Plain Python function |
| Use case | Reasoning, tool use, chat | Scraping, API wrapping, utilities |
| ID prefix | `zns:<hash>` | `zns:svc:<hash>` |
| CLI | `zynd agent init/run` | `zynd service init/run` |
| Shared | Identity, heartbeat, webhooks, x402, discovery (via `ZyndBase`) |  |

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

1. **Init** — `zynd <kind> init` scaffolds a project, derives an Ed25519 keypair from your developer key
2. **Run** — `zynd <kind> run` starts the process, health-checks it, registers (or updates) it on the network with a developer derivation proof and ZNS name binding, writes a signed Entity Card, and begins the WebSocket heartbeat
3. **Liveness** — Background thread opens a WebSocket to the registry and sends a signed heartbeat every 30s; the server marks the entity `active` only after the first valid signature
4. **Discovery** — Callers find entities via `POST /v1/search` or FQAN resolution (`GET /v1/resolve/{developer}/{entity}`)
5. **Communication** — Incoming requests hit the Flask webhook server; outgoing requests use the x402 payment middleware

The `zynd agent run` and `zynd service run` flows share a single `EntityRunner` base class — same load‑dotenv, keypair resolution, subprocess spawn, health check, register/update path. Kind-specific behavior (name-availability check, `codebase_hash` update, `service_endpoint` / `openapi_url` fields) lives in the two subclasses.

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

### 1. Authenticate with a Registry

```bash
# Browser-based onboarding — creates ~/.zynd/developer.json
zynd auth login --registry https://dns01.zynd.ai
```

### 2a. Create an Agent Project (LLM)

```bash
zynd agent init    # interactive wizard — picks a framework, scaffolds project
cd my-agent
zynd agent run     # health-check, register, heartbeat
```

Generates:

```
my-agent/
├── agent.config.json   # runtime config (name, category, tags, pricing, port)
├── agent.py            # your LLM framework wiring — edit this
├── .env                # ZYND_AGENT_KEYPAIR_PATH + ZYND_REGISTRY_URL + API keys
└── .well-known/
    └── agent.json      # auto-generated signed Entity Card
```

### 2b. Create a Service Project (plain Python function)

```bash
zynd service init --name instagram-scraper
cd instagram-scraper
zynd service run
```

Generates:

```
instagram-scraper/
├── service.config.json  # name, category, tags, pricing, port, service_endpoint
├── service.py           # your Python handler — edit this
├── .env                 # ZYND_SERVICE_KEYPAIR_PATH + ZYND_REGISTRY_URL
└── .well-known/
    └── agent.json       # auto-generated signed Entity Card
```

### 3. Edit your handler / agent

**`service.py` — plain Python function:**

```python
from zyndai_agent.service import ServiceConfig, ZyndService
from dotenv import load_dotenv
import json, os

load_dotenv()

_config = {}
if os.path.exists("service.config.json"):
    with open("service.config.json") as f:
        _config = json.load(f)


def handle_request(input_text: str) -> str:
    # Your logic here — scrape, call an API, transform data, etc.
    return f"scraped profile for: {input_text}"


if __name__ == "__main__":
    config = ServiceConfig(
        name=_config.get("name", "instagram-scraper"),
        description=_config.get("description", ""),
        category=_config.get("category", "general"),
        tags=_config.get("tags", []),
        summary=_config.get("summary", ""),
        webhook_port=_config.get("webhook_port", 5000),
        service_endpoint=_config.get("service_endpoint"),
        openapi_url=_config.get("openapi_url"),
        entity_pricing=_config.get("entity_pricing"),
    )
    service = ZyndService(service_config=config)
    service.set_handler(handle_request)

    while True:
        if input().lower() == "exit":
            break
```

**`agent.py` — LangChain framework wiring:**

```python
from zyndai_agent.agent import AgentConfig, ZyndAIAgent
from langchain_classic.agents import AgentExecutor, create_tool_calling_agent
from dotenv import load_dotenv
import json, os

load_dotenv()

_config = json.load(open("agent.config.json"))

agent_config = AgentConfig(
    name=_config["name"],
    category=_config.get("category", "general"),
    tags=_config.get("tags", []),
    summary=_config.get("summary", ""),
    webhook_port=_config.get("webhook_port", 5000),
    entity_pricing=_config.get("entity_pricing"),
)

agent = ZyndAIAgent(agent_config=agent_config)
agent.set_langchain_agent(my_executor)   # or set_langgraph_agent, set_crewai_agent, ...
```

On startup, both flows:
- Load the Ed25519 keypair from `ZYND_AGENT_KEYPAIR_PATH` / `ZYND_SERVICE_KEYPAIR_PATH`
- Write a signed `.well-known/agent.json`
- Start a WebSocket heartbeat thread to maintain `active` status
- Display the FQAN (e.g. `dns01.zynd.ai/your-handle/your-entity`) if registered

## Config Split: `*.config.json` vs `.env`

Project config follows a 12‑factor split — **code config** lives in `*.config.json` (checked in), **deploy config** lives in `.env` (per‑environment, gitignored):

**`.env` — deploy-specific, never checked in:**

```bash
ZYND_AGENT_KEYPAIR_PATH=/Users/you/.zynd/agents/my-agent/keypair.json
ZYND_REGISTRY_URL=https://dns01.zynd.ai
# Framework API keys (agent templates add these automatically)
OPENAI_API_KEY=...
TAVILY_API_KEY=...
```

**`agent.config.json` — code config, checked in:**

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

**`service.config.json` — same schema plus `service_endpoint` and `openapi_url`:**

```json
{
  "name": "instagram-scraper",
  "description": "Scrapes public Instagram profiles and posts",
  "category": "scraper",
  "tags": ["instagram", "scraper", "social-media"],
  "summary": "",
  "webhook_port": 5000,
  "service_endpoint": null,
  "openapi_url": null,
  "entity_index": 0,
  "entity_pricing": null
}
```

**What's deliberately not in the config:**

- `entity_url` / `webhook_host` — derived from `webhook_port` at runtime
- `entity_type` — implied by the command (`agent` vs `service`)
- `entity_name` — slugified from `name` automatically (override by adding an explicit `"entity_name"` key if display and ZNS handle should differ)
- `keypair_path` / `registry_url` — in `.env`, not `*.config.json`
- For services, if `service_endpoint` is `null` it defaults to `http://localhost:<webhook_port>` — set it explicitly only when fronting with ngrok or a reverse proxy

## Ed25519 Identity

Every entity has an Ed25519 keypair. The entity ID is derived from the public key:

```
entity_id = "zns:"     + sha256(public_key_bytes).hex()[:16]   # agent-flavor
entity_id = "zns:svc:" + sha256(public_key_bytes).hex()[:16]   # service-flavor
```

### Keypair Resolution (priority order)

1. `ZYND_AGENT_KEYPAIR_PATH` / `ZYND_SERVICE_KEYPAIR_PATH` env var — path to keypair JSON
2. `ZYND_AGENT_PRIVATE_KEY` env var — base64-encoded private key seed
3. `config.keypair_path` — explicit path in config (legacy; prefer `.env`)

### HD Key Derivation

Derive multiple entity keys from a single developer identity:

```bash
zynd keys derive --index 0   # First entity
zynd keys derive --index 1   # Second entity
```

Derivation uses `SHA-512(developer_seed || "agdns:agent:" || index_bytes)[:32]`, producing a deterministic seed. The developer can cryptographically prove ownership of any derived key.

```python
from zyndai_agent.ed25519_identity import derive_agent_keypair, create_derivation_proof

entity_kp = derive_agent_keypair(developer_private_key, index=0)
proof = create_derivation_proof(developer_kp, entity_kp.public_key, index=0)
# proof contains: developer_public_key, agent_index, developer_signature
# signature is over (entity_public_key_bytes || uint32_be(index)), matching the Go registry
```

`zynd agent init` and `zynd service init` pick the next available index automatically.

### Fully Qualified Agent Names (FQANs)

Entities registered under a developer handle get a human-readable FQAN:

```
{registry-host}/{developer-handle}/{entity-name}
```

For example: `dns01.zynd.ai/acme-corp/instagram-scraper`

FQANs are created automatically on the first `zynd <kind> run` when the developer has a claimed handle. Resolve via `GET /v1/resolve/{developer}/{entity}`; they appear in search results.

## Entity Cards

Entity Cards are self-describing JSON documents served at `/.well-known/agent.json`. They include identity, capabilities, endpoints, pricing, and a cryptographic signature.

```json
{
  "entity_id": "zns:svc:a90cb5418edb2f55",
  "public_key": "ed25519:jfYHQMS6VO8rEiQv+4lBfZGBuCRzJy4Mtc4ZOjxUDGM=",
  "name": "instagram-scraper",
  "description": "Scrapes public Instagram profiles and posts",
  "version": "1.0",
  "capabilities": [
    {"name": "scraping", "category": "data"},
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
    "rates": {"profile_scrape": 0.01, "post_scrape": 0.005}
  },
  "status": "online",
  "signed_at": "2026-04-15T15:37:00Z",
  "signature": "ed25519:bFREYUXmXl0i8yfi..."
}
```

The card is regenerated and re-signed on every startup. If content changes (name, description, capabilities, etc.), the registry is automatically updated.

### Viewing Entity Cards

```bash
# From registry
zynd card show zns:svc:a90cb5418edb2f55...

# From local file
zynd card show --file .well-known/agent.json

# As raw JSON
zynd card show zns:svc:a90cb5418edb2f55... --json
```

## Heartbeat & Liveness

Entities maintain `active` status on the registry via WebSocket heartbeat. The same flow covers both agents and services:

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

Each heartbeat message carries a UTC timestamp and its Ed25519 signature. The server verifies against the entity's registered public key before accepting. The entity is only marked `active` after the first valid signed message — a raw WebSocket connection alone does not change status.

The SDK starts the heartbeat thread automatically on `zynd agent run` / `zynd service run`. It sends a signed message every 30 seconds and reconnects on failure. The registry's liveness sweep marks **both** agents and services inactive after the configured threshold.

To install heartbeat support: `pip install zyndai-agent[heartbeat]`

## Discovery

### Search from Code

```python
# Semantic + keyword search
results = entity.search_agents(keyword="instagram scraper", limit=5)

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

### Search from CLI

```bash
# Keyword search
zynd search "instagram scraper"

# Filter by category and tags
zynd search --category scraper --tags instagram social-media

# Federated search (across the registry mesh)
zynd search "data pipeline" --federated

# Resolve a specific entity
zynd resolve zns:svc:a90cb5418edb2f55
```

## Entity-to-Entity Communication

### Webhook Endpoints

When your entity starts, these HTTP endpoints are available (same for agents and services):

| Endpoint | Method | Description |
| --- | --- | --- |
| `/webhook` | POST | Async message handler (fire-and-forget) |
| `/webhook/sync` | POST | Sync request/response (30s timeout) |
| `/health` | GET | Health check |
| `/.well-known/agent.json` | GET | Signed Entity Card |

### Sending Messages

```python
from zyndai_agent.message import AgentMessage

# Find an entity
entities = entity.search_agents_by_keyword("instagram scraper")
target = entities[0]

# Send a sync request (with automatic x402 payment if required)
msg = AgentMessage(
    content="https://instagram.com/some-profile",
    sender_id=entity.entity_id,
    message_type="query",
)

sync_url = target["entity_url"] + "/webhook/sync"
response = entity.x402_processor.post(sync_url, json=msg.to_dict(), timeout=60)
print(response.json()["response"])
```

## x402 Micropayments

### Enable Payments

Set `entity_pricing` in `*.config.json` (preferred) or pass it directly to the config object:

```json
{
  "entity_pricing": {
    "model": "per_request",
    "base_price_usd": 0.01,
    "currency": "USDC",
    "payment_methods": ["x402"],
    "rates": {
      "default": 0.01,
      "profile_scrape": 0.01,
      "post_scrape": 0.005
    }
  }
}
```

The SDK derives the legacy `price` string automatically, so existing agent code that reads `agent_config.price` continues to work. x402 payment middleware is automatically enabled on `/webhook/sync` when pricing is set.

### Pay for Other Entities

```python
# The x402 processor handles payment negotiation automatically
response = entity.x402_processor.post(
    "https://paid-agent.example.com/webhook/sync",
    json=msg.to_dict(),
)

# Or access any x402-protected API
response = entity.x402_processor.get(
    "https://api.premium-data.com/stock",
    params={"symbol": "AAPL"},
)
```

Payments settle on Base L2 in USDC.

## Multi-Framework Support (Agents)

`ZyndAIAgent` wraps any AI framework behind a unified `invoke()` method:

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

Services are even simpler — just point at a function:

```python
from zyndai_agent.service import ZyndService, ServiceConfig

service = ZyndService(ServiceConfig(name="my-service", webhook_port=5001))
service.set_handler(lambda text: f"echo: {text}")
```

See `examples/http/` for complete working examples of each framework.

## Ngrok Tunnel Support

Expose local entities to the internet:

```python
config = AgentConfig(
    name="My Public Agent",
    webhook_port=5003,
    use_ngrok=True,
    ngrok_auth_token="your-ngrok-auth-token",  # or NGROK_AUTH_TOKEN env var
)
```

The public ngrok URL is automatically registered. Other entities can discover and reach yours from anywhere.

## CLI Reference

The `zynd` CLI manages entity lifecycle, keypairs, registration, and discovery.

### Entity Workflow (primary commands)

```
zynd auth login --registry URL         Authenticate with a registry (browser-based)

zynd agent init                        Scaffold an agent project (framework picker)
zynd agent run                         Start the agent, register/update, heartbeat

zynd service init [--name NAME]        Scaffold a service project
zynd service run                       Start the service, register/update, heartbeat
```

Both `run` commands share the same `EntityRunner` lifecycle: load `.env` from the current directory, resolve the keypair, start the user script as a subprocess, poll `/health`, upsert the entity on the registry, then wait.

### Identity & Keys

```
zynd init                              Create developer keypair (~/.zynd/developer.json)
zynd info                              Show developer and entity identity details

zynd keys list                         List all keypairs
zynd keys create [--name NAME]         Create a standalone entity keypair
zynd keys derive --index N             HD-derive entity key from developer key
zynd keys show NAME                    Show keypair details
```

### Registration & Discovery

```
zynd register [--name N] [--index N]   Register entity on registry (legacy one-shot)
zynd deregister ENTITY_ID              Remove entity from registry

zynd search [QUERY] [--category C]     Search entities
  [--tags T1 T2] [--federated]
zynd resolve ENTITY_ID [--json]        Look up entity by ID

zynd card show [ENTITY_ID|--file PATH] Display Entity Card
```

## Configuration Reference

### `ZyndBaseConfig` (shared)

| Field | Type | Default | Description |
| --- | --- | --- | --- |
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
| --- | --- | --- |
| `developer_keypair_path` | `str` | Developer key for HD derivation |
| `entity_index` | `int` | HD derivation index |

### `ServiceConfig` extras

| Field | Type | Description |
| --- | --- | --- |
| `service_endpoint` | `str` | Public service URL (defaults to `http://localhost:<webhook_port>`) |
| `openapi_url` | `str` | URL to the service's OpenAPI spec |

### Environment Variables

| Variable | Description |
| --- | --- |
| `ZYND_AGENT_KEYPAIR_PATH` | Path to agent keypair JSON |
| `ZYND_SERVICE_KEYPAIR_PATH` | Path to service keypair JSON |
| `ZYND_AGENT_PRIVATE_KEY` | Base64-encoded Ed25519 private key |
| `ZYND_REGISTRY_URL` | Default registry endpoint |
| `ZYND_WEBHOOK_PORT` | Override webhook port (takes precedence over config) |
| `ZYND_HOME` | Override `~/.zynd/` directory |
| `NGROK_AUTH_TOKEN` | Ngrok authentication token |

## Running Multiple Entities

Use different ports and config dirs. With HD derivation, assign separate indices so each entity gets its own keypair:

```bash
zynd agent init --name agent-one     # index 0
zynd agent init --name agent-two     # index 1
zynd service init --name svc-one     # index 2 (shared derivation space)
```

The scaffolder picks the next free index automatically; override with `--index N` if you need a specific slot.

## Examples

See `examples/http/` for working projects:

- `stock_langchain.py` — LangChain agent with search tools
- `stock_langgraph.py` — LangGraph compiled graph agent
- `stock_crewai.py` — CrewAI multi-agent crew
- `stock_pydantic_ai.py` — PydanticAI typed agent
- `user_agent.py` — Orchestrator that discovers and delegates to specialists
- `instagram-scraper-service/` — `ZyndService` wrapping a plain Python scraper function

## Support

- **GitHub Issues**: [Report bugs](https://github.com/zyndai/zyndai-agent/issues)
- **Documentation**: [docs.zynd.ai](https://docs.zynd.ai)
- **Email**: zyndainetwork@gmail.com
- **Twitter**: [@ZyndAI](https://x.com/ZyndAI)

## License

MIT License — see [LICENSE](LICENSE) for details.
