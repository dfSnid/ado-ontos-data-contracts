"""Data product files (ODPS v1.0.0, products/<domain>/<name>.odps.yaml) and their Ontos sync.

A product file lists the product's output ports. Each port names a contract by its ODCS id and
version, so the product moves with its contracts. The file's status is where the product
should be; the sync walks Ontos there one allowed step at a time, behind these gates:

- under_review or later: every port contract is registered in Ontos;
- approved: every port contract is at least approved;
- active: every port contract is active and its Unity Catalog objects exist.

Certification (custom property certificationLevel, an Ontos level name) and publication scope
(publicationScope) need the product active in Ontos. The sync certifies or decertifies to match
the file and sets the Unity Catalog certification tag (the Certified badge) on the port objects;
a deprecated product gets the Deprecated badge instead.

Ontos facts this relies on (v1.0.2): create ignores the file's id, so the product is found by the
sourceId custom property and version; a product without an owner team is a personal draft only
its creator can see, so the owner team is the domain's team; PUT applies the body's status with
no transition check, so the sync sends the current status and moves it with change-status;
PUT on a non-draft product rejects a breaking change (409) unless a new version is made.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from .config import OntosPolicy
from .contract import ContractError, DataContract, Version, load_yaml
from .domains import DomainRegistry
from .lifecycle import DATA_PRODUCT_TRANSITIONS, ORDER, at_least, walk
from .ontos import OntosClient, OntosError
from .ontos_sync import OntosContractSync

log = logging.getLogger(__name__)

SCOPES = ("none", "domain", "organization", "external")


class GateError(RuntimeError):
    """The product file asks for a status its contracts or objects do not support yet."""


@dataclass(frozen=True)
class OutputPort:
    name: str
    version: str
    contract_id: str
    description: str | None = None
    asset_type: str | None = None
    asset_identifier: str | None = None


class DataProductFile:
    def __init__(self, document: Mapping[str, Any], path: Path | None = None):
        self.document, self.path = document, path
        missing = [k for k in ("id", "name", "version", "status", "domain") if not document.get(k)]
        if missing:
            raise ContractError(f"{self._where}: missing {', '.join(missing)}")
        if self.status not in ORDER:
            raise ContractError(f"{self._where}: unknown status '{self.status}'")
        try:
            self.ports = [OutputPort(name=str(p["name"]), version=str(p["version"]),
                                     contract_id=str(p["contractId"]), description=p.get("description"),
                                     asset_type=p.get("assetType"), asset_identifier=p.get("assetIdentifier"))
                          for p in document.get("outputPorts") or []]
        except (KeyError, TypeError) as e:
            raise ContractError(f"{self._where}: each output port needs name, version and contractId ({e})") from e
        if self.publication_scope not in SCOPES:
            raise ContractError(f"{self._where}: publicationScope must be one of {', '.join(SCOPES)}")

    @classmethod
    def from_file(cls, path: str | Path) -> DataProductFile:
        path = Path(path)
        return cls(load_yaml(path), path)

    @property
    def _where(self) -> str:
        return self.path.name if self.path else "data product"

    id = property(lambda self: str(self.document["id"]))
    name = property(lambda self: str(self.document["name"]))
    version = property(lambda self: Version(self.document["version"]))
    status = property(lambda self: str(self.document["status"]))
    domain = property(lambda self: str(self.document["domain"]))

    @property
    def custom_properties(self) -> dict[str, Any]:
        return {p.get("property"): p.get("value") for p in self.document.get("customProperties") or []}

    @property
    def certification_level(self) -> str | None:
        return self.custom_properties.get("certificationLevel") or None

    @property
    def publication_scope(self) -> str:
        return str(self.custom_properties.get("publicationScope") or "none")

    def __str__(self) -> str:
        return f"{self.id} {self.version} ({self.status})"

    def __repr__(self) -> str:
        return f"DataProductFile({self})"


def check_registry(registry: DomainRegistry, domain_values: Iterable[str]) -> list[str]:
    """Registered domains the Unity Catalog domain tag does not allow (the platform team adds them)."""
    allowed = set(domain_values)
    return [f"domain '{n}' is registered but not an allowed value of the domain tag"
            for n in registry.names if allowed and n not in allowed]


def check_product(product: DataProductFile, contracts: Mapping[str, DataContract],
                  registry: DomainRegistry | None, domain_values: Iterable[str] = ()) -> list[str]:
    """Problems with a product file found without calling Ontos (the blocking CI check)."""
    problems = []
    domain_values = set(domain_values)
    if registry is not None and registry.get(product.domain) is None:
        problems.append(f"domain '{product.domain}' is not in {registry.path.name if registry.path else 'the registry'}")
    if domain_values and product.domain not in domain_values:
        problems.append(f"domain '{product.domain}' is not an allowed value of the domain tag")
    if not product.ports:
        problems.append("no output ports")
    for port in product.ports:
        contract = contracts.get(port.contract_id)
        if contract is None:
            problems.append(f"port '{port.name}': no contract with id '{port.contract_id}' in the repository")
            continue
        if Version(port.version) != contract.version:
            problems.append(f"port '{port.name}': version {port.version} but contract {contract.id} is {contract.version}")
        if contract.domain and contract.domain != product.domain:
            problems.append(f"port '{port.name}': contract domain '{contract.domain}' is not the product's")
        for floor in ("approved", "active"):
            if at_least(product.status, floor) and product.status != "deprecated" \
                    and not at_least(contract.status, floor):
                problems.append(f"status {product.status} needs contract {contract.id} {floor}, it is {contract.status}")
                break
    if product.certification_level and product.status != "active":
        problems.append(f"certificationLevel needs status active, it is {product.status}")
    if product.publication_scope != "none" and product.status != "active":
        problems.append(f"publicationScope {product.publication_scope} needs status active, it is {product.status}")
    return problems


class OntosProductSync:
    def __init__(self, client: OntosClient, policy: OntosPolicy, uc=None):
        """`uc` is a UnityCatalogPublisher for the environment; without it the active gate does
        not check that objects exist and no certification tag is set."""
        self.client, self.policy, self.uc = client, policy, uc
        self.contract_sync = OntosContractSync(client, policy)

    def sync(self, product: DataProductFile, contracts: Mapping[str, DataContract],
             owner_team_id: str | None) -> dict:
        """Make Ontos match the product file; returns the Ontos record of this version."""
        log.info("== %s: %s", product.path, product)
        port_contracts = {p.name: contracts.get(p.contract_id) for p in product.ports}
        registered = {name: self._contract_record(c) for name, c in port_contracts.items() if c}
        body = self._body(product, registered, owner_team_id)

        records = self.records_for(product)
        same = next((r for r in records if Version(r["version"]) == product.version), None)
        if same is None:
            older = [r for r in records if Version(r["version"]) < product.version]
            if older:
                parent = max(older, key=lambda r: Version(r["version"]))
                same = self.client.new_product_version(parent["id"], str(product.version))
                log.info("  version %s created from %s (%s)", product.version, parent["version"], same["id"])
            else:
                same = self.client.create_product(dict(body, status="draft"))
                log.info("  created %s (draft)", same["id"])
        record = self._update(same, body)

        self._gate(product, port_contracts, registered)
        for status in walk(record["status"], product.status, DATA_PRODUCT_TRANSITIONS):
            self.client.change_product_status(record["id"], status)
            log.info("  status %s -> %s", record["status"], status)
            record["status"] = status

        self._certify(product, record)
        self._badge(product, port_contracts.values())
        if record["status"] == "active" and (record.get("publication_scope") or "none") != product.publication_scope:
            self.client.set_publication_scope(record["id"], product.publication_scope)
            log.info("  publication scope %s", product.publication_scope)
            record["publication_scope"] = product.publication_scope
        return record

    def records_for(self, product: DataProductFile) -> list[dict]:
        """Every Ontos record of this product, any version, matched on the source id property."""
        key = self.policy.source_id_property
        found = []
        for record in self.client.list_products():
            props = record.get("custom_properties") or record.get("customProperties") or []
            if isinstance(props, list):
                props = {p.get("property"): p.get("value") for p in props}
            if props.get(key) == product.id:
                found.append(record)
        return found

    def _contract_record(self, contract: DataContract) -> dict | None:
        return next((r for r in self.contract_sync.records_for(contract)
                     if Version(r["version"]) == contract.version), None)

    def _body(self, product: DataProductFile, registered: Mapping[str, dict | None],
              owner_team_id: str | None) -> dict:
        doc = product.document
        props = [p for p in doc.get("customProperties") or [] if p.get("property") != self.policy.source_id_property]
        ports = []
        for port in product.ports:
            out = {"name": port.name, "version": port.version, "description": port.description,
                   "assetType": port.asset_type, "assetIdentifier": port.asset_identifier}
            if registered.get(port.name):
                out["contractId"] = str(registered[port.name]["id"])
            ports.append({k: v for k, v in out.items() if v is not None})
        body = {"apiVersion": doc.get("apiVersion", "v1.0.0"), "kind": "DataProduct", "id": product.id,
                "name": product.name, "version": str(product.version), "domain": product.domain,
                "description": doc.get("description"), "outputPorts": ports,
                "customProperties": props + [{"property": self.policy.source_id_property, "value": product.id}]}
        if owner_team_id:
            body["owner_team_id"] = owner_team_id
        return body

    def _update(self, record: dict, body: dict) -> dict:
        """PUT only what differs, with the record's own id and current status."""
        if not _differs(record, body):
            return dict(record)
        update = dict(body, id=record["id"], status=record["status"])
        try:
            updated = self.client.update_product(record["id"], update)
        except OntosError as e:
            if e.status == 409:
                raise OntosError(f"Ontos calls this a breaking change to {record['version']}; "
                                 f"raise the product version in the file", 409) from e
            raise
        log.info("  updated %s", record["id"])
        return dict(updated, status=record["status"])

    def _gate(self, product: DataProductFile, port_contracts: Mapping[str, DataContract | None],
              registered: Mapping[str, dict | None]) -> None:
        target, problems = product.status, []
        if target == "deprecated" or target == "retired" or not at_least(target, "under_review"):
            return
        for port in product.ports:
            contract, record = port_contracts.get(port.name), registered.get(port.name)
            if contract is None or record is None:
                problems.append(f"port '{port.name}': contract {port.contract_id} {port.version} is not in Ontos")
                continue
            for floor in ("approved", "active"):
                if at_least(target, floor) and not at_least(record["status"], floor):
                    problems.append(f"port '{port.name}': contract is {record['status']} in Ontos, needs {floor}")
                    break
            if target == "active" and self.uc is not None and contract.has_server(self.uc.server_name):
                server = contract.server(self.uc.server_name)
                missing = [t.full_name(server) for t in contract.tables if self.uc.table_info(t.full_name(server)) is None]
                if missing:
                    problems.append(f"port '{port.name}': not built yet: {', '.join(missing)}")
        if problems:
            raise GateError(f"{product} cannot move to {target}: " + "; ".join(problems))

    def _certify(self, product: DataProductFile, record: dict) -> None:
        wanted = product.certification_level
        levels = {str(lv["name"]): int(lv["level_order"]) for lv in self.client.list_certification_levels()}
        if wanted and wanted not in levels:
            raise GateError(f"unknown certification level '{wanted}' (Ontos has {', '.join(levels)})")
        target, current = levels.get(wanted) if wanted else None, record.get("certification_level")
        if target == current:
            return
        if target is None:
            self.client.decertify_product(record["id"], f"certificationLevel removed in {product.path or 'the file'}")
            log.info("  decertified")
        elif record["status"] != "active":
            raise GateError(f"certification {wanted} needs status active, it is {record['status']}")
        else:
            self.client.certify_product(record["id"], target, f"From {product.path or 'the product file'}")
            log.info("  certified %s", wanted)
        record["certification_level"] = target

    def _badge(self, product: DataProductFile, contracts: Iterable[DataContract | None]) -> None:
        if self.uc is None:
            return
        value = "deprecated" if product.status == "deprecated" else \
            "certified" if product.certification_level and product.status == "active" else None
        for contract in contracts:
            if contract is not None:
                self.uc.set_certification(contract, value)


def _differs(record: dict, body: dict) -> bool:
    """True when the Ontos record (snake_case, as Ontos returns it) does not match the body."""
    def ports(items, contract_key):
        return sorted((p.get("name"), str(p.get("version")), p.get(contract_key) or None,
                       p.get("description") or None) for p in items or [])

    def props(items):
        return sorted((p.get("property"), str(p.get("value"))) for p in items or [])

    purpose = lambda d: (d or {}).get("purpose")  # noqa: E731
    return (record.get("name") != body["name"] or record.get("domain") != body["domain"]
            or purpose(record.get("description")) != purpose(body.get("description"))
            or (body.get("owner_team_id") and record.get("owner_team_id") != body["owner_team_id"])
            or ports(record.get("output_ports") or record.get("outputPorts"), "contract_id")
            != ports(body["outputPorts"], "contractId")
            or props(record.get("custom_properties") or record.get("customProperties")) != props(body["customProperties"]))
