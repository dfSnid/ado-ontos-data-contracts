# Operations and troubleshooting

## Add a data product

1. Create `contracts/<domain>/<product>.odcs.yaml` and `<product>.access.yaml` in `data-contracts`, on a branch, at `draft`.
2. Create `products/<domain>/<product>.odps.yaml` with one output port per contract, each at the contract's version.
3. Create an Azure DevOps Stewards group for the product and add the required-reviewer policy for its folders (step 8).
4. If the objects are in a catalog the publisher does not manage yet, add it to `publisherCatalogs` and re-run `setup/publisher-identity.sh`.
5. Open the pull request. Contract CI, the Steward's approval and the merge publish it.
6. Create the product's bundle repository from `dp-content`: change `databricks.yml`, the resources, the code and `pipelines/deploy.yml`. If its pipeline applies contract rules, list the contracts in `contracts.txt` and in the deploy's `contracts` trigger paths.
7. Create its ADO Environments and deploy pipeline, permit the three repositories, and run it once (step 7, part 6).
8. Only then add it to the `pipelines` resources of `data-contracts/pipelines/publish.yml`.
9. Move the contract and product to `approved`, then `active`, through pull requests, as in the order of a change on [Data products, bundles and pipeline rules](15-data-products-bundles-and-pipeline-rules.md).

## Add a domain

1. Add it to `tags.domain_values` in `platform.yaml` and to the `domain` enum in the mandatory schema.
2. Add its groups to `ontos.roles.groups` (and to `setup.groups` if the script creates them).
3. Re-run `bootstrap.sh` (updates the tag policy) and `dpi ontos-roles`.
4. Merge to `dp-integration/main` after the unit tests pass.
5. Add the domain, its team and groups to `domains/domains.yaml` in `data-contracts`, with `dp-publishers` among the groups. A platform owner approves it; the next publish creates it in Ontos.

## Add an environment

1. Copy `config/environments/<env>.yml` to the new name and fill it in.
2. Run steps 4 to 7 for it (one publisher identity and service connection per environment).
3. Add it to `environments` in `data-contracts/pipelines/publish.yml`, in the order it publishes. Keep `ontosEnvironment` on one of them.
4. Add it to `environments` in each bundle's `pipelines/deploy.yml`, with a target in `databricks.yml` and an ADO Environment (with an Approvals check if it is production).
5. Add a server with that name to each contract that publishes there.

## Versions and upgrades

| Pinned | Where | Upgrade by |
|---|---|---|
| Ontos v1.0.2 | The clone tag in step 6 | Test a new release on a non-production app first: the version workaround below may no longer be needed |
| `datacontract-cli`, `databricks-sdk` | `pyproject.toml` | Change the range, run the tests, merge |
| Python | `pythonVersion` in the environment file | 3.11 or later |
| Agent image | `vmImage` in the environment file | Microsoft moves `ubuntu-latest` to a new Ubuntu from 19 October 2026. Pin `ubuntu-24.04` if you want to move on your own schedule |

## Known Ontos v1.0.2 behaviour

