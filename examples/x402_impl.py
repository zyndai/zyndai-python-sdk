from zyndai_agent.agent import AgentConfig, ZyndAIAgent
from dotenv import load_dotenv
import os
from time import sleep

load_dotenv()


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

    # Init zynd agent sdk wrapper
    zynd_agent = ZyndAIAgent(agent_config=agent_config)
    auto_select = True
    
    data = zynd_agent.x402_processor.post("http://localhost:3000/api/pay")
    print(data.json())