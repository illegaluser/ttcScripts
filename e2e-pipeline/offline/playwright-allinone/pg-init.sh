#!/usr/bin/env bash
# ============================================================================
# Zero-Touch QA All-in-One — PostgreSQL initdb + Dify 마이그레이션 스냅샷
# 역할: 빌드 타임에 다음을 수행해 /opt/seed/pg 에 사전 데이터 스냅샷을 만든다.
#   1) initdb (UTF-8, ko_KR.UTF-8 locale)
#   2) postgres 기동 (로컬 소켓)
#   3) dify / dify_plugin DB 생성 + 사용자 비밀번호 설정
#   4) (선택) Dify api 컨테이너 마이그레이션 실행 — 현재는 런타임 수행으로 위임
#   5) postgres 정지 후 데이터 디렉토리를 seed 로 남김
#
# 이 스크립트는 Dockerfile.allinone 의 RUN 단계에서 실행된다.
# 런타임 entrypoint 는 이 스냅샷을 /data/pg 로 cp -a 해 즉시 사용 가능한 PG 를 얻는다.
# ============================================================================
set -euo pipefail

PG_VERSION="${PG_VERSION:-15}"
PG_BIN="/usr/lib/postgresql/${PG_VERSION}/bin"
PG_SEED="/opt/seed/pg"
PG_USER="postgres"
PG_PASSWORD="difyai123456"

echo "[pg-init-allinone] initdb → ${PG_SEED}"
mkdir -p "$PG_SEED"
chown -R ${PG_USER}:${PG_USER} "$PG_SEED"

# initdb (pwfile 로 비밀번호 사전 지정)
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

# 로컬 전용 postgresql.conf + pg_hba.conf 패치
cat >> "$PG_SEED/postgresql.conf" <<CONF
listen_addresses = '127.0.0.1'
port = 5432
unix_socket_directories = '/var/run/postgresql'
max_connections = 100
shared_buffers = 128MB
log_destination = 'stderr'
logging_collector = off
CONF

cat > "$PG_SEED/pg_hba.conf" <<HBA
# All-in-One 모드: 동일 컨테이너 내부에서만 연결
local   all             all                                     md5
host    all             all             127.0.0.1/32            md5
host    all             all             ::1/128                 md5
HBA

mkdir -p /var/run/postgresql
chown ${PG_USER}:${PG_USER} /var/run/postgresql

# postgres 기동 (unix socket + localhost)
su ${PG_USER} -c "${PG_BIN}/pg_ctl -D $PG_SEED -l /tmp/pg-init.log -w -o '-k /var/run/postgresql -h 127.0.0.1' start"

# DB 생성 (Dify 본체 + plugin daemon)
# PoC 2026-04-19: initdb --auth=md5 상태이므로 psql 은 PGPASSWORD 환경변수로 인증해야 한다.
# ('su - -c "PGPASSWORD=... psql ..."' 로 하면 postgres 쉘 env 가 달라서 실패하므로 env 인라인 전달)
su ${PG_USER} -c "PGPASSWORD='${PG_PASSWORD}' ${PG_BIN}/psql -h 127.0.0.1 -U ${PG_USER} -c 'CREATE DATABASE dify;'"
su ${PG_USER} -c "PGPASSWORD='${PG_PASSWORD}' ${PG_BIN}/psql -h 127.0.0.1 -U ${PG_USER} -c 'CREATE DATABASE dify_plugin;'"

# 검증: 두 DB 각각 연결 시도 — 실패 시 빌드 중단
su ${PG_USER} -c "PGPASSWORD='${PG_PASSWORD}' ${PG_BIN}/psql -h 127.0.0.1 -U ${PG_USER} -d dify -c 'SELECT 1;'" >/dev/null \
  || { echo "[pg-init-allinone] dify DB 연결 실패" >&2; exit 1; }
su ${PG_USER} -c "PGPASSWORD='${PG_PASSWORD}' ${PG_BIN}/psql -h 127.0.0.1 -U ${PG_USER} -d dify_plugin -c 'SELECT 1;'" >/dev/null \
  || { echo "[pg-init-allinone] dify_plugin DB 연결 실패" >&2; exit 1; }
echo "[pg-init-allinone] DB 생성 검증 OK (dify, dify_plugin)"

# postgres 정지 (스냅샷 보존)
su ${PG_USER} -c "${PG_BIN}/pg_ctl -D $PG_SEED -w stop"

echo "[pg-init-allinone] snapshot 저장 완료: $PG_SEED"
echo "[pg-init-allinone] 런타임에 entrypoint-allinone.sh 가 /data/pg 로 cp -a."
