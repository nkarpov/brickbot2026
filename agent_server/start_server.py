import logging
from pathlib import Path

from dotenv import load_dotenv
from mlflow.genai.agent_server import AgentServer

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env", override=True)

import agent_server.agent  # noqa: E402, F401

agent_server = AgentServer("Brickbot", enable_chat_proxy=True)
app = agent_server.app

try:
    from mlflow.genai.agent_server import setup_mlflow_git_based_version_tracking
    setup_mlflow_git_based_version_tracking()
except Exception:
    logging.getLogger(__name__).info("Git-based version tracking unavailable (not a git repo), skipping.")


def main():
    agent_server.run(app_import_string="agent_server.start_server:app")
