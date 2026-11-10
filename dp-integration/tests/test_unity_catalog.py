import pytest

from dp_integration.config import GrantPolicy
from dp_integration.contract import ContractError, DataContract
from dp_integration.unity_catalog import UnityCatalogPublisher, sql_ident, sql_literal

from conftest import CONTRACT, FakeWorkspace, table

TITLE = "sample_catalog.sample_schema.title"


def publisher(settings, ws, statements, grants=None, commit="abc123"):
    return UnityCatalogPublisher(ws, statements.append, settings.tags, settings.contract,
                                 grants or settings.grants, server="dev", commit=commit)


def test_sql_quoting():
    assert sql_literal("it's") == "'it\\'s'"
    assert sql_ident("a", "b`c") == "`a`.`b``c`"


def test_publish_sets_tags_comments_properties_and_grants(settings, write_contract):
    ws = FakeWorkspace(tables={TITLE: table({"title_id": None, "title_name": "Display name"})},
                       grants={("table", TITLE): {"old-group": {"SELECT"}}})
    statements = []
    assert publisher(settings, ws, statements).publish(DataContract.from_file(write_contract()))

    joined = "\n".join(statements)
    assert "COMMENT ON TABLE `sample_catalog`.`sample_schema`.`title` IS 'One row per title'" in joined
    assert "COMMENT ON COLUMN `sample_catalog`.`sample_schema`.`title`.`title_id` IS 'Title key'" in joined
    assert "title_name" not in joined  # comment already matches
    assert "'dp_tier' = 'domain'" in joined and "'dp_status' = 'published'" in joined
    assert "'dp.contract_commit' = 'abc123'" in joined

    table_change = next(u for u in ws.grants.updates if u[0] == "table")[2]
    by_principal = {c.principal: c for c in table_change}
    assert [p.value for p in by_principal["readers"].add] == ["SELECT"]
    assert [p.value for p in by_principal["old-group"].remove] == ["SELECT"]
    parents = {(u[0], u[1]) for u in ws.grants.updates if u[0] != "table"}
    assert parents == {("catalog", "sample_catalog"), ("schema", "sample_catalog.sample_schema")}


def test_add_only_grants_and_no_parent_use(settings, write_contract):
    ws = FakeWorkspace(tables={TITLE: table({"title_id": "Title key", "title_name": "Display name"})},
                       grants={("table", TITLE): {"old-group": {"SELECT"}}})
    p = publisher(settings, ws, [], grants=GrantPolicy(revoke_unlisted=False, parent_use=False))
    assert p.publish(DataContract.from_file(write_contract()))
    (change,) = ws.grants.updates[0][2]
    assert change.principal == "readers" and change.remove == []
    assert all(u[0] == "table" for u in ws.grants.updates)


def test_drift_blocks_the_table(settings, write_contract, caplog):
    ws = FakeWorkspace(tables={TITLE: table({"title_id": None, "extra": None})})
    statements = []
    assert not publisher(settings, ws, statements).publish(DataContract.from_file(write_contract()))
    assert not any("ALTER TABLE" in s for s in statements)
    assert "column 'title_name' is in the contract but not in" in caplog.text
    assert "column 'extra' is in" in caplog.text


def test_nothing_to_change_runs_only_set_tags(settings, write_contract):
    ws = FakeWorkspace(
        tables={TITLE: table({"title_id": "Title key", "title_name": "Display name"}, comment="One row per title",
                             properties={"dp.contract_id": "sample-product", "dp.contract_version": "1.0.0",
                                         "dp.contract_commit": "abc123"})},
        grants={("table", TITLE): {"readers": {"SELECT"}}, ("catalog", "sample_catalog"): {"readers": {"USE_CATALOG"}},
                ("schema", "sample_catalog.sample_schema"): {"readers": {"USE_SCHEMA"}}})
    statements = []
    assert publisher(settings, ws, statements).publish(DataContract.from_file(write_contract()))
    assert len(statements) == 1 and "SET TAGS" in statements[0]
    assert ws.grants.updates == []


@pytest.mark.parametrize("domain, error", [(None, "'domain' is missing"), ("sport", "not one of")])
def test_domain_must_be_set_and_allowed(settings, write_contract, domain, error):
    contract = DataContract.from_file(write_contract())
    document = {k: v for k, v in contract.document.items() if k != "domain"}
    if domain:
        document["domain"] = domain
    with pytest.raises(ContractError, match=error):
        publisher(settings, FakeWorkspace(), []).tags_for(DataContract(document))


METRIC_VIEW_OBJECT = """\
  - name: mv_titles
    physicalName: mv_titles
    physicalType: metric_view
    description: Titles by genre
    properties:
      - name: genre
        description: Genre dimension
      - name: title_count
        description: Number of titles
"""
MV = "sample_catalog.sample_schema.mv_titles"


@pytest.fixture
def write_mv_contract(tmp_path):
    def write(status="active"):
        text = CONTRACT.format(version="1.0.0", status=status).replace("customProperties:", METRIC_VIEW_OBJECT + "customProperties:", 1)
        path = tmp_path / "mv.odcs.yaml"
        path.write_text(text, encoding="utf-8")
        return path
    return write


