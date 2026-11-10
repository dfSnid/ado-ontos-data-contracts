# Data products, bundles and pipeline rules

## What a data product is here

A data product is one file in `data-contracts`, `products/<domain>/<name>.odps.yaml` (ODPS v1.0.0), and one bundle repository that builds its objects. The file lists the product's output ports; each port names a contract by its id and version. The publish pipeline registers the product in Ontos, owned by its domain's team, and moves it with its contracts.

`products/content/content.odps.yaml`

```yaml
# Content data product (ODPS v1.0.0). Registered in Ontos by the publish pipeline
# (dpi sync-products), owned by the content domain team from domains/domains.yaml.
# Each output port names a contract by its id and version; the port version must match the
# contract file. The status moves with the gates, behind checks on the port contracts:
#   draft (G1), proposed (G2), approved (G3: contracts approved; Ontos passes through
#   under_review), active (G4: contracts active, objects built by the bundle), then
#   deprecated, retired.
# certificationLevel (an Ontos level: Bronze, Silver, Gold) and publicationScope (none, domain,
# organization, external) need status active. Certification also sets the Certified badge in
# Unity Catalog. The Content Data Stewards approve every change to this file.
apiVersion: v1.0.0
kind: DataProduct
id: content
name: Content
version: 1.1.0
status: active
domain: content
tenant: dp-contracts
description:
  purpose: Content library and linear schedule data for programming, scheduling and content analytics.
  usage: Start with the Linear port's metric view for airtime; use the content dimension for detail.
  limitations: Synthetic example data.
outputPorts:
  - name: Linear
    version: 1.1.0
    contractId: content-linear
    description: Current content dimension, linear as-run events and the airtime metric view.
customProperties:
  - property: governanceTier
    value: 2
  - property: certificationLevel
    value: Silver
```

## The domain registry

`domains/domains.yaml` is the one list of business domains. Every contract and product names one. `dpi sync-domains` creates each domain in Ontos with its owner team and the team's groups. A platform owner reviews every change to it.

`domains/domains.yaml`

```yaml
# Domain registry: the one list of business domains. Every contract and data product names one.
# The publish pipeline (dpi sync-domains) creates or updates each domain in Ontos and its owner
# team, and adds the listed groups to the team. It only adds: removing a domain or a group here
# changes nothing in Ontos. A new domain also needs a value in the Unity Catalog domain tag and
# its catalogs, which the platform team adds (CI fails until the tag allows it).
# Changes need a platform owner's review (required reviewer on /domains).
domains:
  - name: content
    description: What the business makes, buys and schedules, from the content library to the linear schedule.
    team:
      name: content-domain
      title: Content domain team
      # Producers and Stewards own the products; the publisher group lets the pipeline manage them.
      groups: [dp-content-stewards, dp-content-producers, dp-publishers]
```

> [!WARNING]
> Keep `dp-publishers` in every domain team. Ontos shows a non-admin outside the owner team only active and deprecated items, so without it the publisher cannot see the drafts it creates.

## Status and gates

The product file's `status` is where the product should be. `dpi sync-products` walks Ontos there one allowed step at a time, and stops with an error when a gate is not met. Contract CI runs the same checks without Ontos, so most problems show on the pull request.

| Product status | Gate | Delivery gate |
|---|---|---|
| `draft`, `proposed` | None | G1, G2 |
| `under_review` | Every port contract is in Ontos |  |
| `approved` | Every port contract is at least approved | G3: approved before the build |
| `active` | Every port contract is active, and its objects exist in Unity Catalog | G4: in use |
| `deprecated`, `retired` | None | Consumers move off it |

Ontos passes through `under_review` on the way to `approved`, so a pull request can move a product several steps at once.

### Certification, badges and scope

- `certificationLevel` (a custom property) names an Ontos certification level, for example Bronze, Silver or Gold. It needs the product active. Removing it decertifies the product.
- A certified active product puts the Certified badge on its port objects in Unity Catalog. A deprecated product puts the Deprecated badge on them instead.
- `publicationScope` (`none`, `domain`, `organization` or `external`) also needs the product active.

## The order of a change

1. Contract pull request at `approved` (G3), with the product file at `approved` too. Publish reports the objects as not built yet; that is expected.
2. Bundle pull request. After merge the deploy pipeline builds in dev, then in prod once the approval is given. Its completion triggers publish, which applies the metadata to the new objects.
3. Contract and product pull request to `active` (G4), with any certification. Publish checks the objects exist before it moves anything to active.

