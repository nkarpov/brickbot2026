CREATE SCHEMA IF NOT EXISTS brickbot2026.tools;

CREATE OR REPLACE FUNCTION brickbot2026.tools.search_sessions(
  query STRING COMMENT 'Search query — topics, speaker names, technologies, or keywords'
)
RETURNS STRING
LANGUAGE SQL
COMMENT 'Search for DAIS 2026 conference sessions by topic, speaker name, technology, or keyword. Use when the user asks about sessions, talks, presentations, or speakers.'
RETURN (
  SELECT http_request(
    conn => 'rainfocus',
    method => 'GET',
    path => 'entityDataDump/session',
    headers => map('Accept', 'application/json')
  ).text
);

CREATE OR REPLACE FUNCTION brickbot2026.tools.search_exhibitors(
  query STRING COMMENT 'Search query — company names, technologies, or keywords'
)
RETURNS STRING
LANGUAGE SQL
COMMENT 'Search for DAIS 2026 expo hall exhibitors by name or keyword. Use when the user asks about exhibitors, booths, sponsors, or the expo hall.'
RETURN (
  SELECT http_request(
    conn => 'rainfocus',
    method => 'GET',
    path => 'entityDataDump/exhibitor',
    headers => map('Accept', 'application/json')
  ).text
);

CREATE OR REPLACE FUNCTION brickbot2026.tools.get_current_time()
RETURNS STRING
LANGUAGE SQL
COMMENT 'Get the current date and time in the conference timezone (US Pacific).'
RETURN (
  SELECT date_format(current_timestamp(), 'EEEE, MMMM dd, yyyy hh:mm a') || ' PT'
);
