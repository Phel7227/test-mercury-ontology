## Simulated project databases

Both loaders read `../apartment-project.jsonld` (the simulated project, 20 aluminium window units) and use only the Python standard library.

### Neo4j

- Install: `brew install neo4j` (2026.10.1). Initial password is set with `neo4j-admin dbms set-initial-password`, before first start. This is a local test password only.
- Start: `neo4j start` (HTTP on port 7474). Stop: `neo4j stop`.
- Load: `NEO4J_PASSWORD=... python3 -I load_neo4j.py`. This clears `:Node` and reloads everything, so reruns are safe.
- Result: 56 nodes and 110 relationships. Relationship types are UPPER_SNAKE, e.g. `HAS_DWELLING`, `OF_WINDOW_TYPE`.

Example query:

```cypher
MATCH (w:WindowUnit)-[:OF_WINDOW_TYPE]->(:WindowType)-[:USES_FRAME_SYSTEM]->(:FrameSystem)-[:USES_ALLOY]->(a)
RETURN w.serialNumber, a.id
```

### Qdrant

- Install: the official `qdrant-aarch64-apple-darwin.tar.gz` release (v1.20.0). It is not in Homebrew. The archive's SHA-256 matched the GitHub release digest. The binary and its storage are in the workdir's `qdrant-dl/` directory, not in this folder.
- Start: `./qdrant` from `qdrant-dl/` (REST on port 6333).
- Load: `python3 -I load_qdrant.py`. It recreates the `mercury_windows` collection with 256-dimensional Cosine vectors and one point per window unit.

The embeddings are a hashed bag-of-words. They are placeholders for testing the search plumbing, not semantic embeddings. Replace `embed()` with a real model once one is approved, since adding one is a dependency decision.
