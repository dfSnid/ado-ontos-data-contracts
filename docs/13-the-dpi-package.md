# The dpi package

## Design

- **One composition root.** `CommandContext` in `cli.py` is the only place that creates the Databricks and Ontos clients. Every other class takes its clients and policies in its constructor, so a test or an extension passes its own.
- **Configuration as objects.** `Settings.load()` reads `platform.yaml` and the environment file once into typed, frozen objects (`TagPolicy`, `GrantPolicy`, `OntosPolicy` and so on). Bad values fail at load time with a clear message and exit code 2.
- **Standard logging.** Every module uses `logging.getLogger(__name__)`. Nothing prints. Only `dpi config` writes values to stdout, for the setup scripts to read.
- **Errors against the file.** A log record can carry `source` and `line`. In Azure DevOps they become an annotation on that file and line.

## Modules and classes

| Module | Class or function | Responsibility |
|---|---|---|
| `config.py` | `Settings`, `Environment`, `TagPolicy`, `ContractFields`, `GrantPolicy`, `OntosPolicy`, `PublisherRole`, `SetupPolicy` | Load and validate configuration; `Settings.get("a.b")` for scripts |
| `contract.py` | `DataContract`, `ContractTable`, `Column`, `AccessPolicy`, `Grant`, `Version` | Read an ODCS file and its access file |
| `mandatory.py` | `MandatoryFieldChecker`, `LineIndex`, `Finding` | Validate against the overlay schema; map each error to a line |
| `unity_catalog.py` | `UnityCatalogPublisher`, `StatementRunner` | Comments, tags, properties and grants |
| `quality.py` | `QualityResults`, `QualityItem` | Read `datacontract test` JSON and score each dimension |
| `ontos.py` | `OntosClient`, `StaticToken`, `WorkspaceTokenExchange`, `SdkToken` | The Ontos REST API and how to get a token for it |
| `ontos_sync.py` | `OntosContractSync` | Import, version and status in Ontos; add quality items |
| `ontos_roles.py` | `OntosRoleMapper` | Create the publisher role and add groups to roles |
| `domains.py` | `DomainRegistry`, `Domain`, `Team`, `OntosDomainSync` | Read `domains/domains.yaml`; create domains, owner teams and their groups in Ontos |
| `products.py` | `DataProductFile`, `OutputPort`, `OntosProductSync`, `GateError`, `check_product()` | Read an ODPS product file; check it in CI; sync it to Ontos behind the gates, with certification, badges and scope |
| `lifecycle.py` | `walk()`, `at_least()`, the Ontos transitions | The shortest walk of allowed status steps from one status to another |
| `expectations.py` | `ContractExpectations`, `Expectation` | Turn a contract's row-level rules into Lakeflow expectations. Copied into product bundles, so it needs PyYAML only |
| `new_product.py` | `NewProduct`, `TemplateError` | Render `product_template/` for `dpi new-product`: the contract, access and product files, and the bundle |
| `expectation_check.py` | `ExpectationCheck` | Run each rule on the built objects and count the rows it would flag |
| `logs.py` | `SourceFormatter`, `AzureDevOpsIssueHandler`, `configure()` | Log format and Azure DevOps annotations |
| `cli.py` | `CommandContext`, `build_parser()`, `main()` | The `dpi` command |

## Special methods

| Class | Method | Why |
|---|---|---|
| `Version` | `__eq__`, `__lt__`, `__hash__` with `functools.total_ordering` | Compare `1.10.0` and `1.9.0` correctly, put `1.9.0-rc1` before `1.9.0`, and use versions as keys |
| `StatementRunner` | `__call__` | A runner is a function from SQL to nothing; tests pass a list's `append` |
| `StaticToken`, `WorkspaceTokenExchange`, `SdkToken` | `__call__` | Any callable that returns a token is a token provider |
| `StaticToken` | `__repr__` | Never shows the token in a log or traceback |
| `OntosClient` | `__enter__`, `__exit__` | `with OntosClient(...) as ontos:` closes the HTTP session |
| `AccessPolicy` | `__iter__`, `__len__` | Loop over grants; an empty policy is falsy |
| `QualityResults` | `collections.abc.Mapping` | Behaves as a read-only dict keyed by contract id and version |
| `Environment`, `LineIndex` | `__getitem__` | `env["warehouseId"]` and `lines[path]` |
| Most classes | `__repr__`, `__str__` | Readable logs and debugging |

## Logging

`configure()` in `logs.py` sets up the `dp_integration` logger once, from `--log-level`, `DP_LOG_LEVEL` or `logging.level` in `platform.yaml`.

- Every record goes to stderr as `LEVEL   file:line: message`.
- When `TF_BUILD=true` (any Azure DevOps job), warnings and errors are also written as `##vso[task.logissue ...]` with the file and line, so they show on the run and the pull request.
- `INFO` shows every SQL statement; `DEBUG` adds every Ontos HTTP call and the token exchange.

```python
import logging
log = logging.getLogger(__name__)

log.error("missing a named Data Steward", extra={"source": "contracts/sales/orders.odcs.yaml", "line": 23})
```

## Extend it

### Use the classes from Python

```python
from dp_integration.config import Settings
from dp_integration.contract import DataContract
from dp_integration.unity_catalog import StatementRunner, UnityCatalogPublisher
from databricks.sdk import WorkspaceClient

settings = Settings.load("dp-integration/config", "dev")
ws = WorkspaceClient(profile="<workspace-profile>")
publisher = UnityCatalogPublisher(
    ws, StatementRunner(ws, settings.env.warehouse_id, dry_run=True),
    settings.tags, settings.contract, settings.grants, settings.env.contract_server, dry_run=True)
publisher.publish(DataContract.from_file("contracts/sales/orders.odcs.yaml"))
```

### Inject a fake in a test

```python
from dp_integration.cli import CommandContext, main

statements = []
publisher = UnityCatalogPublisher(fake_ws, statements.append, ...)   # no warehouse needed
ctx = CommandContext(settings, workspace=fake_ws, ontos=fake_ontos)   # what the tests do
```

### Common changes

| To | Change |
|---|---|
| Add a governed tag | Add the key to `tags.keys` in `platform.yaml`, a value mapping, and a line in `UnityCatalogPublisher.tags_for()`; add a `tag_policy` line in `bootstrap.sh` |
| Publish another table property | `UnityCatalogPublisher.properties_for()` |
| Send quality results elsewhere | A new class that takes `QualityResults`; call it from `sync_ontos` in `cli.py` |
| Use a self-signed Ontos certificate or a proxy | Pass your own `requests.Session` to `OntosClient` |
| A new command | A function `(args, ctx) -> int` in `cli.py` and a sub-parser in `build_parser()` |
| A new kind of pipeline rule | `ContractExpectations._rule()` in `expectations.py`, and a test in `tests/test_expectations.py` |
| A change to what new products start with | The files under `product_template/` (`{{key}}` placeholders), and `tests/test_new_product.py` |
| A new product gate | `check_product()` (CI, no Ontos) and `OntosProductSync._gate()` (publish) |

## Tests

```bash
pip install -e "./dp-integration[test]"
cd dp-integration && python -m pytest -q
```

131 tests, with a fake workspace and a fake Ontos in `tests/conftest.py`. They cover the configuration loading and overrides, the contract model, the mandatory check and its line numbers, the Unity Catalog publisher (drift, grants, idempotency, materialized and metric views), the Ontos sync and roles, domains, products and their gates, the lifecycle walk, the pipeline rules and their check, the new-product templates, token exchange, logging and the command line.
