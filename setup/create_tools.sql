-- Raw RainFocus API wrappers (brickbot2026.rainfocus schema)
-- These are thin SQL functions that call the RainFocus HTTP connection.
-- Used by the refresh job to populate Delta tables.

CREATE OR REPLACE FUNCTION brickbot2026.rainfocus.get_sessions(
  query STRING COMMENT 'Not used — returns all sessions.'
)
RETURNS STRING
LANGUAGE SQL
COMMENT 'Fetch all conference sessions from the RainFocus API.'
RETURN (
  SELECT http_request(
    conn => 'rainfocus',
    method => 'GET',
    path => 'entityDataDump/session?pageSize=100000',
    headers => map('Accept', 'application/json')
  ).text
);

CREATE OR REPLACE FUNCTION brickbot2026.rainfocus.get_exhibitors(
  query STRING COMMENT 'Not used — returns all exhibitors.'
)
RETURNS STRING
LANGUAGE SQL
COMMENT 'Fetch all exhibitors from the RainFocus API.'
RETURN (
  SELECT http_request(
    conn => 'rainfocus',
    method => 'GET',
    path => 'entityDataDump/exhibitor?pageSize=100000',
    headers => map('Accept', 'application/json')
  ).text
);

-- Brickbot search tools (brickbot2026.tools schema)
-- These use Vector Search HYBRID (BM25 + semantic) on the Delta tables.
-- Exposed to the agent via MCP.

CREATE OR REPLACE FUNCTION brickbot2026.tools.search_sessions(
  query STRING COMMENT 'Search query — topics, speaker names, technologies, or keywords'
)
RETURNS TABLE
LANGUAGE SQL
COMMENT 'Search for DAIS 2026 conference sessions by topic, speaker, technology, or keyword. Returns the most relevant sessions using hybrid BM25 + semantic search.'
RETURN (
  SELECT * FROM vector_search(
    index => 'brickbot2026.rainfocus.sessions_index',
    query_text => query,
    query_type => 'HYBRID',
    num_results => 10
  )
);

CREATE OR REPLACE FUNCTION brickbot2026.tools.search_exhibitors(
  query STRING COMMENT 'Search query — company names, technologies, or keywords'
)
RETURNS TABLE
LANGUAGE SQL
COMMENT 'Search for DAIS 2026 expo hall exhibitors by name, description, or booth location. Returns the most relevant exhibitors using hybrid BM25 + semantic search.'
RETURN (
  SELECT * FROM vector_search(
    index => 'brickbot2026.rainfocus.exhibitors_index',
    query_text => query,
    query_type => 'HYBRID',
    num_results => 10
  )
);
