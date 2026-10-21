import hashlib
import json
import logging
from datetime import timedelta
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from app import __version__
from app.api.dependencies import DB, CurrentUser
from app.models import (
    AuthSession,
    Chunk,
    Conversation,
    Document,
    DocumentRevision,
    EvaluationRun,
    IngestionJob,
    KnowledgeBase,
    KnowledgeBaseGrant,
    Message,
    User,
    new_id,
    utcnow,
)
from app.schemas import (
    ChatRequest,
    ComparisonRequest,
    EvaluationRequest,
    KnowledgeBaseRequest,
    LoginRequest,
    RegisterRequest,
)
from app.security import hash_password, issue_token, verify_password
from app.services.access import allowed_kbs, audit, kb_role, require_kb
from app.services.admission import consume
from app.worker import notify_job

router = APIRouter(prefix="/api")
logger = logging.getLogger(__name__)
ALLOWED_EXTENSIONS = {".txt", ".md", ".csv", ".json", ".html", ".htm", ".pdf", ".docx", ".png", ".jpg", ".jpeg", ".webp"}


def user_payload(user, settings=None):
    from app.services.operations import is_platform_admin
    return {"id": user.id, "email": user.email, "name": user.name,
            "is_platform_admin": bool(settings and is_platform_admin(user, settings))}


def auth_payload(user, request, db, response):
    settings = request.app.state.settings
    session = AuthSession(user_id=user.id, expires_at=utcnow() + timedelta(seconds=settings.token_lifetime_seconds))
    db.add(session)
    db.flush()
    token = issue_token(user.id, settings, session.id)
    audit(db, user.id, "session.create", session.id)
    db.commit()
    response.set_cookie("rag_session", token, httponly=True, samesite="lax", path="/api",
        secure=settings.cookie_secure or settings.public_base_url.startswith("https://"), max_age=settings.token_lifetime_seconds)
    return {"access_token": token, "token_type": "bearer", "user": user_payload(user, settings)}


def own_kb(db, user, kb_id, action="read"):
    return require_kb(db, user, kb_id, action)


def own_document(db, user, document_id, action="read"):
    document = db.scalar(select(Document).join(KnowledgeBase).where(
        Document.id == document_id, allowed_kbs(user.id)))
    if document is None:
        raise HTTPException(404, "文档不存在")
    require_kb(db, user, document.knowledge_base_id, action)
    return document


def own_conversation(db, user, conversation_id):
    conversation = db.scalar(select(Conversation).where(
        Conversation.id == conversation_id, Conversation.user_id == user.id))
    if conversation is None:
        raise HTTPException(404, "会话不存在")
    return conversation


def document_payload(db, document):
    job_id = db.scalar(select(IngestionJob.id).where(IngestionJob.document_id == document.id)
                       .order_by(IngestionJob.created_at.desc()).limit(1))
    return {"id": document.id, "knowledge_base_id": document.knowledge_base_id, "name": document.name,
            "size_bytes": document.size_bytes, "status": document.status, "chunk_count": document.chunk_count,
            "error": document.error, "created_at": document.created_at, "job_id": job_id,
            "active_revision_id": document.active_revision_id, "expires_at": document.expires_at,
            "source": document.source}


def evaluation_payload(run, db=None, user=None):
    restricted = False
    if db is not None and user is not None:
        accessible = set(db.scalars(select(KnowledgeBase.id).where(allowed_kbs(user.id))).all())
        references = [c for row in (run.results or []) for c in row.get("citations", [])]
        references += [c for case in (run.cases or []) for c in case.get("recorded_citations", [])]
        restricted = bool(set(run.knowledge_base_ids or []) - accessible) or any(
            c.get("knowledge_base_id") not in accessible for c in references)
    if restricted:
        return {"id": run.id, "name": run.name, "status": "restricted", "metrics": {}, "results": [],
            "cases": [], "knowledge_base_ids": [], "created_at": run.created_at, "error": "来源知识库的访问权限已撤销"}
    return {"id": run.id, "name": run.name, "status": run.status, "metrics": run.metrics,
            "results": run.results, "created_at": run.created_at, "error": run.error,
            "cases": run.cases, "knowledge_base_ids": run.knowledge_base_ids}


