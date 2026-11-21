# 鲜达冷链 · 温控与损耗关联簿

给鲜达冷链 12 条线路的运营分析组，用运输背景解释温控与货损之间的关联。上游班次和温控记录进入工作簿，分析员检查缺失、执行真实统计，负责人把改善建议签核给线路主管。

原流程是调度员整理工作簿，分析员复制公式，主管在聊天记录里拍板。三个痛点是缺失样本被静默删除、统计口径容易改错、建议与研究证据分离。此项目提供班次清单、样本质检、关联分析、改善建议、匿名发布五个业务页面，贯穿四类实体；并含登录、审计、CSV 之外的 JSON 研究导出。

## 对外匿名发布（差分隐私）

第五个页面“匿名发布”面向外部协作：内部原研究仍保留班次、样本等明细且可读，但对外**只发布温控暴露分布与货损分布两个匿名频数直方图**，文件与接口绝不包含班次号、样本号、线路、驾驶员或任何精确原始计数。

- **冻结口径**：分箱边界与裁剪范围在 `constants/enums.py::FROZEN_BINS` 全局固定（版本 `frozen-v1`），所有发布共用，攻击者无法借改变分箱套取额外信息；暴露裁剪到 [0,1440] 分钟、货损裁剪到 [0,100]%。
- **按班次限贡献**：隐私单元是班次，同一班次即使有多条样本，对每个直方图至多贡献 1（取首个非空值）。增删一个班次，单箱频数至多变化 1，L1 敏感度严格为 1。
- **拉普拉斯机制**：每箱叠加尺度 `1/ε` 的拉普拉斯噪声并取非负整数；一次发布的两个分布平分本次 ε（顺序组合）。对外只给加噪量及其后处理（如加噪总和），精确贡献班次数不免费外泄。
- **按温区共用累计预算**：每个温区一条 `PrivacyBudget` 额度，同温区的重叠研究/多次发布都从同一额度逐次扣减；余额不足返回 402 拒绝且不产生任何扣费。
- **并发不超支**：温区进程内互斥锁 + `SELECT … FOR UPDATE` 行锁双重串行化（多实例由行锁兜底）。
- **幂等重试**：请求带 `idempotency_key`；同键重试返回原发布单与原噪声，不重复扣费、不重新加噪，并能自愈上一次中断的草稿。
- **扣费与可见性一致、可恢复**：两阶段提交——先持久化 draft 与扣费，再原子（临时文件 + `os.replace`）写对外文件并置 `published`。仅 `published` 可下载。启动时 `recover_pending` 处理残留 draft：文件完整则补登发布，可重建则重写，仅在确实无法对外可见（I/O 失败）时退还额度并置 `failed`，绝不为不可见结果收费；逐笔 charge/refund 记入 `PrivacyLedger` 供查账。
- **权限**：analyst/admin 可申请发布，仅 admin 能设置温区总预算（不得低于已消耗）与查看完整流水；viewer 全站只读。接口：`GET/PUT /privacy/budgets`、`GET/POST /privacy/releases`、`GET /privacy/releases/{id}/download`、`GET /privacy/ledger`。

## 启动

```sh
cp .env.example .env
docker compose up -d --build
docker compose exec -T backend python -m scripts.seed_demo
```

访问 http://localhost:18201 ，后端 http://localhost:19201/docs ，MySQL 映射 17701。恰好五个健康检查服务 frontend/backend/worker/db/redis。backend 与 worker 共享 `/artifacts`；Redis appendonly；数据库命名卷。停止与清空演示数据：`docker compose down -v --remove-orphans`。

## 演示账号和权限

| 邮箱 | 密码 | 权限 |
|---|---|---|
| admin@demo.local | Demo123! | 全部读写、负责人签核与审计 |
| analyst@demo.local | Demo123! | 创建和修改班次/样本、提交研究、起草建议；不能签核 |
| viewer@demo.local | Demo123! | 所有业务数据只读与下载；禁止任何写入和计算任务提交 |

JWT 有效期 8 小时，后端查数据库角色，不信任前端传入角色。密码 PBKDF2；演示使用固定盐和可公开的 JWT key，部署时通过环境变量替换 key，并为正式账号改成每用户随机盐。首次启动 SEED_DEMO=true 自动导入；失败记日志。自然键唯一约束、只插入缺失数据，重复执行不覆盖用户修改。

## 一天的业务剧本

