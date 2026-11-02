"""Load the simulated window units into Qdrant over the REST API.

Stdlib only. Reads ../apartment-project.jsonld and writes one point per WindowUnit.

Embeddings are a deterministic hashed bag-of-words vector (no model dependency).
They are placeholders for search-plumbing tests, not semantic embeddings.
Usage: python3 -I load_qdrant.py [--url http://localhost:6333] [--collection mercury_windows]
"""
import argparse
import hashlib
import json
import math
import pathlib
import re
import urllib.error
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
DATA = HERE.parent / "apartment-project.jsonld"
DIM = 256


def request(method, url, payload=None):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method=method
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.load(resp)


def embed(text):
    vec = [0.0] * DIM
    for token in re.findall(r"[a-z0-9]+", text.lower()):
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        index = int.from_bytes(digest[:4], "big") % DIM
        sign = 1.0 if digest[4] & 1 else -1.0
        vec[index] += sign
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://localhost:6333")
    parser.add_argument("--collection", default="mercury_windows")
    args = parser.parse_args()

    doc = json.loads(DATA.read_text(encoding="utf-8"))
    by_id = {node["@id"]: node for node in doc["@graph"]}
    windows = [n for n in doc["@graph"] if n["@type"] == "WindowUnit"]

    base = f"{args.url}/collections/{args.collection}"
    # Recreate the collection so reruns don't leave stale points behind.
    try:
        request("DELETE", base)
    except urllib.error.HTTPError:
        pass
    request("PUT", base, {"vectors": {"size": DIM, "distance": "Cosine"}})

    points = []
    for index, window in enumerate(windows, start=1):
        window_type = by_id[window["ofWindowType"]["@id"]]
        glazing = by_id[window["hasGlazing"]["@id"]]
        frame = by_id[window_type["usesFrameSystem"]["@id"]]
        alloy = by_id[frame["usesAlloy"]["@id"]]
        text = (
            f"{window['label']}. {window_type['label']}. {glazing['label']}. "
            f"{window['widthMm']} by {window['heightMm']} mm. "
            f"{frame['label']}. {alloy['label']}."
        )
        points.append({
            "id": index,
            "vector": embed(text),
            "payload": {
                "serialNumber": window["serialNumber"],
                "label": window["label"],
                "windowType": window_type["@id"],
                "glazing": glazing["@id"],
                "frameSystem": frame["@id"],
                "alloy": alloy["@id"],
                "widthMm": window["widthMm"],
                "heightMm": window["heightMm"],
                "project": "TEST-APT-2026-01",
            },
        })

    request("PUT", f"{base}/points?wait=true", {"points": points})
    info = request("GET", base)["result"]
    print(f"collection={args.collection} points={info['points_count']} status={info['status']}")


if __name__ == "__main__":
    main()
