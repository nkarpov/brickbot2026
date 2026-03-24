# Brickbot 2026 — TODOs

## Done

- [x] Databricks App deployed via GitHub integration (auto-deploys on push to `main`)
- [x] OpenAI Agents SDK agent with `databricks-gpt-5-2` Foundation Model API
- [x] Lakebase chat persistence (project: `brickbot`, branch: `production`)
- [x] UC HTTP Connection `rainfocus` (OAuth M2M, v3 API)
- [x] Delta tables: `brickbot2026.rainfocus.sessions` + `exhibitors` (CDF enabled)
- [x] Vector Search HYBRID indexes on both tables (`brickbot` endpoint, `databricks-gte-large-en`)
- [x] Scheduled refresh job every 15 min (DABs, serverless warehouse)
- [x] UC search functions: `brickbot2026.tools.search_sessions` + `search_exhibitors` (MCP)
- [x] Built-in chat UI for testing (e2e-chatbot-app-next)
- [x] SP permissions on catalog, schemas, indexes, functions

## High Priority — Core Features

- [ ] Schedule read: `get_attendee_schedule(rfauthtoken)` → RainFocus `GET /mySchedule`
- [ ] Schedule write: `add_session(rfauthtoken, session_id, session_time_id)` → RainFocus `POST /addSession`
- [ ] Drop session: `drop_session(rfauthtoken, session_time_id)` → RainFocus `POST /dropSwapSession`
- [ ] ACL enforcement: filter search results by user's `attendAccess` IDs
- [ ] rfauthtoken plumbing: pass per-user token from frontend → agent → tools

## Medium Priority — Polish

- [ ] Prompt Registry: move system prompt to MLflow Prompt Registry for live editing
- [ ] System prompt: port FAQ content, jailbreak protections, session linking from 2025
- [ ] Vercel frontend: custom React chat UI, rfauthtoken capture from mobile webview
- [ ] Auth endpoint: dedicated `/auth/init` (not `GET /`) to avoid caching bugs
- [ ] MLflow tracing: fix missing `opentelemetry-exporter-otlp-proto-grpc`, add experiment
- [ ] Audit log: log schedule writes to Lakebase

## Lower Priority — Nice to Have

- [ ] Session recommendations: infer interests from job title + company
- [ ] Confirmation UX: ask before adding sessions to schedule
- [ ] Multi-turn quality: test conversation context across Lakebase-persisted turns
- [ ] Rate limiting
- [ ] Data pipeline monitoring / alerts on refresh job failure
- [ ] Exhibitor booth data: sparse now, will improve as event approaches
