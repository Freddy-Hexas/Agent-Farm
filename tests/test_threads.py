import tempfile
import unittest
from pathlib import Path

from agent_farm.threads import ThreadStore


class ThreadStoreTests(unittest.TestCase):
    def test_persists_turn_items_and_ordered_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "threads"
            store = ThreadStore(root)
            thread = store.create("  Build   the settings center  ")
            turn = store.start_turn(thread["thread_id"], "Implement the feature.")
            item = store.add_item(
                thread["thread_id"],
                turn["turn_id"],
                "supervisor_plan",
                status="running",
                payload={"request": "Implement the feature."},
            )
            store.update_item(
                thread["thread_id"],
                turn["turn_id"],
                item["item_id"],
                status="completed",
                payload={"plan": {"task_id": "settings"}},
            )
            store.update_turn(thread["thread_id"], turn["turn_id"], "awaiting_confirmation")

            reloaded = ThreadStore(root)
            saved = reloaded.read(thread["thread_id"])
            events = reloaded.events(thread["thread_id"], after=2)

            self.assertEqual(saved["title"], "Build the settings center")
            self.assertEqual(saved["status"], "awaiting_confirmation")
            self.assertEqual(saved["turns"][0]["items"][1]["payload"]["plan"]["task_id"], "settings")
            self.assertTrue(events)
            self.assertEqual(
                [event["sequence"] for event in saved["events"]],
                list(range(1, len(saved["events"]) + 1)),
            )
            self.assertEqual(reloaded.list()[0]["thread_id"], thread["thread_id"])

    def test_finds_thread_linked_to_completed_farm(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ThreadStore(Path(tmp) / "threads")
            thread = store.create("Run workers")
            turn = store.start_turn(thread["thread_id"], "Run the plan.")
            store.add_item(
                thread["thread_id"],
                turn["turn_id"],
                "farm_run",
                status="completed",
                payload={"farm_id": "farm-123"},
            )

            self.assertEqual(
                store.find_by_farm("farm-123"),
                (thread["thread_id"], turn["turn_id"]),
            )
            self.assertIsNone(store.find_by_farm("missing"))


if __name__ == "__main__":
    unittest.main()
