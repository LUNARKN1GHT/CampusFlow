import json
from pathlib import Path

import pytest

from campusflow.application import bootstrap
from campusflow.core.config import Settings
from campusflow.infrastructure.secrets import dpapi


@pytest.mark.parametrize("bad_value", [None, 123, {}, []])
@pytest.mark.parametrize("operation", ["set", "delete"])
def test_malformed_entry_never_disappears_on_update(tmp_path, monkeypatch, bad_value, operation):
    target = tmp_path / "secrets.dpapi"
    original = json.dumps({"damaged": bad_value, "existing": "synthetic-ciphertext"})
    target.write_text(original, encoding="utf-8")
    store = dpapi.WindowsDpapiSecretStore(target)
    monkeypatch.setattr(dpapi, "_protect", lambda value: b"synthetic-encrypted")
    with pytest.raises(dpapi.DpapiError, match="格式无效"):
        if operation == "set":
            store.set("new", "synthetic-secret")
        else:
            store.delete("existing")
    assert target.read_text(encoding="utf-8") == original
    assert list(tmp_path.glob(".secrets-*.tmp")) == []


def locked(code: int = 5) -> PermissionError:
    error = PermissionError("synthetic lock")
    error.winerror = code
    return error


@pytest.mark.parametrize("operation", ["get", "set", "delete"])
def test_duplicate_records_are_rejected_without_losing_original(tmp_path, operation):
    target = tmp_path / "secrets.dpapi"
    original = '{"account":"first","account":"second"}'
    target.write_text(original, encoding="utf-8")
    store = dpapi.WindowsDpapiSecretStore(target)
    with pytest.raises(dpapi.DpapiError, match="重复记录"):
        if operation == "set":
            store.set("new", "synthetic")
        else:
            getattr(store, operation)("account")
    assert target.read_text(encoding="utf-8") == original


@pytest.mark.parametrize("code", [5, 32, 33])
def test_temporary_lock_retries_same_ciphertext(tmp_path, monkeypatch, code):
    target = tmp_path / "secrets.dpapi"
    store = dpapi.WindowsDpapiSecretStore(target)
    store._save({"account": "old-ciphertext"})
    replace = dpapi.os.replace
    attempts = []
    waits = []

    def flaky(source, destination):
        attempts.append(Path(source).read_bytes())
        if len(attempts) < 3:
            raise locked(code)
        replace(source, destination)

    monkeypatch.setattr(dpapi.os, "replace", flaky)
    monkeypatch.setattr(dpapi.time, "sleep", waits.append)
    store._save({"account": "new-ciphertext"})
    assert store._load() == {"account": "new-ciphertext"}
    assert len(attempts) == 3 and len(set(attempts)) == 1
    assert waits == [0.2, 0.2]
    assert list(tmp_path.glob(".secrets-*.tmp")) == []


def test_persistent_lock_preserves_previous_file_and_stops(tmp_path, monkeypatch):
    target = tmp_path / "secrets.dpapi"
    store = dpapi.WindowsDpapiSecretStore(target)
    store._save({"account": "old-ciphertext"})
    original = target.read_bytes()
    attempts = []
    waits = []

    def blocked(*args):
        attempts.append(1)
        raise locked()

    monkeypatch.setattr(dpapi.os, "replace", blocked)
    monkeypatch.setattr(dpapi.time, "sleep", waits.append)
    with pytest.raises(dpapi.DpapiError, match="未改用明文"):
        store._save({"account": "new-ciphertext"})
    assert len(attempts) == 6
    assert waits == [0.2] * 5
    assert target.read_bytes() == original
    assert not (tmp_path / "secrets.json").exists()


def test_other_io_errors_are_not_retried(tmp_path, monkeypatch):
    waits = []

    def disk_full(*args):
        raise OSError(28, "synthetic disk full")

    monkeypatch.setattr(dpapi.os, "replace", disk_full)
    monkeypatch.setattr(dpapi.time, "sleep", waits.append)
    with pytest.raises(OSError, match="disk full"):
        dpapi.WindowsDpapiSecretStore(tmp_path / "secrets.dpapi")._save({})
    assert waits == []


@pytest.mark.parametrize("allow_fallback", [None, True])
def test_bootstrap_does_not_downgrade_dpapi_failure(tmp_path, monkeypatch, allow_fallback):
    requested = []
    deleted = []

    class FailingStore:
        backend = "windows-dpapi"

        def set(self, account, value):
            raise dpapi.DpapiError("synthetic failure")

        def delete(self, account):
            deleted.append(account)

    def factory(name, root):
        requested.append(name)
        return FailingStore()

    monkeypatch.setattr(bootstrap, "create_secret_store", factory)
    settings = Settings(_env_file=None, secret_backend="auto")
    with pytest.raises(bootstrap.BootstrapError, match="未改用明文"):
        bootstrap._install_secrets(
            settings,
            tmp_path,
            None,
            {"account": "synthetic-secret"},
            allow_fallback=allow_fallback,
        )
    assert requested == ["auto"]
    assert deleted == []
    assert not (tmp_path / "secrets.json").exists()