A change to a data quality rule is a contract pull request only. After merge, the bundle redeploys with the new rule.

## The product bundle

`dp-content` is the template: a Databricks Asset Bundle that builds the Content product's objects. Copy it for each new product.

| Object | Kind | Built by |
|---|---|---|
| `dim_content_current` | Materialized view | The Lakeflow pipeline `content-linear` |
| `fct_linear_as_run_event` | Materialized view | The same pipeline |
| `mv_linear_airtime` | Metric view | The `content-build` job, after the pipeline refresh |

- **It owns the code and the data, never the metadata.** Comments, tags, grants and the Certified badge come from the contract, through publish.
- **Three targets.** `local` is a personal copy in a prefixed schema; `dev` and `prod` use the catalogs of the contract's `dev` and `prod` servers.
- **Metadata survives a rebuild.** Comments, tags and grants on a materialized view survive a refresh, a full refresh and a redeploy. The metric view is changed with `ALTER VIEW ... AS`, which keeps them too. `CREATE OR REPLACE` would drop them until the next publish.
- **A pipeline cannot take over a table.** Before the first deploy into a schema that still has the objects as tables, drop them. The next publish puts the metadata back.

## Pipeline rules from the contract

The Lakeflow pipeline applies the contract's row-level rules as expectations, so a rule changes only through a contract pull request the Stewards approve. Nothing is written in the pipeline code.

| In the contract | Expectation |
|---|---|
| `required: true` on a column | `<column> IS NOT NULL` (warns) |
| `nullValues` rule with `mustBe: 0` | `<column> IS NOT NULL` |
| `invalidValues` rule with `validValues` and `mustBe: 0` | `<column> IS NULL OR <column> IN (...)` |
| Any rule with the custom property `expectation` | That SQL expression, row by row |

Whole-table rules (unique, row counts, a `type: sql` count) are not row-level, so `datacontract test` keeps running them. Give a `type: sql` count an `expectation` custom property to apply it in the pipeline as well:

```yaml
quality:
  - type: sql
    name: as_run_ends_after_start
    query: SELECT count(*) FROM ${object} WHERE actual_end <= actual_start
    mustBe: 0
    dimension: consistency
    severity: warning
    customProperties:
      - property: expectation
        value: actual_end > actual_start
```

| `severity` | Pipeline action |
|---|---|
| `warning` (the default) or `info` | Keeps the row and counts it |
| `error` | Drops the row |
| `critical` | Fails the update |

### How the rules reach the pipeline

1. `src/pipeline/contracts.txt` lists the contracts the pipeline applies.
2. Before each deploy, `bin/fetch-rules.sh` in `dp-integration` copies them, and `expectations.py` as `contract_expectations.py`, next to the pipeline code. Both are git-ignored and listed under `sync.include` in `databricks.yml`.
3. The pipeline reads them with `ContractExpectations.from_file()` and wraps each dataset in `@rules.apply(...)`. `rules.table_properties()` sets the contract id and version, which publish cannot set on a pipeline dataset.
4. The `contracts` trigger in `pipelines/deploy.yml` redeploys the bundle when a listed contract changes on `main`. Keep its paths in step with `contracts.txt`.

Contract CI tries each rule against the built objects first (`dpi check-expectations`), so a rule that does not run fails the pull request, not the next pipeline update.

`src/pipeline/content_linear.py`

```python
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
```

`src/pipeline/contracts.txt`

```text
# Contracts whose row-level rules this pipeline applies, relative to the data-contracts root.
# bin/fetch-rules.sh in dp-integration copies them into contracts/ here before each deploy.
# Keep pipelines/deploy.yml's contracts trigger paths in step with this list.
contracts/content/content-linear.odcs.yaml
```

Full listing: [`dp-integration/bin/fetch-rules.sh`](../dp-integration/bin/fetch-rules.sh)

Full listing: [`dp-integration/src/dp_integration/expectations.py`](../dp-integration/src/dp_integration/expectations.py)

## Try a bundle change yourself

```bash
cd dp-content
../dp-integration/bin/fetch-rules.sh src/pipeline/contracts.txt ../data-contracts
databricks bundle validate -t local --profile <workspace-profile>
databricks bundle deploy -t local --profile <workspace-profile>
databricks bundle run build -t local --profile <workspace-profile>
```

Remove it with `databricks bundle destroy -t local --profile <workspace-profile>`.
