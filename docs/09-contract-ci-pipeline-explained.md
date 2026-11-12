# Contract CI pipeline explained

## What it does

Contract CI runs on every pull request to `data-contracts/main`. It has two stages.

| Stage | Step | Effect |
|---|---|---|
| Contract validation | `datacontract lint` on each `contracts/**/*.odcs.yaml`, with JUnit results | Blocking |
| Contract validation | `dpi check-mandatory` on the same files | Blocking; an annotation on the failing line |
| Contract validation | `dpi check-products` on each `products/**/*.odps.yaml`, with the registry and the contracts | Blocking; skipped when there is no `domains/domains.yaml` |
| Quality checks | `datacontract test` against the `contractServer` in the contract, as the publisher identity | Advisory: warnings only |
| Quality checks | `dpi check-expectations`: the contract's pipeline rules, tried on the built objects | Blocking when a rule does not run; flagged rows are warnings |

`qualityChecks: false` in `data-contracts/pipelines/contract-ci.yml` drops the second stage. Use it if pull request builds must not reach Databricks.

## The pipeline in data-contracts

The file in the contracts repository only says which template, which environment and which version of the tooling. All the logic is in `dp-integration`, so the Data Producers cannot change it in their own pull request.

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

## The template

`templates/contract-ci.yml`

```yaml
# Contract CI for data products. Used as PR build validation on data-contracts.
# Blocking: ODCS lint, the mandatory-field check, and the product check (data product files,
# the domain registry, port contracts and versions, status against the contracts).
# Advisory: quality checks against Databricks. They warn, never fail ("Always Proceed"). One
# exception: a pipeline rule (a Lakeflow expectation from the contract, expectations.py) that does
# not run on the built object fails the PR, rather than the next pipeline update. The rows a rule
# would flag are only warnings.
# Every host, id and service connection comes from config/environments/<environment>.yml.
parameters:
  - name: environment
    type: string
    default: dev
  - name: qualityChecks       # false: lint and mandatory fields only, no Databricks access needed
    type: boolean
    default: true

stages:
  - stage: validate
    displayName: Contract validation (blocking)
    variables:
      - template: variables.yml
        parameters:
          environment: ${{ parameters.environment }}
    jobs:
      - job: lint
        displayName: Lint and mandatory fields
        pool:
          ${{ if ne(variables.agentPool, '') }}:
            name: ${{ variables.agentPool }}
          ${{ else }}:
            vmImage: ${{ variables.vmImage }}
        steps:
          - template: steps/install.yml
          - script: |
              set -euo pipefail
              shopt -s globstar nullglob
              mkdir -p "$(Common.TestResultsDirectory)/lint"
              rc=0
              for f in contracts/**/*.odcs.yaml; do
                datacontract lint "$f" --output "$(Common.TestResultsDirectory)/lint/$(basename "$f").xml" --output-format junit || rc=1
              done
              exit $rc
            displayName: ODCS lint
            workingDirectory: $(Build.SourcesDirectory)/contracts
          - script: |
              set -euo pipefail
              shopt -s globstar nullglob
              dpi check-mandatory contracts/**/*.odcs.yaml
            displayName: Mandatory fields
            workingDirectory: $(Build.SourcesDirectory)/contracts
          - script: |
              set -euo pipefail
              shopt -s globstar nullglob
              if [ ! -f domains/domains.yaml ]; then echo "No domain registry: product check skipped"; exit 0; fi
              dpi check-products products/**/*.odps.yaml
            displayName: Products and domains
            workingDirectory: $(Build.SourcesDirectory)/contracts
          - task: PublishTestResults@2
            condition: always()
            inputs:
              testResultsFormat: JUnit
              testResultsFiles: '$(Common.TestResultsDirectory)/lint/*.xml'
              testRunTitle: Contract lint

  - ${{ if parameters.qualityChecks }}:
    - stage: quality
      displayName: Quality checks (advisory)
      dependsOn: validate
      variables:
        - template: variables.yml
          parameters:
            environment: ${{ parameters.environment }}
      jobs:
        - job: test
          displayName: datacontract test
          pool:
            ${{ if ne(variables.agentPool, '') }}:
              name: ${{ variables.agentPool }}
            ${{ else }}:
              vmImage: ${{ variables.vmImage }}
          steps:
            - template: steps/install.yml
            - task: AzureCLI@2
              displayName: Run quality checks (advisory)
              inputs:
                azureSubscription: ${{ variables.serviceConnection }}
                scriptType: bash
                scriptLocation: inlineScript
                workingDirectory: $(Build.SourcesDirectory)/contracts
                inlineScript: |
                  shopt -s globstar nullglob
                  # Entra token of the publisher identity for Azure Databricks; no stored secret.
                  export DATACONTRACT_DATABRICKS_TOKEN=$(az account get-access-token --resource $(azureDatabricksResource) --query accessToken -o tsv)
                  export DATACONTRACT_DATABRICKS_SERVER_HOSTNAME='$(databricksHost)'
                  export DATACONTRACT_DATABRICKS_HTTP_PATH='/sql/1.0/warehouses/$(warehouseId)'
                  mkdir -p "$(Common.TestResultsDirectory)/quality"
                  for f in contracts/**/*.odcs.yaml; do
                    grep -q "^  - server: $(contractServer)\$" "$f" || continue   # not deployed here
                    if ! datacontract test --server '$(contractServer)' "$f" --output "$(Common.TestResultsDirectory)/quality/$(basename "$f").xml" --output-format junit > out.txt 2>&1; then
                      echo "##vso[task.logissue type=warning;sourcepath=$f]Quality checks failed for $f (advisory)"
                    fi
                    cat out.txt
                    grep -q 'has warnings' out.txt && echo "##vso[task.logissue type=warning;sourcepath=$f]Quality warnings for $f (advisory)"
                  done
                  exit 0
            - task: AzureCLI@2
              displayName: Pipeline rules (blocking when a rule does not run)
              condition: succeededOrFailed()
              inputs:
                azureSubscription: ${{ variables.serviceConnection }}
                scriptType: bash
                scriptLocation: inlineScript
                workingDirectory: $(Build.SourcesDirectory)/contracts
                inlineScript: |
                  set -euo pipefail
                  shopt -s globstar nullglob
                  export DATABRICKS_TOKEN=$(az account get-access-token --resource $(azureDatabricksResource) --query accessToken -o tsv)
                  dpi check-expectations contracts/**/*.odcs.yaml
            - task: PublishTestResults@2
              condition: always()
              continueOnError: true
              inputs:
                testResultsFormat: JUnit
                testResultsFiles: '$(Common.TestResultsDirectory)/quality/*.xml'
                testRunTitle: Contract quality (advisory)
                failTaskOnFailedTests: false
```

