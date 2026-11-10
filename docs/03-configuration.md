# Configuration

## Two kinds of setting

Configuration lives in `dp-integration/config/`. Nothing in it is secret, so it is committed.

| File | Holds | Changes |
|---|---|---|
| `config/platform.yaml` | HOW contracts are published: tag keys and values, grant policy, Ontos roles, groups, log level | Rarely; the same in every environment |
| `config/environments/<env>.yml` | WHERE they are published: hosts, ids, service connection, names | Once per environment |

The environment file is also an Azure DevOps variable template. The pipelines include it directly, and `dpi` and the setup scripts read the same file, so a value is only ever written once.

## Order of precedence

Later wins:

1. `config/platform.yaml` and `config/environments/<env>.yml`.
2. `DP_<NAME>` environment variables, for example `DP_WAREHOUSE_ID`, `DP_ONTOS_URL` or `DP_LOG_LEVEL`. The name is the setting in upper snake case.
3. Command-line options: `--config-dir`, `--environment`, `--log-level`, `--profile`.

`DP_CONFIG_DIR` and `DP_ENVIRONMENT` choose the folder and the environment. The pipeline templates set both. An override is useful for one run, for example a different warehouse:

```bash
DP_WAREHOUSE_ID=<other-warehouse-id> dpi -e dev publish-uc --dry-run --profile <profile> contracts/**/*.odcs.yaml
```

## Create your environment files

The package ships `dev.yml` and `prod.yml` with placeholders. They differ in `contractServer` (`dev` or `prod`) and in the catalogs. The pipelines use both: publish runs dev, then prod, and syncs Ontos in prod (`ontosEnvironment`).

1. Fill in every `<...>` value you know now in each file. Leave `publisherClientId` and `ontosUrl` as they are: steps 5 and 6 print them.
2. For a further environment, copy `config/environments/example.yml` to `config/environments/<env>.yml`.
3. Keep the `variables:` mapping form (`name: value`). Quote values that look like numbers, such as `workspaceId`.
4. Check each: `dpi -e <env> config get env.databricksHost`. Exit code 2 and a message mean a value is missing.

A value still in `<...>` form counts as unset. Each setup script reads only the values it needs, and stops with exit code 2 and the key's name if one is empty or unset, before it changes anything. So you can run steps 4 and 5 before `ontosUrl` exists. The pipeline commands (`publish-uc`, `check-expectations`, `sync-ontos`, `sync-domains`, `sync-products`, `ontos-roles` and `ontos-check`) need `databricksHost` and `warehouseId` set; the Ontos commands also need `ontosUrl`.

| Key | Read by | Meaning |
|---|---|---|
| `serviceConnection` | Pipelines | Name of the ARM service connection (step 5) |
| `agentPool` | Pipelines | Self-hosted pool name; empty uses Microsoft-hosted |
| `vmImage` | Pipelines | Microsoft-hosted image, for example `ubuntu-latest` or `ubuntu-24.04` |
| `pythonVersion` | Pipelines | Python on the agent |
| `databricksHost` | All | Workspace host name only, without `https://` or a path, for example `adb-0000000000000000.0.azuredatabricks.net` |
| `workspaceId` | Setup | Numeric workspace id (quoted) |
| `warehouseId` | All | SQL warehouse for quality checks and publish SQL |
| `contractServer` | All | The `servers` entry in each contract that this environment publishes |
| `ontosUrl` | dpi | Ontos app URL, printed by `setup/ontos.sh` (step 6) |
| `databricksAccountId` | Setup | Databricks account id |
| `azureResourceGroup` | Setup | Resource group for the publisher identity |
| `adoOrganisation`, `adoProject` | Setup | Where the service connection is created |
| `publisherIdentityName` | Setup | Managed identity name |
| `publisherClientId` | Setup | Printed by `setup/publisher-identity.sh` (step 5) |
| `publisherCatalogs` | Setup | Space-separated catalogs the publisher manages in this environment |
| `publisherDeploysBundles` | Setup | `'true'` lets the publisher identity deploy product bundles too (decision 9) |
| `ontosAppName`, `ontosLakebaseProject`, `ontosAppCatalog` | Setup | Names for the Ontos app, its Lakebase project and its file catalog |
| `demoCatalog` | Setup | Catalog for the synthetic demo tables (skip them with `SKIP_DEMO_TABLES=1`) |

Full listing: [`dp-integration/config/environments/example.yml`](../dp-integration/config/environments/example.yml)

Full listing: [`dp-integration/config/environments/prod.yml`](../dp-integration/config/environments/prod.yml)

## Review platform.yaml

| Section | What to check |
|---|---|
| `tags` | `prefix` (decision 1), the key names, the tier and status mappings, and your domain and sensitivity values |
| `contract` | Which custom properties drive the tier and sensitivity tags, the table property prefix, the access file suffix, the mandatory-field schema |
| `grants` | `revoke_unlisted` and `parent_use` (decisions 2 and 3) |
| `publish` | `pending_statuses` (where a missing object is "not built yet" rather than an error) and `certification_tag` (the system tag behind the Certified and Deprecated badges) |
| `ontos` | The publisher role and its group (Read/Write on contracts, domains, products and teams, and the PRODUCTS approval privilege), and which account groups join the default Ontos roles |
| `setup` | Groups `bootstrap.sh` creates (decision 4) |
| `logging` | Default log level |

> [!IMPORTANT]
> If you change `tags.domain_values` or `tags.sensitivity_values`, change the matching enums in `schemas/mandatory.schema.json` too. A unit test fails if they differ.

Full listing: [`dp-integration/config/platform.yaml`](../dp-integration/config/platform.yaml)

## How the pipelines find the configuration

Every pipeline template includes `templates/variables.yml`, once per environment stage. It pulls in the environment file and tells `dpi` where the configuration is on the agent:

`templates/variables.yml`

```yaml
# Variables every stage of the contract pipelines uses: the environment file, plus what the
# dpi command needs to find it again at run time.
parameters:
  - name: environment
    type: string

variables:
  - template: /config/environments/${{ parameters.environment }}.yml
  - name: DP_CONFIG_DIR
    value: $(Build.SourcesDirectory)/integration/config
  - name: DP_ENVIRONMENT
    value: ${{ parameters.environment }}
  # Application id of the Azure Databricks resource in Entra ID, the same in every tenant.
  - name: azureDatabricksResource
    value: 2ff814a6-3304-4ab8-85cb-cd0e6f879c1d
```

## What is fixed on purpose

| Value | Why it is not a setting |
|---|---|
| `2ff814a6-3304-4ab8-85cb-cd0e6f879c1d` | Application id of Azure Databricks in Entra ID. The same in every tenant |
| `499b84ac-1321-427f-aa17-267ca6975798` | Application id of Azure DevOps in Entra ID. The same in every tenant |
| `contracts/**/*.odcs.yaml`, `products/**/*.odps.yaml`, `domains/domains.yaml` | The layout in `data-contracts` |
| Checkout paths `s/contracts`, `s/integration` and `s/bundle` | Fixed so the templates work whatever the repositories are called |
| Repository resource aliases `integration` and `contracts` | The names the templates check out; the calling pipeline declares them |

## Code-level injection

Every class takes its clients and policies as constructor arguments. Only `CommandContext` in `cli.py` creates real clients, so a test or an extension can pass its own. See [The dpi package](13-the-dpi-package.md).
