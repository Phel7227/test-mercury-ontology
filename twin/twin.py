"""Minimal event-driven digital twin for window installation states.

Stdlib only. Events are appended to a JSONL log and published on an in-process bus.
The twin subscribes to the bus and projects events into the current state of each window.
Invalid transitions are rejected and recorded, not applied.

Usage:
  python3 -I twin.py simulate [--seed 1] [--log events.jsonl]
  python3 -I twin.py status [--log events.jsonl] [--window MW-001]
"""
import argparse
import collections
import json
import pathlib
import random
from datetime import datetime, timedelta, timezone

HERE = pathlib.Path(__file__).resolve().parent
DATA = HERE.parent / "apartment-project.jsonld"

# event type -> (allowed from-states, resulting state)
TRANSITIONS = {
    "FabricationCompleted": (("planned",), "fabricated"),
    "DeliveredToSite": (("fabricated",), "delivered"),
    "InstallationStarted": (("delivered",), "installing"),
    "InstallationCompleted": (("installing",), "installed"),
    "InspectionPassed": (("installed",), "commissioned"),
    "InspectionFailed": (("installed",), "rework"),
    "ReworkStarted": (("rework",), "installing"),
}

START_STATE = "planned"


class EventBus:
    def __init__(self):
        self._handlers = collections.defaultdict(list)

    def subscribe(self, handler):
        self._handlers["*"].append(handler)

    def publish(self, event):
        for handler in self._handlers["*"]:
            handler(event)


class EventLog:
    """Append-only JSONL log. Also used as the event source for replay."""

    def __init__(self, path):
        self.path = pathlib.Path(path)

    def append(self, event):
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, sort_keys=True) + "\n")

    def read(self):
        if not self.path.exists():
            return []
        with self.path.open(encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]


class Twin:
    """Projection of events into the current installation state of each window."""

    def __init__(self):
        self.states = {}
        self.history = collections.defaultdict(list)
        self.rejected = []

    def handle(self, event):
        window_id = event["window_id"]
        current = self.states.get(window_id, START_STATE)
        allowed_from, new_state = TRANSITIONS[event["type"]]
        if current not in allowed_from:
            self.rejected.append({"event": event, "reason": f"{event['type']} not allowed from {current}"})
            return
        self.states[window_id] = new_state
        self.history[window_id].append((event["at"], new_state))

    def replay(self, events):
        for event in events:
            self.handle(event)
        return self

    def summary(self):
        return collections.Counter(self.states.values())


def load_window_ids():
    doc = json.loads(DATA.read_text(encoding="utf-8"))
    return [n["@id"] for n in doc["@graph"] if n["@type"] == "WindowUnit"]


def simulate(window_ids, seed=1, start=None, fail_rate=0.2):
    """Yield a plausible event sequence for every window, including inspection failures and rework."""
    rng = random.Random(seed)
    clock = start or datetime(2026, 11, 2, 8, 0, tzinfo=timezone.utc)
    seq = 0
    for window_id in window_ids:
        at = clock + timedelta(hours=rng.randint(0, 48))
        steps = ["FabricationCompleted", "DeliveredToSite", "InstallationStarted", "InstallationCompleted"]
        for step in steps:
            at += timedelta(hours=rng.randint(2, 24))
            seq += 1
            yield {"event_id": f"E{seq:05d}", "type": step, "window_id": window_id, "at": at.isoformat()}
        while True:
            at += timedelta(hours=rng.randint(1, 12))
            seq += 1
            if rng.random() < fail_rate:
                yield {"event_id": f"E{seq:05d}", "type": "InspectionFailed", "window_id": window_id, "at": at.isoformat()}
                at += timedelta(hours=rng.randint(2, 24))
                seq += 1
                yield {"event_id": f"E{seq:05d}", "type": "ReworkStarted", "window_id": window_id, "at": at.isoformat()}
                at += timedelta(hours=rng.randint(2, 24))
                seq += 1
                yield {"event_id": f"E{seq:05d}", "type": "InstallationCompleted", "window_id": window_id, "at": at.isoformat()}
            else:
                yield {"event_id": f"E{seq:05d}", "type": "InspectionPassed", "window_id": window_id, "at": at.isoformat()}
                break


def sink_handler(twin, sink):
    """Forward only applied events to a sink, with the window's resulting state. Runs after the twin handler."""
    def handle(event):
        # The twin handler has already run, so an event was rejected if it is the latest rejection.
        rejected = twin.rejected and twin.rejected[-1]["event"] is event
        if not rejected:
            sink(event, twin.states[event["window_id"]])
    return handle


def run_simulation(log_path, seed, sinks=()):
    log = EventLog(log_path)
    twin = Twin()
    bus = EventBus()
    bus.subscribe(twin.handle)
    bus.subscribe(log.append)
    for sink in sinks:
        bus.subscribe(sink_handler(twin, sink))
    for event in simulate(load_window_ids(), seed=seed):
        bus.publish(event)
    return twin


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sim = sub.add_parser("simulate")
    sim.add_argument("--seed", type=int, default=1)
    sim.add_argument("--log", default=str(HERE / "events.jsonl"))
    sim.add_argument("--qdrant", action="store_true", help="push each applied event's state to Qdrant")
    sim.add_argument("--neo4j", action="store_true", help="push each applied event's state to Neo4j")
    stat = sub.add_parser("status")
    stat.add_argument("--log", default=str(HERE / "events.jsonl"))
    stat.add_argument("--window")
    args = parser.parse_args()

    if args.command == "simulate":
        pathlib.Path(args.log).unlink(missing_ok=True)
        sinks = []
        if args.qdrant:
            from sinks import QdrantSink
            sinks.append(QdrantSink())
        if args.neo4j:
            from sinks import Neo4jSink
            sinks.append(Neo4jSink())
        twin = run_simulation(args.log, args.seed, sinks)
        print(f"windows={len(twin.states)} rejected={len(twin.rejected)}")
        for state, count in sorted(twin.summary().items()):
            print(f"  {state}: {count}")
        return

    twin = Twin().replay(EventLog(args.log).read())
    if args.window:
        print(f"{args.window}: {twin.states.get(args.window, START_STATE)}")
        for at, state in twin.history[args.window]:
            print(f"  {at} -> {state}")
        return
    for state, count in sorted(twin.summary().items()):
        print(f"{state}: {count}")


if __name__ == "__main__":
    main()
