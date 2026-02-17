# Report — 移除 Plugin 依赖全面调查（Kiro / Antigravity / Qwen）

> 生成日期：2026-02-17  
> 目标：确认 Kiro / Antigravity / Qwen 是否仍依赖 **AntiHub-plugin（历史 Node 插件服务）** 或其 DB/API，并给出“彻底移除 plugin”的改造清单。

---

## 0) TL;DR（结论先行）

- **运行时依赖层面**：根目录 `docker-compose.yml` / `docker-compose.core.yml` 已不再包含 `AntiHub-plugin` 服务；Kiro/Qwen/Antigravity 的核心链路均在 `AntiHub-Backend` 内部实现并直连上游，**不需要** `AntiHub-plugin:8045`。
- **已完成的关键改造（本次更新）**：
  - 后端彻底移除 outbound plugin HTTP 能力：删除 `plugin_api_base_url/plugin_api_admin_key` 配置，并移除 `create_plugin_user`、`proxy_request` 等会访问外部 plugin 的代码路径。
  - 文档/示例清理：`AntiHub-Backend/.env.example`、`AntiHub-Backend/docker-compose.yml`、`AntiHub-Backend/README.md` 以及根目录 `.env.example` 不再提示配置 `PLUGIN_API_BASE_URL/PLUGIN_API_ADMIN_KEY`。
- **仍然存在的 “plugin”** 主要是命名/兼容与迁移期遗留（不构成运行时依赖）：
  - `/api/plugin-api/*` 路径与 `PluginAPIService` 命名（兼容历史调用方），但已不再代理到外部 plugin。
  - “旧 plugin DB → Backend DB” 迁移开关与脚本（默认关闭，仅升级期使用）。

---

## 1) 术语与范围（避免混淆）

- **AntiHub-plugin**：历史上的独立 Node 服务（常见端口 `8045`），过去承载 Antigravity/Kiro/Qwen 等上游对接与账号/配额逻辑。
- **plugin-api（路径/命名）**：本仓库为了兼容旧前端/旧客户端而保留的 HTTP 路径或模块命名（例如 `/api/plugin-api/*`、`plugin_api_service.py`）。  
  这不等于 “运行时需要 AntiHub-plugin 容器”。
- **plugin DB**：历史上 plugin 服务自己的 Postgres 库（常见 DB 名 `antigravity`），现仅在迁移期开启时作为数据源。
- **本报告聚焦**：Kiro / Antigravity / Qwen 在 **运行时链路** 是否会：
  1) 调用 `AntiHub-plugin` HTTP（例如 `*:8045` 或 `PLUGIN_API_BASE_URL`）  
  2) 直接读写旧 plugin DB  
  3) 依赖 plugin 侧 Redis  
  4) 或仅是命名/兼容残留

---

## 2) 渠道现状：Kiro / Antigravity / Qwen 是否依赖 Plugin？

下表按“真正运行时依赖”口径判断：

| 渠道 | 账号/凭证存储 | 上游请求 | 是否需要 AntiHub-plugin 运行时 | 备注 |
|---|---|---|---|---|
| **Kiro** | Backend DB：`kiro_accounts` 等 | 直连 `*.auth.desktop.kiro.dev` 等 | **否** | 注释仍提及“与 plugin 行为一致”，不构成依赖 |
| **Qwen** | Backend DB：`qwen_accounts`（加密 JSON） | 直连 `https://chat.qwen.ai/...` | **否** | `pluginType=GEMINI` 是上游 metadata 字段名 |
| **Antigravity** | Backend DB：`antigravity_accounts` / `antigravity_model_quotas` | 直连 Google OAuth + Cloud Code | **否（核心链路）** | 仍沿用 `/api/plugin-api/*` 路径与 `PluginAPIService` 命名（兼容层）；已移除所有对外部 plugin 的 HTTP 代理能力 |

### 2.1 Kiro（已本地化，无 plugin proxy）

- 账号与配置存储：Backend DB（`kiro_accounts`、`kiro_subscription_models`）。
- OAuth / token refresh / chat / models：`AntiHub-Backend/app/services/kiro_service.py` 直连上游（`*.auth.desktop.kiro.dev` 等），仓库内未发现任何 `PLUGIN_API_BASE_URL` / `:8045` 调用链路。
- 仍然出现 “plugin” 字样的原因：若干文件注释提到“与 AntiHub-plugin 行为一致”，属于历史对齐说明，不影响运行时。

### 2.2 Qwen（已本地化，无 plugin proxy）

