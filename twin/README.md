## Window installation digital twin (prototype)

A minimal, event-driven twin. Each window's state is a projection of events, not a stored record. Stdlib only.

### Model

Each window starts `planned` and moves through the states below. An event is rejected, and not applied, if it arrives from a state that does not allow it.

| Event | From | To |
| --- | --- | --- |
| FabricationCompleted | planned | fabricated |
| DeliveredToSite | fabricated | delivered |
| InstallationStarted | delivered | installing |
| InstallationCompleted | installing | installed |
| InspectionPassed | installed | commissioned |
| InspectionFailed | installed | rework |
| ReworkStarted | rework | installing |

### Pieces

- `EventBus` publishes each event to all subscribers.
- `EventLog` is an append-only JSONL log. Replaying it rebuilds the same state.
- `Twin` projects events into the current state and history of each window.
- `simulate()` generates a seeded event stream for the 20 `WindowUnit`s in `../apartment-project.jsonld`. About 20% of inspections fail and go through rework.

### Usage

```
python3 -I twin.py simulate --seed 1 --log events.jsonl
python3 -I twin.py status --log events.jsonl
python3 -I twin.py status --log events.jsonl --window MW-001
python3 -m unittest test_twin -v
```

`-I` is left out for the tests, since `test_twin` must be importable from the current directory.

### Connected stores

`--qdrant` and `--neo4j` add bus subscribers that write each applied event's state. Neo4j gets `installState` and a `TwinEvent` node per event on each `WindowUnit`. Qdrant gets `installState` on each window's point. Neither sink is needed for the twin to run.

Credentials come only from the environment, never from a file in this folder. Neo4j uses `NEO4J_PASSWORD`. Keep the value in a private env file, e.g. `~/.config/mercury-twin/neo4j.env` (mode 600), and source it before running.

### Full setup

`setup_twins.sh` runs the whole thing once Neo4j and Qdrant are up: it loads the graph and vectors, then runs a seeded simulation into both stores.

```
set -a; . ~/.config/mercury-twin/neo4j.env; set +a
bash setup_twins.sh
```

It exits early if `NEO4J_PASSWORD` is unset or either port is unreachable.

### Limits

- The simulator runs to completion in one pass. It has no time-based snapshot, so a mid-install state is not shown.
- The bus is in-process only. A real deployment would need a broker and persisted subscribers.
- The Qdrant vectors are placeholders (hashed bag-of-words), not semantic embeddings.
