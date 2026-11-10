# Data contracts and data products on Azure Databricks

A working reference implementation of contract-governed data products on Azure Databricks,
driven from Azure DevOps. Data contracts (ODCS 3.1.0) and data product files (ODPS v1.0.0)
live in git. Pipelines validate every change, deploy the product's Databricks Asset Bundle and
publish the metadata to Unity Catalog and to [Ontos](https://github.com/databrickslabs/ontos).
The Data Steward's pull request approval is the business sign-off. No secret is stored
anywhere: the pipelines sign in with workload identity federation.

> "Azure DevOps decides, the Data Steward signs, Unity Catalog enforces."

```text
Data Producer edits a contract or product file on a branch
  -> pull request to main
  -> contract CI: ODCS lint, mandatory fields, products and domains (blocking),
     quality checks (advisory), pipeline rules (blocking only when a rule does not run)
  -> Data Stewards for the product approve (required reviewer)
  -> merge
  -> product bundle deploy, dev then prod (approval): builds the objects and applies
     the contract's row-level rules as Lakeflow expectations
  -> publish pipeline, as the publisher identity, per environment:
       Unity Catalog: governed tags, comments, contract properties, grants
     and once, in the Ontos environment:
       Ontos: domains and teams, contracts (version, status, quality results),
              data products (status behind gates, certification, Certified badge)
```

This covers Tier 2: domain data products, where the product's Stewards approve changes in the
pull request and Ontos is the registration and display copy. Tier 1 (enterprise products, with
a Business Data Owner sign-off in Ontos) is a separate path and is not part of this repository.

## What is in this repository

Each top-level folder becomes its own Azure DevOps repository.

| Folder | What it is | Owned by |
|---|---|---|
| [`dp-integration/`](dp-integration/README.md) | The shared tooling: the `dpi` Python package, the Azure DevOps pipeline templates, the configuration and the one-off setup scripts | Platform team |
| [`data-contracts/`](data-contracts/README.md) | The contracts and access files, the data product files, the domain registry and two small pipeline files | Data Producers and Stewards |
| [`dp-content/`](dp-content/README.md) | An example product bundle: a Lakeflow pipeline, a build job and a metric view for the `content` product | Data Producers |
| [`docs/`](docs/README.md) | The install guide, step by step, and the reference pages | |
| `scripts/split-repos.sh` | Turns the three folders into three Git repositories for Azure DevOps | |

The example domain, contracts and product use synthetic data: a content catalogue and a
two-week linear schedule on placeholder channels.

## Try it on your machine

The unit tests and the contract checks need no cloud access. With Python 3.11 or later:

```bash
git clone <this repository> && cd <repository folder>
python3 -m venv .venv && . .venv/bin/activate
pip install -e "./dp-integration[test]"
(cd dp-integration && python -m pytest -q)

cd data-contracts
export DP_CONFIG_DIR=../dp-integration/config
dpi check-mandatory contracts/*/*.odcs.yaml
dpi check-products products/*/*.odps.yaml
```

Then try a failing change, for example remove the Data Steward from `team` in
`contracts/content/content-catalogue.odcs.yaml` and run `dpi check-mandatory` again.

## Install it

You need:

- An Azure subscription and a resource group for the publisher identity.
- An Azure Databricks Premium workspace with Unity Catalog, a serverless SQL warehouse,
  Databricks Apps and Lakebase Autoscaling.
- An Azure DevOps organisation connected to the same Entra ID tenant.
- On your machine: the Azure CLI with the `azure-devops` extension, the Databricks CLI 0.294 or
  later, Python 3.11 or later, Node.js (once, to build the Ontos frontend), git and rsync.

Follow the [install guide](docs/README.md). In short:

1. Record the decisions in [prerequisites](docs/02-prerequisites-and-decisions.md): tag names,
   environments, domains and who deploys bundles.
2. Fill in `dp-integration/config/environments/dev.yml` and `prod.yml`. Every value that is
   yours is a `<placeholder>`; a script that reads one still unset stops before it changes anything.
3. Run `scripts/split-repos.sh <organisation> <project>` and push the three repositories.
4. Run the setup scripts in `dp-integration/setup/`, in order: Unity Catalog baseline, publisher
   identity, Ontos as a Databricks App.
5. Create the pipelines and the branch policies in Azure DevOps.
6. Prove the install with the [verification scenarios](docs/12-verify-the-install.md).

## Versions

Verified with Databricks CLI 1.18, Ontos v1.0.2, datacontract-cli 1.2.7 and databricks-sdk
0.151.0, on Microsoft-hosted `ubuntu-latest` agents. `dp-integration/pyproject.toml` pins the
Python packages, so every pipeline run uses the same versions.

## Continuous integration

`.github/workflows/ci.yml` runs the unit tests and the contract checks on every push and pull
request to this repository. Once installed, the Azure DevOps pipelines in each folder are the
ones that govern changes.

## Licence

Apache License 2.0: see [LICENSE](LICENSE). Ontos is not included: the Ontos setup step clones it
from its own repository, under its own licence.

This is a reference implementation, provided as is. It is not a supported Databricks product.
