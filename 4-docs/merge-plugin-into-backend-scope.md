# 合并 AntiHub-plugin → AntiHub-Backend：范围 & 兼容性合同（冻结稿）

> 用途：作为 “merge AntiHub-plugin into AntiHub-Backend” 的执行边界与验收基线（Phase 2–9）。  
> 最后更新：2026-02-15

## 1) 目标（Scope）

- 将 AntiHub-plugin（Node）中 **Antigravity 账号 / 配额 /（必要的）转发能力** 合并到 AntiHub-Backend（FastAPI）。
- 最终运行时：Backend **不再依赖** 独立的 `AntiHub-plugin:8045` 服务。
- 数据层：Backend 采用 **单库（单 Postgres）** 存储；不再维护独立的 plugin DB（详见 Phase 7 compose 收敛）。

## 2) 非目标（Non-goals）

- **不迁移** plugin 历史“消费日志/明细/统计”数据（如有迁移需求必须另开 issue 说明口径与范围）。
- **不再支持** shared-pool（共享池）相关语义与接口（仅保留 user quota 的聚合视图）。
- **不再支持第三方直连** `AntiHub-plugin:8045`（任何能力访问统一通过 Backend 对外入口）。
- 不保证兼容任何 “plugin 私有接口 / 内部调试接口”（不属于 Backend public routes 的不承诺）。

## 3) 兼容性合同（Compatibility Contract）

### 3.1 Public routes（单一事实来源）

- 对外 public routes 的基线：`4-docs/BACKEND_PUBLIC_ROUTES.csv`
- Phase 9 的验收/回归以该 CSV 为准逐条勾选（避免口头约定遗漏）。

### 3.2 `/api/plugin-api/*` 的合同

- **路径与方法保持不变**（除非在本文件明确标注为 410/不提供）。
- 实现从“HTTP 代理到 plugin”切换为“Backend 本地实现”（DB 读写 + 本地逻辑）。
- 对明确废弃的接口：返回 **HTTP 410 Gone**，并提供 **可读提示**（前端可直接展示），如存在替代路径则附带替代路径。

### 3.3 `/v1/chat/completions` 渠道选择与流式转发（Antigravity / Kiro / Qwen）

最小约定（用于对齐前端/调用方与后端实现）：

- **API Key 调用**：根据 API key 绑定的 `config_type` 选择渠道（`antigravity|kiro|qwen`）
- **JWT 调用**：默认 `antigravity`；可用请求头 `X-Api-Type: antigravity|kiro|qwen` 覆盖
- **`stream=true`**：返回 SSE（`text/event-stream`），后端做“透传 + 统一 framing”，并在必要时输出 OpenAI 兼容错误事件
- **`stream=false`**：返回 JSON（OpenAI ChatCompletions）；内部实现允许“走上游 SSE 再收集聚合”为 JSON，以保证错误语义与 usage 统计一致
- **多实例一致性**：OAuth/device flow 的 `state` 必须落 Redis（不得使用进程内 map）；账号凭证落 Backend DB（加密字段）

手工回归（示例，需替换真实密钥/Token/模型）：

```bash
# 1) Qwen（stream=true）
curl -N -X POST http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer <API_KEY_OR_JWT>" \
  -H "Content-Type: application/json" \
  -H "X-Api-Type: qwen" \
  -d '{"model":"qwen-plus","stream":true,"messages":[{"role":"user","content":"hello"}]}'

# 2) Qwen（stream=false）
curl -sS -X POST http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer <API_KEY_OR_JWT>" \
  -H "Content-Type: application/json" \
  -H "X-Api-Type: qwen" \
  -d '{"model":"qwen-plus","stream":false,"messages":[{"role":"user","content":"hello"}]}'

# 3) Antigravity（stream=true）
curl -N -X POST http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer <API_KEY_OR_JWT>" \
  -H "Content-Type: application/json" \
  -H "X-Api-Type: antigravity" \
  -d '{"model":"gemini-2.5-pro","stream":true,"messages":[{"role":"user","content":"hello"}]}'

# 4) Kiro（stream=true）
curl -N -X POST http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer <API_KEY_OR_JWT>" \
  -H "Content-Type: application/json" \
  -H "X-Api-Type: kiro" \
  -d '{"model":"<kiro-model>","stream":true,"messages":[{"role":"user","content":"hello"}]}'
```

## 4) 必保留接口清单（来源：`BACKEND_PUBLIC_ROUTES.csv`）

说明：全量接口请以 `4-docs/BACKEND_PUBLIC_ROUTES.csv` 为准；下表仅列出本次迁移范围内的关键 `/api/plugin-api` 路由，便于会议 review 时聚焦。

