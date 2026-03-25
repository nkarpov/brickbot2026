-- Refresh speakers Delta table from RainFocus API
-- Runs every 15 minutes via scheduled job

INSERT OVERWRITE brickbot2026.rainfocus.speakers
WITH raw AS (
  SELECT from_json(
    brickbot2026.rainfocus.get_speakers(''),
    'STRUCT<data: ARRAY<STRUCT<
      speakerId: STRING,
      firstName: STRING,
      lastName: STRING,
      fullName: STRING,
      companyName: STRING,
      jobTitle: STRING,
      bio: STRING,
      sessions: ARRAY<STRUCT<
        sessionId: STRING,
        title: STRING,
        published: STRING,
        status: STRING
      >>
    >>>'
  ) AS parsed
),
speakers AS (
  SELECT explode(parsed.data) AS s FROM raw
)
SELECT
  s.speakerId AS speaker_id,
  s.firstName AS first_name,
  s.lastName AS last_name,
  s.fullName AS full_name,
  s.companyName AS company,
  s.jobTitle AS job_title,
  s.bio,

  -- Sessions as comma-separated titles (accepted only)
  array_join(
    transform(
      filter(s.sessions, sess -> sess.status = 'Accepted' AND sess.published = 'true'),
      sess -> sess.title
    ), ' | '
  ) AS sessions,

  -- Count of accepted sessions
  size(filter(s.sessions, sess -> sess.status = 'Accepted' AND sess.published = 'true')) AS session_count,

  current_timestamp() AS refreshed_at

FROM speakers
WHERE size(s.sessions) > 0;
