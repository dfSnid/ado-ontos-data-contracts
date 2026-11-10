import subprocess
import sys
import textwrap
import types
from pathlib import Path

import pytest

from dp_integration.contract import DataContract
from dp_integration.expectation_check import ExpectationCheck
from dp_integration.expectations import ContractExpectations

from conftest import ROOT, FakeWorkspace, table

CONTRACT = textwrap.dedent("""\
    apiVersion: v3.1.0
    kind: DataContract
    id: linear
    version: 1.2.0
    status: active
    servers:
      - server: dev
        type: databricks
        catalog: cat
        schema: sch
    schema:
      - name: events
        physicalType: materialized_view
        properties:
          - name: event_id
            required: true
            unique: true
          - name: channel
            quality:
              - type: library
                metric: invalidValues
                arguments:
                  validValues: [ch1, "o'clock"]
                mustBe: 0
                dimension: conformity
              - type: library
                metric: nullValues
                mustBe: 0
                severity: error
              - type: library
                metric: duplicateValues
                mustBe: 0
          - name: note
        quality:
          - type: sql
            name: ends_after_start
            query: SELECT count(*) FROM ${object} WHERE ends <= starts
            mustBe: 0
            dimension: consistency
            severity: critical
            customProperties:
              - property: expectation
                value: ends > starts
          - type: sql
            name: has_rows
            query: SELECT count(*) FROM ${object}
            mustBeGreaterThan: 0
      - name: untested
        properties:
          - name: x
    """)


@pytest.fixture
def rules():
    import yaml
    return ContractExpectations(yaml.safe_load(CONTRACT), "linear.odcs.yaml")


def test_row_level_rules_in_contract_order(rules):
    got = [(r.name, r.constraint, r.action, r.dimension) for r in rules.rules("events")]
    assert got == [
        ("event_id_required", "`event_id` IS NOT NULL", "warn", "completeness"),
        ("channel_valid_values", "`channel` IS NULL OR `channel` IN ('ch1', 'o\\'clock')", "warn", "conformity"),
        ("channel_not_null", "`channel` IS NOT NULL", "drop", "other"),
        ("ends_after_start", "ends > starts", "fail", "consistency"),
    ]  # unique, duplicateValues and the row count are whole-table rules: datacontract test runs them
    assert rules.rules("untested") == []


def test_get_rules_by_action_and_dimensions(rules):
    assert rules.get_rules("events", "warn") == {"event_id_required": "`event_id` IS NOT NULL",
                                                 "channel_valid_values": rules.rules("events")[1].constraint}
    assert rules.get_rules("events", "drop") == {"channel_not_null": "`channel` IS NOT NULL"}
    assert rules.get_rules("events", "fail") == {"ends_after_start": "ends > starts"}
    assert rules.dimensions("events")["ends_after_start"] == "consistency"
    assert rules.table_properties() == {"dp.contract_id": "linear", "dp.contract_version": "1.2.0"}


@pytest.mark.parametrize("change, error", [
    (("severity: critical", "severity: fatal"), "severity 'fatal' is not one of"),
    (("name: ends_after_start", "name: event_id_required"), "two rules are called 'event_id_required'"),
])
def test_a_rule_the_pipeline_cannot_apply_is_an_error(change, error):
    import yaml
    with pytest.raises(ValueError, match=error):
        ContractExpectations(yaml.safe_load(CONTRACT.replace(*change))).rules("events")
    with pytest.raises(ValueError, match="no schema object 'nope'"):
        ContractExpectations(yaml.safe_load(CONTRACT)).rules("nope")


def test_apply_stacks_one_decorator_per_action(rules, monkeypatch):
    calls = []

    def recorder(kind):
        return lambda rules_: (lambda f: calls.append((kind, sorted(rules_))) or f)

    dp = types.SimpleNamespace(expect_all=recorder("warn"), expect_all_or_drop=recorder("drop"),
                               expect_all_or_fail=recorder("fail"))
    monkeypatch.setitem(sys.modules, "pyspark", types.SimpleNamespace(pipelines=dp))
    monkeypatch.setitem(sys.modules, "pyspark.pipelines", dp)

    def events():
        return "df"

    assert rules.apply("events")(events) is events
    assert calls == [("warn", ["channel_valid_values", "event_id_required"]), ("drop", ["channel_not_null"]),
                     ("fail", ["ends_after_start"])]
    calls.clear()
    rules.apply("untested")(events)
    assert calls == []


