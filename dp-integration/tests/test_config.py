import json

import pytest
import yaml

from dp_integration.config import ConfigError, Environment, Settings, TagPolicy, snake_case

from conftest import ENV, ROOT


def test_snake_case():
    assert snake_case("databricksHost") == "databricks_host"
    assert snake_case("warehouse_id") == "warehouse_id"


def test_load_reads_platform_and_environment(settings):
    assert settings.tags.key("tier") == "dp_tier"
    assert settings.env.name == "dev"
    assert settings.env.workspace_url.startswith("https://")
    assert settings.env["ontosAppName"] == settings.env["ontos_app_name"]
    assert settings.contract.mandatory_schema.is_file()


def test_environment_variables_override_the_file():
    s = Settings.load(ROOT / "config", "dev", overrides=dict(ENV, DP_WAREHOUSE_ID="abc", DP_LOG_LEVEL="debug"))
    assert s.env.warehouse_id == "abc"
    assert s.log_level == "DEBUG"


def test_environment_from_DP_ENVIRONMENT():
    assert Settings.load(ROOT / "config", overrides=dict(ENV, DP_ENVIRONMENT="dev")).env.name == "dev"


def test_no_environment_is_an_error_only_when_used():
    s = Settings.load(ROOT / "config", overrides={})
    assert s.tags.prefix == "dp_"
    with pytest.raises(ConfigError, match="no environment selected"):
        s.env


def test_missing_required_value(tmp_path):
    (tmp_path / "x.yml").write_text("variables:\n  databricksHost: h\n  warehouseId: w\n")
    with pytest.raises(ConfigError, match="ontos_url"):
        Environment.from_file(tmp_path / "x.yml", overrides={})
    env = Environment.from_file(tmp_path / "x.yml", overrides={"DP_ONTOS_URL": "https://o"})
    assert env.ontos_url == "https://o"


def test_bad_log_level():
    with pytest.raises(ConfigError, match="logging.level"):
        Settings.load(ROOT / "config", overrides={"DP_LOG_LEVEL": "LOUD"})


def test_get_by_dotted_path(settings):
    assert settings.get("env.warehouseId") == settings.env.warehouse_id
    assert settings.get("tags.tier_values.2") == "domain"
    assert settings.get("ontos.publisher_role.group") == "dp-publishers"
    with pytest.raises(ConfigError):
        settings.get("tags.nope")


def test_tag_policy_rejects_bad_keys():
    with pytest.raises(ConfigError, match="missing"):
        TagPolicy(prefix="", keys={"tier": "tier"}, tier_values={}, status_values={},
                  domain_values=(), sensitivity_values=())
    with pytest.raises(ConfigError, match="invalid"):
        TagPolicy(prefix="a b", keys={k: k for k in TagPolicy.LOGICAL}, tier_values={}, status_values={},
                  domain_values=(), sensitivity_values=())


def test_example_environment_has_every_dev_value():
    dev = yaml.safe_load((ROOT / "config/environments/dev.yml").read_text())
    example = yaml.safe_load((ROOT / "config/environments/example.yml").read_text())
    assert set(example["variables"]) == set(dev["variables"])


def test_schema_enums_match_platform_tags(settings):
    """The CI check and the publish job must agree on the allowed domain and sensitivity values."""
    schema = json.loads(settings.contract.mandatory_schema.read_text())
    text = json.dumps(schema)
    assert schema["properties"]["domain"]["enum"] == list(settings.tags.domain_values)
    assert json.dumps(list(settings.tags.sensitivity_values)) in text


def test_every_contract_status_has_a_tag_value(settings):
    schema = json.loads(settings.contract.mandatory_schema.read_text())
    assert set(schema["properties"]["status"]["enum"]) <= set(settings.tags.status_values)


def test_placeholder_counts_as_unset(tmp_path):
    (tmp_path / "x.yml").write_text("variables:\n  databricksHost: h\n  warehouseId: w\n"
                                    "  ontosUrl: <https://ontos>\n  publisherClientId: <printed later>\n")
    with pytest.raises(ConfigError, match="placeholder.*ontos_url"):
        Environment.from_file(tmp_path / "x.yml", overrides={})
    env = Environment.from_file(tmp_path / "x.yml", overrides={}, required=())
    assert env.ontos_url == "" and env["publisherClientId"] == ""


def test_host_must_be_a_host_name(tmp_path):
    (tmp_path / "x.yml").write_text("variables:\n  databricksHost: https://h\n  warehouseId: w\n  ontosUrl: o\n")
    with pytest.raises(ConfigError, match="host name"):
        Environment.from_file(tmp_path / "x.yml", overrides={})


def test_get_rejects_unset_and_unknown_environment_values(tmp_path):
    config = tmp_path / "config"
    (config / "environments").mkdir(parents=True)
    (config / "platform.yaml").write_text((ROOT / "config/platform.yaml").read_text())
    (config / "environments/new.yml").write_text("variables:\n  databricksHost: h\n  warehouseId: w\n"
                                                  "  ontosUrl: ''\n  publisherClientId: <printed later>\n")
    s = Settings.load(config, "new", overrides={}, required=())
    assert s.get("env.warehouseId") == "w"
    assert s.get("env.workspaceUrl") == "https://h"
    with pytest.raises(ConfigError, match="placeholder"):
        s.get("env.publisherClientId")
    with pytest.raises(ConfigError, match="placeholder"):
        s.get("env.ontosUrl")
    with pytest.raises(ConfigError, match="no setting"):
        s.get("env.nope")
