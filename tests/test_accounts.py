import json
import logging
import pytest
import src.accounts as accounts


def test_auth_token_from_cookies_success(tmp_path, monkeypatch):
    monkeypatch.setattr(accounts, "COOKIES_DIR", tmp_path)
    token = "abc"
    (tmp_path / "user.json").write_text(json.dumps([{ "name": "auth-token", "value": token }]), encoding="utf-8")
    assert accounts.auth_token_from_cookies("user") == token


def test_auth_token_from_cookies_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(accounts, "COOKIES_DIR", tmp_path)
    assert accounts.auth_token_from_cookies("user") is None


def test_auth_token_from_cookies_invalid_json(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(accounts, "COOKIES_DIR", tmp_path)
    (tmp_path / "user.json").write_text("{", encoding="utf-8")
    with caplog.at_level(logging.ERROR):
        assert accounts.auth_token_from_cookies("user") is None
        assert "Failed to load auth token" in caplog.text
