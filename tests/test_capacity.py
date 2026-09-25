import sys, tempfile, unittest
from datetime import timedelta
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import ApiError, DroneAirspaceService, iso, utcnow

ROUTE_A = [[116.1, 39.8], [116.2, 39.85]]
ROUTE_B = [[116.5, 39.95], [116.6, 40.0]]


class CapacityTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.svc = DroneAirspaceService(Path(self.tmp.name) / "test.db"); self.start = utcnow() + timedelta(hours=2)

    def tearDown(self): self.tmp.cleanup()

    def capacity(self, max_allowed=1, region="BJ", start=None, end=None):
        return self.svc.create_capacity("reviewer", "airspace_reviewer", {"region": region, "starts_at": iso(start or self.start - timedelta(hours=1)), "ends_at": iso(end or self.start + timedelta(hours=3)), "max_allowed": max_allowed, "note": "早高峰"})

    def plan(self, callsign, operator="OP1", route=ROUTE_A, start=None):
        start = start or self.start
        return self.svc.create_plan("op-user", "operator", operator, {"callsign": callsign, "drone_model": "M400", "payload_kg": 5, "route": route, "starts_at": iso(start), "ends_at": iso(start + timedelta(hours=1)), "max_altitude": 100, "population_risk": 1, "emergency_plan": "返回起降点", "region": "BJ"})

    def submit(self, plan, operator="OP1"): return self.svc.submit(plan["id"], "op-user", "operator", operator, {})["plan"]

    def approve(self, plan, offline_id, revision=1, role="airspace_reviewer", extra=None):
        body = {"expected_revision": revision, "offline_id": offline_id, "reason": "容量充足"}
        if extra: body.update(extra)
        return self.svc.approve(plan["id"], "reviewer", role, body)

    def remaining(self, region="BJ"):
        return self.svc.list_capacities("airspace_reviewer", {"region": region})["capacities"][0]["remaining"]

    def test_capacity_rules_and_operator_query(self):
        with self.assertRaises(ApiError) as ctx:
            self.svc.create_capacity("op-user", "operator", {"region": "BJ", "starts_at": iso(self.start), "ends_at": iso(self.start + timedelta(hours=1)), "max_allowed": 1})
        self.assertEqual(ctx.exception.code, "capacity_forbidden")
        with self.assertRaises(ApiError) as ctx: self.capacity(max_allowed=0)
        self.assertEqual(ctx.exception.code, "invalid_capacity")
        cap = self.capacity(max_allowed=2)
        self.assertEqual(cap["remaining"], 2)
        result = self.svc.list_capacities("operator", {"region": "BJ", "starts_at": iso(self.start), "ends_at": iso(self.start + timedelta(hours=1))})
        self.assertEqual(len(result["capacities"]), 1)
        view = result["capacities"][0]
        self.assertEqual(view["remaining"], 2); self.assertNotIn("occupants", view)
        reviewer_view = self.svc.list_capacities("airspace_reviewer", {})["capacities"][0]
        self.assertIn("occupants", reviewer_view)
        with self.assertRaises(ApiError) as ctx: self.svc.list_capacities("operator", {"starts_at": iso(self.start)})
        self.assertEqual(ctx.exception.code, "invalid_time_filter")

    def test_approval_occupies_slot_and_full_blocks(self):
        self.capacity(max_allowed=1)
        a, b = self.plan("A100", "OP1", ROUTE_A), self.plan("B100", "OP2", ROUTE_B)
        self.submit(a, "OP1"); self.submit(b, "OP2")
        self.approve(a, "off-a")
        self.assertEqual(self.remaining(), 0)
        check = self.svc.check_conflicts(b["id"], "airspace_reviewer", "")
        self.assertFalse(check["capacity_ok"]); self.assertFalse(check["approvable"])
        self.assertEqual(check["capacity"][0]["remaining"], 0); self.assertEqual(check["capacity"][0]["used"], 1)
        with self.assertRaises(ApiError) as ctx: self.approve(b, "off-b")
        self.assertEqual(ctx.exception.code, "capacity_full")
        with self.assertRaises(ApiError) as ctx:
            self.approve(b, "off-b2", role="commander", extra={"override_reason": "紧急任务"})
        self.assertEqual(ctx.exception.code, "capacity_full")

    def test_cancel_releases_slot(self):
        self.capacity(max_allowed=1)
        a, b = self.plan("A110", "OP1", ROUTE_A), self.plan("B110", "OP2", ROUTE_B)
        self.submit(a, "OP1"); self.submit(b, "OP2"); self.approve(a, "off-a")
        with self.assertRaises(ApiError): self.approve(b, "off-b")
        self.svc.cancel(a["id"], "op-user", "operator", "OP1", {"reason": "任务取消"})
        self.assertEqual(self.remaining(), 1)
        self.approve(b, "off-b")
        self.assertEqual(self.remaining(), 0)

    def test_reject_keeps_slot_free(self):
        self.capacity(max_allowed=1)
        a, b = self.plan("A120", "OP1", ROUTE_A), self.plan("B120", "OP2", ROUTE_B)
        self.submit(a, "OP1"); self.submit(b, "OP2")
        self.svc.reject(a["id"], "reviewer", "airspace_reviewer", {"expected_revision": 1, "offline_id": "off-rej", "reason": "资料不全"})
        self.assertEqual(self.remaining(), 1)
        self.approve(b, "off-b")
        self.assertEqual(self.remaining(), 0)

    def test_expire_releases_slot(self):
        self.capacity(max_allowed=1)
        a, b = self.plan("A130", "OP1", ROUTE_A), self.plan("B130", "OP2", ROUTE_B)
        self.submit(a, "OP1"); self.submit(b, "OP2"); self.approve(a, "off-a")
        self.assertEqual(self.remaining(), 0)
        self.svc.repo.conn.execute("UPDATE flight_plans SET ends_at=? WHERE id=?", (iso(utcnow() - timedelta(minutes=1)), a["id"]))
        self.assertEqual(self.svc.expire_plans("reviewer", "airspace_reviewer")["expired"], 1)
        self.assertEqual(self.remaining(), 1)
        self.approve(b, "off-b")

    def test_change_releases_old_slot_and_rechecks(self):
        self.capacity(max_allowed=1)
        a, b = self.plan("A140", "OP1", ROUTE_A), self.plan("B140", "OP2", ROUTE_B)
        self.submit(a, "OP1"); self.submit(b, "OP2"); self.approve(a, "off-a")
        with self.assertRaises(ApiError) as ctx: self.approve(b, "off-b")
        self.assertEqual(ctx.exception.code, "capacity_full")
        changed = self.svc.change(a["id"], "op-user", "operator", "OP1", {"expected_revision": 1, "route": [[116.15, 39.82], [116.25, 39.87]]})
        self.assertEqual(changed["status"], "draft")
        self.assertEqual(self.remaining(), 1)
        self.approve(b, "off-b")
        self.assertEqual(self.remaining(), 0)
        self.submit(a, "OP1")
        with self.assertRaises(ApiError) as ctx: self.approve(a, "off-a2", revision=2)
        self.assertEqual(ctx.exception.code, "capacity_full")
        moved = self.svc.change(a["id"], "op-user", "operator", "OP1", {"expected_revision": 2, "starts_at": iso(self.start + timedelta(hours=10)), "ends_at": iso(self.start + timedelta(hours=11))})
        self.assertEqual(moved["revision"], 3)
        self.submit(a, "OP1")
        self.approve(a, "off-a3", revision=3)
        self.assertEqual(self.remaining(), 0)

    def test_state_shows_capacity_per_role(self):
        self.capacity(max_allowed=2)
        a = self.plan("A150", "OP1", ROUTE_A)
        self.submit(a, "OP1"); self.approve(a, "off-a")
        operator_caps = self.svc.state("operator", "OP1")["capacities"]
        self.assertEqual(operator_caps[0]["remaining"], 1); self.assertNotIn("occupants", operator_caps[0])
        reviewer_caps = self.svc.state("airspace_reviewer", "")["capacities"]
        self.assertEqual(reviewer_caps[0]["used"], 1)
        self.assertEqual(reviewer_caps[0]["occupants"][0]["callsign"], "A150")
        viewer_caps = self.svc.state("viewer", "")["capacities"]
        self.assertEqual(viewer_caps[0]["remaining"], 1)


if __name__ == "__main__": unittest.main()
