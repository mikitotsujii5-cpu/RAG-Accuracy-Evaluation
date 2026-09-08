"""MLflow ResponsesAgent compatibility layer for the Databricks App RAG core.

This module intentionally does not call ``mlflow.models.set_model`` or log/register a
model. A deployment owner can inject server-resolved resources and choose a separate
Model Serving lifecycle later without duplicating the application's RAG logic.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from mlflow.pyfunc import ResponsesAgent
from mlflow.types.responses import ResponsesAgentRequest, ResponsesAgentResponse

from rag import RagOrchestrator, set_rag_trace_context


@dataclass(frozen=True, slots=True)
class ResponsesAgentRuntime:
    """Server-side resources used by :class:`RagResponsesAgent`.

    ``index_name`` and ``model_target`` must be resolved by trusted application code;
    they are constructor inputs rather than values accepted from a Responses API request.
    """

    project_id: str
    variant_id: str
    index_name: str
    model_target: str
    retrieval_config: dict[str, Any]

    def __post_init__(self) -> None:
        for name in ("project_id", "variant_id", "index_name", "model_target"):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"{name} must not be empty")
        required = {
            "query_type",
            "metadata_filtering",
            "reranking",
            "query_optimization",
        }
        if missing := sorted(required - self.retrieval_config.keys()):
            raise ValueError("retrieval_config is missing: " + ", ".join(missing))
        normalized = {
            **self.retrieval_config,
            "phase_id": str(self.retrieval_config.get("phase_id") or "custom"),
            "variant_id": self.variant_id,
        }
        object.__setattr__(self, "retrieval_config", normalized)


class RagResponsesAgent(ResponsesAgent):
    """Thin ResponsesAgent adapter that reuses retrieval and generation from the App."""

    def __init__(self, orchestrator: RagOrchestrator, runtime: ResponsesAgentRuntime) -> None:
        self.orchestrator = orchestrator
        self.runtime = runtime

    def predict(self, request: ResponsesAgentRequest) -> ResponsesAgentResponse:
        question = _latest_user_text(request)
        config = dict(self.runtime.retrieval_config)
        phase_id = str(config["phase_id"])
        set_rag_trace_context(
            project_id=self.runtime.project_id,
            phase_id=phase_id,
            variant_id=self.runtime.variant_id,
        )
        bundle = self.orchestrator.retrieve(
            project_id=self.runtime.project_id,
            index_name=self.runtime.index_name,
            question=question,
            config=config,
            model_target=self.runtime.model_target,
        )
        answer, usage, citations = self.orchestrator.generate_answer(
            question=question,
            bundle=bundle,
            model_target=self.runtime.model_target,
        )
        output_id = f"msg_{uuid.uuid4().hex}"
        public_citations = [
            {
                **citation,
                "href": _pdf_href(self.runtime.project_id, citation),
            }
            for citation in citations
            if citation.get("document_id")
        ]
        return ResponsesAgentResponse(
            output=[self.create_text_output_item(text=answer, id=output_id)],
            custom_outputs={
                "citations": public_citations,
                "retrieval": {
                    "result_count": len(bundle.rows),
                    "expanded_queries": bundle.expanded_queries,
                    "filters": bundle.filters,
                    "elapsed_ms": round(bundle.elapsed_ms, 1),
                    "warnings": bundle.warnings,
                },
                "usage": usage,
                "trace_dimensions": {
                    "project_id": self.runtime.project_id,
                    "phase_id": phase_id,
                    "variant_id": self.runtime.variant_id,
                },
            },
        )


def _pdf_href(project_id: str, citation: dict[str, Any]) -> str:
    href = (
        f"/api/projects/{project_id}/documents/"
        f"{citation['document_id']}/content"
    )
    pages = citation.get("page_numbers") or []
    if pages:
        href += f"#page={int(pages[0])}"
    return href


def _latest_user_text(request: ResponsesAgentRequest) -> str:
    for item in reversed(request.input):
        value = item.model_dump(exclude_none=True)
        if value.get("role") != "user":
            continue
        content = value.get("content")
        if isinstance(content, str) and content.strip():
            return _validate_question(content)
        if isinstance(content, list):
            parts = [
                str(part["text"]).strip()
                for part in content
                if isinstance(part, dict) and isinstance(part.get("text"), str)
            ]
            if text := "\n".join(part for part in parts if part):
                return _validate_question(text)
    raise ValueError("ResponsesAgentRequest.inputにテキストのuserメッセージが必要です。")


def _validate_question(question: str) -> str:
    question = question.strip()
    if len(question) > 8000:
        raise ValueError("質問は8,000文字以下にしてください。")
    return question
