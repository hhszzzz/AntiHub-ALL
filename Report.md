# 摒弃 AntiHub-plugin：全部合并到 AntiHub-Backend 的落地方案（含迁移与验收）

日期：2026-02-14  
目标：移除运行时 plugin 依赖；所有账号/配额/上游调用统一在 Backend 落地；最终单库。  
适用：本仓库 Docker Compose 部署（`docker-compose.yml` / `docker-compose.core.yml`）。

---

## TL;DR（给后续开发人员的 30 秒版）

- **最终不再部署 `AntiHub-plugin`（8045）**：Backend 不再 HTTP 转发到 Node；Compose 里移除 `plugin` 服务。
- **数据库只保留 Backend 单库**（Compose 默认 `POSTGRES_DB=antihub`）；plugin DB（默认 `antigravity`）只用于一次性迁移。
- **鉴权统一 Backend**：只保留 Backend `users` + `api_keys`；plugin 的 `users/api_key` 体系淘汰。
- **迁移只迁“账号/配额/订阅白名单”**；不迁 plugin 历史消费日志；不实现 `user_shared_quota_pool` 等共享池/授权额度机制。
- **共享概念全部移除**：不再存在“共享账号/共享配额池/共享优先级”；历史字段/参数（`is_shared` / `prefer_shared`）不再作为业务概念保留。
- **前端需要适配（主要是 Analytics）**：配额数据完全来自 Backend 本地表（`antigravity_accounts` / `antigravity_model_quotas`），趋势/明细统一走 `/api/usage/requests/*`（本系统用量日志）；`/api/plugin-api/quotas/consumption` 等 plugin 历史“配额消耗日志”接口弃用。
- **对外契约只保证 Backend**：现有前端调用的 `/api/plugin-api/*`、`/api/kiro/*`、`/api/qwen/*`、`/v1/*` 等继续可用；不再保证任何直连 plugin 的外部客户端。

---

## 0. 决策与边界（默认已拍板，不要在实现中偷偷改）

### 0.1 兼容性范围

- ✅ 必须保证：**现有前端** + **现有 Backend 对外 API** 正常。
- ❌ 不保证：任何“直连 `AntiHub-plugin:8045`”的第三方客户端兼容性；plugin 路由/返回值不是对外契约。

### 0.2 运行时组件（最终形态）

- 保留：`web`、`backend`、`postgres`、`redis`
- 移除：`plugin`（Node 服务）及其对 Backend 的运行时依赖

### 0.3 数据库（最终单库）

- 最终只保留 Backend 使用的一个 DB（Compose 默认 `antihub`）。
- plugin DB（Compose 默认 `antigravity`）仅作为**迁移源**；迁移完成后不再创建、不再使用。

### 0.4 用户与鉴权

- 只保留 Backend 的 `users`（JWT 登录）与 `api_keys`（sk-...）。
- plugin 的 `users(api_key, prefer_shared, ...)` 体系为历史：迁移后淘汰。
- Backend 的 `plugin_api_keys`（用于调用 plugin 的密钥映射表）为历史：迁移阶段可用于“UUID→user_id 映射”，最终不再参与请求链路。

### 0.5 迁移数据范围

- ✅ 迁移：账号表 + 必要配置 + 渠道配额/订阅白名单
- ❌ 不迁移：`quota_consumption_log`、`kiro_consumption_log` 等 plugin 历史消费日志
- ❌ 明确丢弃：`user_shared_quota_pool`、`shared_pool_quotas_view` 等共享池/授权额度机制（以及相关函数/脚本）
- ❌ 共享概念移除：不迁移/不保留 plugin 的 `is_shared` / `prefer_shared`（统一视为个人部署的“单人账号管理”）

### 0.6 并发假设（必须多实例安全）

- 不讨论“到底会不会多实例”，实现必须 **默认多实例安全**：
  - 启动迁移：必须分布式锁
  - Qwen device flow 轮询 / Kiro usage limits 背景同步：必须把状态放 Redis/DB（不能只放进程内）

### 0.7 凭证加密

- 所有上游 token/refresh_token/client_secret 等敏感数据：
  - 统一落在 `credentials`（加密 JSON）字段
  - 不允许明文字段落库
- 继续使用 Backend 现有 `Fernet` 加密能力（`AntiHub-Backend/app/utils/encryption.py`）；密钥来自 `PLUGIN_API_ENCRYPTION_KEY`（历史命名，但继续作为系统加密主密钥）。

### 0.8 术语（避免“plugin 已经没了却还叫 plugin-api”）

- AntiHub-plugin：历史 Node 服务，将被移除。
- `/api/plugin-api/*`：Backend 现有路由前缀（由 `AntiHub-Backend/app/api/routes/plugin_api.py` 定义，并在 `AntiHub-Backend/app/main.py` 中以 `prefix="/api"` 挂载）。  
  合并后 **路由前缀保留**（为了前端不改动），但实现改为 Backend 本地逻辑，不再 HTTP proxy 到 Node。

### 0.9 接口清单来源（防漏项，必须看）

本报告不是凭空“拍脑袋列接口”，所有路由覆盖都以仓库内两份表格为准（后续开发照着对照即可，不会漏）：

- Backend 对外契约（**必须持续可用**）：`4-docs/BACKEND_PUBLIC_ROUTES.csv`
- Plugin 历史接口（**迁移覆盖参考**）：`4-docs/plugin_public_routes.csv`

规则：

- 任何 **新增/删除/改名** 的后端对外路由，都必须同步更新 `4-docs/BACKEND_PUBLIC_ROUTES.csv`（否则后续开发会以为“漏了”或“还没做”）。
- 本次合并的目标是：`BACKEND_PUBLIC_ROUTES.csv` 中涉及 `plugin-api / kiro / qwen / v1 / v1beta` 的链路，合并后不再经过 Node 服务。

