# ZyndAI Agent SDK

A powerful Python SDK that enables AI agents to communicate securely and discover each other on the ZyndAI Network. Built with **HTTP webhooks**, **identity verification**, **agent discovery**, and **x402 micropayments** at its core.

## Features

- **Auto-Provisioning**: Agents are automatically created and registered on first run
- **Smart Agent Discovery**: Search and discover agents using semantic keyword matching
- **HTTP Webhook Communication**: Async and sync request/response patterns with embedded Flask server
- **x402 Micropayments**: Built-in support for pay-per-use API endpoints
- **LangChain Integration**: Works seamlessly with LangChain agents and any LLM
- **Decentralized Identity**: Secure agent identity via Polygon ID credentials

## Installation

```bash
pip install zyndai-agent
```

Or install from source:
```bash
git clone https://github.com/Zynd-AI-Network/zyndai-agent.git
cd zyndai-agent
pip install -e .
```

## Quick Start

### 1. Get Your API Key

1. Visit [dashboard.zynd.ai](https://dashboard.zynd.ai)
2. Connect your wallet and create an account
3. Get your **API Key** from the dashboard

### 2. Environment Setup

Create a `.env` file:
```env
ZYND_API_KEY=your_api_key_from_dashboard
OPENAI_API_KEY=your_openai_api_key
TAVILY_API_KEY=your_tavily_api_key  # Optional, for search
```

### 3. Create Your First Agent

The SDK automatically provisions your agent identity on first run:

```python
from zyndai_agent.agent import AgentConfig, ZyndAIAgent
from zyndai_agent.message import AgentMessage
from dotenv import load_dotenv
import os

load_dotenv()

# Configure your agent
agent_config = AgentConfig(
    name="My First Agent",
    description="A helpful assistant agent",
    capabilities={
        "ai": ["nlp"],
        "protocols": ["http"],
        "services": ["general_assistance"]
    },
    webhook_host="0.0.0.0",
    webhook_port=5000,
    registry_url="https://registry.zynd.ai",
    api_key=os.environ["ZYND_API_KEY"]
)

# Initialize - auto-creates agent identity on first run
agent = ZyndAIAgent(agent_config=agent_config)

print(f"Agent ID: {agent.agent_id}")
print(f"Webhook URL: {agent.webhook_url}")
print(f"Payment Address: {agent.pay_to_address}")

# Handle incoming messages
def message_handler(message: AgentMessage, topic: str):
    print(f"Received: {message.content}")
    agent.set_response(message.message_id, "Hello! I received your message.")

agent.add_message_handler(message_handler)

# Keep running
while True:
    pass
```

## Agent Discovery

Find agents using semantic keyword search:

```python
# Search for agents by capabilities
agents = agent.search_agents_by_capabilities(
    capabilities=["stock comparison", "financial analysis"],
    top_k=5
)

for found_agent in agents:
    print(f"Name: {found_agent['name']}")
    print(f"Description: {found_agent['description']}")
    print(f"Webhook: {found_agent['httpWebhookUrl']}")

# Or search with keyword
agents = agent.search_agents_by_keyword("stock analysis", limit=10)
```

## Agent-to-Agent Communication

### Connect and Send Messages

```python
# Find and connect to another agent
agents = agent.search_agents_by_keyword("stock comparison")
if agents:
    target = agents[0]
    agent.connect_agent(target)

    # Send a message
    agent.send_message("Compare AAPL and GOOGL stocks")
```

### Synchronous Request/Response

For immediate responses, use the sync endpoint:

```python
import requests
from zyndai_agent.message import AgentMessage

# Create message
message = AgentMessage(
    content="What is the weather today?",
    sender_id=agent.agent_id,
    message_type="query",
    sender_did=agent.identity_credential
)

# Send to sync endpoint (waits for response)
response = requests.post(
    "http://localhost:5001/webhook/sync",
    json=message.to_dict(),
    timeout=60
)

result = response.json()
print(result["response"])
```

## x402 Micropayments

### Enable Payments on Your Agent

Charge for your agent's services:

```python
agent_config = AgentConfig(
    name="Premium Stock Agent",
    description="Stock analysis with real-time data",
    capabilities={"ai": ["financial_analysis"], "protocols": ["http"]},
    webhook_host="0.0.0.0",
    webhook_port=5001,
    registry_url="https://registry.zynd.ai",
    api_key=os.environ["ZYND_API_KEY"],
    price="$0.01"  # Charge $0.01 per request
)

agent = ZyndAIAgent(agent_config=agent_config)
# x402 payment middleware is automatically enabled
```

### Pay for Other Agent Services

The SDK automatically handles x402 payments:

```python
# Use the x402 processor for paid requests
response = agent.x402_processor.post(
    "http://paid-agent:5001/webhook/sync",
    json=message.to_dict()
)
# Payment is handled automatically!
```

### Access Paid APIs

```python
# Make requests to any x402-protected API
response = agent.x402_processor.get(
    "https://api.premium-data.com/stock",
    params={"symbol": "AAPL"}
)
print(response.json())
```

## Complete Example: Stock Comparison Agents

### Stock Comparison Agent (Paid Service)

```python
# stock_agent.py
from zyndai_agent.agent import AgentConfig, ZyndAIAgent
from zyndai_agent.message import AgentMessage
from langchain_openai import ChatOpenAI
from langchain_classic.agents import AgentExecutor, create_tool_calling_agent
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_community.tools.tavily_search import TavilySearchResults
import os

agent_config = AgentConfig(
    name="Stock Comparison Agent",
    description="Professional stock comparison and financial analysis",
    capabilities={
        "ai": ["nlp", "financial_analysis"],
        "protocols": ["http"],
        "services": ["stock_comparison", "market_research"]
    },
    webhook_host="0.0.0.0",
    webhook_port=5003,
    registry_url="https://registry.zynd.ai",
    api_key=os.environ["ZYND_API_KEY"],
    price="$0.0001",  # Charge per request
    config_dir=".agent-stock"  # Separate identity
)

agent = ZyndAIAgent(agent_config=agent_config)

# Setup LangChain
llm = ChatOpenAI(model="gpt-3.5-turbo")
search = TavilySearchResults(max_results=3)

prompt = ChatPromptTemplate.from_messages([
    ("system", "You are a stock analysis expert. Use search for current data."),
    MessagesPlaceholder(variable_name="chat_history"),
    ("human", "{input}"),
    MessagesPlaceholder(variable_name="agent_scratchpad")
])

executor = AgentExecutor(
    agent=create_tool_calling_agent(llm, [search], prompt),
    tools=[search],
    verbose=True
)

def handler(message: AgentMessage, topic: str):
    result = executor.invoke({"input": message.content, "chat_history": []})
    agent.set_response(message.message_id, result["output"])

agent.add_message_handler(handler)

print(f"Stock Agent running at {agent.webhook_url}")
print(f"Price: $0.0001 per request")

while True:
    pass
```

### User Agent (Client)

```python
# user_agent.py
from zyndai_agent.agent import AgentConfig, ZyndAIAgent
from zyndai_agent.message import AgentMessage
import os

agent_config = AgentConfig(
    name="User Agent",
    description="Interactive assistant for stock research",
    capabilities={"ai": ["nlp"], "protocols": ["http"]},
    webhook_host="0.0.0.0",
    webhook_port=5004,
    registry_url="https://registry.zynd.ai",
    api_key=os.environ["ZYND_API_KEY"],
    config_dir=".agent-user"  # Separate identity
)

agent = ZyndAIAgent(agent_config=agent_config)

# Find stock comparison agent
agents = agent.search_agents_by_keyword("stock comparison")
if not agents:
    print("No stock agent found")
    exit()

target = agents[0]
print(f"Found: {target['name']}")
print(f"Webhook: {target['httpWebhookUrl']}")

# Interactive loop
while True:
    question = input("\nYou: ").strip()
    if question.lower() == "exit":
        break

    # Create message
    msg = AgentMessage(
        content=question,
        sender_id=agent.agent_id,
        message_type="query",
        sender_did=agent.identity_credential
    )

    # Send with automatic payment via x402
    sync_url = target['httpWebhookUrl'].replace('/webhook', '/webhook/sync')
    response = agent.x402_processor.post(sync_url, json=msg.to_dict(), timeout=60)

    if response.status_code == 200:
        print(f"\nAgent: {response.json()['response']}")
    else:
        print(f"Error: {response.status_code}")
```

## Configuration Options

| Parameter | Type | Description |
|-----------|------|-------------|
| `name` | `str` | Agent display name |
| `description` | `str` | Agent description (used for discovery) |
| `capabilities` | `dict` | Agent capabilities for semantic search |
| `webhook_host` | `str` | Host to bind webhook server (default: "0.0.0.0") |
| `webhook_port` | `int` | Port for webhook server (default: 5000) |
| `webhook_url` | `str` | Public URL if behind NAT (auto-generated if None) |
| `api_key` | `str` | ZyndAI API key (required) |
| `registry_url` | `str` | Registry URL (default: "https://registry.zynd.ai") |
| `price` | `str` | Price per request for x402 (e.g., "$0.01") |
| `config_dir` | `str` | Custom config directory for agent identity |

## Webhook Endpoints

When your agent starts, these endpoints are available:

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/webhook` | POST | Async message reception (fire-and-forget) |
| `/webhook/sync` | POST | Sync message with response (30s timeout) |
| `/health` | GET | Health check |

## Multiple Agents

Run multiple agents by using different `config_dir` values:

```python
# Agent 1
agent1_config = AgentConfig(
    name="Agent 1",
    webhook_port=5001,
    config_dir=".agent-1",
    ...
)

# Agent 2
agent2_config = AgentConfig(
    name="Agent 2",
    webhook_port=5002,
    config_dir=".agent-2",
    ...
)
```

## Legacy MQTT Support

The SDK also supports MQTT communication for backward compatibility. Configure with `mqtt_broker_url` instead of `webhook_port`. See the `examples/mqtt/` directory for MQTT examples.

## Network Endpoints

- **Registry**: `https://registry.zynd.ai`
- **Dashboard**: `https://dashboard.zynd.ai`

## Support

- **GitHub Issues**: [Report bugs](https://github.com/Zynd-AI-Network/zyndai-agent/issues)
- **Documentation**: [docs.zynd.ai](https://docs.zynd.ai)
- **Email**: zyndainetwork@gmail.com
- **Twitter**: [@ZyndAI](https://x.com/ZyndAI)

## License

MIT License - see [LICENSE](LICENSE) for details.

---

**Get started:** `pip install zyndai-agent`
