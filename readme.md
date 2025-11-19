🚀 Dify.ai 공식 기반 설치 가이드 (Clean Install)
1단계: 작업 디렉토리 생성 및 초기화
먼저, 이전의 설정과 섞이지 않도록 깨끗한 폴더를 하나 만듭니다.

터미널을 엽니다.

새 폴더를 만들고 이동합니다.

Bash

mkdir dify-official
cd dify-official
2단계: 필수 환경 변수 파일 (.env) 작성
Dify는 docker-compose.yml 안에서 모든 설정을 하드코딩하지 않고, .env 파일을 통해 관리하는 것을 원칙으로 합니다.

dify-official 폴더 안에 .env 파일을 생성합니다.

아래 내용을 그대로 복사해서 붙여넣습니다. (공식 기본값입니다.)

Dify 환경 설정
Nov 19, 5:39 PM
3단계: docker-compose.yaml 파일 작성
이 파일은 Dify의 공식 아키텍처를 그대로 따릅니다. Nginx가 포함되어 있어 프론트엔드(web)와 백엔드(api) 간의 통신을 알아서 중재합니다.

같은 폴더에 docker-compose.yaml 파일을 생성합니다.

아래 코드를 붙여넣습니다.

Dify 공식 아키텍처
Nov 19, 5:39 PM

Open
4단계: Nginx 설정 파일 생성 (필수)
공식 아키텍처의 핵심인 nginx 설정을 위해 별도 폴더와 파일이 필요합니다.

dify-official 폴더 안에 nginx 폴더를 만들고, 그 안에 conf.d 폴더를 만듭니다.

Bash

mkdir -p nginx/conf.d
2.  `nginx/conf.d` 폴더 안에 `default.conf` 파일을 만들고 아래 내용을 붙여넣습니다.
(이 설정이 `http://localhost`로 접속했을 때 `/`는 웹으로, `/console/api` 등은 API 서버로 보내주는 역할을 합니다.)


Nginx 라우팅 설정
Nov 19, 5:39 PM

Open
5단계: 실행 및 접속
이제 모든 준비가 완벽하게 끝났습니다.

터미널에서 dify-official 폴더에 있는지 확인합니다.

실행합니다.

Bash

docker compose up -d
3.  약 1~2분 정도 기다린 후(데이터베이스 초기화 시간), 브라우저를 켭니다.
접속 주소:

http://localhost (포트 번호 없음, 기본 80번)

주의: http://localhost:3000으로 접속하지 마십시오. Nginx를 통해야 에러가 없습니다.

이 구성은 Dify 개발팀이 권장하는 프로덕션용 아키텍처와 동일하며, Nginx가 중간에서 통신을 중재하므로 CORS 오류가 발생하지 않습니다.