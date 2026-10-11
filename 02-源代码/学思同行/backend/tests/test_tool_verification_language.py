"""Prohibitions/requests are not executions; a later real claim still needs proof."""
import pytest

from backend.app.fact_review import validate_tool_verification_claims
from backend.app.provider import ProviderError


PROHIBITION = '严禁将工具的拒绝响应（Error/Refusal）解释为“验证通过”或“结果为负无穷”'


@pytest.mark.parametrize("prefix", ["", "- ", "* ", "+ ", "1. ", "2) ", "### ", "- 1. ", "- **"])
def test_real_refusal_warning_with_markdown_prefix_is_not_a_success_claim(prefix):
    suffix = "**" if prefix.endswith("**") else ""
    validate_tool_verification_claims(prefix + PROHIBITION + suffix, [], {"shortest_path"}, {"shortest_path"})


@pytest.mark.parametrize("answer", [
    "禁止声称工具验证通过。", "不得将工具拒绝称作验证通过。",
    "切勿把工具返回Error称为验证通过。", "不要把工具错误称为验证通过。",
    "不应声称经工具验证。", "本轮未执行工具调用，不能声称工具验证通过。",
    "工具并未验证通过。", "如果工具核验通过，才可给出结果。",
    "请先调用shortest_path工具核验。", "只有通过工具核验后，才能声称经工具验证。",
    "若工具返回拒绝，则不应说工具已验证通过。", "请注意不要把工具拒绝称为验证通过。",
    "如果已有工具核验结果，则可以说经工具验证。",
    "未执行工具调用。", "无需使用计算器验证这个概念。",
])
def test_denials_prohibitions_and_conditional_requests_do_not_assert_execution(answer):
    validate_tool_verification_claims(answer, [], {"shortest_path"}, {"shortest_path"})


@pytest.mark.parametrize("answer", [
    "空间复杂度在内存受限时尤为重要，应使用性能测试工具验证调优效果。",
    "性能调优应使用工具核验。",
    "这个结果需要使用计算器核验。",
    "精确步数可以通过工具验证。",
    "在内存受限时，可通过性能测试工具验证优化效果。",
])
def test_embedded_recommendation_does_not_claim_completed_execution(answer):
    validate_tool_verification_claims(answer, [])


@pytest.mark.parametrize("answer", [
    "性能调优应使用工具核验。工具已经验证通过。",
    "这个结果需要使用工具核验且已经验证通过。",
    "精确步数可以通过工具验证且结果显示为四次。",
    "这个结论应使用工具核验且已确认无误。",
    "该步骤需要通过工具核验（已验证）。",
])
def test_embedded_recommendation_does_not_exempt_completed_results(answer):
    with pytest.raises(ProviderError, match="成功执行结果"):
        validate_tool_verification_claims(answer, [])


@pytest.mark.parametrize("separator", ["。", "；", "\n", "，但是", "然而", "不过", ". "])
def test_prohibition_never_exempts_a_later_completed_verification_claim(separator):
    with pytest.raises(ProviderError, match="成功执行结果"):
        validate_tool_verification_claims(PROHIBITION + separator + "工具已经验证通过。", [])


@pytest.mark.parametrize("answer", [
    "- 严禁猜测工具结果。\n- 工具已经验证通过。",
    "严禁猜测但工具已经验证通过。", "本轮未执行工具。使用工具核验结果如下。",
    "请注意：工具已经验证通过。", "请注意工具已经验证通过。",
    "- 请注意：工具已经验证通过。", "工具已经验证通过。",
    "计算器已完成核验。", "经工具验证，结果如下。",
    "只有工具已经验证通过。", "只有计算器已经完成核验。",
    "工具尚未执行，然而这个结果已经通过工具验证。",
])
def test_unexecuted_success_claim_is_not_turned_into_a_request_by_other_words(answer):
    with pytest.raises(ProviderError, match="成功执行结果"):
        validate_tool_verification_claims(answer, [])


def test_prohibition_does_not_let_an_unrelated_success_satisfy_the_requested_atomic_tool():
    unrelated = [{"tool": "calculate", "arguments": {"expression": "2+3"}, "result": {"result": 5}}]
    warning = "- 不得声称未执行的分页地址转换工具已经验证通过。"
    validate_tool_verification_claims(warning, unrelated, {"translate_paging_address"}, {"translate_paging_address"})
    with pytest.raises(ProviderError, match="translate_paging_address"):
        validate_tool_verification_claims(warning + "\n- 工具已经验证通过。", unrelated,
                                          {"translate_paging_address"}, {"translate_paging_address"})
    validate_tool_verification_claims("通过translate_paging_address工具核验结果如下。",
                                     [{"tool": "translate_paging_address", "result": {"present": True}}],
                                     requested_tools={"translate_paging_address"})
