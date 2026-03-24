import logging
from typing import AsyncGenerator, AsyncIterator, Optional
from uuid import uuid4

from agents.result import StreamEvent
from databricks.sdk import WorkspaceClient
from databricks_openai.agents import AsyncDatabricksSession
from mlflow.genai.agent_server import get_request_headers
from mlflow.types.responses import ResponsesAgentRequest, ResponsesAgentStreamEvent
from uuid_utils import uuid7


def get_session_id(request) -> str:
    """Extract session_id from request or generate a new one."""
    ci = getattr(request, "custom_inputs", None) or (request.get("custom_inputs") if isinstance(request, dict) else None) or {}
    if isinstance(ci, dict) and ci.get("session_id"):
        return str(ci["session_id"])

    ctx = getattr(request, "context", None) or (request.get("context") if isinstance(request, dict) else None)
    if ctx and getattr(ctx, "conversation_id", None):
        return str(ctx.conversation_id)

    return str(uuid7())


def _is_lakebase_hostname(value: str) -> bool:
    return ".database." in value and value.endswith(".com")


def resolve_lakebase_instance_name(
    instance_name: str, workspace_client: Optional[WorkspaceClient] = None
) -> str:
    if not _is_lakebase_hostname(instance_name):
        return instance_name

    client = workspace_client or WorkspaceClient()
    hostname = instance_name

    try:
        instances = list(client.database.list_database_instances())
    except Exception as exc:
        raise ValueError(
            f"Unable to list database instances to resolve hostname '{hostname}'."
        ) from exc

    for instance in instances:
        if hostname in (instance.read_write_dns, instance.read_only_dns):
            if not instance.name:
                raise ValueError(
                    f"Found matching instance for hostname '{hostname}' but name is not available."
                )
            logging.info(f"Resolved Lakebase hostname '{hostname}' to instance name '{instance.name}'")
            return instance.name

    raise ValueError(f"Unable to find database instance matching hostname '{hostname}'.")


def get_databricks_host_from_env() -> Optional[str]:
    try:
        w = WorkspaceClient()
        return w.config.host
    except Exception as e:
        logging.exception(f"Error getting databricks host from env: {e}")
        return None


def get_user_workspace_client() -> WorkspaceClient:
    token = get_request_headers().get("x-forwarded-access-token")
    return WorkspaceClient(token=token, auth_type="pat")


async def deduplicate_input(
    request, session: AsyncDatabricksSession
) -> list[dict]:
    raw_input = request.input if hasattr(request, "input") else request.get("input", [])
    messages = [i.model_dump() if hasattr(i, "model_dump") else i for i in raw_input]
    for msg in messages:
        if (
            isinstance(msg, dict)
            and msg.get("type") == "message"
            and msg.get("role") == "assistant"
            and isinstance(msg.get("content"), str)
        ):
            msg["content"] = [{"type": "output_text", "text": msg["content"], "annotations": []}]
    session_items = await session.get_items()
    if len(session_items) >= len(messages) - 1:
        return [messages[-1]]
    return messages


async def process_agent_stream_events(
    async_stream: AsyncIterator[StreamEvent],
) -> AsyncGenerator[ResponsesAgentStreamEvent, None]:
    curr_item_id = str(uuid4())
    async for event in async_stream:
        if event.type == "raw_response_event":
            event_data = event.data.model_dump()
            if event_data["type"] == "response.output_item.added":
                curr_item_id = str(uuid4())
                event_data["item"]["id"] = curr_item_id
            elif event_data.get("item") is not None and event_data["item"].get("id") is not None:
                event_data["item"]["id"] = curr_item_id
            elif event_data.get("item_id") is not None:
                event_data["item_id"] = curr_item_id
            yield event_data
        elif event.type == "run_item_stream_event" and event.item.type == "tool_call_output_item":
            yield ResponsesAgentStreamEvent(
                type="response.output_item.done",
                item=event.item.to_input_item(),
            )
