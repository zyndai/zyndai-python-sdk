"""
Simple agent using the typed message protocol.

Handles InvokeMessage requests and returns InvokeResponse with structured
results. Demonstrates the migration path from legacy string handlers to
typed message handlers with session support.
"""

import os
from dotenv import load_dotenv

load_dotenv()

from zyndai_agent import ZyndAIAgent, AgentConfig
from zyndai_agent.typed_messages import InvokeMessage, InvokeResponse, parse_message

agent = ZyndAIAgent(AgentConfig(
    name="echo-agent",
    description="Echoes back the received message with metadata",
    capabilities={"skills": ["echo", "ping"]},
    webhook_port=5010,
    registry_url=os.getenv("REGISTRY_URL", "https://registry.zynd.ai"),
))


def handle_message(message, topic, session):
    """3-arg handler: receives session automatically."""
    try:
        typed = parse_message(message.to_dict())
    except Exception:
        typed = None

    if isinstance(typed, InvokeMessage):
        result = {
            "echoed": typed.payload,
            "capability_requested": typed.capability,
            "session_messages": len(session.messages) if session else 0,
        }
        agent.set_response(message.message_id, str(result))
    else:
        agent.set_response(message.message_id, f"Echo: {message.content}")


agent.register_handler(handle_message)

print("\nEcho agent running. Send messages to test typed protocol.")
print("Press Ctrl+C to stop.\n")

try:
    import time
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    print("\nShutting down.")
