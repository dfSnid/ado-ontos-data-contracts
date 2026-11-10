"""Publish a merged contract to Unity Catalog.

For every object in the contract (table, view, materialized view or metric view): the governed
tags, the object and column comments, and the contract id, version and commit as properties. A
materialized view built by a Lakeflow pipeline takes no properties from outside the pipeline,
so the pipeline sets the id and version itself and the publish only checks them. Then the grants in the
access file, applied declaratively, and USE CATALOG and USE SCHEMA on the parents for the same
principals. An object whose columns disagree with the contract is not touched, so a drifted
contract never publishes. For a metric view the columns are its dimensions and measures.

The contract is approved (G3) before the objects are built, so an object that does not exist yet
is "pending" while the contract status is one of publish.pending_statuses: logged, not failed.
Once the contract is active every object must exist. The bundle deploy triggers a publish run, so
the metadata lands as soon as the objects do.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable, Mapping

from databricks.sdk.errors import NotFound
from databricks.sdk.service.catalog import PermissionsChange, Privilege
from databricks.sdk.service.sql import StatementState

from .config import ContractFields, GrantPolicy, PublishPolicy, TagPolicy
from .contract import ContractError, ContractTable, DataContract

log = logging.getLogger(__name__)

SqlRunner = Callable[[str], None]

# Unity Catalog table_type of each contract object kind.
TABLE_TYPES = {"view": {"VIEW", "MATERIALIZED_VIEW"}, "materialized_view": {"MATERIALIZED_VIEW"},
               "metric_view": {"METRIC_VIEW"}}


def sql_literal(value: Any) -> str:
    return "'" + str(value).replace("\\", "\\\\").replace("'", "\\'") + "'"


def sql_ident(*parts: str) -> str:
    return ".".join("`" + p.replace("`", "``") + "`" for p in parts)


class StatementRunner:
    """Runs one SQL statement on a warehouse and waits for it. Call it like a function."""

    def __init__(self, ws, warehouse_id: str, dry_run: bool = False, poll_seconds: float = 2.0):
        self.ws, self.warehouse_id = ws, warehouse_id
        self.dry_run, self.poll_seconds = dry_run, poll_seconds

    def __call__(self, statement: str) -> None:
        log.info("SQL %s", statement)
        if self.dry_run:
            return
        self._execute(statement)

    def rows(self, statement: str) -> list[list[str | None]]:
        """A read-only query's rows (values as strings), run even in a dry run. Small results only."""
        log.debug("SQL %s", statement)
        r = self._execute(statement)
        return (r.result.data_array if r.result else None) or []

    def _execute(self, statement: str):
        r = self.ws.statement_execution.execute_statement(
            warehouse_id=self.warehouse_id, statement=statement, wait_timeout="50s")
        while r.status.state in (StatementState.PENDING, StatementState.RUNNING):
            time.sleep(self.poll_seconds)
            r = self.ws.statement_execution.get_statement(r.statement_id)
        if r.status.state != StatementState.SUCCEEDED:
            error = r.status.error.message if r.status.error else r.status.state
            raise RuntimeError(f"{statement}: {error}")
        return r

    def __repr__(self) -> str:
        return f"StatementRunner(warehouse_id={self.warehouse_id!r}, dry_run={self.dry_run})"


