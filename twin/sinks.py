"""Event-bus subscribers that push twin events into the simulated-project stores.

Stdlib only. Matches the stores built by ../db/load_qdrant.py (and ../db/load_neo4j.py for the graph side).
"""
import base64
import json
import os
import urllib.request

CREDENTIAL_ENV = "NEO4J_PASSWORD"


def _post(url, payload, headers=None, method="POST"):
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **(headers or {})},
        method=method,
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def neo4j_statement(event, state):
    """Cypher for one event: set the window's current state and attach the event to it."""
    return {
        "statement": (
            "MATCH (w:WindowUnit {id: $window}) "
            "SET w.installState = $state, w.stateUpdatedAt = $at "
            "MERGE (e:TwinEvent {id: $eventId}) "
            "SET e.type = $type, e.at = $at "
            "MERGE (w)-[:HAS_EVENT]->(e)"
        ),
        "parameters": {
            "window": event["window_id"],
            "state": state,
            "at": event["at"],
            "eventId": event["event_id"],
            "type": event["type"],
        },
    }


class Neo4jSink:
    """Writes each applied event to the graph. Credentials are read from the environment, never from a file."""

    def __init__(self, url="http://localhost:7474", user="neo4j"):
        credential = os.environ.get(CREDENTIAL_ENV)
        if not credential:
            raise RuntimeError(f"{CREDENTIAL_ENV} is not set; source the private env file first")
        token = base64.b64encode(f"{user}:{credential}".encode("utf-8")).decode("ascii")
        self.endpoint = f"{url}/db/neo4j/tx/commit"
        self.headers = {"Authorization": f"Basic {token}"}

    def __call__(self, event, state):
        result = _post(self.endpoint, {"statements": [neo4j_statement(event, state)]}, self.headers)
        if result.get("errors"):
            raise RuntimeError(result["errors"])


class QdrantSink:
    def __init__(self, url="http://localhost:6333", collection="mercury_windows"):
        self.base = f"{url}/collections/{collection}"
        # Point ids were assigned in window order by load_qdrant.py. Map serial numbers to ids from the store.
        points = {}
        offset = None
        while True:
            body = {"limit": 100, "with_payload": ["serialNumber"]}
            if offset is not None:
                body["offset"] = offset
            page = _post(f"{self.base}/points/scroll", body)["result"]
            for point in page["points"]:
                points[point["payload"]["serialNumber"]] = point["id"]
            offset = page.get("next_page_offset")
            if offset is None:
                break
        self.point_ids = points

    def __call__(self, event, state):
        point_id = self.point_ids[event["window_id"]]
        _post(
            f"{self.base}/points/payload?wait=true",
            {"payload": {"installState": state, "stateUpdatedAt": event["at"]}, "points": [point_id]},
        )
