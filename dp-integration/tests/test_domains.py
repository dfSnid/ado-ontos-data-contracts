import pytest

from dp_integration.contract import ContractError
from dp_integration.domains import DomainRegistry, OntosDomainSync

from conftest import FakeOntos

REGISTRY = """\
domains:
  - name: sales
    description: What the business makes and buys
    team:
      name: sales-team
      title: Sales
      groups: [dp-sales-stewards, dp-sales-producers, dp-publishers]
  - name: online
    description: Online orders
    parent: sales
"""


@pytest.fixture
def registry(tmp_path):
    path = tmp_path / "domains.yaml"
    path.write_text(REGISTRY)
    return DomainRegistry.from_file(path)


def test_registry_orders_parents_first(tmp_path):
    path = tmp_path / "domains.yaml"
    path.write_text("domains:\n  - {name: online, parent: sales}\n  - {name: sales}\n")
    assert [d.name for d in DomainRegistry.from_file(path).ordered()] == ["sales", "online"]


@pytest.mark.parametrize("text,message", [
    ("domains:\n  - {name: a, parent: b}\n  - {name: b, parent: a}\n", "cycle"),
    ("domains:\n  - {name: a, parent: nope}\n", "unknown parent"),
    ("domains:\n  - {name: a}\n  - {name: a}\n", "twice"),
    ("domains:\n  - {description: x}\n", "needs a name"),
])
def test_registry_rejects(tmp_path, text, message):
    path = tmp_path / "domains.yaml"
    path.write_text(text)
    with pytest.raises(ContractError, match=message):
        DomainRegistry.from_file(path)


def test_sync_creates_domains_and_team(registry):
    ontos = FakeOntos()
    ids = OntosDomainSync(ontos).sync(registry)
    assert ontos.domains[ids["online"]]["parent_id"] == ids["sales"]
    (team,) = ontos.teams.values()
    assert team["domain_id"] == ids["sales"]
    assert [m["member_identifier"] for m in team["members"]] == [
        "dp-sales-stewards", "dp-sales-producers", "dp-publishers"]


def test_sync_is_idempotent_and_add_only(registry):
    ontos = FakeOntos()
    OntosDomainSync(ontos).sync(registry)
    team = next(iter(ontos.teams.values()))
    team["members"].append({"member_type": "group", "member_identifier": "added-in-ontos"})
    ontos.calls.clear()
    OntosDomainSync(ontos).sync(registry)
    assert ontos.calls == []
    assert len(team["members"]) == 4  # nothing removed


def test_sync_adopts_an_auto_created_domain(registry):
    ontos = FakeOntos()
    existing = ontos.create_domain({"name": "sales", "description": None, "parent_id": None})
    ontos.calls.clear()
    ids = OntosDomainSync(ontos).sync(registry)
    assert ids["sales"] == existing["id"]
    assert ("update_domain", existing["id"]) in ontos.calls
    assert ontos.domains[existing["id"]]["description"] == "What the business makes and buys"
