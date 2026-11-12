# Security

## Reporting a vulnerability

Please do not open a public issue. Use GitHub's private vulnerability reporting on this
repository (Security tab, "Report a vulnerability"), with the steps to reproduce and the impact
you see. You will get a reply as soon as the maintainer can look at it.

## How this repository handles secrets

There are none to handle, by design:

- The Azure DevOps pipelines sign in with workload identity federation to a user-assigned managed
  identity. No client secret, personal access token or password is stored in a pipeline, a
  variable group or a file.
- Configuration files hold hosts, ids and names only. Installation values are `<placeholder>`
  entries until you fill them in.
- `scripts/check-text.py` fails CI on anything that looks like a Databricks personal access token.

If you find a secret in this repository or its history, report it as above.

## Scope

This is a reference implementation, provided as is under the Apache License 2.0. It is not a
supported Databricks product. Ontos is a separate project with its own security policy.
