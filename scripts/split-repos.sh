#!/usr/bin/env bash
# Turn the three folders of this repository into three Git repositories for Azure DevOps.
#
# Usage: scripts/split-repos.sh <ado-organisation> <ado-project> [OUT_DIR]
#
# Copies dp-integration, data-contracts and dp-orders into OUT_DIR (default ./ado-repos), puts
# the project name into the pipeline files and the environment files, makes one commit on main
# in each and adds the Azure DevOps remote. It never pushes: the push commands are printed at
# the end. Fill in the environment files (docs/03-configuration.md) before or after, as you prefer.
set -euo pipefail

ORG="${1:?usage: scripts/split-repos.sh <ado-organisation> <ado-project> [out-dir]}"
PROJECT="${2:?usage: scripts/split-repos.sh <ado-organisation> <ado-project> [out-dir]}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${3:-$ROOT/ado-repos}"
REPOS=(dp-integration data-contracts dp-orders)

case "$PROJECT$ORG" in *'|'*|*'&'*|*'\'*) echo "names must not contain | & or \\" >&2; exit 2 ;; esac
[ ! -e "$OUT" ] || { echo "$OUT already exists: remove it or pass another folder" >&2; exit 1; }

for repo in "${REPOS[@]}"; do
  mkdir -p "$OUT/$repo"
  rsync -a --exclude build/ --exclude '*.egg-info/' --exclude __pycache__/ --exclude .pytest_cache/ \
    --exclude .venv/ --exclude .databricks/ "$ROOT/$repo/" "$OUT/$repo/"
done

# The pipeline files name the other repositories by project; the setup scripts read the
# organisation and project from the environment files.
fill() { sed -i.bak "$1" "$2" && rm "$2.bak"; }
for f in data-contracts/pipelines/contract-ci.yml data-contracts/pipelines/publish.yml dp-orders/pipelines/deploy.yml; do
  fill "s|<ado-project>|$PROJECT|g" "$OUT/$f"
done
for env in dev prod; do
  f="$OUT/dp-integration/config/environments/$env.yml"
  fill "s|<azure devops organisation>|$ORG|; s|<azure devops project>|$PROJECT|" "$f"
done
if grep -rn "<ado-project>" "$OUT" >/dev/null; then
  echo "an <ado-project> placeholder is left:" >&2; grep -rn "<ado-project>" "$OUT" >&2; exit 1
fi

for repo in "${REPOS[@]}"; do
  (cd "$OUT/$repo" && git init -q -b main && git add . && git commit -q -m "Initial import" &&
   git remote add origin "https://dev.azure.com/$ORG/${PROJECT// /%20}/_git/$repo")
done

cat <<EOF
Ready in $OUT. Create the three empty repositories in $ORG/$PROJECT, then push each:

  for repo in ${REPOS[*]}; do (cd "$OUT/\$repo" && git push -u origin main); done

To push with your Entra sign-in instead of a personal access token:

  T=\$(az account get-access-token --resource 499b84ac-1321-427f-aa17-267ca6975798 --query accessToken -o tsv)
  for repo in ${REPOS[*]}; do (cd "$OUT/\$repo" && git -c http.extraheader="Authorization: Bearer \$T" push -u origin main); done
EOF
