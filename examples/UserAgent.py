from zyndai_agent.agent import AgentConfig, ZyndAIAgent
from zyndai_agent.communication import MQTTMessage
from dotenv import load_dotenv
import os
from time import sleep
import re

load_dotenv()

class StockComparisonOrchestrator:
    def __init__(self, p3_agent):
        self.p3_agent = p3_agent
        self.stock_data_agent = None
        self.comparison_agent = None
        self.received_messages = []
        self.waiting_for_response = False
        self.current_step = None
        self.stock_symbols = []
        self.stock_data_responses = []
        
    def search_and_find_agents(self, auto_select=True):
        """Search for stock data and comparison agents (don't connect yet)"""
        print("Searching for stock data agent...")
        
        # Search for stock data agent
        stock_data_agents = self.p3_agent.search_agents_by_capabilities(["stock_data_retrieval"])
        if stock_data_agents:
            print("Stock Data Agents Found:")
            for agent in stock_data_agents:
                print(f"""
                    DID: {agent["didIdentifier"]}
                    Description: {agent["description"]}
                    Match Score: {agent["matchScore"]}
                """)
            
            if auto_select:
                # Use the first agent (highest match score)
                self.stock_data_agent = stock_data_agents[0]
            else:
                # Manual selection
                agent_select = input("Select Stock Data Agent DID: ")
                selected_agent = None
                for agent in stock_data_agents:
                    if agent["didIdentifier"] == agent_select:
                        selected_agent = agent
                        break
                
                if not selected_agent:
                    print("Invalid DID, agent not found")
                    return False
                self.stock_data_agent = selected_agent
        else:
            print("No stock data agent found!")
            return False
        
        # Search for comparison agent
        print("Searching for stock comparison agent...")
        comparison_agents = self.p3_agent.search_agents_by_capabilities(["stock_comparison"])
        if comparison_agents:
            print("Stock Comparison Agents Found:")
            for agent in comparison_agents:
                print(f"""
                    DID: {agent["didIdentifier"]}
                    Description: {agent["description"]}
                    Match Score: {agent["matchScore"]}
                """)
            
            if auto_select:
                # Use the first agent (highest match score)
                self.comparison_agent = comparison_agents[0]
            else:
                # Manual selection
                agent_select = input("Select Stock Comparison Agent DID: ")
                selected_agent = None
                for agent in comparison_agents:
                    if agent["didIdentifier"] == agent_select:
                        selected_agent = agent
                        break
                
                if not selected_agent:
                    print("Invalid DID, agent not found")
                    return False
                self.comparison_agent = selected_agent
        else:
            print("No stock comparison agent found!")
            return False
        
        return True
    
    def connect_to_agent(self, agent):
        """Connect to a specific agent"""
        print(f"Connecting to agent: {agent['didIdentifier']}")
        self.p3_agent.connect_agent(agent)
    
    def disconnect_current_agent(self):
        """Disconnect from current agent"""
        print("Disconnecting from current agent...")
        # Note: Add actual disconnect method if available in P3 AI SDK
        # For now, we'll just reset the connection state
        pass
    
    def extract_stock_symbols(self, user_input):
        """Extract stock symbols from user input"""
        # Remove common words that might interfere
        cleaned_input = user_input.upper()
        
        # Look for common patterns like "AAPL and GOOGL", "AAPL vs GOOGL", etc.
        patterns = [
            r'\b([A-Z]{2,5})\s+(?:AND|VS|VERSUS)\s+([A-Z]{2,5})\b',
            r'\b([A-Z]{2,5})\s*,\s*([A-Z]{2,5})\b',
            r'COMPARE\s+([A-Z]{2,5})\s+(?:AND|VS|VERSUS)\s+([A-Z]{2,5})',
            r'COMPARISON\s+(?:OF\s+)?([A-Z]{2,5})\s+(?:AND|VS|VERSUS)\s+([A-Z]{2,5})',
            r'\b([A-Z]{2,5})\s+([A-Z]{2,5})\b'
        ]
        
        for pattern in patterns:
            match = re.search(pattern, cleaned_input)
            if match:
                symbol1, symbol2 = match.group(1), match.group(2)
                # Filter out common words that might be mistaken for stock symbols
                common_words = {'GET', 'STOCK', 'PRICE', 'DATA', 'COMPARE', 'COMPARISON', 'OF', 'AND', 'VS', 'VERSUS'}
                if symbol1 not in common_words and symbol2 not in common_words:
                    return [symbol1, symbol2]
        
        # Alternative approach: look for likely stock symbols (2-5 uppercase letters)
        # but exclude common words
        potential_symbols = re.findall(r'\b[A-Z]{2,5}\b', cleaned_input)
        common_words = {'GET', 'STOCK', 'PRICE', 'DATA', 'COMPARE', 'COMPARISON', 'OF', 'AND', 'VS', 'VERSUS'}
        stock_symbols = [symbol for symbol in potential_symbols if symbol not in common_words]
        
        if len(stock_symbols) >= 2:
            return stock_symbols[:2]
        
        return []
    
    def process_stock_comparison_request(self, user_input, auto_select=True):
        """Process a stock comparison request"""
        print(f"Processing stock comparison request: {user_input}")
        
        # Extract stock symbols
        self.stock_symbols = self.extract_stock_symbols(user_input)
        
        if len(self.stock_symbols) < 2:
            print("Could not extract two stock symbols from the request.")
            return "Please provide two stock symbols to compare (e.g., 'Compare AAPL and GOOGL')"
        
        print(f"Extracted stock symbols: {self.stock_symbols}")
        
        # Search for agents (don't connect yet)
        if not self.search_and_find_agents(auto_select):
            return "Error: Could not find required agents"
        
        # Step 1: Connect to stock data agent and get stock data
        print("=== STEP 1: Getting Stock Data ===")
        self.connect_to_agent(self.stock_data_agent)
        
        self.current_step = "getting_stock_data"
        self.waiting_for_response = True
        self.stock_data_responses = []
        
        # Request stock data for both stocks
        for symbol in self.stock_symbols:
            message = f"Get stock price data for {symbol}"
            print(f"Requesting stock data: {message}")
            self.p3_agent.send_message(message)
            
            # Wait for response for this stock
            max_wait = 15  # seconds per stock
            wait_count = 0
            initial_response_count = len(self.stock_data_responses)
            
            while len(self.stock_data_responses) == initial_response_count and wait_count < max_wait:
                sleep(1)
                wait_count += 1
            
            if len(self.stock_data_responses) == initial_response_count:
                return f"Error: Did not receive stock data for {symbol}"
            
            print(f"Received stock data for {symbol}")
        
        if len(self.stock_data_responses) < 2:
            return "Error: Did not receive stock data for both stocks"
        
        # Step 2: Disconnect from stock data agent and connect to comparison agent
        print("=== STEP 2: Getting Stock Comparison ===")
        self.disconnect_current_agent()
        self.connect_to_agent(self.comparison_agent)
        
        # Combine stock data and send to comparison agent
        combined_data = "\n".join(self.stock_data_responses)
        self.current_step = "getting_comparison"
        self.waiting_for_response = True
        
        comparison_message = f"Compare these stocks:\n{combined_data}"
        print(f"Sending to comparison agent: {comparison_message}")
        self.p3_agent.send_message(comparison_message)
        
        # Wait for comparison response
        max_wait = 15  # seconds
        wait_count = 0
        initial_message_count = len(self.received_messages)
        
        while len(self.received_messages) == initial_message_count and wait_count < max_wait:
            sleep(1)
            wait_count += 1
        
        if len(self.received_messages) > initial_message_count:
            comparison_result = self.received_messages[-1]
            print("=== COMPARISON COMPLETE ===")
            return f"Stock Comparison Result:\n{comparison_result}"
        else:
            return "Error: Did not receive comparison result"
    
    def handle_message(self, message: MQTTMessage, topic: str):
        """Handle incoming messages"""
        print(f"Received message: {message.content}")
        self.received_messages.append(message.content)
        
        if self.current_step == "getting_stock_data":
            self.stock_data_responses.append(message.content)
            print(f"Stock data responses received: {len(self.stock_data_responses)}")
            
        elif self.current_step == "getting_comparison":
            self.waiting_for_response = False
            print("Comparison result received")

