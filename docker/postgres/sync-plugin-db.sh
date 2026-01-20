#!/bin/sh
# 同步/初始化 plugin 数据库与用户（可重复执行）
#
# 背景：PostgreSQL 官方镜像的 /docker-entrypoint-initdb.d 只在“首次初始化数据目录”时执行；
# 当你重写 .env（例如生成新密码）但复用旧数据卷时，会出现密码不一致导致 plugin/backend 连接失败。
#
# 该脚本会：
# - 确保 POSTGRES_USER 的密码与 .env 一致
# - 创建/更新 plugin 用户，并设置密码
# - 创建/更新 plugin 数据库归属与授权
# - 在 plugin 库里补齐 public schema 权限与 uuid-ossp 扩展

set -e

strip_cr() {
    printf '%s' "$1" | tr -d '\r'
}

POSTGRES_USER=$(strip_cr "${POSTGRES_USER:-antihub}")
POSTGRES_PASSWORD=$(strip_cr "${POSTGRES_PASSWORD:-please-change-me}")
POSTGRES_DB=$(strip_cr "${POSTGRES_DB:-antihub}")
PLUGIN_DB_NAME=$(strip_cr "${PLUGIN_DB_NAME:-antigravity}")
PLUGIN_DB_USER=$(strip_cr "${PLUGIN_DB_USER:-antigravity}")
PLUGIN_DB_PASSWORD=$(strip_cr "${PLUGIN_DB_PASSWORD:-please-change-me}")

export PGPASSWORD="$POSTGRES_PASSWORD"

echo "[db-init] sync postgres/plugin users & db..."

psql -h postgres -U "$POSTGRES_USER" -d postgres -X -v ON_ERROR_STOP=1 \
    -v su_user="$POSTGRES_USER" -v su_pass="$POSTGRES_PASSWORD" \
    -v main_db="$POSTGRES_DB" \
    -v plugin_db="$PLUGIN_DB_NAME" -v plugin_user="$PLUGIN_DB_USER" -v plugin_pass="$PLUGIN_DB_PASSWORD" <<'EOSQL'
SELECT format('ALTER USER %I WITH PASSWORD %L', :'su_user', :'su_pass') \gexec

SELECT CASE
    WHEN EXISTS (SELECT 1 FROM pg_database WHERE datname = :'main_db') THEN
        format('ALTER DATABASE %I OWNER TO %I', :'main_db', :'su_user')
    ELSE
        format('CREATE DATABASE %I OWNER %I', :'main_db', :'su_user')
END \gexec

SELECT CASE
    WHEN EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'plugin_user') THEN
        format('ALTER USER %I WITH PASSWORD %L', :'plugin_user', :'plugin_pass')
    ELSE
        format('CREATE USER %I WITH PASSWORD %L', :'plugin_user', :'plugin_pass')
END \gexec

SELECT CASE
    WHEN EXISTS (SELECT 1 FROM pg_database WHERE datname = :'plugin_db') THEN
        format('ALTER DATABASE %I OWNER TO %I', :'plugin_db', :'plugin_user')
    ELSE
        format('CREATE DATABASE %I OWNER %I', :'plugin_db', :'plugin_user')
END \gexec

SELECT format('GRANT ALL PRIVILEGES ON DATABASE %I TO %I', :'plugin_db', :'plugin_user') \gexec
EOSQL

psql -h postgres -U "$POSTGRES_USER" -d "$PLUGIN_DB_NAME" -X -v ON_ERROR_STOP=1 \
    -v plugin_user="$PLUGIN_DB_USER" <<'EOSQL'
SELECT format('GRANT ALL ON SCHEMA public TO %I', :'plugin_user') \gexec
SELECT format('ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO %I', :'plugin_user') \gexec
SELECT format('ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO %I', :'plugin_user') \gexec

CREATE EXTENSION IF NOT EXISTS "uuid-ossp" WITH SCHEMA public;
EOSQL

echo "[db-init] done."
