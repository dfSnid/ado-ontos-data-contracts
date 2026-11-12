"""Register data contracts in Ontos.

The Data Steward's PR approval is the gate, so Ontos is the registration and display copy
only. A contract is found in Ontos by its id (kept as a custom property, sourceId by
default) and version:

- the first version is imported;
- a later version is created as the next version of the newest older one, so Ontos shows one
  contract with its history, then filled from the file;
- a version Ontos already has only gets its status brought in line with the file, one allowed
  step at a time (draft, proposed, approved, active), so a PR may skip steps.

Ontos v1.0.2 cannot set servers on update, so later versions show no servers. Unity Catalog
stays the source of truth for where the tables live.

Ontos shows a caller who is not an admin only the active and deprecated contracts, unless the
caller is in the contract's owner team. The owner team is the domain's team from the registry,
which holds the publisher group, so the publisher can find its own proposed and approved
contracts on the next run. A contract imported with no owner team is lost to the publisher once
it leaves draft, and the next run imports it again.
"""
from __future__ import annotations

import logging
from typing import Any, Mapping, NamedTuple

from .config import OntosPolicy
from .contract import DataContract, Version
from .lifecycle import DATA_CONTRACT_TRANSITIONS, walk
from .ontos import OntosClient, OntosError
from .quality import QualityResults

log = logging.getLogger(__name__)


class OwnerTeam(NamedTuple):
    name: str
    id: str


class OntosContractSync:
    def __init__(self, client: OntosClient, policy: OntosPolicy,
                 owner_teams: Mapping[str, OwnerTeam] | None = None):
        """`owner_teams` maps a domain name to the Ontos team that owns its contracts."""
        self.client, self.policy = client, policy
        self.owner_teams = dict(owner_teams or {})

    def owner_team(self, contract: DataContract) -> OwnerTeam | None:
        team = self.owner_teams.get(contract.domain) if contract.domain else None
        if team is None and self.owner_teams:
            log.warning("  no owner team for domain '%s': the publisher cannot find this contract "
                        "in Ontos once it leaves draft", contract.domain)
        return team

    def sync(self, contract: DataContract, quality: QualityResults | None = None) -> dict:
        """Make Ontos match the contract file; returns the Ontos record of this version."""
        log.info("== %s: %s", contract.path, contract)
        records = self.records_for(contract)
        same = next((r for r in records if Version(r["version"]) == contract.version), None)
        if same:
            record = self._align_status(same, contract)
        else:
            older = [r for r in records if Version(r["version"]) < contract.version]
            record = self._next_version(contract, max(older, key=lambda r: Version(r["version"]))) \
                if older else self._import(contract)
        for item in (quality or QualityResults()).for_contract(contract.id, contract.version):
            self.client.add_quality_item(record["id"], item.to_ontos(self.policy.quality_source))
            log.info("  quality %s", item)
        return record

    def records_for(self, contract: DataContract) -> list[dict]:
        """Every Ontos record of this contract, any version, matched on the source id property."""
        found = []
        for summary in self.client.list_contracts(include_history=True):
            if summary.get("name") != contract.name and summary.get("dataProduct") != contract.data_product:
                continue
            detail = self.client.get_contract(summary["id"])
            props = detail.get("customProperties") or {}
            if isinstance(props, list):  # ODCS list form; Ontos v1.0.2 returns a dict
                props = {p.get("property"): p.get("value") for p in props}
            if props.get(self.policy.source_id_property) == contract.id:
                found.append(detail)
        return found

    def _align_status(self, record: dict, contract: DataContract) -> dict:
        log.info("  Ontos already has version %s (%s, %s)", contract.version, record["id"], record["status"])
        for status in walk(record["status"], contract.status, DATA_CONTRACT_TRANSITIONS):
            self.client.change_status(record["id"], status)
            log.info("  status %s -> %s", record["status"], status)
            record = dict(record, status=status)
        return record

    def _import(self, contract: DataContract) -> dict:
        body, team = self.import_body(contract), self.owner_team(contract)
        if team:  # Ontos import takes the owner team by name
            body["owner"] = team.name
        record = self.client.import_odcs(body)
        log.info("  imported version %s as %s (%s)", record["version"], record["id"], record["status"])
        return record

    def _next_version(self, contract: DataContract, parent: dict) -> dict:
        # Ontos v1.0.2 sets version lineage only when it creates the version itself: PUT and
        # import ignore parentContractId, and /clone fails on contracts with a team. /versions
        # copies the header only and starts as a draft, so the draft is filled from the file.
        draft = self.client.new_version(parent["id"], str(contract.version))
        try:
            body, team = self.update_body(contract), self.owner_team(contract)
            if team:
                body["owner_team_id"] = team.id
            record = self.client.update_contract(draft["id"], body)
        except OntosError:
            # Without the source id the half-made draft is invisible to the next run, so remove it.
            self.client.delete_contract(draft["id"])
            raise
        log.info("  version %s created as %s (%s), next after %s (%s)",
                 record["version"], record["id"], record["status"], parent["version"], parent["id"])
        return record

    @staticmethod
    def import_body(contract: DataContract) -> dict[str, Any]:
        """The contract as written. Ontos import keeps the contract's own id as sourceId."""
        return dict(contract.document)

    def update_body(self, contract: DataContract) -> dict[str, Any]:
        """The parts of a contract that change between versions, as an Ontos update body."""
        doc = contract.document
        props = dict(contract.custom_properties, **{self.policy.source_id_property: contract.id})
        body = {"status": contract.status, "schema": doc.get("schema") or [], "customProperties": props}
        for key in ("name", "description", "dataProduct"):
            if key in doc:
                body[key] = doc[key]
        team = doc.get("team")
        if team:  # ODCS 3.1.0 team object; the Ontos update body takes the member list
            body["team"] = team.get("members", []) if isinstance(team, dict) else team
        return body
