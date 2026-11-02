"""Tests for the window installation digital twin. Run: python3 -I -m unittest test_twin -v"""
import json
import tempfile
import unittest
from pathlib import Path

import twin as t


def ev(event_type, window_id="MW-001", at="2026-11-02T08:00:00+00:00"):
    return {"event_id": "x", "type": event_type, "window_id": window_id, "at": at}


class TwinProjectionTests(unittest.TestCase):
    def test_happy_path_reaches_commissioned(self):
        events = [ev(e) for e in [
            "FabricationCompleted", "DeliveredToSite", "InstallationStarted",
            "InstallationCompleted", "InspectionPassed",
        ]]
        tw = t.Twin().replay(events)
        self.assertEqual(tw.states["MW-001"], "commissioned")
        self.assertEqual(tw.rejected, [])

    def test_inspection_failure_goes_to_rework_and_back(self):
        events = [ev(e) for e in [
            "FabricationCompleted", "DeliveredToSite", "InstallationStarted",
            "InstallationCompleted", "InspectionFailed", "ReworkStarted",
            "InstallationCompleted", "InspectionPassed",
        ]]
        tw = t.Twin().replay(events)
        self.assertEqual(tw.states["MW-001"], "commissioned")
        self.assertEqual(len(tw.history["MW-001"]), 8)

    def test_out_of_order_event_is_rejected_not_applied(self):
        tw = t.Twin().replay([ev("InstallationStarted")])
        self.assertEqual(tw.states.get("MW-001", t.START_STATE), "planned")
        self.assertEqual(len(tw.rejected), 1)
        self.assertIn("not allowed from planned", tw.rejected[0]["reason"])

    def test_windows_are_tracked_independently(self):
        tw = t.Twin().replay([ev("FabricationCompleted", "MW-001"), ev("FabricationCompleted", "MW-002")])
        tw.handle(ev("DeliveredToSite", "MW-001"))
        self.assertEqual(tw.states, {"MW-001": "delivered", "MW-002": "fabricated"})


class EventBusTests(unittest.TestCase):
    def test_every_published_event_reaches_all_subscribers(self):
        seen_a, seen_b = [], []
        bus = t.EventBus()
        bus.subscribe(seen_a.append)
        bus.subscribe(seen_b.append)
        bus.publish(ev("FabricationCompleted"))
        self.assertEqual(len(seen_a), 1)
        self.assertEqual(seen_a, seen_b)


class SinkTests(unittest.TestCase):
    def test_sink_receives_only_applied_events_with_resulting_state(self):
        calls = []
        tw = t.Twin()
        bus = t.EventBus()
        bus.subscribe(tw.handle)
        bus.subscribe(t.sink_handler(tw, lambda e, s: calls.append((e["type"], s))))
        bus.publish(ev("InstallationStarted"))  # rejected from planned, must not reach the sink
        bus.publish(ev("FabricationCompleted"))
        self.assertEqual(calls, [("FabricationCompleted", "fabricated")])


class SimulationTests(unittest.TestCase):
    def setUp(self):
        self.window_ids = t.load_window_ids()

    def test_simulation_covers_all_20_windows_with_no_rejections(self):
        self.assertEqual(len(self.window_ids), 20)
        tw = t.Twin().replay(t.simulate(self.window_ids, seed=1))
        self.assertEqual(tw.rejected, [])
        self.assertEqual(set(tw.states), set(self.window_ids))
        self.assertTrue(all(s == "commissioned" for s in tw.states.values()))

    def test_simulation_is_deterministic_for_a_seed(self):
        a = list(t.simulate(self.window_ids, seed=7))
        b = list(t.simulate(self.window_ids, seed=7))
        self.assertEqual(a, b)

    def test_log_round_trip_reproduces_state(self):
        with tempfile.TemporaryDirectory() as d:
            log_path = Path(d) / "events.jsonl"
            live = t.run_simulation(log_path, seed=3)
            replayed = t.Twin().replay(t.EventLog(log_path).read())
            self.assertEqual(live.states, replayed.states)
            lines = log_path.read_text(encoding="utf-8").splitlines()
            expected = list(t.simulate(self.window_ids, seed=3))
            self.assertEqual(len(lines), len(expected))
            self.assertEqual([json.loads(line) for line in lines], expected)


if __name__ == "__main__":
    unittest.main()
