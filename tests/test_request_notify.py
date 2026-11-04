"""备件申请邮件提醒回归（独立临时 SQLite，不依赖真实 SMTP/LDAP）。"""
import os
import tempfile
import unittest
from unittest.mock import call, patch

_db = tempfile.TemporaryDirectory(prefix="partsdepot-notify-")
os.environ["DATABASE_URL"] = "sqlite:///" + _db.name + "/notify.db"
os.environ["SECRET_KEY"] = "test-notify-secret"
os.environ["ADMIN_USERNAME"] = "notify-admin"
os.environ["ADMIN_PASSWORD"] = "Notify-Test-Pass!"

from fastapi.testclient import TestClient  # noqa: E402

from main import app  # noqa: E402
from core.database import SessionLocal  # noqa: E402
from core.models import Config, Request, User, UserRole, UserWarehouse  # noqa: E402


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
            warehouse_admin = User(username="notify-warehouse-admin", role=UserRole.ADMIN, is_ldap_user=True)
            db.add_all([
                warehouse_admin,
                User(username="notify-global-admin", role=UserRole.ADMIN, is_ldap_user=True),
            ])
            db.flush()
            db.add(UserWarehouse(user_id=warehouse_admin.id, warehouse_id=cls.wh))
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

    def test_new_request_falls_back_to_warehouse_admin(self):
        with patch("core.notify.ldap_lookup_email", side_effect=["", "warehouse@corp.example.com"]) as lookup, \
             patch("core.notify.mail.send_mail", return_value=(True, "")) as send:
            resp = self._submit({"applicant_name": "赵六", "warehouse_id": self.wh, "description": "领取备件"})
        self.assertEqual(resp.status_code, 201, resp.text)
        self.assertEqual(lookup.call_args_list, [call("notify-sup"), call("notify-warehouse-admin")])
        send.assert_called_once()
        self.assertEqual(send.call_args.args[0], ["warehouse@corp.example.com"])

    def test_rejected_request_without_valid_contact_falls_back_to_global_admin(self):
        for contact in (None, "not-an-email"):
            with self.subTest(contact=contact):
                with patch("core.notify.deliver_email"):
                    payload = {"applicant_name": "赵六", "warehouse_id": self.wh, "description": "领取备件"}
                    resp = self._submit(payload)
                self.assertEqual(resp.status_code, 201, resp.text)
                req_id = resp.json()["id"]
                if contact is not None:
                    # 模拟邮箱格式校验上线前保存的申请。
                    with SessionLocal() as db:
                        db.query(Request).filter_by(id=req_id).one().contact = contact
                        db.commit()
                with patch("core.notify.ldap_lookup_email",
                           side_effect=["invalid", "", " global@corp.example.com "]) as lookup, \
                     patch("core.notify.mail.send_mail", return_value=(True, "")) as send:
                    resp = self._call("POST", f"/requests/{req_id}/status", {"status": "rejected"})
                self.assertEqual(resp["status"], "rejected")
                self.assertEqual(lookup.call_args_list, [
                    call("notify-sup"), call("notify-warehouse-admin"), call("notify-global-admin"),
                ])
                send.assert_called_once()
                self.assertEqual(send.call_args.args[0], ["global@corp.example.com"])
                self.assertIn("已驳回", send.call_args.args[1])

    def test_invalid_contact_is_rejected_on_new_request(self):
        with patch("core.notify.deliver_email") as deliver:
            resp = self._submit({
                "applicant_name": "赵六", "warehouse_id": self.wh,
                "description": "领取备件", "contact": "not-an-email",
            })
        self.assertEqual(resp.status_code, 422, resp.text)
        self.assertEqual(resp.json()["detail"][0]["loc"], ["body", "contact"])
        deliver.assert_not_called()

    def test_smtp_config_round_trip_and_password_updates(self):
        config = {
            "smtp_host": "smtp.example.com", "smtp_port": 465, "smtp_security": "ssl",
            "smtp_user": "mailer", "smtp_from": "mailer@example.com", "smtp_password": "initial-secret",
        }

        def stored_password():
            with SessionLocal() as db:
                return db.query(Config).filter_by(key="smtp_password").one().value

        smtp_env = {key.upper(): "" for key in config}
        with patch.dict(os.environ, smtp_env):
            result = self._call("PUT", "/notify/config", config)
            expected = {**config, "smtp_password": "******", "configured": True}
            self.assertEqual(result, expected)
            self.assertEqual(self._call("GET", "/notify/config"), expected)
            self.assertEqual(stored_password(), "initial-secret")

            for fields in ({}, {"smtp_password": None}, {"smtp_password": ""},
                           {"smtp_password": "  "}, {"smtp_password": "******"}):
                with self.subTest(fields=fields):
                    result = self._call("PUT", "/notify/config", {"smtp_port": 2525, **fields})
                    self.assertEqual(result, {**expected, "smtp_port": 2525})
                    self.assertEqual(stored_password(), "initial-secret")

            result = self._call("PUT", "/notify/config", {"smtp_password": "updated-secret"})
            self.assertEqual(result["smtp_password"], "******")
            self.assertEqual(stored_password(), "updated-secret")

    def test_non_admin_cannot_update_smtp_config(self):
        self._call("POST", "/users/", {
            "username": "notify-viewer", "password": "password2", "full_name": "普通员工", "role": "viewer",
            "warehouse_ids": [self.wh],
        })
        token = self._login("notify-viewer", "password2")["access_token"]
        resp = self.client.put("/api/notify/config", json={"smtp_host": "unauthorized.example.com"},
                               headers={"Authorization": "Bearer " + token})
        self.assertEqual(resp.status_code, 403, resp.text)

    def test_approved_request_notifies_applicant_email(self):
        sent = []

        def fake_send(recipients, subject, body):
            sent.append({"to": list(recipients), "subject": subject})
            return True, ""

        with patch("core.notify.deliver_email"):
            resp = self._submit({
                "applicant_name": "李四", "department": "质量管理", "description": "无明细申请",
                "contact": "lisi@corp.example.com", "warehouse_id": self.wh,
            })
        self.assertEqual(resp.status_code, 201, resp.text)
        req_id = resp.json()["id"]
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
