import logging
import os
from datetime import datetime
from typing import AsyncGenerator
from zoneinfo import ZoneInfo

import litellm
import mlflow
from agents import Agent, Runner, set_default_openai_api, set_default_openai_client
from agents.tracing import set_trace_processors
from databricks.sdk import WorkspaceClient
from databricks_openai import AsyncDatabricksOpenAI
from databricks_openai.agents import AsyncDatabricksSession, McpServer
from mlflow.genai.agent_server import invoke, stream
from mlflow.types.responses import (
    ResponsesAgentRequest,
    ResponsesAgentResponse,
    ResponsesAgentStreamEvent,
)

from agent_server.utils import (
    deduplicate_input,
    get_session_id,
    process_agent_stream_events,
    resolve_lakebase_instance_name,
)

logger = logging.getLogger(__name__)

# Lakebase configuration
_has_pghost = bool(os.environ.get("PGHOST"))
_LAKEBASE_INSTANCE_NAME_RAW = os.environ.get("LAKEBASE_INSTANCE_NAME") or None

if _has_pghost:
    LAKEBASE_INSTANCE_NAME = None
    LAKEBASE_AUTOSCALING_PROJECT = os.getenv("LAKEBASE_AUTOSCALING_PROJECT") or "brickbot"
    LAKEBASE_AUTOSCALING_BRANCH = os.getenv("LAKEBASE_AUTOSCALING_BRANCH") or "production"
    logger.info(f"Lakebase: PGHOST={os.environ['PGHOST']} project={LAKEBASE_AUTOSCALING_PROJECT} branch={LAKEBASE_AUTOSCALING_BRANCH}")
elif _LAKEBASE_INSTANCE_NAME_RAW:
    LAKEBASE_INSTANCE_NAME = resolve_lakebase_instance_name(_LAKEBASE_INSTANCE_NAME_RAW)
    LAKEBASE_AUTOSCALING_PROJECT = None
    LAKEBASE_AUTOSCALING_BRANCH = None
    logger.info(f"Lakebase: instance_name={LAKEBASE_INSTANCE_NAME}")
else:
    LAKEBASE_AUTOSCALING_PROJECT = os.getenv("LAKEBASE_AUTOSCALING_PROJECT") or None
    LAKEBASE_AUTOSCALING_BRANCH = os.getenv("LAKEBASE_AUTOSCALING_BRANCH") or None
    LAKEBASE_INSTANCE_NAME = None
    if LAKEBASE_AUTOSCALING_PROJECT and LAKEBASE_AUTOSCALING_BRANCH:
        logger.info(f"Lakebase: autoscaling project={LAKEBASE_AUTOSCALING_PROJECT} branch={LAKEBASE_AUTOSCALING_BRANCH}")
    else:
        raise ValueError("Lakebase configuration is required: PGHOST, LAKEBASE_INSTANCE_NAME, or LAKEBASE_AUTOSCALING_PROJECT+BRANCH")

# OpenAI Agents SDK setup for Databricks
set_default_openai_client(AsyncDatabricksOpenAI())
set_default_openai_api("chat_completions")
set_trace_processors([])
mlflow.openai.autolog()
logging.getLogger("mlflow.utils.autologging_utils").setLevel(logging.ERROR)
litellm.suppress_debug_info = True

# Databricks host for MCP endpoint
_databricks_host = WorkspaceClient().config.host

SYSTEM_PROMPT = """\
You are Brickbot, the virtual assistant for the Data + AI Summit 2026 (DAIS 2026).

The conference takes place the week of June 15, 2026 at the Moscone Center in San Francisco, CA.

The current time is {dt}.

You help attendees with:
- Finding sessions by topic, speaker, or technology
- General conference questions (venue, logistics, schedule)
- Exhibitor and expo hall information

Guidelines:
- Be concise and helpful
- When sharing session results, format them clearly with title, speakers, date/time, and location
- If you don't know something, say so — don't make up information
- Stay on topic — only answer questions related to DAIS 2026
- Use the search_sessions tool when users ask about sessions, speakers, or topics
- Use the search_exhibitors tool when users ask about exhibitors, booths, or the expo hall
- The search tools return raw JSON from the conference API — parse and format the results for the user
"""


async def init_mcp_server():
    """Initialize MCP server pointing at our UC functions in brickbot2026.tools."""
    return McpServer(
        url=f"{_databricks_host}/api/2.0/mcp/functions/brickbot2026/tools",
        name="brickbot-tools",
        workspace_client=WorkspaceClient(),
    )


def create_agent(mcp_servers=None) -> Agent:
    dt = datetime.now(ZoneInfo("America/Los_Angeles")).strftime(
        "%A, %B %d, %Y %I:%M %p PT"
    )
    return Agent(
        name="Brickbot",
        instructions=SYSTEM_PROMPT.format(dt=dt),
        model="databricks-gpt-5-2",
        tools=[],
        mcp_servers=mcp_servers or [],
    )


@invoke()
async def invoke_handler(request: ResponsesAgentRequest) -> ResponsesAgentResponse:
    session_id = get_session_id(request)
    if session_id:
        mlflow.update_current_trace(metadata={"mlflow.trace.session": session_id})

    session = AsyncDatabricksSession(
        session_id=session_id,
        instance_name=LAKEBASE_INSTANCE_NAME,
        project=LAKEBASE_AUTOSCALING_PROJECT,
        branch=LAKEBASE_AUTOSCALING_BRANCH,
    )

    try:
        async with await init_mcp_server() as mcp_server:
            agent = create_agent(mcp_servers=[mcp_server])
            messages = await deduplicate_input(request, session)
            result = await Runner.run(agent, messages, session=session)
    except Exception:
        logger.warning("MCP server unavailable, running without tools.", exc_info=True)
        agent = create_agent()
        messages = await deduplicate_input(request, session)
        result = await Runner.run(agent, messages, session=session)

    return ResponsesAgentResponse(
        output=[item.to_input_item() for item in result.new_items],
        custom_outputs={"session_id": session.session_id},
    )


@stream()
async def stream_handler(
    request: ResponsesAgentRequest,
) -> AsyncGenerator[ResponsesAgentStreamEvent, None]:
    session_id = get_session_id(request)
    if session_id:
        mlflow.update_current_trace(metadata={"mlflow.trace.session": session_id})

    session = AsyncDatabricksSession(
        session_id=session_id,
        instance_name=LAKEBASE_INSTANCE_NAME,
        project=LAKEBASE_AUTOSCALING_PROJECT,
        branch=LAKEBASE_AUTOSCALING_BRANCH,
    )

    try:
        async with await init_mcp_server() as mcp_server:
            agent = create_agent(mcp_servers=[mcp_server])
            messages = await deduplicate_input(request, session)
            result = Runner.run_streamed(agent, input=messages, session=session)
            async for event in process_agent_stream_events(result.stream_events()):
                yield event
    except Exception:
        logger.warning("MCP server unavailable, running without tools.", exc_info=True)
        agent = create_agent()
        messages = await deduplicate_input(request, session)
        result = Runner.run_streamed(agent, input=messages, session=session)
        async for event in process_agent_stream_events(result.stream_events()):
            yield event