---

## 1. 现状：哪里依赖 plugin（改造触点清单）

### 1.1 Compose 现状（双 DB + plugin 进程）

- `docker-compose.yml` 当前包含：
  - `backend`：FastAPI（启动会执行 `alembic upgrade heads`）
  - `plugin`：Node/Express（8045）
  - `postgres`：同时初始化 Backend DB（默认 `antihub`）与 plugin DB（默认 `antigravity`，由 `docker/postgres/init/01-init-plugin-db.sh` 创建）
  - `redis`：plugin 用于 OAuth state；backend 也使用
- `docker-compose.core.yml` 同样包含 `plugin` 服务与 `PLUGIN_API_BASE_URL` 依赖。

### 1.2 Backend 当前的 “proxy plugin” 代码路径（必须替换/下线）

以下文件目前直接或间接依赖 `PLUGIN_API_BASE_URL` 并调用 Node 服务：

- 账号与 OAuth 管理：
  - `AntiHub-Backend/app/api/routes/plugin_api.py`（对前端暴露 `/api/plugin-api/*`）
  - `AntiHub-Backend/app/api/routes/qwen.py`（`/api/qwen/*`）
  - `AntiHub-Backend/app/api/routes/kiro.py`（`/api/kiro/*`，通过 `KiroService` 代理）
- 对外 OpenAI/Anthropic/Gemini 兼容接口（会走 Antigravity/Kiro/Qwen）：
  - `AntiHub-Backend/app/api/routes/v1.py`（`/v1/*`）
  - `AntiHub-Backend/app/api/routes/anthropic.py`（`/v1/messages` 与 `/cc/v1/messages`）
  - `AntiHub-Backend/app/api/routes/gemini.py`（`/v1beta/models/*`）
- 具体代理实现：
  - `AntiHub-Backend/app/services/plugin_api_service.py`
  - `AntiHub-Backend/app/services/kiro_service.py`
- 登录/管理员初始化里的 “自动创建 plugin 用户/密钥”（合并后必须移除）：
  - `AntiHub-Backend/app/api/routes/auth.py`（登录后 `auto_create_and_bind_plugin_user`）
  - `AntiHub-Backend/app/utils/admin_init.py`（创建管理员后自动创建 plug-in API key）

> 实现落地时，最低目标是：上述文件不再发起到 `http://antihub-plugin:8045` 的 HTTP 请求。

---

## 2. 目标架构（合并后）

### 2.1 运行时数据流

- 现在：`web -> backend -> HTTP -> plugin -> upstream`
- 合并后：`web -> backend -> upstream`（Backend 内部本地 service 直接管理账号、刷新 token、发起请求、SSE 转发）

### 2.2 单库

- Backend DB 继续为唯一数据源（账号表、配额表、订阅模型表都落同一个 DB）。
- plugin DB 只在迁移时连接读取，迁移完成后不再需要。

### 2.3 Redis（合并后仍然必需，且要“用对”）

Redis 不会随着 plugin 下线而消失：合并后 Backend 需要把“临时状态 / 节流状态 / 多实例锁”统一收口到 Redis（或 DB），避免再出现 plugin 那种 **进程内 Map** 带来的多实例不一致。

#### 2.3.1 配置（合并后唯一入口）

- Compose 中 Backend 统一使用 `REDIS_URL`（例如 `redis://redis:6379/0`）。
- 合并后可删除 plugin 专用的 `PLUGIN_REDIS_HOST/PLUGIN_REDIS_PORT/PLUGIN_REDIS_PASSWORD`（因为不再部署 Node 插件）。

#### 2.3.2 必须落 Redis 的状态（按功能分组）

- **Antigravity OAuth state（Google）**  
  现状：plugin 用内存 `Map` 保存 `state -> {user_id,...}`（5 分钟过期），多实例必炸。  
  合并后：Backend 在 `POST /api/plugin-api/oauth/authorize` 时写 Redis，TTL=300s；`POST /api/plugin-api/oauth/callback` 读取并校验 state。
- **Kiro OAuth state（Social / IdC）**  
  合并后：继续用 Redis 保存 state（建议 TTL=600s，沿用 plugin 行为），`/api/kiro/oauth/status/{state}` 只读状态，不回传敏感 token。
- **Qwen Device Flow state（轮询状态机）**  
  合并后：用 Redis 保存 state（TTL 建议跟随 `expires_in`，范围 300~3600s，沿用 plugin 的 clamp 策略）；只存状态与元信息，不把 access_token/refresh_token 写 Redis（敏感信息只落 DB 的 `credentials`）。
- **Kiro usage limits 节流 + 429 冷却**  
  现状：plugin 用进程内 `Map(account_id -> {inFlight,lastSuccessAt,cooldownUntil})`。  
  合并后：状态改为 Redis（key 按 `account_id` 维度），否则多实例会重复打上游 `/getUsageLimits` 导致 429。
- **后台轮询/同步任务的跨实例互斥**（必须）  
  - Qwen device flow 后台轮询：同一 state 只允许一个实例轮询  
  - Kiro usage limits 后台同步：同一账号只允许一个实例在同步窗口内执行  
  推荐用 Redis `SET key value NX EX ttl` 做轻量锁（TTL 必须设置，避免死锁）；不具备该能力时，用 Postgres advisory lock 替代。

#### 2.3.3 Key 命名与 TTL（建议统一前缀，避免污染/冲突）

建议统一加项目前缀（示例，不强制）：

- `antihub:antigravity:oauth:state:{state}`（300s）
- `antihub:kiro:oauth:state:{state}`（600s）
- `antihub:qwen:oauth:state:{state}`（300~3600s）
- `antihub:kiro:usage_limits:state:{account_id}`（>= 冷却期 + buffer）
- `antihub:lock:{purpose}:{id}`（按锁 TTL 设置）

