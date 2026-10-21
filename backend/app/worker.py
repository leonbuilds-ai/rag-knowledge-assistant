"""Durable ingestion/evaluation/outbox worker with leases and bounded retries."""
import logging
import threading
import uuid
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path

from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy import delete, or_, select, update

from app.config import get_settings
from app.db import Database
from app.models import (
    AdmissionSlot,
    Chunk,
    CleanupTask,
    Document,
    DocumentRevision,
    EvaluationRun,
    IngestionJob,
    KnowledgeBase,
    User,
    new_id,
    utcnow,
)

logger = logging.getLogger(__name__)
QUEUE_KEY = "rag:ingestion:pending"


def notify_job(settings, job_id):
    if settings.redis_url:
        try:
            with Redis.from_url(settings.redis_url, socket_connect_timeout=2, socket_timeout=2) as client:
                key = settings.qdrant_collection_prefix + ":" + QUEUE_KEY
                client.lpush(key, job_id)
                client.ltrim(key, 0, 99)
                client.expire(key, 60)
        except (RedisError, ValueError):
            logger.warning("Redis unavailable; SQL job remains durable")


def _claim(database, settings, job_id):
    now, lease = utcnow(), new_id()
    with database.session() as db:
        changed = db.execute(update(IngestionJob).where(IngestionJob.id == job_id,
            or_(IngestionJob.available_at.is_(None), IngestionJob.available_at <= now),
            or_(IngestionJob.status == "queued", (IngestionJob.status == "processing") &
                or_(IngestionJob.lease_expires_at < now, IngestionJob.lease_expires_at.is_(None))))
            .values(status="processing", progress=1, error=None, lease_token=lease,
                lease_expires_at=now + timedelta(seconds=settings.job_lease_seconds),
                attempts=IngestionJob.attempts + 1, updated_at=now))
        db.commit()
        return lease if changed.rowcount else None


def _heartbeat(database, settings, job_id, lease, done):
    while not done.wait(min(15, settings.job_lease_seconds / 3)):
        try:
            with database.session() as db:
                changed = db.execute(update(IngestionJob).where(IngestionJob.id == job_id,
                    IngestionJob.lease_token == lease, IngestionJob.status == "processing").values(
                    lease_expires_at=utcnow() + timedelta(seconds=settings.job_lease_seconds), updated_at=utcnow()))
                db.commit()
                if not changed.rowcount:
                    return
        except Exception:
            logger.exception("Ingestion heartbeat failed")


