---
mode: plan
cwd: C:\Users\zhongruan\Desktop\GitHub\AntiHub-ALL
task: 合并 AntiHub-plugin 到 AntiHub-Backend（下线 plugin 运行时依赖）
complexity: complex
planning_method: builtin
created_at: 2026-02-14T12:17:28.3123519+08:00
---

# Plan: 合并 AntiHub-plugin 到 AntiHub-Backend（下线 plugin）

🎯 任务概述

本计划根据 `Report.md` 的决策与边界，制定一个“可落地、可追踪”的执行路线：最终 **不再部署/依赖 `AntiHub-plugin`**，所有账号管理、配额、上游调用统一落到 `AntiHub-Backend`，并将数据库收敛为 **Backend 单库**（Compose 默认 `POSTGRES_DB=antihub`）。

兼容性目标：**现有前端 + 现有 Backend 对外 API** 保持可用；不再承诺任何“直连 `AntiHub-plugin:8045`”的第三方客户端兼容性。迁移范围聚焦账号/配置/配额与订阅白名单；不迁移 plugin 历史消费日志；移除“共享/共享池”概念与相关字段语义。

📋 执行计划

1. **对齐边界与交付物（冻结范围）**
   - 产出：一页“范围/非目标/兼容性”确认（直接引用 `Report.md` 的 0.x 章节要点）。
   - 明确：哪些接口必须保留（来自 `4-docs/BACKEND_PUBLIC_ROUTES.csv`），哪些将返回 410（来自 `Report.md` 11.2.3）。
   - 验证点：产出可执行验收清单草稿（对应 `Report.md` 第 10 章）。

2. **触点盘点：定位所有“proxy plugin”调用链**
   - 搜索并列清单：Backend 中所有指向 `AntiHub-plugin` 的 HTTP 调用、配置项、环境变量引用、Docker compose 依赖。
   - 重点文件：`AntiHub-Backend/app/services/plugin_api_service.py`、`AntiHub-Backend/app/api/routes/plugin_api.py`、`docker-compose*.yml`。
   - 产出：一份“触点表”（文件/符号/用途/替换策略/是否可删）。

3. **DB Schema 落地：按最终表结构写 Alembic**
   - 在 `AntiHub-Backend` 中创建 `Report.md` 3.1 定义的新增表（如 `antigravity_accounts`、`antigravity_model_quotas` 等），并落实索引/唯一约束。
   - 明确删除/不再使用：共享池、共享优先级、`is_shared` / `prefer_shared` 等历史语义（按 `Report.md` 3.2/0.5）。
   - 验证点：本地跑 Alembic 升级/降级（至少升级到 head），并确认表结构与约束可满足幂等迁移。

4. **Backend 业务替换：/api/plugin-api 保持路由，替换为本地实现**
   - 目标：不改前端调用路径，改后端内部实现（符合 `Report.md` 5.3）。
   - 改造：
     - `/api/plugin-api/accounts/*`：账号 CRUD、刷新、项目、凭证读取等，统一落库到 Backend 新表。
     - `/api/plugin-api/quotas/user`：语义调整为从 `antigravity_model_quotas` 聚合（按 `Report.md` 11.2.3）。
     - `/api/plugin-api/quotas/shared-pool`、`/api/plugin-api/quotas/consumption`：按报告要求返回 410，并在前端/文档说明替代路径。
   - 验证点：对照 `4-docs/BACKEND_PUBLIC_ROUTES.csv` 跑一轮接口可达性与返回结构抽样比对（至少 cover 账号列表/详情/配额）。

5. **上游调用与流式能力对齐（避免行为漂移）**
   - SSE/流式：确保 `/v1/chat/completions` 等流式转发在 Backend 直接对上游工作（按 `Report.md` 6.1）。
   - 渠道：按 `Report.md` 2.4 通过 API Key/标头选择渠道（Antigravity/Kiro/Qwen），并明确“个人部署核心”逻辑。
   - Redis：所有 device flow 轮询、usage limits 背景同步等状态放 Redis/DB，避免进程内状态（按 `Report.md` 0.6/2.3）。
   - 验证点：用 2 组不同渠道账号做一次流式对话 + 非流式对话回归；多实例启动后状态一致。

