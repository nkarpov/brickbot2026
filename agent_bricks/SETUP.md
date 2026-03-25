# Agent Bricks Setup Guide

This guide walks through setting up the Agent Bricks version of BrickBot.

## Architecture Comparison

| Component | OpenAI SDK (main) | Agent Bricks (this branch) |
|-----------|-------------------|---------------------------|
| Agent Framework | OpenAI Agents SDK | Agent Bricks Supervisor |
| Static Content | System prompt | Knowledge Assistant |
| Search Tools | MCP Server | MCP Server (same) ✓ |
| Chat History | Lakebase | Lakebase (same) ✓ |
| Routing | Single agent | Supervisor → KA + MCP |

## Setup Steps

### Step 1: Create Knowledge Assistant (Programmatic)

```bash
# Set profile
export DATABRICKS_PROFILE=brickbot

# Run setup script
python -m agent_bricks.setup_knowledge_assistant
```

This creates a Knowledge Assistant with:
- Conference FAQ content
- Venue information  
- Policies and guidelines

### Step 2: Prepare Knowledge Source Content

Upload static content to UC Volume:

```bash
# Create volume (if not exists)
databricks volumes create brickbot2026 content faq --profile brickbot

# Upload FAQ documents
databricks fs cp ./content/faq/ dbfs:/Volumes/brickbot2026/content/faq/ --recursive --profile brickbot
```

Content to include:
- `venue-info.md` - Moscone Center maps, rooms, parking
- `policies.md` - Code of conduct, badge policies
- `faq.md` - WiFi, food, accessibility, registration
- `accessibility.md` - Wheelchair access, hearing assistance

### Step 3: Create Supervisor Agent (Manual - UI Only)

⚠️ **BLOCKER**: No SDK/API for Supervisor Agent creation. Must use UI.

1. Go to workspace: https://dbc-9f8f7152-215c.cloud.databricks.com
2. Navigate to **Agents** in left nav
3. Click **Supervisor Agent** → **Build**

Configure:
- **Name**: `brickbot-supervisor`
- **Description**: 
  ```
  BrickBot - DAIS 2026 conference assistant.
  Helps attendees find sessions, get venue info, and manage schedules.
  ```

Add Agents:
1. **Knowledge Assistant** (Type: Agent Endpoint)
   - Select: `brickbot-knowledge-assistant`
   - Description: "Answers questions about venue, policies, FAQ, and general conference info"

2. **Search Tools** (Type: MCP Server)
   - Connection: `/api/2.0/mcp/functions/brickbot2026/tools`
   - Description: "Search sessions by topic, speaker, or technology. Search exhibitors by name or booth."

Instructions:
```
Route questions appropriately:
- Venue, policies, FAQ, accessibility → Knowledge Assistant
- Session search, speaker lookup, exhibitor search → MCP tools
- If unclear, try Knowledge Assistant first
```

3. Click **Create Agent**
4. Wait for provisioning (2-5 minutes)
5. Note the endpoint name for use in code

### Step 4: Update Agent Server

Once Supervisor is created, update `agent_bricks/agent.py` with the endpoint name.

### Step 5: Test

```bash
# Query via curl
curl -X POST "https://dbc-9f8f7152-215c.cloud.databricks.com/serving-endpoints/brickbot-supervisor/invocations" \
  -H "Authorization: Bearer $(databricks auth token --profile brickbot | jq -r .access_token)" \
  -H "Content-Type: application/json" \
  -d '{"messages": [{"role": "user", "content": "What sessions are about ML?"}]}'
```

## Latency Comparison

Run the benchmark script to compare:

```bash
python -m agent_bricks.benchmark --queries queries.json --output results.json
```

## Known Limitations

1. **Supervisor creation is UI-only** - No SDK/API available (as of March 2026)
2. **Knowledge Assistant English only** - No multi-language support
3. **Max 20 subagents** per Supervisor
4. **ESC workspaces not supported** - Enhanced Security and Compliance
