"""The dpi command: one entry point for the pipelines and the setup scripts.

    dpi check-mandatory contracts/**/*.odcs.yaml          blocking CI check, no environment needed
    dpi check-products products/**/*.odps.yaml            blocking CI check: products, domains, ports
    dpi -e dev publish-uc contracts/**/*.odcs.yaml        Unity Catalog tags, comments, grants
    dpi -e dev check-expectations contracts/**/*.odcs.yaml  try the Lakeflow expectations on the objects
    dpi -e dev sync-domains                               Ontos domains and owner teams
    dpi -e dev sync-ontos --quality-dir q contracts/...   Ontos registration, versions, quality
    dpi -e dev sync-products products/**/*.odps.yaml      Ontos products, status, certification
    dpi -e dev ontos-roles                                setup: map groups onto Ontos roles
    dpi -e dev ontos-check                                setup: call Ontos as the current identity
    dpi -e dev config get env.warehouseId                 setup scripts: read one setting
    dpi new-product --domain sales --product orders       start a product: contract, product file, bundle

Exit codes: 0 success, 1 at least one contract or call failed, 2 a setting is missing or invalid.

Auth, in this order:
- Databricks: DATABRICKS_TOKEN (the pipeline's Entra token) against the environment's host,
  else --profile (or DATABRICKS_CONFIG_PROFILE) from ~/.databrickscfg.
- Ontos: ONTOS_TOKEN, else DATABRICKS_TOKEN exchanged at the workspace token endpoint, else the
  profile's token (which must be a Databricks OAuth login).
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from functools import cached_property
from pathlib import Path
from typing import Callable, Mapping, Sequence

from . import __version__, logs
from .config import ConfigError, Environment, Settings
from .contract import ContractError, DataContract
from .domains import DomainRegistry, OntosDomainSync, team_ids
from .mandatory import MandatoryFieldChecker
from .ontos import OntosClient, OntosError, SdkToken, StaticToken, TokenProvider, WorkspaceTokenExchange
from .ontos_roles import OntosRoleMapper
from .ontos_sync import OntosContractSync, OwnerTeam
from .quality import QualityResults

# unity_catalog is imported in publish_uc: it loads databricks.sdk, which costs about half a
# second, and the setup scripts call `dpi config get` many times.

log = logging.getLogger(__name__)

OK, FAILED, CONFIG_ERROR = 0, 1, 2


class CommandContext:
    """Builds what the commands need from the settings and the process environment. This is the
    one place that creates clients; tests pass their own `workspace` and `ontos` instead."""

    def __init__(self, settings: Settings, profile: str | None = None,
                 environ: Mapping[str, str] | None = None, workspace=None, ontos: OntosClient | None = None):
        self.settings, self.profile = settings, profile
        self.environ = os.environ if environ is None else environ
        if workspace is not None:
            self.__dict__["workspace"] = workspace
        if ontos is not None:
            self.__dict__["ontos"] = ontos

    @cached_property
    def workspace(self):
        from databricks.sdk import WorkspaceClient

        token = self.environ.get("DATABRICKS_TOKEN")
        if token:
            return WorkspaceClient(host=self.settings.env.workspace_url, token=token, auth_type="pat")
        return WorkspaceClient(profile=self.profile) if self.profile else WorkspaceClient()

    @cached_property
    def ontos_token(self) -> TokenProvider:
        if self.environ.get("ONTOS_TOKEN"):
            return StaticToken(self.environ["ONTOS_TOKEN"])
        if self.environ.get("DATABRICKS_TOKEN"):
            return WorkspaceTokenExchange(self.settings.env.workspace_url,
                                          StaticToken(self.environ["DATABRICKS_TOKEN"]),
                                          timeout=self.settings.ontos.request_timeout_seconds)
        return SdkToken(self.workspace)

    @cached_property
    def ontos(self) -> OntosClient:
        return OntosClient(self.settings.env.ontos_url, self.ontos_token,
                           timeout=self.settings.ontos.request_timeout_seconds)

    def contracts(self, paths: Sequence[str]) -> list[DataContract | None]:
        """Each contract file, or None (logged against the file) if it cannot be read."""
        loaded = []
        for path in paths:
            try:
                loaded.append(DataContract.from_file(path, self.settings.contract.access_file_suffix))
            except (OSError, ValueError) as e:  # ContractError is a ValueError
                log.error("cannot read contract: %s", e, extra={"source": path})
                loaded.append(None)
        return loaded

    def contracts_by_id(self, folder: str) -> dict[str, DataContract]:
        """Every contract under `folder`, by ODCS id; a file that cannot be read is logged and skipped."""
        paths = sorted(str(p) for p in Path(folder).glob("**/*.odcs.yaml"))
        return {c.id: c for c in self.contracts(paths) if c is not None}

    def products(self, paths: Sequence[str]) -> list:
        from .products import DataProductFile

        loaded = []
        for path in paths:
            try:
                loaded.append(DataProductFile.from_file(path))
            except (OSError, ValueError) as e:
                log.error("cannot read data product: %s", e, extra={"source": path})
                loaded.append(None)
        return loaded


# Commands. Each takes the parsed arguments and a CommandContext and returns an exit code.

def check_mandatory(args: argparse.Namespace, ctx: CommandContext) -> int:
    s = ctx.settings
    checker = MandatoryFieldChecker.from_file(args.schema or s.contract.mandatory_schema,
                                              s.tags.tier_values, s.contract.tier_property)
    failed = 0
    for path in args.contracts:
        try:
            findings = checker.check(path)
        except OSError as e:
            log.error("cannot read contract: %s", e, extra={"source": path})
            failed += 1
            continue
        for f in findings:
            log.error(f.message, extra={"source": f.source, "line": f.line})
        if findings:
            failed += 1
        else:
            log.info("%s: mandatory fields present", path)
    log.info("%d of %d contracts failed the mandatory-field check", failed, len(args.contracts))
    return FAILED if failed else OK


def publish_uc(args: argparse.Namespace, ctx: CommandContext) -> int:
    return _each(args.contracts, ctx, "publish failed", _uc_publisher(args, ctx).publish)


def _uc_publisher(args: argparse.Namespace, ctx: CommandContext):
    from .unity_catalog import StatementRunner, UnityCatalogPublisher

    s = ctx.settings
    return UnityCatalogPublisher(
        ws=ctx.workspace, run_sql=StatementRunner(ctx.workspace, s.env.warehouse_id, dry_run=args.dry_run),
        tags=s.tags, fields=s.contract, grants=s.grants, server=args.server or s.env.contract_server,
        commit=args.commit, dry_run=args.dry_run, policy=s.publish)


def check_expectations(args: argparse.Namespace, ctx: CommandContext) -> int:
    from .expectation_check import ExpectationCheck
    from .unity_catalog import StatementRunner

    s = ctx.settings
    check = ExpectationCheck(ctx.workspace, StatementRunner(ctx.workspace, s.env.warehouse_id).rows,
                             server=args.server or s.env.contract_server)
    return _each(args.contracts, ctx, "expectation check failed", check.check)


def sync_ontos(args: argparse.Namespace, ctx: CommandContext) -> int:
    quality = QualityResults.from_dir(args.quality_dir, ctx.settings.ontos.quality_dimensions)
    log.debug("%r", quality)
    with ctx.ontos as client:
        owners = {}
        if args.registry.is_file():  # each domain's team owns its contracts (see ontos_sync)
            teams = team_ids(client)
            owners = {d.name: OwnerTeam(d.team.name, teams[d.team.name])
                      for d in DomainRegistry.from_file(args.registry)
                      if d.team and d.team.name in teams}
        sync = OntosContractSync(client, ctx.settings.ontos, owners)
        return _each(args.contracts, ctx, "Ontos sync failed", lambda c: sync.sync(c, quality) is not None)


def check_products(args: argparse.Namespace, ctx: CommandContext) -> int:
    from .products import check_product, check_registry

    contracts = ctx.contracts_by_id(args.contracts_dir)
    try:
        registry = DomainRegistry.from_file(args.registry)
    except (OSError, ValueError) as e:
        log.error("cannot read the domain registry: %s", e, extra={"source": str(args.registry)})
        return FAILED
    failed = 0
    for problem in check_registry(registry, ctx.settings.tags.domain_values):
        log.error(problem, extra={"source": str(args.registry)})
        failed += 1
    for contract in contracts.values():
        if contract.domain and registry.get(contract.domain) is None:
            log.error("domain '%s' is not in %s", contract.domain, registry.path.name, extra={"source": str(contract.path)})
            failed += 1
    for path, product in zip(args.products, ctx.products(args.products)):
        if product is None:  # already logged
            failed += 1
            continue
        problems = check_product(product, contracts, registry, ctx.settings.tags.domain_values)
        for problem in problems:
            log.error(problem, extra={"source": path})
        failed += bool(problems)
        if not problems:
            log.info("%s: %s ok", path, product)
    log.info("check-products: %d failed (registry, contracts and %d products)", failed, len(args.products))
    return FAILED if failed else OK


def sync_domains(args: argparse.Namespace, ctx: CommandContext) -> int:
    registry = DomainRegistry.from_file(args.registry)
    with ctx.ontos as client:
        OntosDomainSync(client).sync(registry)
    return OK


def sync_products(args: argparse.Namespace, ctx: CommandContext) -> int:
    from .products import GateError, OntosProductSync

    registry = DomainRegistry.from_file(args.registry)
    contracts = ctx.contracts_by_id(args.contracts_dir)
    failed = 0
    with ctx.ontos as client:
        teams = team_ids(client)
        sync = OntosProductSync(client, ctx.settings.ontos, _uc_publisher(args, ctx))
        for path, product in zip(args.products, ctx.products(args.products)):
            if product is None:
                failed += 1
                continue
            domain = registry.get(product.domain)
            team = teams.get(domain.team.name) if domain and domain.team else None
            if team is None:
                log.error("no Ontos team for domain '%s': run sync-domains first", product.domain,
                          extra={"source": path})
                failed += 1
                continue
            try:
                sync.sync(product, contracts, team)
            except (GateError, ContractError, OntosError, ValueError) as e:
                log.error("product sync failed: %s", e, extra={"source": path})
                failed += 1
            except Exception as e:  # an SDK error: report it against the file, keep going
                log.error("product sync failed: %s: %s", type(e).__name__, e, extra={"source": path})
                failed += 1
    log.info("%d of %d products done, %d failed", len(args.products) - failed, len(args.products), failed)
    return FAILED if failed else OK


def new_product(args: argparse.Namespace, ctx: CommandContext) -> int:
    from .new_product import NewProduct, TemplateError

    try:
        new = NewProduct.build(args.domain, args.product, args.contract, args.dataset,
                               args.catalog_dev, args.catalog_prod)
    except TemplateError as e:
        log.error("new-product: %s", e)
        return FAILED
    domain_values = ctx.settings.tags.domain_values
    if new.domain not in domain_values:
        log.error("domain '%s' is not an allowed value of the domain tag (%s). The platform team adds it to "
                  "tags.domain_values in config/platform.yaml and to the domain enum in the mandatory schema, "
                  "and creates its catalogs", new.domain, ", ".join(domain_values))
        return FAILED
    contracts_root = Path(args.contracts_root)
    bundle_dir = Path(args.bundle_dir or contracts_root.resolve().parent / new.values["bundle"])
    try:
        written = new.write(contracts_root, bundle_dir)
    except TemplateError as e:
        log.error("new-product: %s", e)
        return FAILED
    for path in written:
        log.info("wrote %s", path)
    registry = contracts_root / "domains" / "domains.yaml"
    known = registry.is_file() and DomainRegistry.from_file(registry).get(new.domain) is not None
    v = new.values
    steps = [] if known else [f"Add the domain to {registry} (a platform owner reviews it):\n\n{new.registry_entry(ctx.settings.ontos.publisher_role.group)}"]
    steps += [
        "Replace every <placeholder> in the contract, access and product files, and describe the real columns.",
        f"Open a pull request in data-contracts: contract CI checks it, the {new.domain} Data Stewards approve.",
        f"In {bundle_dir}: fill in the workspace host, source_table and the pipeline query, then push it as "
        f"the {v['bundle']} repository and replace <ado-project> in pipelines/deploy.yml.",
        f"Create the Azure DevOps Environments {new.product}-dev and {new.product}-prod (Approvals on prod) "
        f"and the pipeline {v['bundle']}-deploy; run it once, then add it to data-contracts/pipelines/publish.yml.",
        "Move the contract and product to approved (G3) before the build, and to active (G4) after it.",
    ]
    sys.stdout.write("Next steps:\n" + "".join(f"  {n}. {step}\n" for n, step in enumerate(steps, 1)))
    return OK


def ontos_roles(args: argparse.Namespace, ctx: CommandContext) -> int:
    with ctx.ontos as client:
        OntosRoleMapper(client, ctx.settings.ontos).apply(dry_run=args.dry_run)
    return OK


def ontos_check(args: argparse.Namespace, ctx: CommandContext) -> int:
    with ctx.ontos as client:
        user = client.user_info()
        log.info("Ontos %s as %s", client.base_url, user.get("email") or user.get("username") or user)
        log.info("permissions: %s", client.user_permissions())
        log.info("%d data contracts visible", len(client.list_contracts(include_history=False)))
    return OK


def config_get(args: argparse.Namespace, ctx: CommandContext) -> int:
    _write(ctx.settings.get(args.key))
    return OK


def config_tag_key(args: argparse.Namespace, ctx: CommandContext) -> int:
    _write(ctx.settings.tags.key(_logical(args.logical, ctx)))
    return OK


def config_tag_values(args: argparse.Namespace, ctx: CommandContext) -> int:
    _write(ctx.settings.tags.allowed(_logical(args.logical, ctx)))
    return OK


def _logical(name: str, ctx: CommandContext) -> str:
    if name not in ctx.settings.tags.LOGICAL:
        raise ConfigError(f"no tag '{name}': use one of {', '.join(ctx.settings.tags.LOGICAL)}")
    return name


def _write(value) -> None:
    """Command output for scripts: a list one item per line, anything else as one line."""
    items = value if isinstance(value, (list, tuple, set, frozenset)) else [value]
    sys.stdout.write("".join(f"{item}\n" for item in items))


def _each(paths: Sequence[str], ctx: CommandContext, what: str,
          action: Callable[[DataContract], bool]) -> int:
    """Run `action` on every contract and carry on after a failure; FAILED if any failed."""
    failed = 0
    for path, contract in zip(paths, ctx.contracts(paths)):
        if contract is None:
            failed += 1
            continue
        try:
            if not action(contract):
                failed += 1
        except (ContractError, OntosError, RuntimeError, KeyError, ValueError) as e:
            log.error("%s: %s", what, e, extra={"source": path})
            failed += 1
        except Exception as e:  # an SDK error: report it against the file, keep going
            log.error("%s: %s: %s", what, type(e).__name__, e, extra={"source": path})
            failed += 1
    log.info("%d of %d contracts done, %d failed", len(paths) - failed, len(paths), failed)
    return FAILED if failed else OK


def _common(suppress: bool) -> argparse.ArgumentParser:
    """Options accepted before or after the command. After it they default to SUPPRESS, so a
    subcommand does not overwrite a value given before the command with its own default."""
    default = argparse.SUPPRESS if suppress else None
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-c", "--config-dir", default=default,
                        help="folder with platform.yaml (default: DP_CONFIG_DIR or ./config)")
    common.add_argument("-e", "--environment", default=default,
                        help="environments/<name>.yml to use (default: DP_ENVIRONMENT)")
    common.add_argument("--log-level", default=default, choices=("DEBUG", "INFO", "WARNING", "ERROR"),
                        help="override logging.level for this run")
    common.add_argument("--profile", default=default,
                        help="Databricks CLI profile for local runs (default: DATABRICKS_CONFIG_PROFILE)")
    return common


def build_parser() -> argparse.ArgumentParser:
    common = _common(suppress=True)
    parser = argparse.ArgumentParser(prog="dpi", parents=[_common(suppress=False)], description=__doc__.splitlines()[0])
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True, metavar="command")

    p = sub.add_parser("check-mandatory", parents=[common], help="mandatory-field check (CI, blocking)")
    p.add_argument("--schema", type=Path, help="schema to use instead of contract.mandatory_schema")
    p.add_argument("contracts", nargs="+")
    p.set_defaults(func=check_mandatory)

    p = sub.add_parser("publish-uc", parents=[common], help="publish contracts to Unity Catalog")
    p.add_argument("--server", help="contract server to publish (default: the environment's contractServer)")
    p.add_argument("--commit", default=os.environ.get("BUILD_SOURCEVERSION"),
                   help="commit recorded on each table (default: BUILD_SOURCEVERSION)")
    p.add_argument("--dry-run", action="store_true", help="log the changes, make none")
    p.add_argument("contracts", nargs="+")
    p.set_defaults(func=publish_uc)

    p = sub.add_parser("check-expectations", parents=[common],
                       help="run the contracts' Lakeflow expectations on the built objects (read only)")
    p.add_argument("--server", help="contract server to check (default: the environment's contractServer)")
    p.add_argument("contracts", nargs="+")
    p.set_defaults(func=check_expectations)

    p = sub.add_parser("check-products", parents=[common], help="product, domain and port check (CI, blocking)")
    p.add_argument("--registry", type=Path, default=Path("domains/domains.yaml"), help="domain registry")
    p.add_argument("--contracts-dir", default="contracts", help="folder searched for *.odcs.yaml")
    p.add_argument("products", nargs="*")
    p.set_defaults(func=check_products, env_required=())

    p = sub.add_parser("sync-domains", parents=[common], help="create or update the Ontos domains and teams")
    p.add_argument("registry", nargs="?", type=Path, default=Path("domains/domains.yaml"))
    p.set_defaults(func=sync_domains)

    p = sub.add_parser("sync-products", parents=[common], help="register data products in Ontos")
    p.add_argument("--registry", type=Path, default=Path("domains/domains.yaml"), help="domain registry")
    p.add_argument("--contracts-dir", default="contracts", help="folder searched for *.odcs.yaml")
    p.add_argument("--server", help="contract server of the port objects (default: the environment's contractServer)")
    p.add_argument("--commit", default=os.environ.get("BUILD_SOURCEVERSION"), help=argparse.SUPPRESS)
    p.add_argument("--dry-run", action="store_true", help="log the Unity Catalog changes, make none")
    p.add_argument("products", nargs="+")
    p.set_defaults(func=sync_products)

    p = sub.add_parser("sync-ontos", parents=[common], help="register contracts in Ontos")
    p.add_argument("--quality-dir", help="folder of `datacontract test --output-format json` results")
    p.add_argument("--registry", type=Path, default=Path("domains/domains.yaml"),
                   help="domain registry; its teams own the contracts (skipped if the file is missing)")
    p.add_argument("contracts", nargs="+")
    p.set_defaults(func=sync_ontos)

    p = sub.add_parser("new-product", parents=[common],
                       help="start a data product: contract, access and product files, and its bundle")
    p.add_argument("--domain", required=True, help="an allowed value of the domain tag")
    p.add_argument("--product", required=True, help="product id, lower case words joined by -")
    p.add_argument("--contract", help="contract id of the output port (default: the product id)")
    p.add_argument("--dataset", help="first object the pipeline builds (default: the product id, with _)")
    p.add_argument("--catalog-dev", help="catalog of the dev server (default: <domain>_dev)")
    p.add_argument("--catalog-prod", help="catalog of the prod server (default: <domain>_prod)")
    p.add_argument("--contracts-root", default=".", help="data-contracts checkout (default: .)")
    p.add_argument("--bundle-dir", help="folder for the new bundle (default: ../dp-<product>)")
    p.set_defaults(func=new_product, env_required=())

    p = sub.add_parser("ontos-roles", parents=[common], help="map platform groups onto Ontos roles (admin)")
    p.add_argument("--dry-run", action="store_true", help="log the changes, make none")
    p.set_defaults(func=ontos_roles)

    p = sub.add_parser("ontos-check", parents=[common], help="call the Ontos API as the current identity")
    p.set_defaults(func=ontos_check)

    config = sub.add_parser("config", parents=[common], help="read settings (for the setup scripts)")
    config.set_defaults(env_required=())  # each value read is checked instead
    csub = config.add_subparsers(dest="config_command", required=True, metavar="command")
    p = csub.add_parser("get", parents=[common], help="one setting by dotted path, e.g. env.warehouseId")
    p.add_argument("key")
    p.set_defaults(func=config_get)
    p = csub.add_parser("tag-key", parents=[common], help="full tag key, e.g. tier -> dp_tier")
    p.add_argument("logical")
    p.set_defaults(func=config_tag_key)
    p = csub.add_parser("tag-values", parents=[common], help="allowed values of a governed tag")
    p.add_argument("logical")
    p.set_defaults(func=config_tag_values)
    return parser


def main(argv: Sequence[str] | None = None, environ: Mapping[str, str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    environ = os.environ if environ is None else environ
    logs.configure("WARNING")  # until the settings say otherwise
    try:
        overrides = dict(environ, **({"DP_LOG_LEVEL": args.log_level} if args.log_level else {}))
        settings = Settings.load(args.config_dir, args.environment, overrides,
                                 getattr(args, "env_required", Environment.REQUIRED))
        logs.configure(settings.log_level)
        log.debug("dpi %s, config %s, environment %s", __version__, settings.config_dir,
                  settings.environment.name if settings.environment else None)
        return args.func(args, CommandContext(settings, args.profile, environ))
    except ConfigError as e:
        log.error("configuration: %s", e)
        return CONFIG_ERROR


if __name__ == "__main__":
    sys.exit(main())
