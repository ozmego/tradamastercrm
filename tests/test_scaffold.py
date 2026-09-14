from typer.testing import CliRunner

from tradecrm.cli import app


def test_cli_help() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "TradeCRM" in result.output


def test_settings_defaults() -> None:
    from tradecrm.config import Settings

    settings = Settings(_env_file=None)
    assert settings.trading_enabled is False
    assert settings.dry_run is True
    assert settings.total_capital_allocation_pct == 0.75