def test_metric_view_uses_alter_view_and_comment_on_column(settings, write_mv_contract):
    ws = FakeWorkspace(tables={TITLE: table({"title_id": "Title key", "title_name": "Display name"}),
                               MV: table({"genre": None, "title_count": None}, table_type="METRIC_VIEW")})
    statements = []
    assert publisher(settings, ws, statements).publish(DataContract.from_file(write_mv_contract()))
    mv = [s for s in statements if "`mv_titles`" in s]
    assert mv[0] == "COMMENT ON VIEW `sample_catalog`.`sample_schema`.`mv_titles` IS 'Titles by genre'"
    assert "COMMENT ON COLUMN `sample_catalog`.`sample_schema`.`mv_titles`.`title_count` IS 'Number of titles'" in mv
    assert any(s.startswith("ALTER VIEW `sample_catalog`.`sample_schema`.`mv_titles` SET TAGS") for s in mv)
    assert not any("ALTER TABLE" in s for s in mv)


def test_materialized_view_checks_properties_instead_of_setting_them(settings, write_contract, caplog):
    path = write_contract()
    path.write_text(path.read_text().replace("    physicalName: title\n", "    physicalName: title\n    physicalType: materialized_view\n"))
    pipeline_props = {"dp.contract_id": "sample-product", "dp.contract_version": "0.9.0"}
    ws = FakeWorkspace(tables={TITLE: table({"title_id": None, "title_name": None}, properties=pipeline_props,
                                            table_type="MATERIALIZED_VIEW")})
    statements = []
    p = publisher(settings, ws, statements)
    assert p.publish(DataContract.from_file(path))
    fq = "`sample_catalog`.`sample_schema`.`title`"
    assert statements[0] == f"COMMENT ON TABLE {fq} IS 'One row per title'"  # COMMENT ON has no MATERIALIZED VIEW
    assert any(s.startswith(f"ALTER MATERIALIZED VIEW {fq} SET TAGS") for s in statements)
    assert not any("TBLPROPERTIES" in s for s in statements)
    assert "has {'dp.contract_version': '0.9.0'}, not {'dp.contract_version': '1.0.0'}: redeploy the bundle" in caplog.text

    p.set_certification(DataContract.from_file(path), "certified")
    assert statements[-1].startswith(f"ALTER MATERIALIZED VIEW {fq} SET TAGS")
    ws.tables.tables[TITLE].table_type = "MANAGED"
    assert not p.publish(DataContract.from_file(path))
    assert "is a MANAGED, but the contract says materialized_view" in caplog.text


@pytest.mark.parametrize("contract_kind, table_type", [("table", "METRIC_VIEW"), ("metric_view", "MANAGED")])
def test_wrong_object_kind_is_drift(settings, write_mv_contract, caplog, contract_kind, table_type):
    mv_type = table_type if contract_kind == "metric_view" else "METRIC_VIEW"
    title_type = table_type if contract_kind == "table" else "MANAGED"
    ws = FakeWorkspace(tables={TITLE: table({"title_id": None, "title_name": None}, table_type=title_type),
                               MV: table({"genre": None, "title_count": None}, table_type=mv_type)})
    assert not publisher(settings, ws, []).publish(DataContract.from_file(write_mv_contract()))
    assert "but the contract says" in caplog.text


@pytest.mark.parametrize("status, ok", [("approved", True), ("proposed", True), ("active", False)])
def test_missing_object_is_pending_until_active(settings, write_contract, caplog, status, ok):
    statements = []
    assert publisher(settings, FakeWorkspace(), statements).publish(
        DataContract.from_file(write_contract(status=status))) is ok
    assert not any("`title`" in s for s in statements)
    assert ("not built yet" in caplog.text) is ok
    assert ("does not exist, and contract status 'active' needs it" in caplog.text) is not ok


def test_set_certification_tags_built_objects_only(settings, write_mv_contract):
    ws = FakeWorkspace(tables={TITLE: table({"title_id": None, "title_name": None})},
                       tags={TITLE: {"dp_tier": "domain"}})
    statements = []
    p = publisher(settings, ws, statements)
    assert p.set_certification(DataContract.from_file(write_mv_contract()), "certified") == 1
    assert statements == ["ALTER TABLE `sample_catalog`.`sample_schema`.`title` SET TAGS "
                          "('system.certification_status' = 'certified')"]


def test_set_certification_is_idempotent_and_removes(settings, write_contract):
    ws = FakeWorkspace(tables={TITLE: table({"title_id": None, "title_name": None})},
                       tags={TITLE: {"system.certification_status": "certified"}})
    statements = []
    p = publisher(settings, ws, statements)
    contract = DataContract.from_file(write_contract())
    assert p.set_certification(contract, "certified") == 0
    assert p.set_certification(contract, None) == 1
    assert statements == ["ALTER TABLE `sample_catalog`.`sample_schema`.`title` UNSET TAGS ('system.certification_status')"]


def test_contract_without_a_server_for_the_environment_is_skipped(settings, write_contract):
    statements = []
    uc = UnityCatalogPublisher(FakeWorkspace(), statements.append, settings.tags, settings.contract,
                               settings.grants, server="prod")
    contract = DataContract.from_file(write_contract())
    assert uc.publish(contract) and uc.set_certification(contract, "certified") == 0
    assert statements == []