| 接口 | 作用 | 结论 | 备注 |
| --- | --- | --- | --- |
| `DELETE /api/plugin-api/accounts/{cookie_id}` | 删除账号 | 保留 | DB 本地实现 |
| `GET /api/plugin-api/accounts` | 获取账号列表 | 保留 | DB 本地实现 |
| `GET /api/plugin-api/accounts/{cookie_id}` | 获取账号信息 | 保留 | DB 本地实现 |
| `GET /api/plugin-api/accounts/{cookie_id}/credentials` | 导出账号凭证 | 保留 | 注意敏感信息脱敏/权限 |
| `GET /api/plugin-api/accounts/{cookie_id}/detail` | 获取账号详情 | 保留 | DB 本地实现 |
| `GET /api/plugin-api/accounts/{cookie_id}/projects` | 获取可选 Project 列表 | 保留 | 可能涉及上游查询 |
| `GET /api/plugin-api/accounts/{cookie_id}/quotas` | 获取账号配额 | 保留 | 读取配额表/聚合 |
| `GET /api/plugin-api/key` | 获取 plug-in API 密钥信息 | 保留（待评估） | 合并后如不再需要可在后续移除并更新 public routes |
| `GET /api/plugin-api/models` | 获取模型列表 | 保留 | 与配额/上游能力对齐 |
| `GET /api/plugin-api/preference` | 获取用户信息和 Cookie 优先级 | 保留 | DB 本地实现 |
| `GET /api/plugin-api/quotas/user` | 获取用户配额池 | 保留 | 语义调整：从 `antigravity_model_quotas` 聚合（不再是 shared pool） |
| `GET /api/plugin-api/quotas/shared-pool` | 获取共享池配额 | 410 | 弃用（见 §5） |
| `GET /api/plugin-api/quotas/consumption` | 获取配额消耗记录 | 410 | 弃用（见 §5） |
| `POST /api/plugin-api/accounts/{cookie_id}/refresh` | 刷新账号 | 保留 | 可能涉及上游刷新流程 |
| `POST /api/plugin-api/accounts/import` | 通过 Refresh Token 导入账号 | 保留 | DB 本地实现 |
| `POST /api/plugin-api/chat/completions` | 聊天补全 | 保留 | OpenAI 兼容；流式策略见 Phase 5 |
| `POST /api/plugin-api/oauth/authorize` | 获取 OAuth 授权 URL | 保留 | OAuth 兼容策略需明确 |
| `POST /api/plugin-api/oauth/callback` | 提交 OAuth 回调 | 保留 | OAuth 兼容策略需明确 |
| `POST /api/plugin-api/v1beta/models/{model}:generateContent` | 图片生成 | 保留 | Gemini v1beta 兼容入口 |
| `PUT /api/plugin-api/accounts/{cookie_id}/name` | 更新账号名称 | 保留 | DB 本地实现 |
| `PUT /api/plugin-api/accounts/{cookie_id}/project-id` | 更新账号 Project ID | 保留 | DB 本地实现 |
| `PUT /api/plugin-api/accounts/{cookie_id}/quotas/{model_name}/status` | 更新模型配额状态 | 保留 | DB 本地实现 |
| `PUT /api/plugin-api/accounts/{cookie_id}/status` | 更新账号状态 | 保留 | DB 本地实现 |
| `PUT /api/plugin-api/accounts/{cookie_id}/type` | 转换账号类型 | 保留 | DB 本地实现 |
| `PUT /api/plugin-api/preference` | 更新 Cookie 优先级 | 410 | prefer_shared 机制弃用（见 §5） |

## 5) 410 / 不提供接口清单（来源：`Report.md` 11.2.3）

> 来源：`Report.md` 的 “#### 11.2.3 Antigravity 配额相关（部分迁移，部分弃用）”

### 5.1 需要返回 410（前端可直接处理）

- `GET /api/plugin-api/quotas/shared-pool`：**返回 410**  
  - 前端处理：展示“共享池配额已弃用”的提示，不再重试该接口
- `GET /api/plugin-api/quotas/consumption`：**返回 410**  
   - 替代路径：`/api/usage/requests/*`  
   - 前端处理：展示提示 +（如需要）引导跳转到替代页面/入口
- `PUT /api/plugin-api/preference`：**返回 410**  
  - 前端处理：不再展示/保存 prefer_shared；如仍调用则提示“已弃用”

**410 响应约定（最小可用）**：

- status：`410`
- body：推荐 `{"error":"<可读提示>","alternative":"</api/usage/requests/* 可选>"}`  
  - 兼容：也可能返回 FastAPI 默认格式 `{"detail":"..."}`（前端需同时兼容 `detail/error/message`）。

### 5.2 合并后不提供（不属于 Backend 对外契约）

- `GET /api/quotas/low?threshold=...`：合并后不提供
- `GET /api/quotas/consumption/stats/{model_name}`：合并后不提供

## 6) 验收清单草稿（Phase 9 可逐条打勾）

- [ ] 已落盘触点表：`4-docs/plugin_touchpoints.csv`（覆盖 repo 内所有 `AntiHub-plugin|:8045|plugin-api|PLUGIN` 命中）
- [ ] Alembic migrations 可升级到 head 且可回滚（至少 `downgrade -1` 再 `upgrade head`）
- [ ] `/api/plugin-api/accounts/*` 与 `/api/plugin-api/quotas/user` 已切换为 Backend 本地 DB 实现（无外部 plugin HTTP 调用链）
- [ ] `/api/plugin-api/quotas/shared-pool` 与 `/api/plugin-api/quotas/consumption` 返回 410，且提示信息可被前端直接展示
- [ ] Compose 已移除 `antihub-plugin` 服务与 plugin DB init（运行时无 `:8045` 依赖）
- [ ] 前端已完成 Analytics 适配与 410 处理，并同步文档
- [ ] 依据 `BACKEND_PUBLIC_ROUTES.csv` 完成一次 public routes smoke/回归并可脚本化

## 7) 相关参考

- `4-docs/BACKEND_PUBLIC_ROUTES.csv`（public routes 基线）
- `4-docs/plugin_touchpoints.csv`（plugin 触点表）
- `Report.md`（特别是 11.2.3）
