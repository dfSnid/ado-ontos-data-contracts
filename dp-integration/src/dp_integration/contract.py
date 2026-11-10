"""ODCS 3.1.0 data contracts and their access files, as the publish commands see them."""
from __future__ import annotations

import functools
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Mapping

import yaml


class ContractError(ValueError):
    """The contract or its access file cannot be published as written."""


def load_yaml(path: Path) -> Mapping[str, Any]:
    """A YAML file that must hold a mapping. Bad YAML or another shape is a ContractError."""
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise ContractError(f"{path.name}: not valid YAML: {e}") from e
    if document is None:
        return {}
    if not isinstance(document, Mapping):
        raise ContractError(f"{path.name}: expected a mapping, found {type(document).__name__}")
    return document


@functools.total_ordering
class Version:
    """A contract version, ordered part by part: 1.10.0 > 1.9.0 > 1.9.0-rc2 > 1.9.0-rc1.

    A pre-release (anything after the first "-") sorts before its release, as in SemVer."""

    __slots__ = ("text", "_key")

    def __init__(self, text: Any):
        self.text = str(text)
        release, dash, pre = self.text.partition("-")
        # A release ranks above any pre-release of it: (1,) > (0, ...).
        self._key = (self._parts(release), (0, self._parts(pre)) if dash else (1,))

    @staticmethod
    def _parts(text: str) -> tuple:
        # Numbers sort before words, so a comparison never mixes int and str.
        return tuple((0, int(p), "") if p.isdigit() else (1, 0, p) for p in text.split("."))

    def __eq__(self, other: object) -> bool:
        if isinstance(other, (str, int, float)):
            other = Version(other)
        return isinstance(other, Version) and self._key == other._key

    def __lt__(self, other: Version | str) -> bool:
        return self._key < (other if isinstance(other, Version) else Version(other))._key

    def __hash__(self) -> int:
        return hash(self._key)

    def __str__(self) -> str:
        return self.text

    def __repr__(self) -> str:
        return f"Version({self.text!r})"


@dataclass(frozen=True)
class Column:
    name: str
    description: str | None = None


@dataclass(frozen=True)
class ContractTable:
    """One schema object of the contract (a table, view or metric view) and its documented
    columns. For a metric view the columns are its dimensions and measures."""

    name: str
    physical_name: str
    description: str | None
    columns: Mapping[str, Column]
    kind: str = "table"

    # ODCS physicalType of a schema object -> kind. Anything else is a table.
    KINDS = {"view": "view", "materialized_view": "materialized_view", "materialized view": "materialized_view",
             "metric_view": "metric_view", "metric view": "metric_view"}

    @property
    def is_view(self) -> bool:
        return self.kind != "table"

    @property
    def keyword(self) -> str:
        """The object keyword in ALTER statements."""
        return {"table": "TABLE", "materialized_view": "MATERIALIZED VIEW"}.get(self.kind, "VIEW")

    @property
    def comment_keyword(self) -> str:
        """The object keyword in COMMENT ON, which has no MATERIALIZED VIEW form."""
        return "TABLE" if self.kind == "materialized_view" else self.keyword

    @property
    def pipeline_owned(self) -> bool:
        """A Lakeflow pipeline dataset: its table properties come from the pipeline definition."""
        return self.kind == "materialized_view"

    def full_name(self, server: Mapping[str, Any]) -> str:
        return f"{server['catalog']}.{server['schema']}.{self.physical_name}"


@dataclass(frozen=True)
class Grant:
    principal: str
    privileges: frozenset[str]
    objects: frozenset[str]