- 账号与配置存储：Backend DB（`qwen_accounts`），凭证以加密 JSON 落库（加密密钥目前命名为 `PLUGIN_API_ENCRYPTION_KEY`）。
- OAuth Device Flow / token / chat：`AntiHub-Backend/app/services/qwen_api_service.py` 直连 `https://chat.qwen.ai/...`。
- 注意：`QWEN_CLIENT_METADATA` 中出现 `pluginType=GEMINI`，这是上游要求的客户端 metadata 字段名，不代表依赖 AntiHub-plugin 服务。

### 2.3 Antigravity（核心已本地化，但“plugin-api” 命名仍在）

- 账号/配额存储：Backend DB（`antigravity_accounts`、`antigravity_model_quotas`）。
- OAuth / Cloud Code / chat completions：`AntiHub-Backend/app/services/plugin_api_service.py` 内部直连 Google OAuth + `cloudcode-pa.googleapis.com` 等上游，不依赖 AntiHub-plugin 容器。
- 仍可能引发“看起来依赖 plugin”的点：
  - 账号管理路径仍叫 `/api/plugin-api/*`（前端也在调用）。  
  - 仍使用 `PluginAPIService` / `plugin_api_service.py` 这类历史命名（但已移除 outbound plugin HTTP 逻辑，不再读取 `PLUGIN_API_BASE_URL/PLUGIN_API_ADMIN_KEY`）。

---

## 3) Spec ↔ config_type 白名单（Report.md 3.1-3.4，供代码引用）

> 说明：`AntiHub-Backend/app/core/spec_allowlist.py` 注释声明整理自 `Report.md` 的 3.1-3.4 小节；此处给出可读解释（与“移除 plugin”无直接因果，但本文件在 repo 内被引用）。

### 3.1 OAIResponses（当前 allowlist）

- 当前放行：`codex`
- 原因：Responses 语义/字段与其它渠道差异较大，现阶段只对 Codex 做稳定支持。

### 3.2 OAIChat（当前 allowlist）

- 当前放行：`antigravity`, `kiro`, `qwen`, `gemini-cli`
- 备注：`codex` 在目标态 allowlist 中，但当前默认未放开（见 `SPEC_CONFIG_TYPE_ALLOWLIST_TARGET`）。

### 3.3 Claude（当前 allowlist）

- 当前放行：`antigravity`, `kiro`, `qwen`
- 备注：Claude spec 侧主要通过 `anthropic.py` 转换到内部统一的 OpenAI ChatCompletions 分流。

### 3.4 Gemini（当前 allowlist）

- 当前放行：`gemini-cli`, `zai-image`, `antigravity`
- 备注：其中 `antigravity` 走 Cloud Code；`gemini-cli` 走 CLI 体系；`zai-image` 走图像生成。

---

## 4) 运行时依赖审计（HTTP / DB / Redis）

### 4.1 HTTP 出站：是否会请求 `AntiHub-plugin:8045`？

- 根目录 Compose：不再启动 `antihub-plugin` 服务（`docker-compose.yml` / `docker-compose.core.yml`）。
- Kiro/Qwen：服务代码无 `:8045`、`PLUGIN_API_BASE_URL` 调用。
- Antigravity：核心链路无 `:8045`，且已移除任何 “proxy 到外部 plugin” 的代码路径。

### 4.2 DB：是否还需要旧 plugin DB？

- 日常运行：Backend 使用单库（`DATABASE_URL` 指向 antihub DB），Kiro/Qwen/Antigravity 的账号表均在此库。
- 旧 plugin DB：仅在 `PLUGIN_DB_MIGRATION_ENABLED=true` 时作为迁移数据源使用（默认关闭）。  
  参考：`AntiHub-Backend/app/services/plugin_db_migration_service.py`、`4-docs/plugin-db-to-backend-migration.md`。

### 4.3 Redis：是否依赖旧 plugin Redis？

- 日常运行：Backend 只依赖 `REDIS_URL`。
- `.env.example` 中仍留有 `PLUGIN_REDIS_*` 注释入口，但当前代码链路不要求该组变量存在。

---

## 5) Repo 内仍存在的 “plugin” 触点（按层级清单）

### 5.1 Backend（代码/DB）

- 兼容路由：`AntiHub-Backend/app/api/routes/plugin_api.py`（提供 `/api/plugin-api/*` 入口）
- 服务实现：`AntiHub-Backend/app/services/plugin_api_service.py`（实际承担 Antigravity + 历史兼容）
- 迁移服务：`AntiHub-Backend/app/services/plugin_db_migration_service.py`（仅迁移期开关启用）
- 迁移表：`plugin_user_mappings`（Alembic migration + model）
- 旧密钥表：`plugin_api_keys`（Alembic migration + model；目前更偏历史/迁移用途）
- Settings：`AntiHub-Backend/app/core/config.py` 仅保留 `plugin_api_encryption_key`（Fernet 加密用），已删除 `plugin_api_base_url/plugin_api_admin_key`

