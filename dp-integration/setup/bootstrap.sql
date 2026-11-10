-- Bootstrap: synthetic Content catalogue for the Tier 2 data product.
-- Idempotent: CREATE ... IF NOT EXISTS, so a re-run never replaces data or
-- strips the tags, comments and grants applied later by the publish job.
-- Deterministic: every value derives from hash() of the row id.
-- No tags, comments or grants here: the publish job owns those.
-- bootstrap.sh splits statements on a semicolon at the end of a line.

-- The metastore has no storage root (default storage), so the catalog must be
-- created with SQL; the catalogs REST API rejects it.
CREATE CATALOG IF NOT EXISTS content_dev;

CREATE SCHEMA IF NOT EXISTS content_dev.catalogue;

CREATE TABLE IF NOT EXISTS content_dev.catalogue.title AS
WITH ids AS (
  SELECT id FROM (SELECT explode(sequence(1, 200)) AS id)
),
base AS (
  SELECT
    id,
    abs(hash(id, 'name1')) AS h1,
    abs(hash(id, 'name2')) AS h2,
    abs(hash(id, 'genre')) AS hg,
    abs(hash(id, 'rating')) AS hr,
    abs(hash(id, 'year')) AS hy,
    abs(hash(id, 'provider')) AS hp,
    abs(hash(id, 'rights')) AS hs,
    abs(hash(id, 'term')) AS ht
  FROM ids
)
SELECT
  concat('T', lpad(cast(id AS STRING), 5, '0')) AS title_id,
  concat(
    element_at(array('Silent', 'Golden', 'Southern', 'Hidden', 'Broken', 'Wild', 'Last', 'Distant',
                     'Crimson', 'Quiet', 'Rising', 'Northern', 'Lost', 'Bright', 'Hollow', 'Salt'),
               cast(h1 % 16 AS INT) + 1),
    ' ',
    element_at(array('Harbour', 'Valley', 'Coast', 'Signal', 'Orchard', 'Island', 'Frontier', 'Station',
                     'Ridge', 'Kitchen', 'Courtroom', 'Paddock', 'Summit', 'Archive', 'Tide', 'Line'),
               cast(h2 % 16 AS INT) + 1),
    CASE WHEN id > 100 THEN concat(' ', cast(id AS STRING)) ELSE '' END
  ) AS title_name,
  -- Deliberate quality issue: 2% of titles (every 50th) have no genre.
  CASE WHEN id % 50 = 0 THEN NULL
       ELSE element_at(array('drama', 'comedy', 'documentary', 'reality', 'news', 'sport',
                             'kids', 'crime', 'lifestyle', 'factual'),
                       cast(hg % 10 AS INT) + 1)
  END AS genre,
  CASE WHEN id % 10 < 7 THEN 'series' WHEN id % 10 < 9 THEN 'movie' ELSE 'special' END AS content_type,
  element_at(array('G', 'PG', 'M', '16', '18'), cast(hr % 5 AS INT) + 1) AS age_rating,
  cast(1995 + hy % 31 AS INT) AS release_year,
  element_at(array('Kea Studios', 'Tasman Pictures', 'Ponga Productions', 'Harbourline Media',
                   'Southern Lens', 'Kowhai Films'),
             cast(hp % 6 AS INT) + 1) AS provider,
  date_add(DATE'2024-01-01', cast(hs % 700 AS INT)) AS rights_start,
  -- Deliberate quality issue: 1% of titles (every 100th, offset 37) end rights before they start.
  CASE WHEN id % 100 = 37
       THEN date_sub(date_add(DATE'2024-01-01', cast(hs % 700 AS INT)), 30)
       ELSE date_add(date_add(DATE'2024-01-01', cast(hs % 700 AS INT)), cast(365 + ht % 730 AS INT))
  END AS rights_end
FROM base;

CREATE TABLE IF NOT EXISTS content_dev.catalogue.episode AS
WITH series AS (
  SELECT
    title_id,
    release_year,
    cast(substr(title_id, 2) AS INT) AS id,
    1 + abs(hash(title_id, 'seasons')) % 2 AS n_seasons
  FROM content_dev.catalogue.title
  WHERE content_type = 'series'
),
seasons AS (
  SELECT title_id, release_year, id, season,
         4 + abs(hash(title_id, season, 'eps')) % 7 AS n_eps
  FROM series
  LATERAL VIEW explode(sequence(1, n_seasons)) s AS season
),
eps AS (
  SELECT title_id, release_year, id, season, episode_number
  FROM seasons
  LATERAL VIEW explode(sequence(1, n_eps)) e AS episode_number
)
SELECT
  concat(title_id, '-S', lpad(cast(season AS STRING), 2, '0'), 'E', lpad(cast(episode_number AS STRING), 2, '0')) AS episode_id,
  title_id,
  cast(season AS INT) AS season,
  cast(episode_number AS INT) AS episode_number,
  cast(22 + abs(hash(title_id, season, episode_number, 'dur')) % 39 AS INT) AS duration_minutes,
  date_add(make_date(release_year, 2, 1), cast((season - 1) * 365 + (episode_number - 1) * 7 AS INT)) AS first_aired
FROM eps;
