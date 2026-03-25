"""
Agent Bricks version of BrickBot.

This module queries the Agent Bricks Supervisor endpoint instead of using
OpenAI Agents SDK directly. The Supervisor routes to:
- Knowledge Assistant (static docs)
- MCP Server (UC Functions for search)

The Supervisor must be created via UI first (see SETUP.md).
"""

import logging
import os
from datetime import datetime
from typing import AsyncGenerator
from zoneinfo import ZoneInfo

import httpx
import mlflow
from databricks.sdk import WorkspaceClient
from mlflow.genai.agent_server import invoke, stream
from mlflow.types.responses import (
    ResponsesAgentRequest,
    ResponsesAgentResponse,
    ResponsesAgentStreamEvent,
)

from agent_server.utils import (
    get_session_id,
)

logger = logging.getLogger(__name__)

# Supervisor endpoint configuration
# This must be set after creating the Supervisor via UI
SUPERVISOR_ENDPOINT_NAME = os.environ.get(
    "SUPERVISOR_ENDPOINT_NAME", 
    "brickbot-supervisor"  # Update after UI creation
)

# Initialize Databricks client
_workspace_client = WorkspaceClient()
_databricks_host = _workspace_client.config.host


def get_supervisor_url() -> str:
    """Get the full URL for the Supervisor endpoint."""
    return f"{_databricks_host}/serving-endpoints/{SUPERVISOR_ENDPOINT_NAME}/invocations"


async def query_supervisor(
    messages: list[dict],
    session_id: str | None = None,
) -> dict:
    """
    Query the Agent Bricks Supervisor endpoint.
    
    Args:
        messages: List of message dicts with 'role' and 'content'
        session_id: Optional session ID for conversation continuity
    
    Returns:
        Response dict from the Supervisor
    """
    url = get_supervisor_url()
    
    # Get auth token
    token = _workspace_client.config.token
    
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    
    payload = {
        "messages": messages,
    }
    
    if session_id:
        payload["custom_inputs"] = {"session_id": session_id}
    
    async with httpx.AsyncClient(timeout=120.0) as client:
        response = await client.post(url, json=payload, headers=headers)
        response.raise_for_status()
        return response.json()


async def query_supervisor_stream(
    messages: list[dict],
    session_id: str | None = None,
) -> AsyncGenerator[dict, None]:
    """
    Query the Agent Bricks Supervisor endpoint with streaming.
    
    Args:
        messages: List of message dicts with 'role' and 'content'
        session_id: Optional session ID for conversation continuity
    
    Yields:
        Response chunks from the Supervisor
    """
    url = get_supervisor_url()
    
    # Get auth token
    token = _workspace_client.config.token
    
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }
    
    payload = {
        "messages": messages,
        "stream": True,
    }
    
    if session_id:
        payload["custom_inputs"] = {"session_id": session_id}
    
    async with httpx.AsyncClient(timeout=120.0) as client:
        async with client.stream("POST", url, json=payload, headers=headers) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    import json
                    data = line[6:]
                    if data.strip() and data != "[DONE]":
                        yield json.loads(data)


@invoke()
async def invoke_handler(request: ResponsesAgentRequest) -> ResponsesAgentResponse:
    """Handle invoke requests by forwarding to Supervisor."""
    
    session_id = get_session_id(request)
    if session_id:
        mlflow.update_current_trace(metadata={"mlflow.trace.session": session_id})
    
    # Convert request to messages format
    messages = []
    for item in request.input:
        if hasattr(item, 'role') and hasattr(item, 'content'):
            messages.append({
                "role": item.role,
                "content": item.content if isinstance(item.content, str) else str(item.content)
            })
    
    # Query Supervisor
    try:
        result = await query_supervisor(messages, session_id)
        
        # Extract output from Supervisor response
        output_items = []
        if "choices" in result:
            for choice in result["choices"]:
                if "message" in choice:
                    msg = choice["message"]
                    output_items.append({
                        "type": "message",
                        "role": msg.get("role", "assistant"),
                        "content": msg.get("content", ""),
                    })
        elif "output" in result:
            output_items = result["output"]
        
        return ResponsesAgentResponse(
            output=output_items,
            custom_outputs={"session_id": session_id},
        )
        
    except Exception as e:
        logger.error(f"Error querying Supervisor: {e}", exc_info=True)
        return ResponsesAgentResponse(
            output=[{
                "type": "message",
                "role": "assistant",
                "content": f"I encountered an error processing your request. Please try again.",
            }],
            custom_outputs={"session_id": session_id, "error": str(e)},
        )


@stream()
async def stream_handler(
    request: ResponsesAgentRequest,
) -> AsyncGenerator[ResponsesAgentStreamEvent, None]:
    """Handle streaming requests by forwarding to Supervisor."""
    
    session_id = get_session_id(request)
    if session_id:
        mlflow.update_current_trace(metadata={"mlflow.trace.session": session_id})
    
    # Convert request to messages format
    messages = []
    for item in request.input:
        if hasattr(item, 'role') and hasattr(item, 'content'):
            messages.append({
                "role": item.role,
                "content": item.content if isinstance(item.content, str) else str(item.content)
            })
    
    # Stream from Supervisor
    try:
        async for chunk in query_supervisor_stream(messages, session_id):
            # Convert Supervisor stream events to ResponsesAgentStreamEvent
            if "choices" in chunk:
                for choice in chunk["choices"]:
                    delta = choice.get("delta", {})
                    if "content" in delta:
                        yield ResponsesAgentStreamEvent(
                            type="response.output_text.delta",
                            delta=delta["content"],
                        )
            elif "type" in chunk:
                yield ResponsesAgentStreamEvent(**chunk)
                
    except Exception as e:
        logger.error(f"Error streaming from Supervisor: {e}", exc_info=True)
        yield ResponsesAgentStreamEvent(
            type="response.output_text.delta",
            delta=f"Error: {str(e)}",
        )


# Healthcheck
def healthcheck() -> dict:
    """Check if Supervisor endpoint is accessible."""
    try:
        url = f"{_databricks_host}/api/2.0/serving-endpoints/{SUPERVISOR_ENDPOINT_NAME}"
        response = httpx.get(
            url,
            headers={"Authorization": f"Bearer {_workspace_client.config.token}"},
            timeout=10.0,
        )
        if response.status_code == 200:
            data = response.json()
            return {
                "status": "healthy",
                "endpoint": SUPERVISOR_ENDPOINT_NAME,
                "state": data.get("state", {}).get("ready", "UNKNOWN"),
            }
        else:
            return {
                "status": "unhealthy",
                "endpoint": SUPERVISOR_ENDPOINT_NAME,
                "error": f"HTTP {response.status_code}",
            }
    except Exception as e:
        return {
            "status": "unhealthy",
            "endpoint": SUPERVISOR_ENDPOINT_NAME,
            "error": str(e),
        }
