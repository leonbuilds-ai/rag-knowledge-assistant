"""Qdrant dense search + BM25, reciprocal rank fusion and optional reranking."""
from __future__ import annotations

import hashlib
import re
import threading
import uuid
from contextlib import nullcontext
from functools import lru_cache

from qdrant_client import QdrantClient, models
from rank_bm25 import BM25Okapi

from app.config import get_settings
from app.services.embeddings import dimension, embed, tokenize

_lock = threading.RLock()


@lru_cache(maxsize=1)
def client() -> QdrantClient:
    s = get_settings()
    if s.qdrant_url:
        return QdrantClient(url=s.qdrant_url, api_key=s.qdrant_api_key or None, timeout=60)
    path = s.data_dir / "qdrant"
    path.mkdir(parents=True, exist_ok=True)
    return QdrantClient(path=str(path))


def collection() -> str:
    s = get_settings()
    identity = f"{s.embedding_provider}:{s.embedding_model}:{getattr(s, 'embedding_base_url', '')}:{dimension()}:jieba-v1"
    return s.qdrant_collection_prefix + "_" + hashlib.sha256(identity.encode()).hexdigest()[:12]


def point_id(chunk_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, chunk_id))


def index_chunks(chunks: list[dict], write_fence=None, progress=None) -> None:
    if not chunks:
        return
    with _lock if not get_settings().qdrant_url else nullcontext():
        store, name = client(), collection()
        if not store.collection_exists(name):
            store.create_collection(name, vectors_config=models.VectorParams(size=dimension(), distance=models.Distance.COSINE))
            if get_settings().qdrant_url:
                for field in ("knowledge_base_id", "document_id", "chunk_id"):
                    store.create_payload_index(name, field, field_schema=models.PayloadSchemaType.KEYWORD)
        for offset in range(0, len(chunks), 64):
            if progress:
                progress(offset, len(chunks))
            batch = chunks[offset:offset + 64]
            vectors = embed([c["text"] for c in batch])
            points = [models.PointStruct(id=point_id(c["id"]), vector=v, payload={
                "chunk_id": c["id"], "document_id": c["document_id"], "knowledge_base_id": c["knowledge_base_id"],
                "revision_id": c.get("revision_id"),
            }) for c, v in zip(batch, vectors, strict=True)]
            # Only vector writes are fenced; expensive embedding holds no SQL lock.
            with write_fence() if write_fence else nullcontext():
                store.upsert(name, points, wait=True)
            if progress:
                progress(min(offset + 64, len(chunks)), len(chunks))


def delete_document(document_id: str) -> None:
    delete_revision(document_id)


def delete_revision(document_id: str, revision_id: str | None = None) -> None:
    with _lock:
        store = client()
        # Clean old model collections too, so switching embeddings never resurrects deleted content.
        for item in store.get_collections().collections:
            if item.name.startswith(get_settings().qdrant_collection_prefix + "_"):
                conditions = [models.FieldCondition(key="document_id", match=models.MatchValue(value=document_id))]
                if revision_id:
                    version = models.FieldCondition(key="revision_id", match=models.MatchValue(value=revision_id))
                    legacy = str(uuid.uuid5(uuid.NAMESPACE_URL, "legacy-revision:" + document_id))
                    empty = models.IsEmptyCondition(is_empty=models.PayloadField(key="revision_id"))
                    conditions.append(models.Filter(should=[version, empty]) if revision_id == legacy else version)
                store.delete(item.name, models.FilterSelector(filter=models.Filter(must=conditions)), wait=True)


@lru_cache(maxsize=1)
def reranker(name: str):
    from sentence_transformers import CrossEncoder

    return CrossEncoder(name, trust_remote_code=False)


