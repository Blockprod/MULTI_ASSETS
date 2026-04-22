"""Wait until the local dashboard API responds successfully."""

from __future__ import annotations

import sys
import time
import urllib.error
import urllib.request


DEFAULT_URL = "http://127.0.0.1:8082/api/data"
DEFAULT_TIMEOUT_SECONDS = 20.0
DEFAULT_INTERVAL_SECONDS = 0.5


def wait_for_dashboard_ready(
    url: str = DEFAULT_URL,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    interval_seconds: float = DEFAULT_INTERVAL_SECONDS,
) -> tuple[bool, str]:
    deadline = time.time() + max(timeout_seconds, 0.0)
    last_error = "no response"

    while time.time() <= deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                status = getattr(response, "status", 200)
                if 200 <= status < 300:
                    return True, f"dashboard ready ({status})"
                last_error = f"unexpected status {status}"
        except urllib.error.URLError as exc:
            last_error = str(exc.reason or exc)
        except Exception as exc:  # pragma: no cover - defensive surface
            last_error = str(exc)
        time.sleep(max(interval_seconds, 0.05))

    return False, last_error


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    url = args[0] if args else DEFAULT_URL

    timeout_seconds = DEFAULT_TIMEOUT_SECONDS
    if len(args) > 1:
        try:
            timeout_seconds = float(args[1])
        except ValueError:
            print(f"[!] Invalid timeout: {args[1]}")
            return 2

    ok, message = wait_for_dashboard_ready(url=url, timeout_seconds=timeout_seconds)
    if ok:
        print(f"[OK] {message}: {url}")
        return 0

    print(f"[!] Dashboard not ready after {timeout_seconds:.0f}s: {message}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())