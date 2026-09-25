import sys, tempfile, unittest
from datetime import timedelta
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import ApiError, DroneAirspaceService, iso, utcnow


class CapacityFlowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.svc = DroneAirspaceService(Path(self.tmp.name) / "test.db")
        self.start = utcnow() + timedelta(hours=2)

    def tearDown(self): self.tmp.cleanup()

    def capacity(self, region="BJ", slots=1, start=None, end=None):
        return self.svc.create_capacity("reviewer", "airspace_reviewer", {
            "region": region, "max_slots": slots,
            "starts_at": iso(start or (self.start - timedelta(minutes=30))),
            "ends_at": iso(end or (self.start + timedelta(hours=2))), "note": "演练"})

    def plan(self, callsign="D100", operator="OP1", region="BJ", start=None, end=None, route=None):
        st = start or self.start
        return self.svc.create_plan(f"{operator}-user", "operator", operator, {
            "callsign": callsign, "drone_model": "M400", "payload_kg": 5,
            "route": route or [[116.1, 39.8], [116.3, 39.9]],
            "starts_at": iso(st), "ends_at": iso(end or st + timedelta(hours=1)),
            "max_altitude": 100, "population_risk": 1, "emergency_plan": "返回起降点", "region": region})

    # 同区域、同时段但地理上分开的另一条航路，避免触发相邻航路冲突，便于单独验证容量
    FAR_ROUTE = [[117.1, 38.8], [117.3, 38.9]]

    def submit_and_approve(self, plan, offline, role="airspace_reviewer", actor="reviewer", **extra):
        self.svc.submit(plan["id"], f"{plan['operator_id']}-user", "operator", plan["operator_id"], {})
        body = {"expected_revision": 1, "offline_id": offline, "reason": "核对容量"}; body.update(extra)
        return self.svc.approve(plan["id"], actor, role, body)

    def test_operator_sees_remaining_and_approval_consumes_slot(self):
        self.capacity(slots=2)
        plan = self.plan("D100")
        self.assertEqual(self.svc.get_plan(plan["id"], "operator", "OP1")["capacity"]["remaining_slots"], 2)
        self.submit_and_approve(plan, "cap-1")
        caps = self.svc.list_capacities(self.svc.repo.conn, "operator", "BJ")
        self.assertEqual(caps[0]["max_slots"], 2)
        self.assertEqual(caps[0]["used_slots"], 1)
        self.assertEqual(caps[0]["remaining_slots"], 1)
        self.assertNotIn("occupying_plans", caps[0])  # 运营方看不到是谁占用的

    def test_reviewer_sees_occupying_plans(self):
        self.capacity(slots=2)
        plan = self.plan("D100"); self.submit_and_approve(plan, "cap-1")
        caps = self.svc.list_capacities(self.svc.repo.conn, "airspace_reviewer")
        view = caps[0]
        self.assertEqual([p["plan_id"] for p in view["occupying_plans"]], [plan["id"]])
        state = self.svc.state("airspace_reviewer", "")
        self.assertEqual(state["capacities"][0]["used_slots"], 1)

    def test_full_capacity_blocks_approval(self):
        self.capacity(slots=1)
        first = self.plan("D100"); self.submit_and_approve(first, "cap-1")
        second = self.plan("D101", operator="OP2", route=self.FAR_ROUTE)
        self.svc.submit(second["id"], "OP2-user", "operator", "OP2", {})
        check = self.svc.check_conflicts(second["id"], "airspace_reviewer", "")
        self.assertFalse(check["approvable"])
        self.assertEqual(check["blocking_conflicts"][0]["code"], "capacity_full")
        self.assertEqual(check["capacity"]["remaining_slots"], 0)
        with self.assertRaises(ApiError) as ctx:
            self.svc.approve(second["id"], "reviewer", "airspace_reviewer", {"expected_revision": 1, "offline_id": "cap-2", "reason": "常规审核"})
        self.assertEqual(ctx.exception.code, "capacity_full")
        self.assertEqual(second["id"], self.svc.get_plan(second["id"], "airspace_reviewer")["id"])
        self.assertEqual(self.svc.get_plan(second["id"], "airspace_reviewer")["status"], "submitted")

    def test_commander_can_override_full_capacity(self):
        self.capacity(slots=1)
        first = self.plan("D100"); self.submit_and_approve(first, "cap-1")
        second = self.plan("D101", operator="OP2", route=self.FAR_ROUTE)
        out = self.submit_and_approve(second, "cap-2", role="commander", actor="boss",
                                      reason="应急", override_reason="救援紧急授权")
        self.assertEqual(out["plan"]["status"], "approved")
        self.assertEqual(out["override_kind"], "emergency_authority")

    def test_cancel_releases_slot(self):
        self.capacity(slots=1)
        first = self.plan("D100"); self.submit_and_approve(first, "cap-1")
        second = self.plan("D101", operator="OP2", route=self.FAR_ROUTE)
        self.svc.submit(second["id"], "OP2-user", "operator", "OP2", {})
        with self.assertRaises(ApiError) as ctx:
            self.svc.approve(second["id"], "reviewer", "airspace_reviewer", {"expected_revision": 1, "offline_id": "cap-2", "reason": "审核"})
        self.assertEqual(ctx.exception.code, "capacity_full")
        self.svc.cancel(first["id"], "OP1-user", "operator", "OP1", {"reason": "任务取消"})
        approved = self.svc.approve(second["id"], "reviewer", "airspace_reviewer", {"expected_revision": 1, "offline_id": "cap-2b", "reason": "名额释放后审核"})
        self.assertEqual(approved["plan"]["status"], "approved")

    def test_reject_and_expire_release_slot(self):
        self.capacity(slots=1)
        first = self.plan("D100"); self.svc.submit(first["id"], "OP1-user", "operator", "OP1", {})
        self.svc.reject(first["id"], "reviewer", "airspace_reviewer", {"expected_revision": 1, "offline_id": "rej-1", "reason": "材料不全"})
        second = self.plan("D101", operator="OP2")
        approved = self.submit_and_approve(second, "cap-1")
        self.assertEqual(approved["plan"]["status"], "approved")
        # 到期后释放名额
        self.svc.expire_plans("reviewer", "airspace_reviewer")  # 尚未到期
        self.assertEqual(self.svc.list_capacities(self.svc.repo.conn, "airspace_reviewer")[0]["used_slots"], 1)
        self.svc.repo.conn.execute("UPDATE flight_plans SET ends_at=? WHERE id=?", (iso(utcnow() - timedelta(minutes=1)), second["id"]))
        self.assertEqual(self.svc.expire_plans("reviewer", "airspace_reviewer")["expired"], 1)
        self.assertEqual(self.svc.list_capacities(self.svc.repo.conn, "airspace_reviewer")[0]["used_slots"], 0)

    def test_change_approved_plan_reclaims_old_slot_then_rechecks(self):
        self.capacity(slots=1, end=self.start + timedelta(hours=6))
        plan = self.plan("D100"); self.submit_and_approve(plan, "cap-1")
        other = self.plan("D101", operator="OP2",
                          start=self.start + timedelta(hours=3), end=self.start + timedelta(hours=4),
                          route=[[116.2, 39.85], [116.3, 39.9]])
        # 容量窗口同时覆盖原时段和新时段；旧计划改到新时段后，自己旧名额被收回，新时段空闲，仍剩 1
        changed = self.svc.change(plan["id"], "OP1-user", "operator", "OP1",
                                  {"expected_revision": 1,
                                   "starts_at": iso(self.start + timedelta(hours=3)),
                                   "ends_at": iso(self.start + timedelta(hours=4))})
        self.assertEqual(changed["status"], "draft")
        caps = self.svc.list_capacities(self.svc.repo.conn, "airspace_reviewer")[0]
        self.assertEqual(caps["used_slots"], 0)  # 旧名额已收回，新计划未批准
        self.assertEqual(changed["capacity"]["remaining_slots"], 1)
        self.assertEqual(changed["revision"], 2)

    def test_change_into_full_window_is_reported(self):
        self.capacity(slots=1)
        # 给第二个时段也设满容量窗口
        self.capacity(slots=1, start=self.start + timedelta(hours=2, minutes=30),
                      end=self.start + timedelta(hours=5))
        moving = self.plan("D100")
        holder = self.plan("D101", operator="OP2", start=self.start + timedelta(hours=3),
                           end=self.start + timedelta(hours=4), route=self.FAR_ROUTE)
        self.submit_and_approve(moving, "cap-1")
        self.submit_and_approve(holder, "cap-2")
        changed = self.svc.change(moving["id"], "OP1-user", "operator", "OP1",
                                  {"expected_revision": 1,
                                   "starts_at": iso(self.start + timedelta(hours=3)),
                                   "ends_at": iso(self.start + timedelta(hours=4))})
        self.assertEqual(changed["capacity"]["remaining_slots"], 0)
        self.svc.submit(moving["id"], "OP1-user", "operator", "OP1", {})
        with self.assertRaises(ApiError) as ctx:
            self.svc.approve(moving["id"], "reviewer", "airspace_reviewer", {"expected_revision": 2, "offline_id": "cap-3", "reason": "迁移审核"})
        self.assertEqual(ctx.exception.code, "capacity_full")

    def test_overlapping_capacity_window_rejected_and_other_region_independent(self):
        self.capacity(region="BJ", slots=1)
        with self.assertRaises(ApiError) as ctx:
            self.capacity(region="BJ", slots=2)
        self.assertEqual(ctx.exception.code, "capacity_window_overlap")
        self.capacity(region="SH", slots=1)
        bj = self.plan("D100", region="BJ"); self.submit_and_approve(bj, "cap-1")
        sh = self.plan("D200", region="SH", route=self.FAR_ROUTE)
        self.assertTrue(self.svc.check_conflicts(sh["id"], "operator", "OP1")["approvable"])

    def test_no_capacity_means_no_limit_and_reviewer_only_creation(self):
        with self.assertRaises(ApiError) as ctx:
            self.svc.create_capacity("OP1-user", "operator", {"region": "BJ", "max_slots": 1,
                                                              "starts_at": iso(self.start), "ends_at": iso(self.start + timedelta(hours=1))})
        self.assertEqual(ctx.exception.code, "capacity_forbidden")
        plan = self.plan("D100")
        self.assertIsNone(self.svc.get_plan(plan["id"], "operator", "OP1")["capacity"])
        self.assertTrue(self.svc.check_conflicts(plan["id"], "airspace_reviewer", "")["approvable"])

    def test_state_includes_capacities_for_all_roles(self):
        self.capacity(slots=1)
        for role, kw in [("viewer", {}), ("operator", {}), ("airspace_reviewer", {}), ("commander", {}), ("auditor", {})]:
            state = self.svc.state(role, "OP1")
            self.assertIn("capacities", state)
            self.assertEqual(state["capacities"][0]["remaining_slots"], 1)


if __name__ == "__main__": unittest.main()
