"""FastAPI entry point for the RAG accuracy evaluation Databricks App."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import logging
import re
import unicodedata
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import quote

from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    File,
    Form,
    Header,
    Request,
    UploadFile,
    status,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from console_links import build_console_links
from errors import AppError, ResourceNotReadyError
from gateway import DatabricksGateway
from rag import CancellationRegistry, RagOrchestrator
from repository import Repository
from schemas import (
    ChatRequest,
    DocumentMetadata,
    EvaluationCaseCreate,
    EvaluationRequest,
    PHASE_PRESETS,
    PreparationRequest,
    ProjectCreate,
    SessionCreate,
    parse_document_metadata_json,
    resolve_retrieval_config,
)
from settings import Settings


LOGGER = logging.getLogger("rag_accuracy_app")
BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
MAX_PDF_BYTES = 100 * 1024 * 1024
APP_VERSION = "1.5.0"

Authenticator = Callable[[Request], str | Awaitable[str]]


def create_app(
    settings: Settings | None = None,
    gateway: DatabricksGateway | None = None,
    authenticator: Authenticator | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()
    gateway = gateway or DatabricksGateway(settings)
    repository = Repository(settings, gateway)
    cancellations = CancellationRegistry()

    api = FastAPI(
        title="RAG精度評価アプリ",
        version=APP_VERSION,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )
    api.state.settings = settings
    api.state.gateway = gateway
    api.state.repository = repository
    api.state.cancellations = cancellations
    api.state.rag = RagOrchestrator(repository, cancellations)
    api.state.authenticator = authenticator
    api.mount("/assets", StaticFiles(directory=STATIC_DIR), name="assets")

    @api.exception_handler(AppError)
    async def handle_app_error(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "retryable": exc.retryable,
                    **exc.details,
                }
            },
        )

    @api.exception_handler(RequestValidationError)
    async def handle_validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        fields = [".".join(str(part) for part in item["loc"] if part != "body") for item in exc.errors()]
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": "入力内容を確認してください。",
                    "retryable": False,
                    "fields": fields,
                }
            },
        )

    @api.exception_handler(Exception)
    async def handle_unexpected_error(_: Request, exc: Exception) -> JSONResponse:
        LOGGER.exception("Unhandled application error", exc_info=exc)
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": "予期しないエラーが発生しました。再実行しても解消しない場合はAppログを確認してください。",
                    "retryable": True,
                }
            },
        )

    @api.get("/api/health")
    async def health(request: Request) -> dict[str, Any]:
        current = request.app.state.settings
        return {
            "status": "ok" if current.ready else "configuration_required",
            "title": "RAG精度評価アプリ",
            "version": APP_VERSION,
            "databricks_ready": current.ready,
            "missing": current.missing_required,
            "optional": {
                "preparation_job": bool(current.prep_job_id),
                "evaluation_job": bool(current.eval_job_id),
            },
        }

    @api.get("/api/phases")
    async def phases() -> dict[str, Any]:
        return {"items": [{"phase_id": key, **value} for key, value in PHASE_PRESETS.items()]}

    @api.get("/api/me")
    async def current_user(
        request: Request,
        principal: str = Depends(current_principal),
    ) -> dict[str, Any]:
        # These headers are injected by the authenticated Databricks Apps
        # ingress. The verified Current User API result remains the fallback;
        # no forwarded access token is retained in App state.
        verified = getattr(request.state, "current_user", {})
        email = (
            request.headers.get("x-forwarded-email")
            or verified.get("email")
            or (principal if "@" in principal else None)
        )
        display_name = (
            request.headers.get("x-forwarded-preferred-username")
            or request.headers.get("x-forwarded-user")
            or verified.get("display_name")
            or email
            or "Databricksユーザー"
        )
        return {
            "principal_id": principal,
            "email": email,
            "display_name": display_name,
        }

    @api.get("/api/console-links")
    async def console_links(
        request: Request,
        _: str = Depends(current_principal),
    ) -> dict[str, Any]:
        """Expose only server-generated links to the current Workspace UI."""

        return {"links": build_console_links(request.app.state.settings)}

    @api.get("/api/projects")
    async def list_projects(
        request: Request,
        principal: str = Depends(current_principal),
    ) -> dict[str, Any]:
        items = await asyncio.to_thread(request.app.state.repository.list_projects, principal)
        return {"items": items}

    @api.post("/api/projects", status_code=status.HTTP_201_CREATED)
    async def create_project(
        payload: ProjectCreate,
        request: Request,
        principal: str = Depends(current_principal),
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            request.app.state.repository.create_project,
            principal,
            payload.name,
            payload.description,
        )

    @api.delete("/api/projects/{project_id}")
    async def delete_project(
        project_id: str,
        request: Request,
        principal: str = Depends(current_principal),
    ) -> dict[str, Any]:
        # delete_project performs its own OWNER check and accepts an ARCHIVED
        # row so a timed-out client can retry idempotently.
        return await asyncio.to_thread(
            request.app.state.repository.delete_project,
            principal,
            project_id,
        )

    @api.get("/api/model-options")
    async def model_options(
        capability: str,
        request: Request,
        _: str = Depends(current_principal),
    ) -> dict[str, Any]:
        if capability not in {"embedding", "chat", "chat_tool_calling", "judge"}:
            raise AppError("VALIDATION_ERROR", "capabilityの指定が不正です。", status_code=422)
        items = await asyncio.to_thread(
            request.app.state.repository.list_model_options, capability
        )
        return {"items": items}

    @api.get("/api/index-profiles")
    async def index_profiles(
        request: Request,
        _: str = Depends(current_principal),
    ) -> dict[str, Any]:
        """List only manually provisioned source Table／AI Search Index pairs."""

        items = await asyncio.to_thread(
            request.app.state.repository.list_index_profiles
        )
        return {"items": items}

    @api.get("/api/projects/{project_id}/documents")
    async def list_documents(
        project_id: str,
        request: Request,
        principal: str = Depends(current_principal),
    ) -> dict[str, Any]:
        repo = request.app.state.repository
        await asyncio.to_thread(repo.require_project, principal, project_id, "VIEWER")
        items = await asyncio.to_thread(repo.list_documents, project_id)
        return {"items": items}

    @api.post(
        "/api/projects/{project_id}/documents",
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def upload_document(
        project_id: str,
        background_tasks: BackgroundTasks,
        request: Request,
        file: UploadFile = File(...),
        metadata: str = Form("{}"),
        principal: str = Depends(current_principal),
    ) -> dict[str, Any]:
        repo = request.app.state.repository
        await asyncio.to_thread(repo.require_project, principal, project_id, "EDITOR")
        try:
            parsed_metadata = parse_document_metadata_json(metadata)
        except Exception as exc:
            raise AppError(
                "VALIDATION_ERROR", "PDFのメタデータを確認してください。", status_code=422
            ) from exc
        filename = file.filename or "document.pdf"
        content_type = (file.content_type or "").lower()
        if not filename.lower().endswith(".pdf") or content_type not in {
            "application/pdf", "application/x-pdf", "application/octet-stream",
        }:
            raise AppError("INVALID_FILE", "PDFファイルだけをアップロードできます。", status_code=415)
        data = await file.read(MAX_PDF_BYTES + 1)
        if len(data) > MAX_PDF_BYTES:
            raise AppError("FILE_TOO_LARGE", "PDFは100 MB以下にしてください。", status_code=413)
        if not data.startswith(b"%PDF-"):
            raise AppError("INVALID_FILE", "PDFヘッダーを確認できませんでした。", status_code=415)
        result = await asyncio.to_thread(
            repo.register_document,
            project_id=project_id,
            principal=principal,
            filename=_safe_filename(filename),
            metadata=parsed_metadata,
            data=data,
        )
        doc_uri = result.pop("doc_uri")
        background_tasks.add_task(
            repo.parse_document,
            project_id,
            result["document_id"],
            result["parse_run_id"],
            doc_uri,
        )
        return result

    @api.post(
        "/api/projects/{project_id}/documents/{document_id}:parse",
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def retry_document_parse(
        project_id: str,
        document_id: str,
        background_tasks: BackgroundTasks,
        request: Request,
        principal: str = Depends(current_principal),
    ) -> dict[str, Any]:
        repo = request.app.state.repository
        await asyncio.to_thread(repo.require_project, principal, project_id, "EDITOR")
        result = await asyncio.to_thread(
            repo.retry_document_parse, project_id, document_id, principal
        )
        doc_uri = result.pop("doc_uri")
        background_tasks.add_task(
            repo.parse_document,
            project_id,
            result["document_id"],
            result["parse_run_id"],
            doc_uri,
        )
        return result

    @api.delete(
        "/api/projects/{project_id}/documents/{document_id}",
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def delete_document(
        project_id: str,
        document_id: str,
        request: Request,
        principal: str = Depends(current_principal),
    ) -> dict[str, Any]:
        # Repository performs the EDITOR check itself so a retry can observe a
        # DELETING tombstone without passing through require_project's mutation
        # fence.  This keeps repeated Delete clicks idempotent.
        return await asyncio.to_thread(
            request.app.state.repository.delete_document,
            principal,
            project_id,
            document_id,
        )

    @api.head(
        "/api/projects/{project_id}/documents/{document_id}/content",
        include_in_schema=False,
    )
    @api.get("/api/projects/{project_id}/documents/{document_id}/content")
    async def document_content(
        project_id: str,
        document_id: str,
        request: Request,
        principal: str = Depends(current_principal),
    ) -> Response:
        repo = request.app.state.repository
        await asyncio.to_thread(repo.require_project, principal, project_id, "VIEWER")
        data, filename = await asyncio.to_thread(repo.get_document_content, project_id, document_id)
        ascii_name = _safe_filename(filename).encode("ascii", "ignore").decode() or "document.pdf"
        etag = f'"{hashlib.sha256(data).hexdigest()}"'
        disposition = (
            f'inline; filename="{ascii_name}"; '
            f"filename*=UTF-8''{quote(_safe_filename(filename), safe='')}"
        )
        common_headers = {
            "Content-Disposition": disposition,
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, max-age=300, must-revalidate",
            "Accept-Ranges": "bytes",
            "ETag": etag,
        }
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers=common_headers)
        selected = _parse_single_byte_range(request.headers.get("range"), len(data))
        if selected is False:
            return Response(
                status_code=416,
                headers={**common_headers, "Content-Range": f"bytes */{len(data)}"},
            )
        response_data = b"" if request.method == "HEAD" else data
        status_code = 200
        if isinstance(selected, tuple):
            start, end = selected
            response_data = b"" if request.method == "HEAD" else data[start : end + 1]
            common_headers["Content-Range"] = f"bytes {start}-{end}/{len(data)}"
            common_headers["Content-Length"] = str(end - start + 1)
            status_code = 206
        else:
            common_headers["Content-Length"] = str(len(data))
        return Response(
            response_data,
            status_code=status_code,
            media_type="application/pdf",
            headers=common_headers,
        )

    @api.get("/api/projects/{project_id}/variants")
    async def list_variants(
        project_id: str,
        request: Request,
        principal: str = Depends(current_principal),
    ) -> dict[str, Any]:
        repo = request.app.state.repository
        await asyncio.to_thread(repo.require_project, principal, project_id, "VIEWER")
        return {"items": await asyncio.to_thread(repo.list_variants, project_id)}

    @api.get("/api/projects/{project_id}/evaluation-datasets")
    async def list_evaluation_datasets(
        project_id: str,
        request: Request,
        principal: str = Depends(current_principal),
    ) -> dict[str, Any]:
        repo = request.app.state.repository
        await asyncio.to_thread(repo.require_project, principal, project_id, "VIEWER")
        return {
            "items": await asyncio.to_thread(repo.list_evaluation_datasets, project_id)
        }

    @api.get("/api/projects/{project_id}/evaluation-cases")
    async def list_evaluation_cases(
        project_id: str,
        dataset_version: str,
        dataset_split: str,
        request: Request,
        principal: str = Depends(current_principal),
    ) -> dict[str, Any]:
        repo = request.app.state.repository
        await asyncio.to_thread(repo.require_project, principal, project_id, "VIEWER")
        if dataset_split not in {"development", "holdout"}:
            raise AppError("VALIDATION_ERROR", "評価データ区分が不正です。", status_code=422)
        return {
            "items": await asyncio.to_thread(
                repo.list_evaluation_cases,
                project_id,
                dataset_version,
                dataset_split,
            )
        }

    @api.post(
        "/api/projects/{project_id}/evaluation-cases",
        status_code=status.HTTP_201_CREATED,
    )
    async def create_evaluation_case(
        project_id: str,
        payload: EvaluationCaseCreate,
        request: Request,
        principal: str = Depends(current_principal),
    ) -> dict[str, Any]:
        repo = request.app.state.repository
        await asyncio.to_thread(repo.require_project, principal, project_id, "EDITOR")
        return await asyncio.to_thread(
            repo.create_evaluation_case,
            project_id,
            principal,
            payload,
        )

    @api.post(
        "/api/projects/{project_id}/preparation-runs",
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def create_preparation_run(
        project_id: str,
        payload: PreparationRequest,
        request: Request,
        principal: str = Depends(current_principal),
    ) -> dict[str, Any]:
        repo = request.app.state.repository
        await asyncio.to_thread(repo.require_project, principal, project_id, "EDITOR")
        return await asyncio.to_thread(
            repo.create_preparation_run, project_id, principal, payload
        )

    @api.get("/api/projects/{project_id}/preparation-runs/active")
    async def get_active_preparation_run(
        project_id: str,
        request: Request,
        principal: str = Depends(current_principal),
    ) -> dict[str, Any]:
        repo = request.app.state.repository
        # VIEWER may recover only an already-persisted BUILD_VARIANT control row.
        # This read accepts no build configuration; creation remains EDITOR-only,
        # and Repository/Job code revalidates the durable server-side contract.
        await asyncio.to_thread(repo.require_project, principal, project_id, "VIEWER")
        item = await asyncio.to_thread(repo.get_active_preparation_run, project_id)
        return {"item": item}

    @api.get("/api/projects/{project_id}/preparation-runs/{run_id}")
    async def get_preparation_run(
        project_id: str,
        run_id: str,
        request: Request,
        principal: str = Depends(current_principal),
    ) -> dict[str, Any]:
        repo = request.app.state.repository
        # The same narrow recovery rule as the active endpoint applies here.
        await asyncio.to_thread(repo.require_project, principal, project_id, "VIEWER")
        return await asyncio.to_thread(repo.get_preparation_run, project_id, run_id)

    @api.get("/api/projects/{project_id}/chat/sessions")
    async def list_sessions(
        project_id: str,
        request: Request,
        principal: str = Depends(current_principal),
    ) -> dict[str, Any]:
        repo = request.app.state.repository
        await asyncio.to_thread(repo.require_project, principal, project_id, "VIEWER")
        return {"items": await asyncio.to_thread(repo.list_sessions, project_id, principal)}

    @api.post(
        "/api/projects/{project_id}/chat/sessions",
        status_code=status.HTTP_201_CREATED,
    )
    async def create_session(
        project_id: str,
        payload: SessionCreate,
        request: Request,
        principal: str = Depends(current_principal),
    ) -> dict[str, Any]:
        repo = request.app.state.repository
        await asyncio.to_thread(repo.require_project, principal, project_id, "VIEWER")
        return await asyncio.to_thread(
            repo.create_session, project_id, principal, payload.title
        )

    @api.get("/api/projects/{project_id}/chat/sessions/{session_id}")
    async def get_session(
        project_id: str,
        session_id: str,
        request: Request,
        principal: str = Depends(current_principal),
    ) -> dict[str, Any]:
        repo = request.app.state.repository
        await asyncio.to_thread(repo.require_project, principal, project_id, "VIEWER")
        return await asyncio.to_thread(repo.get_session, project_id, session_id, principal)

    @api.delete("/api/projects/{project_id}/chat/sessions/{session_id}")
    async def delete_session(
        project_id: str,
        session_id: str,
        request: Request,
        principal: str = Depends(current_principal),
    ) -> dict[str, Any]:
        repo = request.app.state.repository
        await asyncio.to_thread(repo.require_project, principal, project_id, "VIEWER")
        try:
            return await asyncio.to_thread(
                repo.delete_session, project_id, session_id, principal
            )
        except AppError as exc:
            if exc.code == "SESSION_RUN_ACTIVE":
                for request_id in exc.details.get("request_ids", []):
                    request.app.state.cancellations.request(str(request_id))
            raise

    @api.post("/api/projects/{project_id}/chat/sessions/{session_id}/messages:stream")
    async def stream_chat_message(
        project_id: str,
        session_id: str,
        payload: ChatRequest,
        request: Request,
        principal: str = Depends(current_principal),
    ) -> StreamingResponse:
        repo: Repository = request.app.state.repository
        await asyncio.to_thread(repo.require_project, principal, project_id, "VIEWER")
        await asyncio.to_thread(repo.require_session, project_id, session_id, principal)
        config = resolve_retrieval_config(payload.retrieval)
        if payload.rag_mode == "AGENTIC":
            raise ResourceNotReadyError(
                "Agentic RAGはTool Calling用streaming adapterの検証後に有効化してください。"
            )
        model_target = await asyncio.to_thread(
            repo.resolve_model_target, payload.answer_model_key, "chat"
        )
        index_name, resolved_variant_id = await asyncio.to_thread(
            repo.resolve_search_target,
            project_id,
            payload.variant_id,
        )
        run = await asyncio.to_thread(
            repo.begin_chat_run,
            project_id=project_id,
            session_id=session_id,
            principal=principal,
            user_message=payload.message,
            request_id=(
                str(payload.client_request_id) if payload.client_request_id else None
            ),
            config={
                **config,
                "rag_mode": payload.rag_mode,
                "variant_id": resolved_variant_id,
                "answer_model_key": payload.answer_model_key,
            },
        )
        generator = request.app.state.rag.stream_response(
            project_id=project_id,
            run=run,
            question=payload.message,
            index_name=index_name,
            model_target=model_target,
            config={**config, "variant_id": resolved_variant_id},
            user_access_token=request.headers.get("x-forwarded-access-token"),
        )
        return StreamingResponse(
            generator,
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
                "X-Request-ID": run["request_id"],
            },
        )

    @api.post("/api/projects/{project_id}/chat/runs/{request_id}:cancel")
    async def cancel_chat_run(
        project_id: str,
        request_id: str,
        request: Request,
        principal: str = Depends(current_principal),
    ) -> dict[str, Any]:
        repo = request.app.state.repository
        await asyncio.to_thread(repo.require_project, principal, project_id, "VIEWER")
        run_status = await asyncio.to_thread(
            repo.request_chat_cancel, project_id, request_id, principal
        )
        # Never register an unauthorised or unknown request in the process-local
        # fast path.  The durable run transition above is the source of truth.
        if run_status == "CANCEL_REQUESTED":
            request.app.state.cancellations.request(request_id)
        return {"request_id": request_id, "status": run_status}

    @api.post(
        "/api/projects/{project_id}/evaluation-runs",
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def create_evaluation_run(
        project_id: str,
        payload: EvaluationRequest,
        request: Request,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        principal: str = Depends(current_principal),
    ) -> dict[str, Any]:
        repo = request.app.state.repository
        await asyncio.to_thread(repo.require_project, principal, project_id, "EDITOR")
        if not idempotency_key or not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", idempotency_key):
            raise AppError(
                "IDEMPOTENCY_KEY_REQUIRED",
                "精度評価の開始にはIdempotency-Keyが必要です。",
                status_code=400,
            )
        # The Project/principal/key scope deterministically identifies one
        # durable evaluation run; the Lakeflow run reuses that ID as its
        # idempotency token.
        return await asyncio.to_thread(
            repo.create_evaluation_run, project_id, principal, payload, idempotency_key
        )

    @api.get("/api/projects/{project_id}/evaluation-runs")
    async def list_evaluation_runs(
        project_id: str,
        request: Request,
        principal: str = Depends(current_principal),
    ) -> dict[str, Any]:
        repo = request.app.state.repository
        await asyncio.to_thread(repo.require_project, principal, project_id, "VIEWER")
        return {
            "items": await asyncio.to_thread(repo.list_evaluation_runs, project_id)
        }

    @api.get("/api/projects/{project_id}/evaluation-runs/{eval_run_id}")
    async def get_evaluation_run(
        project_id: str,
        eval_run_id: str,
        request: Request,
        principal: str = Depends(current_principal),
    ) -> dict[str, Any]:
        repo = request.app.state.repository
        await asyncio.to_thread(repo.require_project, principal, project_id, "VIEWER")
        return await asyncio.to_thread(repo.get_evaluation_run, project_id, eval_run_id)

    @api.get("/api/projects/{project_id}/evaluation-runs/{eval_run_id}/results")
    async def get_evaluation_results(
        project_id: str,
        eval_run_id: str,
        request: Request,
        principal: str = Depends(current_principal),
    ) -> dict[str, Any]:
        repo = request.app.state.repository
        await asyncio.to_thread(repo.require_project, principal, project_id, "VIEWER")
        return await asyncio.to_thread(repo.get_evaluation_results, project_id, eval_run_id)

    @api.post("/api/projects/{project_id}/evaluation-runs/{eval_run_id}:cancel")
    async def cancel_evaluation_run(
        project_id: str,
        eval_run_id: str,
        request: Request,
        principal: str = Depends(current_principal),
    ) -> dict[str, Any]:
        repo = request.app.state.repository
        await asyncio.to_thread(repo.require_project, principal, project_id, "EDITOR")
        final_status = await asyncio.to_thread(
            repo.request_evaluation_cancel, project_id, eval_run_id
        )
        return {
            "eval_run_id": eval_run_id,
            "status": final_status or "CANCEL_REQUESTED",
        }

    @api.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @api.get("/projects/{project_id}/{page}", include_in_schema=False)
    async def project_page(project_id: str, page: str) -> FileResponse:
        del project_id
        if page not in {"data-preparation", "catalog", "chat", "evaluation"}:
            raise AppError("NOT_FOUND", "ページが見つかりません。", status_code=404)
        return FileResponse(STATIC_DIR / "index.html")

    @api.get("/projects/{project_id}/catalog/{document_id}", include_in_schema=False)
    async def catalog_document_page(project_id: str, document_id: str) -> FileResponse:
        del project_id, document_id
        return FileResponse(STATIC_DIR / "index.html")

    return api


async def current_principal(request: Request) -> str:
    custom = request.app.state.authenticator
    if custom is not None:
        result = custom(request)
        if inspect.isawaitable(result):
            result = await result
        principal = str(result)
        request.state.current_user = {
            "principal_id": principal,
            "email": principal if "@" in principal else None,
            "display_name": None,
        }
        return principal

    settings: Settings = request.app.state.settings
    token = request.headers.get("x-forwarded-access-token")
    if token:
        identity = await asyncio.to_thread(_verify_user_identity, settings, token)
        request.state.current_user = identity
        return str(identity["principal_id"])
    if settings.local_principal and not settings.databricks_host:
        request.state.current_user = {
            "principal_id": settings.local_principal,
            "email": settings.local_principal if "@" in settings.local_principal else None,
            "display_name": None,
        }
        return settings.local_principal
    raise AppError(
        "UNAUTHENTICATED",
        "Databricksのユーザー認証を確認できません。Appをワークスペースから開いてください。",
        status_code=401,
    )


def _verify_user_token(settings: Settings, token: str) -> str:
    return str(_verify_user_identity(settings, token)["principal_id"])


def _verify_user_identity(settings: Settings, token: str) -> dict[str, str | None]:
    try:
        from databricks.sdk import WorkspaceClient

        kwargs = {
            "token": token,
            # Databricks Apps also injects the app service principal OAuth
            # credentials. Select bearer-token auth explicitly so the user's
            # forwarded token does not conflict with those ambient values.
            "auth_type": "pat",
        }
        if settings.databricks_host:
            kwargs["host"] = settings.databricks_host
        user = WorkspaceClient(**kwargs).current_user.me()
        principal = getattr(user, "id", None)
        if not principal:
            raise RuntimeError("current user ID missing")
        user_name = getattr(user, "user_name", None)
        email = str(user_name).strip() if user_name and "@" in str(user_name) else None
        if email is None:
            raw_emails = getattr(user, "emails", None) or []
            primary = next(
                (
                    item
                    for item in raw_emails
                    if bool(getattr(item, "primary", False))
                    and getattr(item, "value", None)
                ),
                None,
            )
            selected = primary or next(
                (item for item in raw_emails if getattr(item, "value", None)), None
            )
            if selected is not None:
                email = str(getattr(selected, "value")).strip() or None
        raw_display_name = getattr(user, "display_name", None)
        display_name = str(raw_display_name).strip() if raw_display_name else None
        return {
            "principal_id": str(principal),
            "email": email,
            "display_name": display_name,
        }
    except Exception as exc:
        raise AppError(
            "UNAUTHENTICATED",
            "Databricksのユーザー認証に失敗しました。ページを再読み込みしてください。",
            status_code=401,
        ) from exc


def _parse_single_byte_range(
    value: str | None,
    size: int,
) -> tuple[int, int] | bool | None:
    """Parse one RFC 7233 byte range; False represents an unsatisfiable range."""

    if value is None:
        return None
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", value.strip())
    if match is None or size <= 0:
        return False
    start_text, end_text = match.groups()
    if not start_text and not end_text:
        return False
    if not start_text:
        suffix = int(end_text)
        if suffix <= 0:
            return False
        start = max(0, size - suffix)
        return start, size - 1
    start = int(start_text)
    if start >= size:
        return False
    end = int(end_text) if end_text else size - 1
    if end < start:
        return False
    return start, min(end, size - 1)


def _safe_filename(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    basename = normalized.replace("\\", "/").split("/")[-1]
    # Keep Unicode letters and numbers for Japanese document titles while
    # removing control characters and header/path metacharacters.  The Volume
    # object path itself is UUID-derived and never uses this display filename.
    sanitized = re.sub(r"[^\w.\- ()\[\]]", "_", basename, flags=re.UNICODE).strip(" .")
    return (sanitized or "document.pdf")[:180]


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=app.state.settings.app_port)
