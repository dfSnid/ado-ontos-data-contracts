# Data products, bundles and pipeline rules

## What a data product is here

A data product is one file in `data-contracts`, `products/<domain>/<name>.odps.yaml` (ODPS v1.0.0), and one bundle repository that builds its objects. The file lists the product's output ports; each port names a contract by its id and version. The publish pipeline registers the product in Ontos, owned by its domain's team, and moves it with its contracts.

`products/sales/orders.odps.yaml`

```yaml
# Orders data product (ODPS v1.0.0). Registered in Ontos by the publish pipeline
# (dpi sync-products), owned by the sales domain team.
# Each output port names a contract by its id and version; the port version must match the
# contract file. Move the status with the gates: draft (G1), proposed (G2), approved (G3: port
# contracts approved), active (G4: port contracts active, objects built by dp-orders).
# certificationLevel (Bronze, Silver or Gold) needs status active, and also sets the Certified
# badge in Unity Catalog.
apiVersion: v1.0.0
kind: DataProduct
id: orders
name: Orders
version: 1.0.0
status: active
domain: sales
description:
  purpose: Customer orders, with status, value and priority, for sales reporting.
  usage: Start with the Orders port, sales_prod.orders.orders.
  limitations: Built from the TPC-H sample data.
outputPorts:
  - name: Orders
    version: 1.0.0
    contractId: orders
    description: One row per order, with the contract's quality rules applied in the pipeline.
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
  - name: sales
    description: Orders customers place and what they are worth.
    team:
      name: sales-domain
      title: Sales domain team
      # Producers and Stewards own the products; the publisher group lets the pipeline manage them.
      groups: [dp-sales-stewards, dp-sales-producers, dp-publishers]
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

`dp-orders` is the worked example: a Databricks Asset Bundle that builds the Orders product's object from `samples.tpch.orders`. `dpi new-product` wrote it ([Start a new data product](16-start-a-new-data-product.md)); only the query, the columns and the rules were changed.

| Object | Kind | Built by |
|---|---|---|
| `orders` | Materialized view | The Lakeflow pipeline `orders-<target>`, refreshed by the `orders-build-<target>` job |

- **It owns the code and the data, never the metadata.** Comments, tags, grants and the Certified badge come from the contract, through publish.
- **Three targets.** `local` is a personal copy in a prefixed schema; `dev` and `prod` use the catalogs of the contract's `dev` and `prod` servers.
- **Metadata survives a rebuild.** Comments, tags and grants on a materialized view survive a refresh, a full refresh and a redeploy. If you add a metric view, change it with `ALTER VIEW ... AS`, which keeps them too. `CREATE OR REPLACE` would drop them until the next publish.
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
    name: total_price_positive
    query: SELECT count(*) FROM ${object} WHERE total_price <= 0
    mustBe: 0
    dimension: accuracy
    severity: warning
    customProperties:
      - property: expectation
        value: total_price > 0
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

`src/pipeline/orders.py`

```python
"""Lakeflow pipeline: the datasets of the orders contract, with the contract's rules.

Reads samples.tpch.orders (the `source_table` variable) and renames its columns. The column names
must match the contract, or publish fails on schema drift. The rules are not written here: the deploy
copies the merged contract and contract_expectations.py (from dp-integration) next to this file,
and `rules.apply` turns the contract's row-level rules into expectations on each dataset.
"""
from pyspark import pipelines as dp

from contract_expectations import ContractExpectations

rules = ContractExpectations.from_file(spark.conf.get("contract_path"))  # noqa: F821 (pipeline global)
SOURCE = spark.conf.get("source_table")  # noqa: F821


@dp.materialized_view(name="orders", table_properties=rules.table_properties())
@rules.apply("orders")
def orders():
    return spark.sql(f"""
        SELECT o_orderkey AS order_id, o_custkey AS customer_id, o_orderstatus AS order_status,
               CAST(o_totalprice AS DECIMAL(18,2)) AS total_price, o_orderdate AS order_date,
               o_orderpriority AS order_priority
        FROM {SOURCE}""")
```

`src/pipeline/contracts.txt`

```text
# Contracts whose row-level rules this pipeline applies, relative to the data-contracts root.
# bin/fetch-rules.sh in dp-integration copies them into contracts/ here before each deploy.
# Keep pipelines/deploy.yml's contracts trigger paths in step with this list.
contracts/sales/orders.odcs.yaml
```

Full listing: [`dp-integration/bin/fetch-rules.sh`](../dp-integration/bin/fetch-rules.sh)

Full listing: [`dp-integration/src/dp_integration/expectations.py`](../dp-integration/src/dp_integration/expectations.py)

## Try a bundle change yourself

```bash
cd dp-orders
../dp-integration/bin/fetch-rules.sh src/pipeline/contracts.txt ../data-contracts
databricks bundle validate -t local --profile <workspace-profile>
databricks bundle deploy -t local --profile <workspace-profile>
databricks bundle run build -t local --profile <workspace-profile>
```

Remove it with `databricks bundle destroy -t local --profile <workspace-profile>`.
