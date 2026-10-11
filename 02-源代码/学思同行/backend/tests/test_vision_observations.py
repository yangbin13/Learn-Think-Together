"""Synthetic observation protocol tests: no provider, network, database, or book data."""
import copy
import json

import pytest

from backend.app.provider import ProviderError
from backend.app.vision_observations import (
    MAX_LIMITATIONS, MAX_LIMITATION_CHARACTERS, MAX_OBSERVATION_CHARACTERS,
    MAX_POSITION_CHARACTERS, MAX_REGIONS, MAX_REGION_ITEMS, MAX_REPLY_CHARACTERS,
    MAX_TOTAL_CHARACTERS, VISION_OBSERVATIONS_PROMPT, VisionObservationProtocolError,
    parse_vision_observations, render_vision_observations,
)


def record():
    return {
        "regions": [
            {"position": "页上方文字列表", "visible_text": ["entry A: 7", "entry B: 2"],
             "visual_facts": ["两行文字排列在图框之外。"]},
            {"position": "页下方框图", "visible_text": ["frame A", "frame B"],
             "visual_facts": ["两个矩形上下排列，右侧一条向下箭头。"]},
        ],
        "limitations": ["未确认右下角模糊字符；不能由上方列表补出下方第三个图框。"],
    }


def encoded(value):
    return json.dumps(value, ensure_ascii=False)


def test_separate_regions_preserve_observations_without_fusing_diagram_and_text():
    value = record()
    parsed = parse_vision_observations(encoded(value))
    assert parsed == value and parsed is not value
    rendered = render_vision_observations(parsed)
    first, second = rendered.split("区域2｜位置：", 1)
    assert '"entry A: 7"' in first and '"frame A"' not in first
    assert '"frame A"' in second and '"entry A: 7"' not in second
    assert "区域1｜位置：页上方文字列表" in first
    assert "可见文字：" in rendered and "局部图形：" in rendered and "未确认：" in rendered
    assert "结构检查不证明像素准确" in rendered and "可能误读" in rendered
    assert "第三个图框" in rendered  # Only the explicit limitation, no invented frame.
    assert "先把区域预算用于学生指定的图" in VISION_OBSERVATIONS_PROMPT
    assert "不要把每段普通正文都设为一个区域" in VISION_OBSERVATIONS_PROMPT


def test_unreadable_image_is_a_valid_limited_observation_not_a_fake_success():
    value = {"regions": [], "limitations": ["图像模糊，本轮未能可靠读出任何字段。"]}
    assert parse_vision_observations(encoded(value)) == value
    rendered = render_vision_observations(value)
    assert "区域1" not in rendered and "未能可靠读出" in rendered
    with pytest.raises(VisionObservationProtocolError, match="未确认"):
        parse_vision_observations(encoded({"regions": [], "limitations": []}))


def test_record_can_have_only_visible_text_or_only_local_shapes():
    for text, facts in [(["A"], []), ([], ["左侧可见一个圆形。"])]:
        value = {"regions": [{"position": "左侧", "visible_text": text, "visual_facts": facts}],
                 "limitations": []}
        rendered = render_vision_observations(parse_vision_observations(encoded(value)))
        assert "本记录未列出此类观察" in rendered
        assert "不代表没有识别误差" in rendered


def test_printed_question_and_advice_remain_quoted_page_text_not_teacher_output():
    value = record()
    value["regions"][0]["visible_text"] = ["你认为这个框表示什么？", "请先计算A。"]
    rendered = render_vision_observations(parse_vision_observations(encoded(value)))
    assert '可见文字："你认为这个框表示什么？"；"请先计算A。"' in rendered


def test_whitespace_is_normalized_without_mutating_callers_record():
    value = record()
    value["regions"][0]["position"] = " 左上方 "
    value["regions"][0]["visible_text"] = [" A\nB\tC "]
    saved = copy.deepcopy(value)
    parsed = parse_vision_observations(" \n" + encoded(value) + "\n ")
    assert parsed["regions"][0]["position"] == "左上方"
    assert parsed["regions"][0]["visible_text"] == ["A\nB\tC"]
    render_vision_observations(value)
    assert value == saved


