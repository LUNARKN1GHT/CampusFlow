"""测试库保护规则（T005）：库名安全检查与环境变量还原，均为无数据库用例。"""

import os

import pytest
from conftest import _temporary_database_url, _validate_test_database_url


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+psycopg://u:p@127.0.0.1:5432/campusflow_test",
        "postgresql+psycopg://u:p@127.0.0.1:5432/campusflow_e10_test",
        "postgresql+psycopg://u:p@db.internal:5432/anything_test",
    ],
)
def test_test_database_url_accepts_test_suffix(url: str) -> None:
    _validate_test_database_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+psycopg://u:p@127.0.0.1:5432/campusflow",
        "postgresql+psycopg://u:p@127.0.0.1:5432/campusflow_dev",
        "postgresql+psycopg://u:p@127.0.0.1:5432/campusflow_prod",
    ],
)
def test_test_database_url_rejects_business_database(url: str) -> None:
    with pytest.raises(RuntimeError, match="_test"):
        _validate_test_database_url(url)


def test_temporary_database_url_restores_previous_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CAMPUSFLOW_DATABASE_URL", "original")
    with _temporary_database_url("overridden"):
        assert os.environ["CAMPUSFLOW_DATABASE_URL"] == "overridden"
    assert os.environ["CAMPUSFLOW_DATABASE_URL"] == "original"


def test_temporary_database_url_removes_when_previously_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CAMPUSFLOW_DATABASE_URL", raising=False)
    with _temporary_database_url("overridden"):
        assert os.environ["CAMPUSFLOW_DATABASE_URL"] == "overridden"
    assert "CAMPUSFLOW_DATABASE_URL" not in os.environ
