"""Lakeflow pipeline: the two datasets of the content-linear contract, with the contract's rules.

Synthetic example data: the dimension is the content catalogue, the fact is a deterministic two-week
linear schedule over channels ch1 to ch3. The rules are not written here. The deploy copies the
merged contract and contract_expectations.py (from dp-integration) next to this file, and
`rules.apply` turns the contract's row-level rules into expectations on each dataset.
"""
from pyspark import pipelines as dp

from contract_expectations import ContractExpectations

rules = ContractExpectations.from_file(spark.conf.get("contract_path"))  # noqa: F821 (pipeline global)
SOURCE = spark.conf.get("source_table")  # noqa: F821


@dp.materialized_view(name="dim_content_current", table_properties=rules.table_properties())
@rules.apply("dim_content_current")
def dim_content_current():
    return spark.sql(f"""
        SELECT concat('cnt-', title_id) AS content_id, title_id, title_name, genre, content_type, provider,
               rights_start AS valid_from
        FROM {SOURCE}""")


@dp.materialized_view(name="fct_linear_as_run_event", table_properties=rules.table_properties())
@rules.apply("fct_linear_as_run_event")
def fct_linear_as_run_event():
    # Twelve two-hour slots a day per channel for 14 days; each slot airs a catalogue title picked
    # by hash and runs a little early or late, so the airtime numbers are not all round.
    return spark.sql("""
        WITH titles AS (SELECT content_id, row_number() OVER (ORDER BY content_id) - 1 AS n,
                               count(*) OVER () AS total FROM dim_content_current),
        slots AS (
          SELECT channel, ts AS scheduled_start
          FROM (SELECT explode(array('ch1', 'ch2', 'ch3')) AS channel)
          CROSS JOIN (SELECT explode(sequence(timestamp'2026-10-26 00:00:00', timestamp'2026-11-08 22:00:00',
                                              INTERVAL 2 HOURS)) AS ts)),
        events AS (
          SELECT channel, scheduled_start, abs(hash(channel, scheduled_start)) AS h FROM slots)
        SELECT concat(channel, '-', date_format(scheduled_start, 'yyyyMMddHHmm')) AS as_run_event_id,
               t.content_id, channel AS channel_code, scheduled_start,
               scheduled_start + make_interval(0, 0, 0, 0, 0, 0, h % 120 - 60) AS actual_start,
               scheduled_start + make_interval(0, 0, 0, 0, 0, 0, h % 120 - 60 + 5400 + h % 1800) AS actual_end,
               CAST(5400 + h % 1800 AS INT) AS duration_seconds
        FROM events e JOIN titles t ON t.n = e.h % t.total""")
