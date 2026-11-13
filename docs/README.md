# Install guide

> [!NOTE]
> **Audience:** platform engineers installing the data contract pipeline in their own Azure DevOps, Azure and Databricks environment.
>
> **Status:** verified end to end on Azure Databricks and Azure DevOps with the example product (built from `samples.tpch.orders`): contracts, data products and domains, two environments, the bundle deploy and pipeline rules from the contract.
>
> **Source:** the folders `dp-integration`, `data-contracts` and `dp-orders` in this repository, with a `<placeholder>` for every value that is yours. `scripts/split-repos.sh` turns them into three Azure DevOps repositories.

New here? Start with [How it works](how-it-works.md): the four lanes from pull request to Unity Catalog and Ontos, with diagrams.

## What you are installing

A data product publishes its data contract and its product file through Azure DevOps. The Data Steward's pull request approval is the business signature. The product's own bundle builds the objects the contract names. After each merge or deploy, a pipeline makes Unity Catalog and Ontos match `main`. No secret is stored anywhere: the pipelines sign in with workload identity federation.

**The path of a change**

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

## Install order

Work through the steps in order. Each page explains what the step does, why, the exact commands, and how to check it worked. Later steps need values that earlier steps print.

| Step | Page | Who | Done when |
|---|---|---|---|
| 1 | [Prerequisites and decisions](02-prerequisites-and-decisions.md) | Platform lead | Every box ticked and the decisions recorded |
| 2 | [Configuration](03-configuration.md) | Platform engineer | `config/environments/<env>.yml` filled in, `platform.yaml` reviewed |
| 3 | [Repositories and the dpi command](04-repositories-and-the-dpi-command.md) | ADO project admin | Three repositories pushed; `dpi config get env.databricksHost` works |
| 4 | [Unity Catalog baseline](05-unity-catalog-baseline.md) | Databricks account admin | Tag policies and groups exist |
| 5 | [Publisher identity](06-publisher-identity.md) | Azure and Databricks admins | Service connection exists; client id in each environment file |
| 6 | [Ontos as a Databricks App](07-ontos-as-a-databricks-app.md) | Workspace admin | Ontos is running; URL in the environment file; roles mapped |
| 7 | [Pipelines in Azure DevOps](08-pipelines-in-azure-devops.md) | ADO project admin | Five pipelines exist and are authorised; smoke run and first bundle deploy pass |
| 8 | [Branch policies](10-branch-policies.md) | ADO project admin | A direct push to `main` is rejected |
| 9 | [Verify the install](12-verify-the-install.md) | Platform engineer with a Data Steward | Every scenario passes |

Reference pages: [How it works](how-it-works.md), [Contract CI pipeline explained](09-contract-ci-pipeline-explained.md), [Publish pipeline explained](11-publish-pipeline-explained.md), [Data products, bundles and pipeline rules](15-data-products-bundles-and-pipeline-rules.md), [Start a new data product](16-start-a-new-data-product.md), [The dpi package](13-the-dpi-package.md) and [Operations and troubleshooting](14-operations-and-troubleshooting.md).

## Conventions in this guide

- `<placeholder>` values are yours to fill in. Everything else can be copied as written.
- Commands run from the root of the `dp-integration` checkout unless a step says otherwise.
- `<env>` is the environment name, for example `dev`. It picks `config/environments/<env>.yml`.
- Group names such as `dp-sales-stewards` are the defaults in `platform.yaml`. Change them there and every script and pipeline follows.

## All pages

1. [Overview and architecture](01-overview-and-architecture.md)
2. [Prerequisites and decisions](02-prerequisites-and-decisions.md)
3. [Configuration](03-configuration.md)
4. [Repositories and the dpi command](04-repositories-and-the-dpi-command.md)
5. [Unity Catalog baseline](05-unity-catalog-baseline.md)
6. [Publisher identity](06-publisher-identity.md)
7. [Ontos as a Databricks App](07-ontos-as-a-databricks-app.md)
8. [Pipelines in Azure DevOps](08-pipelines-in-azure-devops.md)
9. [Contract CI pipeline explained](09-contract-ci-pipeline-explained.md)
10. [Branch policies](10-branch-policies.md)
11. [Publish pipeline explained](11-publish-pipeline-explained.md)
12. [Verify the install](12-verify-the-install.md)
13. [The dpi package](13-the-dpi-package.md)
14. [Operations and troubleshooting](14-operations-and-troubleshooting.md)
15. [Data products, bundles and pipeline rules](15-data-products-bundles-and-pipeline-rules.md)
16. [Start a new data product](16-start-a-new-data-product.md)