def contract_file(tmp_path, text=CONTRACT) -> DataContract:
    path = tmp_path / "linear.odcs.yaml"
    path.write_text(text, encoding="utf-8")
    return DataContract.from_file(path)


EVENTS = "cat.sch.events"


def test_check_counts_flagged_and_null_rows(tmp_path, caplog):
    queries = []

    def rows(sql):
        queries.append(sql)
        return [["10", "0", "0", "2", "1", "0", "0", "3", "0"]]

    ws = FakeWorkspace(tables={EVENTS: table({})})
    assert ExpectationCheck(ws, rows, "dev").check(contract_file(tmp_path))
    (sql,) = queries
    assert sql.startswith("SELECT count(*), count_if(NOT (`event_id` IS NOT NULL))") and "FROM `cat`.`sch`.`events`" in sql
    assert "channel_valid_values (warn) would warn on 2 of 10 rows" in caplog.text
    assert "channel_valid_values is NULL for 1 rows" in caplog.text
    assert "ends_after_start (fail) would fail the update on 3 of 10 rows" in caplog.text


def test_check_names_the_rule_that_does_not_run(tmp_path, caplog):
    def rows(sql):
        if "ends > starts" in sql:
            raise RuntimeError("UNRESOLVED_COLUMN starts")
        return [["0"]]

    ws = FakeWorkspace(tables={EVENTS: table({})})
    assert not ExpectationCheck(ws, rows, "dev").check(contract_file(tmp_path))
    assert "rule 'ends_after_start' does not run on `cat`.`sch`.`events`: UNRESOLVED_COLUMN starts" in caplog.text


def test_check_skips_objects_not_built_and_other_servers(tmp_path, caplog):
    def rows(sql):
        raise AssertionError("no query expected")

    contract = contract_file(tmp_path)
    assert ExpectationCheck(FakeWorkspace(), rows, "dev").check(contract)
    assert "cat.sch.events is not built yet: 4 rules not tried" in caplog.text
    assert ExpectationCheck(FakeWorkspace(), rows, "prod").check(contract)


def test_check_fails_on_a_rule_it_cannot_load(tmp_path, caplog):
    contract = contract_file(tmp_path, CONTRACT.replace("severity: critical", "severity: fatal"))
    assert not ExpectationCheck(FakeWorkspace(tables={EVENTS: table({})}), lambda s: [], "dev").check(contract)
    assert "severity 'fatal'" in caplog.text


def test_fetch_rules_copies_the_listed_contracts_and_the_module(tmp_path):
    contracts, bundle = tmp_path / "data-contracts", tmp_path / "bundle" / "src" / "pipeline"
    (contracts / "contracts" / "content").mkdir(parents=True)
    (contracts / "contracts" / "content" / "linear.odcs.yaml").write_text(CONTRACT)
    bundle.mkdir(parents=True)
    (bundle / "contracts").mkdir()
    (bundle / "contracts" / "stale.odcs.yaml").write_text("old")
    manifest = bundle / "contracts.txt"
    manifest.write_text("# rules\ncontracts/content/linear.odcs.yaml   # the linear port\n\n")
    script = ROOT / "bin" / "fetch-rules.sh"

    subprocess.run([str(script), str(manifest), str(contracts)], check=True, capture_output=True)
    assert sorted(p.name for p in (bundle / "contracts").iterdir()) == ["linear.odcs.yaml"]
    assert (bundle / "contract_expectations.py").read_text() == \
        (ROOT / "src" / "dp_integration" / "expectations.py").read_text()

    manifest.write_text("contracts/content/missing.odcs.yaml\n")
    r = subprocess.run([str(script), str(manifest), str(contracts)], capture_output=True, text=True)
    assert r.returncode == 2 and "no contracts/content/missing.odcs.yaml" in r.stderr
