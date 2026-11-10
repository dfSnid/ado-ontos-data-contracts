# Ontos as a Databricks App

## What this step does

Ontos is the data contract and data product registry. The pipeline writes each domain, contract version, data product and the quality results there for people to browse. `setup/ontos.sh` deploys Ontos v1.0.2 as a Databricks App with a Lakebase database, then gives the publisher identity its own Ontos role. It is safe to re-run.

> [!NOTE]
> Ontos is display only at Tier 2. The pull request approval is the signature, and Unity Catalog enforces access. If Ontos is down, publishing fails at the Ontos step after Unity Catalog is already up to date. Re-run the pipeline when Ontos is back.

## 1. Build the Ontos frontend once

The app ships a prebuilt frontend. Build it on a machine with Node.js and npm:

```bash
git clone --branch v1.0.2 --depth 1 https://github.com/databrickslabs/ontos.git
./ontos/src/scripts/build_static.sh      # runs npm, then fills ontos/src/backend/static
ls ontos/src/backend/static/index.html
```

Stay on v1.0.2 until you have tested a newer release: the pipeline code works around two v1.0.2 bugs (see [Operations and troubleshooting](14-operations-and-troubleshooting.md)).

## 2. Before you start

- You are a workspace admin and in the workspace `admins` group (only an Ontos admin can change roles).
- `OAUTH_PROFILE` is a Databricks OAuth (U2M) login: `databricks auth login --host https://<workspace-host> --profile <oauth-profile>`. The Apps proxy in front of Ontos rejects Entra tokens.
- `publisherClientId`, `ontosAppName`, `ontosLakebaseProject` and `ontosAppCatalog` are set.
- Step 5 is done, so the publisher service principal exists.

## 3. Run it

```bash
cd dp-integration/setup
PROFILE=<workspace-profile> OAUTH_PROFILE=<oauth-profile> ACCOUNT_PROFILE=<account-profile> \
  ONTOS_SRC=<path>/ontos/src ENVIRONMENT=<env> ./ontos.sh
```

The last line it prints is the app URL. Put it in `ontosUrl` in your environment file and commit it. `FORCE_DEPLOY=1` redeploys an app that is already running.

## What each part does and why

| Part | What | Why |
|---|---|---|
| 1. Lakebase project | A Lakebase Autoscaling project; its `production` branch holds the Ontos database | Ontos keeps its metadata in Postgres |
| 2. Volume | `<ontosAppCatalog>.app_ontos.app_files` | Ontos stores uploaded files in a Unity Catalog volume |
| 3. Stage the source | Copies `ONTOS_SRC` to `ontos-app/` next to the repository without the frontend sources, tests and `package.json`. Turns Ask Ontos (LLM) off and points `app.yaml` at your Lakebase branch | With `package.json` present, the Apps build runs npm against sources it does not have and fails. LLM off means no serving endpoint is needed |
| 4. App | The app with three resources: Lakebase (CAN CONNECT AND CREATE), the warehouse (CAN USE) and the volume (WRITE VOLUME), plus the user API scopes Ontos asks for |  |
| 5. Deploy | Syncs the staged source to your workspace folder and deploys it, unless a deployment already succeeded |  |
| 6. Publisher access | CAN USE on the app for the publisher; puts it in the `dp-publishers` account group | The Apps proxy only lets in identities with CAN USE |
| 7. Ontos roles | `dpi ontos-roles`: creates the **Contract Publisher** role for `dp-publishers`, and adds the domain groups to the default Ontos roles | Ontos authorises by group. The publisher gets Read/Write on data contracts, data domains, data products and teams, read-only on the catalog, and the PRODUCTS approval privilege, which Ontos needs to record certification. The real approval is still the Steward's pull request |

The role definitions live in `platform.yaml` under `ontos.roles`. Preview a change with `dpi -e <env> ontos-roles --dry-run --profile <oauth-profile>`, then run it without `--dry-run`. Groups are only ever added to a role, never removed.

> [!WARNING]
> Keep `dp-publishers` in every domain team in `domains/domains.yaml`. Ontos v1.0.2 shows a caller who is not an Ontos admin only active and deprecated contracts and products, unless the caller is in the owner team. Without the publisher in the team, the sync loses sight of its own contract the first time it leaves draft.

## Check it worked

```bash
dpi -e <env> ontos-check --profile <oauth-profile>
```

It prints who Ontos thinks you are and your permissions. The same command, run as the publisher identity, is the smoke pipeline on [Pipelines in Azure DevOps](08-pipelines-in-azure-devops.md).

## The script

Full listing: [`dp-integration/setup/ontos.sh`](../dp-integration/setup/ontos.sh)
