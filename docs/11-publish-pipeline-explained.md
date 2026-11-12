# Publish pipeline explained

## What it does

The publish pipeline runs after every merge to `data-contracts/main` that changes `contracts/`, `products/` or `domains/`, and after every product bundle deploy. The Steward approved the pull request, so the pipeline does not ask again. It makes Unity Catalog and Ontos match `main`.

It runs one stage per environment in `environments` (for example `[dev, prod]`), each after the one before. Ontos is updated once, in the stage named by `ontosEnvironment`.

| Step | Where | Command | Effect on failure |
|---|---|---|---|
| Quality checks | Every stage | `datacontract test` per contract with a server in this environment, JSON results kept as artifact `quality-<env>` | Warning only; the run goes on |
| Unity Catalog | Every stage | `dpi publish-uc contracts/**/*.odcs.yaml` | The run fails |
| Merge quality results | Ontos stage | Downloads every `quality-*` artifact; this environment's results win |  |
| Domains | Ontos stage | `dpi sync-domains domains/domains.yaml` | The run fails |
| Contracts | Ontos stage | `dpi sync-ontos --quality-dir <dir> contracts/**/*.odcs.yaml` | The run fails |
| Products | Ontos stage | `dpi sync-products products/**/*.odps.yaml` | The run fails |

A contract publishes only where it lists a server named like the environment's `contractServer`. A dev-only contract is skipped in prod, and still gets quality items in Ontos from the dev results.

## How it signs in

1. The AzureCLI task signs in as the publisher identity through the service connection. No secret is involved.
2. `az account get-access-token --resource <Azure Databricks application id>` gives an Entra token. The step exports it as `DATABRICKS_TOKEN`.
3. `dpi publish-uc` uses that token directly against the workspace.
4. `dpi sync-domains`, `sync-ontos` and `sync-products` exchange it for a Databricks token at `<workspace>/oidc/v1/token` (OAuth token exchange, scope `all-apis`). The Apps proxy in front of Ontos accepts only Databricks tokens. The exchanged token is cached until five minutes before it expires.

## dpi publish-uc

For each contract it reads the server named `contractServer` to find the catalog and schema, then for each object (table, view, materialized view or metric view):

1. Compares the object's kind and columns with the contract. On any difference it logs an error against the contract file, skips the object and fails the run. A metric view's columns are its dimensions and measures.
2. Sets the object comment and each column comment, only where they differ.
3. Sets the four governed tags: tier from `governanceTier`, domain (one of `tags.domain_values`), status from `status`, sensitivity from `securityClassification`.
4. Sets `dp.contract_id`, `dp.contract_version` and `dp.contract_commit` (the merge commit), only where they differ. A materialized view refuses properties from outside its pipeline, so the pipeline sets the id and version and publish warns when they are out of date.
5. Makes the direct grants on the object match the access file. With `revoke_unlisted: true` it revokes anything not listed. The object owner is never affected.

Then, with `parent_use: true`, it adds USE CATALOG and USE SCHEMA for every principal in the access file. These are added only, never revoked, because other products share the catalog and schema.

An object that does not exist yet is reported as "not built yet" and skipped while the contract status is in `publish.pending_statuses` (draft to approved). Once the contract is active, a missing object fails the run.

Preview without changing anything:

```bash
cd data-contracts
dpi -c ../dp-integration/config -e <env> publish-uc --dry-run --profile <workspace-profile> contracts/**/*.odcs.yaml
```

### The access file

Each contract has an access file next to it with the same name and `.access.yaml`. It lists account groups, privileges and objects:

`contracts/sales/orders.access.yaml`

```yaml
# Access for the Orders contract. Applied by the publish pipeline after merge, in every
# environment the contract has a server for. This is the full list of direct grants on the
# contract's objects: publish revokes anything else. Never grant by hand.
contract: orders
grants:
  - principal: dp-sales-consumers
    privileges: [SELECT]
    objects: [orders]
  - principal: dp-sales-stewards
    privileges: [SELECT]
    objects: [orders]
```

## dpi sync-domains

Creates or updates each domain in `domains/domains.yaml`, its owner team and the team's groups. It only adds: removing a domain or a group from the file changes nothing in Ontos.

## dpi sync-ontos

Ontos cannot update a contract in place by its file. `dpi` finds the Ontos records of a contract by the `sourceId` custom property (the contract `id`) and compares versions:

| Ontos has | dpi does |
|---|---|
| Nothing for this contract | Imports the file as the first version, owned by the domain's team |
| Older versions only | Creates the new version from the newest older one, so Ontos keeps one contract with its history. Fills the draft from the file, then walks it to the file's status. Deletes the draft if filling it fails |
| This version | Walks the status to the file's, one allowed step at a time |

Then it adds one quality item per dimension found in the quality results of this contract version, for example completeness or consistency. A check with no known dimension counts as "other".

An older version stays as it was in Ontos (often active). Deprecate it with a pull request when consumers have moved.

## dpi sync-products

Registers each product file in Ontos, owned by its domain's team, and walks it to the status in the file behind gates on its port contracts. Then it sets certification, the Certified badge on the port objects, and publication scope. The gates and badges are on [Data products, bundles and pipeline rules](15-data-products-bundles-and-pipeline-rules.md).

## Safe to re-run

A second run with nothing changed only re-sets the tags, which is harmless. In testing, a re-run on an unchanged contract made no comment, grant or Ontos version changes. It re-set the contract properties only because the commit was different, and added a fresh set of quality items.

## When it fails

- Every error is logged against the contract or product file. In Azure DevOps it becomes an error annotation on the run.
- `dpi` carries on with the other contracts, then exits 1, so one bad contract does not hold up the rest. That includes a file that is not valid YAML: it fails on its own, with the line in the annotation.
- A failed stage stops the later ones, so prod and Ontos wait until dev publishes cleanly.
- Azure DevOps sends its normal failed-run notification. Nothing custom is wired.
- Fix the cause (usually drift between an object and its contract, or an object not built yet at active) and re-run the pipeline. Unity Catalog is updated before Ontos, so a re-run after an Ontos failure is safe.

## The pipeline files

`data-contracts/pipelines/publish.yml`

```yaml
# Publish after merge: the Steward's PR approval is the sign-off, so main is what gets applied.
# Also runs when a data product's bundle deploy completes, so the metadata is applied to the
# objects the deploy just built (G3 to G4).
trigger:
  branches:
    include:
      - main
  paths:
    include:
      - contracts/**
      - products/**
      - domains/**
pr: none

resources:
  repositories:
    - repository: integration
      type: git
      name: <ado-project>/dp-integration   # the project that holds dp-integration
      ref: refs/heads/main
  pipelines:
    # One entry per data product bundle; source is the ADO pipeline name.
    - pipeline: orders_deploy
      source: dp-orders-deploy
      project: <ado-project>             # the project that holds dp-orders
      trigger:
        branches:
          include:
            - main

extends:
  template: templates/publish.yml@integration
  parameters:
    # config/environments/<name>.yml in dp-integration: hosts, ids, service connection.
    environments: [dev, prod]
    ontosEnvironment: prod
```

Full listing: [`dp-integration/templates/publish.yml`](../dp-integration/templates/publish.yml)

Full listing: [`dp-integration/templates/stages/publish-env.yml`](../dp-integration/templates/stages/publish-env.yml)
