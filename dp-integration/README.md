# dp-integration

Shared tooling for Tier 2 data contracts and data products: the `dpi` command, the Azure DevOps
pipeline templates and the one-off setup scripts. The contracts, the product files and the
domain registry live in the data-contracts repository; each product's bundle has its own
repository (for example dp-content). Synthetic example data only.

## Layout

| Path | What it is |
|---|---|
| `config/platform.yaml` | HOW contracts are published: tag keys and values, grant policy, Ontos roles, groups |
| `config/environments/<name>.yml` | WHERE: hosts, ids, service connection. Also an ADO variable template |
| `config/environments/example.yml` | Placeholder copy to start a new environment from |
| `config/environments/dev.yml`, `prod.yml` | Two example environments: one workspace, catalogs `content_dev` and `content_prod` |
| `src/dp_integration/` | The `dpi` package (see below) |
| `schemas/mandatory.schema.json` | Mandatory-field overlay used by `dpi check-mandatory` |
| `templates/contract-ci.yml` | PR validation: blocking lint, mandatory fields, products and domains; advisory quality |
| `templates/publish.yml` | After merge: per environment, advisory quality then Unity Catalog; Ontos once |
| `templates/stages/publish-env.yml` | One environment of `publish.yml` |
| `templates/bundle-deploy.yml` | A product bundle's deploy: per environment, copy in the contract rules, deploy and run the build job |
| `bin/fetch-rules.sh` | Copies a bundle's contracts and `expectations.py` into it, before a deploy |
| `templates/variables.yml`, `templates/steps/install.yml` | Shared by the templates |
| `pipelines/ci.yml` | Unit tests for this repository |
| `setup/*.sh`, `setup/ontos-smoke.yml` | One-off setup, run by a platform engineer |
| `tests/` | pytest, with fake Databricks and Ontos clients |

## Configuration

Settings are read in this order, later wins:

1. `config/platform.yaml` and `config/environments/<name>.yml`
2. `DP_<NAME>` environment variables, for example `DP_WAREHOUSE_ID`, `DP_ONTOS_URL`, `DP_LOG_LEVEL`
3. command-line options (`--environment`, `--log-level`, `--config-dir`)

`DP_CONFIG_DIR` and `DP_ENVIRONMENT` pick the folder and the environment. The pipeline
templates set both. No secrets are kept in any file: the pipelines use workload identity
federation, and local runs use a Databricks CLI profile.

## The dpi command

```
pip install .                      # or: pip install -e ".[test]" for development
dpi check-mandatory contracts/**/*.odcs.yaml
dpi -e dev publish-uc --dry-run --profile <profile> contracts/**/*.odcs.yaml
dpi -e dev sync-ontos --quality-dir <dir> contracts/**/*.odcs.yaml
dpi -e dev check-expectations --profile <profile> contracts/**/*.odcs.yaml   # pipeline rules, on the warehouse
dpi check-products products/**/*.odps.yaml           # registry, contracts and products, no Ontos call
dpi -e prod sync-domains domains/domains.yaml
dpi -e prod sync-products --dry-run --profile <oauth-profile> products/**/*.odps.yaml
dpi -e dev ontos-roles --dry-run --profile <oauth-profile>
dpi -e dev ontos-check --profile <oauth-profile>
dpi -e dev config get env.warehouseId
dpi new-product --domain content --product orders   # contract, product file and bundle, from templates
```

Exit codes: 0 success, 1 a contract or call failed, 2 a setting is missing or invalid.
Logs go to stderr. In a pipeline, warnings and errors also become ADO annotations on the file
and line they relate to.

| Module | Classes |
|---|---|
| `config.py` | `Settings`, `Environment`, `TagPolicy`, `ContractFields`, `GrantPolicy`, `OntosPolicy` |
| `contract.py` | `DataContract`, `AccessPolicy`, `Version`, `ContractTable` |
| `mandatory.py` | `MandatoryFieldChecker`, `LineIndex`, `Finding` |
| `unity_catalog.py` | `UnityCatalogPublisher`, `StatementRunner` |
| `quality.py` | `QualityResults`, `QualityItem` |
| `expectations.py` | `ContractExpectations`, `Expectation`: Lakeflow expectations from a contract (standalone, copied into bundles) |
| `expectation_check.py` | `ExpectationCheck`: tries those rules on the warehouse |
| `ontos.py` | `OntosClient`, `StaticToken`, `WorkspaceTokenExchange`, `SdkToken` |
| `ontos_sync.py` | `OntosContractSync` |
| `ontos_roles.py` | `OntosRoleMapper` |
| `domains.py` | `DomainRegistry`, `Domain`, `Team`, `OntosDomainSync` |
| `products.py` | `DataProductFile`, `OutputPort`, `OntosProductSync`, `GateError` |
| `lifecycle.py` | Ontos status transitions and `walk` (the steps between two statuses) |
| `new_product.py` | `NewProduct`: renders `product_template/` for `dpi new-product` |
| `cli.py` | `CommandContext` (builds the clients) and the commands |
| `logs.py` | `SourceFormatter`, `AzureDevOpsIssueHandler` |