def process_job(database, settings, job_id):
    from app.services.corpus import term_rows
    from app.services.parsing import chunk_pages, parse_document
    from app.services.retrieval import index_chunks
    lease = _claim(database, settings, job_id)
    if not lease:
        return
    done = threading.Event()
    heartbeat = threading.Thread(target=_heartbeat, args=(database, settings, job_id, lease, done), daemon=True)
    heartbeat.start()
    document_id = revision_id = None
    try:
        with database.session() as db:
            job = db.get(IngestionJob, job_id)
            document = db.get(Document, job.document_id) if job else None
            if not document:
                return
            document_id, kb_id = document.id, document.knowledge_base_id
            revision = db.get(DocumentRevision, job.revision_id) if job.revision_id else None
            if revision is None or revision.status == "ready":
                revision = DocumentRevision(document_id=document.id, name=document.name, stored_path=document.stored_path,
                    sha256=document.sha256, size_bytes=document.size_bytes)
                db.add(revision)
                db.flush()
                job.revision_id = revision.id
            revision_id, filename, stored_path = revision.id, revision.name, revision.stored_path
            revision.status, document.status, document.error = "processing", "processing", None
            db.add(CleanupTask(document_id=document_id, revision_id=revision_id,
                available_at=utcnow() + timedelta(seconds=settings.job_lease_seconds)))
            db.commit()
        path = Path(stored_path).resolve()
        if not path.is_relative_to((settings.data_dir / "uploads").resolve()):
            raise ValueError("文件存储路径不在 uploads 目录内")
        def report(processed, total, stage="生成向量"):
            with database.session() as db:
                changed = db.execute(update(IngestionJob).where(IngestionJob.id == job_id,
                    IngestionJob.lease_token == lease, IngestionJob.status == "processing").values(
                    stage=stage, processed_pages=processed if stage != "生成向量" else IngestionJob.processed_pages,
                    total_pages=total if stage != "生成向量" else IngestionJob.total_pages,
                    progress=(35 + int(50 * processed / max(total, 1))) if stage == "生成向量" else
                        (2 + int(30 * processed / max(total, 1))), updated_at=utcnow()))
                db.commit()
                if not changed.rowcount:
                    raise ValueError("任务已取消、删除或租约已变化")
        parsed = chunk_pages(parse_document(path, filename, progress=report))
        indexed = [{"id": str(uuid.uuid5(uuid.UUID(revision_id), str(item["ordinal"]))), "document_id": document_id, "document_name": filename,
            "knowledge_base_id": kb_id, "revision_id": revision_id, **item} for item in parsed]

        @contextmanager
        def fence():
            with database.session() as db:
                doc = db.scalar(select(Document).where(Document.id == document_id).with_for_update())
                if doc is None:
                    raise ValueError("文档已删除")
                db.execute(update(Document).where(Document.id == document_id).values(status=doc.status))
                job = db.scalar(select(IngestionJob).where(IngestionJob.id == job_id).with_for_update())
                if not job or job.lease_token != lease or job.status != "processing":
                    raise ValueError("任务租约已被替换")
                yield
                db.commit()

        index_chunks(indexed, write_fence=fence, progress=report)
        with database.session() as db:
            doc = db.scalar(select(Document).where(Document.id == document_id).with_for_update())
            if not doc:
                return
            owned = db.execute(update(IngestionJob).where(IngestionJob.id == job_id,
                IngestionJob.lease_token == lease, IngestionJob.status == "processing").values(progress=90, stage="发布新版本"))
            job = db.get(IngestionJob, job_id)
            if not owned.rowcount or not job:
                return
            old = doc.active_revision_id
            db.execute(delete(Chunk).where(Chunk.document_id == document_id, Chunk.revision_id == revision_id))
            for item in indexed:
                length, terms = term_rows(item["id"], item["text"])
                db.add(Chunk(id=item["id"], document_id=document_id, revision_id=revision_id, text=item["text"],
                             page=item.get("page"), ordinal=item["ordinal"], token_count=length, locator=item.get("locator", {})))
                db.flush()
                db.add_all(terms)
            revision = db.get(DocumentRevision, revision_id)
            revision.status, revision.chunk_count = "ready", len(indexed)
            doc.active_revision_id, doc.status, doc.chunk_count, doc.error = revision_id, "ready", len(indexed), None
            doc.stored_path, doc.name, doc.sha256, doc.size_bytes = (
                revision.stored_path, revision.name, revision.sha256, revision.size_bytes)
            db.execute(update(KnowledgeBase).where(KnowledgeBase.id == kb_id).values(generation=KnowledgeBase.generation + 1))
            if old and old != revision_id:
                db.add(CleanupTask(document_id=document_id, revision_id=old))
            db.execute(update(CleanupTask).where(CleanupTask.document_id == document_id,
                CleanupTask.revision_id == revision_id, CleanupTask.status == "queued").values(status="completed"))
            job.status, job.progress, job.error = "completed", 100, None
            job.stage = "已完成"
            job.lease_token = job.lease_expires_at = None
            db.commit()
    except Exception as exc:
        logger.exception("Ingestion failed: %s", job_id)
        transient = not isinstance(exc, (ValueError, FileNotFoundError))
        with database.session() as db:
            owned = db.execute(update(IngestionJob).where(IngestionJob.id == job_id,
                IngestionJob.lease_token == lease).values(updated_at=utcnow()))
            job = db.get(IngestionJob, job_id)
            if owned.rowcount and job:
                retry = transient and job.attempts < settings.worker_max_attempts
                job.status, job.error, job.progress = "queued" if retry else "failed", type(exc).__name__ + ": " + str(exc)[:500], 0
                job.available_at = utcnow() + timedelta(seconds=2 ** job.attempts) if retry else None
                job.lease_token = job.lease_expires_at = None
                doc, revision = db.get(Document, document_id), db.get(DocumentRevision, revision_id) if revision_id else None
                if doc:
                    doc.status, doc.error = ("queued" if retry else "ready" if doc.active_revision_id else "failed"), job.error
                if revision:
                    revision.status = "queued" if retry else "failed"
                db.commit()
    finally:
        done.set()
        heartbeat.join(timeout=2)


