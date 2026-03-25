"""
Agent Bricks version of BrickBot.

This module proxies the existing MLflow agent-server contract to a manually
created Agent Bricks Supervisor endpoint. The supervisor is expected to route to:
- a Knowledge Assistant for static conference content
- existing BrickBot UC functions for search/actions

The Supervisor itself must still be created in the Databricks UI.
"""

import logging
import os
from contextvars import ContextVar, Token
from typing import Any, AsyncGenerator
from uuid import uuid4

import httpx
import mlflow
from databricks.sdk import WorkspaceClient
from databricks_openai import AsyncDatabricksOpenAI
from databricks_openai.agents import AsyncDatabricksSession
from mlflow.genai.agent_server import get_request_headers, invoke, stream
from mlflow.types.responses import (
    ResponsesAgentRequest,
    ResponsesAgentResponse,
    ResponsesAgentStreamEvent,
)

from agent_server.utils import get_session_id, resolve_lakebase_instance_name

logger = logging.getLogger(__name__)
_raw_request_json: ContextVar[dict[str, Any] | None] = ContextVar(
    "agent_bricks_raw_request_json",
    default=None,
)
MLFLOW_EXPERIMENT_ID = os.environ.get("MLFLOW_EXPERIMENT_ID") or None
_has_pghost = bool(os.environ.get("PGHOST"))
_LAKEBASE_INSTANCE_NAME_RAW = os.environ.get("LAKEBASE_INSTANCE_NAME") or None

if _has_pghost:
    LAKEBASE_INSTANCE_NAME = None
    LAKEBASE_AUTOSCALING_PROJECT = os.getenv("LAKEBASE_AUTOSCALING_PROJECT") or "brickbot"
    LAKEBASE_AUTOSCALING_BRANCH = os.getenv("LAKEBASE_AUTOSCALING_BRANCH") or "production"
    logger.info(
        "Lakebase: PGHOST=%s project=%s branch=%s",
        os.environ["PGHOST"],
        LAKEBASE_AUTOSCALING_PROJECT,
        LAKEBASE_AUTOSCALING_BRANCH,
    )
elif _LAKEBASE_INSTANCE_NAME_RAW:
    LAKEBASE_INSTANCE_NAME = resolve_lakebase_instance_name(_LAKEBASE_INSTANCE_NAME_RAW)
    LAKEBASE_AUTOSCALING_PROJECT = None
    LAKEBASE_AUTOSCALING_BRANCH = None
    logger.info("Lakebase: instance_name=%s", LAKEBASE_INSTANCE_NAME)
else:
    LAKEBASE_AUTOSCALING_PROJECT = os.getenv("LAKEBASE_AUTOSCALING_PROJECT") or None
    LAKEBASE_AUTOSCALING_BRANCH = os.getenv("LAKEBASE_AUTOSCALING_BRANCH") or None
    LAKEBASE_INSTANCE_NAME = None
    if LAKEBASE_AUTOSCALING_PROJECT and LAKEBASE_AUTOSCALING_BRANCH:
        logger.info(
            "Lakebase: autoscaling project=%s branch=%s",
            LAKEBASE_AUTOSCALING_PROJECT,
            LAKEBASE_AUTOSCALING_BRANCH,
        )
    else:
        raise ValueError(
            "Lakebase configuration is required: PGHOST, LAKEBASE_INSTANCE_NAME, "
            "or LAKEBASE_AUTOSCALING_PROJECT+BRANCH"
        )

SUPERVISOR_ENDPOINT_NAME = os.environ.get(
    "SUPERVISOR_ENDPOINT_NAME",
    "brickbot-supervisor",
)

_workspace_client = WorkspaceClient()
_databricks_host = _workspace_client.config.host.rstrip("/")
USE_APP_AUTH_ONLY = os.environ.get("BRICKBOT_USE_APP_AUTH_ONLY", "true").lower() in {
    "1",
    "true",
    "yes",
}

mlflow.openai.autolog()
logging.getLogger("mlflow.utils.autologging_utils").setLevel(logging.ERROR)


