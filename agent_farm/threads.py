from __future__ import annotations

import copy
import json
import os
import re
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


THREAD_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{7,63}$")
TURN_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{7,63}$")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_title(value: str) -> str:
    title = " ".join(value.strip().split())
    if not title:
        return "New task"
    return title[:160]


class ThreadStore:
    """Small, durable Thread/Turn/Item store for the local desktop runtime."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def _path(self, thread_id: str) -> Path:
        if not THREAD_ID_PATTERN.fullmatch(thread_id):
            raise ValueError("Invalid thread id.")
        return self.root / f"{thread_id}.json"

    def _read_unlocked(self, thread_id: str) -> dict[str, Any]:
        path = self._path(thread_id)
        if not path.exists():
            raise FileNotFoundError(f"Unknown thread: {thread_id}")
        loaded = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(loaded, dict):
            raise ValueError(f"Invalid thread file: {thread_id}")
        return loaded

    def _write_unlocked(self, thread: dict[str, Any]) -> None:
        path = self._path(thread["thread_id"])
        handle, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=self.root
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
                json.dump(thread, stream, indent=2, ensure_ascii=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_path, path)
        finally:
            if temporary_path.exists():
                temporary_path.unlink()

    def _mutate(
        self,
        thread_id: str,
        operation: Callable[[dict[str, Any]], Any],
    ) -> tuple[dict[str, Any], Any]:
        with self._lock:
            thread = self._read_unlocked(thread_id)
            result = operation(thread)
            thread["updated_at"] = _utc_now()
            self._write_unlocked(thread)
            return copy.deepcopy(thread), copy.deepcopy(result)

    @staticmethod
    def _event(
        thread: dict[str, Any],
        event_type: str,
        *,
        turn_id: str | None = None,
        item_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        events = thread.setdefault("events", [])
        event = {
            "schema_version": 1,
            "sequence": (events[-1]["sequence"] + 1) if events else 1,
            "type": event_type,
            "created_at": _utc_now(),
            "turn_id": turn_id,
            "item_id": item_id,
            "payload": payload or {},
        }
        events.append(event)
        return event

    def create(self, title: str) -> dict[str, Any]:
        now = _utc_now()
        thread = {
            "schema_version": 1,
            "thread_id": f"thread-{uuid.uuid4().hex[:16]}",
            "title": _clean_title(title),
            "status": "idle",
            "archived": False,
            "created_at": now,
            "updated_at": now,
            "turns": [],
            "events": [],
        }
        self._event(thread, "thread/started")
        with self._lock:
            self._write_unlocked(thread)
        return copy.deepcopy(thread)

    def read(self, thread_id: str) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._read_unlocked(thread_id))

    def rename(self, thread_id: str, title: str) -> dict[str, Any]:
        def operation(thread: dict[str, Any]) -> None:
            thread["title"] = _clean_title(title)
            self._event(thread, "thread/renamed", payload={"title": thread["title"]})

        thread, _ = self._mutate(thread_id, operation)
        return thread

    def archive(self, thread_id: str, *, archived: bool = True) -> dict[str, Any]:
        def operation(thread: dict[str, Any]) -> None:
            thread["archived"] = bool(archived)
            self._event(thread, "thread/archived" if archived else "thread/resumed")

        thread, _ = self._mutate(thread_id, operation)
        return thread

    def delete(self, thread_id: str) -> None:
        with self._lock:
            thread = self._read_unlocked(thread_id)
            if thread.get("status") in {"planning", "queued", "running", "cancelling"}:
                raise ValueError("An active thread cannot be deleted. Cancel it first.")
            self._path(thread_id).unlink()

    def fork(self, thread_id: str, *, turn_id: str | None = None) -> dict[str, Any]:
        with self._lock:
            source = self._read_unlocked(thread_id)
            turns = list(source.get("turns") or [])
            if turn_id is not None:
                index = next(
                    (index for index, turn in enumerate(turns) if turn.get("turn_id") == turn_id),
                    None,
                )
                if index is None:
                    raise FileNotFoundError(f"Unknown turn: {turn_id}")
                turns = turns[: index + 1]
            now = _utc_now()
            forked = copy.deepcopy(source)
            forked["thread_id"] = f"thread-{uuid.uuid4().hex[:16]}"
            forked["title"] = _clean_title(f"{source.get('title', 'New task')} (fork)")
            forked["status"] = "idle"
            forked["archived"] = False
            forked["created_at"] = now
            forked["updated_at"] = now
            forked["turns"] = turns
            forked["events"] = []
            self._event(
                forked,
                "thread/forked",
                payload={"source_thread_id": thread_id, "source_turn_id": turn_id},
            )
            self._write_unlocked(forked)
            return copy.deepcopy(forked)

    def list(self, *, include_archived: bool = False) -> list[dict[str, Any]]:
        with self._lock:
            summaries: list[dict[str, Any]] = []
            for path in self.root.glob("thread-*.json"):
                try:
                    thread = json.loads(path.read_text(encoding="utf-8-sig"))
                except (OSError, ValueError, json.JSONDecodeError):
                    continue
                if not isinstance(thread, dict):
                    continue
                if thread.get("archived") and not include_archived:
                    continue
                turns = thread.get("turns") if isinstance(thread.get("turns"), list) else []
                summaries.append(
                    {
                        "schema_version": 1,
                        "thread_id": thread.get("thread_id"),
                        "title": thread.get("title", "New task"),
                        "status": thread.get("status", "idle"),
                        "created_at": thread.get("created_at"),
                        "updated_at": thread.get("updated_at"),
                        "turn_count": len(turns),
                    }
                )
            return sorted(summaries, key=lambda item: item.get("updated_at") or "", reverse=True)

    def start_turn(
        self,
        thread_id: str,
        user_message: str,
        *,
        attachments: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        message = user_message.strip()
        if not message:
            raise ValueError("A turn requires a non-empty user message.")
        if len(message) > 100_000:
            raise ValueError("User message is too large.")

        def operation(thread: dict[str, Any]) -> dict[str, Any]:
            turn_id = f"turn-{uuid.uuid4().hex[:16]}"
            item_id = f"item-{uuid.uuid4().hex[:16]}"
            now = _utc_now()
            turn = {
                "schema_version": 1,
                "turn_id": turn_id,
                "status": "planning",
                "created_at": now,
                "completed_at": None,
                "items": [
                    {
                        "schema_version": 1,
                        "item_id": item_id,
                        "type": "user_message",
                        "status": "completed",
                        "created_at": now,
                        "updated_at": now,
                        "payload": {
                            "text": message,
                            "attachments": list(attachments or []),
                        },
                    }
                ],
            }
            thread.setdefault("turns", []).append(turn)
            thread["status"] = "planning"
            self._event(thread, "turn/started", turn_id=turn_id)
            self._event(thread, "item/completed", turn_id=turn_id, item_id=item_id)
            return turn

        _, turn = self._mutate(thread_id, operation)
        return turn

    def add_item(
        self,
        thread_id: str,
        turn_id: str,
        item_type: str,
        *,
        status: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not TURN_ID_PATTERN.fullmatch(turn_id):
            raise ValueError("Invalid turn id.")
        if not item_type or len(item_type) > 80:
            raise ValueError("Invalid item type.")

        def operation(thread: dict[str, Any]) -> dict[str, Any]:
            turn = self._find_turn(thread, turn_id)
            now = _utc_now()
            item = {
                "schema_version": 1,
                "item_id": f"item-{uuid.uuid4().hex[:16]}",
                "type": item_type,
                "status": status,
                "created_at": now,
                "updated_at": now,
                "payload": payload or {},
            }
            turn.setdefault("items", []).append(item)
            self._event(
                thread,
                "item/started" if status in {"queued", "running"} else "item/completed",
                turn_id=turn_id,
                item_id=item["item_id"],
                payload={"type": item_type, "status": status},
            )
            return item

        _, item = self._mutate(thread_id, operation)
        return item

    def update_item(
        self,
        thread_id: str,
        turn_id: str,
        item_id: str,
        *,
        status: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        def operation(thread: dict[str, Any]) -> dict[str, Any]:
            turn = self._find_turn(thread, turn_id)
            item = next(
                (candidate for candidate in turn.get("items", []) if candidate.get("item_id") == item_id),
                None,
            )
            if item is None:
                raise FileNotFoundError(f"Unknown item: {item_id}")
            item["status"] = status
            item["updated_at"] = _utc_now()
            if payload is not None:
                item["payload"] = payload
            self._event(
                thread,
                "item/completed" if status in {"completed", "failed"} else "item/updated",
                turn_id=turn_id,
                item_id=item_id,
                payload={"type": item.get("type"), "status": status},
            )
            return item

        _, item = self._mutate(thread_id, operation)
        return item

    def update_turn(self, thread_id: str, turn_id: str, status: str) -> dict[str, Any]:
        def operation(thread: dict[str, Any]) -> dict[str, Any]:
            turn = self._find_turn(thread, turn_id)
            turn["status"] = status
            if status in {"completed", "failed", "cancelled", "interrupted"}:
                turn["completed_at"] = _utc_now()
            thread["status"] = status
            self._event(thread, f"turn/{status}", turn_id=turn_id)
            return turn

        _, turn = self._mutate(thread_id, operation)
        return turn

    def events(self, thread_id: str, *, after: int = 0) -> list[dict[str, Any]]:
        if type(after) is not int or after < 0:
            raise ValueError("after must be a non-negative integer.")
        thread = self.read(thread_id)
        return [event for event in thread.get("events", []) if event.get("sequence", 0) > after]

    def find_by_farm(self, farm_id: str) -> tuple[str, str] | None:
        with self._lock:
            for summary in self.list(include_archived=True):
                thread = self._read_unlocked(summary["thread_id"])
                for turn in thread.get("turns", []):
                    for item in turn.get("items", []):
                        if item.get("type") == "farm_run" and item.get("payload", {}).get("farm_id") == farm_id:
                            return thread["thread_id"], turn["turn_id"]
        return None

    @staticmethod
    def _find_turn(thread: dict[str, Any], turn_id: str) -> dict[str, Any]:
        turn = next(
            (candidate for candidate in thread.get("turns", []) if candidate.get("turn_id") == turn_id),
            None,
        )
        if turn is None:
            raise FileNotFoundError(f"Unknown turn: {turn_id}")
        return turn
