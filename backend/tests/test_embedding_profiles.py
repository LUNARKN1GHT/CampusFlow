"""Q002：真实 PostgreSQL 的配置持久化、幂等与不可修改约束。"""

from dataclasses import replace

import pytest
from conftest import TEST_DATABASE_URL
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from campusflow.domain.embeddings import EmbeddingProfile
from campusflow.infrastructure.db.models import EmbeddingProfileRecord
from campusflow.infrastructure.embedding_profiles import SqlEmbeddingProfiles


def test_profiles_persist_and_repeated_registration_does_not_duplicate(client):
    engine = create_engine(TEST_DATABASE_URL)
    profile = EmbeddingProfile()
    next_profile = replace(profile, revision="next")
    with Session(engine) as session, session.begin():
        repository = SqlEmbeddingProfiles(session)
        first_id = repository.register(profile)
        assert repository.register(profile) == first_id
        assert repository.register(next_profile) != first_id
    with Session(engine) as session:
        repository = SqlEmbeddingProfiles(session)
        assert repository.get(first_id) == profile
        assert repository.get(next_profile.id) == next_profile
        assert repository.get("missing") is None
        assert session.scalar(select(func.count()).select_from(EmbeddingProfileRecord)) == 2
    engine.dispose()


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE embedding_profiles SET revision='overwritten'",
        "UPDATE embedding_profiles SET generation_config='{}'::jsonb",
        "DELETE FROM embedding_profiles",
    ],
)
def test_database_itself_prevents_rewriting_or_deleting_profile(client, statement):
    engine = create_engine(TEST_DATABASE_URL)
    profile = EmbeddingProfile()
    with Session(engine) as session, session.begin():
        SqlEmbeddingProfiles(session).register(profile)
    with engine.connect() as connection:
        with pytest.raises(DBAPIError, match="immutable"):
            connection.execute(text(statement))
        connection.rollback()
    with Session(engine) as session:
        assert SqlEmbeddingProfiles(session).get(profile.id) == profile
    engine.dispose()