- Uploading a file never updates an existing contract, so `dpi` finds records by `sourceId` and version.
- Only `POST /api/data-contracts/{id}/versions` sets the version lineage. It copies the header only, so `dpi` fills the draft with a PUT and deletes it if that fails.
- PUT ignores `parentContractId`, `versionFamilyId` and servers, so later versions show no servers in Ontos. Unity Catalog stays the source of truth for where the tables live.
- Clone (and the UI's new-version action) returns 500 on a contract with team members.
- Ontos records no author on contracts or quality items. Who approved and published is in Azure DevOps and the Unity Catalog audit log.
- A non-admin who is not in an item's owner team sees only its active and deprecated versions. Keep `dp-publishers` in every domain team, or the publisher cannot see the drafts it made.
- An output port's version must equal its contract's version.
- A changed port on an active product is a breaking change (409). Raise the product version in the file.
- A new version of a contract or product leaves the old one as it was, often active. Deprecate it through a pull request once consumers have moved.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `dpi` exits 2 with a setting name | A required setting is missing or invalid | Fix the environment file or `platform.yaml`; `dpi config get <key>` to check |
| A setup script exits 2: "empty or still a &lt;placeholder&gt;" | It reads a value you have not filled in | Set it in `<env>.yml`. `publisherClientId` comes from step 5, `ontosUrl` from step 6 |
| "databricksHost is a host name" | The value has `https://` or a path | Keep the host name only, for example `adb-0000000000000000.0.azuredatabricks.net` |
| A contract fails with "not valid YAML" | A YAML syntax error in that file | Fix the line in the annotation; the other contracts still ran |
| "A template expression is not allowed in this context" | A conditional `pool` at the pipeline root | Put the pool inside a job, as the templates do |
| Run waits with "needs permission to access a resource" | First use of the service connection or the `dp-integration` repository | Permit it once, or set `PIPELINE_IDS` and re-run step 5 |
| Ontos returns 401 to the pipeline | An Entra token was sent to the Apps proxy | Use `dpi`, which exchanges it; for a script, exchange it at `/oidc/v1/token` |
| Ontos returns 403 | The publisher lacks CAN USE on the app or is not in `dp-publishers` | Re-run `setup/ontos.sh` |
| Ontos role change refused | `OAUTH_PROFILE` is not an Ontos admin | Use a workspace admin's OAuth login |
| App build fails on npm | `package.json` was deployed with the app | Deploy through `setup/ontos.sh`, which leaves it out |
| Ontos UI is blank | The frontend was not built | Run `build_static.sh` and `FORCE_DEPLOY=1 ./ontos.sh` |
| Unity Catalog refuses a tag | The publisher has no ASSIGN on the tag policy, or the value is not allowed | Re-run step 5; check the value against the policy |
| `SKIP <key>: exists and is owned by someone else` | A tag policy with that key already exists | Reuse it or set `tags.prefix` |
| Consumer refused with "does not have USE CATALOG" | Table grant without parent USE | Keep `parent_use: true`, or grant USE on the catalog and schema |
| Catalog creation fails through the API | The metastore uses default storage | Create it with SQL, as `bootstrap.sql` does |
| Service principal cannot query | No `databricks-sql-access` entitlement or no CAN USE on the warehouse | Re-run step 5 |
| Publish fails with "column ... is in ... but not in the contract" | Drift between the table and its contract | Update the contract (pull request) or the table, then re-run |
| "Unable to resolve latest version" for a pipeline resource | The bundle deploy pipeline in `publish.yml` has never succeeded | Run the deploy pipeline once by hand, then re-run publish |
| The bundle deploy waits on a repository | It has no permission on `data-contracts` or `dp-integration` yet | Permit it on the run, or in each repository's Security settings (step 7, part 3) |
| "... does not exist, and contract status 'active' needs it" | The contract went active before the bundle built the object | Run the bundle deploy, or set the contract back to `approved` until it has run |
| "... cannot move to active: ... not built yet" | The product's active gate found a port object missing | As above |
| Products sync cannot find a contract it just made | The publisher is not in the domain team, so it sees active versions only | Add `dp-publishers` to the team in `domains.yaml` |
| "Ontos calls this a breaking change ... raise the product version" | A port changed on an active product | Raise `version` in the product file |
| "no Ontos team for domain" | The domain is not in Ontos yet | Add it to `domains.yaml`; publish runs sync-domains first |
| Bundle deploy fails because a table already exists | A pipeline cannot take over an existing table | Drop the table, then deploy. The next publish puts the metadata back |
| "... has dp.contract_version ..., not ...: redeploy the bundle" | The pipeline's table properties are older than the contract | Run the bundle deploy; the contracts trigger normally does this |
| check-expectations: "rule ... does not run on ...: UNRESOLVED_COLUMN" | The rule names a column the object does not have | Fix the rule in the contract pull request |