def configure_runtime_resources() -> None:
    if MLFLOW_EXPERIMENT_ID:
        try:
            mlflow.set_experiment(experiment_id=MLFLOW_EXPERIMENT_ID)
            logger.info("MLflow experiment configured: %s", MLFLOW_EXPERIMENT_ID)
        except Exception:
            logger.exception("Failed to configure MLflow experiment.")
    else:
        logger.warning(
            "MLFLOW_EXPERIMENT_ID is not set; MLflow traces will use the workspace default."
        )


configure_runtime_resources()


def set_raw_request_json(value: dict[str, Any] | None) -> Token:
    return _raw_request_json.set(value)


def reset_raw_request_json(token: Token) -> None:
    _raw_request_json.reset(token)


def get_raw_request_json() -> dict[str, Any] | None:
    return _raw_request_json.get()


def get_effective_session_id(request: ResponsesAgentRequest) -> str:
    raw_request_json = get_raw_request_json() or {}
    custom_inputs = raw_request_json.get("custom_inputs")
    if isinstance(custom_inputs, dict) and custom_inputs.get("session_id"):
        return str(custom_inputs["session_id"])

    context = raw_request_json.get("context")
    if isinstance(context, dict) and context.get("conversation_id"):
        return str(context["conversation_id"])

    return get_session_id(request)


def get_supervisor_url() -> str:
    return f"{_databricks_host}/serving-endpoints/{SUPERVISOR_ENDPOINT_NAME}/invocations"


