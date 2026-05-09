"""Tests for append_to_allow — the only function with file IO."""

import json
import os

import pytest

import ntfy_permission as np


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    """Redirect ~ to a tmp path so settings.json mutations are sandboxed."""
    monkeypatch.setenv("HOME", str(tmp_path))
    claude = tmp_path / ".claude"
    claude.mkdir()
    return tmp_path


def write_settings(home, contents):
    settings = home / ".claude" / "settings.json"
    settings.write_text(json.dumps(contents))
    return settings


def read_settings(home):
    return json.loads((home / ".claude" / "settings.json").read_text())


class TestAppendToAllow:
    def test_returns_false_when_pattern_empty(self, fake_home):
        write_settings(fake_home, {"permissions": {"allow": []}})
        assert np.append_to_allow("") is False
        assert np.append_to_allow(None) is False

    def test_returns_false_when_settings_missing(self, fake_home):
        # no settings.json written
        assert np.append_to_allow("Bash(ls *)") is False

    def test_returns_false_when_settings_malformed(self, fake_home):
        (fake_home / ".claude" / "settings.json").write_text("{ this is not json")
        assert np.append_to_allow("Bash(ls *)") is False

    def test_appends_to_existing_allow(self, fake_home):
        write_settings(fake_home, {"permissions": {"allow": ["Bash(npm *)"]}})
        assert np.append_to_allow("Bash(rm /tmp/*)") is True
        s = read_settings(fake_home)
        assert s["permissions"]["allow"] == ["Bash(npm *)", "Bash(rm /tmp/*)"]

    def test_idempotent_for_existing_pattern(self, fake_home):
        write_settings(fake_home, {"permissions": {"allow": ["Bash(npm *)"]}})
        assert np.append_to_allow("Bash(npm *)") is True
        s = read_settings(fake_home)
        # not duplicated
        assert s["permissions"]["allow"] == ["Bash(npm *)"]

    def test_creates_permissions_block_if_missing(self, fake_home):
        write_settings(fake_home, {"model": "opus"})
        assert np.append_to_allow("Bash(ls *)") is True
        s = read_settings(fake_home)
        assert s["model"] == "opus"
        assert s["permissions"]["allow"] == ["Bash(ls *)"]

    def test_creates_allow_list_if_missing(self, fake_home):
        write_settings(fake_home, {"permissions": {"deny": []}})
        assert np.append_to_allow("Bash(ls *)") is True
        s = read_settings(fake_home)
        assert s["permissions"]["allow"] == ["Bash(ls *)"]
        # untouched
        assert s["permissions"]["deny"] == []

    def test_writes_audit_log(self, fake_home):
        write_settings(fake_home, {"permissions": {"allow": []}})
        assert np.append_to_allow("Bash(ls *)") is True
        log = (fake_home / ".claude" / ".ntfy_permission.log").read_text()
        assert "Bash(ls *)" in log
        assert "added" in log

    def test_no_temp_file_left_behind(self, fake_home):
        write_settings(fake_home, {"permissions": {"allow": []}})
        np.append_to_allow("Bash(ls *)")
        leftovers = list((fake_home / ".claude").glob("settings.json.ntfy.*"))
        assert leftovers == []

    def test_write_preserves_unrelated_keys(self, fake_home):
        write_settings(
            fake_home,
            {
                "model": "opus",
                "permissions": {"allow": ["Existing(*)"]},
                "hooks": {"SessionStart": [{"matcher": ""}]},
                "theme": "dark",
            },
        )
        np.append_to_allow("Bash(ls *)")
        s = read_settings(fake_home)
        assert s["model"] == "opus"
        assert s["theme"] == "dark"
        assert s["hooks"] == {"SessionStart": [{"matcher": ""}]}
        assert s["permissions"]["allow"] == ["Existing(*)", "Bash(ls *)"]

    def test_atomic_replace_on_io_error(self, fake_home, monkeypatch):
        write_settings(fake_home, {"permissions": {"allow": ["Existing(*)"]}})

        def fail_replace(*args, **kwargs):
            raise OSError("simulated disk full")

        monkeypatch.setattr(os, "replace", fail_replace)
        assert np.append_to_allow("Bash(ls *)") is False
        # Original settings unchanged
        s = read_settings(fake_home)
        assert s["permissions"]["allow"] == ["Existing(*)"]
        # Temp file cleaned up
        assert list((fake_home / ".claude").glob("settings.json.ntfy.*")) == []
