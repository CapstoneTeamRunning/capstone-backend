from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


DEFAULT_DIFFICULTY = 3
DEFAULT_PROBLEM_PASS_K = 8
DEFAULT_EXPLAIN_PASS_K = 4


@dataclass(frozen=True)
class OcrQuestionItem:
    ocr_result_id: int | str
    question_no: int | str
    code: str
    instruction: str
    passage: str
    source_text: str
    category_code: str
    category_name: str
    raw_item: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ModalQuestionRequest:
    code: str
    passage: str
    type: str = ""
    difficulty: int | str = DEFAULT_DIFFICULTY
    ocr_result_id: int | str = ""
    question_no: int | str = ""
    source_question_id: int | str = ""
    problem_pass_k: int = DEFAULT_PROBLEM_PASS_K
    explain_pass_k: int = DEFAULT_EXPLAIN_PASS_K
    instruction: str = ""
    category_code: str = ""
    category_name: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ModalQuestionResult:
    choices: list[str]
    answer: int
    explanation: str
    raw_response: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class QuestionInsertPayload:
    ocr_result_id: int | str
    question_no: int | str
    type: str
    body_data: dict[str, Any]
    choices: list[str]
    answer: int
    explanation: str
    source_type: str
    source_question_id: int | str | None
    exam_code: str
    category_code: str
    category_name: str
    instruction: str
    passage: str
    correct_rate: int | None
    raw_json: dict[str, Any]
    external_code: str
    module: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
