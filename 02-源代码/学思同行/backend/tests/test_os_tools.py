"""Independent scheduling gold cases and rejection of malformed numerical inputs."""
import copy
import json
from pathlib import Path

import pytest

from backend.app.tools import (
    DETERMINISTIC,
    TOOL_SPECS,
    bankers_algorithm,
    simulate_cpu_scheduling,
    simulate_disk_scheduling,
)


@pytest.mark.parametrize("algorithm,order", [("FCFS", ["P1", "P2", "P3"]), ("SJF", ["P3", "P2", "P1"])])
def test_cpu_scheduling_against_independent_textbook_case(algorithm, order):
    cases_path = Path(__file__).resolve().parents[2] / "docs" / "validation" / "textbook-cases.json"
    case = next(c for c in json.loads(cases_path.read_text(encoding="utf-8"))["cases"] if c["id"] == "OS-07")
    golden = case["expected_numeric"][algorithm]
    result = simulate_cpu_scheduling([
        {"pid": "P1", "arrival": 0, "burst": 8},
        {"pid": "P2", "arrival": 0, "burst": 4},
        {"pid": "P3", "arrival": 0, "burst": 2},
    ], algorithm, "ms")
    assert result["execution_order"] == order
    assert result["completion_by_pid"] == golden["completion_by_pid"]
    assert result["mean_turnaround"] == pytest.approx(golden["mean_turnaround"])
    assert result["mean_wait"] == pytest.approx(golden["mean_wait"])
    assert result["time_unit"] == "ms" and result["preemptive"] is False
    weighted_by_pid = {p["pid"]: p["weighted_turnaround"] for p in result["processes"]}
    assert weighted_by_pid == ({"P1": 1, "P2": 3, "P3": 7} if algorithm == "FCFS" else {"P1": 1.75, "P2": 1.5, "P3": 1})
    assert result["mean_weighted_turnaround"] == pytest.approx(11 / 3 if algorithm == "FCFS" else 4.25 / 3)


def test_sjf_never_preempts_running_process_or_selects_not_yet_arrived_job():
    processes = [
        {"pid": "long", "arrival": 0, "burst": 10},
        {"pid": "short", "arrival": 1, "burst": 1},
        {"pid": "later", "arrival": 20, "burst": 2},
    ]
    snapshot = copy.deepcopy(processes)
    result = simulate_cpu_scheduling(processes, "SJF", "s")
    assert result["timeline"] == [
        {"kind": "run", "pid": "long", "start": 0, "end": 10},
        {"kind": "run", "pid": "short", "start": 10, "end": 11},
        {"kind": "idle", "pid": None, "start": 11, "end": 20},
        {"kind": "run", "pid": "later", "start": 20, "end": 22},
    ]
    assert result["mean_wait"] == 3
    assert processes == snapshot


def test_cpu_ties_use_input_queue_order_and_fractional_times_are_consistent():
    result = simulate_cpu_scheduling([
        {"pid": "B", "arrival": 0.1, "burst": 0.2},
        {"pid": "A", "arrival": 0.1, "burst": 0.2},
        {"pid": "C", "arrival": 0.2, "burst": 0.2},
    ], "SJF")
    assert result["execution_order"] == ["B", "A", "C"]
    assert result["timeline"][0] == {"kind": "idle", "pid": None, "start": 0, "end": 0.1}
    assert result["completion_by_pid"] == {"B": 0.3, "A": 0.5, "C": 0.7}
    assert result["mean_wait"] == pytest.approx(1 / 6)
    assert result["processes"][2]["weighted_turnaround"] == 2.5


