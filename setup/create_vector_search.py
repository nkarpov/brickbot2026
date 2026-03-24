"""Create Vector Search endpoint and FULL_TEXT indexes for session/exhibitor search.

Run once after the Delta tables are populated:
    DATABRICKS_CONFIG_PROFILE=BRICKBOT python setup/create_vector_search.py

Uses REST API directly since databricks-vectorsearch package may not be available locally.
"""

import json
import time
from databricks.sdk import WorkspaceClient

ENDPOINT_NAME = "brickbot-search"
SESSIONS_INDEX = "brickbot2026.rainfocus.sessions_index"
EXHIBITORS_INDEX = "brickbot2026.rainfocus.exhibitors_index"


def api(w, method, path, body=None):
    """Call Vector Search REST API."""
    return w.api_client.do(method, f"/api/2.0/vector-search{path}", body=body)


def main():
    w = WorkspaceClient()

    # Create endpoint
    try:
        ep = api(w, "GET", f"/endpoints/{ENDPOINT_NAME}")
        print(f"Endpoint '{ENDPOINT_NAME}' exists, state: {ep.get('endpoint_status', {}).get('state')}")
    except Exception:
        print(f"Creating endpoint '{ENDPOINT_NAME}'...")
        api(w, "POST", "/endpoints", body={
            "name": ENDPOINT_NAME,
            "endpoint_type": "STORAGE_OPTIMIZED",
        })
        print("Waiting for endpoint to come online...")
        for _ in range(60):
            ep = api(w, "GET", f"/endpoints/{ENDPOINT_NAME}")
            state = ep.get("endpoint_status", {}).get("state", "")
            if state == "ONLINE":
                print("Endpoint is ONLINE.")
                break
            print(f"  State: {state}...")
            time.sleep(10)

    # Create sessions index
    try:
        api(w, "GET", f"/indexes/{SESSIONS_INDEX}")
        print(f"Index '{SESSIONS_INDEX}' already exists.")
    except Exception:
        print(f"Creating sessions index...")
        api(w, "POST", "/indexes", body={
            "name": SESSIONS_INDEX,
            "endpoint_name": ENDPOINT_NAME,
            "primary_key": "session_id",
            "index_type": "DELTA_SYNC",
            "delta_sync_index_spec": {
                "source_table": "brickbot2026.rainfocus.sessions",
                "pipeline_type": "TRIGGERED",
            },
        })
        print("Sessions index created.")

    # Create exhibitors index
    try:
        api(w, "GET", f"/indexes/{EXHIBITORS_INDEX}")
        print(f"Index '{EXHIBITORS_INDEX}' already exists.")
    except Exception:
        print(f"Creating exhibitors index...")
        api(w, "POST", "/indexes", body={
            "name": EXHIBITORS_INDEX,
            "endpoint_name": ENDPOINT_NAME,
            "primary_key": "exhibitor_id",
            "index_type": "DELTA_SYNC",
            "delta_sync_index_spec": {
                "source_table": "brickbot2026.rainfocus.exhibitors",
                "pipeline_type": "TRIGGERED",
            },
        })
        print("Exhibitors index created.")

    print("\nDone! Indexes will sync automatically. Check status in the Databricks UI.")


if __name__ == "__main__":
    main()