#### 2.3.4 Redis 不可用时的行为（明确到接口）

为了避免“看起来还能用，但状态错乱”的隐性故障，本方案约定：凡是**依赖 Redis 保证正确性**的接口，Redis 不可用时直接失败（503）。

- Antigravity OAuth：`POST /api/plugin-api/oauth/authorize`、`POST /api/plugin-api/oauth/callback` → 503
- Kiro OAuth：`POST /api/kiro/oauth/authorize`、`GET /api/kiro/oauth/status/{state}`、`POST /api/kiro/oauth/callback` → 503
- Qwen OAuth：`POST /api/qwen/oauth/authorize`、`GET /api/qwen/oauth/status/{state}` → 503（对齐 plugin 行为）
- 仅用于缓存/限流的点：可降级为 DB/无缓存，但不得把敏感信息写进 Redis，也不得破坏多实例幂等

### 2.4 渠道区分（个人部署的核心：用 API key / 标头选择渠道）

合并后，“走哪个渠道/哪个上游”不再由 plugin 路由决定，而由 Backend 统一根据 `config_type` 选择（这也是你说的“像 CodexCLI 一样用后端内置”的方式）。

- **API Key（推荐，个人部署的主要方式）**：创建 API key 时选择 `config_type`（例如 `antigravity/kiro/qwen/codex/gemini-cli`），然后用 `Authorization: Bearer sk-...` 直接调用 `/v1/*`。  
  结论：不需要也不提供 `/v1/kiro/*` 这类“单渠道专用端点”。
- **JWT（Web UI）**：默认使用 `antigravity`；需要切换渠道时通过请求头指定：`X-Api-Type: <config_type>`。
- **客户端标识（用量日志）**：可选请求头 `X-App: <string>`；后端会写入 usage logs，方便你按客户端筛选。

注意：

- `X-Api-Type` 仅在 **JWT 场景**生效；API Key 场景以密钥自身 `config_type` 为准（更安全，也更符合“个人用 API key 调用”的目标）。

---

## 3. 数据库设计（最终表结构，直接按此建表/写 Alembic）

目标：对齐 Backend 现有渠道表风格（`codex_accounts`、`gemini_cli_accounts` 等）：

- `id(int PK) + user_id(FK) + status + 账号字段 + credentials(加密 JSON) + timestamps`

### 3.1 必须新增的表（Backend DB）

#### 3.1.1 `antigravity_accounts`

用途：替代 plugin `public.accounts`。

核心字段（要求与 plugin 字段一一对应，便于迁移和 UI 兼容）：

- `id`：int PK
- `user_id`：FK → `users.id`
- `cookie_id`：string，**唯一**（来自 plugin `accounts.cookie_id`）
- `account_name`：string（对应 plugin `name`）
- `email`：string nullable
- `project_id_0`：string nullable
- `status`：int（0/1）
- `need_refresh`：bool
- `is_restricted`：bool
- `paid_tier`：bool nullable
- `ineligible`：bool
- `token_expires_at`：timestamp nullable（由 plugin `expires_at(ms)` 转换）
- `last_refresh_at`：timestamp nullable
- `credentials`：Text（加密 JSON，至少包含 `access_token`、`refresh_token`、`expires_at_ms` 原值）
- `created_at` / `updated_at` / `last_used_at`

约束/索引：

- `UNIQUE(cookie_id)`
- `INDEX(user_id)`

#### 3.1.2 `antigravity_model_quotas`

用途：替代 plugin `public.model_quotas`。

字段：

- `id`：int PK
- `cookie_id`：string（FK/软关联到 `antigravity_accounts.cookie_id`）
- `model_name`：string
- `quota`：numeric/float（plugin 是 numeric(5,4)，值域 0~1）
- `reset_at`：timestamp nullable（plugin `reset_time`）
- `status`：int（0/1）
- `last_fetched_at`：timestamp nullable
- `created_at` / `updated_at`

约束：

- `UNIQUE(cookie_id, model_name)`（对齐 plugin `uk_cookie_model`）

#### 3.1.3 `kiro_accounts`

用途：替代 plugin `public.kiro_accounts`。

字段（按 plugin schema 保持，避免 UI/逻辑改动）：

- `id`：int PK
- `user_id`：FK → `users.id`
- `account_id`：string，**唯一**（保存 plugin `kiro_accounts.account_id` 的 UUID 文本，便于迁移与接口参数兼容）
- `account_name`：string nullable
- `auth_method`：string（Social/IdC）
- `region`：string（默认 `us-east-1`）
- `machineid`：string
- `email`：string nullable
- `userid`：string（plugin 字段名就是 userid）
- `subscription`：string
- `status`：int（0/1）
- `need_refresh`：bool
- `token_expires_at`：timestamp nullable（由 plugin `expires_at(ms)` 转换）
- `credentials`：Text（加密 JSON，至少包含 `refresh_token`；如有 `access_token/client_id/client_secret/profile_arn` 也放进去）
- usage limits（用于展示/节流，字段保持一致）：
  - `current_usage`、`reset_date`、`usage_limit`、`bonus_*`、`bonus_details(jsonb)`、`free_trial_*` ...
- `created_at` / `updated_at` / `last_used_at`

约束：

- `UNIQUE(account_id)`
- `INDEX(user_id)`

#### 3.1.4 `qwen_accounts`

用途：替代 plugin `public.qwen_accounts`。

字段：

