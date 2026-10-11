from __future__ import annotations

import re
import hashlib
import asyncio
import unicodedata
from collections import OrderedDict

import jieba
import numpy as np
from rank_bm25 import BM25Okapi

from .db import loads

jieba.setLogLevel(40)

# Text-layer caption identities aid precise page location; they do not imply that
# a figure was visually interpreted. Some books number a drawing by its example.
CAPTION_ID = r"(?:[A-Za-z][.\-])?\d+(?:[.\-]\d+)*[A-Za-z]?"
CAPTION_REFERENCE = re.compile(r"(?P<kind>figure|fig\.?|example|table|图|表|例题|例)\s*(?P<number>" + CAPTION_ID + r")(?![A-Za-z0-9]|[.\-]\d)", re.I)
CAPTION_LINE = re.compile(r"^\s*(?:\*\*)?(?P<kind>figure|fig\.?|example|table|图|表|例题|例)\s*(?P<number>" + CAPTION_ID + r")(?![A-Za-z0-9]|[.\-]\d)", re.I | re.M)


def caption_identities(text, headers_only=False):
    normalized = unicodedata.normalize("NFKC", text)
    found = []
    for match in (CAPTION_LINE if headers_only else CAPTION_REFERENCE).finditer(normalized):
        kind = match["kind"].lower().rstrip(".")
        kind = "figure" if kind in {"figure", "fig", "图"} else "table" if kind in {"table", "表"} else "example"
        value = (kind, match["number"].lower().replace("-", "."))
        if value not in found:
            found.append(value)
    return found


def caption_priorities(query, captions):
    priorities = {}
    for kind, number in caption_identities(query):
        exact = [i for i, values in enumerate(captions) if (kind, number) in values]
        related = []
        if not exact and kind in {"figure", "example"}:
            alias = "example" if kind == "figure" else "figure"
            related = [i for i, values in enumerate(captions) if (alias, number) in values]
        for i in exact:
            priorities[i] = max(priorities.get(i, 0), 2)
        for i in related:
            priorities[i] = max(priorities.get(i, 0), 1)
    return priorities


def tokens(text):
    lowered = text.lower()
    words = [token.strip() for token in jieba.lcut(lowered) if re.search(r"[\w\u4e00-\u9fff]", token)]
    # Chinese overlapping bigrams improve course-specific names unknown to Jieba.
    for match in re.findall(r"[\u4e00-\u9fff]+", lowered):
        words.extend(match[i:i + 2] for i in range(len(match) - 1))
    return words


def is_heading_only_chunk(row):
    text = row["text"].strip().lstrip("# ").strip()
    leaf = row["section"].split(" / ")[-1].strip().lstrip("# ").strip()
    return "\n" not in text and bool(text) and re.sub(r"\s+", "", text) == re.sub(r"\s+", "", leaf)