def scoped_chunks(db, user, kb_ids):
    from app.services.corpus import CorpusScope
    return CorpusScope(db, user, list(dict.fromkeys(kb_ids)))


@router.get("/health")
def health():
    return {"status": "ok", "version": __version__}


@router.post("/auth/register", status_code=201)
def register(body: RegisterRequest, request: Request, response: Response, db: DB):
    settings = request.app.state.settings
    if not settings.allow_registration:
        raise HTTPException(403, "当前工作区已关闭公开注册，请使用企业账号登录")
    consume(request.app.state.database, f"auth:{request.client.host}", settings.auth_rate_per_minute)
    user = User(email=body.email, name=body.name, password_hash=hash_password(body.password))
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "此邮箱已注册")
    return auth_payload(user, request, db, response)


@router.post("/auth/login")
def login(body: LoginRequest, request: Request, response: Response, db: DB):
    consume(request.app.state.database, f"auth:{request.client.host}", request.app.state.settings.auth_rate_per_minute)
    user = db.scalar(select(User).where(User.email == body.email))
    encoded = user.password_hash if user else "pbkdf2_sha256$600000$00000000000000000000000000000000$" + "0" * 64
    valid = verify_password(body.password, encoded)
    if user is None or user.disabled or not valid:
        raise HTTPException(401, "邮箱或密码错误", headers={"WWW-Authenticate": "Bearer"})
    return auth_payload(user, request, db, response)


@router.get("/auth/me")
def me(user: CurrentUser, request: Request):
    return user_payload(user, request.app.state.settings)


@router.post("/auth/logout", status_code=204)
def logout(request: Request, response: Response, db: DB, user: CurrentUser):
    session = db.get(AuthSession, request.state.session_id)
    session.revoked = True
    audit(db, user.id, "session.revoke", session.id)
    db.commit()
    response.delete_cookie("rag_session", path="/api")


@router.delete("/auth/sessions", status_code=204)
def revoke_sessions(db: DB, user: CurrentUser):
    db.execute(update(AuthSession).where(AuthSession.user_id == user.id).values(revoked=True))
    audit(db, user.id, "session.revoke_all", user.id)
    db.commit()


@router.get("/knowledge-bases")
def knowledge_bases(db: DB, user: CurrentUser):
    query = select(KnowledgeBase, func.count(Document.id)).outerjoin(Document).where(
        allowed_kbs(user.id)).group_by(KnowledgeBase.id).order_by(KnowledgeBase.created_at.desc())
    return [{"id": kb.id, "name": kb.name, "description": kb.description,
             "created_at": kb.created_at, "document_count": count, "organization_id": kb.organization_id,
             "visibility": kb.visibility, "role": kb_role(db, user, kb)}
            for kb, count in db.execute(query).all()]


@router.post("/knowledge-bases", status_code=201)
def create_knowledge_base(body: KnowledgeBaseRequest, db: DB, user: CurrentUser):
    if body.organization_id:
        from app.services.access import require_org
        member = require_org(db, user, body.organization_id)
        if member.role not in {"admin", "editor"}:
            raise HTTPException(403, "当前角色不能创建知识库")
    kb = KnowledgeBase(user_id=user.id, name=body.name, description=body.description,
                       organization_id=body.organization_id, visibility=body.visibility)
    db.add(kb)
    db.flush()
    if body.organization_id and body.visibility == "restricted":
        db.add(KnowledgeBaseGrant(knowledge_base_id=kb.id, user_id=user.id, role="admin"))
    audit(db, user.id, "knowledge_base.create", kb.id, kb.organization_id)
    db.commit()
    return {"id": kb.id, "name": kb.name, "description": kb.description,
            "created_at": kb.created_at, "document_count": 0, "organization_id": kb.organization_id,
            "visibility": kb.visibility, "role": kb_role(db, user, kb)}