6. **一次性迁移：plugin DB → Backend DB（可控开关 + 多实例安全）**
   - 设计迁移开关与执行时机：推荐在 Backend lifespan 中执行（按 `Report.md` 4.1/4.2）。
   - 分布式锁：实现迁移锁（如 PG advisory lock 或迁移表锁），保证多实例只跑一次（按 `Report.md` 4.3）。
   - 映射规则：plugin UUID → backend `users.id`（按 `Report.md` 4.4），并记录映射以便追溯（迁移期可临时使用 `plugin_api_keys` 映射表，但最终不参与请求链路）。
   - 幂等/失败策略：必须可重复运行；失败应阻止启动（在开关开启时）（按 `Report.md` 4.6）。
   - 验证点：准备一份包含 plugin DB 的本地环境（只用于迁移），跑迁移两次确认幂等，并抽样核对迁移后的账号/配额数据正确。

7. **配置与部署收敛：Compose 删除 plugin 服务与 plugin DB 初始化**
   - 更新：`docker-compose.yml` / `docker-compose.core.yml` / `docker-compose.local.yml`（如存在）移除 `plugin` 服务依赖。
   - 删除/弃用：`docker/postgres/init/01-init-plugin-db.sh`（或改为仅迁移期使用的脚本，不再默认执行）。
   - 更新：`.env.example` 增加/调整迁移开关与（仅迁移期）plugin DB 连接配置；删掉运行时 plugin 相关变量（按 `Report.md` 7.x）。
   - 验证点：`docker compose up -d` 在没有 plugin 的情况下可启动，且核心接口可用。

8. **前端适配与文档同步（重点：Analytics）**
   - 前端：将配额/趋势/明细改为来自 Backend 本地表与 `/api/usage/requests/*`，不再依赖 `/api/plugin-api/quotas/consumption` 等历史接口（按 `Report.md` TL;DR）。
   - 处理 410：前端对已废弃接口的调用必须移除或做兼容提示，避免页面硬崩。
   - 文档：同步更新 `README.md`、`4-docs/*`（尤其是接口与部署说明），确保“最终不部署 plugin”清晰可见。
   - 验证点：手工走一遍前端关键页面（账号管理、配额/Analytics、调用测试页如有）。

9. **验收与回归（按清单执行，可复制复用）**
   - 启动验收：不含 plugin 的 compose 一键启动；Backend 日志中无 “proxy plugin” 调用；接口可用（`Report.md` 10.1）。
   - 迁移验收：在 Phase 2 环境跑迁移；对照迁移范围做抽样核对（`Report.md` 10.2）。
   - 合同验收：对照 `4-docs/BACKEND_PUBLIC_ROUTES.csv` 做路由可用性扫描（可以脚本化）。
   - 回滚预案：保留一个“临时恢复 plugin 的 compose/开关”路径，以便在生产/演示环境快速止损（只作为应急，不作为长期形态）。

⚠️ 风险与注意事项

- **行为漂移风险**：SSE/流式、上游错误码、超时与重试策略如果与 plugin 不一致，可能导致前端/调用方体验变化，需要专项回归。
- **迁移阻塞风险**：报告要求“开关开启时迁移失败即启动失败”，务必通过“迁移期开关”与“分布式锁/幂等”降低不可用窗口。
- **多实例一致性风险**：device flow / usage limits 等状态必须落 Redis/DB，避免单实例开发时看不出问题。
- **兼容性风险**：返回 410 的接口如果前端仍在调用，会直接破坏页面；需要先跑调用链排查，再做前端适配。

📎 参考

- `Report.md:1`
- `docker-compose.yml:1`
- `docker-compose.core.yml:1`
- `.env.example:1`
- `docker/postgres/init/01-init-plugin-db.sh:1`
- `4-docs/BACKEND_PUBLIC_ROUTES.csv:1`
- `4-docs/plugin_public_routes.csv:1`
- `AntiHub-Backend/app/api/routes/plugin_api.py:1`
- `AntiHub-Backend/app/services/plugin_api_service.py:1`
