"""A bounded closed-interval trace, with explicit preconditions and counting."""
from __future__ import annotations

import math


def binary_search_trace(values, target, occurrence="any"):
    """Probe an ascending numeric array; never sort or change student input.

    A probe is one three-way comparison between target and values[mid]. Bounds
    checks, Python's primitive comparisons and the O(N) input validation are not
    counted as probes. Keeping a trace also uses space beyond a textbook search.
    """
    if type(values) is not list or len(values) > 256:
        raise ValueError("values必须为0–256项升序数值数组，不能自动排序")
    if type(occurrence) is not str or occurrence not in {"any", "first"}:
        raise ValueError("occurrence只支持any（遇到相等即返回）或first（最左匹配）")
    for value in [target, *values]:
        if type(value) not in {int, float} or not -1e15 <= value <= 1e15 or not math.isfinite(value):
            raise ValueError("数组元素和target必须为绝对值不超过10^15的有限数值，不接受布尔值或数值字符串")
    if any(a > b for a, b in zip(values, values[1:])):
        raise ValueError("二分查找要求数组非递减有序；当前输入无序，不能将返回未找到当作元素不存在，也不能自动排序")

    low, high, matched, steps = 0, len(values) - 1, None, []
    while low <= high:
        mid = low + (high - low) // 2
        value = values[mid]
        before = [low, high]
        if value == target:
            relation, matched = "equal", mid
            if occurrence == "any":
                steps.append({"probe": len(steps) + 1, "interval": before, "mid": mid,
                              "mid_value": value, "mid_value_relation_to_target": relation,
                              "next_interval": None, "action": "return_match"})
                break
            high = mid - 1
            action = "keep_match_search_left"
        elif value < target:
            relation, low, action = "less", mid + 1, "search_right"
        else:
            relation, high, action = "greater", mid - 1, "search_left"
        steps.append({"probe": len(steps) + 1, "interval": before, "mid": mid,
                      "mid_value": value, "mid_value_relation_to_target": relation,
                      "next_interval": [low, high], "action": action})

    return {"values": values.copy(), "target": target, "occurrence": occurrence,
            "interval_convention": "zero_based_closed", "midpoint_rule": "low + floor((high-low)/2)",
            "found": matched is not None, "index": matched,
            "first_occurrence_guaranteed": occurrence == "first",
            "comparison_count": len(steps), "comparison_unit": "one target-versus-mid-value three-way probe",
            "worst_case_probe_bound": len(values).bit_length(),
            "steps": steps,
            "termination_reason": ("match" if matched is not None and occurrence == "any" else "empty_interval"),
            "final_interval": [low, high],
            "assumptions": ["非递减升序数组，0起算闭区间，向下取整中点；无序输入拒绝，不改变或排序原数组",
                            "comparison_count只计访问中点并与目标值比较的次数，不计循环条件、O(N)有序性验证或复制开销",
                            "any遇到相等即停止，不保证重复值第一个；first继续向左寻找最左匹配",
                            "n=0时0次；n>0时该实现最坏中点探测次数上界为floor(log2(n))+1，并非大O等于精确次数",
                            "工具保存输入与轨迹会额外占用空间，不能把此工具内存开销当作未保存轨迹的教科书算法辅助空间"],
            "verified_by": "确定性闭区间二分查找逐步探测"}
