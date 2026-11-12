# Overview and architecture

## The idea in one line

"Azure DevOps decides, the Data Steward signs, Unity Catalog enforces." The Steward signs by approving the pull request, so Ontos is the registration and display copy only. Products that also need a Business Data Owner sign-off (governance tier 1, enterprise) are not covered yet: `dpi check-mandatory` rejects a contract at tier 1.

## Components

| Component | What it is | Owned by |
|---|---|---|
| `data-contracts` repository | ODCS 3.1.0 contracts and their access files under `contracts/<domain>/`, ODPS product files under `products/<domain>/`, the domain registry `domains/domains.yaml`, and two small pipeline files that call the shared templates. | Data Producers and Stewards; the registry with the platform team |
| `dp-integration` repository | The shared tooling: the `dpi` Python package, the pipeline templates (including the bundle deploy), the configuration and the one-off setup scripts. | Platform team |
| Product bundle repository, for example `dp-orders` | A Databricks Asset Bundle that builds the objects one product's contract names: a Lakeflow pipeline and a build job, and optionally metric views. Its deploy pipeline extends the shared bundle-deploy template. | Data Producers |
| Contract CI pipeline | Pull request build validation on `data-contracts/main`. | Platform team |
| Publish pipeline | Runs after every merge that changes a contract, product file or the registry, and after every bundle deploy. | Platform team |
| Bundle deploy pipeline | One per product bundle. Deploys and builds in dev, then in prod behind an Azure DevOps Environment approval. | Platform team (template), Data Producers (bundle) |
| Publisher identity | A user-assigned managed identity. Azure DevOps reaches it through an Azure Resource Manager service connection with workload identity federation. It is a service principal in Databricks. | Platform team |
| Unity Catalog | Enforces access. Holds the governed tags, comments and the pointer back to the contract version and commit. | Platform team |
| Ontos v1.0.2 | Data contract registry, deployed as a Databricks App on Lakebase. Shows contracts, versions and quality results. | Platform team |
| Groups | Producers, Stewards and Consumers per domain (account groups), plus an Azure DevOps group of Stewards per product for the branch policy. | Identity team |

## What the publish pipeline changes

It runs one stage per environment, in order (for example dev, then prod). A contract is published only in the environments it lists as servers.

### In Unity Catalog, for every object in the contract

1. Checks the object's columns match the contract. On any difference it changes nothing on that object and fails the run, so a drifted contract never publishes.
2. Sets the object and column comments from the contract descriptions (only when they changed).
3. Sets four governed tags: tier, domain, status and sensitivity.
4. Sets three table properties: `dp.contract_id`, `dp.contract_version` and `dp.contract_commit`. On a materialized view the product's pipeline sets the first two, and publish only checks them.
5. Makes the direct grants on the object match the access file. Grants not in the file are revoked.
6. Adds USE CATALOG and USE SCHEMA on the parents for the same principals (add only).

Objects can be tables, materialized views (`physicalType: materialized_view`) or metric views (`physicalType: metric_view`). While a contract is still before active, an object that does not exist yet is reported as not built and skipped.

### In Ontos, once

- Domains and their owner teams are created or updated from `domains/domains.yaml`.
- The first version of a contract is imported, owned by its domain's team.
- A later version is created as the next version of the newest older one, so Ontos shows one contract with its history.
- A version Ontos already has only gets its status brought in line with the file.
- Quality results from `datacontract test` in every environment are added as quality items, one per dimension.
- Data products are registered and walked to the status in their file, behind gates on the port contracts. Certification sets the Certified badge in Unity Catalog ([Data products, bundles and pipeline rules](15-data-products-bundles-and-pipeline-rules.md)).

## Blocking and advisory checks

| Check | When | Effect |
|---|---|---|
| ODCS lint (`datacontract lint`) | Pull request | Blocking |
| Mandatory fields (`dpi check-mandatory`) | Pull request | Blocking, with an annotation on the line |
| Products and domains (`dpi check-products`) | Pull request | Blocking: product files, the registry, port contracts and versions |
| Data Steward approval | Pull request | Blocking (branch policy) |
| Quality checks (`datacontract test`) | Pull request and publish | Advisory: a warning, never a failure |
| Pipeline rules (`dpi check-expectations`) | Pull request | Blocking only when a rule does not run; rows it would flag are warnings |
| Pipeline rules in the product's pipeline | Every pipeline update | By severity: warning keeps and counts the row, error drops it, critical fails the update |
| Schema drift | Publish | Blocking: the object is not touched and the run fails |
| Objects built | Publish, contract or product at active | Blocking: a missing object fails the run and holds the product gate |

## Out of scope

These are deliberately not built: Business Data Owner sign-off in Ontos for enterprise (tier 1) products, the approval checker and evidence store, Ontos webhooks back to Azure DevOps, blocking quality checks in CI, and ABAC policies.

## Proven end to end

Nine scenarios prove an install, and the example product goes through its gates to active and certified. The steps are on [Verify the install](12-verify-the-install.md).
