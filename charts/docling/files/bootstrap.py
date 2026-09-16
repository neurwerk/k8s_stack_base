"""Configure the stock application before any Docling imports; never log content."""

import json
import logging
import os
import sys


def main():
    logging.disable(logging.CRITICAL)
    try:
        with open(os.environ["DOCLING_SERVE_CONFIG_FILE"], encoding="utf-8") as source:
            settings = json.load(source)
        token = os.environ.pop("DOCLING_INFERENCE_TOKEN")
        api_key = os.environ["DOCLING_SERVE_API_KEY"]
        for value in (token, api_key):
            if not value.strip() or "\r" in value or "\n" in value:
                raise ValueError("invalid credential")
        presets = settings["custom_vlm_presets"]
        presets["default"]["engine_options"]["headers"] = {
            "Authorization": "Bearer " + token
        }
        os.environ["DOCLING_SERVE_CUSTOM_VLM_PRESETS"] = json.dumps(presets)

        from docling_serve.app import create_app
        import uvicorn

        # Upstream configures logging during import. Do not let Uvicorn reset it.
        logging.disable(logging.CRITICAL)
        uvicorn.run(
            create_app, factory=True, host="0.0.0.0", port=5001,
            workers=1, reload=False, log_config=None, access_log=False,
            proxy_headers=False, ssl_certfile="/tls/tls.crt",
            ssl_keyfile="/tls/tls.key", limit_concurrency=8,
            h11_max_incomplete_event_size=16384,
        )
    except Exception:
        print("Docling startup failed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