@pytest.mark.parametrize("processes,algorithm,unit", [
    ([], "FCFS", "ms"),
    ([{"pid": "p", "arrival": 0, "burst": 1}] * 101, "FCFS", "ms"),
    ([{"pid": "p", "arrival": 0, "burst": 1}, {"pid": "p", "arrival": 1, "burst": 1}], "FCFS", "ms"),
    ([{"pid": " p ", "arrival": 0, "burst": 1}], "FCFS", "ms"),
    ([{"pid": "p", "arrival": -1, "burst": 1}], "FCFS", "ms"),
    ([{"pid": "p", "arrival": 0, "burst": 0}], "FCFS", "ms"),
    ([{"pid": "p", "arrival": True, "burst": 1}], "FCFS", "ms"),
    ([{"pid": "p", "arrival": 0, "burst": "1"}], "FCFS", "ms"),
    ([{"pid": "p", "arrival": float("nan"), "burst": 1}], "FCFS", "ms"),
    ([{"pid": "p", "arrival": 0, "burst": float("inf")}], "FCFS", "ms"),
    ([{"pid": "p", "arrival": 10 ** 1000, "burst": 1}], "FCFS", "ms"),
    ([{"pid": "p", "arrival": 0}], "FCFS", "ms"),
    ([{"pid": "p", "arrival": 0, "burst": 1, "priority": 5}], "FCFS", "ms"),
    ([{"pid": "p", "arrival": 0, "burst": 1}], "SRTF", "ms"),
    ([{"pid": "p", "arrival": 0, "burst": 1}], None, "ms"),
    ([{"pid": "p", "arrival": 0, "burst": 1}], "FCFS", ""),
])
def test_cpu_rejects_malformed_or_unsupported_scheduling_input(processes, algorithm, unit):
    with pytest.raises(ValueError):
        simulate_cpu_scheduling(processes, algorithm, unit)


@pytest.fixture
def banker_state():
    # Standard five-process / three-resource example; expected sequence and Work
    # transitions are written independently, rather than copied from tool output.
    return {
        "allocation": [[0, 1, 0], [2, 0, 0], [3, 0, 2], [2, 1, 1], [0, 0, 2]],
        "maximum": [[7, 5, 3], [3, 2, 2], [9, 0, 2], [2, 2, 2], [4, 3, 3]],
        "available": [3, 3, 2],
    }


def test_banker_safe_sequence_need_and_resource_conservation(banker_state):
    snapshot = copy.deepcopy(banker_state)
    result = bankers_algorithm(**banker_state)
    assert result["need"] == [[7, 4, 3], [1, 2, 2], [6, 0, 0], [0, 1, 1], [4, 3, 1]]
    assert result["safe"] and result["safe_sequence"] == ["P1", "P3", "P4", "P0", "P2"]
    assert [step["work_after"] for step in result["steps"]] == [[5, 3, 2], [7, 4, 3], [7, 4, 5], [7, 5, 5], [10, 5, 7]]
    assert result["final_work"] == result["total_resources"] == [10, 5, 7]
    assert banker_state == snapshot


def test_banker_request_granted_only_after_safe_trial_and_input_is_unchanged(banker_state):
    snapshot = copy.deepcopy(banker_state)
    result = bankers_algorithm(**banker_state, request={"pid": "P1", "resources": [1, 0, 2]})
    check = result["request_check"]
    assert check["granted"] and check["decision"] == "granted"
    assert check["candidate_available"] == [2, 3, 0]
    assert check["candidate_allocation"][1] == [3, 0, 2]
    assert check["candidate_need"][1] == [0, 2, 0]
    assert check["candidate_safety"]["safe_sequence"] == ["P1", "P3", "P4", "P0", "P2"]
    assert check["candidate_safety"]["final_work"] == result["total_resources"]
    assert banker_state == snapshot and result["available"] == snapshot["available"]


@pytest.mark.parametrize("pid,resources,decision", [
    ("P3", [1, 0, 0], "exceeds_need"),
    ("P0", [0, 4, 0], "insufficient_available"),
    ("P4", [3, 3, 0], "unsafe_wait"),
])
def test_banker_distinguishes_request_above_need_resource_shortage_and_unsafe_trial(banker_state, pid, resources, decision):
    check = bankers_algorithm(**banker_state, request={"pid": pid, "resources": resources})["request_check"]
    assert check["granted"] is False and check["decision"] == decision
    if decision == "unsafe_wait":
        assert check["candidate_safety"]["safe"] is False
        assert check["candidate_safety"]["safe_sequence"] == []
        assert len(check["candidate_safety"]["unfinished"]) == 5
        assert "不能据此断言已死锁" in check["reason"]
    else:
        assert "candidate_safety" not in check


