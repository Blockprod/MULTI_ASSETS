"""Tests for trade_journal.py — JSONL trade logging."""
import sys
import os
import json
import tempfile
import threading
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'code', 'src'))

from trade_journal import log_trade, read_journal, journal_summary


@pytest.fixture
def temp_logs_dir(tmp_path):
    """Temporary directory for journal tests."""
    logs_dir = str(tmp_path / "logs")
    os.makedirs(logs_dir, exist_ok=True)
    return logs_dir


class TestLogTrade:
    """Tests pour log_trade."""

    def test_writes_jsonl_line(self, temp_logs_dir):
        result = log_trade(
            logs_dir=temp_logs_dir,
            pair="BTCUSDC",
            side="buy",
            quantity=0.5,
            price=50000.0,
            fee=17.5,
        )
        assert result is True
        journal_path = os.path.join(temp_logs_dir, "trade_journal.jsonl")
        assert os.path.exists(journal_path)
        with open(journal_path, "r") as f:
            line = f.readline()
        record = json.loads(line)
        assert record["pair"] == "BTCUSDC"
        assert record["side"] == "buy"
        assert record["qty"] == 0.5
        assert record["price"] == 50000.0

    def test_appends_multiple_trades(self, temp_logs_dir):
        log_trade(logs_dir=temp_logs_dir, pair="BTCUSDC", side="buy", quantity=1.0, price=100.0)
        log_trade(logs_dir=temp_logs_dir, pair="BTCUSDC", side="sell", quantity=1.0, price=110.0, pnl=10.0)
        records = read_journal(temp_logs_dir)
        assert len(records) == 2

    def test_extra_fields(self, temp_logs_dir):
        log_trade(
            logs_dir=temp_logs_dir,
            pair="ETHUSDC",
            side="sell",
            quantity=10.0,
            price=3000.0,
            extra={"sell_reason": "STOP-LOSS"},
        )
        records = read_journal(temp_logs_dir)
        assert records[0]["sell_reason"] == "STOP-LOSS"

    def test_timestamp_is_iso(self, temp_logs_dir):
        log_trade(logs_dir=temp_logs_dir, pair="BTCUSDC", side="buy", quantity=1, price=100)
        records = read_journal(temp_logs_dir)
        assert "ts" in records[0]
        # Should be valid ISO 8601
        from datetime import datetime
        datetime.fromisoformat(records[0]["ts"])


class TestReadJournal:
    """Tests pour read_journal."""

    def test_empty_journal(self, temp_logs_dir):
        records = read_journal(temp_logs_dir)
        assert records == []

    def test_last_n(self, temp_logs_dir):
        for i in range(10):
            log_trade(logs_dir=temp_logs_dir, pair="BTCUSDC", side="buy", quantity=i, price=100)
        records = read_journal(temp_logs_dir, last_n=3)
        assert len(records) == 3
        assert records[0]["qty"] == 7  # Last 3: indices 7, 8, 9

    def test_nonexistent_directory(self, tmp_path):
        records = read_journal(str(tmp_path / "nonexistent"))
        assert records == []


class TestJournalSummary:
    """Tests pour journal_summary."""

    def test_empty_summary(self, temp_logs_dir):
        summary = journal_summary(temp_logs_dir)
        assert summary["total_trades"] == 0

    def test_win_rate_calculation(self, temp_logs_dir):
        # 3 winning sells, 1 losing sell
        log_trade(logs_dir=temp_logs_dir, pair="BTCUSDC", side="buy", quantity=1, price=100)
        log_trade(logs_dir=temp_logs_dir, pair="BTCUSDC", side="sell", quantity=1, price=110, pnl=10)
        log_trade(logs_dir=temp_logs_dir, pair="BTCUSDC", side="sell", quantity=1, price=120, pnl=20)
        log_trade(logs_dir=temp_logs_dir, pair="BTCUSDC", side="sell", quantity=1, price=130, pnl=30)
        log_trade(logs_dir=temp_logs_dir, pair="BTCUSDC", side="sell", quantity=1, price=90, pnl=-10)

        summary = journal_summary(temp_logs_dir)
        assert summary["total_trades"] == 5
        assert summary["total_sells"] == 4
        assert summary["win_count"] == 3
        assert summary["loss_count"] == 1
        assert summary["win_rate"] == 75.0
        assert summary["total_pnl"] == 50.0
        assert summary["best_trade"] == 30.0
        assert summary["worst_trade"] == -10.0

    def test_all_losses(self, temp_logs_dir):
        log_trade(logs_dir=temp_logs_dir, pair="BTCUSDC", side="sell", quantity=1, price=90, pnl=-10)
        log_trade(logs_dir=temp_logs_dir, pair="BTCUSDC", side="sell", quantity=1, price=80, pnl=-20)
        summary = journal_summary(temp_logs_dir)
        assert summary["win_rate"] == 0.0
        assert summary["total_pnl"] == -30.0


class TestConcurrency:
    """Test d'écriture concurrente."""

    def test_concurrent_writes(self, temp_logs_dir):
        errors = []

        def writer(trade_id):
            try:
                log_trade(
                    logs_dir=temp_logs_dir,
                    pair="BTCUSDC",
                    side="buy",
                    quantity=trade_id,
                    price=100.0,
                )
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        records = read_journal(temp_logs_dir)
        assert len(records) == 20
