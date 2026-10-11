"""Original input examples only; projection does not read PDFs or call providers."""
import pytest

from backend.app.vision_question_projection import (
    MAX_VISUAL_QUESTION_CHARACTERS, VISUAL_FILE_INDEX_POLICY,
    VisualObservationQuestionError, prepare_visual_observation_question,
)


@pytest.mark.parametrize("position", [
    "第99物理页", "物理页99", "原PDF第99页", "原PDF第99物理页", "PDF物理页99",
    "PDF第99页", "PDF的第99页", "PDF中的第99页", "物理第99页", "物理页第99页",
    "原 PDF 第 ９９ 页", "PDF physical page 99", "physical page 99", "Original PDF physical page 99",
])
def test_explicit_single_file_position_is_projected_without_rewriting_target_content(position):
    question = "请观察" + position + "的图4中上方列表与下方两个框，保留 demo(4)、[4,3,2,1] 和页框99。"
    expected = "请观察本次原页的图4中上方列表与下方两个框，保留 demo(4)、[4,3,2,1] 和页框99。"
    assert prepare_visual_observation_question(question) == expected


def test_repeated_same_page_references_are_projected_but_original_question_is_unchanged():
    question = "第99物理页中，请核对原PDF第99页的局部框图；我认为 demo(4) 使用相加，先核对局部标签。"
    saved = question
    projected = prepare_visual_observation_question(question)
    assert projected == "本次原页中，请核对本次原页的局部框图；我认为 demo(4) 使用相加，先核对局部标签。"
    assert question == saved and "99" in question


@pytest.mark.parametrize("question", [
    "请看第99页的图4。", "请看印刷第99页。", "页脚标记第86页，图99中有数组[99,4,3]。",
    "PDF印刷第86页的图有几个框？", "PDF中的印刷第86页是否有该图？",
    "请比较页框99和页框100。", "物理地址99对应哪个页框？", "原书第99页图4。",
    "PDF page 99 has a footer numbered 86.", "physical pages 99 and 100 show different figures.",
    "physical page 99.5 is an invalid decimal file position.", "physical page 99a is an identifier.",
    "请核对物理页-99。", "请核对物理页0。", "请核对物理页99999999999999。",
])
def test_ambiguous_printed_algorithm_or_invalid_positions_are_not_generically_replaced(question):
    assert prepare_visual_observation_question(question) == question


@pytest.mark.parametrize("literal", [
    "‘原PDF第99页’", '"physical page 99"', "“第99物理页”", "「物理页99」",
    "`原PDF第99页`", "```text\n物理页99\n```", "~~~text\n物理页99\n~~~",
    "> 原PDF第99页",
])
def test_quoted_and_code_file_positions_remain_source_data(literal):
    question = "这段文字仅作示例：\n" + literal + "\n请核对实际原PDF第100页的图4。"
    projected = prepare_visual_observation_question(question)
    assert literal in projected and projected.endswith("请核对实际本次原页的图4。")


@pytest.mark.parametrize("question", [
    "请比较第99物理页与第100物理页的上方图。",
    "请比较PDF physical page 99 with physical page 100.",
    "请看原PDF第99–100页，另外原PDF第101页的图也有关。",
    "请查看物理页99、100和原PDF第101页中的同一标签。",
    "请查看第99至100物理页和原PDF第101页。",
    "Compare PDF physical pages 99-100 with PDF physical page 101.",
    "物理页99与100的两个目标图需要分别核对。",
    "原PDF第99页与第100页的标签是否相同？",
    "请看原PDF第99页的图，并对比第100页中的说明。",
    "请看原PDF第99页的图；第100页另有一个目标图。",
    "请比较A.pdf的第99物理页和B.pdf的第99物理页。",
])
def test_multi_page_ranges_lists_and_unresolved_comparisons_preserve_targets_verbatim(question):
    assert prepare_visual_observation_question(question) == question


def test_single_page_projection_preserves_printed_footer_and_figure_numbers():
    question = "请看原PDF第99页的图4；页脚印刷第86页，上方显示两个标签，数组为[99,86]。"
    assert prepare_visual_observation_question(question) == (
        "请看本次原页的图4；页脚印刷第86页，上方显示两个标签，数组为[99,86]。")
    question = "请看物理页99，页脚标记是第86页（印刷号），请只记录该标记。"
    assert prepare_visual_observation_question(question) == (
        "请看本次原页，页脚标记是第86页（印刷号），请只记录该标记。")


def test_single_target_can_still_compare_two_regions_on_that_same_page():
    question = "请比较原PDF第99页上方列表与下方图4的区别。"
    assert prepare_visual_observation_question(question) == "请比较本次原页上方列表与下方图4的区别。"


def test_no_anchor_preserves_whitespace_and_punctuation_exactly():
    question = " \n请看第99页的图4。\n "
    assert prepare_visual_observation_question(question) == question


@pytest.mark.parametrize("invalid", [
    None, {}, [], 99, "", " \n ", "x" * (MAX_VISUAL_QUESTION_CHARACTERS + 1),
    "PRIVATE-QUESTION-FIXTURE\x00", "PRIVATE-QUESTION-FIXTURE\ud800",
], ids=lambda value: type(value).__name__)
def test_bad_projection_input_is_rejected_without_echoing_student_content(invalid):
    with pytest.raises(VisualObservationQuestionError) as caught:
        prepare_visual_observation_question(invalid)
    assert "PRIVATE" not in str(caught.value)


def test_provider_policy_separates_printed_pixels_from_server_file_metadata_without_index_number():
    for marker in ("印刷号不证明文件物理页", "visible_text", "服务器", "返回结果时附加", "当前像素"):
        assert marker in VISUAL_FILE_INDEX_POLICY
    assert "99" not in VISUAL_FILE_INDEX_POLICY and "86" not in VISUAL_FILE_INDEX_POLICY
