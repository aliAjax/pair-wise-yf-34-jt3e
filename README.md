# 无人机飞行计划审批与空域协调系统

标准库独立项目。系统记录运营方计划、航线、载荷、高度、人口风险和应急方案，检查临时禁飞区、高度范围、人口风险以及相邻有效计划冲突。审核结果支持离线编号幂等回传，计划变更会使原批准失效并生成通知。支持区域时段容量：空域审核员可设置某区域在指定时段最多放行几架，批准计划占用名额，取消、拒绝、到期或变更后自动释放。

## 运行

```bash
python3 app.py --db drone_airspace.db
```

默认监听 `127.0.0.1:8205`，首页 `/`，健康检查 `/health`。

身份头为 `X-User-Id`、`X-Role`；运营方还需 `X-Operator`。角色：`viewer`、`operator`、`airspace_reviewer`、`commander`、`auditor`。

## 主要接口

- `POST /api/restrictions`：新增临时限制或禁飞区。
- `POST /api/capacities`：空域审核员设置区域时段容量（`region`、`starts_at`、`ends_at`、`max_allowed`）。
- `GET /api/capacities`：查询各容量窗口的占用与剩余名额，支持 `region`、`starts_at`、`ends_at` 过滤；审核员角色可见占用计划明细。
- `POST /api/plans`：创建飞行计划。
- `GET /api/plans/{id}/check`：检查硬约束、相邻交通冲突和区域容量余量。
- `POST /api/plans/{id}/submit`、`approve`、`reject`：提交和审核；审核使用 `offline_id` 保证断网重连幂等。批准时占用区域容量名额，名额不足返回 `capacity_full`（紧急授权不能绕过容量限制）。
- `POST /api/plans/{id}/change`、`cancel`：版本化变更与取消，并生成通知。已批准计划变更时先收回旧名额，重新批准时再按新计划核对；取消、拒绝、到期同样释放名额。
- `GET /api/notifications`、`POST /api/expire`：通知与到期处理。
- `GET /api/state`：按角色返回计划、限制、容量占用和公开信息。

## 测试

```bash
python3 -m unittest discover -s tests -v
```

## 主要局限

空域几何使用经纬度矩形和航线包围盒近似，不包含多边形、椭球距离、地形、实时遥测和完整间隔标准。区域容量按区域名精确匹配、按时段重叠计数，不区分高度层，容量规则创建后不可修改。紧急授权只能覆盖空域及交通冲突，不能绕过载荷、高度硬限制和区域容量上限。身份头、无签名离线审核以及单机 SQLite 适合原型，生产环境需要 PKI、真实 GIS 引擎和跨机构事件总线。
