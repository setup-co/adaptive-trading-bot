from httpx import ASGITransport, AsyncClient

from adaptive_trading_bot.api import create_app
from adaptive_trading_bot.config import Settings


async def test_application_boots_and_health_is_truthful() -> None:
    app = create_app(Settings(_env_file=None))  # type: ignore[call-arg]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "adaptive-trading-bot"}
    assert "database" not in response.json()
    assert "exchange" not in response.json()
