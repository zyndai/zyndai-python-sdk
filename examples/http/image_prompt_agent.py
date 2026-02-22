"""
Image + Prompt Agent Example
============================

Demonstrates how to build an agent that accepts image URLs alongside text prompts,
calls a dummy vision API, and returns both a text response and an output image URL.

This example shows the USER-LEVEL approach: images are passed via the `metadata` field
of AgentMessage, which works today without any SDK changes.

Features:
- Multimodal image + text processing
- Ngrok tunnel support for public access

Architecture:
  User Agent --> POST /webhook (prompt + image_url in metadata)
      --> Image Prompt Agent receives message
      --> Calls dummy vision API with image + prompt
      --> Returns response text + output image URL

Usage:
  1. Start this agent:     python examples/http/image_prompt_agent.py
  2. Send a request from another agent or curl:

     curl -X POST http://localhost:5000/webhook/sync \\
       -H "Content-Type: application/json" \\
       -d '{
         "content": "Describe what you see in this image",
         "sender_id": "user-agent",
         "message_type": "query",
         "metadata": {
           "images": [
             "https://upload.wikimedia.org/wikipedia/commons/thumb/a/a7/Camponotus_flavomarginatus_ant.jpg/320px-Camponotus_flavomarginatus_ant.jpg"
           ]
         }
       }'

Running multiple agents on the same machine:
    # Terminal 1 - Image agent on port 5000
    python examples/http/image_prompt_agent.py

    # Terminal 2 - LangChain agent on port 5003
    python examples/http/stock_langchain.py

    # Terminal 3 - User agent on port 5004
    python examples/http/user_agent.py

    Each agent gets its own ngrok tunnel and public URL automatically.
"""

import os
import json
import time
import requests as http_requests
from dotenv import load_dotenv
from zyndai_agent.agent import AgentConfig, ZyndAIAgent
from zyndai_agent.message import AgentMessage

load_dotenv()


# ---------------------------------------------------------------------------
# Dummy Vision API — replace with your real API (OpenAI, Replicate, etc.)
# ---------------------------------------------------------------------------


def call_dummy_vision_api(prompt: str, image_urls: list[str]) -> dict:
    """
    Simulates calling a vision API that takes a prompt + images and returns
    a text description and an output image URL.

    In production, replace this with:
      - OpenAI GPT-4o: openai.chat.completions.create(model="gpt-4o", messages=[...])
      - Replicate: replicate.run("model", input={...})
      - Any other vision API
    """
    print(f"  [Dummy API] Processing prompt: '{prompt}'")
    print(f"  [Dummy API] Input images: {image_urls}")

    # Simulate API processing time
    time.sleep(0.5)

    # Return a dummy response
    return {
        "description": f"I analyzed {len(image_urls)} image(s) with prompt '{prompt}'. "
        f"The image appears to show interesting visual content. "
        f"(This is a dummy response — replace call_dummy_vision_api with a real API.)",
        "output_image_url": "https://placehold.co/600x400/png?text=Processed+Output+Image",
        "model": "dummy-vision-v1",
        "input_image_count": len(image_urls),
    }


# ---------------------------------------------------------------------------
# Message Handler — processes incoming image+prompt requests
# ---------------------------------------------------------------------------


def handle_image_prompt_message(zynd_agent: ZyndAIAgent):
    """
    Returns a handler function that:
    1. Extracts prompt from message.content
    2. Extracts image URLs from message.metadata["images"]
    3. Calls the dummy vision API
    4. Returns the response text + output image URL via set_response()
    """

    def handler(message: AgentMessage, topic):
        print(f"\n--- Received image+prompt request ---")
        print(f"  Sender:  {message.sender_id}")
        print(f"  Prompt:  {message.content}")

        # Extract image URLs from metadata
        images = []
        if message.metadata and isinstance(message.metadata, dict):
            images = message.metadata.get("images", [])

        if not images:
            print("  WARNING: No images provided in metadata.images")

        print(f"  Images:  {images}")

        # Call the vision API
        try:
            api_result = call_dummy_vision_api(
                prompt=message.content,
                image_urls=images,
            )

            # Build response with both text and image output
            response = json.dumps(
                {
                    "text": api_result["description"],
                    "output_images": [api_result["output_image_url"]],
                    "model": api_result["model"],
                    "input_image_count": api_result["input_image_count"],
                }
            )

            print(f"  Response: {response[:100]}...")

        except Exception as e:
            response = json.dumps(
                {
                    "error": str(e),
                    "text": f"Failed to process image: {e}",
                }
            )
            print(f"  ERROR: {e}")

        # Set the response for sync webhook
        zynd_agent.set_response(message.message_id, response)

    return handler


# ---------------------------------------------------------------------------
# Main — start the image+prompt agent
# ---------------------------------------------------------------------------


def main():
    # Agent configuration with ngrok tunnel
    config = AgentConfig(
        name="ImagePromptAgent",
        description="An agent that accepts image URLs + text prompts, processes them via a vision API, and returns text + image responses.",
        capabilities={
            "ai": ["vision", "image-analysis", "multimodal"],
            "data": ["image-processing"],
        },
        webhook_port=5000,
        api_key=os.environ.get("ZYND_API_KEY", "your-api-key"),
        registry_url=os.environ.get("ZYND_REGISTRY_URL", "https://registry.zynd.ai"),
        # Enable ngrok to expose this agent publicly (requires: pip install zyndai-agent[ngrok])
        # Each agent on a different port gets its own ngrok tunnel URL
        use_ngrok=True,
        ngrok_auth_token=os.environ.get(
            "NGROK_AUTH_TOKEN"
        ),  # Or set globally via: ngrok config add-authtoken <token>
    )

    # Initialize agent
    zynd_agent = ZyndAIAgent(config)

    # Use a custom agent that handles image+prompt
    def invoke_with_images(input_text: str) -> str:
        """
        Custom invoke that handles plain text queries.
        Image+prompt messages come through the webhook handler instead.
        """
        result = call_dummy_vision_api(prompt=input_text, image_urls=[])
        return result["description"]

    zynd_agent.set_custom_agent(invoke_with_images)

    # Register the image+prompt webhook handler for sync requests
    zynd_agent.register_handler(handle_image_prompt_message(zynd_agent))

    print("\nImage+Prompt Agent is running!")
    print("Send requests with image URLs in metadata.images field.")
    print("\nExample curl command:")
    print(f"""
  curl -X POST {zynd_agent.webhook_url.replace("/webhook", "/webhook/sync")} \\
    -H "Content-Type: application/json" \\
    -d '{{
      "content": "Describe what you see in this image",
      "sender_id": "test-user",
      "metadata": {{
        "images": [
          "https://upload.wikimedia.org/wikipedia/commons/thumb/a/a7/Camponotus_flavomarginatus_ant.jpg/320px-Camponotus_flavomarginatus_ant.jpg"
        ]
      }}
    }}'
""")

    # Keep the agent running
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nShutting down...")
        zynd_agent.stop_webhook_server()


if __name__ == "__main__":
    main()
