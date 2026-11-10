# data-contracts

The data contracts, data product files and domain registry for Tier 2 (domain) data products.
A change merged to `main` is what gets published. The Data Steward's pull request approval is
the business sign-off. The example data is synthetic.

## Layout

| Path | What it is | Who changes it |
|---|---|---|
| `contracts/<domain>/<name>.odcs.yaml` | A data contract (ODCS 3.1.0): the objects, their columns, quality rules, team and servers | Data Producers; the product's Data Stewards approve |
| `contracts/<domain>/<name>.access.yaml` | The full list of direct grants on the contract's objects | Data Producers; the Stewards approve |
| `products/<domain>/<name>.odps.yaml` | A data product (ODPS v1.0.0): its output ports, each naming a contract id and version | Data Producers; the Stewards approve |
| `domains/domains.yaml` | The domain registry: each domain, its owner team in Ontos and the team's groups | The platform team; a platform owner approves |
| `pipelines/contract-ci.yml` | Pull request validation; extends `templates/contract-ci.yml` in `dp-integration` | The platform team |
| `pipelines/publish.yml` | Publish after merge or after a bundle deploy; extends `templates/publish.yml` | The platform team |

The example is one domain (`content`) with two contracts and one product:

- `content-catalogue`: two tables created by `dp-integration/setup/bootstrap.sh`.
- `content-linear`: two materialized views and a metric view, built by the `dp-content` bundle.
- `content`: the data product, with one output port on `content-linear`.

## How a change flows

1. Change a contract, access file or product file on a branch and open a pull request to `main`.
2. Contract CI runs. Blocking: ODCS lint, the mandatory fields, and the product check (product
   files, the registry, port contracts and versions). Advisory: quality checks against
   Databricks. A pipeline rule that does not run on the built objects also fails the PR.
3. The product's Data Stewards approve. They are a required reviewer on the product's folders.
4. Merge. The publish pipeline applies the comments, governed tags, contract properties and
   grants in Unity Catalog, in every environment the contract lists as a server. Then it updates
   Ontos once: domains, contracts with their quality results, and data products.

## Statuses and gates

A product file's `status` is where the product should be. The publish pipeline walks Ontos
there one allowed step at a time and stops when a gate is not met. Contract CI runs the same
checks without Ontos, so most problems show on the pull request.

| Product status | Gate | Delivery gate |
|---|---|---|
| `draft`, `proposed` | None | G1, G2 |
| `under_review` | Every port contract is in Ontos | |
| `approved` | Every port contract is at least approved | G3: approved before the build |
| `active` | Every port contract is active, and its objects exist in Unity Catalog | G4: in use |
| `deprecated`, `retired` | None | Consumers move off it |

A product's `certificationLevel` (for example Bronze, Silver or Gold) and `publicationScope`
need it `active`. Certification also sets the Certified badge on the port objects.

## Rules worth knowing

- Never grant by hand. The access file is the full list: publish revokes any other direct grant.
- A new contract version is a new `version` in the same file. Ontos keeps one contract with its
  history. A product's port `version` must equal the contract's `version`.
- A changed port on an active product is a breaking change: raise the product `version`.
- A new domain needs a registry entry here, an allowed value of the domain tag in `dp-integration`
  (`tags.domain_values` in `config/platform.yaml` and the enum in the mandatory schema) and its
  catalogs, which the platform team creates.

The full guide is in `docs/` at the root of the source repository.
