"""Pure reviewer protocol regressions: no app, DB, or real model invocation."""
import json
import re

import httpx
import pytest

from backend.app.fact_review import (
    KINDS, REVIEW_OUTPUT_PROTOCOL_PROMPT, REVIEW_PROMPT, SINGLE_STEP_REVIEW_PROMPT,
    build_teaching_review_contract, parse_review, teaching_review_reminder,
    validate_socratic_question_count,
)
from backend.app.provider import ProviderError
from backend.app.socratic_reply import parse_socratic_reply


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def deny(*args, **kwargs):
        pytest.fail("real outbound HTTP is forbidden in reviewer protocol tests")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", deny)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", deny)


def test_declared_output_enum_and_real_json_examples_match_parser_whitelist():
    match = re.search(r"kind只能取以下枚举中的一个值：(\[[^\]]+\])", REVIEW_OUTPUT_PROTOCOL_PROMPT)
    assert set(json.loads(match.group(1))) == KINDS
    assert KINDS == {"numerical", "condition", "logic", "source_support", "coverage", "unsupported_inference"}
    assert "不是问题类型，不能用作kind" in REVIEW_OUTPUT_PROTOCOL_PROMPT
    assert "condition_policy" in REVIEW_OUTPUT_PROTOCOL_PROMPT
    assert REVIEW_OUTPUT_PROTOCOL_PROMPT in REVIEW_PROMPT
    for marker in ("通过示例：", "拒绝示例："):
        example, _ = json.JSONDecoder().raw_decode(REVIEW_OUTPUT_PROTOCOL_PROMPT.split(marker, 1)[1])
        assert parse_review(json.dumps(example)) == example


@pytest.mark.parametrize("kind", sorted(KINDS))
def test_valid_rejection_kind_retains_the_decision_and_does_not_approve_or_rewrite_it(kind):
    value = {"approved": False, "issues": [{"kind": kind, "detail": "存在需核对的具体问题。"}]}
    assert parse_review(json.dumps(value)) == value


@pytest.mark.parametrize("kind", [
    "condition_policy", "teaching_contract", "response_policy", "feedback_allowed",
    "evidence_admissibility_policy", "implementation_attribution_policy", "default_single_step",
    "Condition", "condition|source_support", "unknown", None, True, 1, [], {},
])
def test_unknown_or_non_string_kind_still_fails_closed_as_provider_error(kind):
    value = {"approved": False, "issues": [{"kind": kind, "detail": "题给对象不是新增事实。"}]}
    with pytest.raises(ProviderError, match="问题格式无效"):
        parse_review(json.dumps(value))


def test_actual_failure_shape_with_valid_and_contract_named_kinds_is_not_partially_accepted():
    value = {"approved": False, "issues": [
        {"kind": "source_support", "detail": "问句提及题给对象，但引用未再次列出该对象。"},
        {"kind": "coverage", "detail": "只问待观察的结构，没有先公布该结构的名称。"},
        {"kind": "condition_policy", "detail": "把输入合同字段名错误地当成问题类型。"},
    ]}
    with pytest.raises(ProviderError, match="问题格式无效"):
        parse_review(json.dumps(value))
    assert value["issues"][-1]["kind"] == "condition_policy"
    assert value["approved"] is False and len(value["issues"]) == 3


@pytest.mark.parametrize("value", [
    {"approved": True, "issues": [{"kind": "condition", "detail": "具体条件错误。"}]},
    {"approved": False, "issues": []},
    {"approved": 1, "issues": []},
    {"approved": True, "issues": [], "condition_policy": "approve"},
    {"approved": False, "issues": [{"kind": "source_support", "detail": "<think>hidden"}]},
])
def test_protocol_consistency_extra_fields_and_hidden_content_remain_rejected(value):
    with pytest.raises(ProviderError):
        parse_review(json.dumps(value))


@pytest.mark.parametrize("mode,single,feedback", [
    ("explain", False, False), ("solve", False, False),
    ("socratic", False, False), ("socratic", True, False), ("socratic", True, True),
])
def test_server_contract_carries_exact_allowed_issue_types_as_fresh_data(mode, single, feedback):
    contract = build_teaching_review_contract(mode, single_step_scope=single, feedback_allowed=feedback)
    assert set(contract["valid_issue_kinds"]) == KINDS
    contract["valid_issue_kinds"].append("condition_policy")
    fresh = build_teaching_review_contract(mode, single_step_scope=single, feedback_allowed=feedback)
    assert set(fresh["valid_issue_kinds"]) == KINDS


