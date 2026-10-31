import json
import tempfile
from decimal import Decimal
from pathlib import Path

import pytest

from actp.parser import parse_plan
from actp.plan_store import JsonPlanStore, PlanStoreError, StoredState

SAMPLE_PLAN = """PLAN XAUUSDr
BUY LIMIT (6 layer, 0.72 lot)
Area: 4119.83 - 4117.64
Cut Loss: 4116.98
SL: 4111.30 | TP: 4122.02
"""


def sample_state() -> StoredState:
    plan = parse_plan(SAMPLE_PLAN)
    return StoredState(
        current_message_id="message-002",
        plans={"message-001": plan, "message-002": plan},
        completed={"message-001"},
    )


def test_round_trip_preserves_plans_order_and_decimals():
    with tempfile.TemporaryDirectory() as directory:
        store = JsonPlanStore(Path(directory) / "state.json")
        state = sample_state()

        store.save(state)
        loaded = store.load()

        assert loaded.current_message_id == "message-002"
        assert list(loaded.plans) == ["message-001", "message-002"]
        assert loaded.completed == {"message-001"}
        assert loaded.plans["message-001"] == state.plans["message-001"]
        assert loaded.plans["message-001"]["buy"]["cut_loss"] == Decimal(
            "4116.98"
        )
        assert loaded.plans["message-001"]["sell"] is None


def test_missing_file_gives_an_empty_state():
    with tempfile.TemporaryDirectory() as directory:
        store = JsonPlanStore(Path(directory) / "state.json")

        loaded = store.load()

        assert loaded.current_message_id is None
        assert loaded.plans == {}
        assert loaded.completed == set()


def test_corrupt_json_is_rejected_not_replaced_by_empty_state():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "state.json"
        path.write_text("{not json")

        with pytest.raises(PlanStoreError):
            JsonPlanStore(path).load()


def test_tampered_plan_is_rejected():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "state.json"
        store = JsonPlanStore(path)
        store.save(sample_state())

        data = json.loads(path.read_text())
        data["plans"][0]["plan"]["symbol"] = "EURUSD"
        path.write_text(json.dumps(data))

        with pytest.raises(PlanStoreError):
            store.load()


def test_prices_stored_as_numbers_are_rejected():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "state.json"
        store = JsonPlanStore(path)
        store.save(sample_state())

        data = json.loads(path.read_text())
        data["plans"][0]["plan"]["buy"]["sl"] = 4111.3
        path.write_text(json.dumps(data))

        with pytest.raises(PlanStoreError, match="stored as text"):
            store.load()


def test_current_message_without_a_stored_plan_is_rejected():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "state.json"
        store = JsonPlanStore(path)
        store.save(sample_state())

        data = json.loads(path.read_text())
        data["current_message_id"] = "message-999"
        path.write_text(json.dumps(data))

        with pytest.raises(PlanStoreError):
            store.load()


def test_save_leaves_no_temp_file_and_creates_parent_directory():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "nested" / "state.json"

        JsonPlanStore(path).save(sample_state())

        assert path.exists()
        assert [p.name for p in path.parent.iterdir()] == ["state.json"]
