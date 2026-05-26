from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Protocol

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .modal_client import ModalClientError, ModalQuestionClient
from .question_mapper import (
    QuestionMappingError,
    build_modal_request,
    build_question_insert_payload,
    extract_first_ocr_item,
    normalize_modal_response,
)
from .question_schemas import DEFAULT_DIFFICULTY


class ModalGenerator(Protocol):
    def generate(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        ...


FetchLatestOcrResult = Callable[[int], Mapping[str, Any] | Awaitable[Mapping[str, Any] | None] | None]
InsertQuestion = Callable[[dict[str, Any]], Mapping[str, Any] | int | Awaitable[Mapping[str, Any] | int]]


class GenerateQuestionRequest(BaseModel):
    type: str
    difficulty: int = Field(default=DEFAULT_DIFFICULTY, ge=1, le=5)
    difficult: int | None = Field(default=None, ge=1, le=5)

    def requested_type(self) -> str:
        return str(self.type or "").strip()

    def resolved_difficulty(self) -> int:
        return self.difficult if self.difficult is not None else self.difficulty


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _question_id(inserted: Mapping[str, Any] | int) -> Any:
    if isinstance(inserted, Mapping):
        return inserted.get("id") or inserted.get("question_id") or ""
    return inserted


def _render_response(
    *,
    insert_payload: Mapping[str, Any],
    inserted: Mapping[str, Any] | int,
) -> dict[str, Any]:
    return {
        "question_id": _question_id(inserted),
        "ocr_result_id": insert_payload.get("ocr_result_id", ""),
        "question_no": insert_payload.get("question_no", ""),
        "type": insert_payload.get("type", ""),
        "body_data": insert_payload.get("body_data") or {},
        "choices": insert_payload.get("choices") or [],
        "answer": insert_payload.get("answer", ""),
        "explanation": insert_payload.get("explanation", ""),
        "source_type": insert_payload.get("source_type", "generated"),
        "source_question_id": insert_payload.get("source_question_id"),
        "exam_code": insert_payload.get("exam_code", ""),
        "category_code": insert_payload.get("category_code", ""),
        "category_name": insert_payload.get("category_name", ""),
        "instruction": insert_payload.get("instruction", ""),
        "passage": insert_payload.get("passage", ""),
        "correct_rate": insert_payload.get("correct_rate"),
        "external_code": insert_payload.get("external_code", ""),
        "module": insert_payload.get("module"),
    }


def create_question_generation_router(
    *,
    fetch_latest_ocr_result: FetchLatestOcrResult,
    insert_question: InsertQuestion,
    modal_client: ModalGenerator | ModalQuestionClient,
) -> APIRouter:
    router = APIRouter(prefix="/documents", tags=["questions"])

    @router.post("/{document_id}/questions/generate")
    async def generate_question_for_document(document_id: int, request: GenerateQuestionRequest) -> dict[str, Any]:
        ocr_result_row = await _maybe_await(fetch_latest_ocr_result(document_id))
        if not ocr_result_row:
            raise HTTPException(
                status_code=404,
                detail="No OCR result found for this document. Run OCR before generating a question.",
            )

        try:
            ocr_item = extract_first_ocr_item(ocr_result_row)
            requested_type = request.requested_type()
            if not requested_type:
                raise QuestionMappingError("type must not be empty")
            requested_difficulty = request.resolved_difficulty()
            modal_request = build_modal_request(
                ocr_item,
                requested_type=requested_type,
                difficulty=requested_difficulty,
            )
            modal_response = await _maybe_await(modal_client.generate(modal_request))
            modal_result = normalize_modal_response(modal_response)
            insert_payload = build_question_insert_payload(
                ocr_item=ocr_item,
                modal_result=modal_result,
                requested_type=requested_type,
                requested_difficulty=requested_difficulty,
            )
        except QuestionMappingError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except ModalClientError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

        inserted = await _maybe_await(insert_question(insert_payload))
        return _render_response(insert_payload=insert_payload, inserted=inserted)

    return router
