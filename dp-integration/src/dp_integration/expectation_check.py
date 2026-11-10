"""Try a contract's Lakeflow expectations (see expectations.py) against the objects in one
environment, before the pipeline applies them.

For each object the contract has row-level rules on, one query on the warehouse counts the rows
each rule would flag. A rule that does not run (a typo, a column that does not exist) is an
error: the contract PR fails rather than the next pipeline update. Flagged rows are warnings
only, so the Data Stewards see what a rule would warn on, drop or fail before they approve it.
An object that is not built yet is skipped.
"""
from __future__ import annotations

import logging
from typing import Callable

from databricks.sdk.errors import NotFound

from .contract import DataContract
from .expectations import ContractExpectations, Expectation
from .unity_catalog import sql_ident

log = logging.getLogger(__name__)

RowsRunner = Callable[[str], list]

EFFECT = {"warn": "would warn on", "drop": "would drop", "fail": "would fail the update on"}


class ExpectationCheck:
    """`ws` is a databricks.sdk WorkspaceClient (or anything with its tables API) and `rows`
    runs one query and returns its rows, like StatementRunner.rows."""

    def __init__(self, ws, rows: RowsRunner, server: str):
        self.ws, self.rows, self.server_name = ws, rows, server

    def check(self, contract: DataContract) -> bool:
        """False when a rule cannot be loaded or does not run; flagged rows never fail."""
        if not contract.has_server(self.server_name):
            log.info("== %s: %s has no '%s' server, skipped", contract.path, contract, self.server_name)
            return True
        source = {"source": str(contract.path)}
        server = contract.server(self.server_name)
        expectations = ContractExpectations(contract.document, contract.path.name if contract.path else contract.id)
        log.info("== %s: %s", contract.path, contract)
        ok = True
        for table in contract.tables:
            try:
                rules = expectations.rules(table.name)
            except ValueError as e:
                log.error("%s", e, extra=source)
                ok = False
                continue
            if not rules:
                continue
            full_name = table.full_name(server)
            try:
                self.ws.tables.get(full_name)
            except NotFound:
                log.info("- %s is not built yet: %d rules not tried", full_name, len(rules))
                continue
            ok = self._check_table(full_name, rules, source) and ok
        return ok

    def _check_table(self, full_name: str, rules: list[Expectation], source: dict) -> bool:
        fq = sql_ident(*full_name.split("."))
        columns = ", ".join(f"count_if(NOT ({r.constraint})), count_if(({r.constraint}) IS NULL)" for r in rules)
        try:
            (row,) = self.rows(f"SELECT count(*), {columns} FROM {fq}")
        except RuntimeError:
            return self._find_broken(fq, rules, source)
        total = int(row[0])
        log.info("- %s: %d rows, %d rules", full_name, total, len(rules))
        for n, rule in enumerate(rules):
            false, null = int(row[1 + 2 * n]), int(row[2 + 2 * n])
            if false:
                log.warning("%s.%s (%s) %s %d of %d rows", full_name, rule.name, rule.action,
                            EFFECT[rule.action], false, total, extra=source)
            else:
                log.info("  %s (%s): no rows flagged", rule.name, rule.action)
            if null:
                log.warning("%s.%s is NULL for %d rows: write it so it is true or false", full_name,
                            rule.name, null, extra=source)
        return True

    def _find_broken(self, fq: str, rules: list[Expectation], source: dict) -> bool:
        """The combined query failed: try each rule alone to name the one that does not run."""
        broken = 0
        for rule in rules:
            try:
                self.rows(f"SELECT count_if(NOT ({rule.constraint})) FROM {fq}")
            except RuntimeError as e:
                log.error("rule '%s' does not run on %s: %s", rule.name, fq, e, extra=source)
                broken += 1
        if not broken:
            log.error("the rules on %s do not run together", fq, extra=source)
        return False