- `id`：int PK
- `user_id`：FK → `users.id`
- `account_id`：string，**唯一**（保存 plugin `qwen_accounts.account_id` 的 UUID 文本）
- `account_name`：string nullable
- `resource_url`：string（默认 `portal.qwen.ai`）
- `email`：string nullable
- `status`：int
- `need_refresh`：bool
- `token_expires_at`：timestamp nullable（由 plugin `expires_at(ms)` 转换）
- `last_refresh_at`：timestamp nullable（如 plugin `last_refresh` 无法解析为时间，原值落入 `credentials`）
- `credentials`：Text（加密 JSON，至少包含 `access_token`、`refresh_token`、`expires_at_ms` 原值、`resource_url` 原值）
- `created_at` / `updated_at` / `last_used_at`

#### 3.1.5 `kiro_subscription_models`

用途：迁移 plugin `public.kiro_subscription_models`（订阅 → 允许的模型列表）。

- PK：`subscription`
- 字段：`allowed_model_ids jsonb`、`created_at`、`updated_at`

#### 3.1.6 `migration_state`（必须）

用途：保证“启动迁移强制执行”仍然可幂等、可排障、可重复启动。

字段要求：

- `name`：string，唯一（例如 `plugin_to_backend_v1`）
- `status`：string（`running/succeeded/failed`）
- `started_at`、`finished_at`
- `last_error`：text nullable
- `details`：jsonb nullable（记录迁移计数、源库信息、版本号等）

### 3.2 明确移除的“共享”概念（本次合并决策）

本方案面向个人部署：不需要跨用户共享账号/配额，因此共享概念全部移除。

- 不新增/不保留：`users.prefer_shared`
- 不新增/不保留：各账号表的 `is_shared`
- 相关接口统一下线：`GET /api/plugin-api/quotas/shared-pool` 固定返回 410 Gone

---

## 4. 迁移方案（启动时强制跑，失败即启动失败）

### 4.1 迁移开关（对外契约）

新增/保留以下环境变量（写入 `.env.example`，并在 Backend `Settings` 中读取）：

- `MIGRATION_TYPE`：`true/false`
  - `true`：启动时执行迁移
  - `false`：跳过迁移
- `MIGRATION_SQLURL`：plugin 源库连接串（SQLAlchemy URL）
  - Compose 同机默认示例：`postgresql+asyncpg://antigravity:please-change-me@postgres:5432/antigravity`

约束：

- `MIGRATION_TYPE=true` 时，`MIGRATION_SQLURL` 必须存在且可连，否则启动失败。
- 迁移成功后，将 `MIGRATION_TYPE=false` 并清空 `MIGRATION_SQLURL`（避免误触发）。

### 4.2 执行时机（推荐在 Backend lifespan 中做）

顺序必须固定（避免“表还没建好就开始迁移”）：

1. `alembic upgrade heads`（已由 compose command 执行）
2. Backend 启动，完成 DB/Redis init
3. 确保管理员账号存在（`ensure_admin_user`）
4. 若 `MIGRATION_TYPE=true`：执行迁移
5. 迁移成功：写 `migration_state`，继续启动
6. 迁移失败：记录错误并让进程退出（避免半迁移）

### 4.3 迁移锁（多实例安全）

- 使用 Postgres advisory lock：迁移开始时获取全局锁，迁移结束释放。  
  好处：不依赖 Redis，且天然跨进程/跨实例（同一 DB）。
- 只要拿不到锁（已有实例在迁移）：当前实例直接退出并提示“已有迁移在进行”。

### 4.4 用户映射规则（plugin UUID → backend users.id）

这是迁移成败的关键，规则必须写死：

1) 建映射表：从 Backend DB 的 `plugin_api_keys` 读取：

- `plugin_api_keys.plugin_user_id (UUID text)` → `plugin_api_keys.user_id (int)`
- 该字段在仓库已存在（`AntiHub-Backend/app/models/plugin_api_key.py`）。

2) 迁移 plugin 源库任何带 `user_id(UUID)` 的表时：

- 若 `user_id(UUID)` 能在映射表中找到：归属到对应 backend 用户
- 若找不到：统一归属到 **管理员用户**

3) 管理员定位规则（必须确定且可复现）：

- 必须配置 `ADMIN_USERNAME` / `ADMIN_PASSWORD`（这样 `ensure_admin_user` 会在启动时保证该用户存在）
- 迁移时用 `ADMIN_USERNAME` 查询 `users.id` 作为“管理员用户”
- 若无法定位管理员用户：迁移直接失败（理由：无法安全归属“无映射数据”）

### 4.5 迁移内容与映射（明确到字段级）

#### 4.5.1 Antigravity：`public.accounts` → `antigravity_accounts`

- 主键：
  - plugin `accounts.cookie_id` → backend `antigravity_accounts.cookie_id`
- 归属：
  - plugin `accounts.user_id(UUID)` → 映射为 backend `user_id(int)`
- 凭证：
  - plugin `access_token/refresh_token/expires_at(ms)` → backend `credentials`（加密 JSON）
- 其余字段按原值迁移：`status/need_refresh/name/email/project_id_0/is_restricted/paid_tier/ineligible/created_at/updated_at`
  - 说明：plugin 的 `is_shared` 字段忽略（合并后不再存在“共享账号”概念）

#### 4.5.2 Antigravity：`public.model_quotas` → `antigravity_model_quotas`

- 唯一键：`(cookie_id, model_name)`（upsert）
- 字段：
  - `quota` 原值迁移（numeric 0~1）
  - `reset_time` → `reset_at`
  - `status/last_fetched_at/created_at` 保持

#### 4.5.3 Kiro：`public.kiro_accounts` → `kiro_accounts`

- 唯一键：`account_id`（upsert）
- 凭证字段全部进 `credentials`（`refresh_token/access_token/client_id/client_secret/profile_arn`）
- 其余字段（`machineid/region/subscription/usage_limit/...`）按原值迁移，保持 UI 行为

