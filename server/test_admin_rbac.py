"""Admin RBAC API tests."""
from __future__ import annotations

import importlib
import os
import tempfile
import unittest
from pathlib import Path

TEST_PASSWORD = "TradeAdmin@2026!BS"
os.environ.setdefault("TA_JWT_SECRET", "test-jwt-secret-for-rbac")


class AdminRbacTests(unittest.TestCase):
    def setUp(self) -> None:
        from app.auth import hash_admin_password

        self._tmpdir = tempfile.TemporaryDirectory()
        self._db_path = str(Path(self._tmpdir.name) / "rbac.db")
        os.environ["TA_DB_PATH"] = self._db_path
        os.environ.pop("TA_ADMIN_PASSWORD", None)

        import app.config as config_mod

        importlib.reload(config_mod)
        os.environ["TA_ADMIN_PASSWORD_HASH"] = hash_admin_password(TEST_PASSWORD)

        from app.database import init_db

        init_db()

        import app.main as main_mod

        importlib.reload(main_mod)
        from fastapi.testclient import TestClient

        self.client = TestClient(main_mod.app)
        login = self.client.post(
            "/api/v1/admin/login",
            json={"username": "admin", "password": TEST_PASSWORD},
        )
        self.assertEqual(login.status_code, 200, login.text)
        self.token = login.json()["access_token"]
        self.headers = {"Authorization": f"Bearer {self.token}"}

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_me_returns_all_modules_for_superadmin(self) -> None:
        res = self.client.get("/api/v1/admin/me", headers=self.headers)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["username"], "admin")
        keys = {item["key"] for item in data["nav"]}
        self.assertIn("roles", keys)
        self.assertIn("users", keys)

    def test_create_role_and_user_with_limited_access(self) -> None:
        role_res = self.client.post(
            "/api/v1/admin/roles",
            headers=self.headers,
            json={
                "name": "看板访客",
                "description": "仅看板",
                "modules": ["dashboard"],
            },
        )
        self.assertEqual(role_res.status_code, 200, role_res.text)
        role_id = role_res.json()["role"]["id"]

        user_res = self.client.post(
            "/api/v1/admin/users",
            headers=self.headers,
            json={
                "username": "viewer1",
                "password": "ViewerPass@2026",
                "display_name": "看板用户",
                "role_id": role_id,
            },
        )
        self.assertEqual(user_res.status_code, 200, user_res.text)

        login = self.client.post(
            "/api/v1/admin/login",
            json={"username": "viewer1", "password": "ViewerPass@2026"},
        )
        self.assertEqual(login.status_code, 200)
        viewer_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        me = self.client.get("/api/v1/admin/me", headers=viewer_headers)
        self.assertEqual(me.status_code, 200)
        nav_keys = {item["key"] for item in me.json()["nav"]}
        self.assertEqual(nav_keys, {"dashboard"})

        denied = self.client.get("/api/v1/admin/devices", headers=viewer_headers)
        self.assertEqual(denied.status_code, 403)

        allowed = self.client.get("/api/v1/admin/stats", headers=viewer_headers)
        self.assertEqual(allowed.status_code, 200)

    def test_admin_user_hidden_from_user_list(self) -> None:
        from app.database import get_admin_user_by_username

        res = self.client.get("/api/v1/admin/users", headers=self.headers)
        self.assertEqual(res.status_code, 200)
        names = {u["username"] for u in res.json()["users"]}
        self.assertNotIn("admin", names)

        admin_user = get_admin_user_by_username("admin")
        self.assertIsNotNone(admin_user)
        admin_id = admin_user["id"]
        self.assertEqual(
            self.client.patch(
                f"/api/v1/admin/users/{admin_id}",
                headers=self.headers,
                json={"display_name": "hack"},
            ).status_code,
            400,
        )
        self.assertEqual(
            self.client.delete(f"/api/v1/admin/users/{admin_id}", headers=self.headers).status_code,
            400,
        )

    def test_audit_hides_superadmin_operations(self) -> None:
        from app.database import get_conn, log_audit

        with get_conn() as conn:
            log_audit(conn, "test_super_op", detail="super", actor="admin")
            log_audit(conn, "test_ops_op", detail="ops", actor="ops_auditor")

        role_res = self.client.post(
            "/api/v1/admin/roles",
            headers=self.headers,
            json={
                "name": "审计员",
                "description": "仅操作日志",
                "modules": ["audit"],
            },
        )
        self.assertEqual(role_res.status_code, 200, role_res.text)
        role_id = role_res.json()["role"]["id"]
        self.client.post(
            "/api/v1/admin/users",
            headers=self.headers,
            json={
                "username": "ops_auditor",
                "password": "AuditorPass@2026",
                "role_id": role_id,
            },
        )
        login = self.client.post(
            "/api/v1/admin/login",
            json={"username": "ops_auditor", "password": "AuditorPass@2026"},
        )
        self.assertEqual(login.status_code, 200)
        auditor_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        for headers in (auditor_headers, self.headers):
            res = self.client.get("/api/v1/admin/audit", headers=headers)
            self.assertEqual(res.status_code, 200, res.text)
            actions = {item["action"] for item in res.json()["items"]}
            self.assertNotIn("test_super_op", actions)
            self.assertIn("test_ops_op", actions)

    def test_viewer_cannot_manage_roles(self) -> None:
        role_res = self.client.post(
            "/api/v1/admin/roles",
            headers=self.headers,
            json={"name": "只读2", "description": "", "modules": ["trades"]},
        )
        role_id = role_res.json()["role"]["id"]
        self.client.post(
            "/api/v1/admin/users",
            headers=self.headers,
            json={
                "username": "trader_ro",
                "password": "TraderPass@2026",
                "role_id": role_id,
            },
        )
        login = self.client.post(
            "/api/v1/admin/login",
            json={"username": "trader_ro", "password": "TraderPass@2026"},
        )
        viewer_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        res = self.client.get("/api/v1/admin/roles", headers=viewer_headers)
        self.assertEqual(res.status_code, 403)


if __name__ == "__main__":
    unittest.main()
