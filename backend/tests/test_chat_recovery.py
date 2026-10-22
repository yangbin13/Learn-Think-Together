"""Recovery boundaries with fake completions; no network, services or database."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from backend.app.chat_recovery import CONTINUATION_MODEL, NOTICE, recover_chat
from backend.app.grounded_reply import HiddenReasoningOutput
from backend.app.provider import ProviderError


class FakeProvider:
    def __init__(self, content="栈只允许从栈顶放入或取出，像叠放的餐盘，最后放上去的先取走。", error=None):
        self.settings = SimpleNamespace(api_key="actual-credential-DO-NOT-EXPOSE", embedding_api_key="")
        self.content, self.error, self.calls = content, error, []

    async def complete(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        if self.error:
            raise self.error
        return {"content": self.content, "model": "fake-actual-model", "tool_calls": [],
                "usage": {"prompt_tokens": 20, "completion_tokens": 30, "total_tokens": 50,
                          "private_response": "not allowed"}}


def run(provider, question="解释栈。", mode="explain", failure=None, history=None):
    emitted = []

    async def emit(event, value):
        emitted.append((event, value))

    result = asyncio.run(recover_chat(provider, "数据结构与算法图解", history or [{"role": "user", "content": question}],
                                     mode, failure or ProviderError("private failure body"), emit))
    return result, emitted


def test_ordinary_completion_replaces_failed_draft_and_cannot_certify_evidence():
    provider = FakeProvider()
    result, emitted = run(provider, history=[
        {"role": "system", "content": "old internal instruction"},
        {"role": "assistant", "content": "old failed answer[7]<think>hidden</think>", "citations": [{"id": 7}]},
        {"role": "tool", "content": "stale tool result"},
        {"role": "user", "content": "解释栈。"},
    ], failure=ProviderError("raw secret diagnostic", {"usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}}))
    assert len(provider.calls) == 1
    messages, options = provider.calls[0]
    assert options == {"tools": None, "json_mode": False, "thinking": False, "max_tokens": 1400}
    assert [item["role"] for item in messages] == ["system", "user"]
    assert "old failed" not in str(messages) and "raw secret" not in str(messages)
    assert NOTICE in result["answer"] and "餐盘" in result["answer"]
    assert result["model"] == "fake-actual-model"
    assert result["citations"] == [] and result["grounded"] is False
    assert result["usage"] == {"prompt_tokens": 23, "completion_tokens": 32, "total_tokens": 55}
    assert all(trace["status"] == "degraded" and trace["grounded"] is False for trace in result["traces"])
    assert emitted[0][0] == "answer_reset"
    assert [name for name, _ in emitted].count("token") == 1
    assert emitted[-1] == ("citations", {"items": []})
    assert "raw secret" not in json.dumps(result)


def test_socratic_correction_judges_directly_then_asks_one_question():
    provider = FakeProvider("不正确：二分查找需要有序数据，才能根据比较排除一半候选。\n\n无序数组要先做什么才能使用二分查找？")
    result, _ = run(provider, "我认为二分查找可以直接查无序数组，这样理解对吗？先纠正，再只问我一个下一步问题", "socratic")
    assert result["answer"].startswith("不正确：")
    assert result["answer"].count("？") == 1 and result["answer"].endswith("？")
    assert NOTICE in result["answer"] and result["model"] == "fake-actual-model"


def test_socratic_life_scenario_remains_answerable_and_does_not_leak_answer():
    provider = FakeProvider("把餐盘依次叠放后，每次从顶部取走一个。\n\n你会先取到最早放上的还是最后放上的餐盘？")
    result, _ = run(provider, "用生活情境引出栈，只问一个入门问题，不给答案。", "socratic")
    assert "餐盘" in result["answer"] and result["answer"].count("？") == 1
    assert result["model"] == "fake-actual-model"


@pytest.mark.parametrize("question,content", [
    ("我不理解递归，先用生活情境只问我一个入门问题，不要先给答案。",
     "有几个盒子依次套在一起，你要找到最里面的物品；每次打开当前盒子，只检查里面是否还有一个盒子。\n\n如果当前盒子里还有一个盒子，你下一步要打开哪个盒子？"),
    ("我认为二分查找可以直接查无序数组，这样理解对吗？先纠正，再只问我一个下一步问题。",
     "不正确：二分查找需要有序数据，才能根据比较排除一半候选。\n\n无序数组要先做什么才能使用二分查找？"),
])
def test_recovery_single_step_prompt_requires_concrete_operational_context(question, content):
    provider = FakeProvider(content)
    result, _ = run(provider, question, "socratic")
    prompt = provider.calls[0][0][0]["content"]
    assert "明确目标" in prompt and "学生已知、可执行的操作" in prompt
    assert "一个具体的下一动作或状态" in prompt
    assert "首轮场景短，总共70–110字优先" in prompt
    assert "有限、可操作的生活情境" in prompt and "不用无限镜子或抽象景象" in prompt
    assert "不要泛问‘你会先做什么’或‘有什么相似’" in prompt
    assert "不公布待推导答案" in prompt and result["model"] == "fake-actual-model"


@pytest.mark.parametrize("history,content,first_step", [
    ([{"role": "user", "content": "我不理解递归，先用生活情境只问我一个入门问题，不要先给答案。"}],
     "你要找到套盒最里面的物品，每次只打开当前盒子。\n\n当前盒子里还有一个盒子时，下一步要打开哪个盒子？", True),
    ([{"role": "assistant", "content": "当前盒子里还有一个盒子时，下一步要打开哪个盒子？"},
      {"role": "user", "content": "打开里面那个，再提示下一步。"}],
     "已经打开里面的盒子后，发现里面没有其他盒子。\n\n现在还需要继续打开盒子吗？", False),
    ([{"role": "user", "content": "我认为二分查找可以直接查无序数组，这样理解对吗？先纠正，再只问我一个下一步问题。"}],
     "不正确：二分查找需要有序数据，才能根据比较排除一半候选。\n\n无序数组要先做什么才能使用二分查找？", False),
])
def test_first_turn_newbie_scope_does_not_require_definition_or_concept_mapping(history, content, first_step):
    provider = FakeProvider(content)
    result, _ = run(provider, mode="socratic", history=history)
    prompt = provider.calls[0][0][0]["content"]
    assert ("首轮新手入门：" in prompt) is first_step
    if first_step:
        assert "不用待学术语的定义或结论打头" in prompt
        assert "不要求学生识别或命名待学术语" in prompt
        assert "不要求把情境映射到概念步骤" in prompt
        assert "仅凭情境即可判断一个具体下一动作、状态或停止条件" in prompt
        assert "不能要求先懂待学概念才能作答" in prompt
    assert result["model"] == "fake-actual-model" and result["answer"].count("？") == 1


def test_combined_state_and_next_action_is_rejected_as_a_model_knowledge_reply():
    draft = "剩下的是什么状态，下一步该做什么？"
    provider = FakeProvider(draft)
    result, emitted = run(provider, mode="socratic", history=[
        {"role": "assistant", "content": "取走最上面的餐盘后，下一步会取哪一个？"},
        {"role": "user", "content": "继续取最上面的，再问我下一步。"},
    ])
    prompt = provider.calls[0][0][0]["content"]
    assert "状态、下一动作、停止条件每轮只选一项" in prompt
    assert "不同时问状态和动作" in prompt
    assert len(provider.calls) == 1 and result["model"] == CONTINUATION_MODEL
    assert draft not in result["answer"] and draft not in str(emitted)
    assert result["citations"] == [] and result["grounded"] is False
    assert result["traces"][-1]["node"] == "learning_continuation"
    assert result["traces"][-1]["status"] == "degraded"
    assert "未取得可用的模型知识回答" in result["traces"][-1]["detail"]


def test_socratic_inline_code_does_not_shift_the_raw_question_boundary():
    provider = FakeProvider('先看 `find("?")` 的输入。\n\n运行 `find("?")` 时要查找哪个字符？')
    result, _ = run(provider, "先问我一个可作答的问题。", "socratic")
    assert result["model"] == "fake-actual-model"
    assert '运行 `find("?")` 时要查找哪个字符？' in result["answer"]


@pytest.mark.parametrize("error", [None, ProviderError("private fallback failure")])
def test_socratic_explicit_no_more_questions_is_respected_for_model_and_continuation(error):
    provider = FakeProvider("不正确：二分查找需要有序数据，才能根据比较排除一半候选。", error=error)
    result, _ = run(provider, "我认为二分查找可以直接查无序数组，这样理解对吗？请直接完整解释答案，不要反问。", "socratic")
    assert "？" not in result["answer"]
    assert result["answer"].startswith("不正确：" if error is None else "当前证据不足，无法判断：")
    prompt = provider.calls[0][0][0]["content"]
    assert "不追加反问" in prompt and "再问一个下一步问题" not in prompt


def test_socratic_unrequested_question_is_not_retained_after_opt_out():
    provider = FakeProvider("不正确：二分查找需要有序数据。\n\n那你要先做什么？")
    result, _ = run(provider, "我认为二分查找可以直接查无序数组，这样理解对吗？请直接完整解释答案，不要反问。", "socratic")
    assert result["model"] == CONTINUATION_MODEL and "？" not in result["answer"]


def test_current_no_more_questions_opt_out_does_not_follow_a_quoted_instruction():
    result, _ = run(FakeProvider("我们可以从餐盘的取用顺序理解栈。"), "请解释栈，不再提问。", "socratic")
    assert result["model"] == "fake-actual-model" and "？" not in result["answer"]
    result, _ = run(FakeProvider("你会先取走最早还是最后放上的餐盘？"), "“不要反问”是一个措辞例子，先问我一个入门问题。", "socratic")
    assert result["model"] == "fake-actual-model" and result["answer"].count("？") == 1


def test_recovery_contract_judges_latest_corrected_attempt_without_rewriting_user_history():
    provider = FakeProvider("正确：奶茶从队伍最前面取，餐盘从叠放的最上面取；前者先进先出，后者后进先出。")
    earlier = "我认为奶茶最后面的先取，餐盘最下面的先取，正确吗？"
    latest = "我现在认为奶茶是最前面的先取、栈是最上面的先取，这样理解对吗？请确认并再讲清楚一次，不要继续提问。"
    history = [{"role": "user", "content": earlier},
               {"role": "assistant", "content": "旧纠错回答，不作为当前依据。"},
               {"role": "user", "content": latest}]
    result, _ = run(provider, mode="socratic", history=history)
    assert len(provider.calls) == 1
    messages, _ = provider.calls[0]
    assert messages[1:] == [{"role": "user", "content": earlier}, {"role": "user", "content": latest}]
    prompt = messages[0]["content"]
    assert "只判断最后一条当前学生消息中的尝试" in prompt
    assert "不得把旧说法当作当前错误再批评" in prompt
    assert "不强行判当前尝试有错" in prompt and "当前尝试正确就明确说‘正确’" in prompt
    assert "不追加反问或下一步问题" in prompt
    assert result["answer"].endswith(provider.content) and "？" not in result["answer"]
    assert result["grounded"] is False and NOTICE in result["answer"]
    # Prompt/route contract only. The supplied fake answer is not independent
    # semantic evidence; real latest-attempt judgment is checked separately.


@pytest.mark.parametrize("draft", [
    "你先比较哪里？下一步怎么做？",
    "你先比较哪个元素，并说明理由？",
    "你会先比较哪个元素？然后请解释原因。",
    "你会先做什么？",
])
def test_invalid_socratic_correction_becomes_honest_continuation(draft):
    result, _ = run(FakeProvider(draft), "我认为二分查找可以直接查无序数组，这样理解对吗？", "socratic")
    assert result["model"] == CONTINUATION_MODEL
    assert result["answer"].startswith("当前证据不足，无法判断：")
    assert result["answer"].count("？") == 1


@pytest.mark.parametrize("draft", [
    "<think>private hidden reasoning</think>栈后进先出。",
    "analysis: private hidden reasoning\n栈后进先出。",
    "栈后进先出。\x00",
    "栈后进先出。[1]",
    "栈后进先出。【1†L1-L4】",
    '{"blocks":[{"text":"栈后进先出。","source_ids":[]}]}',
    "教材第八章采用数组实现栈。",
    "本书定义栈为后进先出结构。",
    "教材指出二分查找不能用于无序数组。",
    "根据教材，栈的操作是入栈和出栈。",
    "图中箭头从左指向右。",
    "我已经看过图片，图像有三个节点。",
    "已经使用工具核验，栈采用后进先出。",
    "核验通过，栈后进先出。",
    "密钥：sk-private-secret-token-value",
    "实际凭证actual-credential-DO-NOT-EXPOSE，栈后进先出。",
])
def test_unsafe_or_unverified_model_draft_is_never_emitted(draft):
    provider = FakeProvider(draft)
    result, emitted = run(provider)
    assert len(provider.calls) == 1 and result["model"] == CONTINUATION_MODEL
    assert result["citations"] == [] and result["grounded"] is False
    assert draft not in result["answer"]
    serialized = json.dumps([result, emitted], ensure_ascii=False)
    assert "private hidden" not in serialized and "sk-private" not in serialized
    assert "actual-credential" not in serialized


def test_failed_fallback_preserves_safe_usage_and_uses_no_fake_model_label():
    provider = FakeProvider(error=ProviderError("raw private failure", {"usage": {"prompt_tokens": 4, "total_tokens": 4}}))
    result, emitted = run(provider)
    assert len(provider.calls) == 1 and result["model"] == CONTINUATION_MODEL
    assert result["usage"]["prompt_tokens"] == 4
    assert "raw private failure" not in json.dumps([result, emitted])
    assert "模型知识回答" in result["traces"][-1]["detail"]


@pytest.mark.parametrize("draft", ["物理地址为5572。", "物理地址为五千五百七十二。", "页号=2，偏移=452。", "返回下标4，比较3次。"])
def test_unverified_exact_math_results_are_replaced_with_methods_or_clarification(draft):
    result, _ = run(FakeProvider(draft), "页大小1024字节、虚拟地址2500、页2映射帧5，求页号、偏移和物理地址。", "solve")
    assert result["model"] == CONTINUATION_MODEL
    assert "暂不认定数值结果" in result["answer"] and "5572" not in result["answer"]


def test_symbolic_math_method_can_continue_without_claiming_numeric_verification():
    provider = FakeProvider("先用虚拟地址除以页大小取整得到页号，余数是偏移；再按页表找到帧号，物理地址用帧号乘页大小加偏移。")
    result, _ = run(provider, "页大小1024字节、虚拟地址2500、页2映射帧5，求物理地址。", "solve")
    assert result["model"] == "fake-actual-model" and "页表" in result["answer"]
    assert "确定性核验" in provider.calls[0][0][0]["content"]


@pytest.mark.parametrize("status", [402, 429])
@pytest.mark.parametrize("fallback", [False, True])
def test_current_quota_or_rate_limit_propagates_without_exposing_raw_error(status, fallback):
    error = ProviderError("raw secret error", {"upstream_status": status, "reason": "upstream_http_error"})
    provider = FakeProvider(error=error if fallback else None)
    with pytest.raises(ProviderError) as caught:
        run(provider, failure=None if fallback else error)
    assert caught.value.diagnostic["upstream_status"] == status
    assert "raw secret" not in str(caught.value)
    assert len(provider.calls) == (1 if fallback else 0)
    assert ("额度" if status == 402 else "频率") in str(caught.value)


@pytest.mark.parametrize("fallback", [False, True])
def test_cancellation_propagates_and_does_not_emit_or_save_a_continuation(fallback):
    provider = FakeProvider(error=asyncio.CancelledError() if fallback else None)
    emitted = []

    async def emit(event, value):
        emitted.append((event, value))

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(recover_chat(provider, "测试课程", [{"role": "user", "content": "解释栈。"}], "explain",
                                 ProviderError("failed") if fallback else asyncio.CancelledError(), emit))
    assert not any(name in ("token", "citations") for name, _ in emitted)
    assert len(provider.calls) == (1 if fallback else 0)


def test_known_credentials_are_redacted_before_prompt_and_failure_is_only_safe_metadata():
    provider = FakeProvider()
    result, _ = run(provider, "解释栈。actual-credential-DO-NOT-EXPOSE", failure=HiddenReasoningOutput("private failed draft"))
    assert "actual-credential" not in str(provider.calls)
    assert "private failed draft" not in str(provider.calls) and "private failed draft" not in str(result)
    assert result["traces"][0]["failure_type"] == "HiddenReasoningOutput"