if __name__ == "__main__":
    # Create agent config
    agent_config = AgentConfig(
        default_outbox_topic=None,
        auto_reconnect=True,
        message_history_limit=100,
        registry_url="https://registry.zynd.ai",
        mqtt_broker_url="mqtt://registry.zynd.ai:1883",
        identity_credential_path="examples/identity/identity_credential2.json",
        secret_seed=os.environ["AGENT2_SEED"]
    )

    # Init p3 agent sdk wrapper
    p3_agent = ZyndAIAgent(agent_config=agent_config)
    
    # Create orchestrator
    orchestrator = StockComparisonOrchestrator(p3_agent)
    
    # Set up message handler
    p3_agent.add_message_handler(orchestrator.handle_message)
    
    print("User Orchestrator Agent is running...")
    print("This agent orchestrates stock comparison requests.")
    print("Example: 'get stock comparison of AAPL and GOOGL'")
    print("Example: 'compare TSLA vs MSFT'")
    print("Commands:")
    print("  - 'manual' - Enable manual agent selection")
    print("  - 'auto' - Enable automatic agent selection (default)")
    print("Capabilities: orchestration, workflow_management")
    
    auto_select = True
    
    # Main loop
    while True:
        user_input = input("\nEnter your request (or 'exit' to quit): ")
        
        if user_input.lower() == 'exit':
            break
        elif user_input.lower() == 'manual':
            auto_select = False
            print("Manual agent selection enabled")
            continue
        elif user_input.lower() == 'auto':
            auto_select = True
            print("Automatic agent selection enabled")
            continue
        
        if 'stock' in user_input.lower() and ('comparison' in user_input.lower() or 'compare' in user_input.lower()):
            # Reset agent connections for each new request
            orchestrator.stock_data_agent = None
            orchestrator.comparison_agent = None
            result = orchestrator.process_stock_comparison_request(user_input, auto_select)
            print(f"\nResult: {result}")
        else:
            print("Please ask for a stock comparison (e.g., 'get stock comparison of AAPL and GOOGL')")
        
        sleep(1)