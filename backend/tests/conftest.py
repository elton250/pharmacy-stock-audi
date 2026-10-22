"""Pytest configuration: points the app at the test database and provides fixtures."""
import os
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))
os.chdir(BACKEND_DIR)

from app.config import Settings  # noqa: E402

# The suite truncates every table before each test, so it must never be pointed
# at a database that holds data you want to keep. ``PHARMACY_TEST_DB_URL``
# allows a throwaway database to be used instead of the shared ``pharmacy_test``;
# otherwise the test URL from backend/.env (DATABASE_URL_TEST) is used.
TEST_DB_URL = os.environ.get("PHARMACY_TEST_DB_URL") or Settings().database_url_test
os.environ["DATABASE_URL"] = TEST_DB_URL
os.environ["DATABASE_URL_TEST"] = TEST_DB_URL
os.environ["SECRET_KEY"] = "test-secret-key-for-pytest-only-0123456789"
os.environ["ENVIRONMENT"] = "test"
os.environ["COOKIE_SECURE"] = "false"

from alembic import command as alembic_command  # noqa: E402
from alembic.config import Config as AlembicConfig  # noqa: E402

_alembic_cfg = AlembicConfig(str(BACKEND_DIR / "alembic.ini"))
alembic_command.upgrade(_alembic_cfg, "head")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import inspect, text  # noqa: E402

from app.database import SessionLocal, engine as app_engine  # noqa: E402
from app.main import app  # noqa: E402
from app.routers import auth as auth_router  # noqa: E402


def truncate_all_tables() -> None:
    inspector = inspect(app_engine)
    tables = [t for t in inspector.get_table_names() if t != "alembic_version"]
    if not tables:
        return
    listed = ", ".join(f'"{t}"' for t in tables)
    with app_engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {listed} RESTART IDENTITY CASCADE"))


class ApiClient:
    """TestClient wrapper that auto-attaches the CSRF header for mutating calls."""

    def __init__(self, client: TestClient):
        self.client = client

    def _csrf_headers(self, extra: dict | None = None) -> dict:
        headers = dict(extra or {})
        token = self.client.cookies.get("pharmacy_csrf")
        if token:
            headers["X-CSRF-Token"] = token
        return headers

    def get(self, url: str, **kwargs):
        return self.client.get(url, **kwargs)

    def post(self, url: str, json=None, **kwargs):
        kwargs["headers"] = self._csrf_headers(kwargs.pop("headers", None))
        return self.client.post(url, json=json, **kwargs)

    def patch(self, url: str, json=None, **kwargs):
        kwargs["headers"] = self._csrf_headers(kwargs.pop("headers", None))
        return self.client.patch(url, json=json, **kwargs)

    def put(self, url: str, json=None, **kwargs):
        kwargs["headers"] = self._csrf_headers(kwargs.pop("headers", None))
        return self.client.put(url, json=json, **kwargs)

    def delete(self, url: str, **kwargs):
        kwargs["headers"] = self._csrf_headers(kwargs.pop("headers", None))
        return self.client.delete(url, **kwargs)


@pytest.fixture()
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture()
def raw_client():
    # Truncate at setup only: truncating during teardown can deadlock against
    # sessions belonging to other fixtures that have not been closed yet.
    truncate_all_tables()
    auth_router.reset_rate_limiter()
    with TestClient(app) as client:
        yield client


@pytest.fixture()
def api(raw_client) -> ApiClient:
    return ApiClient(raw_client)


@pytest.fixture()
def admin_api(api: ApiClient) -> ApiClient:
    """API client logged in as the initial administrator."""
    resp = api.post(
        "/api/auth/setup",
        json={"username": "admin", "password": "adminpass123", "full_name": "Site Admin"},
    )
    assert resp.status_code == 201, resp.text
    return api


def create_user(api: ApiClient, username: str, password: str, roles: list[str]) -> dict:
    resp = api.post(
        "/api/users",
        json={
            "username": username,
            "password": password,
            "full_name": username.title(),
            "role_names": roles,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def login(api: ApiClient, username: str, password: str) -> ApiClient:
    resp = api.post("/api/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return api


@pytest.fixture()
def pharmacist_api(admin_api: ApiClient) -> ApiClient:
    create_user(admin_api, "pharma1", "pharmapass123", ["pharmacist"])
    admin_api.post("/api/auth/logout")
    return login(admin_api, "pharma1", "pharmapass123")


@pytest.fixture()
def counter_api(admin_api: ApiClient) -> ApiClient:
    create_user(admin_api, "counter1", "counterpass123", ["stock_counter"])
    admin_api.post("/api/auth/logout")
    return login(admin_api, "counter1", "counterpass123")


FIXTURE_MEDICINE = {
    "name": "Paracetamol 500mg",
    "generic_name": "Paracetamol",
    "brand_name": "Panadol",
    "strength": "500mg",
    "dosage_form": "Tablet",
    "manufacturer": "GSK",
    "category": "Analgesic",
    "base_unit": "tablet",
    "opening_stock": 0,
    "reorder_level": 100,
    "barcodes": [{"barcode": "8901234567890", "is_primary": True}],
    "packages": [
        {"name": "Blister", "units_per_parent": 10, "sort_order": 1},
        {"name": "Box", "parent_name": "Blister", "units_per_parent": 10, "sort_order": 2},
    ],
}
