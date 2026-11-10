# Pipelines in Azure DevOps

## The five pipelines

| Pipeline | Repository | YAML file | Runs |
|---|---|---|---|
| `contract-ci` | `data-contracts` | `pipelines/contract-ci.yml` | As build validation on every pull request to `main` |
| `publish` | `data-contracts` | `pipelines/publish.yml` | On every merge to `main` that changes `contracts/`, `products/` or `domains/`, and when a bundle deploy completes |
| `ontos-smoke` | `dp-integration` | `setup/ontos-smoke.yml` | By hand, to check the publisher can reach Ontos |
| `dp-integration-ci` | `dp-integration` | `pipelines/ci.yml` | On every push to `dp-integration/main`; also as build validation there |
| `dp-content-deploy` | `dp-content` | `pipelines/deploy.yml` | On every merge to `dp-content/main`, and when a contract it applies changes on `data-contracts/main` |

Keep the name `dp-content-deploy`: the publish pipeline refers to it by that name.

## 1. Create the Environments

In Pipelines, Environments, create `content-dev` and `content-prod`. On `content-prod` add an Approvals check with the people who approve a prod deploy. The deploy pipeline waits there before its prod stage.

## 2. Create the pipelines

In the UI: Pipelines, New pipeline, Azure Repos Git, pick the repository, Existing Azure Pipelines YAML file, pick the file, then Save (not Run). Rename each one to the name above.

Or with the CLI:

```bash
ORG=https://dev.azure.com/<organisation>; PROJECT=<project>
new() { az pipelines create --organization $ORG --project "$PROJECT" --name "$1" \
  --repository "$2" --repository-type tfsgit --branch main --yml-path "$3" --skip-first-run; }
new contract-ci       data-contracts pipelines/contract-ci.yml
new publish           data-contracts pipelines/publish.yml
new ontos-smoke       dp-integration setup/ontos-smoke.yml
new dp-integration-ci dp-integration pipelines/ci.yml
new dp-content-deploy dp-content     pipelines/deploy.yml
```

## 3. Authorise them

Each pipeline needs permission to use the service connection and to read the other repositories it checks out:

| Pipeline | Reads |
|---|---|
| `contract-ci`, `publish` | `dp-integration` |
| `ontos-smoke`, `dp-integration-ci` | Its own repository only |
| `dp-content-deploy` | `dp-integration` and `data-contracts` |

1. Service connection: re-run step 5 with the pipeline ids. The id is in each pipeline's URL (`definitionId=`).
2. Repositories: the first run of each pipeline stops with "This pipeline needs permission to access a resource", once per repository it has not been allowed to read. Open the run, select View, then Permit. Alternatively, Project settings, Repositories, pick the repository, Security: allow the project build service to read it.
3. Environments: the first deploy may also ask to use `content-dev` and `content-prod`. Permit them the same way.

```bash
PIPELINE_IDS="<contract-ci-id> <publish-id> <ontos-smoke-id> <dp-content-deploy-id>" \
PROFILE=<workspace-profile> ACCOUNT_PROFILE=<account-profile> ENVIRONMENT=<env> ./publisher-identity.sh
```

> [!IMPORTANT]
> If your organisation limits job authorisation scope to the current project (the default), all three repositories must be in the same project. Otherwise turn off "Protect access to repositories in YAML pipelines" for this project, or move them together.

## 4. Run the smoke pipeline

Run `ontos-smoke` on `main`. It installs `dpi`, signs in as the publisher through the service connection, exchanges the Entra token for a Databricks token and calls Ontos. A green run proves the identity, the federation, the app permission and the Ontos role in one go.

`setup/ontos-smoke.yml`

