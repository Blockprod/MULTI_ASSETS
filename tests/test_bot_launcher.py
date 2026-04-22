import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


_MODULE_PATH = Path(__file__).resolve().parents[1] / 'code' / 'scripts' / 'wait_for_bot_ready.py'
_SPEC = importlib.util.spec_from_file_location('bot_wait_ready_test_module', _MODULE_PATH)
assert _SPEC is not None and _SPEC.loader is not None
wait_mod = importlib.util.module_from_spec(_SPEC)
sys.modules.setdefault('bot_wait_ready_test_module', wait_mod)
_SPEC.loader.exec_module(wait_mod)


def test_wait_for_bot_ready_succeeds_with_matching_fresh_heartbeat(tmp_path, monkeypatch):
    lock_path = tmp_path / '.running.lock'
    heartbeat_path = tmp_path / 'heartbeat.json'
    lock_path.write_text('4242', encoding='utf-8')
    heartbeat_path.write_text(
        json.dumps({
            'timestamp': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
            'pid': 4242,
            'circuit_mode': 'RUNNING',
            'loop_counter': 1,
        }),
        encoding='utf-8',
    )
    monkeypatch.setattr(wait_mod, '_pid_exists', lambda pid: pid == 4242)

    ok, message = wait_mod.wait_for_bot_ready(
        lock_path=lock_path,
        heartbeat_path=heartbeat_path,
        timeout_seconds=0.2,
        interval_seconds=0.05,
        max_heartbeat_age_seconds=30.0,
    )

    assert ok is True
    assert 'pid=4242' in message


def test_wait_for_bot_ready_accepts_fresh_heartbeat_when_lock_is_stale(tmp_path, monkeypatch):
    lock_path = tmp_path / '.running.lock'
    heartbeat_path = tmp_path / 'heartbeat.json'
    lock_path.write_text('4242', encoding='utf-8')
    heartbeat_path.write_text(
        json.dumps({
            'timestamp': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
            'pid': 9898,
            'circuit_mode': 'RUNNING',
            'loop_counter': 2,
        }),
        encoding='utf-8',
    )
    monkeypatch.setattr(wait_mod, '_pid_exists', lambda pid: pid == 9898)

    ok, message = wait_mod.wait_for_bot_ready(
        lock_path=lock_path,
        heartbeat_path=heartbeat_path,
        timeout_seconds=0.2,
        interval_seconds=0.05,
        max_heartbeat_age_seconds=30.0,
    )

    assert ok is True
    assert 'pid=9898' in message


def test_wait_for_bot_ready_rejects_stale_or_mismatched_live_heartbeat(tmp_path, monkeypatch):
    lock_path = tmp_path / '.running.lock'
    heartbeat_path = tmp_path / 'heartbeat.json'
    lock_path.write_text('4242', encoding='utf-8')
    heartbeat_path.write_text(
        json.dumps({
            'timestamp': (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat().replace('+00:00', 'Z'),
            'pid': 9898,
            'circuit_mode': 'RUNNING',
            'loop_counter': 1,
        }),
        encoding='utf-8',
    )
    monkeypatch.setattr(wait_mod, '_pid_exists', lambda pid: pid in {4242, 9898})

    ok, message = wait_mod.wait_for_bot_ready(
        lock_path=lock_path,
        heartbeat_path=heartbeat_path,
        timeout_seconds=0.2,
        interval_seconds=0.05,
        max_heartbeat_age_seconds=30.0,
    )

    assert ok is False
    assert 'mismatch' in message or 'stale' in message
