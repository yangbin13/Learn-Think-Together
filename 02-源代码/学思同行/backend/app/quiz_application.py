"""Bounded application contracts; model statements remain reviewable claims.

No model, filesystem, database, or business-state operations occur here.
"""
from __future__ import annotations

import json
import re

from .provider import ProviderError
from .quiz_review import (
    MAX_REVIEW_CHARACTERS,
    MAX_REVIEW_QUESTIONS,
    QUIZ_REVIEW_PROMPT,
    build_quiz_difficulty_contract,
    parse_quiz_review,
)


MAX_BASIS_TEXT = 120
MAX_APPLICATION_REASON = 120
MAX_ORIGINAL_SOURCES = 4
MAX_GENERATION_SOURCES = 16
_BASIS_FIELDS = frozenset({"original_source_ids", "original_case", "new_condition", "required_operation"})
_CHECK_FLAGS = ("has_new_condition", "requires_inference", "explanation_uses_new_condition", "requires_multiple_steps")
_CHECK_FIELDS = frozenset({"question_id", "reason", *_CHECK_FLAGS})


QUIZ_APPLICATION_BASIS_PROMPT = (
    "每题另给application_basis：非应用题为null；应用题为精确对象"
    '{"original_source_ids":["本次evidence中的原例id"],"original_case":"原例情形",'
    '"new_condition":"题干实际给出的新输入/状态/约束","required_operation":"学生须执行的推演/计算/诊断"}。'
    "original_source_ids为1至4个不同真实id，三个说明各不超过120字符；不写答案、结果或解题金标。"
    "按同一原理或实现比较原例和新任务，不能仅以数字出现或未出现认定新旧；"
    "改变的条件必须体现在题干，解析实际使用它。依据须忠于本次完整evidence，不能隐去已见原例。"
    "这是待独立核对的计划说明，非应用证明；仍满足服务器difficulty_contract，挑战还需两个不同步骤或约束。"
)


QUIZ_APPLICATION_REVIEW_EXTENSION_PROMPT = (
    "另独立填写application_checks，每题精确字段question_id,has_new_condition,requires_inference,"
    "explanation_uses_new_condition,requires_multiple_steps,reason；四个判断为JSON布尔值，reason为1至120字符的具体短结论。"
    "evidence是本次完整generation_context：每题正确性与答案解析支持仍只依据该题source_ids；"
    "未引用片段仅用于核对原例差异，不能借它补救题目缺失的来源。"
    "application_basis是待核对自述，不能照抄批准；独立比较原例、题干的条件与要求操作，"
    "并核对解析是否实际推演该条件。不因某数字出现或未出现就判断新旧，也不要求原文包含自拟题。"
    "has_new_condition判断同一原理/实现下是否确有不同于来源原例的输入、状态或约束；"
    "requires_inference判断题干是否要求据此应用原理而非照读事实；"
    "explanation_uses_new_condition判断解析是否实际使用新条件展示关键结论；"
    "requires_multiple_steps判断题干是否联系两个不同步骤或约束，而非拆同一步凑数。"
    "application_basis缺失或null不计应用题；不能因difficulty标签或自述就把任一判断置true。"
    "整组通过还需前三项皆true且basis非null的题数达到服务器minimum_application_questions；"
    "挑战每题还须requires_multiple_steps=true。基础允许没有应用题。未达合同以coverage拒绝；"
    "不能改题、答案、解析或把独立判断回填生成内容。"
)


_OLD_SCHEMA_START = "只输出一个JSON对象，精确字段approved,issues,question_checks。格式："
_OLD_SCHEMA_END = "拒绝时issues必须"
_APPLICATION_REVIEW_SCHEMA = (
    "只输出一个JSON对象，精确字段approved,issues,question_checks,application_checks。格式："
    '{"approved":true,"issues":[],"question_checks":[{"question_id":"原题id",'
    '"conditions_complete":true,"source_supported":true,"options":[{"key":"A",'
    '"verdict":"correct","reason":"具体依据"},{"key":"B","verdict":"incorrect","reason":"排除依据"},'
    '{"key":"C","verdict":"incorrect","reason":"排除依据"},{"key":"D","verdict":"incorrect","reason":"排除依据"}]}],'
    '"application_checks":[{"question_id":"原题id","has_new_condition":true,"requires_inference":true,'
    '"explanation_uses_new_condition":true,"requires_multiple_steps":true,"reason":"独立核对后的具体短结论"}]}。'
)