def remove_documents(db, documents, settings):
    from app.services.cleanup import enqueue_delete
    tasks = []
    for document in documents:
        # This write fences in-flight vector upserts before deleting SQL rows.
        db.execute(update(Document).where(Document.id == document.id).values(status="deleting"))
        tasks.append(enqueue_delete(db, document))
    db.flush()
    return tasks


def unlink_uploads(paths, settings):
    root = (settings.data_dir / "uploads").resolve()
    for stored_path in paths:
        path = Path(stored_path).resolve()
        if path.is_relative_to(root):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                logger.exception("Could not remove deleted upload %s", path.name)


@router.delete("/knowledge-bases/{kb_id}", status_code=204)
def delete_knowledge_base(kb_id: str, request: Request, db: DB, user: CurrentUser):
    own_kb(db, user, kb_id, "admin")
    kb = db.scalar(select(KnowledgeBase).where(KnowledgeBase.id == kb_id).with_for_update())
    if kb is None:
        raise HTTPException(404, "知识库不存在")
    # PostgreSQL's row lock prevents a new document FK from appearing during
    # cleanup; this write also provides the equivalent serialization in SQLite.
    db.execute(update(KnowledgeBase).where(KnowledgeBase.id == kb.id).values(name=kb.name))
    documents = db.scalars(select(Document).where(Document.knowledge_base_id == kb.id)).all()
    remove_documents(db, documents, request.app.state.settings)
    audit(db, user.id, "knowledge_base.delete", kb.id, kb.organization_id)
    db.delete(kb)
    db.commit()
    # External cleanup is asynchronous; SQL visibility changes immediately.


@router.get("/documents")
def documents(db: DB, user: CurrentUser, knowledge_base_id: str | None = None):
    query = select(Document).join(KnowledgeBase).where(allowed_kbs(user.id))
    if knowledge_base_id:
        own_kb(db, user, knowledge_base_id)
        query = query.where(Document.knowledge_base_id == knowledge_base_id)
    return [document_payload(db, item) for item in db.scalars(query.order_by(Document.created_at.desc())).all()]


@router.post("/documents/upload", status_code=201)
async def upload_document(request: Request, db: DB, user: CurrentUser,
                          file: Annotated[UploadFile, File()], knowledge_base_id: Annotated[str, Form()],
                          document_id: Annotated[str | None, Form()] = None):
    kb = own_kb(db, user, knowledge_base_id, "write")
    replacing = own_document(db, user, document_id, "write") if document_id else None
    if replacing and replacing.knowledge_base_id != kb.id:
        raise HTTPException(422, "文档不属于所选知识库")
    if replacing and replacing.status not in {"ready", "failed"}:
        raise HTTPException(409, "已有文档任务正在运行")
    filename = Path((file.filename or "").replace("\\", "/")).name[:255]
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(415, "不支持此格式。支持 PDF、Word(docx)、文本、Markdown、CSV、JSON、HTML 和图片")
    settings = request.app.state.settings
    upload_root = (settings.data_dir / "uploads").resolve()
    upload_root.mkdir(parents=True, exist_ok=True)
    target = upload_root / f"{new_id()}{suffix}"
    digest, size, committed = hashlib.sha256(), 0, False
    try:
        with target.open("xb") as output:
            while data := await file.read(1024 * 1024):
                size += len(data)
                if size > settings.max_upload_mb * 1024 * 1024:
                    raise HTTPException(413, f"文档不能超过 {settings.max_upload_mb} MB")
                digest.update(data)
                output.write(data)
        if size == 0:
            raise HTTPException(400, "不能上传空文件")
        existing = db.scalar(select(Document).where(Document.knowledge_base_id == knowledge_base_id,
                                                    Document.sha256 == digest.hexdigest()))
        if existing and not replacing:
            target.unlink(missing_ok=True)
            payload = document_payload(db, existing)
            return {"document": payload, "job_id": payload["job_id"], "duplicate": True}
        if existing and replacing and existing.id != replacing.id:
            raise HTTPException(409, "相同内容已存在于此知识库的另一文档中")
        from app.services.lifecycle import reserve_ingestion
        reserve_ingestion(db, user, knowledge_base_id, settings, size)
        if replacing:
            db.refresh(replacing)
            if replacing.status not in {"ready", "failed"}:
                raise HTTPException(409, "已有文档任务正在运行")
        document = replacing or Document(knowledge_base_id=knowledge_base_id, name=filename, stored_path=str(target),
                            sha256=digest.hexdigest(), size_bytes=size, status="queued")
        db.add(document)
        db.flush()
        revision = DocumentRevision(document_id=document.id, name=filename, stored_path=str(target),
                                    sha256=digest.hexdigest(), size_bytes=size)
        db.add(revision)
        db.flush()
        document.status, document.error = "queued", None
        job = IngestionJob(document_id=document.id, revision_id=revision.id)
        db.add(job)
        audit(db, user.id, "document.version.upload" if replacing else "document.upload", document.id, kb.organization_id,
              getattr(request.state, "request_id", ""))
        db.commit()
        committed = True
        notify_job(settings, job.id)
        return {"document": document_payload(db, document), "job_id": job.id, "duplicate": False}
    except IntegrityError:
        db.rollback()
        target.unlink(missing_ok=True)
        existing = db.scalar(select(Document).where(Document.knowledge_base_id == knowledge_base_id,
                                                    Document.sha256 == digest.hexdigest()))
        if existing:
            payload = document_payload(db, existing)
            return {"document": payload, "job_id": payload["job_id"], "duplicate": True}
        raise HTTPException(409, "文档或知识库状态已发生变化，请刷新后重试")
    except BaseException:
        if not committed:
            target.unlink(missing_ok=True)
        raise
    finally:
        await file.close()