def test_banker_zero_need_and_zero_available_can_still_be_safe():
    result = bankers_algorithm([[1], [2]], [[1], [2]], [0], process_ids=["alpha", "beta"])
    assert result["safe_sequence"] == ["alpha", "beta"]
    assert result["final_work"] == [3]
    assert bankers_algorithm([[0]], [[0]], [0], request={"pid": "P0", "resources": [0]})["request_check"]["granted"]


def test_banker_partial_sequence_is_not_reported_as_complete_safety():
    result = bankers_algorithm([[1], [1]], [[1], [3]], [0])
    assert result["safe"] is False and result["safe_sequence"] == []
    assert result["completed_prefix"] == ["P0"]
    assert result["unfinished"] == [{"pid": "P1", "need": [2], "shortage": [1]}]
    assert any("不等同于已经发生死锁" in note for note in result["assumptions"])


@pytest.mark.parametrize("allocation,maximum,available", [
    ([], [], [1]),
    ([[0]], [[0]], []),
    ([[0, 0]], [[0, 0]], [1]),
    ([[0]], [], [1]),
    ([[2]], [[1]], [1]),
    ([[-1]], [[1]], [1]),
    ([[False]], [[1]], [1]),
    ([[0.5]], [[1]], [1]),
    ([[0]], [[1]], ["1"]),
    ([[0]], [[1]], [float("inf")]),
    ([[0]] * 51, [[0]] * 51, [1]),
    ([[0] * 21], [[0] * 21], [1] * 21),
])
def test_banker_rejects_bad_dimensions_negative_counts_and_invalid_maximum(allocation, maximum, available):
    with pytest.raises(ValueError):
        bankers_algorithm(allocation, maximum, available)


@pytest.mark.parametrize("extra", [
    {"process_ids": ["P", "P", "P2", "P3", "P4"]},
    {"process_ids": ["P"]},
    {"process_ids": [["P"], "P1", "P2", "P3", "P4"]},
    {"request": {"pid": "unknown", "resources": [0, 0, 0]}},
    {"request": {"pid": "P0", "resources": [-1, 0, 0]}},
    {"request": {"pid": "P0", "resources": [0, 0]}},
    {"request": {"pid": "P0", "resources": [0, 0, 0], "execute": True}},
])
def test_banker_rejects_ambiguous_names_and_malformed_requests(banker_state, extra):
    with pytest.raises(ValueError):
        bankers_algorithm(**banker_state, **extra)


@pytest.mark.parametrize("algorithm,direction,order,total", [
    ("FCFS", None, [98, 183, 37, 122, 14, 124, 65, 67], 640),
    ("SSTF", None, [65, 67, 37, 14, 98, 122, 124, 183], 236),
    ("SCAN", "up", [65, 67, 98, 122, 124, 183, 37, 14], 331),
    ("SCAN", "down", [37, 14, 65, 67, 98, 122, 124, 183], 236),
    ("LOOK", "up", [65, 67, 98, 122, 124, 183, 37, 14], 299),
    ("LOOK", "down", [37, 14, 65, 67, 98, 122, 124, 183], 208),
])
def test_disk_scheduling_independent_gold_sequences_and_total_distance(algorithm, direction, order, total):
    requests = [98, 183, 37, 122, 14, 124, 65, 67]
    result = simulate_disk_scheduling(requests, 53, algorithm, direction, 199)
    assert result["service_order"] == order and result["total_movement_cylinders"] == total
    assert sorted(result["service_request_indices"]) == list(range(len(requests)))
    assert result["average_movement_cylinders"] == total / 8
    boundaries = [move["to"] for move in result["movements"] if move["kind"] == "boundary"]
    assert boundaries == ([199 if direction == "up" else 0] if algorithm == "SCAN" else [])
    assert "不等于毫秒" in result["unit"]
    assert requests == [98, 183, 37, 122, 14, 124, 65, 67]