def rerank_candidates(question: str, candidates: list[dict], top_k: int, use_model: bool = True) -> list[dict]:
    from app.services.evidence import (
        clean_pdf_text,
        clean_text,
        comparison_subjects,
        coverage,
        evidence_score,
        focus,
        quantity_subject,
        requirements_question,
        simple_definition,
        useful_sentence,
    )
    candidates = [{**item, "text": clean_text(item["text"])} for item in candidates]
    candidates = [{**item, "text": clean_pdf_text(item["text"])} if item.get("document_name", "").lower().endswith(".pdf")
                  else item for item in candidates]
    candidates = [item for item in candidates if item["text"]]
    if simple_definition(question) and not requirements_question(question):
        candidates = [item for item in candidates if any(useful_sentence(question, part)
                      for part in re.split(r"(?<=[。！？])|\n", item["text"]))]
    if quantity_subject(question):
        # A value lookup needs evidence about the requested metric, not just a
        # document title or shared words such as "average" and "time".
        candidates = [item for item in candidates if coverage(question, item["text"]) >= .75]
    subjects = comparison_subjects(question)
    if not subjects and (focus(question) != question or re.search(r"什么是|是什么", question)):
        candidates = [item for item in candidates if coverage(question, item["text"] + " " +
                      item.get("document_name", "") + " " + item.get("knowledge_base_name", "")) >= .75]
    model = get_settings().reranker_model if use_model else ""
    if model and candidates:
        values = reranker(model).predict([(question, c["text"]) for c in candidates])
        for item, value in zip(candidates, values, strict=True):
            item["rerank_score"] = float(value)
        candidates.sort(key=lambda c: c["rerank_score"], reverse=True)
    else:
        candidates.sort(key=lambda c: (evidence_score(question, c), c.get("retrieval_score", 0)), reverse=True)
    # A high score on one side must not crowd out the other named subject.
    selected = []
    def topic_score(subject, item):
        sections = (item.get("locator") or {}).get("section", [])
        local = " ".join(sections[1:])
        body = re.sub(r"(?m)^#{1,6}[^\n]*(?:\n|$)", "", item["text"])
        return coverage(subject, local) if local else 0, coverage(subject, body), evidence_score(subject, item)
    for subject in subjects:
        matches = [item for item in candidates if coverage(subject, item["text"] + " " + item.get("document_name", "")) >= .5]
        if matches:
            best = max(matches, key=lambda item: topic_score(subject, item))
            if best not in selected:
                selected.append(best)
            if re.search(r"处理|解决|预防|应对", question):
                solutions = [item for item in matches if topic_score(subject, item)[0] >= .5
                    and re.search(r"解决方案|应对措施|处理方法|预防措施", " ".join((item.get("locator") or {}).get("section", [])[-1:]))]
                if solutions:
                    solution = max(solutions, key=lambda item: topic_score(subject, item))
                    if solution not in selected:
                        selected.append(solution)
    selected.extend(item for item in candidates if item not in selected)
    return selected[:top_k]


def retrieve(question: str, chunks: list[dict], top_k: int = 5, strategy: str = "hybrid", rerank_results: bool = True) -> list[dict]:
    if not chunks:
        return []
    if strategy not in {"hybrid", "dense", "bm25"}:
        raise ValueError("strategy must be hybrid, dense or bm25")
    top_k = max(1, min(top_k, 20))
    from app.services.corpus import CorpusScope
    lazy = isinstance(chunks, CorpusScope)
    by_id = {} if lazy else {c["id"]: c for c in chunks}
    limit = min(len(chunks), max(60, top_k * 6))
    dense, sparse = [], []
    dense_scores, sparse_scores = {}, {}
    if strategy != "bm25":
        vector = embed([question])[0]
        with _lock if not get_settings().qdrant_url else nullcontext():
            store, name = client(), collection()
            if not store.collection_exists(name):
                raise ValueError("当前 embedding 配置尚无索引，请重新索引文档（失败/就绪文档均可重试）")
            # Authorization is applied BEFORE vector ranking, down to the exact allowed chunk IDs.
            scope = chunks.dense_filter() if lazy else models.Filter(must=[models.HasIdCondition(has_id=[point_id(i) for i in by_id])])
            hits = store.query_points(name, query=vector, query_filter=scope, limit=min(limit * 4, 320) if lazy else limit).points
        if lazy:
            # SQL is authoritative for active revision and expiry even during a cleanup outage.
            by_id.update(chunks.hydrate([(h.payload or {}).get("chunk_id") for h in hits]))
        for hit in hits:
            cid = (hit.payload or {}).get("chunk_id")
            if cid in by_id and hit.score > 0:
                dense.append(cid)
                dense_scores[cid] = float(hit.score)
    if strategy != "dense":
        if lazy:
            sparse, sparse_scores = chunks.sparse(question, limit)
            by_id.update(chunks.hydrate(sparse))
        else:
            sparse, sparse_scores = _small_corpus_sparse(question, chunks, limit)
    dense = dense[:limit]
    fused = {}
    for ranking in (dense, sparse):
        for rank, cid in enumerate(ranking, 1):
            fused[cid] = fused.get(cid, 0.0) + 1 / (60 + rank)
    ids = sorted(fused, key=lambda cid: fused[cid], reverse=True)[:limit]
    result = [{**by_id[cid], "chunk_id": cid, "score": fused[cid], "retrieval_score": fused[cid],
               "dense_score": dense_scores.get(cid), "bm25_score": sparse_scores.get(cid)} for cid in ids]
    return rerank_candidates(question, result, top_k, use_model=rerank_results) if strategy == "hybrid" else result[:top_k]


def _small_corpus_sparse(question, chunks, limit):
    """Explicit in-memory fixture/demo adapter; production uses SQL posting lists."""
    sparse, sparse_scores = [], {}
    if chunks:
        corpus = [tokenize(c["text"]) or ["__empty__"] for c in chunks]
        # BM25 can return negative IDF for tiny corpora. Positive floor keeps useful common terms.
        bm25 = BM25Okapi(corpus)
        for word, value in bm25.idf.items():
            if value <= 0:
                bm25.idf[word] = 0.1
        scores = bm25.get_scores(tokenize(question))
        ordered = sorted(zip(chunks, scores, strict=True), key=lambda x: float(x[1]), reverse=True)
        for c, score in ordered[:limit]:
            if score > 0:
                sparse.append(c["id"])
                sparse_scores[c["id"]] = float(score)
    return sparse, sparse_scores