### 5.2 Frontend（Next.js）

- 代理路由：`AntiHub/app/api/plugin-api/[[...path]]/route.ts`（把 web 侧 `/api/plugin-api/*` 转发到 backend）
- API SDK：`AntiHub/lib/api.ts`（Antigravity 账号管理仍调用 `/api/plugin-api/*`）

### 5.3 Compose / Deploy / 脚本

- 迁移期 DB init：`docker/postgres/init/01-init-plugin-db.sh`、`docker/postgres/sync-plugin-db.sh`、`docker/docker-compose.db-init.yml`
- 一键脚本：`deploy.sh` 仍包含 “旧 plugin DB 初始化” 分支（仅在 `.env` 显式开启时触发）
- 示例 env：`.env.example`、`AntiHub-Backend/.env.example` 中仍存在大量 `PLUGIN_*` 变量说明（部分已是历史/可选）

### 5.4 Docs

- `4-docs/merge-plugin-into-backend-scope.md`（迁移范围与兼容契约）
- `4-docs/backend_plugin_forwarding_inventory.csv`（迁移 inventory）
- `4-docs/plugin_public_routes.csv` / `4-docs/plugin_touchpoints.csv`（其中 `plugin_touchpoints.csv` 已出现与当前实现不一致的内容，建议复核/更新）
- `AntiHub-Backend/README.md`（已更新为“无需外部 plugin 服务”的表述）

### 5.5 额外：本地未跟踪目录

- 仓库根目录存在本地目录：`临时-勿提交git-plugin/`（不在 Git 跟踪范围）。  
  该目录在若干 docs（如 inventory CSV）中被引用作为“历史插件代码对照”，如果你要求彻底移除 plugin，可手动删除该目录。

---

## 6) 方案（仅保留一个）：全面移除 AntiHub-plugin 依赖（含接口迁移说明）

本方案目标：让 Kiro / Antigravity / Qwen 在运行时不再需要任何外部 `AntiHub-plugin` HTTP/容器；并把所有“曾经通过 plugin 代理/管理”的能力迁移到 Backend 本地实现。

### 6.1 后端依赖 plugin 的接口清单（HTTP / DB）

> 这里的“依赖”指：后端运行时需要外部 AntiHub-plugin 服务/端口/接口。

| 类别 | 旧依赖点（plugin 侧） | 后端触发位置 | 现状 | 迁移/替代 |
|---|---|---|---|---|
| HTTP | `POST {PLUGIN_API_BASE_URL}/api/users`（创建 plugin users） | 旧：`PluginAPIService.create_plugin_user` | ✅ 已移除 | 后端不再创建/绑定 plugin 用户 |
| HTTP | `ANY {PLUGIN_API_BASE_URL}{path}`（通用 proxy） | 旧：`PluginAPIService.proxy_request` / `proxy_stream_request` | ✅ 已移除 | 各渠道由 Backend 直连上游（Antigravity/Kiro/Qwen/…） |
| DB | 旧 plugin DB（`accounts/model_quotas/...`） | `plugin_db_migration_service`（启动期，可选） | ✅ 保留（默认关闭） | 仅升级迁移期使用，不属于运行时硬依赖 |

并且：
- `/api/plugin-api/*` 目前是 **后端内部实现的 legacy 路径**（兼容旧前端/调用方），不再代理到 `AntiHub-plugin:8045`。

### 6.2 部署迁移说明（从“需要 plugin” → “只跑 web+backend”）

1) **删除/忽略以下环境变量（不再使用）**：
   - `PLUGIN_API_BASE_URL`
   - `PLUGIN_API_ADMIN_KEY`

2) **保留/必配**（不要随意更换）：
   - `PLUGIN_API_ENCRYPTION_KEY`：Fernet key，用于加密存储上游凭证/API Key（更换会导致历史密文无法解密）
   - `JWT_SECRET_KEY`
   - `DATABASE_URL` / `REDIS_URL`

3) **对外服务入口**：
   - 旧：客户端/前端直连 `AntiHub-plugin:8045`
   - 新：统一直连 `AntiHub-Backend`（例如 `/v1/chat/completions`、`/api/kiro/*`、`/api/qwen/*`、`/api/plugin-api/*` legacy）