def test_disk_scan_opposite_only_requests_reaches_physical_endpoint_before_turning():
    scan = simulate_disk_scheduling([30, 20], 50, "SCAN", "up", 99)
    look = simulate_disk_scheduling([30, 20], 50, "LOOK", "up")
    assert scan["service_order"] == look["service_order"] == [30, 20]
    assert scan["total_movement_cylinders"] == 128
    assert look["total_movement_cylinders"] == 30
    assert scan["movements"][0] == {"from": 50, "to": 99, "distance": 49, "kind": "boundary", "request_index": None}


def test_disk_finite_batch_stops_when_last_request_finishes_and_sstf_tie_uses_queue_order():
    scan = simulate_disk_scheduling([60, 70], 50, "SCAN", "up", 199)
    assert scan["final_head"] == 70 and scan["total_movement_cylinders"] == 20
    assert not any(move["kind"] == "boundary" for move in scan["movements"])
    assert simulate_disk_scheduling([60, 40], 50, "SSTF")["service_order"] == [60, 40]
    assert simulate_disk_scheduling([40, 60], 50, "SSTF")["service_order"] == [40, 60]


def test_disk_repeated_requests_at_head_are_served_individually_and_empty_queue_does_not_move():
    result = simulate_disk_scheduling([50, 51, 50, 50], 50)
    assert result["service_order"] == [50, 51, 50, 50] and result["service_request_indices"] == [0, 1, 2, 3]
    assert result["total_movement_cylinders"] == 2
    single_cylinder = simulate_disk_scheduling([0, 0], 0, "SCAN", "down", 0)
    assert single_cylinder["service_request_indices"] == [0, 1] and single_cylinder["total_movement_cylinders"] == 0
    empty = simulate_disk_scheduling([], 53)
    assert empty["final_head"] == 53 and empty["movements"] == [] and empty["average_movement_cylinders"] == 0


@pytest.mark.parametrize("requests,head,algorithm,direction,maximum", [
    ("1,2", 0, "FCFS", None, None),
    ([1] * 201, 0, "FCFS", None, None),
    ([-1], 0, "FCFS", None, None),
    ([1], -1, "FCFS", None, None),
    ([True], 0, "FCFS", None, None),
    ([1.5], 0, "FCFS", None, None),
    ([1], False, "FCFS", None, None),
    ([1], 0, "C-SCAN", "up", 199),
    ([1], 0, "SCAN", None, 199),
    ([1], 0, "SCAN", "up", None),
    ([1], 0, "LOOK", None, None),
    ([200], 0, "SCAN", "up", 199),
    ([1], 200, "FCFS", None, 199),
    ([1], 0, "SCAN", "right", 199),
    ([1], 0, "SCAN", ["up"], 199),
    ([1], 0, "SCAN", "up", True),
    ([1], 0, "SCAN", "up", -1),
])
def test_disk_rejects_bad_bounds_and_missing_direction_or_scan_geometry(requests, head, algorithm, direction, maximum):
    with pytest.raises(ValueError):
        simulate_disk_scheduling(requests, head, algorithm, direction, maximum)


def test_all_os_tools_are_registered_and_their_outputs_are_valid_json_evidence():
    calls = {
        "simulate_cpu_scheduling": {"processes": [{"pid": "P", "arrival": 0, "burst": 1}], "algorithm": "FCFS"},
        "bankers_algorithm": {"allocation": [[0]], "maximum": [[1]], "available": [1]},
        "simulate_disk_scheduling": {"requests": [1], "head": 0, "algorithm": "FCFS"},
    }
    specs = {spec["function"]["name"]: spec["function"] for spec in TOOL_SPECS}
    for name, args in calls.items():
        assert name in specs and set(specs[name]["parameters"]["required"]) <= args.keys()
        result = DETERMINISTIC[name](**args)
        assert result["verified_by"] and "assumptions" in result
        json.dumps(result, ensure_ascii=False, allow_nan=False)
