-- Refresh sessions Delta table from RainFocus API
-- Runs every 15 minutes via scheduled job

INSERT OVERWRITE brickbot2026.rainfocus.sessions
WITH raw AS (
  SELECT from_json(
    brickbot2026.rainfocus.get_sessions(''),
    'STRUCT<data: ARRAY<STRUCT<
      sessionId: STRING,
      title: STRING,
      abstract: STRING,
      status: STRING,
      published: BOOLEAN,
      times: ARRAY<STRUCT<
        dayDisplayName: STRING,
        startTime: STRING,
        room: STRING,
        capacity: STRING,
        registered: STRING,
        seatsRemaining: INT
      >>,
      participants: ARRAY<STRUCT<
        fullName: STRING,
        company: STRING,
        jobTitle: STRING,
        roles: STRING
      >>,
      attributeValues: ARRAY<STRUCT<
        attribute: STRING,
        value: STRING
      >>,
      attendAccess: ARRAY<STRING>,
      sessionCode: STRING
    >>>'
  ) AS parsed
),
sessions AS (
  SELECT explode(parsed.data) AS s FROM raw
)
SELECT
  s.sessionId AS session_id,
  s.sessionCode AS session_code,
  s.title,
  s.abstract,
  s.status,
  s.published,

  -- Extract speakers as comma-separated string
  array_join(
    transform(s.participants, p -> p.fullName),
    ', '
  ) AS speakers,

  -- Extract time info from first time slot
  CASE WHEN size(s.times) > 0 THEN s.times[0].dayDisplayName END AS day_name,
  CASE WHEN size(s.times) > 0 THEN s.times[0].startTime END AS start_time,
  CASE WHEN size(s.times) > 0 THEN s.times[0].room END AS room,
  CASE WHEN size(s.times) > 0 THEN s.times[0].capacity END AS capacity,
  CASE WHEN size(s.times) > 0 THEN s.times[0].seatsRemaining END AS seats_remaining,

  -- Extract attributes by name
  array_join(
    transform(
      filter(s.attributeValues, a -> a.attribute = 'Session Track'),
      a -> a.value
    ), ', '
  ) AS session_track,

  array_join(
    transform(
      filter(s.attributeValues, a -> a.attribute = 'Session Type'),
      a -> a.value
    ), ', '
  ) AS session_type,

  array_join(
    transform(
      filter(s.attributeValues, a -> a.attribute = 'Industry'),
      a -> a.value
    ), ', '
  ) AS industries,

  -- ACL: attend access IDs as JSON string for filtering
  to_json(s.attendAccess) AS attend_access,

  current_timestamp() AS refreshed_at

FROM sessions
WHERE s.status = 'Accepted' AND s.published = true;
