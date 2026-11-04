"""备件申请邮件提醒回归（独立临时 SQLite，不依赖真实 SMTP/LDAP）。"""
import os
import tempfile
import unittest
from unittest.mock import patch

_db = tempfile.TemporaryDirectory(prefix="partsdepot-notify-")
os.environ["DATABASE_URL"] = "sqlite:///" + _db.name + "/notify.db"
os.environ["SECRET_KEY"] = "test-notify-secret"
os.environ["ADMIN_USERNAME"] = "notify-admin"
os.environ["ADMIN_PASSWORD"] = "Notify-Test-Pass!"

from fastapi.testclient import TestClient  # noqa: E402

from main import app  # noqa: E402
from core.database import SessionLocal  # noqa: E402
from core.models import User  # noqa: E402


class RequestNotifyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.client.__enter__()
        admin = cls._login("notify-admin", "Notify-Test-Pass!")
        cls.admin_auth = admin["access_token"]
        cls.wh = cls._call("POST", "/warehouses/", {"code": "N1", "name": "备件仓"})["id"]
        cls._call("POST", f"/users/{admin['user']['id']}/switch-warehouse?warehouse_id={cls.wh}")
        cls._call("POST", "/locations/", {"warehouse_id": cls.wh, "location_code": "N-L1", "name": "备件库位"})
        cls._call("POST", "/goods/", {"barcode": "N-B1", "name": "备件A", "price": 5})
        cls.supervisor = cls._call("POST", "/users/", {
            "username": "notify-sup", "password": "password2", "full_name": "仓库主管",
            "role": "supervisor", "warehouse_ids": [cls.wh],
        })["id"]
        # 标记为 LDAP 用户（AD 邮箱查询仅对 LDAP 账号生效）
        db = SessionLocal()
        try:
            db.query(User).filter(User.id == cls.supervisor).update({"is_ldap_user": True})
            db.commit()
        finally:
            db.close()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        _db.cleanup()

    @classmethod
    def _login(cls, username, password):
        resp = cls.client.post("/api/token", data={"username": username, "password": password})
        assert resp.status_code == 200, resp.text
        return resp.json()

    @classmethod
    def _call(cls, method, path, payload=None, expected=200):
        headers = {"Authorization": "Bearer " + cls.admin_auth}
        resp = cls.client.request(method, "/api" + path, json=payload, headers=headers)
        assert resp.status_code == expected, (method, path, resp.status_code, resp.text)
        return resp.json()

    def _submit(self, payload):
        return self.client.post("/api/requests/", json=payload,
                                headers={"X-Real-IP": "198.51.100.200"})

    def test_new_request_notifies_supervisor_ad_email(self):
        sent = []

        def fake_lookup(username):
            return f"{username}@corp.example.com"

        def fake_send(recipients, subject, body):
            sent.append({"to": list(recipients), "subject": subject, "body": body})
            return True, ""

        with patch("core.notify.ldap_lookup_email", side_effect=fake_lookup), \
             patch("core.notify.mail.send_mail", side_effect=fake_send):
            resp = self._submit({
                "applicant_name": "张三", "department": "工程管理部", "description": "领取备件",
                "warehouse_id": self.wh, "items": [{"barcode": "N-B1", "quantity": 2}],
            })
        self.assertEqual(resp.status_code, 201, resp.text)
        self.assertEqual(len(sent), 1, sent)
        self.assertEqual(sent[0]["to"], ["notify-sup@corp.example.com"])
        self.assertIn("新申请", sent[0]["subject"])
        self.assertIn("N-B1", sent[0]["body"])
        self.assertIn("备件仓", sent[0]["subject"])

    def test_approved_request_notifies_applicant_email(self):
        sent = []

        def fake_send(recipients, subject, body):
            sent.append({"to": list(recipients), "subject": subject})
            return True, ""

        resp = self._submit({
            "applicant_name": "李四", "department": "质量管理", "description": "无明细申请",
            "contact": "lisi@corp.example.com", "warehouse_id": self.wh,
        })
        self.assertEqual(resp.status_code, 201, resp.text)
        req_id = resp.json()["id"]
        sent.clear()  # 忽略新申请通知
        with patch("core.notify.ldap_lookup_email", side_effect=lambda u: f"{u}@corp.example.com"), \
             patch("core.notify.mail.send_mail", side_effect=fake_send):
            r = self.client.post(f"/api/requests/{req_id}/status", json={"status": "approved"},
                                 headers={"Authorization": "Bearer " + self.admin_auth})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(len(sent), 1, sent)
        self.assertEqual(sent[0]["to"], ["lisi@corp.example.com"])
        self.assertIn("已通过", sent[0]["subject"])

    def test_mail_failure_does_not_break_request_submission(self):
        def boom(recipients, subject, body):
            raise RuntimeError("smtp down")

        with patch("core.notify.ldap_lookup_email", return_value="sup@corp.example.com"), \
             patch("core.notify.mail.send_mail", side_effect=boom):
            resp = self._submit({
                "applicant_name": "王五", "department": "安全管理部", "description": "SMTP 故障",
                "warehouse_id": self.wh, "items": [{"barcode": "N-B1", "quantity": 1}],
            })
        self.assertEqual(resp.status_code, 201, resp.text)


if __name__ == "__main__":
    unittest.main()
