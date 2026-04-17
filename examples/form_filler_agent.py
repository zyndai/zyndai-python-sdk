#!/usr/bin/env python3
"""
Form Filler Agent with AG-UI Streaming.

Streams a dynamic form, collects input, and processes submission.
Demonstrates CUSTOM widget forms and STATE updates.

Usage:
    python form_filler_agent.py

Then send a message to trigger the form.
"""

import asyncio
import json
import logging
from zyndai_agent import ZyndAIAgent, AgentConfig

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def main():
    """Run form-filler agent with AG-UI streaming."""

    config = AgentConfig(
        name="Form Filler",
        description="Dynamic form with validation and submission",
        webhook_host="0.0.0.0",
        webhook_port=5002,
        generative_ui=True,  # Enable AG-UI streaming
        registry_url="http://localhost:8080",
    )

    agent = ZyndAIAgent(agent_config=config)

    @agent.register_handler
    async def handle_form_request(message, ui):
        """Handle form request and stream form widget."""

        # Emit intro
        await ui.text("📋 Please fill out the feedback form below")

        # Stream form as CUSTOM widget
        await ui.custom(
            "form",
            {
                "title": "Customer Feedback Form",
                "description": "Help us improve by sharing your feedback",
                "fields": [
                    {
                        "name": "name",
                        "label": "Full Name",
                        "type": "text",
                        "required": True,
                        "placeholder": "John Doe",
                    },
                    {
                        "name": "email",
                        "label": "Email Address",
                        "type": "email",
                        "required": True,
                        "placeholder": "john@example.com",
                    },
                    {
                        "name": "rating",
                        "label": "Overall Rating",
                        "type": "select",
                        "required": True,
                        "options": [
                            {"label": "⭐ Poor", "value": "1"},
                            {"label": "⭐⭐ Fair", "value": "2"},
                            {"label": "⭐⭐⭐ Good", "value": "3"},
                            {"label": "⭐⭐⭐⭐ Very Good", "value": "4"},
                            {"label": "⭐⭐⭐⭐⭐ Excellent", "value": "5"},
                        ],
                    },
                    {
                        "name": "feedback",
                        "label": "Your Feedback",
                        "type": "textarea",
                        "required": True,
                        "placeholder": "Tell us what you think...",
                    },
                    {
                        "name": "subscribe",
                        "label": "Subscribe to updates",
                        "type": "checkbox",
                        "required": False,
                    },
                ],
                "submitLabel": "Submit Feedback",
                "cancelLabel": "Cancel",
            }
        )

        # Wait for form submission (in real scenario, webhook would receive response)
        await ui.text(
            "\n💡 **Note**: In a real scenario, the form submission would be "
            "sent back as a webhook request. This is a demo of the form streaming capability."
        )

        # Simulate waiting for form data
        await asyncio.sleep(2)

        # Emit example of processed form
        example_submission = {
            "name": "Jane Smith",
            "email": "jane@example.com",
            "rating": "5",
            "feedback": "Great service!",
            "subscribe": True,
        }

        await ui.state_delta([
            {
                "op": "add",
                "path": "/formSubmission",
                "value": example_submission,
            }
        ])

        await ui.text(
            "✅ Form submitted successfully!\n\n"
            f"Thank you {example_submission['name']}! We've received your feedback "
            "and will review it shortly."
        )

        return "Form processing complete"

    # Alternative: approval form demo
    @agent.register_handler
    async def handle_approval_request(message, ui):
        """Handle approval request with approval widget."""

        if "approve" not in message.content.lower():
            return None

        await ui.text("🔐 Requesting approval for transaction")

        # Stream approval widget
        await ui.custom(
            "approval",
            {
                "title": "Transaction Approval Required",
                "description": "Please review and approve this transaction",
                "details": {
                    "Type": "Wire Transfer",
                    "Amount": "$5,000.00",
                    "Recipient": "Acme Corp",
                    "Account": "****1234",
                    "Date": "2024-04-15",
                },
                "approveLabel": "Approve Transfer",
                "rejectLabel": "Reject Transfer",
                "requireReason": True,
            }
        )

        await ui.text(
            "Please review the transaction details above and click approve or reject."
        )

        return "Approval form submitted"

    # Wait indefinitely
    print("\n✅ Form Filler Agent running")
    print(f"📍 Webhook: http://localhost:5002/webhook")
    print(f"📡 Stream test: http://localhost:5002/ui/stream/test-form-1")
    print(f"\nTest Form:")
    print(f"curl -X POST http://localhost:5002/webhook/sync -H 'Content-Type: application/json' -d '{{\"content\": \"show form\", \"sender_id\": \"test\", \"conversation_id\": \"test-form-1\"}}'")
    print(f"\nTest Approval:")
    print(f"curl -X POST http://localhost:5002/webhook/sync -H 'Content-Type: application/json' -d '{{\"content\": \"approve\", \"sender_id\": \"test\", \"conversation_id\": \"test-form-2\"}}'")
    print()

    try:
        await asyncio.sleep(float('inf'))
    except KeyboardInterrupt:
        print("\n⛔ Shutting down...")
        agent.stop_webhook_server()


if __name__ == "__main__":
    asyncio.run(main())
