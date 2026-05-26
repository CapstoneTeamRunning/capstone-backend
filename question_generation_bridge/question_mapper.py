from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from .question_schemas import (
    DEFAULT_DIFFICULTY,
    DEFAULT_EXPLAIN_PASS_K,
    DEFAULT_PROBLEM_PASS_K,
    ModalQuestionRequest,
    ModalQuestionResult,
    OcrQuestionItem,
    QuestionInsertPayload,
)


class QuestionMappingError(ValueError):
    pass


def _as_mapping(value: Any, *, field_name: str) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    raise QuestionMappingError(f"{field_name} must be an object")


def _as_dict(value: Any, *, field_name: str) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, Mapping):
        return dict(value)
    raise QuestionMappingError(f"{field_name} must be an object")


def _coerce_json_object(value: Any, *, field_name: str) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise QuestionMappingError(f"{field_name} is not valid JSON") from exc
    return _as_dict(value, field_name=field_name)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _get(row: Mapping[str, Any], key: str, default: Any = None) -> Any:
    try:
        return row[key]
    except KeyError:
        return default


def extract_first_ocr_item(ocr_result_row: Mapping[str, Any]) -> OcrQuestionItem:
    row = _as_mapping(ocr_result_row, field_name="ocr_result_row")
    ocr_result_id = _get(row, "id", _get(row, "ocr_result_id", ""))
    if ocr_result_id in ("", None):
        raise QuestionMappingError("ocr_result_row missing id")

    ocr_data = _coerce_json_object(_get(row, "ocr_data", row), field_name="ocr_data")
    items = ocr_data.get("items")
    if not isinstance(items, list) or not items:
        raise QuestionMappingError("ocr_data.items must contain at least one item")

    item = _as_dict(items[0], field_name="ocr_data.items[0]")
    category = _as_mapping(item.get("category") or {}, field_name="item.category")
    content = _as_mapping(item.get("content") or {}, field_name="item.content")

    code = _text(item.get("code"))
    passage = _text(content.get("passage"))
    source_text = passage or code
    if not source_text:
        raise QuestionMappingError("OCR item must include content.passage or code")

    return OcrQuestionItem(
        ocr_result_id=ocr_result_id,
        question_no=item.get("number") or "",
        code=code,
        instruction=_text(content.get("instruction")),
        passage=passage,
        source_text=source_text,
        category_code=_text(category.get("code")),
        category_name=_text(category.get("name")),
        raw_item=item,
    )


def build_modal_request(
    ocr_item: OcrQuestionItem,
    *,
    requested_type: str = "",
    difficulty: int | str = DEFAULT_DIFFICULTY,
    seed: int | None = None,
) -> dict[str, Any]:
    payload = ModalQuestionRequest(
        code=ocr_item.code,
        passage=ocr_item.source_text,
        type=_text(requested_type),
        difficulty=difficulty,
        ocr_result_id=ocr_item.ocr_result_id,
        question_no=ocr_item.question_no,
        problem_pass_k=DEFAULT_PROBLEM_PASS_K,
        explain_pass_k=DEFAULT_EXPLAIN_PASS_K,
        instruction=ocr_item.instruction,
        category_code=ocr_item.category_code,
        category_name=ocr_item.category_name,
    ).to_dict()
    if seed is not None:
        payload["seed"] = seed
    return payload


def build_modal_request_from_ocr_row(
    ocr_result_row: Mapping[str, Any],
    *,
    requested_type: str = "",
    difficulty: int | str = DEFAULT_DIFFICULTY,
    seed: int | None = None,
) -> dict[str, Any]:
    return build_modal_request(
        extract_first_ocr_item(ocr_result_row),
        requested_type=requested_type,
        difficulty=difficulty,
        seed=seed,
    )


def normalize_modal_response(response: Mapping[str, Any]) -> ModalQuestionResult:
    data = _as_dict(response, field_name="modal_response")
    choices_value = data.get("choices")
    if not isinstance(choices_value, list) or not choices_value:
        raise QuestionMappingError("modal_response.choices must be a non-empty list")

    choices = [_text(choice) for choice in choices_value]
    if any(not choice for choice in choices):
        raise QuestionMappingError("modal_response.choices contains an empty choice")

    try:
        answer = int(data.get("answer"))
    except (TypeError, ValueError) as exc:
        raise QuestionMappingError("modal_response.answer must be an integer") from exc
    if answer < 1 or answer > len(choices):
        raise QuestionMappingError("modal_response.answer must be 1-based and within choices")

    explanation = _text(data.get("explanation"))
    if not explanation:
        raise QuestionMappingError("modal_response.explanation must not be empty")

    return ModalQuestionResult(
        choices=choices,
        answer=answer,
        explanation=explanation,
        raw_response=data,
    )


def build_question_insert_payload(
    *,
    ocr_item: OcrQuestionItem,
    modal_result: ModalQuestionResult | Mapping[str, Any],
    requested_type: str = "",
    requested_difficulty: int | str = DEFAULT_DIFFICULTY,
    source_question_id: int | str | None = None,
) -> dict[str, Any]:
    result = (
        modal_result
        if isinstance(modal_result, ModalQuestionResult)
        else normalize_modal_response(modal_result)
    )
    clean_requested_type = _text(requested_type)
    question_type = clean_requested_type or ocr_item.category_code or "generated"
    modal_request = build_modal_request(
        ocr_item,
        requested_type=clean_requested_type,
        difficulty=requested_difficulty,
    )
    body_data = {
        "code": ocr_item.code,
        "instruction": ocr_item.instruction,
        "passage": ocr_item.source_text,
        "requested_type": clean_requested_type,
        "difficulty": requested_difficulty,
        "category_code": ocr_item.category_code,
        "category_name": ocr_item.category_name,
        "generated": {
            "choices": result.choices,
            "answer": result.answer,
            "explanation": result.explanation,
        },
    }
    payload = QuestionInsertPayload(
        ocr_result_id=ocr_item.ocr_result_id,
        question_no=ocr_item.question_no,
        type=question_type,
        body_data=body_data,
        choices=result.choices,
        answer=result.answer,
        explanation=result.explanation,
        source_type="generated",
        source_question_id=source_question_id,
        exam_code=ocr_item.code,
        category_code=ocr_item.category_code,
        category_name=ocr_item.category_name,
        instruction=ocr_item.instruction,
        passage=ocr_item.source_text,
        correct_rate=None,
        raw_json={
            "requested": {
                "type": clean_requested_type,
                "difficulty": requested_difficulty,
            },
            "ocr_item": ocr_item.raw_item,
            "modal_request": modal_request,
            "modal_response": result.raw_response,
        },
        external_code=ocr_item.code,
        module=None,
    )
    return payload.to_dict()


def build_question_insert_payload_from_rows(
    *,
    ocr_result_row: Mapping[str, Any],
    modal_response: Mapping[str, Any],
    requested_type: str = "",
    requested_difficulty: int | str = DEFAULT_DIFFICULTY,
    source_question_id: int | str | None = None,
) -> dict[str, Any]:
    return build_question_insert_payload(
        ocr_item=extract_first_ocr_item(ocr_result_row),
        modal_result=normalize_modal_response(modal_response),
        requested_type=requested_type,
        requested_difficulty=requested_difficulty,
        source_question_id=source_question_id,
    )
