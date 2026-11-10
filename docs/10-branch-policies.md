# Branch policies

## What this step does

The branch policies make the Data Steward's approval the business signature. Without them anyone with write access could change a contract and publish it.

Open Project settings, Repositories, `data-contracts`, Policies, then the `main` branch.

## 1. Require a reviewer

1. Turn on **Require a minimum number of reviewers**, set it to 1.
2. Leave **Allow requestors to approve their own changes** off.
3. Turn on **When new changes are pushed: reset all approval votes**.

## 2. Contract CI as build validation

1. Under Build Validation, add a policy for the `contract-ci` pipeline.
2. Trigger: Automatic. Policy requirement: Required. Build expiration: Immediately when `main` is updated.
3. Display name: Contract CI.

## 3. Stewards as required reviewers per product

1. Under Automatically included reviewers, add the product's Stewards group, for example **Content Data Stewards**.
2. Policy requirement: Required.
3. Path filter: `/contracts/content/*;/products/content/*` (the product's contract and product folders).
4. Repeat for every product, each with its own Stewards group and folders.

## 4. Platform owners on the domain registry

1. Add one more required reviewer: the platform owners' group (the reference setup used Project Administrators).
2. Path filter: `/domains/*`.
3. A new domain also needs a domain tag value and its catalogs, which only the platform team can add.

> [!IMPORTANT]
> The publish pipeline only runs on `main`, so these policies are the whole approval control at Tier 2. Also deny **Bypass policies** to everyone except a break-glass group.

## Check it worked

1. Try to push straight to `main`. Azure DevOps refuses with `TF402455: Pushes to this branch are not permitted`.
2. Open a pull request that changes a contract or a product file. It shows Contract CI and the Stewards group as required.
3. The pull request cannot complete until a Steward approves and Contract CI passes.

## Optional: the same with the CLI

```bash
ORG=https://dev.azure.com/<organisation>; PROJECT=<project>
REPO=$(az repos show --organization $ORG --project "$PROJECT" --repository data-contracts --query id -o tsv)
CI=$(az pipelines show --organization $ORG --project "$PROJECT" --name contract-ci --query id -o tsv)
az repos policy approver-count create --organization $ORG --project "$PROJECT" --repository-id $REPO \
  --branch main --blocking true --enabled true --minimum-approver-count 1 \
  --creator-vote-counts false --allow-downvotes false --reset-on-source-push true
az repos policy build create --organization $ORG --project "$PROJECT" --repository-id $REPO \
  --branch main --blocking true --enabled true --build-definition-id $CI \
  --display-name "Contract CI" --manual-queue-only false --queue-on-source-update-only false --valid-duration 0
```

Add the Stewards and platform owners groups as required reviewers in the UI (parts 3 and 4): the CLI takes reviewers by email address.

> [!IMPORTANT]
> Bundle repositories such as `dp-content` need the same reviewer and build rules on their `main`, set by the team that owns the product. The prod deploy is gated separately, by the Approvals check on the `content-prod` Environment.
