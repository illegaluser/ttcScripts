-- Dify plugin_daemon 용 별도 데이터베이스 생성
-- /docker-entrypoint-initdb.d 에 마운트되어 postgres 최초 기동 시 1회 실행된다.
-- plugin_daemon 은 이 DB 에 자체 스키마를 만들어 플러그인 메타데이터를 저장한다.
CREATE DATABASE dify_plugin;
