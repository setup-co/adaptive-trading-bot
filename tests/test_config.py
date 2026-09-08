from adaptive_trading_bot.config import Environment, Settings


def test_live_trading_defaults_to_disabled() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.live_trading_enabled is False


def test_environment_variables_override_defaults(monkeypatch: object) -> None:
    # pytest's fixture is intentionally duck-typed here to keep tests free of plugin types.
    monkeypatch.setenv("ATB_ENVIRONMENT", "test")  # type: ignore[attr-defined]
    monkeypatch.setenv("ATB_API_PORT", "9001")  # type: ignore[attr-defined]
    monkeypatch.setenv("ATB_LIVE_TRADING_ENABLED", "true")  # type: ignore[attr-defined]

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.environment is Environment.TEST
    assert settings.api_port == 9001
    assert settings.live_trading_enabled is True
