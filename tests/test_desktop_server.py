from __future__ import annotations

import json
from types import SimpleNamespace

from agent_farm.desktop_server import READY_PREFIX, native_runtime_url, ready_message


def test_native_runtime_url_disables_legacy_desktop_chrome() -> None:
    runtime = SimpleNamespace(url="http://127.0.0.1:43123/?desktop=1")

    assert native_runtime_url(runtime) == "http://127.0.0.1:43123/?native=1"


def test_ready_message_is_machine_readable() -> None:
    message = ready_message("http://127.0.0.1:43123/?native=1")

    assert message.startswith(READY_PREFIX)
    assert json.loads(message[len(READY_PREFIX) :]) == {
        "url": "http://127.0.0.1:43123/?native=1"
    }
