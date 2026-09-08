from alembic.config import Config
from alembic.script import ScriptDirectory

from adaptive_trading_bot.config import Settings
from adaptive_trading_bot.database import Base, create_engine


async def test_postgresql_engine_lifecycle_is_initialized_without_connecting() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    engine = create_engine(settings)

    assert engine.url.drivername == "postgresql+asyncpg"
    assert not Base.metadata.tables

    await engine.dispose()


def test_alembic_script_directory_initializes() -> None:
    script = ScriptDirectory.from_config(Config("alembic.ini"))

    assert script.get_heads() == []