#### 4.5.4 Qwen：`public.qwen_accounts` → `qwen_accounts`

- 唯一键：`account_id`（upsert）
- `access_token/refresh_token/expires_at(ms)` 入 `credentials`（加密 JSON）
- `resource_url/status/need_refresh/email/created_at/updated_at` 保持
  - 说明：plugin 的 `is_shared` 字段忽略（合并后不再存在“共享账号”概念）

#### 4.5.5 Kiro 订阅模型：`public.kiro_subscription_models` → `kiro_subscription_models`

- 直接 upsert（PK=`subscription`）

#### 4.5.6 明确不迁移的表

- `quota_consumption_log`
- `kiro_consumption_log`
- `user_shared_quota_pool` 以及相关 view/function/trigger

### 4.6 幂等与失败策略（必须）

- 幂等：
  - 所有迁移表必须有可 upsert 的唯一键
  - 迁移过程中允许重复启动，不产生重复数据/不破坏已迁移数据
- 失败：
  - 任意一张表迁移失败 → 迁移整体失败 → Backend 启动失败
  - `migration_state` 记录 `last_error`，方便排障

---

## 5. 代码改造清单（按“先保证链路可用，再做清理”排序）

目标不是“写得漂亮”，是“合并后不再依赖 Node”。

### 5.1 去除所有到 plugin 的 HTTP 依赖

- 将 `PluginAPIService` 从 “HTTP proxy 到 plugin” 改为 “本地实现/上游直连”：
  - 第一阶段可保留类名与路由前缀不变（减少前端改动），但内部不允许再访问 `PLUGIN_API_BASE_URL`。
- 将 `KiroService` 从 “proxy plugin” 改为 “直连上游 + 本地 DB”。

### 5.2 登录/初始化逻辑去 plugin 化

- `AntiHub-Backend/app/api/routes/auth.py`：移除登录后自动创建 plugin 用户/密钥逻辑。
- `AntiHub-Backend/app/utils/admin_init.py`：移除管理员创建后自动创建 plugin 用户/密钥逻辑。

### 5.3 前端兼容（/api/plugin-api 不改路由，只改实现）

必须保证下列前端在用的接口继续返回可解析的 JSON（允许“尚无数据时为空”，但不允许用“永远空数组”当作实现）：

- Antigravity 账号管理（cookie_id 维度）：`/api/plugin-api/accounts/*`
- 配额（来自 Backend 本地表；共享概念已移除）：
  - `GET /api/plugin-api/accounts/{cookieId}/quotas`（账号维度的模型配额，数据源：`antigravity_model_quotas`）
  - `GET /api/plugin-api/quotas/user`（用户维度“模型配额概览”，由 `antigravity_model_quotas` 聚合生成；不再是 shared pool）  
    建议实现：每个 `model_name` 取“quota 最大的账号”作为该模型的可用额度；字段沿用前端 `UserQuotaItem` 结构：`pool_id/user_id/model_name/quota/max_quota/last_recovered_at/last_updated_at`。
- 消耗/趋势（统一用本系统日志，数据源：`usage_logs` / `usage_counters`）：
  - `/api/usage/requests/stats?config_type=antigravity`
  - `/api/usage/requests/logs?config_type=antigravity`
- OpenAI 兼容入口：`/v1/models`、`/v1/chat/completions`（SSE 必须 no-buffer）
- 渠道区分（个人部署推荐做法）：
  - API Key：创建时设置 `config_type`（`antigravity/kiro/qwen/codex/...`），调用 `/v1/*` 时无需额外标头
  - JWT：用 `X-Api-Type: <config_type>` 指定渠道
  - 可选：用 `X-App: <string>` 标记客户端（写入用量日志）

同时，以下接口属于 plugin 历史机制，合并后应 **前端移除依赖**；后端为避免误用，保留路由但返回 **410 Gone**：

- `GET /api/plugin-api/quotas/shared-pool`（共享池机制已丢弃）
- `GET /api/plugin-api/quotas/consumption`（plugin 的配额消耗日志不迁移；前端改用 `/api/usage/requests/*`）
- `PUT /api/plugin-api/preference`（共享优先级概念已移除）

需要改动的前端文件（本仓库内明确引用了上述接口）：

- `AntiHub/lib/api.ts`
- `AntiHub/app/dashboard/analytics/page.tsx`
- `AntiHub/components/quota-trend-chart.tsx`
- `AntiHub/components/section-cards.tsx`

---

## 6. 上游调用细节（需要对齐 plugin 行为，避免行为漂移）

### 6.1 SSE/流式转发

- 对外 `text/event-stream` 必须加 no-buffer headers（Backend 已有 `_sse_no_buffer_headers()` 实现，可复用）。
- 反向代理层（Nginx/Caddy）也必须关闭缓冲（在部署文档中写清楚）。

### 6.2 Antigravity（Google/Cloud Code）

参考 plugin 代码：

- `AntiHub-plugin/src/services/oauth.service.js`
- `AntiHub-plugin/src/services/project.service.js`
- `AntiHub-plugin/src/api/multi_account_client.js`

实现必须覆盖：

- OAuth token exchange/refresh：`oauth2.googleapis.com/token`
- 上游 SSE：`v1internal:streamGenerateContent?alt=sse` 等
- headers/重试策略对齐 plugin（迁移阶段不做“顺手优化”）

### 6.3 Kiro

参考 plugin 代码：

- `AntiHub-plugin/src/services/kiro.service.js`
- `AntiHub-plugin/src/api/kiro_client.js`

实现必须覆盖：

