"""Original labels only: source-location scopes without model, network, or DB."""
import copy
import re

import pytest

from backend.app.citation_locations import validate_citation_locations
from backend.app.grounded_reply import render_grounded_reply
from backend.app.provider import ProviderError


def source(source_id, page, kind="physical", **fields):
    return {"id": source_id, "page": page, "page_kind": kind,
            "source_type": "uploaded_pdf" if kind == "physical" else "docx", **fields}


def answer(blocks):
    return render_grounded_reply({"blocks": blocks})


def test_long_grounded_second_block_does_not_bind_its_location_to_previous_block_marker():
    citations = [source(3, 98), source(8, 99), source(5, 99, evidence_kind="image")]
    blocks = [{"text": "原创反馈X。", "source_ids": [3]},
              {"text": "物理页99展示局部标签Y。" + "这是一项原创局部描述。" * 15,
               "source_ids": [8, 5]}]
    rendered = answer(blocks)
    location = re.search(r"物理页99", rendered)
    nearest = min(re.finditer(r"\[(\d+)\]", rendered),
                  key=lambda marker: min(abs(marker.end() - location.start()),
                                         abs(location.end() - marker.start())))
    assert nearest.group(1) == "3"  # Reproduces the old global-nearest mistake.
    assert validate_citation_locations(rendered, citations, grounded_blocks=blocks) is None


def test_true_grounded_mismatch_cannot_borrow_from_previous_block_or_unselected_sources():
    citations = [source(1, 99), source(2, 98)]
    blocks = [{"text": "局部说明X。", "source_ids": [1]},
              {"text": "物理页99展示标签Y。", "source_ids": [2]}]
    with pytest.raises(ProviderError, match="出处位置与实际引用不一致"):
        validate_citation_locations(answer(blocks), citations, grounded_blocks=blocks)
    blocks = [{"text": "物理页99展示标签Y。", "source_ids": []}]
    with pytest.raises(ProviderError):
        validate_citation_locations(answer(blocks), citations, grounded_blocks=blocks)


def test_grounded_location_matches_any_actual_selected_source_not_nearest_or_first():
    citations = [source(1, 98), source(2, 99), source(3, 100)]
    blocks = [{"text": "物理页99展示标签Y。", "source_ids": [1, 2, 3]}]
    assert validate_citation_locations(answer(blocks), citations, grounded_blocks=blocks) is None


@pytest.mark.parametrize("range_text", ["98–100", "98-100", "98—100", "98至100", "98到100"])
def test_grounded_range_requires_each_page_in_this_block_selected_sources(range_text):
    citations = [source(1, 98), source(2, 99), source(3, 100)]
    blocks = [{"text": "物理页" + range_text + "分别展示三个原创标签。", "source_ids": [1, 2, 3]}]
    assert validate_citation_locations(answer(blocks), citations, grounded_blocks=blocks) is None
    blocks[0]["source_ids"] = [1, 3]
    with pytest.raises(ProviderError):
        validate_citation_locations(answer(blocks), citations, grounded_blocks=blocks)


def test_grounded_range_cannot_use_wrong_kind_or_other_block_for_missing_middle_page():
    citations = [source(1, 98), source(2, 99, "logical"), source(3, 100), source(4, 99)]
    blocks = [{"text": "局部说明X。", "source_ids": [4]},
              {"text": "物理页98–100展示三个原创标签。", "source_ids": [1, 2, 3]}]
    with pytest.raises(ProviderError):
        validate_citation_locations(answer(blocks), citations, grounded_blocks=blocks)


@pytest.mark.parametrize("location", ["物理页0", "物理页100–98", "物理页1–999999999", "物理页999999999999999"])
def test_invalid_or_unbounded_ranges_fail_without_expanding_the_span(location):
    blocks = [{"text": location + "展示标签X。", "source_ids": [1]}]
    with pytest.raises(ProviderError):
        validate_citation_locations(answer(blocks), [source(1, 99)], grounded_blocks=blocks)


@pytest.mark.parametrize("location", ["物理页99", "物理页99–99", "物理页９９"])
def test_exact_and_equal_end_ranges_use_actual_physical_page(location):
    blocks = [{"text": location + "展示标签X。", "source_ids": [1]}]
    assert validate_citation_locations(answer(blocks), [source(1, 99)], grounded_blocks=blocks) is None


