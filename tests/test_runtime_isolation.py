from __future__ import annotations

import ast
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "code" / "src"
SCRIPTS = ROOT / "code" / "scripts"


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _imports_module(path: Path, module_name: str) -> bool:
    tree = ast.parse(_source(path), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(alias.name == module_name for alias in node.names):
                return True
        if isinstance(node, ast.ImportFrom) and node.module == module_name:
            return True
    return False


def test_binance_entrypoint_is_binance_only() -> None:
    source = _source(SRC / "MULTI_SYMBOLS.py")

    assert "KrakenSpotClient" not in source
    assert "BrokerConfig" not in source
    assert "bot_state_kraken" not in source
    assert "heartbeat_kraken" not in source
    assert "kraken_trading_bot" not in source
    assert "BROKER\"] = \"KRAKEN\"" not in source
    assert "BROKER'] = 'KRAKEN'" not in source
    assert "KRAKEN-PREFLIGHT" not in source


def test_binance_runtime_modules_do_not_branch_to_kraken() -> None:
    runtime_files = [
        "bot_config.py",
        "state_manager.py",
        "metrics.py",
        "data_fetcher.py",
        "display_ui.py",
        "order_manager.py",
        "position_reconciler.py",
        "timestamp_utils.py",
        "watchdog.py",
    ]
    combined = "\n".join(_source(SRC / name) for name in runtime_files)

    assert "KRAKEN" not in combined
    assert "Kraken" not in combined
    assert "kraken" not in combined


def test_kraken_entrypoint_does_not_launch_binance_runtime() -> None:
    path = SRC / "KRAKEN_SYMBOLS.py"
    source = _source(path)

    assert not _imports_module(path, "MULTI_SYMBOLS")
    assert "run_module(\"MULTI_SYMBOLS\"" not in source
    assert "kraken_bot" in source
    assert "os.environ[\"BROKER\"] = \"KRAKEN\"" in source


def test_kraken_dashboard_is_standalone() -> None:
    path = SCRIPTS / "kraken_dashboard_server.py"
    source = _source(path)

    assert not _imports_module(path, "dashboard_server")
    assert "dashboard_server" not in source
    assert "bot_state_kraken.json" in source
    assert "heartbeat_kraken.json" in source
    assert "kraken_trading_bot.log" in source
    assert "PORT = int(os.environ.get(\"KRAKEN_DASHBOARD_PORT\", \"8084\"))" in source


def test_kraken_sanity_check_does_not_import_binance_config() -> None:
    path = SCRIPTS / "kraken_sanity_check.py"
    source = _source(path)

    assert not _imports_module(path, "bot_config")
    assert "KRAKEN_API_KEY" in source
    assert "KRAKEN_SECRET_KEY" in source
    assert "KrakenSpotClient" in source


def test_kraken_runtime_uses_local_exchange_layer() -> None:
    runner = _source(SRC / "kraken_bot" / "runner.py")
    exchange_layer = _source(SRC / "kraken_bot" / "exchange_client.py")
    watchdog = _source(SRC / "kraken_bot" / "watchdog.py")

    assert "BinanceFinalClient" not in runner
    assert "run_module(\"MULTI_SYMBOLS\"" not in runner
    assert "class BinanceFinalClient" not in exchange_layer
    assert "KRAKEN_SYMBOLS.py" in watchdog
    assert "heartbeat_kraken.json" in watchdog


def test_kraken_runner_late_walk_forward_import_resolves_local() -> None:
    env = os.environ.copy()
    env.update(
        {
            "BROKER": "KRAKEN",
            "BOT_MODE": "DEMO",
            "SIZING_MODE": "risk",
            "KRAKEN_API_KEY": "test-key",
            "KRAKEN_SECRET_KEY": "dGVzdC1zZWNyZXQ=",
            "SENDER_EMAIL": "sender@example.test",
            "RECEIVER_EMAIL": "receiver@example.test",
            "GOOGLE_MAIL_PASSWORD": "password",
            "PYTHONPATH": os.pathsep.join([str(SRC / "kraken_bot"), str(SRC)]),
        }
    )
    code = (
        "import importlib, pathlib\n"
        "import runner\n"
        "wf = importlib.import_module('walk_forward')\n"
        "display_ui = importlib.import_module('display_ui')\n"
        "order_manager = importlib.import_module('order_manager')\n"
        "state_manager = importlib.import_module('state_manager')\n"
        "constants = importlib.import_module('constants')\n"
        "strategy_policy = importlib.import_module('strategy_policy')\n"
        "metrics = importlib.import_module('metrics')\n"
        "correlation_guard = importlib.import_module('correlation_guard')\n"
        "cython_integrity = importlib.import_module('cython_integrity')\n"
        "print(pathlib.Path(wf.__file__).resolve())\n"
        "print(pathlib.Path(display_ui.__file__).resolve())\n"
        "print(pathlib.Path(order_manager.__file__).resolve())\n"
        "print(pathlib.Path(state_manager.__file__).resolve())\n"
        "print(pathlib.Path(constants.__file__).resolve())\n"
        "print(pathlib.Path(strategy_policy.__file__).resolve())\n"
        "print(pathlib.Path(metrics.__file__).resolve())\n"
        "print(pathlib.Path(correlation_guard.__file__).resolve())\n"
        "print(pathlib.Path(cython_integrity.__file__).resolve())\n"
        "for name in ('display_account_balances_panel', 'load_state', '_execute_buy', '_execute_scheduled_trading', '_place_exchange_stop_loss'):\n"
        "    source = __import__('inspect').getsourcefile(getattr(runner, name))\n"
        "    print(name + '=' + str(pathlib.Path(source).resolve()))\n"
    )

    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=45,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    output = result.stdout.strip()
    assert str((SRC / "kraken_bot" / "walk_forward.py").resolve()) in output
    assert str((SRC / "kraken_bot" / "display_ui.py").resolve()) in output
    assert str((SRC / "kraken_bot" / "order_manager.py").resolve()) in output
    assert str((SRC / "kraken_bot" / "state_manager.py").resolve()) in output
    assert str((SRC / "kraken_bot" / "constants.py").resolve()) in output
    assert str((SRC / "kraken_bot" / "strategy_policy.py").resolve()) in output
    assert str((SRC / "kraken_bot" / "metrics.py").resolve()) in output
    assert str((SRC / "kraken_bot" / "correlation_guard.py").resolve()) in output
    assert str((SRC / "kraken_bot" / "cython_integrity.py").resolve()) in output
    assert f"display_account_balances_panel={(SRC / 'kraken_bot' / 'display_ui.py').resolve()}" in output
    assert f"load_state={(SRC / 'kraken_bot' / 'state_manager.py').resolve()}" in output
    assert f"_execute_buy={(SRC / 'kraken_bot' / 'order_manager.py').resolve()}" in output
    assert f"_execute_scheduled_trading={(SRC / 'kraken_bot' / 'backtest_orchestrator.py').resolve()}" in output
    assert f"_place_exchange_stop_loss={(SRC / 'kraken_bot' / 'exchange_client.py').resolve()}" in output
