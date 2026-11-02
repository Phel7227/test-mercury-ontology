"""Load the simulated apartment project into Neo4j over the HTTP transaction API.

Stdlib only (no driver dependency). Reads ../apartment-project.jsonld.
Usage: NEO4J_PASSWORD=... python3 -I load_neo4j.py [--url http://localhost:7474] [--user neo4j]
"""
import argparse
import base64
import json
import os
import pathlib
import re
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
DATA = HERE.parent / "apartment-project.jsonld"


def run_statements(url, auth, statements):
    body = json.dumps({"statements": statements}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "Authorization": auth},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        result = json.load(resp)
    if result.get("errors"):
        raise RuntimeError(result["errors"])
    return result


def rel_type(prop):
    # camelCase ontology property -> UPPER_SNAKE relationship type, e.g. hasDwelling -> HAS_DWELLING
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", prop).upper()


def short_id(value):
    # @id values are either short ("MW-001") or full IRIs; keep the last segment for the project root.
    return value.rsplit("/", 1)[-1] if value.startswith("http") else value


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://localhost:7474")
    parser.add_argument("--user", default="neo4j")
    args = parser.parse_args()
    password = os.environ["NEO4J_PASSWORD"]
    auth = "Basic " + base64.b64encode(f"{args.user}:{password}".encode()).decode()
    endpoint = f"{args.url}/db/neo4j/tx/commit"

    doc = json.loads(DATA.read_text(encoding="utf-8"))
    # The project root is the document itself; the rest live in @graph.
    root = {k: v for k, v in doc.items() if k != "@graph"}
    nodes = [root] + doc["@graph"]

    # Pass 1: nodes with scalar properties. Every node gets the :Node label plus its ontology type.
    node_statements = []
    rel_statements = []
    for node in nodes:
        node_id = short_id(node["@id"])
        node_type = node["@type"]
        props = {"id": node_id, "name": node.get("label", node_id)}
        for key, value in node.items():
            if key in ("@id", "@type", "label"):
                continue
            if isinstance(value, (str, int, float, bool)):
                props[key] = value
        node_statements.append({
            "statement": f"MERGE (n:Node:{node_type} {{id: $id}}) SET n += $props",
            "parameters": {"id": node_id, "props": props},
        })

        # Pass 2 inputs: object-valued properties become relationships to nodes that exist by id.
        for key, value in node.items():
            targets = value if isinstance(value, list) else [value]
            for target in targets:
                if isinstance(target, dict) and "@id" in target:
                    rel_statements.append({
                        "statement": (
                            f"MATCH (a:Node {{id: $from}}), (b:Node {{id: $to}}) "
                            f"MERGE (a)-[:{rel_type(key)}]->(b)"
                        ),
                        "parameters": {"from": node_id, "to": short_id(target["@id"])},
                    })

    # Start from an empty graph so reruns don't leave stale nodes or relationship types behind.
    run_statements(endpoint, auth, [{"statement": "MATCH (n:Node) DETACH DELETE n"}])
    run_statements(endpoint, auth, node_statements)
    run_statements(endpoint, auth, rel_statements)

    counts = run_statements(endpoint, auth, [{
        "statement": "MATCH (w:WindowUnit) WITH count(w) AS windows "
                     "MATCH ()-[r]->() WITH windows, count(r) AS rels "
                     "MATCH (n:Node) RETURN windows, rels, count(n) AS nodes",
    }])
    row = counts["results"][0]["data"][0]["row"]
    print(f"windows={row[0]} relationships={row[1]} nodes={row[2]}")


if __name__ == "__main__":
    main()
