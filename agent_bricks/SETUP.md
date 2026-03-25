# Agent Bricks Setup Guide

This guide walks through deploying the Agent Bricks version of BrickBot as a
separate Databricks App in the same workspace.

## Architecture Comparison

| Component | OpenAI SDK (main) | Agent Bricks (this branch) |
|-----------|-------------------|---------------------------|
| Agent Framework | OpenAI Agents SDK | Agent Bricks Supervisor |
| Static Content | System prompt | Knowledge Assistant |
| Search Tools | MCP Server | MCP Server (same) ✓ |
| Chat History | Lakebase | Lakebase (same) ✓ |
| Routing | Single agent | Supervisor → KA + MCP |

## Deployment Model

- Keep the current OpenAI Agents SDK version on `main`
- Deploy this branch (`agent-bricks`) as a second app, for example:
  - baseline app: `brickbot`
  - Agent Bricks app: `brickbot-agent-bricks`
- Use the same deployment mode as `main`: a native Databricks App configured to
  pull from GitHub, not a bundle-managed app deployment
- Share the same backend data plane:
  - `brickbot2026.rainfocus.*` tables
  - existing Vector Search indexes
  - existing UC functions in `brickbot2026.tools.*`
- Isolate the app layer:
  - separate app name
  - separate app service principal
  - separate secret scope
  - separate env vars
  - same Lakebase project with a dedicated branch for app conversations
  - separate MLflow experiment for traces/evals

Current resource names for this branch:

- Lakebase autoscaling project: `brickbot`
- Lakebase branch: `agent-bricks`
- MLflow experiment: `/Shared/brickbot-agent-bricks`

The `agent-bricks` Lakebase branch is created from `brickbot/main`, so it keeps
the same database, schema layout, and existing app-state shape while isolating
writes for this git branch.

Repo ownership split:

- `app.yaml` defines the repo-side runtime contract
- Databricks App Git source, app resources, and user authorization scopes are
  configured on the Databricks App itself
- `databricks.yml` remains responsible for shared backend resources like the
  RainFocus refresh job, not the Databricks App deployment

## Setup Steps

### Step 1: Deploy this branch as a separate app

```bash
# This branch is configured to run the Agent Bricks backend
# via BRICKBOT_BACKEND_VARIANT=agent-bricks in app.yaml.
```

Create a second Databricks App in the target workspace. Do not replace the
baseline app.

Preferred parity path with `main`:

1. Create a custom Databricks App named `brickbot-agent-bricks`
2. In **Configure Git** (Beta), point it at:
   - repo: `nkarpov/brickbot2026`
   - branch: `agent-bricks`
3. Keep the app connected to that branch for future redeploys / refreshes
4. After each push to `agent-bricks`, redeploy the app from the same configured
   Git branch in the Databricks App UI

If the Git-backed app flow is not enabled in the workspace yet, the fallback is
workspace-synced source code. That is functional, but it is not deployment
parity with `main`.

If `databricks apps get brickbot-agent-bricks --profile brickbot -o json` shows
`"mode": "SNAPSHOT"` on the active deployment, that app is still using
workspace-synced source. Recreate it as a Git-backed app to reach full parity
with `main`.

### Step 2: Give the new app its own secrets and permissions

- Create a separate secret scope for the Agent Bricks app
- Grant the app service principal only the permissions it needs
- Reuse the existing shared UC functions and backend resources
- Keep user authorization minimal: only add the serving scope needed for
  forwarded user token queries to Model Serving / Agent Bricks endpoints

Minimum permissions that were required in the evaluation workspace:

