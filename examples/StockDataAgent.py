from zyndai_agent.agent import AgentConfig, ZyndAIAgent
from zyndai_agent.communication import MQTTMessage
from langchain_openai import ChatOpenAI
from langchain.memory import ConversationBufferMemory
from langchain_core.tools import tool
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain.agents import create_tool_calling_agent, AgentExecutor

from dotenv import load_dotenv
import os
import json
from time import sleep

load_dotenv()

@tool
def get_stock_price(stock_symbol: str) -> str:
    """Get current stock price and basic data for a given stock symbol"""
    # Dummy data for demonstration
    dummy_data = {
        "AAPL": {"price": 175.50, "change": "+2.5%", "volume": "45.2M", "market_cap": "2.8T"},
        "GOOGL": {"price": 2650.30, "change": "-1.2%", "volume": "22.1M", "market_cap": "1.7T"},
        "MSFT": {"price": 420.75, "change": "+0.8%", "volume": "28.5M", "market_cap": "3.1T"},
        "TSLA": {"price": 245.60, "change": "+3.4%", "volume": "89.7M", "market_cap": "780B"},
        "AMZN": {"price": 3180.90, "change": "-0.5%", "volume": "18.9M", "market_cap": "1.6T"},
        "META": {"price": 485.20, "change": "+1.9%", "volume": "35.4M", "market_cap": "1.2T"}
    }
    
    stock_upper = stock_symbol.upper()
    if stock_upper in dummy_data:
        data = dummy_data[stock_upper]
        return json.dumps({
            "symbol": stock_upper,
            "price": data["price"],
            "change": data["change"],
            "volume": data["volume"],
            "market_cap": data["market_cap"]
        })
    else:
        return json.dumps({
            "symbol": stock_upper,
            "price": 100.00,
            "change": "0.0%",
            "volume": "1M",
            "market_cap": "50B",
            "note": "Dummy data for unknown stock"
        })

if __name__ == "__main__":
    # Create agent config
    agent_config = AgentConfig(
        default_outbox_topic=None,
        auto_reconnect=True,
        message_history_limit=100,
        registry_url="https://registry.zynd.ai",
        mqtt_broker_url="mqtt://registry.zynd.ai:1883",
        identity_credential_path="examples/identity/identity_cred_stock_data.json",
        secret_seed=os.environ["STOCK_DATA_AGENT_SEED"]
    )

    # Init p3 agent sdk wrapper
    p3_agent = ZyndAIAgent(agent_config=agent_config)
    
    # Create a langchain agent with stock price tool
    llm = ChatOpenAI(model="gpt-3.5-turbo", temperature=0)
    tools = [get_stock_price]
    memory = ConversationBufferMemory(memory_key="chat_history", return_messages=True)
    
    prompt = ChatPromptTemplate.from_messages([
        ("system", """You are a Stock Data Agent. Your job is to fetch stock price information for requested stocks.
        
        When a user asks for stock information, use the get_stock_price tool to fetch the data.
        If multiple stocks are requested, fetch data for each one.
        
        Always return the data in a clear, structured format that can be easily parsed by other agents.
        Include the stock symbol, current price, change percentage, volume, and market cap.
        
        Capabilities: stock_data_retrieval, financial_data, market_information"""),
        MessagesPlaceholder(variable_name="chat_history"),
        ("human", "{input}"),
        MessagesPlaceholder(variable_name="agent_scratchpad")
    ])
    
    # Use create_tool_calling_agent instead of create_openai_functions_agent
    agent = create_tool_calling_agent(llm, tools, prompt)
    agent_executor = AgentExecutor(agent=agent, tools=tools, memory=memory, verbose=True)

    p3_agent.set_agent_executor(agent_executor)

    def message_handler(message: MQTTMessage, topic: str):
        print(f"Received message: {message.content}")
        agent_response = p3_agent.agent_executor.invoke({"input": message.content})
        agent_output = agent_response["output"]
        print(f"Sending response: {agent_output}")
        p3_agent.send_message(agent_output)

    p3_agent.add_message_handler(message_handler)

    print("Stock Data Agent is running...")
    print("This agent can fetch stock price data for any stock symbol.")
    print("Capabilities: stock_data_retrieval, financial_data, market_information")
    
    # Keep the agent running
    while True:
        sleep(1)