@router.get("/documents/{document_id}/download")
def download_document(document_id: str, request: Request, db: DB, user: CurrentUser):
    document = own_document(db, user, document_id)
    path = Path(document.stored_path).resolve()
    if not path.is_relative_to((request.app.state.settings.data_dir / "uploads").resolve()) or not path.is_file():
        raise HTTPException(404, "原文件不存在")
    kb = own_kb(db, user, document.knowledge_base_id)
    audit(db, user.id, "document.download", document.id, kb.organization_id)
    db.commit()
    return FileResponse(path, filename=document.name, media_type="application/octet-stream",
                        headers={"X-Content-Type-Options": "nosniff"})


@router.delete("/documents/{document_id}", status_code=204)
def delete_document_api(document_id: str, request: Request, db: DB, user: CurrentUser):
    document = own_document(db, user, document_id, "write")
    kb = own_kb(db, user, document.knowledge_base_id)
    remove_documents(db, [document], request.app.state.settings)
    kb.generation += 1
    audit(db, user.id, "document.delete", document_id, kb.organization_id)
    db.commit()
    # The committed outbox survives a storage outage and is retried by the worker.


@router.post("/documents/{document_id}/retry")
def retry_document(document_id: str, request: Request, db: DB, user: CurrentUser):
    document = own_document(db, user, document_id, "write")
    from app.services.lifecycle import reserve_ingestion
    reserve_ingestion(db, user, document.knowledge_base_id, request.app.state.settings)
    if document.status not in {"failed", "ready"}:
        raise HTTPException(409, "仅失败或已就绪的文档可以重新索引")
    changed = db.execute(update(Document).where(Document.id == document_id, Document.status.in_(["failed", "ready"]))
                         .values(status="queued", error=None))
    if changed.rowcount != 1:
        raise HTTPException(409, "任务状态已变化，请刷新后重试")
    revision = DocumentRevision(document_id=document_id, name=document.name, stored_path=document.stored_path,
                                sha256=document.sha256, size_bytes=document.size_bytes)
    db.add(revision)
    db.flush()
    job = IngestionJob(document_id=document_id, revision_id=revision.id)
    db.add(job)
    audit(db, user.id, "document.reindex", document_id, own_kb(db, user, document.knowledge_base_id).organization_id)
    db.commit()
    notify_job(request.app.state.settings, job.id)
    return {"job_id": job.id}


