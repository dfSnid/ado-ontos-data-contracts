"""The domain registry (domains/domains.yaml in the contracts repository) and its Ontos sync.

The registry is the one list of business domains. Every contract and product names one of them,
and each domain names the Ontos team that owns its products. The sync creates or updates the
Ontos domains (parents first) and their teams, and adds the listed groups to each team. It adds
only: it never deletes a domain, a team or a team member, because other entities may use them.

Unity Catalog catalogs and the allowed values of the domain tag are not created here: that
needs metastore and account rights the publisher identity does not hold. The platform team
creates them (Terraform); check-products checks that the tag allows every registered domain.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from .contract import ContractError, load_yaml
from .ontos import OntosClient

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Team:
    name: str
    title: str | None
    groups: tuple[str, ...]


@dataclass(frozen=True)
class Domain:
    name: str
    description: str | None
    parent: str | None
    team: Team | None


class DomainRegistry:
    def __init__(self, domains: list[Domain], path: Path | None = None):
        self.path = path
        self._domains = {d.name: d for d in domains}
        if len(self._domains) != len(domains):
            raise ContractError(f"{self._where}: a domain is listed twice")
        for d in domains:
            if d.parent and d.parent not in self._domains:
                raise ContractError(f"{self._where}: domain '{d.name}' has unknown parent '{d.parent}'")
        self.ordered()  # a parent cycle is an error here, not halfway through a sync

    @property
    def _where(self) -> str:
        return self.path.name if self.path else "domain registry"

    @classmethod
    def from_file(cls, path: str | Path) -> DomainRegistry:
        path = Path(path)
        raw = load_yaml(path)
        try:
            domains = []
            for d in raw.get("domains") or []:
                team = d.get("team")
                domains.append(Domain(
                    name=str(d["name"]), description=d.get("description"), parent=d.get("parent"),
                    team=Team(name=str(team["name"]), title=team.get("title"),
                              groups=tuple(team.get("groups") or ())) if team else None))
        except (KeyError, TypeError) as e:
            raise ContractError(f"{path.name}: each domain needs a name, each team a name ({e})") from e
        return cls(domains, path)

    @property
    def names(self) -> list[str]:
        return list(self._domains)

    def get(self, name: str) -> Domain | None:
        return self._domains.get(name)

    def ordered(self) -> list[Domain]:
        """Parents before children."""
        done: dict[str, Domain] = {}

        def visit(d: Domain, seen: tuple[str, ...]) -> None:
            if d.name in seen:
                raise ContractError(f"{self._where}: domain parents form a cycle: {' > '.join(seen + (d.name,))}")
            if d.parent and d.parent not in done:
                visit(self._domains[d.parent], seen + (d.name,))
            done.setdefault(d.name, d)

        for d in self._domains.values():
            visit(d, ())
        return list(done.values())

    def __iter__(self) -> Iterator[Domain]:
        return iter(self._domains.values())

    def __len__(self) -> int:
        return len(self._domains)

    def __repr__(self) -> str:
        return f"DomainRegistry({self.names})"


class OntosDomainSync:
    def __init__(self, client: OntosClient):
        self.client = client

    def sync(self, registry: DomainRegistry) -> dict[str, str]:
        """Make Ontos hold every registered domain and team; returns {domain name: Ontos id}."""
        existing = {d["name"]: d for d in self.client.list_domains()}
        teams = {t["name"]: t for t in self.client.list_teams()}
        ids: dict[str, str] = {}
        for domain in registry.ordered():
            parent_id = ids.get(domain.parent) if domain.parent else None
            record = existing.get(domain.name)
            if record is None:
                record = self.client.create_domain({"name": domain.name, "description": domain.description,
                                                    "parent_id": parent_id})
                log.info("domain %s created (%s)", domain.name, record["id"])
            elif (record.get("description") or None) != domain.description or record.get("parent_id") != parent_id:
                record = self.client.update_domain(record["id"], {"description": domain.description,
                                                                  "parent_id": parent_id})
                log.info("domain %s updated (%s)", domain.name, record["id"])
            else:
                log.info("domain %s up to date (%s)", domain.name, record["id"])
            ids[domain.name] = str(record["id"])
            if domain.team:
                self._sync_team(domain.team, ids[domain.name], teams.get(domain.team.name))
        return ids

    def _sync_team(self, team: Team, domain_id: str, record: dict | None) -> None:
        if record is None:
            record = self.client.create_team({"name": team.name, "title": team.title, "domain_id": domain_id})
            log.info("  team %s created (%s)", team.name, record["id"])
        elif record.get("domain_id") != domain_id or (team.title and record.get("title") != team.title):
            self.client.update_team(record["id"], {"title": team.title or record.get("title"), "domain_id": domain_id})
            log.info("  team %s updated", team.name)
        members = {m.get("member_identifier") for m in record.get("members") or [] if m.get("member_type") == "group"}
        for group in team.groups:
            if group not in members:
                self.client.add_team_member(record["id"], {"member_type": "group", "member_identifier": group})
                log.info("  team %s: group %s added", team.name, group)


def team_ids(client: OntosClient) -> dict[str, str]:
    """{team name: Ontos team id}."""
    return {t["name"]: str(t["id"]) for t in client.list_teams()}