@pytest.mark.parametrize("raw", [
    None, 1, {}, [], "", "  ", "观察到两个框。", '```json\n{"regions":[],"limitations":["未读清"]}\n```',
    '前言 {"regions":[],"limitations":["未读清"]}',
    '{"regions":[],"limitations":["未读清"]} 后记',
    '{"regions":[],"limitations":["未读清"]}{"regions":[],"limitations":[]}',
    '[{"regions":[],"limitations":["未读清"]}]',
    '"{\\"regions\\":[],\\"limitations\\":[\\"未读清\\"]}"',
    '{"regions":[],"limitations":["未读清"],}',
    '{"regions":[],"limitations":[NaN]}',
    '{"regions":[],"limitations":[Infinity]}',
    '{"regions":[],"limitations":[-Infinity]}',
    "null", "true", "42", '\ufeff{"regions":[],"limitations":["未读清"]}',
    "[" * 1500 + "]" * 1500,
    " " * (MAX_REPLY_CHARACTERS + 1),
], ids=lambda value: f"{type(value).__name__}-{len(value) if isinstance(value, str) else 'type'}")
def test_non_strict_json_never_falls_back_to_unrestricted_prose(raw):
    with pytest.raises(VisionObservationProtocolError):
        parse_vision_observations(raw)


@pytest.mark.parametrize("raw", [
    '{"regions":[],"regions":[],"limitations":["未读清"]}',
    '{"regions":[],"limitations":[],"limitations":["未读清"]}',
    '{"regions":[{"position":"上方","position":"下方","visible_text":["A"],"visual_facts":[]}],"limitations":[]}',
    '{"regions":[{"position":"上方","visible_text":["A"],"visible_text":["B"],"visual_facts":[]}],"limitations":[]}',
])
def test_duplicate_fields_are_rejected_in_all_objects(raw):
    with pytest.raises(VisionObservationProtocolError, match="重复"):
        parse_vision_observations(raw)


@pytest.mark.parametrize("extra", ["judgment", "answer", "question", "reasoning", "analysis", "page", "approved"])
@pytest.mark.parametrize("where", ["top", "region"])
def test_extra_judgment_answer_or_reasoning_fields_are_not_observations(extra, where):
    value = record()
    target = value if where == "top" else value["regions"][0]
    target[extra] = "This field must never become evidence."
    with pytest.raises(VisionObservationProtocolError, match="必须且只能"):
        parse_vision_observations(encoded(value))


@pytest.mark.parametrize("field,invalid", [
    ("regions", None), ("regions", {}), ("regions", True), ("regions", [None]),
    ("regions", [{"position": "上方", "visible_text": ["A"]}]),
    ("limitations", None), ("limitations", "未读清"), ("limitations", [1]),
    ("limitations", [True]), ("limitations", [""]), ("limitations", [" \n "]),
    ("limitations", ["A"] * (MAX_LIMITATIONS + 1)),
    ("limitations", ["A" * (MAX_LIMITATION_CHARACTERS + 1)]),
])
def test_top_level_arrays_and_items_have_strict_types_and_bounds(field, invalid):
    value = record()
    value[field] = invalid
    with pytest.raises(VisionObservationProtocolError):
        parse_vision_observations(encoded(value))


@pytest.mark.parametrize("field,invalid", [
    ("position", None), ("position", True), ("position", 1), ("position", ""),
    ("position", " \n "), ("position", "上方\n下方"), ("position", "上方\t左侧"),
    ("position", "上" * (MAX_POSITION_CHARACTERS + 1)),
    ("visible_text", None), ("visible_text", "A"), ("visible_text", {}),
    ("visible_text", [True]), ("visible_text", [1]), ("visible_text", [["A"]]),
    ("visible_text", [""]), ("visible_text", ["A"] * (MAX_REGION_ITEMS + 1)),
    ("visible_text", ["A" * (MAX_OBSERVATION_CHARACTERS + 1)]),
    ("visual_facts", None), ("visual_facts", "左侧可见一个框"),
    ("visual_facts", [1]), ("visual_facts", [""]),
    ("visual_facts", ["A"] * (MAX_REGION_ITEMS + 1)),
    ("visual_facts", ["A" * (MAX_OBSERVATION_CHARACTERS + 1)]),
])
def test_region_fields_have_strict_types_single_line_positions_and_limits(field, invalid):
    value = record()
    value["regions"][0][field] = invalid
    with pytest.raises(VisionObservationProtocolError):
        parse_vision_observations(encoded(value))


