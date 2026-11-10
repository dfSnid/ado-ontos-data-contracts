# dp-content

The Content data product's Databricks Asset Bundle. It builds the objects
the `content-linear` contract in `data-contracts` names:

| Object | Kind | Built from |
|---|---|---|
| `dim_content_current` | materialized view | the content catalogue (`content_dev.catalogue.title`) |
| `fct_linear_as_run_event` | materialized view | a synthetic two-week schedule on channels ch1 to ch3 |
| `mv_linear_airtime` | metric view | the fact, joined to the dimension (`src/mv_linear_airtime.yaml`) |

The two materialized views come from the Lakeflow pipeline `content-linear`
(`src/pipeline/content_linear.py`). The `content-build` job refreshes it, then creates or alters
the metric view (`src/metric_view.py`).

All data is synthetic.

## What this repository owns, and what it does not

The bundle owns the code and the data: schema `content`, the pipeline, the `content-build` job and
the objects they write. It never sets comments, tags, grants or the Certified badge. The publish
pipeline in `data-contracts` applies those from the contract after each deploy.

Two things on a pipeline's datasets only the pipeline can set, so it takes them from the
contract:

- **Data quality rules.** The pipeline applies the contract's row-level rules as Lakeflow
  expectations (`@rules.apply(...)`). A rule changes only through a contract PR, never here. A
  rule's `severity` sets what it does: warning keeps the row and counts it, error drops it,
  critical fails the update. The pipeline's event log records the counts.
- **The contract id and version table properties.** Unity Catalog refuses `SET TBLPROPERTIES` on
  a pipeline dataset, so the pipeline sets them (`rules.table_properties()`). Publish only checks
  them, and warns when they are out of date.

The rules come from the contracts in `src/pipeline/contracts.txt`. The deploy copies each one, and
the module that reads them (`contract_expectations.py`), into `src/pipeline/` before it deploys.
Both are git-ignored, so the pipeline always runs the merged contract's rules. A merged change to
a listed contract redeploys this bundle (the `contracts` trigger in `pipelines/deploy.yml`).

Comments, tags and grants on the materialized views survive a refresh, a full refresh and a
redeploy. The metric view is changed with `ALTER VIEW ... AS`, which keeps them too. Do not change
it to `CREATE OR REPLACE`: that drops them until the next publish.

A pipeline cannot take over an existing table. Before the first deploy of the pipeline into a
schema that still has `dim_content_current` or `fct_linear_as_run_event` as tables, drop them.
The next publish puts the metadata back.

## Targets

| Target | Catalog | Used by |
|---|---|---|
| `local` (default) | `content_dev`, schema prefixed with your name | you, to try a change |
| `dev` | `content_dev` | `pipelines/deploy.yml`, after merge |
| `prod` | `content_prod` | `pipelines/deploy.yml`, after the content-prod approval |

The catalogs match the `dev` and `prod` servers of the contract.

## Order of a change

1. Contract PR in `data-contracts` at `approved` (G3). The publish pipeline reports the objects as
   not built yet; that is expected.
2. Bundle PR here. After merge, `pipelines/deploy.yml` deploys and runs the build in dev, then in
   prod once the approval is given. Its completion triggers the publish pipeline, which applies the
   metadata to the new objects.
3. Contract and product PR to `active` (G4). The publish pipeline checks the objects exist before
   it moves anything to active.

A change to a data quality rule is a contract PR only. Its CI tries the rule against the built
objects (`dpi check-expectations`) and fails when the rule does not run. After merge, the
`contracts` trigger redeploys this bundle with the new rule.

## Try it yourself

The deploy pipeline copies the contract rules in. Locally, copy them yourself first, from
checkouts of `data-contracts` and `dp-integration` beside this one:

```bash
../dp-integration/bin/fetch-rules.sh src/pipeline/contracts.txt ../data-contracts
databricks bundle validate -t local --profile <your profile>
databricks bundle deploy -t local --profile <your profile>
databricks bundle run build -t local --profile <your profile>
```

## Pipeline setup (once)

- Create the ADO Environments `content-dev` and `content-prod`; add an Approvals check on
  `content-prod`.
- Create the pipeline from `pipelines/deploy.yml` and name it `dp-content-deploy`. The publish
  pipeline in `data-contracts` refers to it by that name.
- Run it once, by hand, before its entry goes into the publish pipeline. ADO fails a pipeline
  whose pipeline resource has no successful run yet ("Unable to resolve latest version").
- The pipeline deploys as the publisher identity (a shortcut for a trial). Run
  `setup/publisher-identity.sh` in `dp-integration` with `publisherDeploysBundles: 'true'`.
