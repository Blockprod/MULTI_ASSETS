"""Tests for trade_journal.py — JSONL trade logging."""
import sys
import os
import json
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


# ══════════════════════════════════════════════════════════════════════════════
# P2-10 — Tests supplémentaires
# ══════════════════════════════════════════════════════════════════════════════

class TestP210TradeJournal:
    """P2-10: branches manquantes dans trade_journal.py."""

    def test_log_trade_returns_false_on_write_error(self, temp_logs_dir):
        """log_trade retourne False si l'écriture échoue."""
        # Utiliser un chemin invalide pour provoquer une erreur d'écriture
        bad_dir = os.path.join(temp_logs_dir, "\x00invalid")
        result = log_trade(
            logs_dir=bad_dir, pair="BTCUSDC", side="buy",
            quantity=1.0, price=100.0,
        )
        assert result is False

    def test_log_trade_with_optional_fields(self, temp_logs_dir):
        """log_trade avec tous les champs optionnels."""
        result = log_trade(
            logs_dir=temp_logs_dir, pair="ETHUSDC", side="sell",
            quantity=10.0, price=3000.0, fee=3.0,
            slippage=0.0001, scenario="StochRSI", timeframe="4h",
            ema1=12, ema2=26, atr_value=50.0, stop_price=2900.0,
            pnl=150.0, pnl_pct=5.0,
            equity_before=10000.0, equity_after=10150.0,
        )
        assert result is True
        records = read_journal(temp_logs_dir)
        rec = records[-1]
        assert rec["pair"] == "ETHUSDC"
        assert rec["scenario"] == "StochRSI"
        assert rec["timeframe"] == "4h"
        assert rec["pnl"] == 150.0

    def test_read_journal_skips_corrupt_line(self, temp_logs_dir):
        """read_journal s'arrête à la première ligne corrompue (exception capturée)."""
        journal_path = os.path.join(temp_logs_dir, "trade_journal.jsonl")
        with open(journal_path, "w") as f:
            f.write('{"pair":"BTCUSDC","side":"buy","qty":1,"price":100}\n')
            f.write('NOT VALID JSON\n')
            f.write('{"pair":"ETHUSDC","side":"sell","qty":2,"price":200}\n')
        records = read_journal(temp_logs_dir)
        # La ligne corrompue provoque une exception → seules les lignes avant sont retournées
        assert len(records) == 1
        assert records[0]["pair"] == "BTCUSDC"

    def test_journal_summary_avg_win_and_avg_loss(self, temp_logs_dir):
        """journal_summary calcule bien avg_win et avg_loss."""
        log_trade(logs_dir=temp_logs_dir, pair="A", side="sell", quantity=1, price=110, pnl=10)
        log_trade(logs_dir=temp_logs_dir, pair="A", side="sell", quantity=1, price=120, pnl=30)
        log_trade(logs_dir=temp_logs_dir, pair="A", side="sell", quantity=1, price=80, pnl=-20)
        summary = journal_summary(temp_logs_dir)
        assert summary["avg_win"] == pytest.approx(20.0)  # (10+30)/2
        assert summary["avg_loss"] == pytest.approx(-20.0)  # -20/1

    def test_journal_summary_pnl_zero_is_loss(self, temp_logs_dir):
        """Un sell avec pnl=0 est compté comme une perte (pas un gain)."""
        log_trade(logs_dir=temp_logs_dir, pair="A", side="sell", quantity=1, price=100, pnl=0)
        summary = journal_summary(temp_logs_dir)
        assert summary["win_count"] == 0
        assert summary["loss_count"] == 1

    def test_journal_summary_buys_only(self, temp_logs_dir):
        """journal_summary avec uniquement des buys → total_sells=0, win_rate=0."""
        log_trade(logs_dir=temp_logs_dir, pair="A", side="buy", quantity=1, price=100)
        log_trade(logs_dir=temp_logs_dir, pair="B", side="buy", quantity=2, price=200)
        summary = journal_summary(temp_logs_dir)
        assert summary["total_trades"] == 2
        assert summary["total_sells"] == 0
        assert summary["win_rate"] == 0.0


