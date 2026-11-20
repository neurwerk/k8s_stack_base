"""Configure the stock application before any Docling imports; never log content."""

import json
import logging
import os
import sys


def main():
    # Upstream error messages can contain inference response bodies.
    logging.disable(logging.CRITICAL)
    try:
        with open(os.environ["DOCLING_SERVE_CONFIG_FILE"], encoding="utf-8") as source:
            settings = json.load(source)
        api_key = os.environ["DOCLING_SERVE_API_KEY"]
        if not api_key.strip() or "\r" in api_key or "\n" in api_key:
            raise ValueError("invalid credential")

        from docling_serve.app import create_app
        import uvicorn

        # Upstream configures logging during import. Do not let Uvicorn reset it.
        logging.disable(logging.CRITICAL)
        # A server-wide concurrency cap would reject health probes when busy.
        uvicorn.run(
            create_app, factory=True, host="0.0.0.0", port=5001,
            workers=1, reload=False, log_config=None, access_log=False,
            proxy_headers=False, ssl_certfile="/tls/tls.crt",
            ssl_keyfile="/tls/tls.key", limit_concurrency=None,
            timeout_graceful_shutdown=settings["max_sync_wait"],
            h11_max_incomplete_event_size=16384,
        )
    except Exception:
        print("Docling startup failed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
