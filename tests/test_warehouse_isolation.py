"""Fresh SQLite integration checks for warehouse-scoped catalogs and roles."""
import os
import tempfile
import unittest

from fastapi.testclient import TestClient

_db = tempfile.TemporaryDirectory(prefix="partsdepot-scope-")
os.environ["DATABASE_URL"] = "sqlite:///" + _db.name + "/scope.db"
os.environ["SECRET_KEY"] = "test-scope-secret"
os.environ["ADMIN_USERNAME"] = "scope-admin"
os.environ["ADMIN_PASSWORD"] = "Scope-Test-Pass!"

from main import app  # noqa: E402 - environment must precede application import


class WarehouseIsolationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.client.__enter__()
        cls.admin = cls._login("scope-admin", "Scope-Test-Pass!")
        cls.admin_id = cls.admin["user"]["id"]
        cls.wh1 = cls._call("POST", "/warehouses/", {"code": "S1", "name": "工程"})["id"]
        cls.wh2 = cls._call("POST", "/warehouses/", {"code": "S2", "name": "行政"})["id"]
        cls._call("POST", f"/users/{cls.admin_id}/switch-warehouse?warehouse_id={cls.wh1}")
        cls.loc1 = cls._call("POST", "/locations/", {"warehouse_id": cls.wh1, "location_code": "S-L1", "name": "一号库位"})["id"]
        cls.loc2 = cls._call("POST", "/locations/", {"warehouse_id": cls.wh2, "location_code": "S-L2", "name": "二号库位"})["id"]
        cls.g1 = cls._call("POST", "/goods/", {"barcode": "SAME", "name": "工程耗材", "price": 3})["id"]
        cls._call("POST", f"/users/{cls.admin_id}/switch-warehouse?warehouse_id={cls.wh2}")
        cls.g2 = cls._call("POST", "/goods/", {"barcode": "SAME", "name": "行政用品", "price": 8})["id"]
        cls._call("POST", f"/users/{cls.admin_id}/switch-warehouse?warehouse_id={cls.wh1}")
        cls.supervisor = cls._call("POST", "/users/", {"username": "scope-supervisor", "password": "password2", "full_name": "仓库主管", "role": "supervisor", "warehouse_ids": [cls.wh1]})["id"]
        cls.viewer = cls._call("POST", "/users/", {"username": "scope-viewer", "password": "password2", "full_name": "普通员工", "role": "viewer", "warehouse_ids": [cls.wh1]})["id"]
        cls.supervisor_auth = cls._login("scope-supervisor", "password2")["access_token"]
        cls.viewer_auth = cls._login("scope-viewer", "password2")["access_token"]

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
    def _call(cls, method, path, payload=None, auth=None, expected=200):
        headers = {"Authorization": "Bearer " + (auth or cls.admin["access_token"])}
        resp = cls.client.request(method, "/api" + path, json=payload, headers=headers)
        assert resp.status_code == expected, (method, path, resp.status_code, resp.text)
        return resp.json()

    def test_duplicate_barcode_and_catalog_scope(self):
        self.assertNotEqual(self.g1, self.g2)
        rows = self._call("GET", "/goods/", auth=self.supervisor_auth)
        self.assertEqual([g["name"] for g in rows], ["工程耗材"])
        viewer_rows = self._call("GET", "/goods/", auth=self.viewer_auth)
        self.assertIsNone(viewer_rows[0]["price"])
        self._call("GET", f"/goods/{self.g2}", auth=self.supervisor_auth, expected=404)
        self._call("PUT", f"/goods/{self.g2}", {"name": "越权"}, auth=self.supervisor_auth, expected=404)
        self._call("POST", "/goods/", {"warehouse_id": self.wh2, "barcode": "OTHER", "name": "越权"}, auth=self.supervisor_auth, expected=403)
        self._call("POST", "/goods/", {"barcode": "SAME", "name": "重复"}, auth=self.supervisor_auth, expected=400)

    def test_legacy_request_item_uses_the_only_active_warehouse(self):
        req = self._call("POST", "/requests/", {"applicant_name": "历史申请", "description": "未指定仓库"}, expected=201)
        path = f"/requests/{req['id']}/items"
        self._call("POST", path, {"barcode": "SAME", "quantity": 1}, expected=400)
        self._call("PUT", f"/warehouses/{self.wh2}", {"is_active": False})
        try:
            item = self._call("POST", path, {"barcode": "SAME", "quantity": 1})
            self.assertEqual(item["name"], "工程耗材")
            edited = self._call("PUT", f"{path}/{item['id']}", {"barcode": "SAME", "quantity": 2})
            self.assertEqual(edited["quantity"], 2)
        finally:
            self._call("PUT", f"/warehouses/{self.wh2}", {"is_active": True})

    def test_stale_tab_cannot_write_to_changed_warehouse(self):
        self._call("POST", f"/users/{self.admin_id}/switch-warehouse?warehouse_id={self.wh2}")
        try:
            # A tab still showing warehouse 1 sends its displayed warehouse explicitly.
            self._call("POST", "/goods/", {"warehouse_id": self.wh1, "barcode": "STALE-TAB", "name": "旧页面"}, expected=403)
            response = self.client.post(f"/api/goods/import?warehouse_id={self.wh1}",
                                        files={"file": ("goods.xlsx", b"unused")},
                                        headers={"Authorization": "Bearer " + self.admin["access_token"]})
            self.assertEqual(response.status_code, 403)
            self.assertEqual(self._call("GET", f"/goods/?warehouse_id={self.wh1}")[0]["name"], "工程耗材")
            self.assertEqual(self._call("GET", f"/goods/?warehouse_id={self.wh2}")[0]["name"], "行政用品")
            self.assertEqual(len(self._call("GET", "/goods/")), 1)
            # A request for another granted warehouse can still search its own catalog.
            self._call("GET", f"/goods/?warehouse_id={self.wh2}", auth=self.supervisor_auth, expected=403)
        finally:
            self._call("POST", f"/users/{self.admin_id}/switch-warehouse?warehouse_id={self.wh1}")

    def test_public_application_uses_selected_warehouse(self):
        headers = {"X-Real-IP": "198.51.100.22"}
        for wid, expected in [(self.wh1, "工程耗材"), (self.wh2, "行政用品")]:
            resp = self.client.get("/api/public/goods-search", params={"q": "SAME", "warehouse_id": wid}, headers=headers)
            self.assertEqual(resp.status_code, 200, resp.text)
            self.assertEqual([g["name"] for g in resp.json()], [expected])
        resp = self.client.get("/api/public/goods-search", params={"q": "SAME"}, headers=headers)
        self.assertEqual(resp.status_code, 400)
        resp = self.client.post("/api/requests/", json={"applicant_name": "员工", "contact": "", "description": "测试", "warehouse_id": self.wh1,
                                                         "items": [{"barcode": "SAME", "quantity": 1}]}, headers={"X-Real-IP": "198.51.100.23"})
        self.assertEqual(resp.status_code, 201, resp.text)
        rows = self._call("GET", "/requests/", auth=self.supervisor_auth)
        self.assertTrue(any(r["id"] == resp.json()["id"] for r in rows))
        self.__class__.request_id = resp.json()["id"]

    def test_scan_and_role_boundary(self):
        self._call("POST", "/inventory/scan", {"goods_barcode": "SAME", "location_code": "S-L1", "type": "入库", "quantity": 2}, auth=self.supervisor_auth)
        self._call("POST", "/inventory/scan", {"goods_barcode": "SAME", "location_code": "S-L1", "type": "入库", "quantity": 1}, auth=self.viewer_auth, expected=403)
        self._call("POST", "/inventory/scan", {"goods_barcode": "SAME", "location_code": "S-L2", "type": "入库", "quantity": 1}, auth=self.supervisor_auth, expected=403)
        self._call("POST", "/goods/", {"barcode": "VIEW", "name": "越权"}, auth=self.viewer_auth, expected=403)
        result = self._call("POST", f"/requests/{self.request_id}/status", {"status": "approved"}, auth=self.supervisor_auth)
        self.assertEqual(result["status"], "approved")
        stock = self._call("GET", "/stock/", auth=self.viewer_auth)
        self.assertEqual({r["warehouse_id"] for r in stock}, {self.wh1})
        self.assertTrue(all(r["goods_price"] is None for r in stock))
        self._call("GET", "/outbound-orders/", auth=self.viewer_auth, expected=403)

    def test_supervisor_cannot_handle_other_warehouse_request(self):
        resp = self.client.post("/api/requests/", json={
            "applicant_name": "跨仓申请", "description": "行政仓申请", "warehouse_id": self.wh2,
            "items": [{"barcode": "SAME", "quantity": 1}],
        }, headers={"X-Real-IP": "198.51.100.24"})
        self.assertEqual(resp.status_code, 201, resp.text)
        request_id = resp.json()["id"]
        rows = self._call("GET", "/requests/", auth=self.supervisor_auth)
        self.assertNotIn(request_id, [row["id"] for row in rows])
        self._call("POST", f"/requests/{request_id}/status", {"status": "approved"},
                   auth=self.supervisor_auth, expected=403)


if __name__ == "__main__":
    unittest.main()
