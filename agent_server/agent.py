import json
import logging
import os
from datetime import datetime
from typing import AsyncGenerator
from zoneinfo import ZoneInfo

import litellm
import mlflow
from agents import Agent, Runner, function_tool, set_default_openai_api, set_default_openai_client
from agents.tracing import set_trace_processors
from databricks.sdk import WorkspaceClient
from databricks_openai import AsyncDatabricksOpenAI
from databricks_openai.agents import AsyncDatabricksSession
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
_LAKEBASE_INSTANCE_NAME_RAW = os.environ.get("LAKEBASE_INSTANCE_NAME") or None
LAKEBASE_AUTOSCALING_PROJECT = os.getenv("LAKEBASE_AUTOSCALING_PROJECT") or None
LAKEBASE_AUTOSCALING_BRANCH = os.getenv("LAKEBASE_AUTOSCALING_BRANCH") or None

_has_autoscaling = LAKEBASE_AUTOSCALING_PROJECT and LAKEBASE_AUTOSCALING_BRANCH
if not _LAKEBASE_INSTANCE_NAME_RAW and not _has_autoscaling:
    raise ValueError(
        "Lakebase configuration is required. Set one of:\n"
        "  LAKEBASE_INSTANCE_NAME=<name>\n"
        "  LAKEBASE_AUTOSCALING_PROJECT=<project> and LAKEBASE_AUTOSCALING_BRANCH=<branch>\n"
    )

LAKEBASE_INSTANCE_NAME = (
    resolve_lakebase_instance_name(_LAKEBASE_INSTANCE_NAME_RAW)
    if _LAKEBASE_INSTANCE_NAME_RAW
    else None
)

# OpenAI Agents SDK setup for Databricks
set_default_openai_client(AsyncDatabricksOpenAI())
set_default_openai_api("chat_completions")
set_trace_processors([])
mlflow.openai.autolog()
logging.getLogger("mlflow.utils.autologging_utils").setLevel(logging.ERROR)
litellm.suppress_debug_info = True

# Workspace client for UC function calls
_ws = WorkspaceClient()

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
"""


@function_tool
def get_current_time() -> str:
    """Get the current date and time in the conference timezone (America/Los_Angeles)."""
    return datetime.now(ZoneInfo("America/Los_Angeles")).strftime("%A, %B %d, %Y %I:%M %p PT")


@function_tool
def search_sessions(query: str) -> str:
    """Search for conference sessions by topic, speaker name, technology, or keyword.

    Use this tool when the user asks about sessions, talks, presentations, or speakers.

    Args:
        query: The search query — topics, speaker names, technologies, or keywords.
    """
    try:
        resp = _ws.serving_endpoints.http_request(
            conn="rainfocus",
            method="GET",
            path="entityDataDump/session",
            headers={"Accept": "application/json"},
        )
        data = resp.json() if hasattr(resp, "json") else json.loads(resp.text)
        sessions = data.get("data", [])

        # Filter to accepted, published sessions
        query_lower = query.lower()
        query_terms = query_lower.split()

        results = []
        for session in sessions:
            if session.get("status") != "Accepted" or not session.get("published"):
                continue

            title = (session.get("title") or "").lower()
            abstract = (session.get("abstract") or "").lower()
            speakers = ", ".join(
                p.get("fullName", "") for p in session.get("participants", []) if isinstance(p, dict)
            ).lower()

            score = 0
            for term in query_terms:
                if term in title:
                    score += 3
                if term in speakers:
                    score += 3
                if term in abstract:
                    score += 1

            if score > 0:
                times = session.get("times", [])
                time_info = ""
                room = ""
                if times:
                    t = times[0]
                    time_info = f"{t.get('dayDisplayName', '')} {t.get('startTime', '')}".strip()
                    room = t.get("room", "")

                attrs = {
                    a["attribute"]: a["value"]
                    for a in session.get("attributeValues", [])
                    if isinstance(a, dict)
                }

                results.append((score, {
                    "title": session.get("title", "N/A"),
                    "speakers": ", ".join(
                        p.get("fullName", "")
                        for p in session.get("participants", [])
                        if isinstance(p, dict)
                    ) or "N/A",
                    "date_time": time_info or "TBD",
                    "location": room or "TBD",
                    "track": attrs.get("Session Track", "N/A"),
                    "type": attrs.get("Session Type", "N/A"),
                    "abstract": (session.get("abstract") or "")[:300],
                    "sessionId": session.get("sessionId"),
                }))

        results.sort(key=lambda x: x[0], reverse=True)
        top = [r[1] for r in results[:5]]

        if not top:
            return "No sessions found matching your query. Try different keywords or speaker names."

        formatted = []
        for s in top:
            formatted.append(
                f"**{s['title']}**\n"
                f"  Speakers: {s['speakers']}\n"
                f"  Track: {s['track']} | Type: {s['type']}\n"
                f"  When: {s['date_time']} | Where: {s['location']}\n"
                f"  {s['abstract']}"
            )
        return "\n\n".join(formatted)

    except Exception as e:
        logger.error(f"Error searching sessions: {e}")
        return f"Error searching for sessions: {str(e)}"


def create_agent() -> Agent:
    dt = datetime.now(ZoneInfo("America/Los_Angeles")).strftime(
        "%A, %B %d, %Y %I:%M %p PT"
    )
    return Agent(
        name="Brickbot",
        instructions=SYSTEM_PROMPT.format(dt=dt),
        model="databricks-gpt-5-2",
        tools=[get_current_time, search_sessions],
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

    agent = create_agent()
    messages = await deduplicate_input(request, session)
    result = Runner.run_streamed(agent, input=messages, session=session)

    async for event in process_agent_stream_events(result.stream_events()):
        yield event
