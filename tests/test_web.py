import re
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from funwithcam.storage.auth_repository import AuthRepository
from funwithcam.storage.database import Database
from funwithcam.web import create_app


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class WebAppTest(unittest.TestCase):
    def test_login_and_add_today_item(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "test.db"
            database = Database(database_path)
            database.initialize()
            AuthRepository(database).set_password(
                "parent",
                "long-password",
                datetime.now(timezone.utc),
            )
            app = create_app(
                PROJECT_ROOT / "config" / "config.example.yaml",
                database_path,
            )

            with TestClient(app) as client:
                unauthorized = client.get("/", follow_redirects=False)
                self.assertEqual(303, unauthorized.status_code)
                self.assertEqual("/login", unauthorized.headers["location"])

                login = client.post(
                    "/login",
                    data={"username": "parent", "password": "long-password"},
                    follow_redirects=False,
                )
                self.assertEqual(303, login.status_code)

                dashboard = client.get("/")
                self.assertEqual(200, dashboard.status_code)
                csrf = re.search(
                    r'name="csrf" value="([^"]+)"',
                    dashboard.text,
                ).group(1)

                added = client.post(
                    "/today/items",
                    data={
                        "csrf": csrf,
                        "item_type": "homework",
                        "title": "数学练习册",
                        "estimated_minutes": "30",
                        "preparation_minutes": "0",
                    },
                    follow_redirects=False,
                )
                self.assertEqual(303, added.status_code)
                self.assertIn("数学练习册", client.get("/").text)

    def test_rejects_post_without_csrf(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "test.db"
            database = Database(database_path)
            database.initialize()
            AuthRepository(database).set_password(
                "parent",
                "long-password",
                datetime.now(timezone.utc),
            )
            app = create_app(
                PROJECT_ROOT / "config" / "config.example.yaml",
                database_path,
            )

            with TestClient(app) as client:
                client.post(
                    "/login",
                    data={"username": "parent", "password": "long-password"},
                )
                response = client.post(
                    "/today/items",
                    data={
                        "item_type": "homework",
                        "title": "不应写入",
                    },
                )

        self.assertEqual(403, response.status_code)


if __name__ == "__main__":
    unittest.main()
