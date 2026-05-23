import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

import psycopg
from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

DB_HOST = os.getenv("DB_HOST", "postgres")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "capstone")
DB_USER = os.getenv("DB_USER", "capstone_user")
DB_PASSWORD = os.getenv("DB_PASSWORD", "capstone_password_1234")

UPLOAD_DIR = Path("/app/uploads")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Capstone Test Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/uploads", StaticFiles(directory=str(UPLOAD_DIR)), name="uploads")


def get_conn():
    return psycopg.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
        row_factory=dict_row,
    )


def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def hours_ago(dt: datetime | None) -> int:
    if dt is None:
        return 0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    diff = datetime.now(timezone.utc) - dt
    return max(0, int(diff.total_seconds() // 3600))


def time_ago_text(dt: datetime | None) -> str:
    h = hours_ago(dt)
    if h < 1:
        return "방금 전"
    if h < 24:
        return f"{h}시간 전"
    return f"{h // 24}일 전"


def format_authored_at(dt: datetime | None) -> str:
    if dt is None:
        return ""
    return dt.strftime("%Y.%m.%d.%H:%M")


def ensure_demo_user(conn) -> int:
    row = conn.execute(
        """
        INSERT INTO users (username, email, password_hash)
        VALUES (%s, %s, %s)
        ON CONFLICT (email) DO UPDATE SET email = EXCLUDED.email
        RETURNING id
        """,
        ("demo", "demo@example.com", hash_password("demo1234")),
    ).fetchone()
    return row["id"]


def post_list_dto(row):
    created_at = row.get("created_at")
    content = row.get("content") or ""
    return {
        "id": row["id"],
        "category": row["category"],
        "title": row["title"],
        "summary": content[:80],
        "hours_ago": hours_ago(created_at),
        "time_ago": time_ago_text(created_at),
        "views": row["view_count"],
        "likes": row["like_count"],
        "comments": row["comment_count"],
        "author": row["author"],
        "authored_at": format_authored_at(created_at),
    }


def post_detail_dto(row, is_liked=False):
    created_at = row.get("created_at")
    content = row.get("content") or ""
    return {
        "id": row["id"],
        "category": row["category"],
        "title": row["title"],
        "summary": content[:80],
        "body": content,
        "hours_ago": hours_ago(created_at),
        "time_ago": time_ago_text(created_at),
        "views": row["view_count"],
        "likes": row["like_count"],
        "comments": row["comment_count"],
        "author": row["author"],
        "authored_at": format_authored_at(created_at),
        "is_liked": is_liked,
    }


def comment_dto(row):
    created_at = row.get("created_at")
    return {
        "id": row["id"],
        "post_id": row["post_id"],
        "author_id": row["author_id"],
        "author": row["author"],
        "content": row["content"],
        "hours_ago": hours_ago(created_at),
        "time_ago": time_ago_text(created_at),
        "authored_at": format_authored_at(created_at),
    }


class SignupRequest(BaseModel):
    username: str
    email: str
    password: str


class LoginRequest(BaseModel):
    email: str
    password: str


class LikeToggleRequest(BaseModel):
    user_id: int = Field(alias="user_id")


class CommentCreateRequest(BaseModel):
    author_id: int = Field(alias="author_id")
    content: str


class PostCreateRequest(BaseModel):
    author_id: int = Field(alias="author_id")
    category: str
    title: str
    content: str


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/db-test")
def db_test():
    with get_conn() as conn:
        row = conn.execute("SELECT now() AS now").fetchone()
    return {"db": "connected", "now": str(row["now"])}


@app.post("/auth/signup")
def signup(payload: SignupRequest):
    try:
        with get_conn() as conn:
            row = conn.execute(
                """
                INSERT INTO users (username, email, password_hash)
                VALUES (%s, %s, %s)
                RETURNING id, username, email
                """,
                (payload.username, payload.email, hash_password(payload.password)),
            ).fetchone()
            conn.commit()
        return {"message": "회원가입 성공", "user": row}
    except psycopg.errors.UniqueViolation:
        raise HTTPException(status_code=409, detail="이미 존재하는 사용자 이름 또는 이메일입니다.")


@app.post("/auth/login")
def login(payload: LoginRequest):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, username, email, password_hash FROM users WHERE email = %s",
            (payload.email,),
        ).fetchone()
    if row is None or row["password_hash"] != hash_password(payload.password):
        raise HTTPException(status_code=401, detail="이메일 또는 비밀번호가 올바르지 않습니다.")
    return {"message": "로그인 성공", "user": {"id": row["id"], "username": row["username"], "email": row["email"]}}


@app.get("/posts")
def get_posts(category: str | None = Query(default=None)):
    with get_conn() as conn:
        if category:
            rows = conn.execute(
                """
                SELECT p.*, u.username AS author
                FROM posts p
                JOIN users u ON u.id = p.author_id
                WHERE p.deleted_at IS NULL AND p.category = %s
                ORDER BY p.created_at DESC
                """,
                (category,),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT p.*, u.username AS author
                FROM posts p
                JOIN users u ON u.id = p.author_id
                WHERE p.deleted_at IS NULL
                ORDER BY p.created_at DESC
                """
            ).fetchall()
    return [post_list_dto(row) for row in rows]


@app.post("/posts")
def create_post(payload: PostCreateRequest):
    with get_conn() as conn:
        row = conn.execute(
            """
            INSERT INTO posts (author_id, category, title, content)
            VALUES (%s, %s, %s, %s)
            RETURNING *
            """,
            (payload.author_id, payload.category, payload.title, payload.content),
        ).fetchone()
        author = conn.execute("SELECT username FROM users WHERE id = %s", (payload.author_id,)).fetchone()
        conn.commit()
    row["author"] = author["username"] if author else "unknown"
    return post_detail_dto(row, is_liked=False)


@app.get("/posts/{post_id}")
def get_post_detail(post_id: int, user_id: int = Query(default=1)):
    with get_conn() as conn:
        conn.execute("UPDATE posts SET view_count = view_count + 1 WHERE id = %s", (post_id,))
        row = conn.execute(
            """
            SELECT p.*, u.username AS author
            FROM posts p
            JOIN users u ON u.id = p.author_id
            WHERE p.id = %s AND p.deleted_at IS NULL
            """,
            (post_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="게시글을 찾을 수 없습니다.")
        liked = conn.execute(
            "SELECT 1 FROM post_likes WHERE post_id = %s AND user_id = %s",
            (post_id, user_id),
        ).fetchone() is not None
        conn.commit()
    return post_detail_dto(row, is_liked=liked)


@app.get("/posts/{post_id}/comments")
def get_comments(post_id: int):
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT c.*, u.username AS author
            FROM comments c
            JOIN users u ON u.id = c.author_id
            WHERE c.post_id = %s AND c.deleted_at IS NULL
            ORDER BY c.created_at ASC
            """,
            (post_id,),
        ).fetchall()
    return [comment_dto(row) for row in rows]


@app.post("/posts/{post_id}/comments")
def create_comment(post_id: int, payload: CommentCreateRequest):
    with get_conn() as conn:
        row = conn.execute(
            """
            INSERT INTO comments (post_id, author_id, content)
            VALUES (%s, %s, %s)
            RETURNING *
            """,
            (post_id, payload.author_id, payload.content),
        ).fetchone()
        conn.execute("UPDATE posts SET comment_count = comment_count + 1 WHERE id = %s", (post_id,))
        author = conn.execute("SELECT username FROM users WHERE id = %s", (payload.author_id,)).fetchone()
        conn.commit()
    row["author"] = author["username"] if author else "unknown"
    return comment_dto(row)


@app.post("/posts/{post_id}/like")
def toggle_like(post_id: int, payload: LikeToggleRequest):
    with get_conn() as conn:
        exists = conn.execute(
            "SELECT 1 FROM post_likes WHERE post_id = %s AND user_id = %s",
            (post_id, payload.user_id),
        ).fetchone()
        if exists:
            conn.execute("DELETE FROM post_likes WHERE post_id = %s AND user_id = %s", (post_id, payload.user_id))
            conn.execute("UPDATE posts SET like_count = GREATEST(like_count - 1, 0) WHERE id = %s", (post_id,))
            liked = False
        else:
            conn.execute("INSERT INTO post_likes (post_id, user_id) VALUES (%s, %s)", (post_id, payload.user_id))
            conn.execute("UPDATE posts SET like_count = like_count + 1 WHERE id = %s", (post_id,))
            liked = True
        like_count = conn.execute("SELECT like_count FROM posts WHERE id = %s", (post_id,)).fetchone()["like_count"]
        conn.commit()
    return {"post_id": post_id, "liked": liked, "like_count": like_count}


@app.post("/documents/upload")
async def upload_document(image: UploadFile = File(...)):
    content = await image.read()
    safe_name = image.filename or "upload.bin"
    filename = f"{uuid.uuid4().hex}_{safe_name}"
    file_path = UPLOAD_DIR / filename
    file_path.write_bytes(content)

    with get_conn() as conn:
        demo_user_id = ensure_demo_user(conn)
        row = conn.execute(
            """
            INSERT INTO documents (user_id, title, storage_key, mime_type, file_size, status)
            VALUES (%s, %s, %s, %s, %s, 'uploaded')
            RETURNING id
            """,
            (demo_user_id, safe_name, f"uploads/{filename}", image.content_type or "application/octet-stream", len(content)),
        ).fetchone()
        conn.commit()
    return {"document_id": row["id"], "image_url": f"/uploads/{filename}"}


@app.post("/documents/{document_id}/ocr")
def run_ocr_mock(document_id: int):
    mock_item = {
        "code": "MOCK-2026-001",
        "number": 1,
        "category": {"code": "NORMAL", "name": "주제/요지"},
        "content": {
            "instruction": "다음 글의 주제로 가장 적절한 것을 고르시오.",
            "passage": "This is a mock OCR passage for the capstone application test. It is saved into PostgreSQL and then used for AI analysis.",
        },
    }
    with get_conn() as conn:
        doc = conn.execute("SELECT id FROM documents WHERE id = %s", (document_id,)).fetchone()
        if doc is None:
            raise HTTPException(status_code=404, detail="문서를 찾을 수 없습니다.")
        conn.execute("UPDATE ocr_results SET is_latest = false WHERE document_id = %s", (document_id,))
        conn.execute(
            """
            INSERT INTO ocr_results
            (document_id, ocr_data, full_content, engine, status, attempt_no, is_latest, finished_at)
            VALUES (%s, %s, %s, %s, 'succeeded', 1, true, now())
            RETURNING id
            """,
            (document_id, Jsonb({"items": [mock_item]}), mock_item["content"]["passage"], "mock-ocr"),
        ).fetchone()
        conn.execute("UPDATE documents SET status = 'ocr_succeeded' WHERE id = %s", (document_id,))
        conn.commit()
    return {"document_id": document_id, "status": "succeeded", "mock_item": mock_item}


@app.post("/documents/{document_id}/parse")
def run_parse_mock(document_id: int):
    with get_conn() as conn:
        ocr = conn.execute(
            """
            SELECT id, full_content
            FROM ocr_results
            WHERE document_id = %s AND is_latest = true
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (document_id,),
        ).fetchone()
        if ocr is None:
            raise HTTPException(status_code=404, detail="OCR 결과가 없습니다. 먼저 /ocr를 실행하세요.")

        analysis_run = conn.execute(
            """
            INSERT INTO analysis_runs
            (document_id, ocr_result_id, status, model_name, model_version, prompt_version, started_at, finished_at)
            VALUES (%s, %s, 'succeeded', 'mock-ai', 'v1', 'test', now(), now())
            RETURNING id
            """,
            (document_id, ocr["id"]),
        ).fetchone()

        result_json = {
            "code": "MOCK-2026-001",
            "topic": "Mock analysis result",
            "commentary": "이 결과는 EC2 백엔드와 PostgreSQL 연동 테스트를 위한 Mock 분석 결과입니다.",
            "passage": ocr["full_content"],
            "analysis_data": [
                {
                    "sentence_no": 1,
                    "sentence": "This is a mock OCR passage for the capstone application test.",
                    "full_translation": "이것은 캡스톤 애플리케이션 테스트를 위한 Mock OCR 지문입니다.",
                    "chunks": [
                        {
                            "chunk_order": 1,
                            "target_text": "This is a mock OCR passage",
                            "korean_meaning": "이것은 Mock OCR 지문이다",
                            "syntax_tag": "SVC",
                            "grammar_note": "This가 주어, is가 동사 역할을 합니다.",
                        }
                    ],
                }
            ],
            "vocabulary": [
                {"vocab_order": 1, "word": "mock", "meaning": "모의의, 가짜의"},
                {"vocab_order": 2, "word": "passage", "meaning": "지문"},
            ],
            "generated_questions": [
                {"question_no": 1, "type": "topic", "question": "What is the main purpose of this passage?", "answer": 1}
            ],
        }

        conn.execute(
            "INSERT INTO analysis_results (analysis_run_id, result_json, summary_text) VALUES (%s, %s, %s)",
            (analysis_run["id"], Jsonb(result_json), "Mock AI 분석 완료"),
        )
        syntax = conn.execute(
            """
            INSERT INTO syntax_analysis_results (ocr_result_id, code, topic, commentary, raw_json)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING syntax_analysis_id
            """,
            (ocr["id"], result_json["code"], result_json["topic"], result_json["commentary"], Jsonb(result_json)),
        ).fetchone()
        sentence = conn.execute(
            """
            INSERT INTO syntax_analysis_sentences (syntax_analysis_id, sentence_no, full_translation)
            VALUES (%s, 1, %s)
            RETURNING sentence_id
            """,
            (syntax["syntax_analysis_id"], "이것은 캡스톤 애플리케이션 테스트를 위한 Mock OCR 지문입니다."),
        ).fetchone()
        conn.execute(
            """
            INSERT INTO syntax_analysis_chunks (sentence_id, chunk_order, target_text, korean_meaning, syntax_tag, grammar_note)
            VALUES (%s, 1, %s, %s, %s, %s)
            """,
            (sentence["sentence_id"], "This is a mock OCR passage", "이것은 Mock OCR 지문이다", "SVC", "This가 주어, is가 동사 역할을 합니다."),
        )
        conn.execute(
            """
            INSERT INTO syntax_analysis_vocabulary (syntax_analysis_id, vocab_order, word, meaning)
            VALUES (%s, 1, 'mock', '모의의, 가짜의'), (%s, 2, 'passage', '지문')
            """,
            (syntax["syntax_analysis_id"], syntax["syntax_analysis_id"]),
        )
        conn.commit()
    return {"document_id": document_id, "analysis_run_id": analysis_run["id"], "status": "succeeded"}


@app.get("/documents/{document_id}/parse/{analysis_run_id}")
def get_parse_status(document_id: int, analysis_run_id: int):
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT ar.document_id, ar.id AS analysis_run_id, ar.status, ar.error_message, res.result_json
            FROM analysis_runs ar
            LEFT JOIN analysis_results res ON res.analysis_run_id = ar.id
            WHERE ar.document_id = %s AND ar.id = %s
            """,
            (document_id, analysis_run_id),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="분석 실행 결과를 찾을 수 없습니다.")
    return {
        "document_id": row["document_id"],
        "analysis_run_id": row["analysis_run_id"],
        "status": row["status"],
        "result_json": row["result_json"],
        "error_message": row["error_message"],
    }
