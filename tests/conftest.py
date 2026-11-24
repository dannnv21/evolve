import io
import shutil
import sqlite3

import pytest
from PIL import Image

from app import create_app
from app.config import TestingConfig

from .sqlite_schema import sqlite_script

PASSWORD = "Evolve2026!"
CLIENT_EMAIL = "alex@correo.test"
ADMIN_EMAIL = "daniel@evolve.test"
EMPLOYEE_EMAIL = "andrea@evolve.test"


@pytest.fixture(scope="session")
def template_database(tmp_path_factory):
    path = tmp_path_factory.mktemp("db") / "plantilla.sqlite"
    connection = sqlite3.connect(path)
    connection.executescript(sqlite_script())
    connection.close()
    return path


@pytest.fixture()
def app(template_database, tmp_path):
    database_path = tmp_path / "evolve.sqlite"
    shutil.copy(template_database, database_path)

    class Config(TestingConfig):
        DATABASE_URL = f"sqlite:///{database_path}"
        STORAGE_DIR = str(tmp_path / "storage")

    application = create_app(Config)
    application.test_cli_runner().invoke(args=["seed-storage"])
    return application


@pytest.fixture()
def client(app):
    return app.test_client()


def login(client, email: str) -> dict:
    response = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200, response.get_json()
    return {"Authorization": f"Bearer {response.get_json()['access_token']}"}


@pytest.fixture()
def client_headers(client):
    return login(client, CLIENT_EMAIL)


@pytest.fixture()
def admin_headers(client):
    return login(client, ADMIN_EMAIL)


@pytest.fixture()
def employee_headers(client):
    return login(client, EMPLOYEE_EMAIL)


def image_file(name: str = "foto.jpg", color=(120, 120, 130)):
    buffer = io.BytesIO()
    Image.new("RGB", (64, 48), color).save(buffer, format="JPEG")
    buffer.seek(0)
    return buffer, name
