import pytest

from dp_integration.contract import DataContract
from dp_integration.domains import DomainRegistry
from dp_integration.ontos import OntosError
from dp_integration.ontos_sync import OntosContractSync
from dp_integration.products import DataProductFile, GateError, OntosProductSync, check_product, check_registry
from dp_integration.unity_catalog import UnityCatalogPublisher

from conftest import FakeOntos, FakeWorkspace, table

TITLE = "sample_catalog.sample_schema.title"

PRODUCT = """\
apiVersion: v1.0.0
kind: DataProduct
id: sample
name: Sample
version: {version}
status: {status}
domain: sales
description:
  purpose: A sample product
outputPorts:
  - name: Titles
    version: 1.0.0
    contractId: sample-product
customProperties:
  - property: governanceTier
    value: 2
{extra}"""


@pytest.fixture
def write_product(tmp_path):
    def write(status="draft", version="1.0.0", level=None, scope=None):
        extra = "".join(f"  - property: {k}\n    value: {v}\n"
                        for k, v in (("certificationLevel", level), ("publicationScope", scope)) if v)
        path = tmp_path / "sample.odps.yaml"
        path.write_text(PRODUCT.format(status=status, version=version, extra=extra))
        return DataProductFile.from_file(path)
    return write


@pytest.fixture
def world(settings, write_contract):
    """Ontos with the contract registered at a given status, and a sync for the product."""
    def make(contract_status="active", built=True):
        ontos = FakeOntos()
        contract = DataContract.from_file(write_contract(status=contract_status))
        OntosContractSync(ontos, settings.ontos).sync(contract)
        ws = FakeWorkspace(tables={TITLE: table({"title_id": "Title key"})} if built else {})
        statements = []
        uc = UnityCatalogPublisher(ws, statements.append, settings.tags, settings.contract, settings.grants,
                                   server="dev", commit="abc", policy=settings.publish)
        return ontos, {contract.id: contract}, OntosProductSync(ontos, settings.ontos, uc), statements
    return make


def test_create_walks_to_under_review_with_owner_team(world, write_product):
    ontos, contracts, sync, _ = world(contract_status="approved")
    record = sync.sync(write_product(status="under_review"), contracts, owner_team_id="team-1")
    stored = ontos.products[record["id"]]
    assert stored["status"] == "under_review" and stored["owner_team_id"] == "team-1"
    assert stored["output_ports"][0]["contract_id"]  # the contract's Ontos id, not its ODCS id
    assert {"property": "sourceId", "value": "sample"} in stored["custom_properties"]
    assert [c[2] for c in ontos.calls if c[0] == "product_status"] == ["proposed", "under_review"]


def test_active_certifies_scopes_and_badges(world, write_product):
    ontos, contracts, sync, statements = world()
    record = sync.sync(write_product(status="active", level="Silver", scope="organization"), contracts, "team-1")
    stored = ontos.products[record["id"]]
    assert stored["status"] == "active" and stored["certification_level"] == 2
    assert stored["publication_scope"] == "organization"
    assert any("'system.certification_status' = 'certified'" in s for s in statements)


def test_second_run_changes_nothing(world, write_product):
    ontos, contracts, sync, _ = world()
    product = write_product(status="active", level="Silver", scope="organization")
    sync.sync(product, contracts, "team-1")
    ontos.calls.clear()
    sync.sync(product, contracts, "team-1")
    assert ontos.calls == []


def test_update_keeps_the_current_status_in_the_put(world, write_product):
    ontos, contracts, sync, _ = world()
    sync.sync(write_product(status="active"), contracts, "team-1")
    product = write_product(status="active")
    product.document["description"] = {"purpose": "changed"}
    record = sync.sync(product, contracts, "team-1")
    assert ("update_product", record["id"], "active") in ontos.calls
    assert ontos.products[record["id"]]["status"] == "active"


def test_breaking_change_asks_for_a_new_version(world, write_product):
    ontos, contracts, sync, _ = world()
    sync.sync(write_product(status="active"), contracts, "team-1")
    ontos.breaking = True
    product = write_product(status="active")
    product.document["name"] = "Renamed"
    with pytest.raises(OntosError, match="raise the product version"):
        sync.sync(product, contracts, "team-1")


def test_new_version_starts_from_the_older_one(world, write_product):
    ontos, contracts, sync, _ = world()
    first = sync.sync(write_product(status="active"), contracts, "team-1")
    second = sync.sync(write_product(status="active", version="1.1.0"), contracts, "team-1")
    assert ontos.products[second["id"]]["parent"] == first["id"]
    assert ontos.products[second["id"]]["status"] == "active"


@pytest.mark.parametrize("contract_status,built,target,message", [
    ("proposed", True, "approved", "needs approved"),
    ("approved", True, "active", "needs active"),
    ("active", False, "active", "not built yet"),
])
def test_gates(world, write_product, contract_status, built, target, message):
    ontos, contracts, sync, _ = world(contract_status=contract_status, built=built)
    with pytest.raises(GateError, match=message):
        sync.sync(write_product(status=target), contracts, "team-1")
    (stored,) = ontos.products.values()
    assert stored["status"] == "draft"  # created, but held back


def test_unregistered_contract_blocks_review(settings, write_contract, write_product):
    ontos = FakeOntos()
    contract = DataContract.from_file(write_contract(status="draft"))
    sync = OntosProductSync(ontos, settings.ontos)
    sync.sync(write_product(status="proposed"), {contract.id: contract}, "team-1")  # draft and proposed are fine
    with pytest.raises(GateError, match="not in Ontos"):
        sync.sync(write_product(status="under_review"), {contract.id: contract}, "team-1")


def test_deprecate_decertifies_and_badges(world, write_product):
    ontos, contracts, sync, statements = world()
    sync.sync(write_product(status="active", level="Gold"), contracts, "team-1")
    record = sync.sync(write_product(status="deprecated"), contracts, "team-1")
    assert ontos.products[record["id"]]["certification_level"] is None
    assert any("'system.certification_status' = 'deprecated'" in s for s in statements)


def test_unknown_level(world, write_product):
    _, contracts, sync, _ = world()
    with pytest.raises(GateError, match="unknown certification level"):
        sync.sync(write_product(status="active", level="Platinum"), contracts, "team-1")


def test_check_product(tmp_path, write_contract, write_product):
    contract = DataContract.from_file(write_contract(status="approved"))
    (tmp_path / "domains.yaml").write_text("domains:\n  - {name: sales}\n")
    registry = DomainRegistry.from_file(tmp_path / "domains.yaml")
    assert check_product(write_product(status="under_review"), {contract.id: contract}, registry, ["sales"]) == []
    problems = check_product(write_product(status="active", level="Gold"), {contract.id: contract}, registry,
                             ["sales"])
    assert problems == ["status active needs contract sample-product active, it is approved"]
    problems = check_product(write_product(status="approved", level="Gold", scope="domain"), {}, registry, ["marketing"])
    assert len(problems) == 4  # tag value, missing contract, level and scope before active


def test_check_registry(tmp_path):
    (tmp_path / "domains.yaml").write_text("domains:\n  - {name: sales}\n  - {name: marketing}\n")
    registry = DomainRegistry.from_file(tmp_path / "domains.yaml")
    assert check_registry(registry, ["sales"]) == [
        "domain 'marketing' is registered but not an allowed value of the domain tag"]
