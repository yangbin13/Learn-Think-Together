import pytest

from backend.app.tools import TOOL_SPECS, shortest_path


def test_negative_edge_is_rejected_by_the_runtime_tool():
    with pytest.raises(ValueError, match="Dijkstra 仅支持有限非负权重"):
        shortest_path([
            {"source": "A", "target": "B", "weight": -2},
        ], "A")


def test_shortest_path_schema_allows_negative_input_to_reach_runtime_guard():
    tool = next(item for item in TOOL_SPECS
                if item["function"]["name"] == "shortest_path")
    weight = tool["function"]["parameters"]["properties"]["edges"]["items"]["properties"]["weight"]

    assert weight == {"type": "number"}
    assert "负权" in tool["function"]["description"]
