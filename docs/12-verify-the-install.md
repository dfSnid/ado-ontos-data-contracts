# Verify the install

Use the example Orders product in this repository to prove the install. It reads
`samples.tpch.orders`, which every Databricks workspace has, so there is no data to load.
`bootstrap.sh` creates its catalog (`env.demoCatalog`) in each environment.

The example ships at `active`, the state at the end of these steps. To prove an install, take it
through the gates from the start.

## Prepare

1. In `data-contracts/contracts/sales/orders.odcs.yaml`, set `host` on both servers to your
   workspace. Set the same host in `dp-orders/databricks.yml`.
2. Put one real Business Data Owner and one real Data Steward in the contract's `team`.
3. Make sure two different people are available: a Producer and a member of the Stewards group.
4. On a branch of `data-contracts`, set `status: approved` in the contract and in
   `products/sales/orders.odps.yaml`, and remove `certificationLevel` from the product. The
   objects are not built yet, so this is as far as the first pull request can go (G3).

## The scenarios

| # | Do this | Expect |
|---|---|---|
| 1 | On the branch, also remove the Data Steward from the team. Open a pull request | Contract CI fails at Mandatory fields, with an annotation on the team line. Lint passes |
| 2 | Put the Steward back. Push | Contract CI passes. The pull request still cannot complete: the Stewards group is a required reviewer |
| 3 | A Steward approves. Complete the pull request | Publish runs. It reports `orders` as "not built yet", registers the contract in Ontos at approved, and registers the product, owned by the sales domain team, and walks it to approved (G3) |
| 4 | Run `dp-orders-deploy` on `main`, or merge a change to `dp-orders` | The dev stage deploys and runs the build. The prod stage waits for the `orders-prod` approval. When it completes, publish runs and puts the comments, tags and grants on `orders` in each catalog |
| 5 | In one pull request, set the contract and the product to `active` and put back `certificationLevel: Silver`. Merge it | Publish checks the objects exist, then moves both to active (G4). Ontos shows the product certified Silver. The view shows the Certified badge in Catalog Explorer |
| 6 | Look at the quality results | Ontos shows one quality item per dimension on the contract version, from `datacontract test`. The pipeline's event log shows the pass and fail counts of each expectation. The sample data passes every rule, so nothing is flagged |
| 7 | Add a rule: an `invalidValues` rule on `order_priority`. Bump the contract and the product's port to `1.1.0` | Contract CI runs check-expectations on the built view. After merge, the contracts trigger redeploys the bundle with the new rule. Ontos shows 1.1.0 as the latest version, with 1.0.0 as its parent |
| 8 | Break a rule on a branch: change the `expectation` on `total_price_positive` to name a column that does not exist | Contract CI fails at check-expectations, naming the rule and `UNRESOLVED_COLUMN`. A rule that only flags rows is a warning, never a failure |
| 9 | Run `setup/access-check.sh` | A member of the consumer group can query the view. A member of a group not in the access file is refused |

Close the pull request from scenario 8 without merging it.

## SQL checks for scenario 4

```sql
SELECT tag_name, tag_value FROM system.information_schema.table_tags
 WHERE catalog_name = '<demo-catalog>' AND schema_name = 'orders' AND table_name = 'orders';

SHOW TBLPROPERTIES <demo-catalog>.orders.orders ('dp.contract_version');

SHOW GRANTS ON TABLE <demo-catalog>.orders.orders;
```

## The access check

`access-check.sh` creates one Databricks-managed test service principal per group, makes each a member, and runs a `SELECT count(*)` as each. It uses a short-lived OAuth secret that is deleted straight after the query. Both test groups can use the warehouse, so the only difference is the grant from the publish pipeline.

```bash
cd dp-integration/setup
PROFILE=<workspace-profile> ACCOUNT_PROFILE=<account-profile> ENVIRONMENT=<env> ./access-check.sh
```

Expected output:

```text
dp-test-consumer (dp-sales-consumers): can query, <rows> rows
dp-test-other-analyst (dp-other-analysts): refused, [INSUFFICIENT_PERMISSIONS] ...
```

Delete the two test service principals when you are done.

Full listing: [`dp-integration/setup/access-check.sh`](../dp-integration/setup/access-check.sh)
