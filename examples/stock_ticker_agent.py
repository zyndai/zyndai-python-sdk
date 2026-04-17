#!/usr/bin/env python3
"""
Stock Ticker Agent with AG-UI Streaming.

Streams live stock chart data via AG-UI CUSTOM event.
Demonstrates generative UI integration.

Usage:
    python stock_ticker_agent.py

Then visit: http://localhost:5000/ui/stream/conv-123
"""

import asyncio
import logging
from datetime import datetime, timedelta
import yfinance as yf
from zyndai_agent import ZyndAIAgent, AgentConfig

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def fetch_stock_data(ticker: str, period: str = "1mo") -> list[dict]:
    """Fetch historical stock data from Yahoo Finance."""
    try:
        stock = yf.Ticker(ticker)
        hist = stock.history(period=period)

        # Format for Recharts
        data = []
        for date, row in hist.iterrows():
            data.append({
                "date": date.strftime("%Y-%m-%d"),
                "close": round(float(row["Close"]), 2),
                "open": round(float(row["Open"]), 2),
                "high": round(float(row["High"]), 2),
                "low": round(float(row["Low"]), 2),
                "volume": int(row["Volume"]),
            })

        return data
    except Exception as e:
        logger.error(f"Failed to fetch stock data: {e}")
        return []


async def main():
    """Run stock ticker agent with AG-UI streaming."""

    config = AgentConfig(
        name="Stock Ticker",
        description="Real-time stock chart streaming with live data",
        webhook_host="0.0.0.0",
        webhook_port=5000,
        generative_ui=True,  # Enable AG-UI streaming
        registry_url="http://localhost:8080",
    )

    agent = ZyndAIAgent(agent_config=config)

    @agent.register_handler
    async def handle_stock_query(message, ui):
        """Handle stock query and stream chart."""

        # Extract ticker from message
        ticker = message.content.strip().upper()
        if not ticker or len(ticker) > 10:
            await ui.text("Invalid ticker symbol")
            return "Error: Invalid ticker"

        # Emit status
        await ui.text(f"Fetching {ticker} data...")

        # Fetch data
        data = fetch_stock_data(ticker, period="3mo")

        if not data:
            await ui.text(f"Could not fetch data for {ticker}")
            return f"Error: No data for {ticker}"

        # Calculate metrics
        prices = [d["close"] for d in data]
        current_price = prices[-1]
        previous_price = prices[0]
        change = current_price - previous_price
        change_pct = (change / previous_price * 100) if previous_price else 0

        # Stream status
        await ui.text(
            f"📊 {ticker} Stock Analysis\n"
            f"Current: ${current_price:.2f}\n"
            f"Change: ${change:+.2f} ({change_pct:+.1f}%)\n"
            f"Period: 3 months ({len(data)} trading days)"
        )

        # Stream chart as CUSTOM widget
        await ui.custom(
            "chart",
            {
                "type": "line",
                "title": f"{ticker} Stock Price (3M)",
                "data": data,
                "dataKey": "close",
                "xAxis": "date",
                "yAxis": "close",
                "width": 100,
                "height": 400,
            }
        )

        # Stream additional metrics
        await ui.state_snapshot({
            "ticker": ticker,
            "current_price": current_price,
            "change": change,
            "change_percent": change_pct,
            "high": max(prices),
            "low": min(prices),
            "avg": sum(prices) / len(prices),
        })

        await ui.text("✅ Chart loaded successfully!")

        return f"Stock data for {ticker} streamed"

    # Wait indefinitely
    print("\n✅ Stock Ticker Agent running")
    print(f"📍 Webhook: http://localhost:5000/webhook")
    print(f"📡 Stream test: http://localhost:5000/ui/stream/test-conv-1")
    print(f"Try: curl -X POST http://localhost:5000/webhook/sync -H 'Content-Type: application/json' -d '{{\"content\": \"AAPL\", \"sender_id\": \"test\", \"conversation_id\": \"test-conv-1\"}}'\n")

    try:
        await asyncio.sleep(float('inf'))
    except KeyboardInterrupt:
        print("\n⛔ Shutting down...")
        agent.stop_webhook_server()


if __name__ == "__main__":
    asyncio.run(main())
