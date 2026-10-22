import copy

import pytest

from backend.app.paging import paging_argument_shape, translate_paging_address
from backend.app.tools import DETERMINISTIC, TOOL_SPECS


@pytest.mark.parametrize("address,size,table,page,offset,frame,physical", [
    (5000, 1024, {"4": 7}, 4, 904, 7, 8072),
    (4096, 1024, {4: 7}, 4, 0, 7, 7168),
    (0, 4096, {"0": 0}, 0, 0, 0, 0),
    (4095, 4096, {0: 18}, 0, 4095, 18, 77823),
    (4096, 4096, {1: 0}, 1, 0, 0, 0),
    (106, 10, {10: 3}, 10, 6, 3, 36),
    (5, 4, [2, 0, None], 1, 1, 0, 1),
])
def test_atomic_paging_against_independent_integer_gold(address, size, table, page, offset, frame, physical):
    before = copy.deepcopy(table)
    result = translate_paging_address(address, size, table)
    assert (result["page"], result["offset"], result["frame"], result["physical_address"]) == (page, offset, frame, physical)
    assert result["present"] is True and table == before


def test_changed_address_cannot_reuse_previous_turn_offset():
    first = translate_paging_address(4904, 1024, {4: 7})
    corrected = translate_paging_address(5000, 1024, {4: 7})
    assert first["offset"] == 808 and first["physical_address"] == 7976
    assert corrected["offset"] == 904 and corrected["physical_address"] == 8072


@pytest.mark.parametrize("table,address,reason", [({0: 7}, 8192, "未提供"), ([2, None], 4096, "明确为null")])
def test_absent_page_mapping_does_not_invent_a_physical_address(table, address, reason):
    result = translate_paging_address(address, 4096, table)
    assert result["present"] is False and result["frame"] is None and result["physical_address"] is None
    assert reason in result["reason"]


@pytest.mark.parametrize("address,size,table", [
    (True, 1024, {0: 7}), (0.0, 1024, {0: 7}), ("0", 1024, {0: 7}), (-1, 1024, {0: 7}),
    (2**53, 1024, {0: 7}), (0, 0, {0: 7}), (0, True, {0: 7}), (0, 2**32 + 1, {0: 7}),
    (0, 1024, {}), (0, 1024, []), (0, 1024, {"01": 2}), (0, 1024, {-1: 2}),
    (0, 1024, {True: 2}), (0, 1024, {0: True}), (0, 1024, {0: -1}), (0, 1024, {0: 1.5}),
    (0, 1024, {0: "7"}), (0, 1024, {0: 2**32}), (0, 1024, {0: 7, "0": 8}),
    (0, 1024, [0] * 4097), (2**32, 1, {0: 7}), (0, 2**32, {0: 2**32 - 1}),
])
def test_paging_rejects_ambiguous_or_out_of_bounds_input(address, size, table):
    with pytest.raises(ValueError):
        translate_paging_address(address, size, table)


def test_atomic_paging_is_registered_as_a_real_tool():
    assert DETERMINISTIC["translate_paging_address"] is translate_paging_address
    definition = next(tool["function"] for tool in TOOL_SPECS if tool["function"]["name"] == "translate_paging_address")
    assert set(definition["parameters"]["required"]) == {"logical_address", "page_size_bytes", "page_table"}


def test_model_schema_exposes_explicit_nonempty_page_frame_records():
    definition = next(tool["function"] for tool in TOOL_SPECS if tool["function"]["name"] == "translate_paging_address")
    table = definition["parameters"]["properties"]["page_table"]
    assert table["type"] == "array" and "anyOf" not in table
    assert (table["minItems"], table["maxItems"]) == (1, 4096)
    assert table["items"]["type"] == "object"
    assert set(table["items"]["required"]) == {"page", "frame"}
    assert table["items"]["additionalProperties"] is False
    assert table["items"]["properties"]["page"]["type"] == "integer"
    assert set(table["items"]["properties"]["frame"]["type"]) == {"integer", "null"}


def test_sparse_record_table_matches_legacy_mapping_without_mutation_or_fake_missing_frame():
    table = [{"page": 4, "frame": 7}, {"page": 8, "frame": None}]
    before = copy.deepcopy(table)
    actual = translate_paging_address(5000, 1024, table)
    assert actual == translate_paging_address(5000, 1024, {4: 7, 8: None})
    assert (actual["page"], actual["offset"], actual["frame"], actual["physical_address"]) == (4, 904, 7, 8072)
    absent = translate_paging_address(8192, 1024, table)
    assert absent["present"] is False and absent["physical_address"] is None
    assert "明确为null" in absent["reason"] and table == before


@pytest.mark.parametrize("table", [
    [{"page": True, "frame": 1}], [{"page": None, "frame": 1}], [{"page": "4", "frame": 7}],
    [{"page": 4.0, "frame": 7}], [{"page": -1, "frame": 7}], [{"page": 2**32, "frame": 7}],
    [{"page": 0, "frame": True}], [{"page": 0, "frame": -1}], [{"page": 0, "frame": "7"}],
    [{"page": 0, "frame": 2**32}], [{"page": 0, "frame": 1}, {"page": 0, "frame": 2}],
    [{"page": 0, "frame": 1}, 2], [{"page": 0}], [{"frame": 1}],
    [{"page": 0, "frame": 1, "extra": "must-not-be-accepted"}],
])
def test_record_tables_keep_integer_duplicate_and_closed_field_safety(table):
    with pytest.raises(ValueError):
        translate_paging_address(0, 1024, table)


def test_invalid_argument_shape_never_contains_arbitrary_keys_or_values():
    shape = paging_argument_shape({
        "logical_address": "private-address-marker", "page_size_bytes": True,
        "page_table": [{"page": 4, "frame": 7, "private-key-name": "private-value-marker"}],
        "private-top-level-key": "private-top-level-value",
    })
    assert shape["logical_address_type"] == "string"
    assert shape["page_size_bytes_type"] == "boolean"
    assert shape["page_table_entries"] == 1
    assert shape["page_table_record_fields"] == ["frame", "page"]
    assert shape["page_table_unknown_fields"] is True
    assert "private" not in str(shape)
