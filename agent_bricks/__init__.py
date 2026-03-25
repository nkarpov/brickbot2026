"""Agent Bricks version of BrickBot.

This module provides an alternative implementation using Databricks Agent Bricks
instead of OpenAI Agents SDK.

Architecture:
- Supervisor Agent (created via UI) routes to:
  - Knowledge Assistant (static docs - venue, FAQ, policies)
  - MCP Server (existing UC Functions - search_sessions, search_exhibitors)

Comparison with OpenAI SDK version:
- Same MCP tools (brickbot2026.tools.*)
- Same Lakebase chat persistence
- Added Knowledge Assistant for static content
- Supervisor routing instead of single-agent
"""
