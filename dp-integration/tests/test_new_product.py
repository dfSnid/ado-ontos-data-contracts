import ast
import shutil

import pytest
import yaml

from dp_integration import cli
from dp_integration.expectations import ContractExpectations
from dp_integration.new_product import NewProduct, TemplateError

from conftest import ROOT

CONFIG = str(ROOT / "config")
EXAMPLE = ROOT.parent / "data-contracts"


@pytest.fixture
def contracts_root(tmp_path):
    """A copy of the example data-contracts repository, so the new files sit beside real ones."""
    root = tmp_path / "data-contracts"
    shutil.copytree(EXAMPLE, root, ignore=shutil.ignore_patterns("__pycache__"))
    return root


def new_product(contracts_root, *extra):
    return cli.main(["-c", CONFIG, "new-product", "--domain", "sales", "--product", "returns",
                     "--contracts-root", str(contracts_root), *extra], environ={})


def test_generated_files_pass_the_blocking_checks(contracts_root, capsys):
    assert new_product(contracts_root) == cli.OK
    contract = contracts_root / "contracts/sales/returns.odcs.yaml"
    product = contracts_root / "products/sales/returns.odps.yaml"
    assert (contracts_root / "contracts/sales/returns.access.yaml").is_file()
    assert "Next steps:" in capsys.readouterr().out

    assert cli.main(["-c", CONFIG, "check-mandatory", str(contract)], environ={}) == cli.OK
    assert cli.main(["-c", CONFIG, "check-products", "--registry", str(contracts_root / "domains/domains.yaml"),
                     "--contracts-dir", str(contracts_root / "contracts"), str(product)], environ={}) == cli.OK


def test_bundle_is_complete_and_filled_in(contracts_root):
    assert new_product(contracts_root) == cli.OK
    bundle = contracts_root.parent / "dp-returns"
    files = sorted(p.relative_to(bundle).as_posix() for p in bundle.rglob("*") if p.is_file())
    assert files == [".gitignore", "README.md", "databricks.yml", "pipelines/deploy.yml",
                     "resources/returns.yml", "src/pipeline/contracts.txt", "src/pipeline/returns.py"]
    for path in bundle.rglob("*"):
        if path.is_file():
            assert "{{" not in path.read_text(), path
    ast.parse((bundle / "src/pipeline/returns.py").read_text())
    resources = yaml.safe_load((bundle / "resources/returns.yml").read_text())["resources"]
    assert resources["pipelines"]["returns"]["configuration"]["contract_path"].endswith(
        "/contracts/returns.odcs.yaml")
    # A workspace allows each pipeline name once, so dev and prod in one workspace need their own.
    assert resources["pipelines"]["returns"]["name"] == "returns-${bundle.target}"
    # A bundle rejects the same resource key in two resource types.
    keys = [key for kind in resources.values() for key in kind]
    assert len(keys) == len(set(keys)), keys
    assert yaml.safe_load((bundle / "databricks.yml").read_text())["targets"]["prod"]["variables"] == {
        "catalog": "sales_prod"}
    # The manifest and the deploy trigger name the same contract, and fetch-rules can find it.
    manifest = [line for line in (bundle / "src/pipeline/contracts.txt").read_text().splitlines()
                if line and not line.startswith("#")]
    assert manifest == ["contracts/sales/returns.odcs.yaml"]
    assert (contracts_root / manifest[0]).is_file()
    assert manifest[0] in (bundle / "pipelines/deploy.yml").read_text()


def test_contract_rules_become_pipeline_expectations(contracts_root):
    assert new_product(contracts_root) == cli.OK
    rules = ContractExpectations.from_file(contracts_root / "contracts/sales/returns.odcs.yaml")
    constraints = {r.constraint for r in rules.rules("returns")}
    assert "`id` IS NOT NULL" in constraints
    assert "`category` IS NULL OR `category` IN ('a', 'b')" in constraints
    assert "loaded_at <= current_timestamp()" in constraints


def test_options_name_the_contract_dataset_and_catalogs(contracts_root, tmp_path):
    bundle = tmp_path / "elsewhere"
    assert new_product(contracts_root, "--contract", "returns-daily", "--dataset", "fct_return",
                       "--catalog-dev", "returns_dev", "--catalog-prod", "returns_prod",
                       "--bundle-dir", str(bundle)) == cli.OK
    contract = yaml.safe_load((contracts_root / "contracts/sales/returns-daily.odcs.yaml").read_text())
    assert [s["catalog"] for s in contract["servers"]] == ["returns_dev", "returns_prod"]
    assert contract["schema"][0]["name"] == "fct_return"
    product = yaml.safe_load((contracts_root / "products/sales/returns.odps.yaml").read_text())
    assert product["outputPorts"][0]["contractId"] == "returns-daily"
    assert (bundle / "src/pipeline/returns.py").is_file()


def test_never_overwrites(contracts_root):
    assert new_product(contracts_root) == cli.OK
    before = (contracts_root / "contracts/sales/returns.odcs.yaml").read_text() + "# edited\n"
    (contracts_root / "contracts/sales/returns.odcs.yaml").write_text(before)
    assert new_product(contracts_root) == cli.FAILED
    assert (contracts_root / "contracts/sales/returns.odcs.yaml").read_text() == before


def test_unknown_domain_writes_nothing(contracts_root):
    assert cli.main(["-c", CONFIG, "new-product", "--domain", "finance", "--product", "ledger",
                     "--contracts-root", str(contracts_root)], environ={}) == cli.FAILED
    assert not (contracts_root / "contracts/finance").exists()


def test_domain_missing_from_the_registry_prints_the_entry(tmp_path, capsys):
    assert cli.main(["-c", CONFIG, "new-product", "--domain", "sales", "--product", "returns",
                     "--contracts-root", str(tmp_path / "data-contracts")], environ={}) == cli.OK
    out = capsys.readouterr().out
    assert "  - name: sales\n" in out and "dp-sales-stewards" in out


@pytest.mark.parametrize("product", ["Orders", "orders_daily", "-orders", "orders-"])
def test_bad_names_are_refused(product):
    with pytest.raises(TemplateError):
        NewProduct.build("sales", product)


def test_unknown_template_key_is_an_error():
    with pytest.raises(TemplateError, match="unknown template key"):
        NewProduct._render("{{nope}}", {"domain": "sales"}, "x.tmpl")