def process_evaluation(database, settings, run_id):
    from app.services.corpus import CorpusScope
    from app.services.evaluation import evaluate_cases
    lease, now = new_id(), utcnow()
    with database.session() as db:
        changed = db.execute(update(EvaluationRun).where(EvaluationRun.id == run_id,
            or_(EvaluationRun.status == "queued", (EvaluationRun.status == "running") &
                (EvaluationRun.lease_expires_at < now))).values(status="running", lease_token=lease,
                    lease_expires_at=now + timedelta(seconds=settings.model_timeout * 600 + 300)))
        db.commit()
        if not changed.rowcount:
            return
        run = db.get(EvaluationRun, run_id)
        try:
            user = db.get(User, run.user_id)
            if user.disabled:
                raise ValueError("账户已停用")
            from app.services.access import allowed_kbs
            accessible = set(db.scalars(select(KnowledgeBase.id).where(allowed_kbs(user.id))).all())
            if any(c.get("knowledge_base_id") not in accessible for case in run.cases
                   for c in case.get("recorded_citations", [])):
                raise ValueError("历史证据的权限已撤销")
            scope = CorpusScope(db, user, run.knowledge_base_ids)
            fingerprint = scope.fingerprint()
            result = evaluate_cases(run.cases, scope, run.use_ragas)
            if fingerprint != CorpusScope(db, user, run.knowledge_base_ids).fingerprint():
                raise ValueError("语料或权限在评测期间变化，请重跑以保持比较口径一致")
            db.execute(update(EvaluationRun).where(EvaluationRun.id == run_id, EvaluationRun.lease_token == lease)
                .values(status="completed", metrics=result["metrics"], results=result["results"],
                        lease_token=None, lease_expires_at=None))
            db.commit()
        except Exception as exc:
            db.rollback()
            db.execute(update(EvaluationRun).where(EvaluationRun.id == run_id, EvaluationRun.lease_token == lease)
                .values(status="failed", error=type(exc).__name__ + ": " + str(exc)[:500],
                        lease_token=None, lease_expires_at=None))
            db.commit()


def run_worker(database, settings, stop):
    from app.services.cleanup import process_cleanup
    from app.services.observability import worker_heartbeat
    def pulse():
        while not stop.is_set():
            try:
                worker_heartbeat(database)
            except Exception:
                logger.exception("Worker heartbeat unavailable")
            stop.wait(15)
    threading.Thread(target=pulse, daemon=True).start()
    while not stop.is_set():
        try:
            worker_heartbeat(database)
            now = utcnow()
            with database.session() as db:
                pending = db.scalars(select(IngestionJob.id).where(
                    or_(IngestionJob.available_at.is_(None), IngestionJob.available_at <= now),
                    or_(IngestionJob.status == "queued", (IngestionJob.status == "processing") &
                        or_(IngestionJob.lease_expires_at < now, IngestionJob.lease_expires_at.is_(None))))
                    .order_by(IngestionJob.created_at).limit(10)).all()
                cleanup = db.scalars(select(CleanupTask.id).where(CleanupTask.status.in_(["queued", "processing"]),
                    CleanupTask.available_at <= now, or_(CleanupTask.lease_expires_at.is_(None),
                    CleanupTask.lease_expires_at < now)).limit(20)).all()
                evaluations = db.scalars(select(EvaluationRun.id).where(or_(EvaluationRun.status == "queued",
                    (EvaluationRun.status == "running") & (EvaluationRun.lease_expires_at < now))).limit(1)).all()
                db.execute(delete(AdmissionSlot).where(AdmissionSlot.expires_at < now))
                db.commit()
            for task_id in cleanup:
                if not stop.is_set():
                    process_cleanup(database, settings, task_id)
            for job_id in pending:
                if not stop.is_set() and settings.worker_role in {"all", "ingestion"}:
                    process_job(database, settings, job_id)
            for run_id in evaluations:
                if not stop.is_set() and settings.worker_role in {"all", "evaluation"}:
                    process_evaluation(database, settings, run_id)
            if settings.redis_url and not pending and not evaluations and not cleanup:
                try:
                    with Redis.from_url(settings.redis_url, socket_connect_timeout=2, socket_timeout=3) as client:
                        client.brpop(settings.qdrant_collection_prefix + ":" + QUEUE_KEY,
                                     timeout=min(2.0, settings.worker_poll_seconds))
                except RedisError:
                    stop.wait(settings.worker_poll_seconds)
            else:
                stop.wait(settings.worker_poll_seconds)
        except Exception:
            logger.exception("Worker loop failed")
            stop.wait(settings.worker_poll_seconds)


def main():
    import signal
    logging.basicConfig(level=logging.INFO)
    settings, stop = get_settings(), threading.Event()
    database = Database(settings)
    database.initialize()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, lambda *_: stop.set())
    try:
        run_worker(database, settings, stop)
    finally:
        database.engine.dispose()


if __name__ == "__main__":
    main()
