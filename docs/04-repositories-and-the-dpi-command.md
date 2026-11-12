# Repositories and the dpi command

## 1. Create the project and repositories

1. Create one Azure DevOps project for the data contract path, or use an existing one.
2. Create three empty Git repositories in it: `dp-integration`, `data-contracts` and `dp-content`. `dp-content` is the first product bundle; each further product gets its own repository.
3. Clone this repository. It has one folder per Azure DevOps repository.

## 2. Point the pipelines at your project

The pipeline files name the other repositories by project. Replace every `<ado-project>` with your project name (`scripts/split-repos.sh` does it for you):

| File | Names |
|---|---|
| `data-contracts/pipelines/contract-ci.yml` | `dp-integration` |
| `data-contracts/pipelines/publish.yml` | `dp-integration`, and the `dp-content-deploy` pipeline as a completion trigger |
| `dp-content/pipelines/deploy.yml` | `dp-integration` (the template and `fetch-rules.sh`) and `data-contracts` (the contracts its pipeline applies) |

`data-contracts/pipelines/contract-ci.yml`

```yaml
# PR build validation for all contracts. Wired to main by branch policy, so no CI trigger.
trigger: none

resources:
  repositories:
    - repository: integration
      type: git
      name: <ado-project>/dp-integration   # the project that holds dp-integration
      ref: refs/heads/main

extends:
  template: templates/contract-ci.yml@integration
  parameters:
    # config/environments/<environment>.yml in dp-integration: hosts, ids, service connection.
    environment: dev
```

`ref: refs/heads/main` means the pipelines always use the tooling on `dp-integration/main` and the contracts on `data-contracts/main`. A change reaches them only after it is merged there.

Also set the workspace host in `dp-content/databricks.yml` and in each contract's `servers`, and the catalogs of the `dev` and `prod` targets.

## 3. Fill in the environment files and push

Fill in `dp-integration/config/environments/dev.yml` and `prod.yml` as described on [Configuration](03-configuration.md), then make the three repositories and push them. The script fills in the project, commits each folder on `main` and adds the remote; it never pushes.

```bash
scripts/split-repos.sh <organisation> <project>     # writes ado-repos/<repo>
for repo in dp-integration data-contracts dp-content; do
  (cd "ado-repos/$repo" && git push -u origin main)
done
```

To push without a personal access token, use your Entra token:

```bash
T=$(az account get-access-token --resource 499b84ac-1321-427f-aa17-267ca6975798 --query accessToken -o tsv)
git -c http.extraheader="Authorization: Bearer $T" push -u origin main
```

## 4. Create the Stewards group in Azure DevOps

Create one Azure DevOps group per data product, for example **Content Data Stewards**, in Project settings, Permissions. Add the product's Data Stewards. The branch policy on [Branch policies](10-branch-policies.md) makes it a required reviewer for that product's contract and product folders.

## 5. Install the dpi command

The setup scripts and the pipelines use the `dpi` command from the `dp-integration` package. Install it in a virtual environment:

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install ./dp-integration            # or: pip install -e "./dp-integration[test]" to change the code
dpi --version
cd dp-integration && dpi -e <env> config get env.databricksHost
```

Run the unit tests once to check the install (131 tests, no network needed):

```bash
pip install -e "./dp-integration[test]" && (cd dp-integration && python -m pytest -q)
```

## The dpi command

| Command | Used by | What it does |
|---|---|---|
| `dpi check-mandatory <contracts>` | Contract CI | Mandatory-field check; no environment needed |
| `dpi check-products <products>` | Contract CI | Product files, the domain registry, port contracts and versions; no Ontos call |
| `dpi -e <env> check-expectations <contracts>` | Contract CI | Tries each contract's pipeline rules on the built objects; fails when a rule does not run |
| `dpi -e <env> publish-uc [--dry-run] <contracts>` | Publish | Unity Catalog tags, comments, properties, grants |
| `dpi -e <env> sync-domains domains/domains.yaml` | Publish | Ontos domains, owner teams and their groups (add only) |
| `dpi -e <env> sync-ontos --quality-dir <dir> <contracts>` | Publish | Ontos registration, versions, status, quality items |
| `dpi -e <env> sync-products [--dry-run] <products>` | Publish | Ontos data products: status behind the gates, certification, badges, publication scope |
| `dpi -e <env> ontos-roles [--dry-run]` | `setup/ontos.sh` | Maps groups onto Ontos roles (Ontos admin) |
| `dpi -e <env> ontos-check` | Smoke pipeline | Calls Ontos as the current identity |
| `dpi -e <env> config get <dotted.key>` | Setup scripts | Prints one setting, for example `env.warehouseId` or `setup.groups` |
| `dpi config tag-key <tier\|domain\|status\|sensitivity>` | Setup scripts | Full tag key, for example `tier` gives `dp_tier` |
| `dpi config tag-values <logical>` | Setup scripts | Allowed values of a governed tag |
| `dpi new-product --domain <d> --product <p>` | Data Producers | Writes a new product's contract, access and product files, and its bundle ([Start a new data product](16-start-a-new-data-product.md)) |

Options go before or after the command: `-c/--config-dir`, `-e/--environment`, `--log-level` and `--profile`. Exit codes: 0 success, 1 at least one contract or call failed, 2 a setting is missing or invalid.

## Repository layout

| Path | What it is |
|---|---|
| `dp-integration/config/` | `platform.yaml` and `environments/<env>.yml` |
| `dp-integration/src/dp_integration/` | The `dpi` package ([details](13-the-dpi-package.md)) |
| `dp-integration/schemas/mandatory.schema.json` | The mandatory-field overlay on ODCS |
| `dp-integration/templates/` | Pipeline templates: `contract-ci.yml`, `publish.yml` with `stages/publish-env.yml`, `bundle-deploy.yml`, `variables.yml`, `steps/install.yml` |
| `dp-integration/bin/fetch-rules.sh` | Copies a bundle's contracts and the expectations module into it, before a deploy |
| `dp-integration/pipelines/ci.yml` | Unit tests for `dp-integration` |
| `dp-integration/setup/` | One-off setup scripts and the Ontos smoke pipeline |
| `dp-integration/tests/` | pytest, with fake Databricks and Ontos clients |
| `data-contracts/contracts/<domain>/` | `<contract>.odcs.yaml` and `<contract>.access.yaml` |
| `data-contracts/products/<domain>/` | `<product>.odps.yaml`: the product, its output ports and target status |
| `data-contracts/domains/domains.yaml` | The domain registry: each domain and its owner team |
| `data-contracts/pipelines/` | `contract-ci.yml` and `publish.yml`, which extend the templates |
| `dp-content/databricks.yml`, `resources/content.yml` | The bundle: targets `local`, `dev` and `prod`; the pipeline and the build job |
| `dp-content/src/pipeline/` | The Lakeflow pipeline and `contracts.txt`, the contracts whose rules it applies |
| `dp-content/src/metric_view.py`, `src/mv_linear_airtime.yaml` | Creates or alters the metric view |
| `dp-content/pipelines/deploy.yml` | The deploy pipeline, which extends `bundle-deploy.yml` |