The classes take their clients and policies as constructor arguments; only `CommandContext`
creates real clients. To change behaviour, change `platform.yaml` first, then inject a
different client or policy, then subclass.

## Data products and domains

`domains/domains.yaml` is the one list of domains. Each domain names the Ontos team that owns its
products and contracts, and the groups in that team. Keep the publisher group in every team:
outside its teams, a caller who is not an Ontos admin sees only active and deprecated entities.
`sync-domains` creates or updates them in Ontos and never
deletes. The Unity Catalog catalogs and the allowed values of the domain tag stay with the
platform team; `check-products` fails when a registered domain is not an allowed tag value.

A product file (`products/<domain>/<name>.odps.yaml`, ODPS v1.0.0) lists output ports, each
naming a contract id and version. Its `status` is where the product should be; `sync-products`
walks Ontos there one allowed step at a time, behind these gates:

| Product status | Needs |
|---|---|
| `under_review` | every port contract in Ontos |
| `approved` | every port contract at least approved (objects may not exist yet) |
| `active` | every port contract active, and its objects built in Unity Catalog |

`certificationLevel` (an Ontos level name) and `publicationScope` need the product active.
Certification also sets the Certified badge (`system.certification_status`) on the port
objects; a deprecated product gets the Deprecated badge.

## Pipeline rules from the contract

A product bundle with a Lakeflow pipeline applies its contract's row-level rules as expectations,
so the rules stay in the contract and change only through a Steward-approved PR.
`src/dp_integration/expectations.py` reads them (its docstring has the mapping and an example).
It needs only PyYAML, so `bin/fetch-rules.sh` copies it into the bundle as
`contract_expectations.py`, with the contracts the bundle lists. `templates/bundle-deploy.yml`
runs the script when the bundle sets `rulesManifest`.

| Contract | Expectation | Action |
|---|---|---|
| `required: true` | `<column> IS NOT NULL` | warn |
| `nullValues`, `mustBe: 0` | `<column> IS NOT NULL` | from `severity` |
| `invalidValues` with `validValues`, `mustBe: 0` | `<column> IS NULL OR <column> IN (...)` | from `severity` |
| any rule with the custom property `expectation` | its value, row by row | from `severity` |

Severity warning (the default) warns, error drops the row, critical fails the update. Give a
`type: sql` count its row-level form with the `expectation` custom property rather than writing a
`type: custom` rule: `datacontract test` skips a custom rule for an engine it does not know, so
Ontos would lose its score. Whole-table rules (unique, row counts) stay with `datacontract test`.

Contract CI runs `check-expectations`: one warehouse query per object counts the rows each rule
would flag (warnings), and a rule that does not run fails the PR. A pipeline dataset
(`physicalType: materialized_view`) refuses `SET TBLPROPERTIES`, so the pipeline sets the contract
id and version itself (`table_properties()`), and `publish-uc` only checks them. Comments use
`COMMENT ON TABLE` and tags `ALTER MATERIALIZED VIEW`.

## Environments

The publish pipeline runs one stage per environment, in order. A contract is published only in
the environments it lists as servers, so a dev-only contract is skipped in prod. There is one
Ontos, updated in the stage named by `ontosEnvironment` (prod), with every environment's
quality results.

A product bundle deploys with `templates/bundle-deploy.yml`, dev first and then prod behind the
approval on the prod ADO Environment. The publish pipeline has the deploy pipeline as a
completion trigger. Add a bundle's entry to `pipelines/publish.yml` in `data-contracts` only after
its deploy pipeline has one successful run: ADO fails a pipeline whose pipeline resource has none
yet. After a workspace rebuild, run each deploy pipeline once before the first publish. A trial setup
can let the publisher identity deploy bundles too (`publisherDeploysBundles: 'true'`); a
production setup uses a separate deploy identity.

## Setup order

On a fresh workspace, with `pip install .` done and the environment file
filled in. Leave `publisherClientId` and `ontosUrl` as their `<placeholder>` values: steps 2 and 3
print them. A script that reads a value that is still empty or a placeholder stops with exit
code 2 and names it, before it changes anything.

1. `setup/bootstrap.sh`: tag policies, groups, and the synthetic demo tables
2. `setup/publisher-identity.sh`: managed identity, WIF service connection, Databricks rights;
   put the client id it prints into `publisherClientId`. Run it once per environment file
   (`ENVIRONMENT=prod` after creating the prod catalog)
3. `setup/ontos.sh`: Ontos as a Databricks App, publisher access, role mapping; put the URL it
   prints into `ontosUrl`
4. run `setup/ontos-smoke.yml`, then authorise the contract pipelines on the service connection
   (`PIPELINE_IDS` for step 2)
5. `setup/access-check.sh` after the first publish: scenario 6 in `docs/12-verify-the-install.md`
