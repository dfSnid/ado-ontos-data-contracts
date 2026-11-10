import pytest

from dp_integration.contract import AccessPolicy, ContractError, DataContract, Version
from dp_integration.mandatory import LineIndex, MandatoryFieldChecker

from conftest import ROOT


@pytest.mark.parametrize("low, high", [("1.9.0", "1.10.0"), ("1.0.0", "1.0.1"), ("1.9.0-rc1", "1.9.1"),
                                       ("2", "10"), ("1.9.0-rc1", "1.9.0"), ("1.9.0-rc1", "1.9.0-rc2")])
def test_version_order(low, high):
    assert Version(low) < Version(high)
    assert max([Version(high), Version(low)]) == high


def test_version_equality_and_hash():
    assert Version("1.0.0") == "1.0.0"
    assert len({Version("1.0.0"), Version("1.0.0")}) == 1
    assert repr(Version("1.2")) == "Version('1.2')"


def test_contract_reads_access_file(write_contract):
    c = DataContract.from_file(write_contract())
    assert str(c) == "sample-product 1.0.0 (active)"
    assert c.custom("governanceTier") == 2
    assert [t.name for t in c.tables] == ["title"]
    assert c.tables[0].full_name(c.server("dev")) == "sample_catalog.sample_schema.title"
    assert c.access.for_table("title") == {"readers": {"SELECT"}}
    assert len(c.access) == 1 and c.access.principals == {"readers"}


def test_contract_without_access_file(write_contract):
    c = DataContract.from_file(write_contract(access=None))
    assert len(c.access) == 0 and c.access.for_table("title") == {}


def test_access_file_for_another_contract(write_contract):
    with pytest.raises(ContractError, match="access file is for"):
        DataContract.from_file(write_contract(access="contract: other\ngrants: []\n"))


def test_contract_errors(write_contract):
    c = DataContract.from_file(write_contract())
    with pytest.raises(ContractError, match="no server"):
        c.server("prod")
    with pytest.raises(ContractError, match="custom property"):
        c.custom("nope")
    with pytest.raises(ContractError, match="'id' is missing"):
        DataContract({"version": "1", "status": "draft"})


def test_empty_access_policy_is_falsy():
    assert not AccessPolicy.empty()


def test_line_index_falls_back_to_parent():
    lines = LineIndex("a:\n  b: 1\n  c:\n    - x\n")
    assert lines[("a", "b")] == 2
    assert lines[("a", "c", 0)] == 4
    assert lines[("a", "missing")] == 2  # the mapping under a starts on line 2
    assert lines[()] == 1


def test_mandatory_check_passes_on_a_complete_contract():
    checker = MandatoryFieldChecker.from_file(ROOT / "schemas/mandatory.schema.json")
    contract = ROOT.parent / "data-contracts/contracts/content/content-catalogue.odcs.yaml"
    if not contract.is_file():
        pytest.skip("data-contracts checkout not next to dp-integration")
    assert checker.check(contract) == []


def test_mandatory_check_points_at_lines(write_contract):
    checker = MandatoryFieldChecker.from_file(ROOT / "schemas/mandatory.schema.json")
    findings = checker.check(write_contract())
    assert findings, "the sample contract leaves out mandatory fields"
    assert all(f.line >= 1 for f in findings)
    assert [f.line for f in findings] == sorted(f.line for f in findings)
    assert any("Data Steward" in f.message for f in findings)
    assert str(findings[0]).startswith(f"{findings[0].source}:{findings[0].line}: ")


@pytest.mark.parametrize("text, error", [("id: [unclosed\n", "not valid YAML"), ("- a list\n", "expected a mapping")])
def test_unreadable_contract_is_a_contract_error(tmp_path, text, error):
    path = tmp_path / "bad.odcs.yaml"
    path.write_text(text)
    with pytest.raises(ContractError, match=error):
        DataContract.from_file(path)


def test_mandatory_check_reports_bad_yaml_as_a_finding(tmp_path):
    path = tmp_path / "bad.odcs.yaml"
    path.write_text("id: x\nname: [unclosed\n")
    checker = MandatoryFieldChecker.from_file(ROOT / "schemas/mandatory.schema.json")
    [finding] = checker.check(path)
    assert "not valid YAML" in finding.message and finding.line > 1
