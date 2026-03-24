-- Raw RainFocus API wrappers
-- These are thin SQL functions that call the RainFocus HTTP connection.
-- They live in brickbot2026.rainfocus schema.

CREATE OR REPLACE FUNCTION brickbot2026.rainfocus.get_sessions(
  query STRING COMMENT 'Not used yet — returns all sessions. Will support filtering in the future.'
)
RETURNS STRING
LANGUAGE SQL
COMMENT 'Fetch conference sessions from the RainFocus API.'
RETURN (
  SELECT http_request(
    conn => 'rainfocus',
    method => 'GET',
    path => 'entityDataDump/session',
    headers => map('Accept', 'application/json')
  ).text
);

CREATE OR REPLACE FUNCTION brickbot2026.rainfocus.get_exhibitors(
  query STRING COMMENT 'Not used yet — returns all exhibitors. Will support filtering in the future.'
)
RETURNS STRING
LANGUAGE SQL
COMMENT 'Fetch exhibitors from the RainFocus API.'
RETURN (
  SELECT http_request(
    conn => 'rainfocus',
    method => 'GET',
    path => 'entityDataDump/exhibitor',
    headers => map('Accept', 'application/json')
  ).text
);

-- Brickbot-specific tools go in brickbot2026.tools schema.
-- These will do actual work: search/filter/format, schedule writes, etc.
-- TODO: search_sessions (Python UC function that calls rainfocus.get_sessions + filters/ranks)
-- TODO: search_exhibitors (same pattern)
-- TODO: add_session (calls RainFocus POST /addSession)
-- TODO: get_attendee_schedule (calls RainFocus GET /mySchedule)
