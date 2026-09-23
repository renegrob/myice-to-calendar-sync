"""Tests for sync_state load/save/prune (local-file path)."""
import tempfile
import unittest
from datetime import date
from pathlib import Path

import sync_state


class PrunePast(unittest.TestCase):
    def test_drops_entries_before_today(self):
        state = {
            "synced": {"a": {"date": "2020-01-01", "summary": "old"},
                       "b": {"date": "2099-01-01", "summary": "future"}},
            "tombstones": {"c": {"date": "2020-06-01", "summary": "old-ts"}},
        }
        sync_state.prune_past(state, date(2026, 1, 1))
        self.assertNotIn("a", state["synced"])
        self.assertIn("b", state["synced"])
        self.assertNotIn("c", state["tombstones"])


class LocalRoundTrip(unittest.TestCase):
    def test_missing_file_loads_empty(self):
        with tempfile.TemporaryDirectory() as d:
            state = sync_state.load(Path(d) / "nope.json")
            self.assertEqual(state, {"synced": {}, "tombstones": {}})

    def test_save_then_load_roundtrips(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "state.json"
            state = {"synced": {"a": {"date": "2099-01-01", "summary": "keep"}},
                     "tombstones": {}}
            sync_state.save(path, state, today=date(2026, 1, 1))
            self.assertEqual(sync_state.load(path), state)


if __name__ == "__main__":
    unittest.main()
