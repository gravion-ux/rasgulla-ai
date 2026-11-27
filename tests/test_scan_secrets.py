"""The repository secret scanner and its allow-list.

The scanner keeps one allow-list for locations that legitimately contain a
credential-shaped string (a test fixture that proves secrets are never read).
That allow-list has to be honoured by the scan itself, and every entry has to
point at a file that actually exists — otherwise it silently rots.
"""
import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "scan-secrets.py"

PRIVATE_KEY_HEADER = "-----BEGIN RSA PRIVATE KEY-----"


def _scanner():
    """Load scripts/scan-secrets.py as a module."""
    spec = importlib.util.spec_from_file_location("scan_secrets", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a_private_key_block_is_reported(tmp_path):
    (tmp_path / "notes.txt").write_text(PRIVATE_KEY_HEADER + "\n", encoding="utf-8")
    _, hard, _, _ = _scanner().scan(tmp_path)
    assert ("notes.txt", 1, "Private key block") in hard


def test_an_allowed_match_is_not_reported(tmp_path, monkeypatch):
    module = _scanner()
    monkeypatch.setattr(module, "ALLOWED_MATCHES", {("notes.txt", "Private key block")})
    (tmp_path / "notes.txt").write_text(PRIVATE_KEY_HEADER + "\n", encoding="utf-8")
    _, hard, _, _ = module.scan(tmp_path)
    assert hard == []


def test_this_test_file_is_allow_listed():
    """The fixture above writes a PEM header, so the scanner has to be told
    that this one location is safe."""
    assert ("tests/test_scan_secrets.py", "Private key block") in _scanner().ALLOWED_MATCHES


def test_every_allowed_match_points_at_a_file_that_exists():
    root = SCRIPT.parents[1]
    for relative, _label in _scanner().ALLOWED_MATCHES:
        assert (root / relative).is_file(), relative