def test_physical_and_logical_locations_are_distinct_even_with_same_number():
    citations = [source(1, 9), source(2, 9, "logical")]
    blocks = [{"text": "物理页9展示标签X，逻辑段9解释标签Y。", "source_ids": [1, 2]}]
    assert validate_citation_locations(answer(blocks), citations, grounded_blocks=blocks) is None
    blocks[0]["source_ids"] = [2]
    with pytest.raises(ProviderError):
        validate_citation_locations(answer(blocks), citations, grounded_blocks=blocks)


@pytest.mark.parametrize("source_type,location", [
    ("pdf", "物理页9"), ("uploaded_pdf", "物理页9"), ("open_textbook", "物理页9"),
    ("docx", "逻辑段9"), ("txt", "逻辑段9"), ("md", "逻辑段9"),
])
def test_absent_page_kind_retains_existing_source_type_classifier(source_type, location):
    citations = [{"id": 1, "page": 9, "source_type": source_type}]
    blocks = [{"text": location + "展示标签X。", "source_ids": [1]}]
    assert validate_citation_locations(answer(blocks), citations, grounded_blocks=blocks) is None


@pytest.mark.parametrize("invalid_source", [
    {"id": 1, "source_type": "pdf"},
    {"id": 1, "page": 9, "page_kind": "unknown", "source_type": "pdf"},
    {"id": 1, "page": 9, "page_kind": None, "source_type": "pdf"},
    {"id": 1, "page": 9, "page_kind": [], "source_type": "pdf"},
    {"id": 1, "page": 9, "source_type": []},
    {"id": 1, "page": 0, "page_kind": "physical", "source_type": "pdf"},
    {"id": 1, "page": True, "page_kind": "physical", "source_type": "pdf"},
    {"id": 1, "page": [], "page_kind": "physical", "source_type": "pdf"},
])
def test_missing_page_unknown_kind_or_nonpositive_page_cannot_validate_a_location(invalid_source):
    blocks = [{"text": "物理页9展示标签X。", "source_ids": [1]}]
    with pytest.raises(ProviderError):
        validate_citation_locations(answer(blocks), [invalid_source], grounded_blocks=blocks)
    with pytest.raises(ProviderError):
        validate_citation_locations("物理页9展示标签X。[1]", [invalid_source])


@pytest.mark.parametrize("tool", [
    {"id": 1, "page": 0, "page_kind": "calculation", "source_type": "verified_computation"},
    {"id": 1, "page": 0, "document_id": "tool:original-fixture", "source_type": "tool"},
])
def test_tool_page_zero_cannot_support_grounded_textbook_location(tool):
    blocks = [{"text": "物理页9展示标签X。", "source_ids": [1]}]
    with pytest.raises(ProviderError):
        validate_citation_locations(answer(blocks), [tool], grounded_blocks=blocks)
    with pytest.raises(ProviderError):
        validate_citation_locations("物理页9展示标签X。[1]", [tool])
    blocks[0]["source_ids"] = [1, 2]
    assert validate_citation_locations(answer(blocks), [tool, source(2, 9)], grounded_blocks=blocks) is None


def test_legacy_nearest_marker_never_crosses_a_paragraph_boundary():
    citations = [source(1, 98), source(2, 99)]
    rendered = "原创反馈X。[1]\n\n物理页99展示标签Y。" + "这是一项原创局部描述。" * 15 + "[2]"
    assert validate_citation_locations(rendered, citations) is None
    assert validate_citation_locations("原创反馈X。[1]\n\n物理页99展示标签Y。", citations) is None
    with pytest.raises(ProviderError):
        validate_citation_locations("原创反馈X。[2]\n\n物理页99展示标签Y。[1]", citations)


