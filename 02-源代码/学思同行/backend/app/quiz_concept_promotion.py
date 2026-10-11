"""Narrow, source-bound upward mapping of generated quiz teaching labels.

This only repairs a teaching-label scope. It neither changes a question nor
certifies its answer, explanation, difficulty or source support. The caller must
still run its strict concept binder and independent question review.
"""
from __future__ import annotations

from collections import Counter

from .knowledge_map import cleaned_label, derive_textbook_tree, heading_id


def _source_chain(tree, index, chunk):
    aliases = tree.get("node_aliases", {})
    section = chunk.get("section")
    document_id = chunk.get("document_id")
    if not isinstance(section, str) or not isinstance(document_id, str):
        return []
    parts = [part.strip() for part in section.split(" / ") if part.strip()]
    node = None
    for depth in range(len(parts), 0, -1):
        original = heading_id(document_id, " / ".join(parts[:depth]))
        candidate = index.get(aliases.get(original, original))
        if candidate and candidate["kind"] in {"chapter", "section"}:
            node = candidate
            break
    chain, seen = [], set()
    while node and node["kind"] in {"chapter", "section"}:
        if node["id"] in seen:
            return []
        seen.add(node["id"])
        chain.append(node)
        node = index.get(node["parent_id"])
    return chain


def promote_question_concepts(db, course_id, user_id, all_chunks, evidence, questions, scopes):
    """Return notices for safely promoted questions, keyed by question ID.

    A generated section label must already occur in a selected source's safe
    scope. Only its nearest uniquely catalog-bound *real ancestor*, allowed by
    every selected source in the same real chapter, can replace it. Unknown,
    unrelated, ambiguous and unmeasured labels remain untouched so the existing
    strict binder retains its rejection/measurement decision.
    """
    if not isinstance(questions, list) or not isinstance(scopes, dict):
        return {}
    question_ids = [q.get("id") for q in questions if isinstance(q, dict)]
    if (len(question_ids) != len(questions)
            or any(not isinstance(key, str) or not key for key in question_ids)
            or len(set(question_ids)) != len(question_ids)):
        return {}
    documents = db.all(
        "SELECT id,title,source_type,page_count,created_at,quality FROM documents "
        "WHERE course_id=? AND status='ready' AND user_id=? ORDER BY created_at,id",
        (course_id, user_id),
    )
    owned_documents = {document["id"] for document in documents}
    tree = derive_textbook_tree(documents, all_chunks)
    index = {node["id"]: node for node in tree["nodes"]}
    headings = [node for node in tree["nodes"] if node["kind"] in {"chapter", "section"}]
    frequencies = Counter(cleaned_label(node["label"]) for node in headings)
    aliases = tree.get("node_aliases", {})
    bindings = {}
    for row in db.all("SELECT id,label FROM concepts WHERE course_id=?", (course_id,)):
        node = index.get(aliases.get(row["id"], row["id"]))
        if (node and node["kind"] in {"chapter", "section"}
                and row["label"] == cleaned_label(node["label"])
                and frequencies[row["label"]] == 1):
            bindings.setdefault(row["label"], set()).add(node["id"])
    unique_bindings = {label: next(iter(targets)) for label, targets in bindings.items() if len(targets) == 1}
    labels_by_node = {}
    for label, node_id in unique_bindings.items():
        labels_by_node.setdefault(node_id, []).append(label)
    # A single node with several catalog labels is not a unique teaching label.
    unique_labels = {node_id: labels[0] for node_id, labels in labels_by_node.items() if len(labels) == 1}

    chunks_by_id, ambiguous_chunks = {}, set()
    for chunk in all_chunks:
        chunk_id = chunk.get("id")
        if chunk_id in chunks_by_id:
            ambiguous_chunks.add(chunk_id)
        chunks_by_id[chunk_id] = chunk
    evidence_by_id, ambiguous_evidence = {}, set()
    for chunk in evidence:
        chunk_id = chunk.get("id")
        if chunk_id in evidence_by_id:
            ambiguous_evidence.add(chunk_id)
        evidence_by_id[chunk_id] = chunk

    notices = {}
    for question in questions:
        original_label, source_ids = question.get("concept"), question.get("source_ids")
        if (not isinstance(original_label, str) or not isinstance(source_ids, list)
                or not source_ids or any(not isinstance(source, str) for source in source_ids)):
            continue
        selected_scopes, source_chains, chapters, valid = [], [], set(), True
        for source_id in dict.fromkeys(source_ids):
            chunk = chunks_by_id.get(source_id)
            selected = evidence_by_id.get(source_id)
            safe = scopes.get(source_id)
            if (source_id in ambiguous_chunks or source_id in ambiguous_evidence
                    or not chunk or not selected
                    or chunk.get("document_id") not in owned_documents
                    or selected.get("document_id") != chunk.get("document_id")
                    or selected.get("section") != chunk.get("section")
                    or not isinstance(safe, list) or not safe
                    or any(not isinstance(label, str) for label in safe)):
                valid = False
                break
            chain = _source_chain(tree, index, chunk)
            chain_ids = {node["id"] for node in chain}
            chapter_ids = {node["id"] for node in chain if node["kind"] == "chapter"}
            if (len(chapter_ids) != 1
                    or any(unique_bindings.get(label) not in chain_ids for label in safe)):
                valid = False
                break
            chapters.update(chapter_ids)
            selected_scopes.append(set(safe))
            source_chains.append(chain_ids)
        if not valid or len(chapters) != 1:
            continue
        common = set.intersection(*selected_scopes)
        if (not common or original_label in common
                or not any(original_label in safe for safe in selected_scopes)):
            continue
        original_id = unique_bindings.get(original_label)
        original_node = index.get(original_id)
        if (not original_node or original_node["kind"] != "section"
                or unique_labels.get(original_id) != original_label):
            continue
        ancestor, seen = index.get(original_node["parent_id"]), {original_id}
        target_label = None
        while ancestor and ancestor["kind"] in {"chapter", "section"}:
            if ancestor["id"] in seen:
                target_label = None
                break
            seen.add(ancestor["id"])
            # Do not hide an ambiguous real common title by jumping to a
            # broader chapter. Physical-page exclusions remain represented by
            # the caller's safe scopes, but duplicate titles are not repaired.
            if (all(ancestor["id"] in chain for chain in source_chains)
                    and frequencies[cleaned_label(ancestor["label"])] != 1):
                break
            label = unique_labels.get(ancestor["id"])
            if label in common:
                target_label = label
                break
            ancestor = index.get(ancestor["parent_id"])
        if not target_label:
            continue
        question.update(concept=target_label, concept_original=original_label, concept_scope_adjusted=True)
        notices[question["id"]] = (
            f"原标签「{original_label}」仅获部分来源支持；本题按全部来源共同支持的上级「{target_label}」计入掌握度，"
            f"不表示已单独测量「{original_label}」。"
        )
    return notices
