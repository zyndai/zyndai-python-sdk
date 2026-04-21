"""
__SERVICE_NAME__ — Service on Zynd Network
"""

from zyndai_agent.service import ServiceConfig, ZyndService

from payload import RequestPayload, ResponsePayload, MAX_FILE_SIZE_BYTES

from dotenv import load_dotenv
import json
import os

load_dotenv()

_config = {}
if os.path.exists("service.config.json"):
    with open("service.config.json") as _f:
        _config = json.load(_f)


def handle_request(input_text: str) -> str:
    """
    Your service logic here.

    This function is called for every incoming request.
    It receives the request content as a string and should return
    the response as a string.

    Replace this with your own implementation.
    """
    return f"Hello from __SERVICE_NAME__! You sent: {input_text}"


if __name__ == "__main__":
    config = ServiceConfig(
        name=_config.get("name", "__SERVICE_NAME__"),
        description=_config.get("description", ""),
        capabilities=_config.get("capabilities"),
        category=_config.get("category", "general"),
        tags=_config.get("tags", []),
        summary=_config.get("summary", "__SERVICE_NAME__ service"),
        service_endpoint=_config.get("service_endpoint"),
        openapi_url=_config.get("openapi_url"),
        webhook_host="0.0.0.0",
        webhook_port=_config.get("webhook_port", 5000),
        registry_url=os.environ.get(
            "ZYND_REGISTRY_URL",
            _config.get("registry_url", "http://localhost:8080"),
        ),
        keypair_path=os.environ.get(
            "ZYND_SERVICE_KEYPAIR_PATH",
            _config.get("keypair_path"),
        ),
        entity_url=os.environ.get("ZYND_ENTITY_URL", _config.get("entity_url")),
        price=_config.get("price"),
        entity_pricing=_config.get("entity_pricing"),
    )

    service = ZyndService(
        service_config=config,
        payload_model=RequestPayload,
        output_model=ResponsePayload,
        max_file_size_bytes=MAX_FILE_SIZE_BYTES,
    )
    service.set_handler(handle_request)

    print(f"\n__SERVICE_NAME__ is running")
    print(f"Webhook: {service.webhook_url}")
    print("Type 'exit' to quit\n")

    while True:
        cmd = input()
        if cmd.lower() == "exit":
            break