```yaml
# Check that the publisher identity can call the Ontos API (run manually after setup or a rebuild).
# Databricks Apps accept Databricks OAuth tokens only, so dpi exchanges the identity's Entra token
# at the workspace token endpoint first. No federation policy is needed on Azure Databricks.
trigger: none

parameters:
  - name: environment
    type: string
    default: dev

variables:
  - template: /config/environments/${{ parameters.environment }}.yml
  - name: DP_CONFIG_DIR
    value: $(Build.SourcesDirectory)/config
  - name: DP_ENVIRONMENT
    value: ${{ parameters.environment }}

jobs:
  - job: smoke
    displayName: Call Ontos as the publisher identity
    pool:
      ${{ if ne(variables.agentPool, '') }}:
        name: ${{ variables.agentPool }}
      ${{ else }}:
        vmImage: ${{ variables.vmImage }}
    steps:
      - checkout: self
      - task: UsePythonVersion@0
        inputs:
          versionSpec: $(pythonVersion)
      - script: python -m pip install --quiet --disable-pip-version-check "$(Build.SourcesDirectory)"
        displayName: Install dpi
      - task: AzureCLI@2
        displayName: Call Ontos as the publisher identity
        inputs:
          azureSubscription: ${{ variables.serviceConnection }}
          scriptType: bash
          scriptLocation: inlineScript
          inlineScript: |
            set -euo pipefail
            # Application id of the Azure Databricks resource in Entra ID, the same in every tenant.
            export DATABRICKS_TOKEN=$(az account get-access-token --resource 2ff814a6-3304-4ab8-85cb-cd0e6f879c1d --query accessToken -o tsv)
            dpi ontos-check
```

## 5. Run the unit tests pipeline

Run `dp-integration-ci` once. Then add it as build validation on `dp-integration/main` (the same steps as on [Branch policies](10-branch-policies.md)) so a tooling change is tested before the other pipelines use it.

`pipelines/ci.yml`

```yaml
# Unit tests for the dpi package. Runs on every push to main; add it as build validation on main
# so a change to the shared code is tested before the contract pipelines pick it up.
trigger:
  branches:
    include:
      - main

pool:
  vmImage: ubuntu-latest

steps:
  - task: UsePythonVersion@0
    inputs:
      versionSpec: '3.12'
  - script: python -m pip install --quiet --disable-pip-version-check ".[test]"
    displayName: Install dpi and pytest
  - script: python -m pytest --junitxml="$(Common.TestResultsDirectory)/pytest.xml"
    displayName: Unit tests
  - task: PublishTestResults@2
    condition: always()
    inputs:
      testResultsFormat: JUnit
      testResultsFiles: '$(Common.TestResultsDirectory)/pytest.xml'
      testRunTitle: dpi unit tests
```

## 6. Run the deploy pipeline once, before the first publish

The publish pipeline has `dp-content-deploy` as a pipeline resource. Azure DevOps fails to compile a pipeline whose pipeline resource has no successful run yet, with "Unable to resolve latest version for pipeline content_deploy". So run `dp-content-deploy` on `main` and approve its prod stage before `publish` runs for the first time. Do the same after rebuilding an environment, and for each new product bundle before its entry goes into `publish.yml`.

The deploy builds the objects in dev, then in prod after the approval, and its completion triggers `publish`. What the bundle does is on [Data products, bundles and pipeline rules](15-data-products-bundles-and-pipeline-rules.md).

`dp-content/pipelines/deploy.yml`

```yaml
# Deploy the content bundle after merge: dev first, then prod once the content-prod
# Environment's approval is given. The contracts repository's publish pipeline runs when this
# one completes, so the Unity Catalog metadata is applied to what was just built.
#
# The pipeline applies the rules of the contracts in src/pipeline/contracts.txt, so a merged
# change to one of them redeploys too (the contracts trigger). Keep its paths and the manifest
# in step.
trigger:
  branches:
    include:
      - main
  paths:
    include:
      - databricks.yml
      - resources/**
      - src/**
pr: none

resources:
  repositories:
    - repository: integration
      type: git
      name: <ado-project>/dp-integration   # the project that holds dp-integration
      ref: refs/heads/main
    - repository: contracts
      type: git
      name: <ado-project>/data-contracts   # the project that holds data-contracts
      ref: refs/heads/main
      trigger:
        branches:
          include:
            - main
        paths:
          include:
            - contracts/content/content-linear.odcs.yaml

extends:
  template: templates/bundle-deploy.yml@integration
  parameters:
    rulesManifest: src/pipeline/contracts.txt
    environments:
      - environment: dev          # config/environments/dev.yml in dp-integration
        target: dev               # databricks.yml target
        adoEnvironment: content-dev
      - environment: prod
        target: prod
        adoEnvironment: content-prod    # add the Approvals check here
```

Full listing: [`dp-integration/templates/bundle-deploy.yml`](../dp-integration/templates/bundle-deploy.yml)
