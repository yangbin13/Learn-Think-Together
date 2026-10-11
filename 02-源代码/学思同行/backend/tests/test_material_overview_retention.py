from backend.app.agent import _include_material_evidence


def item(key, text=None):
    return {"chunk_id": key, "section": "正文", "text": text or f"实际正文片段 {key}。"}


def test_repeated_chapter_searches_do_not_evict_the_book_overview():
    anchors = [item(f"overview-{i}") for i in range(6)]
    pool = {row["chunk_id"]: row for row in anchors}
    pinned = set(pool)
    for batch in range(4):
        _include_material_evidence(pool, [item(f"chapter-{batch}-{i}") for i in range(8)], pinned)
    assert len(pool) == 18
    assert pinned <= pool.keys()
    assert all(f"chapter-3-{i}" in pool for i in range(8))
    assert "chapter-0-0" not in pool


def test_precision_refresh_is_retained_and_title_only_hits_do_not_displace_sources():
    pool = {f"source-{i}": item(f"source-{i}") for i in range(18)}
    _include_material_evidence(pool, [item("source-0", "精读补充后的真实正文。"), item("title", "正文")])
    assert len(pool) == 18
    assert pool["source-0"]["text"] == "精读补充后的真实正文。"
    assert "title" not in pool
    _include_material_evidence(pool, [item("new")])
    assert "source-0" in pool and "source-1" not in pool
