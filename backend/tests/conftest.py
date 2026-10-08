"""Test fixtures: isolated sqlite DB + overridden get_db + temp storage."""
import os
import sys
import tempfile

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite:///{tmp}/test.db"
os.environ["STORAGE_BACKEND"] = "local"
os.environ["STORAGE_DIR"] = os.path.join(tmp, "storage")
os.environ["JWT_SECRET"] = "test-secret-min-32-chars-0123456789abcdef"
# Part 2 (contract discovery search): tests point at a DEDICATED index on a
# host-reachable cluster — never at the live `contract_search` alias, so a
# running stack's data can not be touched by the suite. If no cluster is
# reachable the search tests skip (see tests/test_contract_search.py);
# every other test is unaffected because indexing hooks fail soft.
os.environ["ELASTICSEARCH_URL"] = os.environ.get(
    "ELASTICSEARCH_TEST_URL", "http://localhost:9200"
)
os.environ["ELASTICSEARCH_INDEX"] = "contract_search_test"

from app.db.base import Base  # noqa: E402
from app.db.session import get_db  # noqa: E402
from app.main import app  # noqa: E402

engine = create_engine(f"sqlite:///{tmp}/test.db", connect_args={"check_same_thread": False})
TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base.metadata.create_all(bind=engine)


def _override():
    db = TestingSession()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = _override


@pytest.fixture
def client():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    with TestClient(app) as c:
        yield c


def register(client, email="a@x.com", pw="password123", org="Acme"):
    r = client.post("/api/v1/auth/register", json={
        "email": email, "password": pw, "org_name": org})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def auth(token):
    return {"Authorization": f"Bearer {token}"}