- auth/refresh：`prod.<region>.auth.desktop.kiro.dev`
- AWS OIDC：`oidc.<region>.amazonaws.com/token`
- 流式调用：`q.<region>.amazonaws.com/generateAssistantResponse`
- usage limits 节流与 429 冷却：状态必须落 Redis/DB（多实例安全）

### 6.4 Qwen

参考 plugin 代码：

- `AntiHub-plugin/src/services/qwen.service.js`
- `AntiHub-plugin/src/api/qwen_client.js`
- `AntiHub-plugin/src/server/qwen_routes.js`

实现必须覆盖：

- device flow：`/device/code` + `/token`（后台轮询）
- chat completions：`https://portal.qwen.ai/v1/chat/completions` 或 token 返回的 `resource_url`
- 后台轮询任务加锁（防多实例重复轮询）

---

## 7. 配置与部署（该删的删，该加的加）

### 7.1 合并后 Backend 需要的迁移配置

- 新增：`MIGRATION_TYPE`、`MIGRATION_SQLURL`
- 保留并作为系统加密主密钥：`PLUGIN_API_ENCRYPTION_KEY`

### 7.2 合并后可删除的跨服务配置

- 删除：`PLUGIN_API_BASE_URL`
- 删除：`PLUGIN_API_ADMIN_KEY`

### 7.3 Compose 改动（最终）

- `docker-compose.yml`：
  - 移除 `plugin` 服务
  - Backend 不再 `depends_on: plugin`
  - 移除 postgres init 脚本 `docker/postgres/init/01-init-plugin-db.sh`（或至少不再创建 plugin DB）
- `docker-compose.core.yml`：同上

---

## 8. 风险与对策（写给排障的人）

- 用户映射缺失：要求 `ADMIN_USERNAME` 必填；否则迁移失败（安全优先）。
- 迁移重复执行：必须有 `migration_state` + upsert 幂等。
- 多实例并发迁移：必须 advisory lock。
- SSE 被缓冲：必须 `X-Accel-Buffering: no` + `Cache-Control: no-transform`。
- 行为漂移：headers/重试策略严格对齐 plugin。

---

## 9. 分阶段路线图（按可交付物拆分）

### Phase 0（准备）

- Alembic：新增目标表结构（第 3 节）
- Backend：引入 `migration_state` + advisory lock 工具函数

### Phase 1（本地实现替换 proxy）

- Backend：用本地 service 替换所有到 plugin 的 HTTP 调用
- 前端：适配 Analytics 的配额/消耗展示（不再使用 plugin 的 shared-pool/consumption 体系；统一改用 Backend 本地表 + `/api/usage/requests/*`）

### Phase 2（一次性迁移）

- 设置：`MIGRATION_TYPE=true` + `MIGRATION_SQLURL=...` + `ADMIN_USERNAME/ADMIN_PASSWORD`
- 启动 Backend：迁移成功后写 `migration_state=succeeded`
- 关闭迁移开关：`MIGRATION_TYPE=false` 并清空 `MIGRATION_SQLURL`

### Phase 3（下线 plugin）

- Compose 移除 plugin 服务与 plugin DB 初始化脚本
- 清理代码：移除 `plugin_api_keys` 表与相关 repo/service（可单独 PR）

---

## 10. 验收清单（能直接照着测）

### 10.1 不含 plugin 的启动验收

1) `docker compose up -d`（不含 plugin）后：

- `web` 能正常登录
- `/api/health` 返回健康状态（包含 DB/Redis 连接正常）
- `/api/plugin-api/accounts` 能返回列表（即使为空也不能 500）
- `/api/qwen/accounts`、`/api/kiro/accounts` 能正常工作
- `/api/usage/requests/stats?config_type=antigravity` 与 `/api/usage/requests/logs?config_type=antigravity` 正常返回（调用后有数据）
- `/api/plugin-api/quotas/user` 正常返回（有账号/配额数据时不为空；无账号时可为空）
- `/api/plugin-api/quotas/consumption` 返回 410 Gone（并提示替代接口 `/api/usage/requests/*`）

2) `/v1/chat/completions`：

- 用不同渠道分别验证一次：  
  - API Key：创建不同 `config_type` 的 key（如 `antigravity/kiro/qwen/codex`），分别调用一次确认路由正确  
  - 或 JWT：用 `X-Api-Type: <config_type>` 指定渠道
- 非流式：返回正常 JSON
- 流式：能持续输出，且代理层不缓冲（无长时间无输出）

### 10.2 迁移验收（只在 Phase 2）

- `migration_state` 标记 succeeded
- 账号表 count 与源库一致（允许用户映射导致归属变化）
- 任意账号导出 credentials 能正常解密/使用

---

## 11. 接口迁移对照（按 CSV，防漏项）

> 本节的目的：让后续开发可以用“逐条勾选”的方式验收——plugin 被移除后，哪些路由必须仍可用，哪些路由明确下线/不再提供。

数据来源：

- Backend 对外契约：`4-docs/BACKEND_PUBLIC_ROUTES.csv`
- Plugin 历史接口：`4-docs/plugin_public_routes.csv`

### 11.1 Backend 对外契约：合并后必须继续可用的路由（节选）

以下路由在 `BACKEND_PUBLIC_ROUTES.csv` 中出现，且当前实现链路（或历史链路）依赖 `AntiHub-plugin`；合并后必须做到 **0 依赖 Node 也能工作**（除明确标注 410 的弃用项）。

#### 11.1.1 Antigravity / plug-in-api 前缀（`/api/plugin-api/*`）

- OAuth（Antigravity / Google）：  
  - `POST /api/plugin-api/oauth/authorize`（生成授权链接，state 必须落 Redis）  
  - `POST /api/plugin-api/oauth/callback`（手动提交 callback_url；从 Redis 取 state → 换 token → 落库）