def test_single_step_review_separates_task_hypotheses_from_asserted_facts_and_does_not_force_full_answer():
    contract = build_teaching_review_contract("socratic", single_step_scope=True, feedback_allowed=False)
    reminder = teaching_review_reminder(contract)
    assert len(reminder) < 420
    assert "本轮没有反馈权限" in reminder and "空feedback不是拒绝理由" in reminder
    assert "待求项不用先填答案" in reminder and "合同不代表通过" in reminder
    assert "当前消息优先" in reminder and "完成即失效" in reminder
    assert "完成即失效" in contract["turn_scope_policy"]
    assert SINGLE_STEP_REVIEW_PROMPT in REVIEW_PROMPT
    for phrase in ("不要求本轮给出全答", "不是source_support或coverage缺失", "作为观察/比较对象",
                   "不等于助教肯定该假设", "待验等价关系写成已成立前提", "提前泄题仍须拒绝",
                   "启发问句本身引入的事实", "不自行切换到未请求的平均/最坏复杂度",
                   "来源只支持冲突桶查找步骤时，不能据此询问/断言O(N)条件",
                   "用户明确问‘对吗/是否正确’时，必须简短判断当前尝试",
                   "不能追问未列出的其他因素或编造因素清单",
                   "明确标为推论而非教材原话的谨慎概括"):
        assert phrase in SINGLE_STEP_REVIEW_PROMPT


def test_feedback_permission_covers_saved_answer_or_explicit_current_attempt_but_never_requires_feedback():
    contract = build_teaching_review_contract("socratic", single_step_scope=True, feedback_allowed=True)
    for value in (contract["feedback_policy"], teaching_review_reminder(contract), SINGLE_STEP_REVIEW_PROMPT):
        assert "紧邻助教引导的回答" in value and "当前用户明确提交尝试要求纠错" in value
    assert "仍可为空" in contract["feedback_policy"]
    assert "其他场景仅许可而不要求feedback非空" in SINGLE_STEP_REVIEW_PROMPT
    assert "其他情况仍可为空" in contract["feedback_policy"]
    assert "须简短判断当前尝试" in contract["feedback_policy"]
    assert "明确问‘对吗/是否正确’时须简短判断当前尝试" in teaching_review_reminder(contract)


def test_completed_turn_scoped_instruction_does_not_override_a_later_confirmation_request():
    contract = build_teaching_review_contract("socratic", single_step_scope=True, feedback_allowed=True)
    reminder = teaching_review_reminder(contract)
    assert "历史‘本轮/这次/先’" in reminder
    assert "除非明确设为持续或当前重申，不得沿用" in reminder


def test_real_counterexample_shaped_hint_protocol_keeps_only_chosen_text_reference_without_image_attachment():
    question = "题给两个词都在同一槽，是否因此可视为同一个键并覆盖旧值？先按图提示我一小步。"
    reply = {"feedback": "", "question": "按题给同槽条件，这个槽替换成什么结构来容纳两项[1]？"}
    parsed = parse_socratic_reply(json.dumps(reply), allow_feedback=False)
    validate_socratic_question_count(question, parsed["question"], "socratic")
    assert parsed == reply and "[7]" not in parsed["question"]
    for phrase in ("不将未选择的image计入", "不自动补来源编号", "文字足以支持当前结构提示",
                   "任务，也不等于声称已读出该图", "背景措辞本身不证明或增加图像引用",
                   "仍须对应当前image支持"):
        assert phrase in SINGLE_STEP_REVIEW_PROMPT
    # This checks protocol/provenance instructions, never semantic approval of
    # a teacher draft or an assumption that any real critic will obey the rule.


def test_image_model_output_is_not_treated_as_pixel_truth_when_it_conflicts_with_same_page_text():
    for phrase in ("excerpt仍是模型生成的识别文本，不是像素本身或正确性证明",
                   "不得确认复杂图的完整边表/拓扑", "不得把冲突升级为教材错误结论",
                   "审查器看不到像素时不能偏信任任一侧"):
        assert phrase in REVIEW_PROMPT
