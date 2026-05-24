"""`python -m neuro_research_discovery.web` entry point."""

from __future__ import annotations

import argparse

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m neuro_research_discovery.web")
    parser.add_argument(
        "--host", default="127.0.0.1",
        help="Bind address. Use 0.0.0.0 to serve to your LAN for an event.",
    )
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--reload", action="store_true",
        help="Restart on code change (dev only).",
    )
    args = parser.parse_args()

    uvicorn.run(
        "neuro_research_discovery.web.app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
    )


if __name__ == "__main__":
    main()
