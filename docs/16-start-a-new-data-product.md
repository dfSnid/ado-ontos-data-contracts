# Start a new data product

## What you get

`dpi new-product` writes everything a new data product needs, from templates that ship in
the `dpi` package:

| File | Where | What it is |
|---|---|---|
| `contracts/<domain>/<contract>.odcs.yaml` | `data-contracts` | The contract at `draft`, version 0.1.0: one object, three columns, one rule of each kind |
| `contracts/<domain>/<contract>.access.yaml` | `data-contracts` | Grants for the domain's consumers and stewards groups |
| `products/<domain>/<product>.odps.yaml` | `data-contracts` | The product file at `draft`, with one output port on the contract |
| `databricks.yml`, `resources/<product>.yml` | New bundle folder | A bundle with `local`, `dev` and `prod` targets, a Lakeflow pipeline and a build job |
| `src/pipeline/<product>.py`, `src/pipeline/contracts.txt` | New bundle folder | The pipeline, with the contract's rules applied by `@rules.apply(...)` |
| `pipelines/deploy.yml` | New bundle folder | The deploy pipeline, extending the shared bundle-deploy template |

Once the domain is in the registry, every generated file passes the blocking CI checks as it
stands (ODCS lint, mandatory fields, products and domains), so the first pull request is green.
`dp-orders` is what this command wrote for `--domain sales --product orders`, with the query,
columns and rules changed to read `samples.tpch.orders`.

## Generate it

From the root of a `data-contracts` checkout, with `dp-integration` installed:

```bash
export DP_CONFIG_DIR=../dp-integration/config
dpi new-product --domain sales --product returns
```

| Option | Default | Meaning |
|---|---|---|
| `--domain` | (required) | An allowed value of the domain tag (`tags.domain_values` in `platform.yaml`) |
| `--product` | (required) | Product id: lower case words joined by `-` |
| `--contract` | the product id | Contract id of the output port |
| `--dataset` | the product id, with `_` | The first object the pipeline builds |
| `--catalog-dev`, `--catalog-prod` | `<domain>_dev`, `<domain>_prod` | Catalogs of the contract's servers and the bundle's targets |
| `--contracts-root` | `.` | The `data-contracts` checkout |
| `--bundle-dir` | `../dp-<product>` | Folder for the new bundle |

It never overwrites a file: if any target exists, it stops before writing anything. It ends by
printing the next steps.

> [!NOTE]
> A domain that is not an allowed tag value stops the command. Adding a domain is a platform
> change: a value in `tags.domain_values` and in the mandatory schema's domain enum, its catalogs,
> and an entry in `domains/domains.yaml`. When only the registry entry is missing, the command
> prints it for you to add.

## Fill it in

1. **The contract.** Replace each `<placeholder>`: the purpose, the Business Data Owner and the
   Data Steward, the description of each object. Describe the real columns: publish fails if the
   built object's columns differ from the contract.
2. **The rules.** Keep, change or remove the example rules. `required: true` becomes
   `IS NOT NULL`; an `invalidValues` rule becomes `IN (...)`; a `type: sql` count with an
   `expectation` custom property is applied row by row. `severity` sets the action: warning keeps
   and counts the row, error drops it, critical fails the update.
3. **The access file.** List every principal that should read each object. Publish revokes
   anything else.
4. **The bundle.** Set the workspace host and `source_table` in `databricks.yml`, and write the
   query in `src/pipeline/<product>.py`. Add a function per extra object, each with
   `@rules.apply("<object>")`.

## Take it to active

This is the order of a change from [Data products, bundles and pipeline rules](15-data-products-bundles-and-pipeline-rules.md), from a new product:

| Step | Pull request | Status | What happens |
|---|---|---|---|
| 1 | `data-contracts`: the three new files | `draft` (G1) or `proposed` (G2) | Contract CI checks them; the domain's Stewards approve; publish registers the product in Ontos |
| 2 | `data-contracts`: contract and product to `approved` | `approved` (G3) | Publish reports the objects as not built yet: expected |
| 3 | The bundle repository | | Deploy builds in dev, then prod after the approval; publish then applies the metadata |
| 4 | `data-contracts`: contract and product to `active`, with any `certificationLevel` | `active` (G4) | Publish checks the objects exist, walks Ontos to active and sets the Certified badge |

## Pipeline setup for the bundle (once)

1. Push the bundle folder as its own repository, `dp-<product>`, and replace `<ado-project>` in
   `pipelines/deploy.yml`.
2. Create the Azure DevOps Environments `<product>-dev` and `<product>-prod`; add an Approvals
   check on `<product>-prod`.
3. Create the pipeline from `pipelines/deploy.yml`, named `dp-<product>-deploy`, and run it once.
4. Add it to `data-contracts/pipelines/publish.yml` as a pipeline resource, like
   `orders_deploy`, so publish runs after each deploy.
