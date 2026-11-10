"""
Text Transform Service — Example Zynd Service wrapping a utility API

Shows how to wrap an existing function/API as a Zynd service.
Agents on the network can discover and call this service to transform text.
"""

from zyndai_agent.service import ServiceConfig, ZyndService
from dotenv import load_dotenv
import json
import os

load_dotenv()


def handle_request(input_text: str) -> str:
    """
    Transform text based on a command.

    Accepts JSON: {"command": "uppercase|lowercase|reverse|wordcount", "text": "..."}
    Or plain text (defaults to uppercase).
    """
    try:
        req = json.loads(input_text)
        command = req.get("command", "uppercase")
        text = req.get("text", "")
    except (json.JSONDecodeError, TypeError):
        command = "uppercase"
        text = input_text

    if command == "uppercase":
        return json.dumps({"result": text.upper()})
    elif command == "lowercase":
        return json.dumps({"result": text.lower()})
    elif command == "reverse":
        return json.dumps({"result": text[::-1]})
    elif command == "wordcount":
        words = len(text.split())
        chars = len(text)
        return json.dumps({"words": words, "characters": chars})
    elif command == "slug":
        import re
        slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
        return json.dumps({"result": slug})
    else:
        return json.dumps({
            "error": f"Unknown command: {command}",
            "available": ["uppercase", "lowercase", "reverse", "wordcount", "slug"],
        })


if __name__ == "__main__":
    config = ServiceConfig(
        name="Text Transform Service",
        description="Text transformation utilities — uppercase, lowercase, reverse, word count, slugify.",
        capabilities={
            "protocols": ["http"],
            "services": ["text_transform"],
        },
        category="developer-tools",
        tags=["text", "transform", "utility"],
        summary="Stateless text transformation API: uppercase, lowercase, reverse, wordcount, slug.",
        webhook_host="0.0.0.0",
        webhook_port=5021,
        registry_url=os.environ.get("ZYND_REGISTRY_URL", "http://localhost:8080"),
    )

    service = ZyndService(service_config=config)
    service.set_handler(handle_request)

    print(f"\nText Transform Service is running")
    print(f"Webhook: {service.webhook_url}")
    print("Type 'exit' to quit\n")

    while True:
        cmd = input()
        if cmd.lower() == "exit":
            break
