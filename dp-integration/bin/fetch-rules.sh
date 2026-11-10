#!/usr/bin/env bash
# Copy a bundle's pipeline rules into it: the contracts its pipeline applies, and the module that
# turns them into Lakeflow expectations (src/dp_integration/expectations.py).
#
# Usage:
#   fetch-rules.sh <manifest> <data-contracts checkout> [<dp-integration checkout>]
#
# <manifest> is a file in the bundle (dp-content: src/pipeline/contracts.txt) with one contract
# path per line, relative to the data-contracts root; # starts a comment. The contracts go to
# contracts/ beside the manifest, and the module to contract_expectations.py beside it. The
# bundle git-ignores both and lists them under sync.include. templates/bundle-deploy.yml runs
# this before every deploy; run it yourself before a local deploy.
set -euo pipefail

manifest=${1:?manifest file}
contracts_root=${2:?data-contracts checkout}
integration_root=${3:-$(cd "$(dirname "$0")/.." && pwd)}
dest=$(dirname "$manifest")

module="$integration_root/src/dp_integration/expectations.py"
[ -f "$module" ] || { echo "no $module" >&2; exit 2; }
rm -rf "$dest/contracts" && mkdir -p "$dest/contracts"
n=0
while IFS= read -r line || [ -n "$line" ]; do
  path=${line%%#*}
  path=$(echo "$path" | xargs)
  [ -n "$path" ] || continue
  [ -f "$contracts_root/$path" ] || { echo "$manifest: no $path in $contracts_root" >&2; exit 2; }
  cp "$contracts_root/$path" "$dest/contracts/"
  echo "rules: $path"
  n=$((n + 1))
done < "$manifest"
[ "$n" -gt 0 ] || { echo "$manifest lists no contracts" >&2; exit 2; }
cp "$module" "$dest/contract_expectations.py"
echo "rules: $n contracts and contract_expectations.py in $dest"