```bash
# Resolve the Databricks App service principal client ID.
APP_SP_CLIENT_ID=$(databricks apps get brickbot-agent-bricks --profile brickbot -o json | jq -r .service_principal_client_id)

# Knowledge Assistant content source.
databricks grants update catalog brickbot2026 \
  --json "{\"changes\":[{\"principal\":\"${APP_SP_CLIENT_ID}\",\"add\":[\"USE_CATALOG\"]}]}" \
  --profile brickbot
databricks grants update schema brickbot2026.content \
  --json "{\"changes\":[{\"principal\":\"${APP_SP_CLIENT_ID}\",\"add\":[\"USE_SCHEMA\"]}]}" \
  --profile brickbot
databricks grants update volume brickbot2026.content.faq \
  --json "{\"changes\":[{\"principal\":\"${APP_SP_CLIENT_ID}\",\"add\":[\"READ_VOLUME\"]}]}" \
  --profile brickbot

# Shared search tools exposed through the Supervisor.
databricks grants update schema brickbot2026.tools \
  --json "{\"changes\":[{\"principal\":\"${APP_SP_CLIENT_ID}\",\"add\":[\"USE_SCHEMA\"]}]}" \
  --profile brickbot
databricks grants update function brickbot2026.tools.search_sessions \
  --json "{\"changes\":[{\"principal\":\"${APP_SP_CLIENT_ID}\",\"add\":[\"EXECUTE\"]}]}" \
  --profile brickbot
databricks grants update function brickbot2026.tools.search_exhibitors \
  --json "{\"changes\":[{\"principal\":\"${APP_SP_CLIENT_ID}\",\"add\":[\"EXECUTE\"]}]}" \
  --profile brickbot

# Vector Search indexes that power the UC functions.
databricks grants update schema brickbot2026.rainfocus \
  --json "{\"changes\":[{\"principal\":\"${APP_SP_CLIENT_ID}\",\"add\":[\"USE_SCHEMA\"]}]}" \
  --profile brickbot
databricks grants update table brickbot2026.rainfocus.sessions_index \
  --json "{\"changes\":[{\"principal\":\"${APP_SP_CLIENT_ID}\",\"add\":[\"SELECT\"]}]}" \
  --profile brickbot
databricks grants update table brickbot2026.rainfocus.exhibitors_index \
  --json "{\"changes\":[{\"principal\":\"${APP_SP_CLIENT_ID}\",\"add\":[\"SELECT\"]}]}" \
  --profile brickbot

# Serving endpoint ACLs.
# Use the serving endpoint IDs, not just the friendly endpoint names.
SUPERVISOR_ENDPOINT_ID=$(databricks serving-endpoints list --profile brickbot -o json | jq -r '.[] | select(.name=="mas-24e1fc96-endpoint") | .id')
KA_ENDPOINT_ID=$(databricks serving-endpoints list --profile brickbot -o json | jq -r '.[] | select(.name=="ka-415d90ea-endpoint") | .id')

databricks serving-endpoints update-permissions "${SUPERVISOR_ENDPOINT_ID}" \
  --json "{\"access_control_list\":[{\"service_principal_name\":\"${APP_SP_CLIENT_ID}\",\"permission_level\":\"CAN_QUERY\"}]}" \
  --profile brickbot

# The app-created Knowledge Assistant endpoint already had CAN_MANAGE for the app
# service principal in the evaluation workspace, but check it explicitly if your
# setup differs.
databricks serving-endpoints get-permissions "${KA_ENDPOINT_ID}" --profile brickbot

# Lakebase short-term memory tables are created on first use. After the first
# successful app request, run the grant helper so the app SP can access any
# newly created tables and sequences on the branch.
uv run python scripts/grant_lakebase_permissions.py "${APP_SP_CLIENT_ID}" \
  --memory-type openai-short-term \
  --project brickbot \
  --branch agent-bricks
```

If you plan to test the Supervisor directly as yourself in the UI or with `curl`,
you may also need equivalent `USE_SCHEMA` / `EXECUTE` / `SELECT` grants for your
user identity.

### Step 3: Prepare Knowledge Assistant content

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

### Step 4: Create Knowledge Assistant

Option A: programmatic, after the deployment image picks up a newer `databricks-sdk`

```bash
export DATABRICKS_PROFILE=brickbot
python -m agent_bricks.setup_knowledge_assistant
```

Option B: manual in the Databricks UI

Use the uploaded UC volume content as the knowledge source.

### Step 5: Create Supervisor Agent (Manual - UI Only)

⚠️ **BLOCKER**: No SDK/API for Supervisor Agent creation. Must use UI.

1. Go to workspace: https://dbc-9f8f7152-215c.cloud.databricks.com
2. Navigate to **Agents** in left nav
3. Click **Supervisor Agent** → **Build**

Configure:
- **Name**: any UI label is fine, for example `brickbot-supervisor`
- **Description**: 
  ```
  BrickBot - DAIS 2026 conference assistant.
  Helps attendees find sessions, get venue info, and manage schedules.
  ```

Add Agents:
1. **Knowledge Assistant** (Type: Agent Endpoint)
   - Select: `brickbot-knowledge-assistant`
   - Description: "Answers questions about venue, policies, FAQ, and general conference info"

2. **UC Functions / Tools**
   - `brickbot2026.tools.search_sessions`
   - `brickbot2026.tools.search_exhibitors`
   - Add Rainfocus-backed schedule functions later if/when they exist
   - Description: "Search sessions by topic, speaker, or technology. Search exhibitors by name or booth."

Instructions:
```
Route questions appropriately:
- Venue, policies, FAQ, accessibility → Knowledge Assistant
- Session search, speaker lookup, exhibitor search → UC functions / tools
- If unclear, try Knowledge Assistant first
```

