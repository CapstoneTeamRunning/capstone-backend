# Capstone Test Backend

FastAPI 기반 캡스톤 앱 테스트용 백엔드입니다.

## 주요 API

- `GET /health`
- `GET /db-test`
- `POST /auth/signup`
- `POST /auth/login`
- `GET /posts`
- `POST /posts`
- `GET /posts/{post_id}`
- `GET /posts/{post_id}/comments`
- `POST /posts/{post_id}/comments`
- `POST /posts/{post_id}/like`
- `POST /documents/upload`
- `POST /documents/{document_id}/ocr`
- `POST /documents/{document_id}/parse`
- `GET /documents/{document_id}/parse/{analysis_run_id}`

## EC2 배포 방식

EC2에는 이미 `capstone-server` 폴더와 `capstone-postgres` 컨테이너가 있다고 가정합니다.

```bash
cd ~/capstone-server
git clone <YOUR_BACKEND_REPO_URL> backend

docker compose up -d --build
```

## 환경 변수

`.env.example`을 참고하세요. EC2에서는 상위 폴더 `~/capstone-server/.env`에 DB 환경 변수를 둡니다.
