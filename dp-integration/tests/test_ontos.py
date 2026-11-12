import json

import pytest

from dp_integration.contract import DataContract
from dp_integration.ontos import OntosError, StaticToken
from dp_integration.ontos_roles import OntosRoleMapper
from dp_integration.ontos_sync import OntosContractSync, OwnerTeam
from dp_integration.quality import QualityResults

from conftest import FakeOntos

RUN = {"dataContractId": "sample-product", "dataContractVersion": "1.0.0", "runId": "r1", "server": "dev",
       "result": "warning", "timestampEnd": "2026-11-05T00:00:00Z",
       "checks": [{"dimension": "completeness", "result": "passed"},
                  {"dimension": "completeness", "result": "failed"},
                  {"dimension": "made-up", "result": "passed"}]}


def test_first_version_is_imported_with_quality(settings, write_contract, tmp_path):
    (tmp_path / "q").mkdir()
    (tmp_path / "q" / "run.json").write_text(json.dumps(RUN))
    (tmp_path / "q" / "other.json").write_text("[1, 2]")
    quality = QualityResults.from_dir(tmp_path / "q", settings.ontos.quality_dimensions)
    ontos = FakeOntos()
    record = OntosContractSync(ontos, settings.ontos).sync(DataContract.from_file(write_contract()), quality)
    assert record["version"] == "1.0.0"
    assert ("import", "1.0.0") in ontos.calls
    assert ("quality", record["id"], "completeness", 50.0) in ontos.calls
    assert ("quality", record["id"], "other", 100.0) in ontos.calls


def test_same_version_only_aligns_status(settings, write_contract):
    ontos, sync = FakeOntos(), None
    sync = OntosContractSync(ontos, settings.ontos)
    sync.sync(DataContract.from_file(write_contract(status="draft")))
    record = sync.sync(DataContract.from_file(write_contract(status="active")))
    assert record["status"] == "active"
    # Ontos moves one step at a time: draft -> proposed -> approved -> active
    assert [c for c in ontos.calls if c[0] == "change_status"] == [
        ("change_status", record["id"], s) for s in ("proposed", "approved", "active")]
    assert [c[0] for c in ontos.calls].count("import") == 1


def test_later_version_is_linked_to_the_newest_older_one(settings, write_contract):
    ontos = FakeOntos()
    sync = OntosContractSync(ontos, settings.ontos)
    first = sync.sync(DataContract.from_file(write_contract(version="1.0.0")))
    second = sync.sync(DataContract.from_file(write_contract(version="1.10.0")))
    sync.sync(DataContract.from_file(write_contract(version="1.2.0")))
    assert ontos.contracts[second["id"]]["parent"] == first["id"]
    assert ontos.contracts[second["id"]]["customProperties"]["sourceId"] == "sample-product"
    assert isinstance(ontos.contracts[second["id"]]["team"], list)
    # 1.2.0 sorts below 1.10.0, so its parent is 1.0.0
    third = next(c for c in ontos.contracts.values() if c["version"] == "1.2.0")
    assert third["parent"] == first["id"]


def test_failed_fill_deletes_the_draft(settings, write_contract):
    ontos = FakeOntos()
    OntosContractSync(ontos, settings.ontos).sync(DataContract.from_file(write_contract(version="1.0.0")))
    ontos.fail_update = True
    with pytest.raises(OntosError):
        OntosContractSync(ontos, settings.ontos).sync(DataContract.from_file(write_contract(version="1.1.0")))
    assert ontos.calls[-1][0] == "delete"
    assert [c["version"] for c in ontos.contracts.values()] == ["1.0.0"]


def test_owner_team_keeps_the_contract_visible_to_the_publisher(settings, write_contract):
    ontos = FakeOntos()
    team = ontos.create_team({"name": "sample-domain"})
    ontos.publisher_member_of = team["id"]
    sync = OntosContractSync(ontos, settings.ontos, {"sales": OwnerTeam("sample-domain", team["id"])})
    first = sync.sync(DataContract.from_file(write_contract(status="proposed")))
    assert ontos.contracts[first["id"]]["owner_team_id"] == team["id"]
    second = sync.sync(DataContract.from_file(write_contract(status="approved")))
    assert second["id"] == first["id"] and second["status"] == "approved"
    assert [c[0] for c in ontos.calls].count("import") == 1
    later = sync.sync(DataContract.from_file(write_contract(version="1.1.0", status="proposed")))
    assert ontos.contracts[later["id"]]["owner_team_id"] == team["id"]


def test_without_an_owner_team_a_proposed_contract_is_imported_again(settings, write_contract, caplog):
    # Without an owner team, the publisher cannot see its own proposed contract in Ontos.
    ontos = FakeOntos()
    ontos.publisher_member_of = ontos.create_team({"name": "sample-domain"})["id"]
    sync = OntosContractSync(ontos, settings.ontos, {"other": OwnerTeam("other-domain", "t-x")})
    sync.sync(DataContract.from_file(write_contract(status="proposed")))
    sync.sync(DataContract.from_file(write_contract(status="approved")))
    assert [c[0] for c in ontos.calls].count("import") == 2
    assert "no owner team for domain" in caplog.text


def test_role_mapper(settings):
    ontos = FakeOntos()
    ontos.roles = [{"id": "r1", "name": "Data Steward", "assigned_groups": ["data-stewards", "dp-publishers"]},
                   {"id": "r2", "name": "Admin", "assigned_groups": ["admins"]}]
    result = OntosRoleMapper(ontos, settings.ontos).apply()
    assert result["Data Steward"] == ["data-stewards", "dp-sales-stewards"]
    assert result["Contract Publisher"] == ["dp-publishers"]
    assert result["Admin"] == ["admins"]
    assert OntosRoleMapper(ontos, settings.ontos).apply() == result  # idempotent


def test_role_mapper_brings_the_publisher_role_up_to_date(settings):
    ontos = FakeOntos()
    ontos.roles = [{"id": "r1", "name": "Contract Publisher", "assigned_groups": ["dp-publishers"],
                    "feature_permissions": {"data-contracts": "Read/Write", "teams": "None"},
                    "approval_privileges": {"PRODUCTS": False, "CONTRACTS": False}}]
    OntosRoleMapper(ontos, settings.ontos).apply()
    (role,) = ontos.roles
    assert role["feature_permissions"]["data-products"] == "Read/Write"
    assert role["approval_privileges"] == {"PRODUCTS": True}
    ontos.calls.clear()
    OntosRoleMapper(ontos, settings.ontos).apply()
    assert ontos.calls == []  # Ontos listing unset features as None is not a difference


def test_role_mapper_dry_run_changes_nothing(settings):
    ontos = FakeOntos()
    OntosRoleMapper(ontos, settings.ontos).apply(dry_run=True)
    assert ontos.roles == [] and ontos.calls == []


def test_static_token_hides_its_value():
    assert StaticToken("secret")() == "secret"
    assert "secret" not in repr(StaticToken("secret"))
