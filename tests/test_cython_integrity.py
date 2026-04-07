"""
Tests for cython_integrity.py — P1-01 Cython .pyd integrity verification.

Covers:
- verify_cython_integrity returns True when checksums match
- verify_cython_integrity returns False + logs CRITICAL on mismatch
- alert_fn is called on mismatch
- verify_cython_integrity returns False (no crash) when checksums.json absent
- CYTHON_INTEGRITY_VERIFIED flag is set correctly
- Files absent from disk (different Python version) are silently skipped
- generate_checksums() writes valid JSON with correct SHA256
"""

import hashlib
import json
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'code', 'src'))


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ─── Tests ───────────────────────────────────────────────────────────────────

class TestVerifyCythonIntegrity:
    """Tests for verify_cython_integrity()."""

    def test_returns_true_when_checksums_match(self, tmp_path):
        """Returns True and sets CYTHON_INTEGRITY_VERIFIED=True when all checksums OK."""
        import cython_integrity as ci

        # create a fake .pyd
        pyd = tmp_path / "fake.cp311-win_amd64.pyd"
        pyd.write_bytes(b"fake cython binary content")
        expected_hash = _sha256(b"fake cython binary content")

        checksums = {pyd.name: expected_hash}
        checksums_file = tmp_path / "checksums.json"
        checksums_file.write_text(json.dumps(checksums), encoding='utf-8')

        with patch.object(ci, '_BIN_DIR', str(tmp_path)), \
             patch.object(ci, '_CHECKSUMS_FILE', str(checksums_file)):
            result = ci.verify_cython_integrity()

        assert result is True
        assert ci.CYTHON_INTEGRITY_VERIFIED is True

    def test_returns_false_and_logs_critical_on_mismatch(self, tmp_path, caplog):
        """Returns False + logs CRITICAL when checksum doesn't match."""
        import cython_integrity as ci

        pyd = tmp_path / "indicators.cp311-win_amd64.pyd"
        pyd.write_bytes(b"original content")
        good_hash = _sha256(b"original content")

        # Store the correct hash but the file has different content now
        checksums = {pyd.name: good_hash}
        checksums_file = tmp_path / "checksums.json"
        checksums_file.write_text(json.dumps(checksums), encoding='utf-8')

        # Corrupt the file
        pyd.write_bytes(b"CORRUPTED content")

        with patch.object(ci, '_BIN_DIR', str(tmp_path)), \
             patch.object(ci, '_CHECKSUMS_FILE', str(checksums_file)), \
             caplog.at_level('CRITICAL', logger='cython_integrity'):
            result = ci.verify_cython_integrity()

        assert result is False
        assert ci.CYTHON_INTEGRITY_VERIFIED is False
        assert any('CHECKSUM MISMATCH' in r.message for r in caplog.records)

    def test_alert_fn_called_on_mismatch(self, tmp_path):
        """alert_fn is called with subject+body when mismatch detected."""
        import cython_integrity as ci
        from unittest.mock import MagicMock

        pyd = tmp_path / "backtest_engine_standard.cp311-win_amd64.pyd"
        pyd.write_bytes(b"original")
        good_hash = _sha256(b"original")

        checksums_file = tmp_path / "checksums.json"
        checksums_file.write_text(json.dumps({pyd.name: good_hash}), encoding='utf-8')

        pyd.write_bytes(b"CORRUPTED")

        alert_fn = MagicMock()
        with patch.object(ci, '_BIN_DIR', str(tmp_path)), \
             patch.object(ci, '_CHECKSUMS_FILE', str(checksums_file)):
            ci.verify_cython_integrity(alert_fn=alert_fn)

        alert_fn.assert_called_once()
        subject, body = alert_fn.call_args[0]
        assert 'P1-01' in subject
        assert pyd.name in body

    def test_returns_false_when_checksums_file_absent(self, tmp_path):
        """Returns False (no crash) when checksums.json doesn't exist."""
        import cython_integrity as ci

        missing_checksums = str(tmp_path / "nonexistent.json")
        with patch.object(ci, '_CHECKSUMS_FILE', missing_checksums):
            result = ci.verify_cython_integrity()

        assert result is False
        assert ci.CYTHON_INTEGRITY_VERIFIED is False

    def test_missing_pyd_on_disk_is_silently_skipped(self, tmp_path):
        """A .pyd listed in checksums.json but absent on disk is skipped (other Python version)."""
        import cython_integrity as ci

        # Checksums reference a file that doesn't exist locally
        checksums = {"indicators.cp313-win_amd64.pyd": "deadbeef" * 8}
        checksums_file = tmp_path / "checksums.json"
        checksums_file.write_text(json.dumps(checksums), encoding='utf-8')

        # BIN_DIR points to empty tmp_path — file doesn't exist
        with patch.object(ci, '_BIN_DIR', str(tmp_path)), \
             patch.object(ci, '_CHECKSUMS_FILE', str(checksums_file)):
            result = ci.verify_cython_integrity()

        # No files to check → considered OK (vacuously true)
        assert result is True

    def test_alert_fn_exception_does_not_crash(self, tmp_path):
        """If alert_fn raises, verify_cython_integrity still returns False without crashing."""
        import cython_integrity as ci

        pyd = tmp_path / "indicators.cp311-win_amd64.pyd"
        pyd.write_bytes(b"original")
        checksums_file = tmp_path / "checksums.json"
        checksums_file.write_text(json.dumps({pyd.name: _sha256(b"original")}), encoding='utf-8')
        pyd.write_bytes(b"CORRUPTED")

        def boom(subject, body):
            raise RuntimeError("SMTP failure")

        with patch.object(ci, '_BIN_DIR', str(tmp_path)), \
             patch.object(ci, '_CHECKSUMS_FILE', str(checksums_file)):
            result = ci.verify_cython_integrity(alert_fn=boom)

        assert result is False  # still returns False, didn't crash


class TestGenerateChecksums:
    """Tests for generate_checksums()."""

    def test_generates_valid_json_with_sha256(self, tmp_path):
        """generate_checksums() writes valid JSON with correct SHA256 entries."""
        import cython_integrity as ci

        # Create two fake .pyd files
        f1 = tmp_path / "mod_a.cp311-win_amd64.pyd"
        f2 = tmp_path / "mod_b.cp311-win_amd64.pyd"
        f1.write_bytes(b"content_a")
        f2.write_bytes(b"content_b")

        checksums_file = tmp_path / "checksums.json"

        with patch.object(ci, '_BIN_DIR', str(tmp_path)), \
             patch.object(ci, '_CHECKSUMS_FILE', str(checksums_file)):
            result = ci.generate_checksums()

        assert result[f1.name] == _sha256(b"content_a")
        assert result[f2.name] == _sha256(b"content_b")

        saved = json.loads(checksums_file.read_text(encoding='utf-8'))
        assert saved == result

    def test_generate_then_verify_passes(self, tmp_path):
        """generate_checksums() then verify_cython_integrity() returns True."""
        import cython_integrity as ci

        pyd = tmp_path / "indicators.cp311-win_amd64.pyd"
        pyd.write_bytes(b"legitimate binary")

        checksums_file = tmp_path / "checksums.json"
        with patch.object(ci, '_BIN_DIR', str(tmp_path)), \
             patch.object(ci, '_CHECKSUMS_FILE', str(checksums_file)):
            ci.generate_checksums()
            result = ci.verify_cython_integrity()

        assert result is True
