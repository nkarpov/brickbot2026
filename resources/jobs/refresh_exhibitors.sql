-- Refresh exhibitors Delta table from RainFocus API
-- Runs every 15 minutes via scheduled job

CREATE OR REPLACE TABLE brickbot2026.rainfocus.exhibitors
TBLPROPERTIES (delta.enableChangeDataFeed = true) AS
WITH raw AS (
  SELECT from_json(
    brickbot2026.rainfocus.get_exhibitors(''),
    'STRUCT<data: ARRAY<STRUCT<
      exhibitorId: STRING,
      code: STRING,
      name: STRING,
      description: STRING,
      published: BOOLEAN,
      url: STRING,
      booth: ARRAY<STRUCT<
        name: STRING,
        boothLocation: STRING
      >>
    >>>'
  ) AS parsed
),
exhibitors AS (
  SELECT explode(parsed.data) AS e FROM raw
)
SELECT
  e.exhibitorId AS exhibitor_id,
  e.code,
  e.name,
  e.description,
  e.published,
  e.url,

  -- Extract booth info from first booth entry
  CASE WHEN size(e.booth) > 0 THEN e.booth[0].name END AS booth_name,
  CASE WHEN size(e.booth) > 0 THEN e.booth[0].boothLocation END AS booth_location,

  current_timestamp() AS refreshed_at

FROM exhibitors
WHERE e.published = true;
