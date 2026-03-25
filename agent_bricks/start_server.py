import logging
import os
import json
from pathlib import Path

from dotenv import load_dotenv
from mlflow.genai.agent_server import AgentServer
from starlette.requests import Request

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env", override=True)

import agent_bricks.agent  # noqa: E402, F401
from agent_bricks.setup_knowledge_assistant import main as bootstrap_knowledge_assistant

agent_server = AgentServer("Brickbot Agent Bricks", enable_chat_proxy=False)
app = agent_server.app


@app.middleware("http")
async def log_invocations_body(request: Request, call_next):
    token = None
    if request.method == "POST" and request.url.path == "/invocations":
        body = await request.body()
        try:
            token = agent_bricks.agent.set_raw_request_json(json.loads(body))
        except Exception:
            logging.getLogger(__name__).exception("Failed to parse raw /invocations body.")

    try:
        return await call_next(request)
    finally:
        if token is not None:
            agent_bricks.agent.reset_raw_request_json(token)

if os.environ.get("BRICKBOT_BOOTSTRAP_KA", "").lower() in {"1", "true", "yes"}:
    try:
        bootstrap_knowledge_assistant()
    except Exception:
        logging.getLogger(__name__).exception(
            "Knowledge Assistant bootstrap failed during startup."
        )

try:
    from mlflow.genai.agent_server import setup_mlflow_git_based_version_tracking

    setup_mlflow_git_based_version_tracking()
except Exception:
    logging.getLogger(__name__).info(
        "Git-based version tracking unavailable (not a git repo), skipping."
    )


def main():
    agent_server.run(app_import_string="agent_bricks.start_server:app")
