import base64
import hashlib
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

import psycopg
from fastapi import BackgroundTasks, FastAPI, File, HTTPException, Query, UploadFile
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
AI_PROVIDER = os.getenv("AI_PROVIDER", "gemini")
AI_MODEL = os.getenv("AI_MODEL", "gemini-2.0-flash")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")

UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", "/app/uploads"))
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


def parse_json_object(raw: str) -> dict:
    text_raw = raw.strip()
    if text_raw.startswith("```"):
        text_raw = re.sub(r"^```[a-zA-Z]*\s*", "", text_raw)
        text_raw = re.sub(r"\s*```$", "", text_raw).strip()
    start = text_raw.find("{")
    end = text_raw.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("AI 응답에서 JSON 객체를 찾지 못했습니다.")
    parsed = json.loads(text_raw[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("AI 응답 JSON 최상위 타입이 object가 아닙니다.")
    return parsed


def build_parse_overlay_prompt(*, code: str, topic: str, full_text: str) -> str:
    return f"""You are a world-class English syntax analysis and translation expert AI.
Your task is to analyze the provided English passage and return a structured JSON object.
Adhere strictly to the JSON format and analysis rules provided below.

### Analysis Rules:
1.  **Overall Structure**: The root object must contain `code`, `topic`, `commentary`, `analysis_data`, and `vocabulary`.
2.  **`topic`**: Read the passage and extract the core topic. You MUST format it exactly as "한글 주제 / English Topic".
3.  **`commentary`**: Provide a one-sentence summary of the entire passage's core message IN KOREAN.
4.  **`sentences`**: Split the passage into individual sentences. For each sentence:
    -   **CRITICAL RULE FOR SPLITTING**: Split the passage into individual sentences strictly based on terminal punctuation (periods `.`, question marks `?`, exclamation marks `!`). NEVER combine two distinct sentences into one.
    -   Assign a sequential `sentence_no`.
    -   Provide a natural, full Korean translation in `full_translation`.
    -   **`is_topic_sentence`**: Always set this value to `false` for now.
    -   Break the entire sentence down into meaningful `chunks`.
5.  **`chunks`**: For each chunk:
    -   **CRITICAL RULE FOR COMPLETENESS**: You MUST analyze every single word of the sentence from the beginning to the very end. DO NOT skip, summarize, or leave out any words, even if the sentence is extremely long.
    -   **CRITICAL RULE FOR CLAUSES**: NEVER group an entire clause (Noun, Adjective, or Adverbial clause) into a single chunk. You MUST split the clause into separate chunks so that its internal Subject (S) and Verb (V) have their own independent chunks.
    -   `chunk_id`: A unique integer index starting from 0 for each chunk in the sentence.
    -   `target_text`: The original English text of the chunk.
    -   `korean_meaning`: A direct, literal Korean translation of the chunk.
    -   `syntax_tag`: Assign one of the following tags: S (Subject), V (Verb), O (Object), C (Complement), M (Modifier).
    -   `box_color`:
        -   Use "red" for the main Subject (S).
        -   Use "blue" for the main Verb (V).
        -   Use "green" for the main Object (O) or Complement (C).
        -   Leave as null for Modifiers (M).
    -   `bracket_open` / `bracket_close`:
        -   Assign `[` to the first chunk of the clause (e.g., the conjunction or relative pronoun).
        -   Assign `]` to the very last chunk of the clause.
        -   DO NOT use brackets for simple phrases or other modifiers.
        -   If multiple clauses end simultaneously, use an array of strings like `["]", "]"]`.
    -   `grammar_note`: If there's a specific grammatical point worth noting, add a brief explanation IN KOREAN ONLY using Korean grammatical terms (e.g., '과거분사', '관계대명사', '부사절'). Do not use English terms like 'Adverbial Clause'.
    -   `modifies_chunk_id`: **STRICTLY** set this to an integer `chunk_id` **ONLY** when the current chunk is an adjective or adjective phrase (`M` tag) that directly modifies a preceding noun or noun phrase. For all other cases (adverbial modifiers, etc.), set it to `null`.
6.  **`vocabulary`**: Extract 3-5 key vocabulary words from the passage and provide their `word` and `meaning` IN KOREAN.
7.  **JSON Formatting Constraints**: Output MUST be perfectly valid JSON.
    - **Escape double quotes** inside string values using backslashes (e.g., \\"word\\"). NEVER use single quotes (') to enclose strings, as it violates JSON standards.
    - **CRITICAL**: You MUST include commas `,` between all elements in arrays (especially between `chunk` objects and `sentence` objects).
    - Do NOT leave trailing commas at the end of arrays or objects.

### Input Data:
-   Passage Code: `{code}`
-   Topic: `{topic}`
-   Passage Text: `{full_text}`

### Output JSON Format (Strictly follow this schema):
{{
  "code": "string",
  "topic": "한글 주제 / English Topic",
  "commentary": "string",
  "analysis_data": {{
    "sentences": [
      {{
        "sentence_no": 1,
        "full_translation": "string",
        "is_topic_sentence": false,
        "chunks": [ ... ]
      }}
    ]
  }},
  "vocabulary": [ {{ "word": "example", "meaning": "예시" }} ]
}}

Now, analyze the provided input data and generate the JSON output.
""".strip()


def validate_overlay_result(parsed: dict) -> dict:
    analysis_data = parsed.get("analysis_data")
    if not isinstance(analysis_data, dict) or not isinstance(analysis_data.get("sentences"), list):
        raise RuntimeError("AI 응답에 analysis_data.sentences가 없습니다.")
    return parsed


def call_gemini_parse_overlay(
    *,
    code: str,
    topic: str,
    full_text: str,
    api_key: str | None = None,
    model: str | None = None,
) -> dict:
    actual_api_key = (api_key or GEMINI_API_KEY).strip()
    actual_model = (model or AI_MODEL).strip() or "gemini-2.0-flash"
    if not actual_api_key:
        raise RuntimeError("GEMINI_API_KEY가 비어 있습니다.")

    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:
        raise RuntimeError("google-genai 패키지가 설치되지 않았습니다.") from exc

    prompt = build_parse_overlay_prompt(code=code, topic=topic, full_text=full_text)
    try:
        client = genai.Client(
            api_key=actual_api_key,
            http_options=types.HttpOptions(timeout=120000),
        )
        response = client.models.generate_content(
            model=actual_model,
            contents=prompt,
            config=types.GenerateContentConfig(response_mime_type="application/json"),
        )
    except Exception as exc:
        raise RuntimeError(f"Gemini 호출 실패: {exc}") from exc

    content = getattr(response, "text", None)
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("Gemini 응답 본문이 비어 있습니다.")
    return validate_overlay_result(parse_json_object(content))


def call_openrouter_parse_overlay(
    *,
    code: str,
    topic: str,
    full_text: str,
    api_key: str | None = None,
    model: str | None = None,
) -> dict:
    actual_api_key = (api_key or OPENROUTER_API_KEY).strip()
    actual_model = (model or AI_MODEL).strip() or "google/gemini-flash-1.5"
    if not actual_api_key:
        raise RuntimeError("OPENROUTER_API_KEY가 비어 있습니다.")

    prompt = build_parse_overlay_prompt(code=code, topic=topic, full_text=full_text)
    payload = {
        "model": actual_model,
        "response_format": {"type": "json_object"},
        "messages": [{"role": "user", "content": prompt}],
    }
    req = urllib.request.Request(
        url="https://openrouter.ai/api/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {actual_api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "http://localhost:8000",
            "X-Title": "Capstone Parse API",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore") if hasattr(exc, "read") else str(exc)
        raise RuntimeError(f"OpenRouter HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"OpenRouter 연결 실패: {exc.reason}") from exc

    try:
        content = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("OpenRouter 응답 구조가 예상과 다릅니다.") from exc
    return validate_overlay_result(parse_json_object(content))


def call_parse_overlay(
    *,
    code: str,
    topic: str,
    full_text: str,
    api_key: str | None = None,
    provider: str | None = None,
    model: str | None = None,
) -> dict:
    actual_provider = (provider or AI_PROVIDER or "gemini").strip().lower()
    if actual_provider == "gemini":
        return call_gemini_parse_overlay(code=code, topic=topic, full_text=full_text, api_key=api_key, model=model)
    if actual_provider == "openrouter":
        return call_openrouter_parse_overlay(code=code, topic=topic, full_text=full_text, api_key=api_key, model=model)
    raise RuntimeError(f"지원하지 않는 AI_PROVIDER입니다: {actual_provider}")


def first_ocr_item(ocr_data) -> dict:
    if isinstance(ocr_data, dict):
        items = ocr_data.get("items")
        if isinstance(items, list) and items and isinstance(items[0], dict):
            return items[0]
        return ocr_data
    return {}


def strip_data_url_prefix(image_base64: str) -> str:
    if "," in image_base64:
        return image_base64.split(",", 1)[1]
    return image_base64


def to_openrouter_image_url(image_base64: str, mime_type: str) -> str:
    candidate = image_base64.strip()
    if candidate.startswith("http://") or candidate.startswith("https://") or candidate.startswith("data:"):
        return candidate
    return f"data:{mime_type};base64,{strip_data_url_prefix(candidate)}"


def map_openrouter_model(model: str) -> str:
    model_map = {
        "gemini-3.1-pro-preview": "google/gemini-3.1-pro-preview",
        "gemini-3-flash-preview": "google/gemini-3-flash-preview",
        "gemini-3.1-flash-lite-preview": "google/gemini-3.1-flash-lite-preview",
        "gemini-2.5-flash": "google/gemini-2.5-flash",
        "gemini-2.5-pro": "google/gemini-2.5-pro",
        "gemini-2.0-flash": "google/gemini-2.0-flash-001",
    }
    return model_map.get(model, model)


def build_ocr_prompt(document_id: int) -> str:
    return f"""[ROLE]
You are an OCR and Korean CSAT English question extraction engine.

[TASK]
Read the uploaded image and extract the most complete visible English reading question into one JSON object.
The output will be stored as an OCR result and later used for syntax analysis, so preserve the English passage faithfully.

[STRICT OUTPUT]
Return ONLY one valid JSON object. Do not use markdown. Do not add explanations.

[JSON SCHEMA]
{{
  "code": "DOC-{document_id}",
  "number": 1,
  "category": {{ "code": "NORMAL", "name": "판단한 문제 유형명" }},
  "content": {{
    "instruction": "question instruction text, or empty string if unavailable",
    "passage": "complete visible English passage text as one string",
    "choices": [
      {{ "index": 1, "text": "choice text" }}
    ]
  }},
  "answer": null
}}

[RULES]
1. `passage` must contain only the extracted passage/body text, not commentary.
2. If the image contains no choices, return an empty `choices` array.
3. If the question number is not visible, set `number` to 1.
4. If the category is unclear, set category.name to "미분류".
5. Preserve symbols such as ①, ②, ③, ④, ⑤, (A), (B), (C), and blanks like ____.
6. Do not invent missing passage text. Extract only visible text.
""".strip()


def validate_ocr_item(parsed: dict, document_id: int) -> dict:
    content = parsed.get("content") if isinstance(parsed.get("content"), dict) else {}
    passage = str(content.get("passage") or "").strip()
    if not passage:
        raise RuntimeError("AI OCR 응답에 content.passage가 없습니다.")

    category = parsed.get("category") if isinstance(parsed.get("category"), dict) else {}
    number = parsed.get("number")
    if not isinstance(number, int):
        number = 1

    choices = content.get("choices")
    if not isinstance(choices, list):
        choices = []

    return {
        "code": str(parsed.get("code") or f"DOC-{document_id}"),
        "number": number,
        "category": {
            "code": str(category.get("code") or "NORMAL"),
            "name": str(category.get("name") or "미분류"),
        },
        "content": {
            "instruction": str(content.get("instruction") or ""),
            "passage": passage,
            "choices": choices,
        },
        "answer": parsed.get("answer"),
    }


def call_gemini_vision_ocr(*, image_base64: str, mime_type: str, prompt: str) -> dict:
    api_key = GEMINI_API_KEY.strip()
    model = AI_MODEL.strip() or "gemini-2.0-flash"
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY가 비어 있습니다.")

    url_model = urllib.parse.quote(model, safe="")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{url_model}:generateContent?key={api_key}"
    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {"text": prompt},
                    {
                        "inline_data": {
                            "mime_type": mime_type,
                            "data": strip_data_url_prefix(image_base64),
                        }
                    },
                ],
            }
        ],
        "generationConfig": {"responseMimeType": "application/json"},
    }
    req = urllib.request.Request(
        url=url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore") if hasattr(exc, "read") else str(exc)
        raise RuntimeError(f"Gemini OCR HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Gemini OCR 연결 실패: {exc.reason}") from exc

    candidates = body.get("candidates") or []
    if not candidates:
        raise RuntimeError("Gemini OCR 응답에 candidates가 없습니다.")
    parts = ((candidates[0].get("content") or {}).get("parts") or [])
    texts = [part.get("text") for part in parts if isinstance(part, dict) and isinstance(part.get("text"), str)]
    text = "\n".join(texts).strip()
    if not text:
        raise RuntimeError("Gemini OCR 응답 본문이 비어 있습니다.")
    return parse_json_object(text)


def call_openrouter_vision_ocr(*, image_base64: str, mime_type: str, prompt: str) -> dict:
    api_key = OPENROUTER_API_KEY.strip()
    model = map_openrouter_model(AI_MODEL.strip() or "google/gemini-2.0-flash-001")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY가 비어 있습니다.")

    payload = {
        "model": model,
        "response_format": {"type": "json_object"},
        "temperature": 0,
        "messages": [
            {"role": "system", "content": prompt},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Extract the question from this image according to the schema."},
                    {"type": "image_url", "image_url": {"url": to_openrouter_image_url(image_base64, mime_type)}},
                ],
            },
        ],
    }
    req = urllib.request.Request(
        url="https://openrouter.ai/api/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "http://localhost:8000",
            "X-Title": "Capstone OCR API",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore") if hasattr(exc, "read") else str(exc)
        raise RuntimeError(f"OpenRouter OCR HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"OpenRouter OCR 연결 실패: {exc.reason}") from exc

    try:
        text = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("OpenRouter OCR 응답 구조가 예상과 다릅니다.") from exc
    if not isinstance(text, str) or not text.strip():
        raise RuntimeError("OpenRouter OCR 응답 본문이 비어 있습니다.")
    return parse_json_object(text)


def extract_ocr_item_from_image(*, image_bytes: bytes, mime_type: str, document_id: int) -> dict:
    image_base64 = base64.b64encode(image_bytes).decode("utf-8")
    prompt = build_ocr_prompt(document_id)
    provider = (AI_PROVIDER or "gemini").strip().lower()
    if provider == "gemini":
        parsed = call_gemini_vision_ocr(image_base64=image_base64, mime_type=mime_type, prompt=prompt)
    elif provider == "openrouter":
        parsed = call_openrouter_vision_ocr(image_base64=image_base64, mime_type=mime_type, prompt=prompt)
    else:
        raise RuntimeError(f"지원하지 않는 AI_PROVIDER입니다: {provider}")
    return validate_ocr_item(parsed, document_id)


def resolve_upload_path(storage_key: str) -> Path:
    filename = Path(storage_key).name
    if not filename:
        raise RuntimeError("문서 storage_key가 비어 있습니다.")
    file_path = UPLOAD_DIR / filename
    if not file_path.exists():
        raise RuntimeError(f"업로드 파일을 찾을 수 없습니다: {storage_key}")
    return file_path


def overlay_sentences(result_json: dict) -> list:
    analysis_data = result_json.get("analysis_data")
    if isinstance(analysis_data, dict) and isinstance(analysis_data.get("sentences"), list):
        return analysis_data["sentences"]
    return []


def save_syntax_analysis(conn, ocr_result_id: int, result_json: dict):
    syntax = conn.execute(
        """
        INSERT INTO syntax_analysis_results (ocr_result_id, code, topic, commentary, raw_json)
        VALUES (%s, %s, %s, %s, %s)
        RETURNING syntax_analysis_id
        """,
        (
            ocr_result_id,
            result_json.get("code"),
            result_json.get("topic"),
            result_json.get("commentary"),
            Jsonb(result_json),
        ),
    ).fetchone()
    syntax_analysis_id = syntax["syntax_analysis_id"]

    for sentence_index, sentence in enumerate(overlay_sentences(result_json), start=1):
        if not isinstance(sentence, dict):
            continue
        sentence_no = sentence.get("sentence_no") or sentence_index
        sentence_row = conn.execute(
            """
            INSERT INTO syntax_analysis_sentences (syntax_analysis_id, sentence_no, full_translation)
            VALUES (%s, %s, %s)
            RETURNING sentence_id
            """,
            (syntax_analysis_id, sentence_no, sentence.get("full_translation")),
        ).fetchone()
        sentence_id = sentence_row["sentence_id"]

        chunks = sentence.get("chunks") if isinstance(sentence.get("chunks"), list) else []
        for chunk_index, chunk in enumerate(chunks):
            if not isinstance(chunk, dict):
                continue
            chunk_order = chunk.get("chunk_id")
            if chunk_order is None:
                chunk_order = chunk.get("chunk_order")
            if chunk_order is None:
                chunk_order = chunk_index
            conn.execute(
                """
                INSERT INTO syntax_analysis_chunks
                (sentence_id, chunk_order, target_text, korean_meaning, syntax_tag, grammar_note)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    sentence_id,
                    chunk_order,
                    str(chunk.get("target_text") or ""),
                    chunk.get("korean_meaning"),
                    chunk.get("syntax_tag"),
                    chunk.get("grammar_note"),
                ),
            )

    vocabulary = result_json.get("vocabulary") if isinstance(result_json.get("vocabulary"), list) else []
    for vocab_index, vocab in enumerate(vocabulary, start=1):
        if not isinstance(vocab, dict) or not vocab.get("word"):
            continue
        conn.execute(
            """
            INSERT INTO syntax_analysis_vocabulary (syntax_analysis_id, vocab_order, word, meaning)
            VALUES (%s, %s, %s, %s)
            """,
            (syntax_analysis_id, vocab_index, vocab.get("word"), vocab.get("meaning")),
        )


def run_parse_job(analysis_run_id: int):
    try:
        with get_conn() as conn:
            run = conn.execute(
                """
                SELECT ar.id, ar.document_id, ar.ocr_result_id, o.ocr_data, o.full_content
                FROM analysis_runs ar
                JOIN ocr_results o ON o.id = ar.ocr_result_id
                WHERE ar.id = %s
                """,
                (analysis_run_id,),
            ).fetchone()
            if run is None:
                return
            conn.execute(
                """
                UPDATE analysis_runs
                SET status = 'running'::analysis_status, started_at = now(), error_message = NULL
                WHERE id = %s
                """,
                (analysis_run_id,),
            )
            conn.commit()

        ocr_item = first_ocr_item(run["ocr_data"])
        content = ocr_item.get("content") if isinstance(ocr_item.get("content"), dict) else {}
        full_text = str(content.get("passage") or run["full_content"] or "").strip()
        if not full_text:
            raise RuntimeError("분석할 OCR 텍스트가 비어 있습니다.")
        code = str(ocr_item.get("code") or f"DOC-{run['document_id']}")
        category = ocr_item.get("category") if isinstance(ocr_item.get("category"), dict) else {}
        topic = str(category.get("name") or "주제 없음")
        summary = str(content.get("instruction") or "구문 분석 결과")

        print(
            f"[parse] calling provider={AI_PROVIDER} model={AI_MODEL} document_id={run['document_id']}",
            flush=True,
        )
        result_json = call_parse_overlay(code=code, topic=topic, full_text=full_text)
        print(f"[parse] provider call succeeded document_id={run['document_id']}", flush=True)

        with get_conn() as conn:
            conn.execute(
                """
                INSERT INTO analysis_results (analysis_run_id, result_json, summary_text)
                VALUES (%s, %s, %s)
                ON CONFLICT (analysis_run_id)
                DO UPDATE SET result_json = EXCLUDED.result_json, summary_text = EXCLUDED.summary_text
                """,
                (analysis_run_id, Jsonb(result_json), summary),
            )
            save_syntax_analysis(conn, run["ocr_result_id"], result_json)
            conn.execute(
                """
                UPDATE analysis_runs
                SET status = 'succeeded'::analysis_status, error_message = NULL, finished_at = now()
                WHERE id = %s
                """,
                (analysis_run_id,),
            )
            conn.commit()
    except Exception as exc:
        err = str(exc)
        print(f"[parse] provider call failed analysis_run_id={analysis_run_id}: {err}", flush=True)
        with get_conn() as conn:
            conn.execute(
                """
                UPDATE analysis_runs
                SET status = 'failed'::analysis_status, error_message = %s, finished_at = now()
                WHERE id = %s
                """,
                (err, analysis_run_id),
            )
            conn.commit()


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


class AnalyzeRequest(BaseModel):
    text: str
    passage_code: str = "TEMP"
    topic_title: str = "임시 주제"
    api_key: str | None = None
    provider: str | None = None
    model: str | None = None


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
    with get_conn() as conn:
        doc = conn.execute(
            "SELECT id, storage_key, mime_type FROM documents WHERE id = %s",
            (document_id,),
        ).fetchone()
        if doc is None:
            raise HTTPException(status_code=404, detail="문서를 찾을 수 없습니다.")

    try:
        file_path = resolve_upload_path(doc["storage_key"])
        image_bytes = file_path.read_bytes()
        mime_type = doc["mime_type"] or "image/png"
        ocr_item = extract_ocr_item_from_image(
            image_bytes=image_bytes,
            mime_type=mime_type,
            document_id=document_id,
        )
    except Exception as exc:
        with get_conn() as conn:
            conn.execute("UPDATE documents SET status = 'ocr_failed' WHERE id = %s", (document_id,))
            conn.commit()
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    full_content = ocr_item["content"]["passage"]
    with get_conn() as conn:
        conn.execute("UPDATE ocr_results SET is_latest = false WHERE document_id = %s", (document_id,))
        previous_attempt = conn.execute(
            """
            SELECT attempt_no
            FROM ocr_results
            WHERE document_id = %s
            ORDER BY attempt_no DESC
            LIMIT 1
            """,
            (document_id,),
        ).fetchone()
        attempt_no = (previous_attempt["attempt_no"] if previous_attempt else 0) + 1
        conn.execute(
            """
            INSERT INTO ocr_results
            (document_id, ocr_data, full_content, engine, status, attempt_no, is_latest, finished_at)
            VALUES (%s, %s, %s, %s, 'succeeded', %s, true, now())
            RETURNING id
            """,
            (
                document_id,
                Jsonb({"items": [ocr_item]}),
                full_content,
                f"{AI_PROVIDER}-vision-ocr",
                attempt_no,
            ),
        ).fetchone()
        conn.execute("UPDATE documents SET status = 'ocr_succeeded' WHERE id = %s", (document_id,))
        conn.commit()
    return {"document_id": document_id, "status": "succeeded", "mock_item": ocr_item}


@app.post("/documents/{document_id}/parse")
def run_parse_mock(document_id: int, background_tasks: BackgroundTasks):
    with get_conn() as conn:
        ocr = conn.execute(
            """
            SELECT id
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
            (document_id, ocr_result_id, status, model_name, model_version, prompt_version)
            VALUES (%s, %s, 'queued', %s, %s, 'react-overlay-v1')
            RETURNING id
            """,
            (document_id, ocr["id"], AI_MODEL, AI_PROVIDER),
        ).fetchone()
        conn.commit()
    background_tasks.add_task(run_parse_job, analysis_run["id"])
    return {"document_id": document_id, "analysis_run_id": analysis_run["id"], "status": "queued"}


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


@app.post("/documents/analyze")
def analyze_passage_directly(payload: AnalyzeRequest):
    try:
        return call_parse_overlay(
            code=payload.passage_code,
            topic=payload.topic_title,
            full_text=payload.text,
            api_key=payload.api_key,
            provider=payload.provider,
            model=payload.model,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
