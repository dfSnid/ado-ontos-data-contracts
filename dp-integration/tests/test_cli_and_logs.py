import io
import logging

from dp_integration import cli, logs

from conftest import ROOT


def test_issue_handler_escapes_and_points_at_the_line():
    out = io.StringIO()
    logger = logs.configure("INFO", annotate=False, stream=io.StringIO())
    handler = logs.AzureDevOpsIssueHandler(stream=out)
    logger.addHandler(handler)
    logging.getLogger("dp_integration.test").error("bad ] value\n100%", extra={"source": "a;b.yaml", "line": 7})
    logging.getLogger("dp_integration.test").info("not annotated")
    logger.removeHandler(handler)
    assert out.getvalue() == "##vso[task.logissue type=error;sourcepath=a%3Bb.yaml;linenumber=7]bad %5D value%0A100%AZP25\n"


def test_console_format_includes_the_source():
    stream = io.StringIO()
    logs.configure("INFO", annotate=False, stream=stream)
    logging.getLogger("dp_integration.test").warning("missing", extra={"source": "c.yaml", "line": 3})
    assert stream.getvalue() == "WARNING c.yaml:3: missing\n"


def test_options_before_and_after_the_command():
    parser = cli.build_parser()
    assert parser.parse_args(["-e", "dev", "config", "get", "x"]).environment == "dev"
    assert parser.parse_args(["config", "get", "x", "-e", "dev"]).environment == "dev"


def test_config_get(capsys):
    code = cli.main(["-c", str(ROOT / "config"), "-e", "dev", "config", "get", "env.contractServer"], environ={})
    assert code == cli.OK and capsys.readouterr().out == "dev\n"


def test_config_tag_values(capsys):
    assert cli.main(["-c", str(ROOT / "config"), "config", "tag-values", "tier"], environ={}) == cli.OK
    assert capsys.readouterr().out == "domain\nenterprise\n"


def test_config_error_exit_code():
    assert cli.main(["-c", str(ROOT / "config"), "-e", "missing", "config", "get", "x"], environ={}) == cli.CONFIG_ERROR


def test_check_mandatory_exit_code(write_contract):
    assert cli.main(["-c", str(ROOT / "config"), "check-mandatory", str(write_contract())], environ={}) == cli.FAILED


def test_context_prefers_the_ontos_token(settings):
    from dp_integration.ontos import StaticToken, WorkspaceTokenExchange
    assert isinstance(cli.CommandContext(settings, environ={"ONTOS_TOKEN": "t"}).ontos_token, StaticToken)
    ctx = cli.CommandContext(settings, environ={"DATABRICKS_TOKEN": "entra"})
    assert isinstance(ctx.ontos_token, WorkspaceTokenExchange)


def test_unreadable_contract_fails_but_others_run(settings, write_contract, tmp_path):
    good = write_contract()
    ctx = cli.CommandContext(settings, environ={})
    seen = []
    code = cli._each([str(tmp_path / "missing.odcs.yaml"), str(good)], ctx, "x", lambda c: seen.append(c.id) or True)
    assert code == cli.FAILED and seen == ["sample-product"]


def test_config_get_works_before_ontos_exists(tmp_path, capsys):
    config = tmp_path / "config"
    (config / "environments").mkdir(parents=True)
    (config / "platform.yaml").write_text((ROOT / "config/platform.yaml").read_text())
    (config / "environments/new.yml").write_text("variables:\n  databricksHost: h\n  warehouseId: w\n"
                                                  "  ontosUrl: <printed by ontos.sh>\n")
    assert cli.main(["-c", str(config), "-e", "new", "config", "get", "env.warehouseId"], environ={}) == cli.OK
    assert capsys.readouterr().out == "w\n"
    assert cli.main(["-c", str(config), "-e", "new", "config", "get", "env.ontosUrl"], environ={}) == cli.CONFIG_ERROR
    assert cli.main(["-c", str(config), "-e", "new", "config", "get", "env.nope"], environ={}) == cli.CONFIG_ERROR
    assert cli.main(["-c", str(config), "-e", "new", "ontos-check"], environ={}) == cli.CONFIG_ERROR


def test_bad_yaml_contract_fails_but_others_run(settings, write_contract, tmp_path):
    good = write_contract()
    bad = tmp_path / "bad.odcs.yaml"
    bad.write_text("- not a mapping\n")
    seen = []
    code = cli._each([str(bad), str(good)], cli.CommandContext(settings, environ={}), "x",
                     lambda c: seen.append(c.id) or True)
    assert code == cli.FAILED and seen == ["sample-product"]


def test_check_mandatory_carries_on_after_a_bad_file(write_contract, tmp_path, capsys):
    missing = tmp_path / "missing.odcs.yaml"
    code = cli.main(["-c", str(ROOT / "config"), "check-mandatory", str(missing), str(write_contract())], environ={})
    err = capsys.readouterr().err
    assert code == cli.FAILED
    assert "cannot read contract" in err and "missing a named Data Steward" in err


def test_config_get_does_not_load_the_sdk():
    import subprocess
    import sys
    code = "import sys, dp_integration.cli; print('databricks.sdk' in sys.modules)"
    assert subprocess.run([sys.executable, "-c", code], capture_output=True, text=True).stdout == "False\n"
