from pathlib import Path

import pytest

from campusflow.application import bootstrap
from campusflow.core.config import Settings
from campusflow.infrastructure.secrets import dpapi


def locked(code: int = 5) -> PermissionError:
    error = PermissionError("synthetic lock")
    error.winerror = code
    return error


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
