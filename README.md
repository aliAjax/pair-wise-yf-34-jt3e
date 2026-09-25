# 无人机飞行计划审批与空域协调系统

标准库独立项目。系统记录运营方计划、航线、载荷、高度、人口风险和应急方案，检查临时禁飞区、高度范围、人口风险、区域时段容量以及相邻有效计划冲突。审核结果支持离线编号幂等回传，计划变更会使原批准失效并生成通知。

## 运行

```bash
python3 app.py --db drone_airspace.db
```

默认监听 `127.0.0.1:8205`，首页 `/`，健康检查 `/health`。

身份头为 `X-User-Id`、`X-Role`；运营方还需 `X-Operator`。角色：`viewer`、`operator`、`airspace_reviewer`、`commander`、`auditor`。

## 主要接口

- `POST /api/restrictions`：新增临时限制或禁飞区。
- `POST /api/capacities`：空域审核员（或指挥官）为某区域、某时段设置最多放行架数；同一区域重叠时段拒绝重复设置（`capacity_window_overlap`）。
- `GET /api/capacities?region=`：查询容量。运营方/公众只看到容量与剩余数；审核员、指挥官、审计员还能看到占用名额的已批准计划明细。
- `POST /api/plans`：创建飞行计划。
- `GET /api/plans/{id}/check`：检查硬约束、容量和相邻交通冲突，返回命中的容量窗口（`max_slots/used_slots/remaining_slots`）。
- `POST /api/plans/{id}/submit`、`approve`、`reject`：提交和审核；审核使用 `offline_id` 保证断网重连幂等。批准成功才占用名额，容量满时返回 `capacity_full` 无法批准（指挥官可紧急授权超额，沿用 `override_reason` 留痕）。
- `POST /api/plans/{id}/change`、`cancel`：版本化变更与取消，并生成通知。已批准计划改航线或时间时先收回旧名额（状态回退为草稿），再按新计划核对容量，响应中给出新窗口的剩余名额。
- `GET /api/notifications`、`POST /api/expire`：通知与到期处理。拒绝、取消、到期后计划不再处于 `approved`，名额自动释放。
- `GET /api/state`：按角色返回计划、限制、容量（含占用情况）和公开信息。

### 区域时段容量语义

- 容量按 `region + 时间区间重叠` 匹配计划；未设置容量的区域时段不做名额限制。
- 占用计数只包含 `approved` 计划，且在事务（`BEGIN IMMEDIATE`）内完成，保证并发批准不会超额。
- 容量是对相邻航路两两比对之外的**总量**控制：多运营方同时报同一区域时，剩余名额统一可见。
- 首页 `/` 按角色展示容量表格（容量满红色、接近满黄色），审核员可直接新增容量。

## 测试

```bash
python3 -m unittest discover -s tests -v
```

## 主要局限

空域几何使用经纬度矩形和航线包围盒近似，不包含多边形、椭球距离、地形、实时遥测和完整间隔标准。紧急授权只能覆盖空域及交通冲突，不能绕过载荷与高度硬限制。身份头、无签名离线审核以及单机 SQLite 适合原型，生产环境需要 PKI、真实 GIS 引擎和跨机构事件总线。