def test_region_count_and_empty_region_have_independent_checks():
    value = record()
    value["regions"] = [value["regions"][0]] * (MAX_REGIONS + 1)
    with pytest.raises(VisionObservationProtocolError, match="0至8"):
        parse_vision_observations(encoded(value))
    value = {"regions": [{"position": "上方", "visible_text": [], "visual_facts": []}],
             "limitations": ["文字模糊。"]}
    with pytest.raises(VisionObservationProtocolError, match="至少"):
        parse_vision_observations(encoded(value))


def test_total_text_bound_is_independent_from_per_item_limits():
    region = {"position": "左侧", "visible_text": ["A" * MAX_OBSERVATION_CHARACTERS] * MAX_REGION_ITEMS,
              "visual_facts": ["B" * MAX_OBSERVATION_CHARACTERS] * MAX_REGION_ITEMS}
    value = {"regions": [region, region], "limitations": []}
    assert len(encoded(value)) < MAX_REPLY_CHARACTERS
    assert sum(len(item) for part in value["regions"]
               for item in part["visible_text"] + part["visual_facts"]) > MAX_TOTAL_CHARACTERS
    with pytest.raises(VisionObservationProtocolError, match="总文字"):
        parse_vision_observations(encoded(value))


@pytest.mark.parametrize("bad", [
    "\x00", "\x08", "\x0b", "\x0c", "\x1f", "\x7f", "\ud800", "\udfff",
    "<think>PRIVATE-CONTENT-91</think>", "<ANALYSIS>PRIVATE-CONTENT-91</ANALYSIS>",
    "reasoning: PRIVATE-CONTENT-91", "思维链：PRIVATE-CONTENT-91",
])
@pytest.mark.parametrize("field", ["position", "visible_text", "visual_facts", "limitations"])
def test_control_characters_surrogates_and_hidden_content_are_rejected_without_echo(bad, field):
    value = record()
    if field == "position":
        value["regions"][0][field] = bad
    elif field == "limitations":
        value[field] = [bad]
    else:
        value["regions"][0][field] = [bad]
    with pytest.raises(VisionObservationProtocolError) as caught:
        parse_vision_observations(json.dumps(value, ensure_ascii=True))
    assert "PRIVATE-CONTENT-91" not in str(caught.value)
    assert isinstance(caught.value, ProviderError)


@pytest.mark.parametrize("contamination", [
    "你的理解不正确。", "学生的答案正确。", "下一步你先观察右边。",
    "请先计算这个值。", "你认为哪个框先恢复？", "这里有几个框?",
    "Your answer is incorrect.", "Next you should calculate the total.",
])
@pytest.mark.parametrize("field", ["position", "visual_facts", "limitations"])
def test_observation_fields_reject_obvious_teaching_leakage(contamination, field):
    value = record()
    if field == "position":
        value["regions"][0][field] = contamination
    elif field == "limitations":
        value[field] = [contamination]
    else:
        value["regions"][0][field] = [contamination]
    with pytest.raises(VisionObservationProtocolError, match="教学"):
        parse_vision_observations(encoded(value))


def test_renderer_revalidates_manually_constructed_records():
    value = record()
    value["regions"][1]["judgment"] = "correct"
    with pytest.raises(VisionObservationProtocolError):
        render_vision_observations(value)


def test_prompt_separates_region_observations_and_forbids_tutor_output_or_pixel_proof():
    for marker in ("不判断学生正确性", "不提出下一步问题", "上方返回值列表不能补成下方栈图",
                   "judgment", "answer", "question", "reasoning", "不能可靠确认", "不代表观察已被像素核实"):
        assert marker in VISION_OBSERVATIONS_PROMPT
