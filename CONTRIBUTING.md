# Contributing

Thanks for helping. This repository is a reference implementation of governance as code on
Databricks, so changes should keep it working end to end and keep it general: no organisation
names, hosts or ids in any file.

## Before you open a pull request

With Python 3.11 or later, from the repository root:

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e "./dp-integration[test]"
(cd dp-integration && python -m pytest -q)

cd data-contracts
export DP_CONFIG_DIR=../dp-integration/config
dpi check-mandatory contracts/*/*.odcs.yaml
dpi check-products products/*/*.odps.yaml
cd ..

python3 scripts/check-secrets.py
python3 scripts/build-diagrams.py --check
```

`scripts/check-secrets.py` fails on anything that looks like a Databricks personal access token.

## What a good change looks like

- **One concern per pull request.** A fix to the publish logic and a docs rewrite are two PRs.
- **Tests with code.** Every `dpi` command has unit tests that run without Databricks, Azure or
  Ontos (`dp-integration/tests/`); add to them when you change behaviour.
- **Docs with behaviour.** If a change alters what a pipeline does, update the page in `docs/`
  that explains it, and `docs/how-it-works.md` if the flow changes.
- **Diagrams from source.** The infographics in `docs/assets/` are built by
  `scripts/build-diagrams.py`. Change the labels there, run it, and commit both; CI runs it with
  `--check`.
- **Placeholders, not values.** Anything specific to an installation is a `<placeholder>` in
  `dp-integration/config/environments/`. The setup scripts stop on an unset one.
- **Public sample data only** in examples, such as the `samples` catalog every workspace has.

## Writing style

Docs use plain, active sentences. Headings in sentence case. Code, commands and file names in backticks.

## Reporting a problem

Open an issue with the bug or question template. For a security problem, see
[SECURITY.md](SECURITY.md) instead: do not open a public issue.