@router.get("/documents/{document_id}/chunks")
def document_chunks(document_id: str, db: DB, user: CurrentUser):
    document = own_document(db, user, document_id)
    return [{"id": chunk.id, "text": chunk.text, "page": chunk.page, "ordinal": chunk.ordinal, "locator": chunk.locator or {}}
            for chunk in db.scalars(select(Chunk).where(Chunk.document_id == document_id,
                Chunk.revision_id == document.active_revision_id).order_by(Chunk.ordinal).limit(500)).all()]


@router.get("/jobs/{job_id}")
def job_status(job_id: str, db: DB, user: CurrentUser):
    job = db.scalar(select(IngestionJob).join(Document).join(KnowledgeBase).where(
        IngestionJob.id == job_id, allowed_kbs(user.id)))
    if job is None:
        raise HTTPException(404, "任务不存在")
    return {"id": job.id, "status": job.status, "progress": job.progress,
            "error": job.error, "document_id": job.document_id, "stage": job.stage,
            "processed_pages": job.processed_pages, "total_pages": job.total_pages}


def perform_chat(body, db, user, request):
    from app.services.chat import run_chat
    try:
        return run_chat(body, db, user, request)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Answer provider failed")
        raise HTTPException(503, "问答服务暂时不可用，请稍后重试") from exc


@router.post("/chat")
def chat(body: ChatRequest, request: Request, db: DB, user: CurrentUser):
    return perform_chat(body, db, user, request)