1. 08:50 viewer 核对昨日 XD-1115-001 班次。
2. 09:00 analyst 查看 60 班次、60 条样本和缺失数据。
3. 09:10 创建 XD-1115-061，鲜达线路 01 / 冷藏。
4. 09:20 采集暴露 32 分钟、超温 3 次、货损 4.2% 的样本。
5. 09:30 创建冷藏温区暴露时长研究。
6. 09:40 Redis 队列交给 worker 计算；刷新查看真实结果。
7. 10:00 阅读散点、p 值、配对数及混杂因素警示。
8. 10:10 起草“复核线路 01 装卸等待时长”等建议，admin 签核。
9. 10:20 viewer 下载 coldchain-link-<研究id>.json 供线路主管复核。

## 算法与边界

Python SciPy 计算 Pearson r = sum((x-xmean)(y-ymean))/sqrt(sum((x-xmean)^2)sum((y-ymean)^2))；Spearman 使用平均秩处理并列值，再对秩求 Pearson。返回双侧 p 值、有效配对数、排除数、散点和原始快照。输入为同温区样本，x 为暴露分钟或超温次数，y 固定为货损率%。只有 x/y 均非空的配对进入计算；不做插补。少于3对、任一变量为常量，任务明确 failed。相关性不等于因果，不自动决定线路处罚。60条 seed 使用 Random(3102)；冷藏和冷冻各有一条缺失暴露，10条常温对照的暴露均20分钟，用于失败诊断。研究创建时冻结输入，后续修改样本不改变已有结果。任务失败可修复原始记录再新建研究。worker 启动恢复 queued/running 任务；单 worker 模式，不承诺多副本 exactly-once。

## 技术和目录

Python3.11 / FastAPI / SQLAlchemy / SciPy / PyMySQL / PyJWT；MySQL8.0；Redis7；Nginx1.27；原生 HTML、CSS、JS ES modules。前端无构建步骤。

- backend/src/models、schemas、services、controllers、routes 分层承载实体与业务。
- backend/src/services/privacy.py 为冻结分箱/裁剪与拉普拉斯机制（纯函数，可种子复现）；ledger.py 为按温区累计预算、行锁并发控制、两阶段发布与崩溃恢复、查账；routes/privacy.py 承载匿名发布接口。新增表 privacy_budgets / privacy_releases / privacy_ledger。
- backend/src/middlewares 为 JWT 角色鉴权、请求 ID、安全响应头；异常返回可恢复错误。
- backend/scripts/seed_demo.py 导入数据；migrations/001_initial.sql 说明建表策略，当前由 SQLAlchemy create_all 建表，尚未采用版本迁移工具。
- frontend/src/api、stores、types、components/common、hooks、pages、router、utils、constants 为请求、状态、契约、共享表格/标签/字段、表单、四页面与路由。
- 共享枚举 ROLES、STATES、ZONES：backend/src/constants/enums.py 与 frontend/src/constants/enums.js；校验在 backend/src/schemas/business.py，任务状态在 worker.py，UI 标签在 components/common/status.js。

本地开发：安装 backend/requirements.lock（真实运行环境完整依赖锁定；requirements.txt 为直接依赖）；启动本地 MySQL/Redis，设置 DATABASE_URL/REDIS_URL/JWT_SECRET 后 `uvicorn src.main:app --reload`。前端可由 Nginx 提供，代理 `/api/` 去除前缀转发给后端。

环境变量：COMPOSE_PROJECT_NAME（容器前缀）、FRONTEND_PORT/BACKEND_PORT/DB_PORT（映射）、DB_PASSWORD（演示数据库密码）、JWT_SECRET、SEED_DEMO。仅内部服务接触 Redis，不映射宿主端口。编辑班次与样本 API 支持 PUT；UI 当前以新增和浏览为主，无批量 CSV 导入、删除、分页或邮件通知。Google Fonts 需联网，无法联网则浏览器回退 serif/sans-serif。证据和测试在批次根 test-artifacts/gb-3102，不写入源码。匿名发布的单元与接口测试位于 test-artifacts/gb-3102/privacy（28 项，SQLite + TestClient，无需 MySQL/Redis），覆盖冻结分箱、按班次限贡献、拉普拉斯、对外无明细、预算不足拒绝、并发不超支、幂等不重复扣费/加噪、两阶段崩溃恢复与退款、viewer 只读、内部原研究仍可读。本地运行：`PYTHONPATH=backend:test-artifacts/gb-3102/privacy python -m pytest test-artifacts/gb-3102/privacy -q`（需设置 JWT_SECRET/DATABASE_URL/REDIS_URL，测试内默认使用 sqlite 内存库与临时对外目录）。

License: MIT。

NETWORK_SUBNET 默认 10.253.102.0/24，适配本机默认地址池耗尽的情况；迁移到其他环境可修改为空闲子网。
