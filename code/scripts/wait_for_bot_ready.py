"""Wait until the local bot reports a fresh heartbeat for the current lock PID."""

from __future__ import annotations

import json
import os
import time
import ctypes
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parents[2]
DEFAULT_LOCK_PATH = BASE_DIR / ".running.lock"
DEFAULT_HEARTBEAT_PATH = BASE_DIR / "code" / "src" / "states" / "heartbeat.json"
DEFAULT_TIMEOUT_SECONDS = 120.0
DEFAULT_INTERVAL_SECONDS = 0.5
DEFAULT_MAX_HEARTBEAT_AGE_SECONDS = 30.0


def _read_pid(lock_path: Path) -> int | None:
    try:
        raw = lock_path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return int(raw) if raw.isdigit() else None


def _read_heartbeat(heartbeat_path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(heartbeat_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _heartbeat_age_seconds(timestamp: str) -> float | None:
    try:
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (datetime.now(timezone.utc) - parsed).total_seconds()


def _pid_exists(pid: int | None) -> bool:
    if pid is None or pid <= 0:
        return False
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True

    process_handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
    if not process_handle:
        return False
    ctypes.windll.kernel32.CloseHandle(process_handle)
    return True


def wait_for_bot_ready(
    lock_path: Path = DEFAULT_LOCK_PATH,
    heartbeat_path: Path = DEFAULT_HEARTBEAT_PATH,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    interval_seconds: float = DEFAULT_INTERVAL_SECONDS,
    max_heartbeat_age_seconds: float = DEFAULT_MAX_HEARTBEAT_AGE_SECONDS,
) -> tuple[bool, str]:
    deadline = time.time() + max(timeout_seconds, 0.0)
    last_error = "lock pid not available"

    while time.time() <= deadline:
        expected_pid = _read_pid(lock_path)
        heartbeat = _read_heartbeat(heartbeat_path)
        expected_pid_alive = _pid_exists(expected_pid)

        if expected_pid is None:
            last_error = "lock pid not available"
        elif heartbeat is None:
            last_error = "heartbeat not available"
        else:
            heartbeat_pid = heartbeat.get("pid")
            heartbeat_ts = heartbeat.get("timestamp")
            heartbeat_mode = heartbeat.get("circuit_mode")
            loop_counter = heartbeat.get("loop_counter")
            heartbeat_pid_ok = heartbeat_pid if isinstance(heartbeat_pid, int) else None
            heartbeat_pid_alive = _pid_exists(heartbeat_pid_ok)

            if heartbeat_pid != expected_pid and expected_pid_alive:
                last_error = f"heartbeat pid mismatch ({heartbeat_pid} != {expected_pid})"
            elif not isinstance(heartbeat_ts, str) or not heartbeat_ts.strip():
                last_error = "heartbeat timestamp missing"
            else:
                age_seconds = _heartbeat_age_seconds(heartbeat_ts)
                if age_seconds is None:
                    last_error = "heartbeat timestamp invalid"
                elif age_seconds > max_heartbeat_age_seconds:
                    last_error = f"heartbeat stale ({age_seconds:.1f}s)"
                elif not heartbeat_pid_alive:
                    last_error = f"heartbeat pid not alive ({heartbeat_pid})"
                elif not isinstance(loop_counter, int) or loop_counter < 1:
                    last_error = f"loop counter not ready ({loop_counter})"
                else:
                    mode_label = heartbeat_mode if isinstance(heartbeat_mode, str) and heartbeat_mode else "unknown"
                    ready_pid = heartbeat_pid if isinstance(heartbeat_pid, int) else expected_pid
                    return True, f"bot ready (pid={ready_pid}, mode={mode_label}, loop={loop_counter})"

        time.sleep(max(interval_seconds, 0.05))

    return False, last_error


def main(argv: list[str] | None = None) -> int:
    args = [] if argv is None else list(argv)
    timeout_seconds = DEFAULT_TIMEOUT_SECONDS
    if args:
        try:
            timeout_seconds = float(args[0])
        except ValueError:
            print(f"[!] Invalid timeout: {args[0]}")
            return 2

    ok, message = wait_for_bot_ready(timeout_seconds=timeout_seconds)
    if ok:
        print(f"[OK] {message}")
        return 0

    print(f"[!] Bot not ready after {timeout_seconds:.0f}s: {message}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())