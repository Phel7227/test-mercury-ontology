#!/usr/bin/env bash
# Set up the simulated window digital twins end to end.
#
# Assumes Neo4j (port 7474) and Qdrant (port 6333) are already running.
# Credentials come from the environment, e.g. `set -a; . ~/.config/mercury-twin/neo4j.env; set +a`.
# Steps: load the apartment graph into Neo4j, load window vectors into Qdrant,
# then run a seeded simulation that writes each window's install state to both stores.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
DB_DIR="$HERE/../db"
SEED="${SEED:-1}"

if [ -z "${NEO4J_PASSWORD:-}" ]; then
  echo "NEO4J_PASSWORD is not set. Source the private env file first." >&2
  exit 1
fi

for port in 7474 6333; do
  if ! curl -s -m 3 -o /dev/null "localhost:$port"; then
    echo "Service on port $port is not reachable. Start Neo4j and Qdrant first." >&2
    exit 1
  fi
done

echo "1/3 loading graph into Neo4j"
python3 -I "$DB_DIR/load_neo4j.py"

echo "2/3 loading window vectors into Qdrant"
python3 -I "$DB_DIR/load_qdrant.py"

echo "3/3 simulating installs (seed $SEED) into Neo4j and Qdrant"
cd "$HERE"
python3 twin.py simulate --seed "$SEED" --neo4j --qdrant --log "$HERE/events.jsonl"

echo "setup complete; event log at $HERE/events.jsonl"
