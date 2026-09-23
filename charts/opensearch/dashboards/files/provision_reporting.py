import json
import os
import time
from pathlib import Path

import requests


BASE_URL = os.environ.get("DASHBOARDS_URL", "http://monitor-opensearch-dashboards:5601")
DATA_SOURCE_NAME = os.environ.get("DATA_SOURCE_NAME", "neurwerk-prometheus")
DATA_SOURCE_URI = os.environ.get("DATA_SOURCE_URI", "")
READER_ROLE = os.environ.get("DATA_SOURCE_READER_ROLE", "")
DASHBOARD_FILE = Path(os.environ.get("DASHBOARD_FILE", "/dashboard/dashboard.ndjson"))
PLACEHOLDER = "__PROMETHEUS_DATA_CONNECTION_ID__"


def checked_json(response):
    response.raise_for_status()
    try:
        body = response.json()
    except ValueError as exc:
        raise RuntimeError(f"{response.request.method} {response.url} returned non-JSON") from exc
    if isinstance(body, dict):
        if body.get("success") is False or body.get("error") or body.get("errors"):
            raise RuntimeError(f"{response.request.method} {response.url} failed: {body}")
        if isinstance(body.get("statusCode"), int) and body["statusCode"] >= 400:
            raise RuntimeError(f"{response.request.method} {response.url} failed: {body}")
    return body


def request_json(session, method, path, **kwargs):
    return checked_json(session.request(method, f"{BASE_URL}{path}", timeout=30, **kwargs))


def matching_saved_objects(session):
    body = request_json(
        session,
        "GET",
        "/api/saved_objects/_find?type=data-connection&per_page=10000",
    )
    return [
        item
        for item in body.get("saved_objects", [])
        if item.get("attributes", {}).get("connectionId") == DATA_SOURCE_NAME
        and item.get("attributes", {}).get("type") == "Prometheus"
    ]


def get_backend(session):
    response = session.get(
        f"{BASE_URL}/api/directquery/dataconnections/{DATA_SOURCE_NAME}", timeout=30
    )
    if response.status_code == 404:
        return None
    return checked_json(response)


def backend_matches(body):
    properties = body.get("properties", {})
    return (
        body.get("name") == DATA_SOURCE_NAME
        and str(body.get("connector", "")).lower() == "prometheus"
        and properties.get("prometheus.uri") == DATA_SOURCE_URI
        and sorted(body.get("allowedRoles", [])) == [READER_ROLE]
    )


def create_backend(session):
    request_json(
        session,
        "POST",
        "/api/directquery/dataconnections",
        json={
            "name": DATA_SOURCE_NAME,
            "connector": "prometheus",
            "allowedRoles": [READER_ROLE],
            "properties": {"prometheus.uri": DATA_SOURCE_URI},
        },
    )


def delete_data_source(session):
    request_json(
        session,
        "DELETE",
        f"/api/directquery/dataconnections/{DATA_SOURCE_NAME}",
    )


def converge_data_source(session):
    backend = get_backend(session)
    saved_objects = matching_saved_objects(session)
    if backend is not None and (not backend_matches(backend) or len(saved_objects) != 1):
        delete_data_source(session)
        backend = None
        saved_objects = matching_saved_objects(session)
    if backend is None:
        # The Dashboards endpoint removes one matching saved object even when the
        # backend is already absent. Clear stale duplicates before recreating both.
        for _ in range(len(saved_objects)):
            delete_data_source(session)
            saved_objects = matching_saved_objects(session)
        if saved_objects:
            raise RuntimeError(f"could not remove stale saved objects for {DATA_SOURCE_NAME}")
        create_backend(session)

    saved_objects = matching_saved_objects(session)
    if len(saved_objects) != 1:
        raise RuntimeError(
            f"expected exactly one data-connection saved object for {DATA_SOURCE_NAME}, "
            f"found {len(saved_objects)}"
        )
    return saved_objects[0]["id"]


def import_dashboard(session, data_connection_id):
    dashboard = DASHBOARD_FILE.read_text(encoding="utf-8")
    if PLACEHOLDER not in dashboard:
        raise RuntimeError(f"dashboard does not contain {PLACEHOLDER}")
    dashboard = dashboard.replace(PLACEHOLDER, data_connection_id)
    request_json(
        session,
        "POST",
        "/api/saved_objects/_import?overwrite=true",
        files={"file": ("dashboard.ndjson", dashboard, "application/ndjson")},
    )


def main():
    if not DATA_SOURCE_URI or not READER_ROLE:
        raise RuntimeError("DATA_SOURCE_URI and DATA_SOURCE_READER_ROLE are required")

    session = requests.Session()
    session.auth = (os.environ["OPENSEARCH_USERNAME"], os.environ["OPENSEARCH_PASSWORD"])
    session.headers.update({"osd-xsrf": "true"})

    for attempt in range(60):
        try:
            response = session.get(f"{BASE_URL}/api/status", timeout=10)
            if response.status_code < 500:
                break
        except requests.RequestException:
            pass
        if attempt == 59:
            raise RuntimeError("OpenSearch Dashboards did not become ready")
        time.sleep(5)

    import_dashboard(session, converge_data_source(session))


if __name__ == "__main__":
    main()