class QuizApplicationProtocolError(ProviderError):
    """Malformed application metadata/ledger, never a semantic approval."""


def _invalid(detail):
    # Public messages expose only fixed validation facts, never raw model text.
    raise QuizApplicationProtocolError("教材练习应用协议无效，已阻止入库：" + detail)


def _short_text(value, limit):
    return (isinstance(value, str) and 1 <= len(value.strip()) <= limit
            and not re.search(r"<\s*(?:think|analysis)\b", value, re.I))


def build_application_review_prompt(base_prompt=QUIZ_REVIEW_PROMPT):
    """Replace the old response schema once, retaining its correctness rules.

    Fail closed if an upstream prompt refactor moves these explicit boundaries;
    silently appending two incompatible exact schemas would weaken the protocol.
    """
    if (not isinstance(base_prompt, str) or base_prompt.count(_OLD_SCHEMA_START) != 1
            or base_prompt.count(_OLD_SCHEMA_END) != 1):
        _invalid("完整复核提示协议边界已改变")
    before, remainder = base_prompt.split(_OLD_SCHEMA_START, 1)
    if _OLD_SCHEMA_END not in remainder:
        _invalid("完整复核提示协议边界顺序无效")
    _example, after = remainder.split(_OLD_SCHEMA_END, 1)
    return (before + _APPLICATION_REVIEW_SCHEMA + _OLD_SCHEMA_END + after
            + QUIZ_APPLICATION_REVIEW_EXTENSION_PROMPT)


def _context_ids(generation_context):
    if not isinstance(generation_context, list) or not 1 <= len(generation_context) <= MAX_GENERATION_SOURCES:
        _invalid("本次教材上下文数量无效")
    ids = set()
    for item in generation_context:
        if (not isinstance(item, dict) or not _short_text(item.get("id"), 128)
                or not isinstance(item.get("text"), str) or not item["text"].strip()
                or item["id"] in ids):
            _invalid("本次教材上下文ID、正文或去重无效")
        ids.add(item["id"])
    return ids


def _validate_basis(value, context_ids):
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != _BASIS_FIELDS:
        _invalid("应用依据字段无效")
    sources = value["original_source_ids"]
    if (not isinstance(sources, list) or not 1 <= len(sources) <= MAX_ORIGINAL_SOURCES
            or any(not isinstance(source, str) or source not in context_ids for source in sources)
            or len(set(sources)) != len(sources)):
        _invalid("应用依据的原例来源不在本次上下文或重复")
    if any(not _short_text(value[field], MAX_BASIS_TEXT)
           for field in ("original_case", "new_condition", "required_operation")):
        _invalid("应用依据说明类型或长度无效")
    # This validates types and provenance only. Same/different meaning is left
    # to independent review; numerical presence is deliberately not an oracle.
    return {"original_source_ids": list(sources),
            **{field: value[field].strip() for field in ("original_case", "new_condition", "required_operation")}}


def retain_application_bases(raw_questions, questions, generation_context):
    """Attach bounded metadata by the generator validator's unchanged order.

    The caller must first validate stems/options/answers/source_ids. This helper
    never copies a generated answer and never mutates either input list.
    Missing metadata means null, including for advanced/challenge drafts;
    independent application checks, rather than self-declaration, decide counts.
    """
    context_ids = _context_ids(generation_context)
    if (not isinstance(raw_questions, list) or not isinstance(questions, list)
            or not 1 <= len(questions) <= MAX_REVIEW_QUESTIONS or len(raw_questions) != len(questions)):
        _invalid("生成题目与已校验题目数量不一致")
    retained = []
    for raw, question in zip(raw_questions, questions):
        if (not isinstance(raw, dict) or not isinstance(question, dict)
                or not _short_text(question.get("id"), 128)
                or not isinstance(question.get("stem"), str) or raw.get("stem") != question["stem"]):
            _invalid("生成题目与已校验题目身份或顺序不一致")
        retained.append({**question, "application_basis": _validate_basis(raw.get("application_basis"), context_ids)})
    if len({question["id"] for question in retained}) != len(retained):
        _invalid("已校验题目ID重复")
    return retained


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            _invalid("JSON字段重复")
        value[key] = item
    return value


def _reject_constant(_value):
    _invalid("JSON包含非标准常量")