# ─── P1-02: Monthly rotation ──────────────────────────────────────────────────

class TestMonthlyRotation:
    """Tests pour la rotation mensuelle du journal (P1-02)."""

    def test_no_rotation_same_month(self, temp_logs_dir):
        """Pas de rotation si le fichier est du mois courant."""
        from trade_journal import _maybe_rotate_journal, _CURRENT_JOURNAL
        # Write a record with current month timestamp
        log_trade(logs_dir=temp_logs_dir, pair="BTCUSDC", side="buy", quantity=1, price=100)
        journal_path = os.path.join(temp_logs_dir, _CURRENT_JOURNAL)
        assert os.path.exists(journal_path)

        _maybe_rotate_journal(temp_logs_dir)

        # Still exists under original name after no-rotation
        assert os.path.exists(journal_path)

    def test_rotation_on_month_change(self, temp_logs_dir):
        """Le fichier est renommé journal_YYYY-MM.jsonl si le mois a changé."""
        from trade_journal import _maybe_rotate_journal, _CURRENT_JOURNAL
        # Create a journal with a record timestamped to a past month
        journal_path = os.path.join(temp_logs_dir, _CURRENT_JOURNAL)
        old_record = {"ts": "2025-01-15T10:00:00+00:00", "pair": "BTCUSDC"}
        with open(journal_path, "w", encoding="utf-8") as f:
            f.write(json.dumps(old_record) + "\n")

        _maybe_rotate_journal(temp_logs_dir)

        # Original file should be gone
        assert not os.path.exists(journal_path)
        # Archive file should exist
        archive = os.path.join(temp_logs_dir, "journal_2025-01.jsonl")
        assert os.path.exists(archive)

    def test_new_trade_after_rotation_creates_new_file(self, temp_logs_dir):
        """Après rotation, le prochain log_trade crée un nouveau trade_journal.jsonl."""
        from trade_journal import _CURRENT_JOURNAL
        # Seed with a past-month record
        journal_path = os.path.join(temp_logs_dir, _CURRENT_JOURNAL)
        old_record = {"ts": "2024-06-01T00:00:00+00:00", "pair": "ETHUSDC"}
        with open(journal_path, "w", encoding="utf-8") as f:
            f.write(json.dumps(old_record) + "\n")

        # New write should trigger rotation then create fresh file
        log_trade(logs_dir=temp_logs_dir, pair="BTCUSDC", side="buy", quantity=0.5, price=50000)

        # New journal file exists
        assert os.path.exists(journal_path)
        # Archive exists
        archive = os.path.join(temp_logs_dir, "journal_2024-06.jsonl")
        assert os.path.exists(archive)
        # New journal only has the new record
        with open(journal_path, "r") as f:
            lines = [ln.strip() for ln in f.readlines() if ln.strip()]
        assert len(lines) == 1
        assert json.loads(lines[0])["pair"] == "BTCUSDC"

    def test_rotation_on_empty_file_no_crash(self, temp_logs_dir):
        """_maybe_rotate_journal ne plante pas sur un fichier vide."""
        from trade_journal import _maybe_rotate_journal, _CURRENT_JOURNAL
        journal_path = os.path.join(temp_logs_dir, _CURRENT_JOURNAL)
        open(journal_path, "w").close()  # empty file
        _maybe_rotate_journal(temp_logs_dir)  # should not raise

    def test_rotation_on_missing_file_no_crash(self, temp_logs_dir):
        """_maybe_rotate_journal ne plante pas si le fichier n'existe pas."""
        from trade_journal import _maybe_rotate_journal
        _maybe_rotate_journal(temp_logs_dir)  # should not raise