def test_legacy_48_character_guard_and_nearest_source_rules_are_retained():
    citations = [source(1, 98), source(2, 99)]
    with pytest.raises(ProviderError):
        validate_citation_locations("物理页99" + "X" * 48 + "[1]", citations)
    assert validate_citation_locations("物理页99" + "X" * 49 + "[1]", citations) is None
    assert validate_citation_locations("物理页99展示标签Y。[2]", citations) is None
    with pytest.raises(ProviderError):
        validate_citation_locations("物理页99展示标签Y。[1]" + "X" * 30 + "[2]", citations)
    with pytest.raises(ProviderError):
        validate_citation_locations("物理页98–99展示两个标签。[1][2]", citations)


@pytest.mark.parametrize("newline", ["\n\n", "\r\n\r\n", "\n \t\n"])
def test_paragraph_scope_includes_standard_blank_line_variants(newline):
    assert validate_citation_locations("原创说明。[1]" + newline + "物理页99展示标签Y。",
                                       [source(1, 98)]) is None


def test_code_locations_and_numeric_containers_are_not_source_claims():
    citations = [source(1, 98)]
    assert validate_citation_locations('`物理页99 [1]`只是代码字符串。', citations) is None
    assert validate_citation_locations('数组：[1]。\n\n物理页99展示标签Y。', citations) is None
    blocks = [{"text": '```text\n物理页99 [1]\n```', "source_ids": [1]}]
    assert validate_citation_locations(answer(blocks), citations, grounded_blocks=blocks) is None
    rendered = "原创说明。[1]\n\n```text\n\n物理页99 [1]\n\n```\n\n物理页99展示标签Y。"
    assert validate_citation_locations(rendered, citations) is None


def test_input_text_and_source_bindings_are_never_rewritten():
    citations = [source(1, 99)]
    blocks = [{"text": "物理页99展示标签Y。", "source_ids": [1]}]
    original = copy.deepcopy((citations, blocks))
    rendered = answer(blocks)
    assert validate_citation_locations(rendered, citations, grounded_blocks=blocks) is None
    assert (citations, blocks) == original and answer(blocks) == rendered


def test_safe_mismatch_diagnostics_do_not_echo_private_text_or_identifiers():
    citations = [source(1, 98, document_id="PRIVATE-ID-FIXTURE", title="PRIVATE-TITLE-FIXTURE")]
    blocks = [{"text": "物理页99：PRIVATE-EXCERPT-FIXTURE。", "source_ids": [1]}]
    with pytest.raises(ProviderError) as caught:
        validate_citation_locations(answer(blocks), citations, grounded_blocks=blocks)
    assert "PRIVATE" not in str(caught.value)


@pytest.mark.parametrize("blocks", [
    [], {}, "blocks", [None], [{"text": "物理页9"}],
    [{"text": "物理页9", "source_ids": [], "extra": True}],
    [{"text": None, "source_ids": []}], [{"text": " ", "source_ids": []}],
    [{"text": "x" * 8193, "source_ids": []}],
    [{"text": "物理页9", "source_ids": [True]}],
    [{"text": "物理页9", "source_ids": [[]]}],
    [{"text": "物理页9", "source_ids": [1, 1]}],
    [{"text": "物理页9", "source_ids": [2]}],
    [{"text": "物理页9", "source_ids": list(range(1, 34))}],
    [{"text": "物理页9", "source_ids": [1]}] * 33,
    [{"text": "x" * 8192, "source_ids": []}] * 5,
], ids=lambda value: type(value).__name__)
def test_bad_grounded_scope_metadata_is_a_safe_provider_error(blocks):
    with pytest.raises(ProviderError):
        validate_citation_locations("物理页9展示标签X。[1]", [source(1, 9)], grounded_blocks=blocks)


@pytest.mark.parametrize("citations", [
    None, {}, [None], [{}], [{"id": True}], [{"id": []}], [{"id": 0}],
    [source(1, 9), source(1, 10)],
], ids=lambda value: type(value).__name__)
def test_bad_citation_index_metadata_does_not_escape_as_typeerror(citations):
    with pytest.raises(ProviderError):
        validate_citation_locations("物理页9展示标签X。[1]", citations)


def test_numeric_string_metadata_and_fullwidth_legacy_markers_remain_compatible():
    assert validate_citation_locations("物理页９展示标签X。【１】",
                                       [{"id": "1", "page": "9", "source_type": "pdf"}]) is None