def _read_review(content):
    if not isinstance(content, str) or len(content) > MAX_REVIEW_CHARACTERS:
        _invalid("复核响应类型或长度无效")
    value = content.strip()
    if value.startswith("```"):
        fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", value, re.S)
        if not fenced:
            _invalid("JSON围栏不完整")
        value = fenced.group(1)
    try:
        parsed = json.loads(value, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
    except (ValueError, RecursionError):
        _invalid("复核响应不是完整JSON对象")
    if (not isinstance(parsed, dict)
            or set(parsed) != {"approved", "issues", "question_checks", "application_checks"}):
        _invalid("完整复核顶层字段无效")
    return parsed


def parse_quiz_application_review(content, questions, *, difficulty_contract, generation_context):
    """Validate the old correctness ledger and the independent application ledger.

    A well-formed approval with insufficient cognitive coverage becomes an
    explicit semantic coverage rejection eligible for the existing bounded
    regeneration. Malformed identities, booleans, references, or ledgers raise
    ProviderError and stop. No candidate content is rewritten.
    Model flags and their reasons still need source-grounded content validation;
    this function proves protocol/count consistency, not semantic correctness.
    """
    value = _read_review(content)
    correctness = parse_quiz_review(json.dumps({key: value[key] for key in
        ("approved", "issues", "question_checks")}, ensure_ascii=False), questions)
    if not isinstance(difficulty_contract, dict):
        _invalid("难度合同不等于本次服务器合同")
    expected_contract = build_quiz_difficulty_contract(difficulty_contract.get("requested_difficulty"), len(questions))
    if (set(difficulty_contract) != set(expected_contract)
            or any(type(difficulty_contract[key]) is not type(expected_contract[key]) for key in expected_contract)
            or difficulty_contract != expected_contract):
        _invalid("难度合同不等于本次服务器合同")
    context_ids = _context_ids(generation_context)
    expected = {question["id"]: question for question in questions}
    bases = {question_id: _validate_basis(question.get("application_basis"), context_ids)
             for question_id, question in expected.items()}
    checks = value["application_checks"]
    if not isinstance(checks, list) or len(checks) != len(expected):
        _invalid("应用账目未逐题完整覆盖")
    seen, application_ids, multi_step_ids = set(), [], []
    for item in checks:
        if (not isinstance(item, dict) or set(item) != _CHECK_FIELDS
                or not isinstance(item.get("question_id"), str) or item["question_id"] not in expected
                or item["question_id"] in seen
                or any(type(item[flag]) is not bool for flag in _CHECK_FLAGS)
                or not _short_text(item["reason"], MAX_APPLICATION_REASON)):
            _invalid("应用账目ID、布尔值、字段或短理由无效")
        question_id = item["question_id"]
        seen.add(question_id)
        if bases[question_id] is not None and all(item[flag] for flag in _CHECK_FLAGS[:3]):
            application_ids.append(question_id)
            if item["requires_multiple_steps"]:
                multi_step_ids.append(question_id)
    # Stable input order makes audits independent of the review output's order.
    application_ids = [question_id for question_id in expected if question_id in application_ids]
    multi_step_ids = [question_id for question_id in expected if question_id in multi_step_ids]
    minimum = difficulty_contract["minimum_application_questions"]
    challenge = difficulty_contract["requested_difficulty"] == "挑战"
    challenge_failures = [question_id for question_id in expected if question_id not in multi_step_ids] if challenge else []
    if correctness["approved"] and (len(application_ids) < minimum or challenge_failures):
        detail = f"独立应用复核仅{len(application_ids)}/{len(expected)}题满足新条件、实际应用及解析使用，服务器合同至少要求{minimum}题。"
        if any(basis is None for basis in bases.values()):
            detail += "缺失或null的应用依据未计入。"
        if challenge_failures:
            detail += f"挑战要求每题联系不同步骤或约束，{len(challenge_failures)}题未达标。"
        correctness = {**correctness, "approved": False, "issues": [{"kind": "coverage", "detail": detail}]}
    return {**correctness, "application_checks": checks,
            "application_summary": {"provenance": "server", "application_count": len(application_ids),
                "minimum_application_questions": minimum, "application_question_ids": application_ids,
                "multi_step_question_ids": multi_step_ids,
                "missing_basis_question_ids": [question_id for question_id in expected if bases[question_id] is None],
                "challenge_failure_question_ids": challenge_failures}}
