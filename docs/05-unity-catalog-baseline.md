# Unity Catalog baseline

## What this step does

`setup/bootstrap.sh` creates the Unity Catalog baseline the publish pipeline writes into. It is safe to re-run.

| Part | What it creates | Usually |
|---|---|---|
| 1. Demo tables | A schema and two synthetic tables (`catalogue.title`, `catalogue.episode`) in `env.demoCatalog`, from `bootstrap.sql` | Skip with `SKIP_DEMO_TABLES=1` unless you want the demo data product |
| 2. Governed tag policies | One policy per tag (tier, domain, status, sensitivity), keys and allowed values from `platform.yaml` `tags` | Always |
| 3. Account groups | Every group in `setup.groups`, assigned to the workspace with USER | Usually skipped: sync the groups from Entra ID and set `setup.groups: []` |

## Before you start

- You are a Databricks account admin and a workspace admin.
- `env.warehouseId`, `env.workspaceId` and `env.demoCatalog` are set in your environment file.
- `dpi` is installed (step 3).
- Each environment's catalogs exist, for example `content_dev` and `content_prod`. On a metastore with default storage, create them with SQL (`CREATE CATALOG`), not the API.

## Run it

```bash
cd dp-integration/setup
PROFILE=<workspace-profile> ACCOUNT_PROFILE=<account-profile> ENVIRONMENT=<env> \
  SKIP_DEMO_TABLES=1 ./bootstrap.sh
```

`OWNER_EMAIL=<user>` adds one person to the Producer and Steward groups. It is only useful for a demo.

## Why the tag policies carry a marker

Each policy the script creates has `[dp-contracts]` at the end of its description. On a re-run the script only updates policies with that marker. A policy with the same key that someone else owns is reported as `SKIP` and left alone. Rename the marker in `bootstrap.sh` if you prefer your own word.

> [!WARNING]
> If the script prints `SKIP <key>: exists and is owned by someone else`, either reuse that policy (check its allowed values match `platform.yaml`) or pick a `tags.prefix` so the keys no longer clash.

## Check it worked

```bash
databricks tag-policies get-tag-policy $(dpi config tag-key tier) --profile <workspace-profile>
databricks account groups list --filter 'displayName eq "dp-content-stewards"' --profile <account-profile>
```

## The script

Full listing: [`dp-integration/setup/bootstrap.sh`](../dp-integration/setup/bootstrap.sh)

Full listing: [`dp-integration/setup/lib.sh`](../dp-integration/setup/lib.sh)