4) **旧数据迁移（可选）**：如果你之前使用的是 plugin DB 数据卷
   - 设置：
     - `PLUGIN_DB_MIGRATION_ENABLED=true`
     - `PLUGIN_MIGRATION_DATABASE_URL=postgresql+asyncpg://...`（指向旧 plugin DB）
   - 重启 backend 后自动执行一次迁移（见 `4-docs/plugin-db-to-backend-migration.md`）

### 6.3 本次已完成的迁移/清理点（2026-02-17）

- 后端：`AntiHub-Backend/app/core/config.py` 删除 `plugin_api_base_url/plugin_api_admin_key`。
- 后端：`AntiHub-Backend/app/services/plugin_api_service.py` 删除 `create_plugin_user`、`proxy_request`、`proxy_stream_request` 等 outbound plugin HTTP 能力。
- 文档/示例：`AntiHub-Backend/.env.example`、`AntiHub-Backend/docker-compose.yml`、`AntiHub-Backend/README.md`、`.env.example` 移除 `PLUGIN_API_BASE_URL/PLUGIN_API_ADMIN_KEY` 提示。

### 6.4 后续（如果你要求“连命名也去掉 plugin”）

- 将 Antigravity 相关账号管理从 `/api/plugin-api/*` 重命名为 `/api/antigravity/*` 并同步前端；旧路径可短期 301/410。
- 将 `PLUGIN_API_ENCRYPTION_KEY` 重命名为更中性的 `APP_ENCRYPTION_KEY`（建议双读过渡），并提供明确迁移步骤。

## 8) 验证清单（如何证明 “plugin 已移除”）

### 8.1 代码层 grep

建议逐步清零以下关键词命中（或至少将其限定为“纯注释/历史说明”）：

- `:8045`
- `PLUGIN_API_BASE_URL`
- `antihub-plugin`
- `AntiHub-plugin`
- `临时-勿提交git-plugin`

### 8.2 Docker 形态

- `docker compose up -d` 后 `docker ps` 不应出现 plugin 容器

### 8.3 运行时回归（最小集）

- `/v1/chat/completions` 分别以 `X-Api-Type: antigravity|kiro|qwen` 验证 stream/非 stream
- 管理后台：账号增删改查（Antigravity/Kiro/Qwen）与模型列表

---

## 9) 附录：文件级清单（按文件名包含 plugin）

以下文件名直接包含 `plugin`（`git ls-files | rg -i plugin`）：

- `4-docs/backend_plugin_forwarding_inventory.csv`
- `4-docs/merge-plugin-into-backend-scope.md`
- `4-docs/plugin-db-to-backend-migration.md`
- `4-docs/plugin_public_routes.csv`
- `4-docs/plugin_touchpoints.csv`
- `AntiHub-Backend/alembic/versions/2b6c1a1f7c3e_add_plugin_user_mappings_table.py`
- `AntiHub-Backend/alembic/versions/365ffc1d6ea0_add_plugin_api_key_table.py`
- `AntiHub-Backend/app/api/routes/plugin_api.py`
- `AntiHub-Backend/app/models/plugin_api_key.py`
- `AntiHub-Backend/app/models/plugin_user_mapping.py`
- `AntiHub-Backend/app/repositories/plugin_api_key_repository.py`
- `AntiHub-Backend/app/schemas/plugin_api.py`
- `AntiHub-Backend/app/services/plugin_api_service.py`
- `AntiHub-Backend/app/services/plugin_db_migration_service.py`
- `AntiHub/app/api/plugin-api/[[...path]]/route.ts`
- `docker/postgres/init/01-init-plugin-db.sh`
- `docker/postgres/sync-plugin-db.sh`

---

## 11) 兼容性 / 弃用清单（与 `4-docs/merge-plugin-into-backend-scope.md` 对齐）

### 11.1 已删除的 plugin side effects（历史说明）

- 登录/初始化时自动创建 plugin 用户：已删除（参考 `4-docs/backend_plugin_forwarding_inventory.csv` 对应条目）

### 11.2 仍保留但会返回 410 的旧接口

#### 11.2.3 Antigravity 配额相关（部分迁移，部分弃用）

- `GET /api/plugin-api/quotas/shared-pool` → `410 Gone`
  - 原因：shared-pool 语义不再支持（单库后只保留 user quota 聚合视图）
- `GET /api/plugin-api/quotas/consumption` → `410 Gone`
  - 替代：`/api/usage/requests/*`
- `PUT /api/plugin-api/preference` → `410 Gone`
  - 原因：`prefer_shared` 机制弃用

（建议 410 返回体包含 `error` 与 `alternative` 字段，便于前端展示/引导；目前部分实现已按此约定返回。）
