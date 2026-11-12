# Lakehouse Governance as Code

**The pipeline decides, the Data Steward signs, Unity Catalog enforces.**

[![Licence: Apache 2.0](https://img.shields.io/badge/licence-Apache%202.0-blue)](LICENSE)
[![ODCS 3.1.0](https://img.shields.io/badge/ODCS-3.1.0-informational)](https://bitol-io.github.io/open-data-contract-standard/)
[![ODPS v1.0.0](https://img.shields.io/badge/ODPS-v1.0.0-informational)](https://bitol-io.github.io/open-data-product-standard/)

Data contracts (ODCS) and data products (ODPS) live in git. A pull request is the change request,
contract CI enforces the standard, and the Data Steward's approval is the sign-off. After the
merge, pipelines build the product on Databricks with the contract's quality rules as Lakeflow
expectations, then make Unity Catalog and [Ontos](https://github.com/databrickslabs/ontos) match
what was approved. No secret is stored anywhere: the pipelines sign in with workload identity
federation.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/how-it-works-dark.svg">
  <img alt="The four lanes: create a contract, review and approve it in a pull request, build the product with the rules embedded, publish to Unity Catalog and Ontos. Below, the lifecycle from draft G1 to active G4." src="docs/assets/how-it-works.svg" width="100%">
</picture>

**[Read how it works, step by step](docs/how-it-works.md)**

## Why governance as code

Most data governance plans describe stewardship, standards and quality as policy: a document says
every dataset has an owner, changes are approved and access matches the agreement. Whether that
happens depends on people remembering to do it. Here each of those statements is a check or a
pipeline step. A change that breaks the standard cannot merge, an approval is recorded on the pull
request, and Unity Catalog and Ontos are set from `main`, so what runs is always what was signed.
Git keeps the audit trail for free.

## Governance on paper vs governance as code

| The policy says | What enforces it here |
|---|---|
| Every dataset has an accountable Data Steward and Business Data Owner | `dpi check-mandatory` fails the pull request until both are named in the contract |
| Changes are approved by the business | The product's Stewards group is a required reviewer on its folders; a direct push to `main` is refused |
| Data meets its quality rules | The contract's rules run as Lakeflow expectations in the product's pipeline; severity decides keep, drop or fail |
| The catalogue describes what is really there | Publish fails on schema drift between an object and its contract |
| Access matches the agreement | Publish makes the grants match the access file and revokes anything not listed |
| Every dataset is classified | Governed tags for tier, domain, status and sensitivity come from the contract, never by hand |
| Products are certified before people rely on them | The product reaches `active` (G4) only when its contracts are active and its objects exist; certification sets the Certified badge |

## How it works

1. **[Create](docs/how-it-works.md#1-create).** A Data Producer writes the ODCS contract, its
   access file and the ODPS product file on a branch of `data-contracts`, or starts from the
   template with `dpi new-product`, and opens a pull request.
2. **[Review and approve](docs/how-it-works.md#2-review-and-approve).** Contract CI runs
   `datacontract lint`, `dpi check-mandatory`, `dpi check-products` and `dpi check-expectations`
   (blocking) and `datacontract test` (advisory). The product's Data Stewards approve, and the
   change merges.
3. **[Build with the rules embedded](docs/how-it-works.md#3-build-with-the-rules-embedded).** The
   product's Databricks Asset Bundle deploys to dev, then to prod after an approval. The merged
   contract travels with it, and `@rules.apply(...)` turns each row-level rule into an expectation.
4. **[Publish and sync](docs/how-it-works.md#4-publish-and-sync).** As the publisher identity, the
   publish pipeline sets comments, governed tags, contract properties and grants in Unity Catalog,
   then syncs domains, contract versions, quality results and data products to Ontos, through the
   gates G1 to G4.

## Quality rules become pipeline expectations

The rules live in the contract, so a rule changes only through a pull request the Stewards
approve. The pipeline code holds none.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/contract-to-expectation-dark.svg">
  <img alt="Contract rules and the Lakeflow expectations they become: required true becomes IS NOT NULL, invalidValues becomes IN the valid values, an expectation custom property is applied as written. Severity warning keeps the row, error drops it, critical fails the update." src="docs/assets/contract-to-expectation.svg" width="100%">
</picture>

In the contract:

```yaml
quality:
  - type: sql
    name: as_run_ends_after_start
    query: SELECT count(*) FROM ${object} WHERE actual_end <= actual_start
    mustBe: 0
    severity: warning
    customProperties:
      - property: expectation
        value: actual_end > actual_start
```

In the pipeline, a dataset only needs the decorator:

```python
rules = ContractExpectations.from_file(spark.conf.get("contract_path"))

@dp.materialized_view(name="fct_linear_as_run_event", table_properties=rules.table_properties())
@rules.apply("fct_linear_as_run_event")
def fct_linear_as_run_event():
    ...
```

More: [Data products, bundles and pipeline rules](docs/15-data-products-bundles-and-pipeline-rules.md).

## What you see in Ontos

Approval happens in the pull request; Ontos shows the result. Every record in Ontos is written by
the publisher identity from `main`, so a status there can only move after a Steward approved the
change. That makes Ontos the place to see where each contract and product stands, and how it got
there.

<img align="right" width="300" alt="The Ontos activity timeline for the Content data product, newest first: created at draft, then status changes draft to proposed, proposed to under_review, under_review to approved and approved to active, each made by the publisher identity." src="docs/assets/ontos-product-timeline.png">

**The activity timeline is the audit trail.** Open a data product and choose Comments, then
Changes. Each publish run that changes the product adds entries: the status before and after,
who made the change and when. Read from the bottom up, it is the product's path through the
gates:

- **Create** at `draft` (G1). A new product always starts here.
- **draft to proposed** (G2).
- **proposed to under_review to approved** (G3). `dpi sync-products` walks to the status in the
  product file one allowed step at a time, so Ontos's `under_review` step shows too.
- **approved to active** (G4), once the port contracts are active and the bundle has built the
  objects.

Every entry names the publisher identity, never a person: nobody edits Ontos by hand. To see who
approved a step, open the pull request that merged it.

<br clear="right">

**The product page shows where it stands now:** its status, quality score and certification
level, with each output port linked to its contract and version.

<img alt="The Content data product in Ontos: status active, quality 100%, certification Silver, team content-domain, and one deliverable, Linear v1.0.0, linked to the Content linear contract." src="docs/assets/ontos-product.png" width="100%">

The contracts list, and the quality results the sync attaches to each contract version, are in
[How it works: what it looks like in Ontos](docs/how-it-works.md#what-it-looks-like-in-ontos).

## Try it in five minutes

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

Then try a failing change: remove the Data Steward from `team` in
`contracts/content/content-catalogue.odcs.yaml` and run `dpi check-mandatory` again. That is the
check that stops the pull request.

## Adopt it in your organisation

**Start a data product from the template.** From a `data-contracts` checkout:

```bash
dpi new-product --domain content --product sample-orders
```

It writes the contract, the access file and the product file at `draft` (G1), and a bundle for
the product, all passing the blocking checks as they stand. Replace the `<placeholder>` values and
open the first pull request. See [Start a new data product](docs/16-start-a-new-data-product.md).

**Install the platform.** You need:

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

**What you can change without touching code:** tag names, group names and grant behaviour in
`dp-integration/config/platform.yaml`; hosts, ids and service connections per environment in
`config/environments/`; the business domains in `data-contracts/domains/domains.yaml`.

## Scope and roadmap

This covers Tier 2: domain data products, where the product's Stewards approve changes in the pull
request and Ontos is the registration and display copy. Azure DevOps is the reference CI; the
pattern itself does not depend on it, since every check is a `dpi` command.

Next:

- **GitHub Actions** equivalents of the four pipelines.
- **Tier 1** enterprise products, with a Business Data Owner sign-off in Ontos.
- **A neutral example domain** alongside the current content example.

## Repository layout

Each of the first three folders becomes its own Azure DevOps repository.

| Folder | What it is | Owned by |
|---|---|---|
| [`dp-integration/`](dp-integration/README.md) | The shared tooling: the `dpi` Python package, the product template, the Azure DevOps pipeline templates, the configuration and the one-off setup scripts | Platform team |
| [`data-contracts/`](data-contracts/README.md) | The contracts and access files, the data product files, the domain registry and two small pipeline files | Data Producers and Stewards |
| [`dp-content/`](dp-content/README.md) | The worked example product bundle: a Lakeflow pipeline, a build job and a metric view for the `content` product | Data Producers |
| [`docs/`](docs/README.md) | How it works, the install guide step by step, and the reference pages | |
| `scripts/split-repos.sh` | Turns the three folders into three Git repositories for Azure DevOps | |
| `scripts/check-text.py` | Text checks CI runs on every file: no em-dash or en-dash, no Databricks token | |
| `scripts/build-diagrams.py` | Builds the infographics in `docs/assets/` | |

The example domain, contracts and product use synthetic data: a content catalogue and a
two-week linear schedule on placeholder channels.

## Versions

Verified with Databricks CLI 1.18, Ontos v1.0.2, datacontract-cli 1.2.7 and databricks-sdk
0.151.0, on Microsoft-hosted `ubuntu-latest` agents. `dp-integration/pyproject.toml` pins the
Python packages, so every pipeline run uses the same versions.

`.github/workflows/ci.yml` runs the unit tests and the contract checks on every push and pull
request to this repository. Once installed, the Azure DevOps pipelines in each folder are the
ones that govern changes.

## Contributing and security

See [CONTRIBUTING.md](CONTRIBUTING.md) for the checks to run before a pull request, and
[SECURITY.md](SECURITY.md) to report a vulnerability privately.

## Licence

Apache License 2.0: see [LICENSE](LICENSE). Ontos is not included: the Ontos setup step clones it
from its own repository, under its own licence.

This is a reference implementation, provided as is. It is not a supported Databricks product.
