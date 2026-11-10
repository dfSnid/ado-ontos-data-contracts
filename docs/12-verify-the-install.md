# Verify the install

Use the synthetic Content catalogue product in this repository to prove the install. Run `bootstrap.sh` without `SKIP_DEMO_TABLES` to create its tables, or point the contract at tables of your own.

## Prepare

1. In `data-contracts/contracts/content/content-catalogue.odcs.yaml`, set the `dev` server `host`, `catalog` and `schema` to your workspace and demo catalog.
2. Add one real Business Data Owner and one real Data Steward to its `team`.
3. Make sure two different people are available: a Producer and a member of the Stewards group.

## The six scenarios

| # | Do this | Expect |
|---|---|---|
| 1 | On a branch, remove the Data Steward from the team. Open a pull request | Contract CI fails at Mandatory fields, with an annotation on the team line. Lint passes |
| 2 | Put the Steward back. Push | Contract CI passes. The pull request still cannot complete: the Stewards group is required |
| 3 | A Steward approves. Complete the pull request | The publish pipeline runs. Unity Catalog shows the tags, comments, contract properties and grants. Ontos shows the contract at its version, active |
| 4 | Add a column to the table (as the product's own deploy would), add it to the contract, bump the version to the next minor, and merge through a pull request | Ontos shows the new version as the latest. The old version stays visible as its parent. `dp.contract_version` on the tables is the new version |
| 5 | Look at the quality results | The synthetic data has two titles whose rights end before they start. The rights-window rule warns in Contract CI and lands in Ontos as consistency 0%. Nothing is blocked |
| 6 | Run `setup/access-check.sh` | A member of the consumer group can query the table. A member of a group not in the access file is refused |

## The product checks

Then take the Content product through its gates ([Data products, bundles and pipeline rules](15-data-products-bundles-and-pipeline-rules.md)). It needs the `content-linear` contract and the `dp-content` bundle.

| # | Do this | Expect |
|---|---|---|
| 7 | In one pull request, set `content-linear` and `products/content/content.odps.yaml` to `approved`. Merge it | Contract CI passes check-products. Publish reports the three objects as "not built yet", registers the product owned by the content domain team and walks it to approved (G3) |
| 8 | Merge a change to `dp-content`, or run `dp-content-deploy` by hand | The dev stage deploys and runs the build. The prod stage waits for the content-prod approval. When it completes, publish runs and puts the comments, tags and grants on the new objects |
| 9 | In one pull request, set the contract and the product to `active` and add `certificationLevel: Silver`. Merge it | Publish checks the objects exist, then moves both to active (G4). Ontos shows the product certified Silver. The port objects show the Certified badge in Catalog Explorer |
| 10 | Change a pipeline rule in the contract, for example the `expectation` on `as_run_ends_after_start` | Contract CI runs check-expectations on the built objects. A rule that does not run fails the pull request; flagged rows are only warnings. After merge, the contracts trigger redeploys the bundle with the new rule |

## SQL checks for scenario 3

```sql
SELECT tag_name, tag_value FROM system.information_schema.table_tags
 WHERE catalog_name = '<demo-catalog>' AND schema_name = 'catalogue' AND table_name = 'title';

SHOW TBLPROPERTIES <demo-catalog>.catalogue.title ('dp.contract_version');

SHOW GRANTS ON TABLE <demo-catalog>.catalogue.title;
```

## The access check

`access-check.sh` creates one Databricks-managed test service principal per group, makes each a member, and runs a `SELECT count(*)` as each. It uses a short-lived OAuth secret that is deleted straight after the query. Both test groups can use the warehouse, so the only difference is the grant from the publish pipeline.

```bash
cd dp-integration/setup
PROFILE=<workspace-profile> ACCOUNT_PROFILE=<account-profile> ENVIRONMENT=<env> ./access-check.sh
```

Expected output:

```text
dp-test-consumer (dp-content-consumers): can query, 200 rows
dp-test-other-analyst (dp-other-analysts): refused, [INSUFFICIENT_PERMISSIONS] ...
```

Delete the two test service principals when you are done.

Full listing: [`dp-integration/setup/access-check.sh`](../dp-integration/setup/access-check.sh)

## Results from testing

All six passed on Azure Databricks in November 2026, on Microsoft-hosted agents with synthetic data. Contract CI, publish (also re-run on an unchanged contract), the smoke pipeline and the unit tests pipeline all passed.

- Monday 9 November 2026: the Content product passed G3 before its objects were built, then the bundle deploy, then G4. It ended active and certified Silver, with the Certified badge on its port objects.
- Tuesday 10 November 2026: on a personal (`local`) deploy, the pipeline applied 14 rules from the contract to the two materialized views (200 and 504 rows), and none flagged a row. A rule with a misspelt column (`actual_endd`) made check-expectations name the rule and the error (`UNRESOLVED_COLUMN`) and exit 1. `datacontract test` still passed all 56 checks.
