"""Unit tests for doorbell king terminal routing."""

import json
from pathlib import Path

import pytest

from cli_agent_orchestrator.doorbell.routing import (
    DoorbellRoutingError,
    king_terminal_id_from_state,
    resolve_king_terminal_id,
)


class TestResolveKingTerminalId:
    def test_explicit_to_wins_over_state_file(self, tmp_path: Path):
        state = tmp_path / "state.json"
        state.write_text(json.dumps({"king_terminal_id": "aaaaaaaa"}), encoding="utf-8")
        assert resolve_king_terminal_id(explicit_to="bbbbbbbb", state_path=state) == "bbbbbbbb"

    def test_reads_king_terminal_id_from_state(self, tmp_path: Path):
        state = tmp_path / "state.json"
        state.write_text(
            json.dumps(
                {
                    "epic_id": "bd-abc",
                    "king_terminal_id": "abcd1234",
                    "policy_version": 1,
                    "legacy_wave": ["old", "keys"],
                }
            ),
            encoding="utf-8",
        )
        assert king_terminal_id_from_state(state) == "abcd1234"

    def test_missing_state_file(self, tmp_path: Path):
        missing = tmp_path / "missing.json"
        with pytest.raises(DoorbellRoutingError, match="not found"):
            king_terminal_id_from_state(missing)

    def test_missing_king_terminal_id(self, tmp_path: Path):
        state = tmp_path / "state.json"
        state.write_text(json.dumps({"epic_id": "bd-abc"}), encoding="utf-8")
        with pytest.raises(DoorbellRoutingError, match="missing king_terminal_id"):
            king_terminal_id_from_state(state)

    def test_null_king_terminal_id(self, tmp_path: Path):
        state = tmp_path / "state.json"
        state.write_text(json.dumps({"king_terminal_id": None}), encoding="utf-8")
        with pytest.raises(DoorbellRoutingError, match="missing king_terminal_id"):
            king_terminal_id_from_state(state)

    def test_invalid_king_terminal_id(self, tmp_path: Path):
        state = tmp_path / "state.json"
        state.write_text(json.dumps({"king_terminal_id": "not-a-terminal"}), encoding="utf-8")
        with pytest.raises(DoorbellRoutingError, match="8-character lowercase hex"):
            king_terminal_id_from_state(state)

    def test_invalid_json(self, tmp_path: Path):
        state = tmp_path / "state.json"
        state.write_text("{not json", encoding="utf-8")
        with pytest.raises(DoorbellRoutingError, match="invalid JSON"):
            king_terminal_id_from_state(state)
