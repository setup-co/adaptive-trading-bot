import json
import logging

from adaptive_trading_bot.logging import configure_logging


def test_structured_logging_initializes(capsys: object) -> None:
    configure_logging("INFO")

    logging.getLogger("adaptive_trading_bot.test").info(
        "foundation ready",
        extra={"component": "test", "context": {"correlation_id": "test-1"}},
    )

    output = capsys.readouterr().err  # type: ignore[attr-defined]
    record = json.loads(output)
    assert record["level"] == "INFO"
    assert record["logger"] == "adaptive_trading_bot.test"
    assert record["component"] == "test"
    assert record["message"] == "foundation ready"
    assert record["context"] == {"correlation_id": "test-1"}
    assert record["timestamp"].endswith("+00:00")
