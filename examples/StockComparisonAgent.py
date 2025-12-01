from zyndai_agent.agent import AgentConfig, ZyndAIAgent
from zyndai_agent.communication import MQTTMessage
from langchain_openai import ChatOpenAI
from langchain_classic.memory import ChatMessageHistory
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_classic.agents import AgentExecutor, create_tool_calling_agent
from langchain_core.tools import tool

from dotenv import load_dotenv
import os
import json
import re
from time import sleep

load_dotenv()

@tool
def compare_stocks(stock_data: str) -> str:
    """Compare two stocks based on their financial data"""
    try:
        # Try to parse the stock data
        lines = stock_data.strip().split('\n')
        stock_info = []
        
        for line in lines:
            # Look for JSON-like data in the line
            if '{' in line and '}' in line:
                json_start = line.find('{')
                json_end = line.rfind('}') + 1
                json_str = line[json_start:json_end]
                try:
                    stock_data_obj = json.loads(json_str)
                    stock_info.append(stock_data_obj)
                except json.JSONDecodeError:
                    continue
        
        if len(stock_info) < 2:
            return "Error: Need at least 2 stocks to compare. Please provide data for 2 stocks."
        
        # Compare the first two stocks
        stock1 = stock_info[0]
        stock2 = stock_info[1]
        
        comparison = {
            "stock1": stock1,
            "stock2": stock2,
            "comparison": {
                "higher_price": stock1["symbol"] if stock1["price"] > stock2["price"] else stock2["symbol"],
                "price_difference": abs(stock1["price"] - stock2["price"]),
                "better_performer": None,
                "analysis": ""
            }
        }
        
        # Determine better performer based on change percentage
        change1 = float(stock1["change"].replace('%', '').replace('+', ''))
        change2 = float(stock2["change"].replace('%', '').replace('+', ''))
        
        if change1 > change2:
            comparison["comparison"]["better_performer"] = stock1["symbol"]
        elif change2 > change1:
            comparison["comparison"]["better_performer"] = stock2["symbol"]
        else:
            comparison["comparison"]["better_performer"] = "Equal"
        
        # Generate analysis
        analysis = f"""
        Stock Comparison Analysis:
        
        {stock1['symbol']} vs {stock2['symbol']}:
        - Price: ${stock1['price']} vs ${stock2['price']} (Difference: ${comparison['comparison']['price_difference']:.2f})
        - Today's Change: {stock1['change']} vs {stock2['change']}
        - Volume: {stock1['volume']} vs {stock2['volume']}
        - Market Cap: {stock1['market_cap']} vs {stock2['market_cap']}
        
        Better Performer Today: {comparison['comparison']['better_performer']}
        Higher Priced Stock: {comparison['comparison']['higher_price']}
        
        Recommendation: Based on today's performance, {comparison['comparison']['better_performer']} is showing better momentum.
        """
        
        comparison["comparison"]["analysis"] = analysis
        
        return json.dumps(comparison, indent=2)
        
    except Exception as e:
        return f"Error comparing stocks: {str(e)}"

if __name__ == "__main__":
    # Create agent config
    agent_config = AgentConfig(
        default_outbox_topic=None,
        auto_reconnect=True,
        message_history_limit=100,
        registry_url="https://registry.zynd.ai",
        mqtt_broker_url="mqtt://registry.zynd.ai:1883",
        identity_credential_path="examples/identity/identity_cred_stock_comparison.json",
        secret_seed=os.environ["STOCK_COMPARISON_AGENT_SEED"]
    )

    # Init p3 agent sdk wrapper
    p3_agent = ZyndAIAgent(agent_config=agent_config)

    # Create a langchain agent with stock comparison tool
    llm = ChatOpenAI(model="gpt-3.5-turbo", temperature=0)
    tools = [compare_stocks]

    # Create message history store
    message_history = ChatMessageHistory()

    prompt = ChatPromptTemplate.from_messages([
        ("system", """You are a Stock Comparison Agent. Your job is to analyze and compare stock data.

        When you receive stock data, use the compare_stocks tool to analyze the differences between stocks.
        Provide clear insights about which stock is performing better, price differences, and recommendations.

        Always return a comprehensive comparison with analysis that helps users make informed decisions.

        Capabilities: stock_comparison, financial_analysis, investment_advice"""),
        MessagesPlaceholder(variable_name="chat_history"),
        ("human", "{input}"),
        MessagesPlaceholder(variable_name="agent_scratchpad")
    ])

    agent = create_tool_calling_agent(llm, tools, prompt)
    agent_executor = AgentExecutor(agent=agent, tools=tools, verbose=True)

    p3_agent.set_agent_executor(agent_executor)

    def message_handler(message: MQTTMessage, topic: str):
        print(f"Received message: {message.content}")

        # Add user message to history
        message_history.add_user_message(message.content)

        agent_response = p3_agent.agent_executor.invoke({
            "input": message.content,
            "chat_history": message_history.messages
        })
        agent_output = agent_response["output"]

        # Add AI response to history
        message_history.add_ai_message(agent_output)

        print(f"Sending response: {agent_output}")
        p3_agent.send_message(agent_output)

    p3_agent.add_message_handler(message_handler)

    print("Stock Comparison Agent is running...")
    print("This agent can compare and analyze stock data.")
    print("Capabilities: stock_comparison, financial_analysis, investment_advice")
    
    # Keep the agent running
    while True:
        sleep(1)