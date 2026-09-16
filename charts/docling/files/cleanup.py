"""Remove native completed results, never documents in another store."""

import os
import ssl
import sys
import urllib.request


def main():
    try:
        key = os.environ["DOCLING_SERVE_API_KEY"]
        if not key.strip() or "\r" in key or "\n" in key:
            raise ValueError("invalid credential")
        retention = int(os.environ["RESULT_RETENTION_SECONDS"])
        if not 0 < retention <= 3600:
            raise ValueError("invalid retention")
        request = urllib.request.Request(
            "https://docling.docling.svc:443/v1/clear/results?older_then=" + str(retention),
            headers={"X-Api-Key": key},
        )
        # Do not forward credentials to redirects or use ambient proxy settings.
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None

        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), NoRedirect(),
            urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile="/tls/ca.crt")),
        )
        with opener.open(request, timeout=20) as response:
            if response.status != 200:
                raise ValueError("cleanup failed")
    except Exception:
        print("Docling result cleanup failed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
