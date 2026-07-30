import uvicorn

from mafia.config import get_settings


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "mafia.main:app",
        host=settings.api_host,
        port=settings.api_port,
        workers=settings.api_workers,
        proxy_headers=True,
    )


if __name__ == "__main__":
    main()
