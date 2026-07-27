"""Read-only Kraken Pro sanity check.

Usage:
    $env:BROKER="KRAKEN"
    python code/scripts/kraken_sanity_check.py

This script never places or cancels orders. It checks pair availability,
balances and open orders through the Kraken adapter.
"""

from __future__ import annotations

import os
import sys


BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
ROOT = os.path.join(BASE_DIR, "code", "src")
KRAKEN_BOT_ROOT = os.path.join(ROOT, "kraken_bot")
for path in (ROOT, KRAKEN_BOT_ROOT):
    if path not in sys.path:
        sys.path.insert(0, path)

os.environ.setdefault("BROKER", "KRAKEN")

from broker_models import BrokerConfig  # noqa: E402
from kraken_client import KrakenSpotClient  # noqa: E402


def _status(label: str, ok: bool, detail: str = "") -> None:
    prefix = "[OK]" if ok else "[BLOCKED]"
    suffix = f": {detail}" if detail else ""
    print(f"{prefix} {label}{suffix}")


def _load_env_key(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    if value:
        return value.strip().strip('"').strip("'")
    env_path = os.path.join(BASE_DIR, ".env")
    try:
        with open(env_path, encoding="utf-8") as fh:
            for raw_line in fh:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, raw_value = line.split("=", 1)
                if key.strip() == name:
                    return raw_value.strip().strip('"').strip("'")
    except OSError:
        return default
    return default


def main() -> int:
    api_key = _load_env_key("KRAKEN_API_KEY")
    api_secret = _load_env_key("KRAKEN_SECRET_KEY")
    if not api_key or not api_secret:
        print("[BLOCKED] KRAKEN_API_KEY ou KRAKEN_SECRET_KEY manquante dans l'environnement/.env")
        return 1

    client = KrakenSpotClient(
        BrokerConfig(
            broker="KRAKEN",
            api_key=api_key,
            api_secret=api_secret,
            api_url=_load_env_key("KRAKEN_API_URL", "https://api.kraken.com") or "https://api.kraken.com",
            ws_url=_load_env_key("KRAKEN_WS_URL", "wss://ws-auth.kraken.com/v2") or "wss://ws-auth.kraken.com/v2",
            strict_pair_validation=True,
        ),
        requests_params={"timeout": float(_load_env_key("API_TIMEOUT", "30") or 30)},
    )
    pairs = ["ONDOUSD", "XRPUSDC", "CROUSDC"]
    print("Kraken read-only sanity check")
    preflight = client.preflight_private_api()
    _status("Public API", preflight.public_ok)
    _status("Balance / Query Funds", preflight.balance_ok, preflight.permission_error or "")
    _status("OpenOrders / Query Open Orders & Trades", preflight.open_orders_ok)
    _status("ClosedOrders / Query Closed Orders & Trades", preflight.closed_orders_ok)
    if not preflight.private_api_ok:
        print(
            "[ACTION] Permissions a cocher: Requete fonds, consulter ordres/transactions "
            "ouverts, consulter ordres/transactions clotures, creer/modifier ordres, "
            "annuler/cloturer ordres."
        )
        print("[ACTION] Ne pas cocher Depot, Retrait, Gains, Registre ou Export.")
    for pair in pairs:
        try:
            resolved = client.resolve_pair(pair)
            print(f"[OK] {pair} -> {resolved.broker_symbol} ({resolved.broker_pair_id})")
        except Exception as exc:
            print(f"[BLOCKED] {pair}: {exc}")
    try:
        account = client.get_account()
        non_zero = [
            bal for bal in account.get("balances", [])
            if float(bal.get("free", 0) or 0) + float(bal.get("locked", 0) or 0) > 0
        ]
        print(f"[OK] Balances non-zero: {len(non_zero)}")
    except Exception as exc:
        print(f"[WARN] Balance read failed: {exc}")
    try:
        open_orders = client.get_open_orders()
        print(f"[OK] Open orders: {len(open_orders)}")
    except Exception as exc:
        print(f"[WARN] Open orders read failed: {exc}")
    return 0 if preflight.tradable else 1


if __name__ == "__main__":
    raise SystemExit(main())