@router.post("/chat/stream")
async def stream_chat(body: ChatRequest, request: Request, user: CurrentUser):
    import asyncio
    import queue
    import threading

    from app.services.chat import run_chat
    events, cancelled = queue.Queue(maxsize=128), threading.Event()
    user_id = user.id

    def emit(event, value):
        while not cancelled.is_set():
            try:
                events.put((event, value), timeout=0.2)
                return
            except queue.Full:
                continue
        raise HTTPException(499, "请求已取消")

    def produce():
        try:
            with request.app.state.database.session() as session:
                actor = session.get(User, user_id)
                if not actor or actor.disabled:
                    raise HTTPException(401, "登录状态无效")
                result = run_chat(body, session, actor, request, emit, cancelled.is_set)
                emit("done", result)
        except Exception as exc:
            if not cancelled.is_set():
                emit("error", {"status": exc.status_code if isinstance(exc, HTTPException) else 503,
                    "detail": exc.detail if isinstance(exc, HTTPException) else "生成失败，请稍后重试"})

    threading.Thread(target=produce, daemon=True).start()

    async def stream():
        try:
            while not cancelled.is_set():
                if await request.is_disconnected():
                    break
                try:
                    event, value = await asyncio.to_thread(events.get, True, 0.5)
                except queue.Empty:
                    yield ": keepalive\n\n"
                    continue
                yield "event: " + event + "\ndata: " + json.dumps(value, ensure_ascii=False) + "\n\n"
                if event in {"done", "error"}:
                    break
        finally:
            cancelled.set()
    return StreamingResponse(stream(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/conversations")
def conversations(db: DB, user: CurrentUser):
    return [{"id": item.id, "title": item.title, "created_at": item.created_at}
            for item in db.scalars(select(Conversation).where(Conversation.user_id == user.id)
                                   .order_by(Conversation.created_at.desc())).all()]


@router.get("/conversations/{conversation_id}")
def conversation_detail(conversation_id: str, db: DB, user: CurrentUser):
    item = own_conversation(db, user, conversation_id)
    messages = db.scalars(select(Message).where(Message.conversation_id == item.id)
                          .order_by(Message.created_at, Message.id)).all()
    accessible = set(db.scalars(select(KnowledgeBase.id).where(allowed_kbs(user.id))).all())
    payloads = []
    for message in messages:
        restricted = any(c.get("knowledge_base_id") not in accessible for c in (message.citations or []))
        payloads.append({"id": message.id, "role": message.role,
            "content": "此回答涉及已撤销访问权限的知识库，内容已隐藏。" if restricted else message.content,
            "citations": [] if restricted else message.citations,
            "trace": [] if restricted else message.trace, "created_at": message.created_at,
            "run_metadata": {} if restricted else message.run_metadata})
    return {"id": item.id, "title": item.title, "messages": payloads}


@router.delete("/conversations/{conversation_id}", status_code=204)
def delete_conversation(conversation_id: str, db: DB, user: CurrentUser):
    item = own_conversation(db, user, conversation_id)
    db.delete(item)
    db.commit()


def validate_expected_documents(db, user, cases, kb_ids):
    expected = {doc for case in cases for doc in case.expected_document_ids}
    if not expected:
        return
    query = select(Document.id).join(KnowledgeBase).where(
        allowed_kbs(user.id), Document.id.in_(expected))
    if kb_ids:
        query = query.where(Document.knowledge_base_id.in_(kb_ids))
    if set(db.scalars(query).all()) != expected:
        raise HTTPException(422, "预期文档必须属于当前用户所选知识库")


@router.post("/evaluations", status_code=202)
def create_evaluation(body: EvaluationRequest, request: Request, db: DB, user: CurrentUser):
    scoped_chunks(db, user, body.knowledge_base_ids)
    validate_expected_documents(db, user, body.cases, body.knowledge_base_ids)
    cases = [case.model_dump() for case in body.cases]
    for case in cases:
        if case.get("message_id"):
            recorded = db.scalar(select(Message).join(Conversation).where(
                Message.id == case["message_id"], Message.role == "assistant", Conversation.user_id == user.id))
            if recorded is None:
                raise HTTPException(404, "历史答案不存在或无权访问")
            accessible = set(db.scalars(select(KnowledgeBase.id).where(allowed_kbs(user.id))).all())
            if any(c.get("knowledge_base_id") not in accessible for c in (recorded.citations or [])):
                raise HTTPException(403, "历史答案的来源知识库权限已撤销")
            question_message = db.scalar(select(Message).where(Message.conversation_id == recorded.conversation_id,
                Message.role == "user", Message.created_at <= recorded.created_at)
                .order_by(Message.created_at.desc(), Message.id.desc()).limit(1))
            if question_message is None:
                raise HTTPException(400, "历史答案缺少对应问题")
            case.update(question=question_message.content, recorded_answer=recorded.content,
                recorded_citations=recorded.citations or [])
    from app.services.operations import effective_settings
    settings = effective_settings(db, request.app.state.settings, user.id)
    consume(request.app.state.database, f"evaluation:{user.id}", 5, 60)
    consume(request.app.state.database, f"model-budget:{user.id}", settings.user_daily_model_requests,
            86400, cost=len(cases) * (6 if body.use_ragas else 1))
    run = EvaluationRun(user_id=user.id, name=body.name, status="queued", cases=cases,
        knowledge_base_ids=body.knowledge_base_ids, use_ragas=body.use_ragas)
    db.add(run)
    db.flush()
    audit(db, user.id, "evaluation.create", run.id)
    db.commit()
    notify_job(settings, run.id)
    return evaluation_payload(run, db, user)


@router.post("/evaluations/compare")
def compare_evaluation(body: ComparisonRequest, db: DB, user: CurrentUser):
    from app.services.evaluation import compare_retrieval
    chunks = scoped_chunks(db, user, body.knowledge_base_ids)
    validate_expected_documents(db, user, body.cases, body.knowledge_base_ids)
    try:
        return compare_retrieval([case.model_dump() for case in body.cases], chunks)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        logger.exception("Retrieval comparison failed")
        raise HTTPException(503, "检索对比暂时不可用") from exc


@router.get("/evaluations")
def evaluations(db: DB, user: CurrentUser):
    return [evaluation_payload(run, db, user) for run in db.scalars(select(EvaluationRun)
            .where(EvaluationRun.user_id == user.id).order_by(EvaluationRun.created_at.desc())).all()]


@router.get("/evaluations/{evaluation_id}")
def evaluation_detail(evaluation_id: str, db: DB, user: CurrentUser):
    run = db.scalar(select(EvaluationRun).where(EvaluationRun.id == evaluation_id, EvaluationRun.user_id == user.id))
    if run is None:
        raise HTTPException(404, "评测不存在")
    return evaluation_payload(run, db, user)


@router.get("/system")
def system_info(user: CurrentUser):
    from app.services.runtime import capabilities
    return capabilities()