- 账号（cookie_id 维度）：  
  - `GET /api/plugin-api/accounts`  
  - `GET /api/plugin-api/accounts/{cookie_id}`  
  - `POST /api/plugin-api/accounts/import`  
  - `POST /api/plugin-api/accounts/{cookie_id}/refresh`  
  - `GET /api/plugin-api/accounts/{cookie_id}/credentials`（敏感；凭证需加密存储）  
  - `GET /api/plugin-api/accounts/{cookie_id}/detail`  
  - `GET /api/plugin-api/accounts/{cookie_id}/projects`  
  - `PUT /api/plugin-api/accounts/{cookie_id}/project-id`  
  - `PUT /api/plugin-api/accounts/{cookie_id}/status`  
  - `PUT /api/plugin-api/accounts/{cookie_id}/name`  
  - `PUT /api/plugin-api/accounts/{cookie_id}/type`  
  - `DELETE /api/plugin-api/accounts/{cookie_id}`
- 配额：  
  - `GET /api/plugin-api/accounts/{cookie_id}/quotas`  
  - `PUT /api/plugin-api/accounts/{cookie_id}/quotas/{model_name}/status`  
  - `GET /api/plugin-api/quotas/user`（本方案中语义改为“模型配额概览”，不再是 shared pool）  
  - `GET /api/plugin-api/quotas/shared-pool`（**弃用：返回 410 Gone**）  
  - `GET /api/plugin-api/quotas/consumption`（**弃用：返回 410 Gone**，替代：`/api/usage/requests/*`）
- 用户信息（legacy，可选）：  
  - `GET /api/plugin-api/preference`（用于 API key 场景的 whoami；如响应中包含 `prefer_shared`，固定为 `0`）  
  - `PUT /api/plugin-api/preference`（**弃用：返回 410 Gone**）
- OpenAI/Gemini 兼容（API Key 鉴权的“旧入口”，仍属于对外契约）：  
  - `GET /api/plugin-api/models`  
  - `POST /api/plugin-api/chat/completions`  
  - `POST /api/plugin-api/v1beta/models/{model}:generateContent`
- 密钥（历史）：`GET /api/plugin-api/key`  
  合并后不再需要“plugin_api_keys”概念；建议在 Phase 3 清理 `plugin_api_keys` 时一并下线该路由（下线前先确认无外部依赖）。

#### 11.1.2 Kiro（`/api/kiro/*`）

- OAuth：`POST /api/kiro/oauth/authorize`、`POST /api/kiro/oauth/callback`、`GET /api/kiro/oauth/status/{state}`
- 账号：`POST /api/kiro/accounts`、`GET /api/kiro/accounts`、`GET /api/kiro/accounts/{account_id}`、`GET /api/kiro/accounts/{account_id}/credentials`
- 余额/用量：`GET /api/kiro/accounts/{account_id}/balance`、`GET /api/kiro/accounts/{account_id}/consumption`、`GET /api/kiro/consumption/stats`
- 管理员订阅白名单：`GET /api/kiro/admin/subscription-models`、`PUT /api/kiro/admin/subscription-models`
- AWS IdC（本来就在 Backend，不经 plugin；但 Redis/多实例也要一致）：`POST /api/kiro/aws-idc/device/authorize`、`GET /api/kiro/aws-idc/device/status/{state}`、`POST /api/kiro/aws-idc/import`

#### 11.1.3 Qwen（`/api/qwen/*`）

- OAuth Device Flow：`POST /api/qwen/oauth/authorize`、`GET /api/qwen/oauth/status/{state}`（Redis 不可用需 503）
- 账号：`POST /api/qwen/accounts/import`、`GET /api/qwen/accounts`、`GET /api/qwen/accounts/{account_id}`、`GET /api/qwen/accounts/{account_id}/credentials`、`PUT /api/qwen/accounts/{account_id}/status`、`PUT /api/qwen/accounts/{account_id}/name`、`DELETE /api/qwen/accounts/{account_id}`

#### 11.1.4 OpenAI / Gemini 兼容入口（`/v1/*` 与 `/v1beta/*`）

plugin 下线后，以下路由仍必须工作（并且内部不再走 Node）：

- 渠道选择机制见 **2.4**（API Key 的 `config_type` / JWT 的 `X-Api-Type`）。

- `GET /v1/models`
- `POST /v1/chat/completions`（SSE 必须 no-buffer）
- `POST /v1beta/models/{model}:generateContent`
- `POST /v1beta/models/{model}:streamGenerateContent`

### 11.2 Plugin 历史接口：迁移后对应关系（逐类映射）

> 说明：plugin_public_routes.csv 列的是 plugin 曾经对外/对内暴露的接口；合并后不再部署 Node，因此这些接口要么“迁移到 Backend 的对应路由”，要么“明确下线”。

#### 11.2.1 用户/管理（下线；仅保留 whoami 查询能力）

- `GET /api/user/me`：不再提供（plugin 用户体系淘汰）；能力迁移为：
  - JWT 场景：`GET /api/auth/me`
  - API Key 场景：`GET /api/plugin-api/preference`（whoami；如响应包含 `prefer_shared` 固定为 `0`）
- `PUT /api/users/{user_id}/preference`：不再提供（共享概念移除；Backend 的 `PUT /api/plugin-api/preference` 也应返回 410）
- 其余管理员接口下线：`POST /api/users`、`GET /api/users`、`POST /api/users/{user_id}/regenerate-key`、`PUT /api/users/{user_id}/status`、`DELETE /api/users/{user_id}`

#### 11.2.2 Antigravity OAuth 与账号（迁移到 `/api/plugin-api/*`）