### Notes on the template

- The `pool` sits inside each job. Azure DevOps does not allow a conditional pool at the pipeline root.
- `steps/install.yml` checks out both repositories at fixed paths and installs `dpi` from the `dp-integration` checkout.
- The quality step gets an Entra token for Azure Databricks from the service connection (`azureDatabricksResource` is the fixed application id) and hands it to `datacontract` as `DATACONTRACT_DATABRICKS_TOKEN`.
- `exit 0` and `failTaskOnFailedTests: false` keep quality advisory. A failure shows as a warning on the run and on the pull request.
- The pipeline rules step runs even when the quality step warns (`succeededOrFailed()`). It is the one Databricks step that can fail the pull request.

`templates/steps/install.yml`

```yaml
# Check out the contracts repository and this one side by side, then install the dpi command.
#   $(Build.SourcesDirectory)/contracts    the repository whose pipeline is running (self)
#   $(Build.SourcesDirectory)/integration  this repository (resource `integration`)
# Fixed paths, so the pipelines work whatever the two repositories are called.
steps:
  - checkout: self
    path: s/contracts
  - checkout: integration
    path: s/integration
  - task: UsePythonVersion@0
    inputs:
      versionSpec: $(pythonVersion)
  - script: python -m pip install --quiet --disable-pip-version-check "$(Build.SourcesDirectory)/integration"
    displayName: Install dpi
```

## The mandatory-field check

ODCS allows a contract without a Steward or a classification. The overlay schema `schemas/mandatory.schema.json` adds what this platform needs:

- `id`, `name`, `domain` (one of the domain values) and `status`.
- A team with a named Business Data Owner and a named Data Steward.
- At least one schema object with properties.
- Custom properties `governanceTier`, `securityClassification` (one of four values) and `containsPii` (true or false).

The check also fails a contract whose `governanceTier` is not in `tags.tier_values` in `platform.yaml`. Only `2: domain` is listed, so an enterprise contract at tier 1 cannot merge until your process has a Business Data Owner sign-off and you add `1: enterprise`.

`dpi check-mandatory` validates each file against it and reports each finding against the line in the file, for example:

```text
ERROR   contracts/sales/orders.odcs.yaml:23: missing a named Data Steward
ERROR   contracts/sales/orders.odcs.yaml:125: governanceTier 1 is not set up on this platform, which publishes 2 (domain) (tags.tier_values in platform.yaml)
```

In Azure DevOps the same finding is an error annotation on that line of the pull request.

### Change the mandatory fields

1. Edit `schemas/mandatory.schema.json` in `dp-integration` (standard JSON Schema, draft 2020-12).
2. If you change the domain or sensitivity lists, change `tags.domain_values` or `tags.sensitivity_values` in `platform.yaml` to match.
3. Run `python -m pytest` and open a pull request. Once merged to `main`, every contract pull request uses the new rules.

Full listing: [`dp-integration/schemas/mandatory.schema.json`](../dp-integration/schemas/mandatory.schema.json)

## The products and domains check

`dpi check-products` reads every product file with the domain registry and the contracts in the same pull request. It makes no Ontos call. It fails when:

- a product or a contract names a domain that is not in `domains/domains.yaml`, or a registered domain is not an allowed value of the domain tag (`tags.domain_values`);
- a product has no output ports, or a port names a contract id that has no file, a version that differs from the contract file's `version`, or a contract in another domain;
- the product's status is ahead of its port contracts, for example an active product with a contract still at approved;
- certification or publication scope is set on a product that is not active.

## The pipeline rules check

A contract's row-level rules run in the product's own Lakeflow pipeline ([Data products, bundles and pipeline rules](15-data-products-bundles-and-pipeline-rules.md)). A rule that does not compile there would only fail the next pipeline update, after the merge. `dpi check-expectations` catches it on the pull request instead. It sends one warehouse query per object that counts the rows each rule would flag:

| Result | Effect |
|---|---|
| A rule flags rows | A warning with the count and what the pipeline would do, against the contract file |
| A rule is NULL for some rows | A warning: write the rule so it is true or false |
| A rule does not run (for example a misspelt column) | An error with the Databricks message, and the step fails |
| The object is not built yet | Skipped, as in publish |
| A rule has an unknown severity or a repeated name | An error |

**Example output**, after a change to `validValues: [F, O]` and a misspelt column in `total_price_positive`:

```text
WARNING contracts/sales/orders.odcs.yaml: <catalog>.orders.orders.order_status_valid_values (warn) would warn on <n> of <rows> rows
ERROR   contracts/sales/orders.odcs.yaml: rule 'total_price_positive' does not run on `<catalog>`.`orders`.`orders`: [UNRESOLVED_COLUMN] ...
```