def serialize_response_value(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return serialize_response_value(value.model_dump(exclude_none=True))
    if hasattr(value, "to_dict"):
        return serialize_response_value(value.to_dict())
    if isinstance(value, list):
        return [serialize_response_value(item) for item in value]
    if isinstance(value, dict):
        return {key: serialize_response_value(item) for key, item in value.items()}
    return value


def get_forwarded_workspace_client() -> WorkspaceClient | None:
    if USE_APP_AUTH_ONLY:
        return None
    forwarded_token = (get_request_headers() or {}).get("x-forwarded-access-token")
    if not forwarded_token:
        return None
    return WorkspaceClient(host=_databricks_host, token=forwarded_token, auth_type="pat")


def get_auth_headers() -> dict[str, str]:
    """
    Use app credentials for serving queries unless explicitly configured to
    prefer forwarded end-user auth.
    """
    if USE_APP_AUTH_ONLY:
        return _workspace_client.config.authenticate()
    forwarded_token = (get_request_headers() or {}).get("x-forwarded-access-token")
    if forwarded_token:
        return {"Authorization": f"Bearer {forwarded_token}"}
    return _workspace_client.config.authenticate()


def normalize_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                if item.get("type") == "input_text":
                    parts.append(item.get("text", ""))
                elif item.get("type") == "output_text":
                    parts.append(item.get("text", ""))
        return " ".join(part for part in parts if part)
    return str(content)


def normalize_message_item(item: Any) -> dict[str, str] | None:
    data = item.model_dump(exclude_none=True) if hasattr(item, "model_dump") else item
    if not isinstance(data, dict):
        return None

    role = data.get("role")
    if not role:
        return None

    return {
        "role": str(role),
        "content": normalize_content(data.get("content", "")),
    }


def request_to_messages(request: ResponsesAgentRequest) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    raw_input = request.input if hasattr(request, "input") else []
    for item in raw_input:
        normalized = normalize_message_item(item)
        if normalized:
            messages.append(normalized)

    if messages:
        return messages

    raw_request_json = get_raw_request_json() or {}
    for item in raw_request_json.get("input", []) or []:
        normalized = normalize_message_item(item)
        if normalized:
            messages.append(normalized)
    return messages


def create_lakebase_session(session_id: str) -> AsyncDatabricksSession:
    return AsyncDatabricksSession(
        session_id=session_id,
        instance_name=LAKEBASE_INSTANCE_NAME,
        project=LAKEBASE_AUTOSCALING_PROJECT,
        branch=LAKEBASE_AUTOSCALING_BRANCH,
    )


async def get_session_messages(
    session: AsyncDatabricksSession,
) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    for item in await session.get_items():
        normalized = normalize_message_item(item)
        if normalized:
            messages.append(normalized)
    return messages


def deduplicate_messages(
    request_messages: list[dict[str, str]],
    session_messages: list[dict[str, str]],
) -> list[dict[str, str]]:
    if not request_messages:
        return []
    if len(session_messages) >= len(request_messages) - 1:
        return [request_messages[-1]]
    return request_messages


async def build_supervisor_messages(
    request: ResponsesAgentRequest,
    session: AsyncDatabricksSession,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    request_messages = request_to_messages(request)
    session_messages = await get_session_messages(session)
    new_messages = deduplicate_messages(request_messages, session_messages)
    return new_messages, [*session_messages, *new_messages]


def serialize_items(items: list[Any]) -> list[dict[str, Any]]:
    serialized: list[dict[str, Any]] = []
    for item in items:
        data = serialize_response_value(item)
        if isinstance(data, dict):
            serialized.append(data)
    return serialized


async def persist_session_turn(
    session: AsyncDatabricksSession,
    input_messages: list[dict[str, str]],
    output_items: list[Any],
) -> None:
    items_to_store = [*input_messages, *serialize_items(output_items)]
    if not items_to_store:
        return
    await session.add_items(items_to_store)


def extract_output_items(result: dict[str, Any]) -> list[dict[str, Any]]:
    if hasattr(result, "output"):
        return serialize_response_value(result.output) or []
    if "output" in result and isinstance(result["output"], list):
        return result["output"]

    output_items: list[dict[str, Any]] = []
    for choice in result.get("choices", []) or []:
        message = choice.get("message") or {}
        content = normalize_content(message.get("content", ""))
        if content:
            output_items.append(make_message_output_item(content, message.get("role", "assistant")))
    return output_items


def make_output_text_part(content: str) -> dict[str, Any]:
    return {
        "type": "output_text",
        "text": content,
        "annotations": [],
        "logprobs": None,
    }


def make_message_output_item(
    content: str,
    role: str = "assistant",
    status: str | None = None,
) -> dict[str, Any]:
    return {
        "id": str(uuid4()),
        "type": "message",
        "role": role,
        "content": [make_output_text_part(content)],
        "status": status,
    }


async def query_supervisor(
    messages: list[dict[str, str]],
    session_id: str | None = None,
) -> Any:
    kwargs: dict[str, Any] = {
        "model": SUPERVISOR_ENDPOINT_NAME,
        "input": messages,
    }
    if session_id:
        kwargs["extra_body"] = {"custom_inputs": {"session_id": session_id}}

    forwarded_workspace_client = get_forwarded_workspace_client()
    if forwarded_workspace_client is not None:
        try:
            client = AsyncDatabricksOpenAI(workspace_client=forwarded_workspace_client)
            return await client.responses.create(**kwargs)
        except Exception as e:
            if getattr(e, "status_code", None) != 403:
                raise
            logger.info(
                "Supervisor responses query returned 403 with forwarded token; retrying with app auth."
            )

    client = AsyncDatabricksOpenAI(workspace_client=_workspace_client)
    return await client.responses.create(**kwargs)


async def query_supervisor_stream(
    messages: list[dict[str, str]],
    session_id: str | None = None,
) -> Any:
    kwargs: dict[str, Any] = {
        "model": SUPERVISOR_ENDPOINT_NAME,
        "input": messages,
        "stream": True,
    }
    if session_id:
        kwargs["extra_body"] = {"custom_inputs": {"session_id": session_id}}

    forwarded_workspace_client = get_forwarded_workspace_client()
    if forwarded_workspace_client is not None:
        try:
            client = AsyncDatabricksOpenAI(workspace_client=forwarded_workspace_client)
            return await client.responses.create(**kwargs)
        except Exception as e:
            if getattr(e, "status_code", None) != 403:
                raise
            logger.info(
                "Supervisor responses stream returned 403 with forwarded token; retrying with app auth."
            )

    client = AsyncDatabricksOpenAI(workspace_client=_workspace_client)
    return await client.responses.create(**kwargs)


@invoke()
async def invoke_handler(request: ResponsesAgentRequest) -> ResponsesAgentResponse:
    session_id = get_effective_session_id(request)
    if session_id:
        mlflow.update_current_trace(metadata={"mlflow.trace.session": session_id})

    session = create_lakebase_session(session_id)
    new_messages, supervisor_messages = await build_supervisor_messages(request, session)

    try:
        result = await query_supervisor(supervisor_messages, session_id)
        output_items = extract_output_items(result)
        await persist_session_turn(session, new_messages, output_items)
        return ResponsesAgentResponse(
            output=output_items,
            custom_outputs={"session_id": session_id},
        )
    except Exception as e:
        logger.error("Error querying Supervisor", exc_info=True)
        return ResponsesAgentResponse(
            output=[
                make_message_output_item(
                    "I encountered an error processing your request. Please try again."
                )
            ],
            custom_outputs={"session_id": session_id, "error": str(e)},
        )


@stream()
async def stream_handler(
    request: ResponsesAgentRequest,
) -> AsyncGenerator[ResponsesAgentStreamEvent, None]:
    session_id = get_effective_session_id(request)
    if session_id:
        mlflow.update_current_trace(metadata={"mlflow.trace.session": session_id})

    session = create_lakebase_session(session_id)
    new_messages, supervisor_messages = await build_supervisor_messages(request, session)
    response_id = str(uuid4())
    current_item_id = str(uuid4())
    final_output_items: list[dict[str, Any]] = []
    output_item_done_events: list[dict[str, Any]] = []

    try:
        stream_response = await query_supervisor_stream(supervisor_messages, session_id)
        async for event in stream_response:
            payload = serialize_response_value(event)
            if isinstance(payload, dict):
                if payload.get("type") == "response.completed":
                    response = payload.get("response") or {}
                    output = response.get("output") or []
                    if isinstance(output, list):
                        final_output_items = serialize_items(output)
                elif payload.get("type") == "response.output_item.done":
                    item = payload.get("item")
                    if isinstance(item, dict):
                        output_item_done_events.append(item)
            yield payload

        await persist_session_turn(
            session,
            new_messages,
            final_output_items or output_item_done_events,
        )
    except Exception as e:
        logger.error("Error streaming from Supervisor", exc_info=True)
        error_text = f"Error: {str(e)}"
        final_item = make_message_output_item(error_text, status="completed")
        final_item["id"] = current_item_id
        yield {
            "type": "response.created",
            "response": {
                "id": response_id,
                "created_at": None,
                "error": None,
                "object": "response",
                "output": [],
            },
            "sequence_number": 0,
        }
        yield {
            "type": "response.output_item.added",
            "item": {
                "id": current_item_id,
                "content": [],
                "role": "assistant",
                "status": "in_progress",
                "type": "message",
            },
            "output_index": 0,
            "sequence_number": 1,
        }
        yield {
            "type": "response.content_part.added",
            "content_index": 0,
            "item_id": current_item_id,
            "output_index": 0,
            "part": make_output_text_part(""),
            "sequence_number": 2,
        }
        yield {
            "type": "response.output_text.delta",
            "content_index": 0,
            "item_id": current_item_id,
            "delta": error_text,
            "logprobs": [],
            "output_index": 0,
            "sequence_number": 3,
        }
        yield {
            "type": "response.content_part.done",
            "content_index": 0,
            "item_id": current_item_id,
            "output_index": 0,
            "part": make_output_text_part(error_text),
            "sequence_number": 4,
        }
        yield {
            "type": "response.output_item.done",
            "item": final_item,
            "output_index": 0,
            "sequence_number": 5,
        }
        yield {
            "type": "response.completed",
            "response": {
                "id": response_id,
                "created_at": None,
                "error": None,
                "object": "response",
                "output": [final_item],
                "status": "completed",
                "usage": None,
            },
            "sequence_number": 6,
        }


def healthcheck() -> dict[str, str]:
    try:
        response = httpx.get(
            f"{_databricks_host}/api/2.0/serving-endpoints/{SUPERVISOR_ENDPOINT_NAME}",
            headers=get_auth_headers(),
            timeout=10.0,
        )
        response.raise_for_status()
        data = response.json()
        return {
            "status": "healthy",
            "endpoint": SUPERVISOR_ENDPOINT_NAME,
            "state": data.get("state", {}).get("ready", "UNKNOWN"),
        }
    except Exception as e:
        return {
            "status": "unhealthy",
            "endpoint": SUPERVISOR_ENDPOINT_NAME,
            "error": str(e),
        }
