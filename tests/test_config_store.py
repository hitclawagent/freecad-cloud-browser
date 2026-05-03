import os
import unittest.mock
import pytest
from core.config_store import ConfigStore


def test_config_dir_is_directory(config_store):
    assert os.path.isdir(config_store.config_dir)


def test_list_accounts_empty(config_store):
    assert config_store.list_accounts() == []


def test_add_and_list(config_store):
    account_id = config_store.add_account("google_drive", "My Drive", {})
    assert isinstance(account_id, str) and len(account_id) > 0
    accounts = config_store.list_accounts()
    assert len(accounts) == 1
    assert "id" in accounts[0]


def test_get_account_deepcopy(config_store):
    account_id = config_store.add_account("google_drive", "My Drive", {})
    result = config_store.get_account(account_id)
    result["name"] = "MUTATED"
    assert config_store.get_account(account_id)["name"] != "MUTATED"


def test_list_accounts_deepcopy(config_store):
    config_store.add_account("google_drive", "My Drive", {})
    accounts = config_store.list_accounts()
    accounts[0]["name"] = "MUTATED"
    assert config_store.list_accounts()[0]["name"] != "MUTATED"


def test_delete_account(config_store):
    account_id = config_store.add_account("google_drive", "My Drive", {})
    config_store.delete_account(account_id)
    assert config_store.get_account(account_id) is None


def test_save_persists(config_store):
    account_id = config_store.add_account("google_drive", "My Drive", {"extra": "val"})
    store2 = ConfigStore(config_path=config_store._path)
    account = store2.get_account(account_id)
    assert account is not None
    assert account["extra"] == "val"


def test_atomic_save_on_corrupted_json(config_store):
    # Write garbage to config file before constructing a new ConfigStore
    with open(config_store._path, "w") as f:
        f.write("not valid json{{{{")
    store2 = ConfigStore(config_path=config_store._path)
    assert store2.list_accounts() == []


def test_get_set_setting(config_store):
    config_store.set_setting("show_all", True)
    assert config_store.get_setting("show_all") is True
    # Persists across reload
    store2 = ConfigStore(config_path=config_store._path)
    assert store2.get_setting("show_all") is True


def test_save_raises_on_permission_error(config_store):
    with unittest.mock.patch("os.replace", side_effect=PermissionError("disk full")):
        with pytest.raises(PermissionError):
            config_store.set_setting("x", 1)
