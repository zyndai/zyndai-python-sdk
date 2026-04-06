"""
Single-capability worker agent.

Handles translation requests via the typed message protocol. Designed to
be discovered and called by coordinator agents via fan_out.
"""

import os
from dotenv import load_dotenv

load_dotenv()

from zyndai_agent import ZyndAIAgent, AgentConfig

agent = ZyndAIAgent(AgentConfig(
    name="translator",
    description="Translates text between languages",
    capabilities={"skills": ["translation", "language"]},
    category="language",
    tags=["translation", "multilingual"],
    webhook_port=5011,
    registry_url=os.getenv("REGISTRY_URL", "https://registry.zynd.ai"),
))


def handle_message(message, topic):
    """Process translation requests."""
    text = message.content
    metadata = message.metadata or {}
    target_lang = metadata.get("language", "French")

    # Placeholder translation (replace with actual translation logic)
    translated = f"[{target_lang}] {text}"

    agent.set_response(message.message_id, translated)


agent.register_handler(handle_message)

print("\nTranslator worker running.")
print("Press Ctrl+C to stop.\n")

try:
    import time
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    print("\nShutting down.")
