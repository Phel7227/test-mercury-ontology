"""Sync pending projects from projects.json into Neo4j and Qdrant.

Each project carries its own structure: client, then buildings, dwellings, rooms and windows.
Status flow per project: pending_sync -> synced, or pending_sync -> sync_failed.
A project is marked synced only after both stores accept it. Failed projects are retried on the next run.
Writes are idempotent (MERGE in Neo4j, upsert by a stable id in Qdrant), so retries are safe.

Window types and glazing are matched to existing nodes from load_neo4j.py, not created here.
Stdlib only. Neo4j credential comes from NEO4J_PASSWORD, as in load_neo4j.py.
Usage: NEO4J_PASSWORD=... python3 -I sync_projects.py
"""
import base64
import hashlib
import json
import os
import pathlib
import sys
import urllib.error
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
REGISTRY = HERE / "projects.json"
NEO4J_URL = "http://localhost:7474"
QDRANT_URL = "http://localhost:6333"
PROJECT_COLLECTION = "mercury_projects"
WINDOW_COLLECTION = "mercury_windows"
PENDING = "pending_sync"

sys.path.insert(0, str(HERE))
from load_qdrant import DIM, embed, request as qdrant_request  # noqa: E402


def stable_point_id(key):
    return int(hashlib.sha256(key.encode("utf-8")).hexdigest()[:12], 16)


def project_statements(project):
    """Cypher for one project, in dependency order. Runs as a single transaction."""
    statements = [{
        "statement": (
            "MERGE (p:Node:Project {id: $id}) SET p.name = $name, p.status = 'synced' "
            "MERGE (c:Node:Client {id: $client}) "
            "MERGE (p)-[:FOR_PROJECT]->(c)"
        ),
        "parameters": {"id": project["id"], "name": project["name"], "client": project["client"]},
    }]
    for building in project.get("buildings", []):
        statements.append({
            "statement": (
                "MATCH (p:Project {id: $project}) "
                "MERGE (b:Node:Building {id: $id}) SET b.name = $name "
                "MERGE (p)-[:HAS_BUILDING]->(b)"
            ),
            "parameters": {"project": project["id"], "id": building["id"], "name": building["label"]},
        })
        for dwelling in building.get("dwellings", []):
            statements.append({
                "statement": (
                    "MATCH (b:Building {id: $building}) "
                    "MERGE (d:Node:Dwelling {id: $id}) SET d.name = $name "
                    "MERGE (b)-[:HAS_DWELLING]->(d)"
                ),
                "parameters": {"building": building["id"], "id": dwelling["id"], "name": dwelling["label"]},
            })
            for room in dwelling.get("rooms", []):
                statements.append({
                    "statement": (
                        "MATCH (d:Dwelling {id: $dwelling}) "
                        "MERGE (r:Node:Room {id: $id}) SET r.name = $name "
                        "MERGE (d)-[:HAS_ROOM]->(r)"
                    ),
                    "parameters": {"dwelling": dwelling["id"], "id": room["id"], "name": room["label"]},
                })
                for window in room.get("windows", []):
                    statements.append({
                        "statement": (
                            "MATCH (r:Room {id: $room}), (wt:WindowType {id: $windowType}), (g:Glazing {id: $glazing}) "
                            "MERGE (w:Node:WindowUnit {id: $id}) "
                            "SET w.name = $name, w.serialNumber = $id, w.widthMm = $width, w.heightMm = $height "
                            "MERGE (r)-[:HAS_OPENING]->(w) "
                            "MERGE (w)-[:OF_WINDOW_TYPE]->(wt) "
                            "MERGE (w)-[:HAS_GLAZING]->(g)"
                        ),
                        "parameters": {
                            "room": room["id"],
                            "id": window["id"],
                            "name": window["label"],
                            "windowType": window["windowType"],
                            "glazing": window["glazing"],
                            "width": window["widthMm"],
                            "height": window["heightMm"],
                        },
                    })
    return statements


def sync_neo4j(project, auth):
    body = json.dumps({"statements": project_statements(project)}).encode("utf-8")
    req = urllib.request.Request(
        f"{NEO4J_URL}/db/neo4j/tx/commit",
        data=body,
        headers={"Content-Type": "application/json", "Authorization": auth},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        result = json.load(resp)
    if result.get("errors"):
        raise RuntimeError(result["errors"])


def ensure_collection(name, distance="Cosine"):
    try:
        qdrant_request("GET", f"{QDRANT_URL}/collections/{name}")
    except urllib.error.HTTPError as err:
        if err.code != 404:
            raise
        qdrant_request("PUT", f"{QDRANT_URL}/collections/{name}", {"vectors": {"size": DIM, "distance": distance}})


def sync_qdrant(project):
    text = f"{project['name']}. client {project['client']}. simulated apartment project."
    qdrant_request("PUT", f"{QDRANT_URL}/collections/{PROJECT_COLLECTION}/points?wait=true", {"points": [{
        "id": stable_point_id(project["id"]),
        "vector": embed(text),
        "payload": {"projectId": project["id"], "name": project["name"], "client": project["client"], "status": "synced"},
    }]})

    points = []
    for building in project.get("buildings", []):
        for dwelling in building.get("dwellings", []):
            for room in dwelling.get("rooms", []):
                for window in room.get("windows", []):
                    text = (
                        f"{window['label']}. {window['windowType']}. {window['glazing']}. "
                        f"{window['widthMm']} by {window['heightMm']} mm."
                    )
                    points.append({
                        "id": stable_point_id(window["id"]),
                        "vector": embed(text),
                        "payload": {
                            "serialNumber": window["id"],
                            "label": window["label"],
                            "windowType": window["windowType"],
                            "glazing": window["glazing"],
                            "widthMm": window["widthMm"],
                            "heightMm": window["heightMm"],
                            "project": project["id"],
                        },
                    })
    if points:
        qdrant_request("PUT", f"{QDRANT_URL}/collections/{WINDOW_COLLECTION}/points?wait=true", {"points": points})


def main():
    secret = os.environ.get("NEO4J_PASSWORD")
    if not secret:
        sys.exit("NEO4J_PASSWORD is not set. Source the private env file first.")
    auth = "Basic " + base64.b64encode(f"neo4j:{secret}".encode("utf-8")).decode("ascii")

    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    pending = [p for p in registry["projects"] if p["status"] in (PENDING, "sync_failed")]
    if not pending:
        print("nothing to sync")
        return

    ensure_collection(PROJECT_COLLECTION)
    ensure_collection(WINDOW_COLLECTION)
    failures = 0
    for project in pending:
        try:
            sync_neo4j(project, auth)
            sync_qdrant(project)
            project["status"] = "synced"
            project.pop("error", None)
            print(f"{project['id']}: synced to Neo4j and Qdrant")
        except Exception as err:  # keep going so one bad project does not block the rest
            project["status"] = "sync_failed"
            project["error"] = str(err)
            failures += 1
            print(f"{project['id']}: sync failed: {err}")
        REGISTRY.write_text(json.dumps(registry, indent=2) + "\n", encoding="utf-8")
    if failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