class AccessPolicy:
    """The grants in <contract>.access.yaml. Iterate it for the grants; it is falsy when empty."""

    def __init__(self, contract_id: str | None, grants: list[Grant], path: Path | None = None):
        self.contract_id, self.path = contract_id, path
        self._grants = list(grants)

    @classmethod
    def from_file(cls, path: Path) -> AccessPolicy:
        raw = load_yaml(path)
        try:
            grants = [Grant(principal=g["principal"], privileges=frozenset(g["privileges"]),
                            objects=frozenset(g["objects"])) for g in raw.get("grants") or []]
        except (KeyError, TypeError) as e:
            raise ContractError(f"{path.name}: each grant needs principal, privileges and objects ({e})") from e
        return cls(raw.get("contract"), grants, path)

    @classmethod
    def empty(cls) -> AccessPolicy:
        return cls(None, [])

    def for_table(self, table: str) -> dict[str, set[str]]:
        """{principal: privileges} that should be granted directly on one table."""
        wanted: dict[str, set[str]] = {}
        for grant in self._grants:
            if table in grant.objects:
                wanted.setdefault(grant.principal, set()).update(grant.privileges)
        return wanted

    @property
    def principals(self) -> set[str]:
        return {g.principal for g in self._grants}

    def __iter__(self) -> Iterator[Grant]:
        return iter(self._grants)

    def __len__(self) -> int:
        return len(self._grants)

    def __repr__(self) -> str:
        return f"AccessPolicy({self.contract_id!r}, {len(self)} grants)"


class DataContract:
    """An ODCS contract file. `document` is the parsed YAML, exactly as written."""

    def __init__(self, document: Mapping[str, Any], path: Path | None = None,
                 access: AccessPolicy | None = None):
        for key in ("id", "version", "status"):
            if not document.get(key):
                raise ContractError(f"'{key}' is missing")
        self.document, self.path = document, path
        self.access = access if access is not None else AccessPolicy.empty()
        if self.access.contract_id is not None and self.access.contract_id != self.id:
            raise ContractError(f"access file is for '{self.access.contract_id}', not '{self.id}'")

    @classmethod
    def from_file(cls, path: str | Path, access_suffix: str = ".access.yaml") -> DataContract:
        path = Path(path)
        document = load_yaml(path)
        access_path = path.with_name(path.name.replace(".odcs.yaml", access_suffix))
        access = AccessPolicy.from_file(access_path) if access_path != path and access_path.is_file() else None
        return cls(document, path, access)

    @property
    def id(self) -> str:
        return str(self.document.get("id") or "")

    @property
    def name(self) -> str:
        return str(self.document.get("name") or self.id)

    @property
    def version(self) -> Version:
        return Version(self.document["version"])

    @property
    def status(self) -> str:
        return str(self.document["status"])

    @property
    def domain(self) -> str | None:
        return self.document.get("domain")

    @property
    def data_product(self) -> str | None:
        return self.document.get("dataProduct")

    @property
    def custom_properties(self) -> dict[str, Any]:
        return {p["property"]: p.get("value") for p in self.document.get("customProperties") or []}

    def custom(self, name: str) -> Any:
        """A custom property value; an error if the contract does not set it."""
        props = self.custom_properties
        if name not in props:
            raise ContractError(f"custom property '{name}' is missing")
        return props[name]

    def has_server(self, name: str) -> bool:
        """A contract lists the environments it is deployed to as servers."""
        return any(s.get("server") == name for s in self.document.get("servers") or [])

    def server(self, name: str) -> Mapping[str, Any]:
        """The Databricks server entry called `name`."""
        for server in self.document.get("servers") or []:
            if server.get("server") == name:
                if server.get("type") != "databricks":
                    raise ContractError(f"server '{name}' is type '{server.get('type')}', not databricks")
                return server
        raise ContractError(f"no server named '{name}'")

    @property
    def tables(self) -> list[ContractTable]:
        return [ContractTable(name=obj["name"], physical_name=obj.get("physicalName", obj["name"]),
                              description=obj.get("description"),
                              columns={p["name"]: Column(p["name"], p.get("description"))
                                       for p in obj.get("properties") or []},
                              kind=ContractTable.KINDS.get(str(obj.get("physicalType") or "table").lower(), "table"))
                for obj in self.document.get("schema") or []]

    def __str__(self) -> str:
        return f"{self.id} {self.version} ({self.status})"

    def __repr__(self) -> str:
        return f"DataContract({self.id!r}, version={self.version.text!r}, path={str(self.path)!r})"
