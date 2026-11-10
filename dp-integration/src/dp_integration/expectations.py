"""Lakeflow expectations from a data contract: the portable-expectations pattern, with the
contract as the rules store.

The rules stay out of pipeline code. A product bundle's deploy copies this file and the
product's merged contract into the bundle (templates/bundle-deploy.yml), and the pipeline
applies the contract's row-level rules to each dataset:

    from contract_expectations import ContractExpectations
    rules = ContractExpectations.from_file(spark.conf.get("contract_path"))

    @dp.materialized_view(name="fct_linear_as_run_event", table_properties=rules.table_properties())
    @rules.apply("fct_linear_as_run_event")
    def fct_linear_as_run_event(): ...

So a rule changes only through a contract pull request the Data Stewards approve. The rules,
in contract order:

- `required: true`: `<column> IS NOT NULL` (completeness)
- a `nullValues` rule with `mustBe: 0`: `<column> IS NOT NULL`
- an `invalidValues` rule with `validValues` and `mustBe: 0`: `<column> IS NULL OR <column> IN (...)`
- any quality rule with a custom property `expectation`: that SQL expression, row by row. Use
  it to give a table-level `type: sql` count its row-level form, so `datacontract test` still
  runs the count and Ontos still scores it.

A rule's action comes from its `severity`: warning (the default) keeps the row and counts it,
error drops it, critical fails the update. The implicit `required` rules warn. Whole-table rules
(unique, row counts, percentages, sql counts without an `expectation`) are not row-level, so
`datacontract test` keeps running them.

Only PyYAML is needed, so the file runs as a plain module in a pipeline. Do not edit the copy in
a bundle: it is replaced on every deploy.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Mapping

import yaml

ACTIONS = {"info": "warn", "warning": "warn", "error": "drop", "critical": "fail"}
EXPECTATION_PROPERTY = "expectation"


@dataclass(frozen=True)
class Expectation:
    name: str
    constraint: str
    action: str       # warn, drop or fail
    dimension: str
    column: str | None = None


def _column(name: str) -> str:
    return "`" + name.replace("`", "``") + "`"


def _literal(value: Any) -> str:
    return "'" + str(value).replace("\\", "\\\\").replace("'", "\\'") + "'"


def _custom(rule: Mapping[str, Any], name: str) -> Any:
    return next((p.get("value") for p in rule.get("customProperties") or [] if p.get("property") == name), None)


class ContractExpectations:
    """The row-level rules of one ODCS contract, by schema object."""

    def __init__(self, document: Mapping[str, Any], source: str = "contract"):
        self.document, self.source = document, source
        self.objects = {o["name"]: o for o in document.get("schema") or []}

    @classmethod
    def from_file(cls, path: str | Path) -> ContractExpectations:
        path = Path(path)
        return cls(yaml.safe_load(path.read_text(encoding="utf-8")) or {}, path.name)

    def rules(self, table: str) -> list[Expectation]:
        """Every row-level rule on one schema object. A rule the pipeline cannot apply as
        written (an unknown severity, a repeated name) is an error, not skipped."""
        if table not in self.objects:
            raise ValueError(f"{self.source}: no schema object '{table}'")
        rules, names = list(self._rules(self.objects[table])), set()
        for rule in rules:
            if rule.name in names:
                raise ValueError(f"{self.source}: {table}: two rules are called '{rule.name}'")
            names.add(rule.name)
        return rules

    def _rules(self, obj: Mapping[str, Any]) -> Iterator[Expectation]:
        for prop in obj.get("properties") or []:
            name, c = prop["name"], _column(prop["name"])
            if prop.get("required"):
                yield Expectation(f"{name}_required", f"{c} IS NOT NULL", "warn", "completeness", name)
            for n, rule in enumerate(prop.get("quality") or []):
                yield from self._rule(obj, rule, n, name)
        for n, rule in enumerate(obj.get("quality") or []):
            yield from self._rule(obj, rule, n, None)

    def _rule(self, obj: Mapping[str, Any], rule: Mapping[str, Any], n: int,
              column: str | None) -> Iterator[Expectation]:
        constraint, default_name = _custom(rule, EXPECTATION_PROPERTY), None
        if constraint is None and column and rule.get("type", "library") == "library" and rule.get("mustBe") == 0:
            c = _column(column)
            if rule.get("metric") == "nullValues":
                constraint, default_name = f"{c} IS NOT NULL", f"{column}_not_null"
            elif rule.get("metric") == "invalidValues" and (rule.get("arguments") or {}).get("validValues"):
                values = ", ".join(_literal(v) for v in rule["arguments"]["validValues"])
                constraint, default_name = f"{c} IS NULL OR {c} IN ({values})", f"{column}_valid_values"
        if constraint is None:
            return  # whole-table rule: datacontract test runs it
        severity = str(rule.get("severity") or "warning").lower()
        if severity not in ACTIONS:
            raise ValueError(f"{self.source}: {obj['name']}: severity '{severity}' is not one of "
                             f"{', '.join(ACTIONS)}")
        name = rule.get("name") or default_name or f"{column or obj['name']}_rule_{n + 1}"
        yield Expectation(str(name), str(constraint), ACTIONS[severity], rule.get("dimension") or "other", column)

    def get_rules(self, table: str, action: str) -> dict[str, str]:
        """{name: constraint} for one object and action (warn, drop, fail): the argument to
        dp.expect_all, dp.expect_all_or_drop or dp.expect_all_or_fail."""
        return {r.name: r.constraint for r in self.rules(table) if r.action == action}

    def table_properties(self, prefix: str = "dp.contract_") -> dict[str, str]:
        """The contract id and version, for the dataset's table_properties. The publish pipeline
        cannot set properties on a pipeline dataset, so the pipeline records them itself
        (prefix: contract.table_property_prefix in platform.yaml)."""
        return {f"{prefix}id": str(self.document["id"]), f"{prefix}version": str(self.document["version"])}

    def dimensions(self, table: str) -> dict[str, str]:
        """{rule name: quality dimension}, to score the pipeline's event log by dimension."""
        return {r.name: r.dimension for r in self.rules(table)}

    def apply(self, table: str):
        """Decorator for a dataset function: every rule of `table`, grouped by action. Put it
        under @dp.materialized_view or @dp.table."""
        from pyspark import pipelines as dp  # imported here so the rules load without Spark

        decorators = {"warn": dp.expect_all, "drop": dp.expect_all_or_drop, "fail": dp.expect_all_or_fail}

        def wrap(function):
            for action, decorator in decorators.items():
                rules = self.get_rules(table, action)
                if rules:
                    function = decorator(rules)(function)
            return function
        return wrap