3. Click **Create Agent**
4. Wait for provisioning (2-5 minutes)
5. After creation, copy the actual serving endpoint name for use in code

Important: the Supervisor UI label is **not** the serving endpoint name. In the
evaluation workspace, the UI label was user-chosen, but Databricks created a
serving endpoint named `mas-24e1fc96-endpoint`. Use the real serving endpoint
name in `SUPERVISOR_ENDPOINT_NAME`, not the friendly UI label.

### Step 6: Configure the app

Add the following Databricks App resources in the app configuration UI:

1. **Database**
   - Type: Lakebase Autoscaling database
   - Project: `brickbot`
   - Branch: `agent-bricks`
   - Database: `databricks_postgres`
   - Permission: `Can connect and create`
   - Resource key: leave default `postgres`

2. **MLflow experiment**
   - Experiment: `/Shared/brickbot-agent-bricks`
   - Permission: `Can edit`
   - Resource key: leave default `experiment`

3. **User authorization scopes**
   - Add `serving.serving-endpoints`
   - Keep the default identity scopes Databricks includes:
     - `iam.current-user:read`
     - `iam.access-control:read`

Set the app env var:

- `SUPERVISOR_ENDPOINT_NAME=<your-supervisor-endpoint>`

The app code is already wired to read this env var. `app.yaml` now also expects:

- `MLFLOW_EXPERIMENT_ID` from app resource key `experiment`
- Lakebase autoscaling target `brickbot` / `agent-bricks`
- Postgres connection env vars from the first database resource (`postgres`)
- Forwarded user access token from Databricks App headers for end-user serving
  authorization, with app auth fallback in the backend

Current branch default:

```yaml
- name: SUPERVISOR_ENDPOINT_NAME
  value: "mas-24e1fc96-endpoint"
```

The app proxy sends Databricks agent-style payloads with an `input` field. If
you test the Supervisor directly, use `input`, not `messages`.

Lakebase resource attachment is what makes the following variables available to
the running app without hardcoding connection details:

- `PGHOST`
- `PGDATABASE`
- `PGUSER`
- `PGPORT`
- `PGSSLMODE`

### Step 7: Test and validate parity

```bash
# Query via curl
curl -X POST "https://dbc-9f8f7152-215c.cloud.databricks.com/serving-endpoints/${SUPERVISOR_ENDPOINT_NAME}/invocations" \
  -H "Authorization: Bearer $(databricks auth token --profile brickbot | jq -r .access_token)" \
  -H "Content-Type: application/json" \
  -d '{"input": [{"role": "user", "content": "What sessions are about ML?"}]}'
```

Expected behavior:
- The Supervisor should call `brickbot2026.tools.search_sessions` for ML-style
  session queries
- The Knowledge Assistant should answer venue / FAQ / policies questions via
  `ka-415d90ea-endpoint`

Parity checks:

1. Deployment parity
   - The app shows Git source `nkarpov/brickbot2026` on branch `agent-bricks`
   - Redeploys happen from that configured branch, not from a manually synced
     workspace folder
2. Auth parity
   - `databricks apps get brickbot-agent-bricks --profile brickbot -o json`
     shows `serving.serving-endpoints` in `effective_user_api_scopes`
   - The app can query the Supervisor with forwarded user auth, and falls back
     to app auth only when the forwarded token lacks serving scope
3. Lakebase parity
   - Multi-turn chat history lands on Lakebase project `brickbot`, branch
     `agent-bricks`
   - Repeated turns reuse stored history instead of requiring the full prior
     transcript from the frontend
4. MLflow parity
   - Traces land in `/Shared/brickbot-agent-bricks`
   - Session metadata is visible on traces via `mlflow.trace.session`

Useful verification commands:

```bash
databricks apps get brickbot-agent-bricks --profile brickbot -o json
databricks postgres list-branches projects/brickbot --profile brickbot -o json
databricks experiments get-by-name /Shared/brickbot-agent-bricks --profile brickbot -o json
```

## Latency Comparison

Run the benchmark script to compare:

```bash
python -m agent_bricks.benchmark --queries queries.json --output results.json
```

## Known Limitations

1. **Supervisor creation is UI-only** - No SDK/API available (as of March 2026)
2. **Knowledge Assistant automation depends on a newer `databricks-sdk` than some local/deploy environments may currently have**
3. **The app can be deployed now, but it will not function end-to-end until the Knowledge Assistant and Supervisor exist**
4. **Knowledge Assistant is English-only** - no multi-language support
5. **Max 20 subagents** per Supervisor
6. **ESC workspaces are not supported** - Enhanced Security and Compliance
