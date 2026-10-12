import asyncio

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.session import wait_for_db_and_create_schema
from app.ingest.service import ingest


async def main() -> None:
    cfg = get_settings()
    configure_logging(cfg.log_level, cfg.log_json)
    await wait_for_db_and_create_schema()
    print(await ingest())


if __name__ == "__main__":
    asyncio.run(main())
