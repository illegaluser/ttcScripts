#!/usr/bin/env bash
# ============================================================================
# TTC 4-Pipeline All-in-One — PostgreSQL initdb + DB 스냅샷
# Dify (dify / dify_plugin) + SonarQube (sonar) 3개 DB 생성 후 /opt/seed/pg 에
# 스냅샷을 남긴다. 런타임 entrypoint 가 /data/pg 로 cp -a 하여 사용.
# ============================================================================
set -euo pipefail

PG_VERSION="${PG_VERSION:-15}"
PG_BIN="/usr/lib/postgresql/${PG_VERSION}/bin"
PG_SEED="/opt/seed/pg"
PG_USER="postgres"
PG_PASSWORD="difyai123456"
SONAR_PASSWORD="sonar"

echo "[pg-init] initdb → ${PG_SEED}"
mkdir -p "$PG_SEED"
chown -R ${PG_USER}:${PG_USER} "$PG_SEED"

PWFILE=$(mktemp)
echo "$PG_PASSWORD" > "$PWFILE"
chown ${PG_USER}:${PG_USER} "$PWFILE"

su ${PG_USER} -c "${PG_BIN}/initdb \
    --pgdata=$PG_SEED \
    --username=${PG_USER} \
    --auth=md5 \
    --pwfile=$PWFILE \
    --encoding=UTF8 \
    --no-locale"
rm -f "$PWFILE"

cat >> "$PG_SEED/postgresql.conf" <<CONF
listen_addresses = '127.0.0.1'
port = 5432
unix_socket_directories = '/var/run/postgresql'
max_connections = 200
shared_buffers = 256MB
log_destination = 'stderr'
logging_collector = off
CONF

cat > "$PG_SEED/pg_hba.conf" <<HBA
# All-in-One (통합 이미지): 동일 컨테이너 내부에서만 연결
local   all             all                                     md5
host    all             all             127.0.0.1/32            md5
host    all             all             ::1/128                 md5
HBA

mkdir -p /var/run/postgresql
chown ${PG_USER}:${PG_USER} /var/run/postgresql

su ${PG_USER} -c "${PG_BIN}/pg_ctl -D $PG_SEED -l /tmp/pg-init.log -w -o '-k /var/run/postgresql -h 127.0.0.1' start"

run_sql() {
    su ${PG_USER} -c "PGPASSWORD='${PG_PASSWORD}' ${PG_BIN}/psql -h 127.0.0.1 -U ${PG_USER} -v ON_ERROR_STOP=1 $*"
}

# Dify 본체 + plugin daemon
run_sql -c "CREATE DATABASE dify;"
run_sql -c "CREATE DATABASE dify_plugin;"

# SonarQube 사용자/DB (SonarQube 는 전용 user 를 요구)
run_sql -c "CREATE USER sonar WITH ENCRYPTED PASSWORD '${SONAR_PASSWORD}';"
run_sql -c "CREATE DATABASE sonar OWNER sonar ENCODING 'UTF8';"
run_sql -c "GRANT ALL PRIVILEGES ON DATABASE sonar TO sonar;"

# 검증
for db in dify dify_plugin sonar; do
    run_sql -d "$db" -c "SELECT 1;" >/dev/null \
        || { echo "[pg-init] $db DB 연결 실패" >&2; exit 1; }
done
echo "[pg-init] DB 생성 검증 OK (dify, dify_plugin, sonar)"

su ${PG_USER} -c "${PG_BIN}/pg_ctl -D $PG_SEED -w stop"

echo "[pg-init] snapshot 저장 완료: $PG_SEED"
