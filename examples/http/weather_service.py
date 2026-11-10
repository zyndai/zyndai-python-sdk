"""
Weather Service — Example Zynd Service (no LLM, pure Python)

Demonstrates how to register a stateless API service on the Zynd network.
Unlike agents which use LLM frameworks, services wrap plain Python functions.
"""

from zyndai_agent.service import ServiceConfig, ZyndService
from dotenv import load_dotenv
import json
import os

load_dotenv()

# Simulated weather data (replace with real API calls)
WEATHER_DATA = {
    "new york": {"temp": 72, "condition": "Partly Cloudy", "humidity": 65},
    "london": {"temp": 59, "condition": "Rainy", "humidity": 80},
    "tokyo": {"temp": 68, "condition": "Clear", "humidity": 55},
    "mumbai": {"temp": 88, "condition": "Humid", "humidity": 90},
    "san francisco": {"temp": 62, "condition": "Foggy", "humidity": 75},
}


def handle_request(input_text: str) -> str:
    """
    Handle weather lookup requests.

    Accepts a city name and returns weather data as JSON.
    This is what gets called for every incoming message.
    """
    city = input_text.strip().lower()

    if city in WEATHER_DATA:
        data = WEATHER_DATA[city]
        return json.dumps({
            "city": city.title(),
            "temperature_f": data["temp"],
            "condition": data["condition"],
            "humidity_pct": data["humidity"],
        })

    # Fuzzy match
    for known_city, data in WEATHER_DATA.items():
        if known_city in city or city in known_city:
            return json.dumps({
                "city": known_city.title(),
                "temperature_f": data["temp"],
                "condition": data["condition"],
                "humidity_pct": data["humidity"],
            })

    available = ", ".join(c.title() for c in WEATHER_DATA.keys())
    return json.dumps({
        "error": f"City '{input_text.strip()}' not found",
        "available_cities": available,
    })


if __name__ == "__main__":
    config = ServiceConfig(
        name="Weather Service",
        description="A simple weather lookup service for demonstration.",
        capabilities={
            "protocols": ["http"],
            "services": ["weather_lookup"],
        },
        category="data",
        tags=["weather", "api", "demo"],
        summary="Returns weather data for major cities. No LLM needed.",
        service_endpoint="https://api.weather.example.com",
        webhook_host="0.0.0.0",
        webhook_port=5020,
        registry_url=os.environ.get("ZYND_REGISTRY_URL", "http://localhost:8080"),
        price="$0.0001",
    )

    service = ZyndService(service_config=config)
    service.set_handler(handle_request)

    print(f"\nWeather Service is running")
    print(f"Webhook: {service.webhook_url}")
    print("Try: curl -X POST {webhook_url}/webhook/sync -H 'Content-Type: application/json' -d '{{\"content\": \"Tokyo\"}}'")
    print("Type 'exit' to quit\n")

    while True:
        cmd = input()
        if cmd.lower() == "exit":
            break