- `POST /api/oauth/authorize` → `POST /api/plugin-api/oauth/authorize`
- `POST /api/oauth/callback/manual` → `POST /api/plugin-api/oauth/callback`
- `GET /api/oauth/callback`：合并后明确不提供（个人部署/个人 API 调用不需要该浏览器直回调页面）

账号相关（逐条 1:1）：

- `POST /api/accounts/import` → `POST /api/plugin-api/accounts/import`
- `GET /api/accounts` → `GET /api/plugin-api/accounts`
- `GET /api/accounts/{cookie_id}` → `GET /api/plugin-api/accounts/{cookie_id}`
- `GET /api/accounts/{cookie_id}/credentials` → `GET /api/plugin-api/accounts/{cookie_id}/credentials`
- `POST /api/accounts/{cookie_id}/refresh` → `POST /api/plugin-api/accounts/{cookie_id}/refresh`
- `GET /api/accounts/{cookie_id}/projects` → `GET /api/plugin-api/accounts/{cookie_id}/projects`
- `PUT /api/accounts/{cookie_id}/project-id` → `PUT /api/plugin-api/accounts/{cookie_id}/project-id`
- `GET /api/accounts/{cookie_id}/detail` → `GET /api/plugin-api/accounts/{cookie_id}/detail`
- `PUT /api/accounts/{cookie_id}/status` → `PUT /api/plugin-api/accounts/{cookie_id}/status`
- `PUT /api/accounts/{cookie_id}/name` → `PUT /api/plugin-api/accounts/{cookie_id}/name`
- `DELETE /api/accounts/{cookie_id}` → `DELETE /api/plugin-api/accounts/{cookie_id}`
- `PUT /api/accounts/{cookie_id}/type` → `PUT /api/plugin-api/accounts/{cookie_id}/type`
- `GET /api/accounts/{cookie_id}/quotas` → `GET /api/plugin-api/accounts/{cookie_id}/quotas`
- `PUT /api/accounts/{cookie_id}/quotas/{model_name}/status` → `PUT /api/plugin-api/accounts/{cookie_id}/quotas/{model_name}/status`

#### 11.2.3 Antigravity 配额相关（部分迁移，部分弃用）

- `GET /api/quotas/user` → `GET /api/plugin-api/quotas/user`（语义调整：不再是 shared pool；改为从 `antigravity_model_quotas` 聚合）
- `GET /api/quotas/shared-pool` → `GET /api/plugin-api/quotas/shared-pool`（弃用：返回 410）
- `GET /api/quotas/consumption` → `GET /api/plugin-api/quotas/consumption`（弃用：返回 410，替代：`/api/usage/requests/*`）
- `GET /api/quotas/low?threshold=...`：合并后不提供（不属于当前 Backend 对外契约）
- `GET /api/quotas/consumption/stats/{model_name}`：合并后不提供（不属于当前 Backend 对外契约）

#### 11.2.4 OpenAI / Gemini 兼容（迁移到 Backend 同名路由）

- `GET /v1/models` → `GET /v1/models`
- `POST /v1/chat/completions` → `POST /v1/chat/completions`
- `POST /v1beta/models/{model}:generateContent` → `POST /v1beta/models/{model}:generateContent`
- `POST /v1beta/models/{model}:streamGenerateContent` → `POST /v1beta/models/{model}:streamGenerateContent`

#### 11.2.5 Kiro（迁移到 Backend 同名前缀 `/api/kiro/*`）

plugin 的 Kiro 路由在 Backend 已有同名入口（见 `BACKEND_PUBLIC_ROUTES.csv`），合并后仅需把内部实现从“proxy plugin”改为“直连上游 + 本地 DB + Redis 状态”。

另外，plugin 还曾暴露 Kiro 专用 OpenAI 兼容入口：

- `GET /v1/kiro/models`
- `POST /v1/kiro/chat/completions`

Backend 当前对外契约不包含这两条；合并后明确不提供（统一收敛到 `GET /v1/models` + `POST /v1/chat/completions` 或 `/api/kiro/*`）。

#### 11.2.6 Qwen（迁移到 Backend 同名前缀 `/api/qwen/*`）

- plugin 的 `/api/qwen/*` 路由在 Backend 已有同名入口（见 `BACKEND_PUBLIC_ROUTES.csv`）
- `GET /api/qwen/admin/accounts`：Backend 当前未暴露该管理接口；合并后默认不提供（如确有需要，另行加管理员路由并写入 CSV）

---

## 12. 参考（仓库内）

### 12.1 Compose/部署

- `docker-compose.yml`
- `docker-compose.core.yml`
- `docker/postgres/init/01-init-plugin-db.sh`
- `.env.example`
- `4-docs/BACKEND_PUBLIC_ROUTES.csv`
- `4-docs/plugin_public_routes.csv`

### 12.2 plugin（迁移源/行为参考）

- `AntiHub-plugin/schema.sql`
- `AntiHub-plugin/src/server/routes.js`
- `AntiHub-plugin/src/server/kiro_routes.js`
- `AntiHub-plugin/src/server/qwen_routes.js`
- `AntiHub-plugin/src/services/*`
- `AntiHub-plugin/src/api/*`

### 12.3 backend（需要改造的调用链）

- `AntiHub-Backend/app/main.py`（路由挂载前缀）
- `AntiHub-Backend/app/api/routes/plugin_api.py`
- `AntiHub-Backend/app/api/routes/v1.py`
- `AntiHub-Backend/app/api/routes/kiro.py`
- `AntiHub-Backend/app/api/routes/qwen.py`
- `AntiHub-Backend/app/api/routes/auth.py`
- `AntiHub-Backend/app/utils/admin_init.py`
- `AntiHub-Backend/app/services/plugin_api_service.py`
- `AntiHub-Backend/app/services/kiro_service.py`
- `AntiHub-Backend/app/utils/encryption.py`