class UnityCatalogPublisher:
    """Applies a contract to Unity Catalog. `ws` is a databricks.sdk WorkspaceClient (or any
    object with the same tables and grants API) and `run_sql` runs one statement."""

    def __init__(self, ws, run_sql: SqlRunner, tags: TagPolicy, fields: ContractFields,
                 grants: GrantPolicy, server: str, commit: str | None = None, dry_run: bool = False,
                 policy: PublishPolicy | None = None):
        self.ws, self.run_sql = ws, run_sql
        self.tag_policy, self.fields, self.grant_policy = tags, fields, grants
        self.policy = policy or PublishPolicy()
        self.server_name, self.commit, self.dry_run = server, commit, dry_run

    def publish(self, contract: DataContract) -> bool:
        """True when every object was published or is pending; drift and an object missing
        from an active contract are logged against the contract file. A contract with no server
        for this environment is not deployed here, so there is nothing to publish."""
        if not contract.has_server(self.server_name):
            log.info("== %s: %s has no '%s' server, skipped", contract.path, contract, self.server_name)
            return True
        server = contract.server(self.server_name)
        tags, props = self.tags_for(contract), self.properties_for(contract)
        log.info("== %s: %s", contract.path, contract)
        ok = True
        for table in contract.tables:
            ok = self._publish_table(contract, table, table.full_name(server), tags, props) and ok
        if self.grant_policy.parent_use and contract.access.principals:
            catalog, schema = server["catalog"], f"{server['catalog']}.{server['schema']}"
            self.ensure_privilege("catalog", catalog, "USE_CATALOG", contract.access.principals)
            self.ensure_privilege("schema", schema, "USE_SCHEMA", contract.access.principals)
        return ok

    def tags_for(self, contract: DataContract) -> dict[str, str]:
        t = self.tag_policy
        tier = int(contract.custom(self.fields.tier_property))
        if tier not in t.tier_values:
            raise ContractError(f"{self.fields.tier_property} {tier} has no tier tag value")
        if contract.status not in t.status_values:
            raise ContractError(f"status '{contract.status}' has no status tag value")
        if not contract.domain:
            raise ContractError("'domain' is missing")
        if t.domain_values and contract.domain not in t.domain_values:
            raise ContractError(f"domain '{contract.domain}' is not one of {', '.join(t.domain_values)}")
        return {t.key("tier"): t.tier_values[tier],
                t.key("domain"): str(contract.domain),
                t.key("status"): t.status_values[contract.status],
                t.key("sensitivity"): str(contract.custom(self.fields.sensitivity_property))}

    def properties_for(self, contract: DataContract) -> dict[str, str]:
        prefix = self.fields.table_property_prefix
        props = {f"{prefix}id": contract.id, f"{prefix}version": str(contract.version)}
        if self.commit:
            props[f"{prefix}commit"] = self.commit
        return props

    def table_info(self, full_name: str):
        """The Unity Catalog object, or None if it does not exist."""
        try:
            return self.ws.tables.get(full_name)
        except NotFound:
            return None

    def _publish_table(self, contract: DataContract, table: ContractTable, full_name: str,
                       tags: Mapping[str, str], props: Mapping[str, str]) -> bool:
        info = self.table_info(full_name)
        if info is None:
            if contract.status in self.policy.pending_statuses:
                log.warning("%s %s is not built yet: pending until the bundle deploy creates it",
                            table.kind.replace("_", " "), full_name, extra={"source": str(contract.path)})
                return True
            log.error("%s does not exist, and contract status '%s' needs it", full_name, contract.status,
                      extra={"source": str(contract.path)})
            return False
        drift = self.drift(table, full_name, info)
        for message in drift:
            log.error(message, extra={"source": str(contract.path)})
        if drift:
            return False

        log.info("- %s (%s)", full_name, table.kind)
        fq, kw = sql_ident(*full_name.split(".")), table.keyword
        actual = {c.name: c for c in info.columns or []}
        if table.description and table.description != info.comment:
            self.run_sql(f"COMMENT ON {table.comment_keyword} {fq} IS {sql_literal(table.description)}")
        for name, column in table.columns.items():
            if column.description and column.description != actual[name].comment:
                # COMMENT ON COLUMN works on tables and views; ALTER VIEW has no ALTER COLUMN.
                self.run_sql(f"COMMENT ON COLUMN {fq}.{sql_ident(name)} IS {sql_literal(column.description)}")
        self.run_sql(f"ALTER {kw} {fq} SET TAGS ({self._pairs(tags)})")
        if table.pipeline_owned:
            # Unity Catalog rejects SET TBLPROPERTIES on a pipeline dataset; the pipeline sets the
            # contract id and version from the contract it was deployed with (expectations.py).
            stale = {k: v for k, v in props.items() if not k.endswith("commit") and (info.properties or {}).get(k) != v}
            if stale:
                log.warning("%s has %s, not %s: redeploy the bundle that builds it", full_name,
                            {k: (info.properties or {}).get(k) for k in stale}, stale,
                            extra={"source": str(contract.path)})
        elif any((info.properties or {}).get(k) != v for k, v in props.items()):
            self.run_sql(f"ALTER {kw} {fq} SET TBLPROPERTIES ({self._pairs(props)})")
        self.apply_table_grants(full_name, contract.access.for_table(table.name))
        return True

    @staticmethod
    def drift(table: ContractTable, full_name: str, info) -> list[str]:
        """How the object differs from the contract: its kind, then its columns."""
        table_type = getattr(info.table_type, "value", info.table_type) or ""
        expected = TABLE_TYPES.get(table.kind)
        if (expected and table_type not in expected) or (not expected and table_type in set().union(*TABLE_TYPES.values())):
            return [f"{full_name} is a {table_type}, but the contract says {table.kind}"]
        actual = {c.name for c in info.columns or []}
        drift = [f"column '{c}' is in the contract but not in {full_name}" for c in table.columns if c not in actual]
        return drift + [f"column '{c}' is in {full_name} but not in the contract" for c in actual if c not in table.columns]

    def set_certification(self, contract: DataContract, value: str | None) -> int:
        """Set the Unity Catalog certification tag (the Certified or Deprecated badge) on every
        built object of the contract, or remove it when `value` is None. Returns how many
        objects were changed; objects not built yet are skipped."""
        if not contract.has_server(self.server_name):
            return 0
        server, key, changed = contract.server(self.server_name), self.policy.certification_tag, 0
        for table in contract.tables:
            full_name = table.full_name(server)
            if self.table_info(full_name) is None:
                continue
            fq, kw = sql_ident(*full_name.split(".")), table.keyword
            current = self.object_tags(full_name).get(key)
            if value and current != value:
                self.run_sql(f"ALTER {kw} {fq} SET TAGS ({self._pairs({key: value})})")
            elif not value and current is not None:
                self.run_sql(f"ALTER {kw} {fq} UNSET TAGS ({sql_literal(key)})")
            else:
                continue
            log.info("- %s: %s = %s", full_name, key, value)
            changed += 1
        return changed

    def object_tags(self, full_name: str) -> dict[str, str]:
        """Tags on one table or view, from the entity tag assignments API."""
        assignments = self.ws.entity_tag_assignments.list("tables", full_name)
        return {a.tag_key: a.tag_value for a in assignments or []}

    @staticmethod
    def _pairs(values: Mapping[str, str]) -> str:
        return ", ".join(f"{sql_literal(k)} = {sql_literal(v)}" for k, v in values.items())

    def apply_table_grants(self, full_name: str, desired: Mapping[str, set[str]]) -> None:
        """Make the direct grants on a table match `desired`. With revoke_unlisted off, add only."""
        current = {a.principal: {p.value for p in a.privileges or []}
                   for a in self.ws.grants.get("table", full_name).privilege_assignments or []}
        changes = []
        for principal in sorted(set(current) | set(desired)):
            add = sorted(desired.get(principal, set()) - current.get(principal, set()))
            remove = sorted(current.get(principal, set()) - desired.get(principal, set()))
            if not self.grant_policy.revoke_unlisted:
                remove = []
            if add or remove:
                log.info("GRANT %s %s: +%s -%s", full_name, principal, add, remove)
                changes.append(PermissionsChange(principal=principal, add=[Privilege(p) for p in add],
                                                 remove=[Privilege(p) for p in remove]))
        if changes and not self.dry_run:
            self.ws.grants.update("table", full_name, changes=changes)

    def ensure_privilege(self, securable: str, full_name: str, privilege: str, principals: set[str]) -> None:
        """Add one privilege on a shared catalog or schema. Add only: other products use it too."""
        current = {a.principal for a in self.ws.grants.get(securable, full_name).privilege_assignments or []
                   if any(p.value == privilege for p in a.privileges or [])}
        missing = sorted(set(principals) - current)
        for principal in missing:
            log.info("GRANT %s %s %s: +['%s']", securable, full_name, principal, privilege)
        if missing and not self.dry_run:
            self.ws.grants.update(securable, full_name, changes=[
                PermissionsChange(principal=p, add=[Privilege(privilege)]) for p in missing])