class Retriever:
    def __init__(self, db, provider):
        self.db, self.provider = db, provider
        self.lexical_cache = OrderedDict()

    @staticmethod
    def build_index(rows):
        corpus = [tokens(row["section"] + " " + row["text"]) for row in rows]
        term_sets = [set(words) for words in corpus]
        vectors, indices = [], []
        for index, row in enumerate(rows):
            encoded = row.pop("embedding", None)
            if encoded:
                vector = np.asarray(loads(encoded), dtype=np.float32)
                if vector.ndim != 1 or not vector.size or not np.all(np.isfinite(vector)):
                    continue
                if vectors and vector.size != vectors[0].size:
                    continue
                norm = np.linalg.norm(vector)
                if norm > 0:
                    indices.append(index)
                    vectors.append(vector / norm)
        matrix = np.stack(vectors) if vectors else np.empty((0, 0), dtype=np.float32)
        return {"rows": rows, "corpus": corpus, "term_sets": term_sets, "bm25": BM25Okapi(corpus), "indices": indices, "matrix": matrix,
                "captions": [caption_identities(row["text"], headers_only=True) for row in rows]}

    async def search(self, course_id, user_id, query, limit=6):
        # Always recheck current ownership/readiness in SQLite; cached content cannot grant access.
        documents = self.db.all("SELECT d.id,d.title,d.source_type,d.embedding_status,d.seed_hash,d.chunk_count,d.quality,d.progress "
                                "FROM documents d JOIN courses co ON co.id=d.course_id WHERE d.course_id=? "
                                "AND (d.user_id IS NULL OR d.user_id=?) AND (co.is_builtin=1 OR co.user_id=?) "
                                "AND d.status='ready' ORDER BY d.created_at,d.id", (course_id, user_id, user_id))
        cache_key = (course_id, user_id)
        if not documents:
            self.lexical_cache.pop(cache_key, None)
            return {"items": [], "method": "BM25", "notice": "本课程尚无可检索教材，请先上传并等待解析完成"}
        query_tokens = tokens(query)
        if not query_tokens:
            return {"items": [], "method": "BM25", "notice": "检索词为空"}
        # Metadata changes on atomic reingestion, completion/retry, deletion, or shared seed updates.
        signature = hashlib.sha256(repr(documents).encode()).hexdigest()
        cached = self.lexical_cache.get(cache_key)
        if cached and cached[0] == signature:
            _, index = cached
            self.lexical_cache.move_to_end(cache_key)
        else:
            rows = self.db.all("SELECT c.id,c.document_id,c.page,c.section,c.text,c.embedding,d.title,d.source_type,d.embedding_status "
                               "FROM chunks c JOIN documents d ON d.id=c.document_id JOIN courses co ON co.id=d.course_id "
                               "WHERE c.course_id=? AND (c.user_id IS NULL OR c.user_id=?) AND (co.is_builtin=1 OR co.user_id=?) "
                               "AND d.status='ready' ORDER BY d.created_at,c.page,c.rowid", (course_id, user_id, user_id))
            rows = [row for row in rows if not is_heading_only_chunk(row)]
            if not rows:
                return {"items": [], "method": "BM25", "notice": "教材尚无已就绪的正文片段；仅标题不能支持教学结论"}
            index = await asyncio.to_thread(self.build_index, rows)
            self.lexical_cache[cache_key] = (signature, index)
            self.lexical_cache.move_to_end(cache_key)
            while len(self.lexical_cache) > 8:
                self.lexical_cache.popitem(last=False)
        rows = index["rows"]
        scores = index["bm25"].get_scores(query_tokens)
        query_terms = set(query_tokens)
        overlap = [len(query_terms & words) for words in index["term_sets"]]
        lexical = sorted((i for i in range(len(rows)) if overlap[i]), key=lambda i: (float(scores[i]) + overlap[i] * 0.2), reverse=True)
        combined = {i: 1 / (60 + rank) for rank, i in enumerate(lexical, 1)}
        vector_indices = index["indices"]
        method, notice = "BM25 + 中文分词", "当前教材使用本地词法检索"
        semantic_enabled = getattr(getattr(self.provider, "settings", None), "embedding_enabled", True)
        if vector_indices and semantic_enabled:
            try:
                query_vector = np.asarray((await self.provider.embed([query]))[0], dtype=np.float32)
                if query_vector.ndim != 1 or not np.all(np.isfinite(query_vector)):
                    raise ValueError("查询向量格式无效")
                query_vector = query_vector / max(np.linalg.norm(query_vector), 1e-12)
                similarities = index["matrix"] @ query_vector
                semantic = [(row_index, float(cosine)) for row_index, cosine in zip(vector_indices, similarities) if cosine >= 0.2]
                semantic.sort(key=lambda pair: pair[1], reverse=True)
                for rank, (i, _) in enumerate(semantic, 1):
                    combined[i] = combined.get(i, 0) + 1 / (60 + rank)
                method, notice = "BM25 + 语义向量 / RRF", "混合检索通过 reciprocal rank fusion 合并"
                if any(row["embedding_status"] == "lexical_fallback" for row in rows):
                    method, notice = "BM25 + 部分语义向量 / RRF", "向量尚未完整生成；仅已完成片段参与语义检索，其余使用词法检索，可在教材管理重试补全"
            except Exception:
                method, notice = "BM25（语义服务降级）", "语义向量服务暂不可用，本轮使用中文词法检索"
        elif not semantic_enabled:
            notice = "服务器语义向量已关闭，本轮仅使用本地中文词法检索"
        elif any(row["embedding_status"] == "lexical_fallback" for row in rows):
            method, notice = "BM25（摄取向量降级）", "教材向量生成失败，本轮使用中文词法检索"
        priorities = caption_priorities(query, index["captions"])
        if priorities:
            # Explicit caption matches lead the candidate set within the already
            # authenticated course index, independent of common digit token scores.
            for i, priority in priorities.items():
                combined[i] = combined.get(i, 0) + priority
            method += " + 精确编号定位"
            notice += "；已匹配正文图/表/例题标题编号，仍需实际原页图才能识别图内内容"
        ranked = sorted(combined, key=lambda i: combined[i], reverse=True)[:max(1, min(limit, 12))]
        items = [{"chunk_id": rows[i]["id"], "document_id": rows[i]["document_id"], "title": rows[i]["title"],
                  "page": rows[i]["page"], "section": rows[i]["section"], "text": rows[i]["text"],
                  "source_type": rows[i]["source_type"], "score": round(combined[i], 5)} for i in ranked]
        return {"items": items, "method": method, "notice": notice}

