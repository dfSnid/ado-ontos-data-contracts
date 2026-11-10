"""Settings: one platform file plus one file per environment.

    config/platform.yaml            HOW contracts are published (tags, grants, Ontos roles)
    config/environments/<name>.yml  WHERE they are published (hosts, ids, service connection)

The environment file is also an Azure DevOps variable template, so the pipelines and this
package read the same values. Precedence, lowest first: the files, then DP_<NAME> environment
variables (for example DP_WAREHOUSE_ID or DP_LOG_LEVEL), then command-line options.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

TAG_KEY = re.compile(r"^[A-Za-z0-9_.-]{1,255}$")
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")


class ConfigError(ValueError):
    """A setting is missing or invalid."""


def snake_case(name: str) -> str:
    """databricksHost -> databricks_host; snake_case names are returned unchanged."""
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name).lower()


def _section(raw: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    value = raw.get(name)
    if not isinstance(value, Mapping):
        raise ConfigError(f"platform.yaml: section '{name}' is missing")
    return value


def _required(section: Mapping[str, Any], key: str, where: str) -> Any:
    if section.get(key) in (None, ""):
        raise ConfigError(f"{where}: '{key}' is missing")
    return section[key]


@dataclass(frozen=True)
class Environment:
    """Where to publish. Built from environments/<name>.yml plus DP_<NAME> overrides."""

    name: str
    databricks_host: str
    warehouse_id: str
    ontos_url: str
    contract_server: str = "dev"
    service_connection: str = ""
    workspace_id: str = ""
    # Every other value in the file (setup-only values), by snake_case name.
    extra: Mapping[str, str] = field(default_factory=dict)

    # What the pipeline commands need. `dpi config get` needs none of them, so the setup
    # scripts can run before ontosUrl exists; they check each value they read instead.
    REQUIRED = ("databricks_host", "warehouse_id", "ontos_url")
    PLACEHOLDER = re.compile(r"^<.*>$")   # a template value nobody has filled in counts as unset

    @property
    def workspace_url(self) -> str:
        host = self.databricks_host.removeprefix("https://").removeprefix("http://").rstrip("/")
        return f"https://{host}"

    @classmethod
    def from_file(cls, path: Path, overrides: Mapping[str, str] | None = None,
                  required: Sequence[str] = REQUIRED) -> Environment:
        overrides = os.environ if overrides is None else overrides
        if not path.is_file():
            raise ConfigError(f"no environment file {path}")
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        variables = raw.get("variables")
        if not isinstance(variables, Mapping):
            raise ConfigError(f"{path.name}: expected a 'variables' mapping (name: value)")
        values = {snake_case(str(k)): "" if v is None or cls.PLACEHOLDER.match(str(v)) else str(v)
                  for k, v in variables.items()}
        known = {f.name for f in fields(cls)} - {"name", "extra"}
        for key in set(values) | known:  # an override may also set a value the file leaves out
            if f"DP_{key.upper()}" in overrides:
                values[key] = overrides[f"DP_{key.upper()}"]
        missing = [k for k in required if not values.get(k)]
        if missing:
            raise ConfigError(f"{path.name}: missing or still a <placeholder>: {', '.join(missing)}")
        host = values.get("databricks_host", "")
        if "/" in host:
            raise ConfigError(f"{path.name}: databricksHost is a host name, without https:// or a path")
        return cls(name=path.stem, extra={k: v for k, v in values.items() if k not in known},
                   **{k: v for k, v in values.items() if k in known})

    def __getitem__(self, key: str) -> str:
        """Any value by its file name or snake_case name, for example env['ontosAppName'].
        Also `workspaceUrl`, the host as an https URL."""
        key = snake_case(key)
        if key in self.extra:
            return self.extra[key]
        if key in {f.name for f in fields(self)} - {"extra"} | {"workspace_url"}:
            return getattr(self, key)
        raise KeyError(key)


@dataclass(frozen=True)
class TagPolicy:
    """Governed tags set on every published table."""

    prefix: str
    keys: Mapping[str, str]
    tier_values: Mapping[int, str]
    status_values: Mapping[str, str]
    domain_values: tuple[str, ...]
    sensitivity_values: tuple[str, ...]

    LOGICAL = ("tier", "domain", "status", "sensitivity")

    def __post_init__(self):
        missing = [k for k in self.LOGICAL if k not in self.keys]
        if missing:
            raise ConfigError(f"tags.keys: missing {', '.join(missing)}")
        bad = [self.key(k) for k in self.LOGICAL if not TAG_KEY.match(self.key(k))]
        if bad:
            raise ConfigError(f"tags: invalid tag keys {bad}")

    def key(self, logical: str) -> str:
        """Full tag key, for example key('tier') -> 'dp_tier'."""
        return f"{self.prefix}{self.keys[logical]}"

    def allowed(self, logical: str) -> list[str]:
        """Values a governed tag policy should allow."""
        return {
            "tier": sorted(set(self.tier_values.values())),
            "domain": list(self.domain_values),
            "status": sorted(set(self.status_values.values())),
            "sensitivity": list(self.sensitivity_values),
        }[logical]

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> TagPolicy:
        return cls(prefix=str(raw.get("prefix") or ""),
                   keys=dict(_required(raw, "keys", "tags")),
                   tier_values={int(k): str(v) for k, v in _required(raw, "tier_values", "tags").items()},
                   status_values={str(k): str(v) for k, v in _required(raw, "status_values", "tags").items()},
                   domain_values=tuple(_required(raw, "domain_values", "tags")),
                   sensitivity_values=tuple(_required(raw, "sensitivity_values", "tags")))


@dataclass(frozen=True)
class ContractFields:
    """Where the publish commands find things in and next to a contract."""

    tier_property: str
    sensitivity_property: str
    table_property_prefix: str
    access_file_suffix: str
    mandatory_schema: Path

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any], base: Path) -> ContractFields:
        where = "contract"
        return cls(tier_property=_required(raw, "tier_property", where),
                   sensitivity_property=_required(raw, "sensitivity_property", where),
                   table_property_prefix=_required(raw, "table_property_prefix", where),
                   access_file_suffix=_required(raw, "access_file_suffix", where),
                   mandatory_schema=(base / _required(raw, "mandatory_schema", where)).resolve())


@dataclass(frozen=True)
class GrantPolicy:
    revoke_unlisted: bool = True
    parent_use: bool = True

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> GrantPolicy:
        return cls(revoke_unlisted=bool(raw.get("revoke_unlisted", True)),
                   parent_use=bool(raw.get("parent_use", True)))


@dataclass(frozen=True)
class PublishPolicy:
    """How publish-uc treats objects that are not built yet, and the certification badge."""

    # A contract in one of these statuses may name objects that do not exist yet.
    pending_statuses: frozenset[str] = frozenset({"draft", "proposed", "under_review", "approved"})
    # Unity Catalog system tag behind the Certified and Deprecated badges.
    certification_tag: str = "system.certification_status"

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> PublishPolicy:
        default = cls()
        return cls(pending_statuses=frozenset(raw.get("pending_statuses", default.pending_statuses)),
                   certification_tag=str(raw.get("certification_tag") or default.certification_tag))


@dataclass(frozen=True)
class PublisherRole:
    name: str
    description: str
    group: str
    feature_permissions: Mapping[str, str]
    approval_privileges: Mapping[str, bool] = field(default_factory=dict)

    def as_ontos_role(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.description, "assigned_groups": [self.group],
                "feature_permissions": dict(self.feature_permissions),
                "approval_privileges": dict(self.approval_privileges)}


@dataclass(frozen=True)
class OntosPolicy:
    source_id_property: str
    quality_source: str
    quality_dimensions: frozenset[str]
    request_timeout_seconds: float
    publisher_role: PublisherRole
    role_groups: Mapping[str, tuple[str, ...]]

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> OntosPolicy:
        roles = _required(raw, "roles", "ontos")
        publisher = _required(roles, "publisher", "ontos.roles")
        return cls(source_id_property=_required(raw, "source_id_property", "ontos"),
                   quality_source=str(raw.get("quality_source") or "external"),
                   quality_dimensions=frozenset(_required(raw, "quality_dimensions", "ontos")),
                   request_timeout_seconds=float(raw.get("request_timeout_seconds") or 60),
                   publisher_role=PublisherRole(
                       name=_required(publisher, "name", "ontos.roles.publisher"),
                       description=str(publisher.get("description") or ""),
                       group=_required(publisher, "group", "ontos.roles.publisher"),
                       feature_permissions=dict(_required(publisher, "feature_permissions",
                                                          "ontos.roles.publisher")),
                       approval_privileges={str(k): bool(v) for k, v in
                                            (publisher.get("approval_privileges") or {}).items()}),
                   role_groups={str(k): tuple(v or ()) for k, v in (roles.get("groups") or {}).items()})


@dataclass(frozen=True)
class SetupPolicy:
    groups: tuple[str, ...] = ()


@dataclass(frozen=True)
class Settings:
    """Everything the commands need. Build it with Settings.load(); tests build it directly."""

    tags: TagPolicy
    contract: ContractFields
    grants: GrantPolicy
    ontos: OntosPolicy
    setup: SetupPolicy
    publish: PublishPolicy = field(default_factory=PublishPolicy)
    log_level: str = "INFO"
    environment: Environment | None = None
    config_dir: Path | None = None

    @property
    def env(self) -> Environment:
        """The selected environment; an error for commands that need one when none was given."""
        if self.environment is None:
            raise ConfigError("no environment selected: pass --environment or set DP_ENVIRONMENT")
        return self.environment

    @classmethod
    def load(cls, config_dir: str | Path | None = None, environment: str | None = None,
             overrides: Mapping[str, str] | None = None,
             required: Sequence[str] = Environment.REQUIRED) -> Settings:
        """`required`: environment values that must be set, Environment.REQUIRED by default."""
        overrides = os.environ if overrides is None else overrides
        config_dir = Path(config_dir or overrides.get("DP_CONFIG_DIR") or "config").resolve()
        platform_file = config_dir / "platform.yaml"
        if not platform_file.is_file():
            raise ConfigError(f"no platform file {platform_file} (set --config-dir or DP_CONFIG_DIR)")
        raw = yaml.safe_load(platform_file.read_text(encoding="utf-8")) or {}
        level = str(overrides.get("DP_LOG_LEVEL") or (raw.get("logging") or {}).get("level") or "INFO").upper()
        if level not in LOG_LEVELS:
            raise ConfigError(f"logging.level: '{level}' is not one of {', '.join(LOG_LEVELS)}")
        environment = environment or overrides.get("DP_ENVIRONMENT")
        return cls(tags=TagPolicy.from_dict(_section(raw, "tags")),
                   contract=ContractFields.from_dict(_section(raw, "contract"), config_dir),
                   grants=GrantPolicy.from_dict(raw.get("grants") or {}),
                   ontos=OntosPolicy.from_dict(_section(raw, "ontos")),
                   setup=SetupPolicy(groups=tuple((raw.get("setup") or {}).get("groups") or ())),
                   publish=PublishPolicy.from_dict(raw.get("publish") or {}),
                   log_level=level,
                   environment=Environment.from_file(config_dir / "environments" / f"{environment}.yml",
                                                     overrides, required) if environment else None,
                   config_dir=config_dir)

    def get(self, dotted: str) -> Any:
        """A value by dotted path, for the setup scripts: get('env.warehouse_id'), get('tags.prefix').
        An environment value must be set: empty or a <placeholder> is an error, not ''."""
        node: Any = self
        for part in dotted.split("."):
            if part == "env":
                node = self.env
            elif isinstance(node, Environment):
                try:
                    node = node[part]
                except KeyError:
                    raise ConfigError(f"no setting '{dotted}' in {node.name}.yml") from None
                if not node:
                    raise ConfigError(f"'{dotted}' is empty or still a <placeholder> in {self.env.name}.yml")
            elif is_dataclass(node) and hasattr(node, snake_case(part)):
                node = getattr(node, snake_case(part))
            elif isinstance(node, Mapping) and part in node:
                node = node[part]
            elif isinstance(node, Mapping) and part.isdigit() and int(part) in node:
                node = node[int(part)]
            else:
                raise ConfigError(f"no setting '{dotted}'")
        return node
