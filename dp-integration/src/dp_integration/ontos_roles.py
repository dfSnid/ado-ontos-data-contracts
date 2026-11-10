"""Map platform groups onto Ontos roles (setup, run as an Ontos admin).

The publisher group gets its own role, with the feature permissions and approval privileges set
in platform.yaml (contracts, data domains, data products and teams, plus the PRODUCTS approval
privilege that certification needs). The publisher role is kept exactly as configured, so a new
permission in platform.yaml reaches Ontos on the next run. The people groups are added to the
default roles named in platform.yaml. Adds only, except that the publisher group is taken out
of every other role, so the publisher never holds more than its own role.
"""
from __future__ import annotations

import logging

from .config import OntosPolicy
from .ontos import OntosClient

log = logging.getLogger(__name__)


class OntosRoleMapper:
    def __init__(self, client: OntosClient, policy: OntosPolicy):
        self.client, self.policy = client, policy

    def apply(self, dry_run: bool = False) -> dict[str, list[str]]:
        """Returns {role name: assigned groups} after the change."""
        publisher = self.policy.publisher_role
        roles = self.client.list_roles()
        if not any(r["name"] == publisher.name for r in roles):
            log.info("create role %s", publisher.name)
            if not dry_run:
                self.client.create_role(publisher.as_ontos_role())
                roles = self.client.list_roles()
        result = {}
        for role in roles:
            groups = list(role.get("assigned_groups") or [])
            wanted = self.wanted_groups(role["name"], groups)
            update = dict(role, assigned_groups=wanted)
            if role["name"] == publisher.name:
                configured = publisher.as_ontos_role()
                for key in ("feature_permissions", "approval_privileges"):
                    current = {k: v for k, v in (role.get(key) or {}).items() if v not in (None, False, "None")}
                    if current != configured[key]:
                        log.info("role %s: %s %s -> %s", role["name"], key, current, configured[key])
                        update[key] = configured[key]
            if update != role:
                if wanted != groups:
                    log.info("role %s: %s -> %s", role["name"], groups, wanted)
                if not dry_run:
                    self.client.update_role(update)
            result[role["name"]] = wanted
            log.info("  %s: %s", role["name"], wanted)
        return result

    def wanted_groups(self, role: str, current: list[str]) -> list[str]:
        publisher = self.policy.publisher_role
        if role == publisher.name:
            return [publisher.group]
        wanted = [g for g in current if g != publisher.group]
        return wanted + [g for g in self.policy.role_groups.get(role, ()) if g not in wanted]
