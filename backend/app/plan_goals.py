"""Bounded, evidence-based matching of separate study intents to existing concepts."""
from __future__ import annotations

import re


def normalized_label(value):
    value = re.sub(r"^(?:第[一二三四五六七八九十百0-9]+[章节]\s*|\d+(?:\.\d+)*[.、]?\s*)", "", value)
    return re.sub(r"\s+", "", value).casefold()


def goal_queries(goal):
    # Preserve subject words; separate independently requested topics instead of blending them.
    parts = re.split(r"[，,；;。！？!?\n]|以及|并且|同时|与|和|\band\b|\bthen\b", goal, flags=re.I)
    generic = r"(?:本次|当前|这次|之前|以前|我的|本人|课程|教材|中|中的|里|相关|学习|练习|复习|纠正|错题|薄弱点|薄弱概念|薄弱知识点|弱项|不足|疑问|知识点|所有|全部|的|\s)+"
    useful = [part.strip() for part in parts if len(part.strip()) >= 2 and not re.fullmatch(generic, part.strip())]
    return list(dict.fromkeys(useful))[:4]


async def match_plan_goals(retriever, course_id, user_id, goal, labels):
    if not goal:
        return [], None
    labels = list(dict.fromkeys(labels))
    direct = sorted((label for label in labels if label.casefold() in goal.casefold()), key=lambda label: goal.casefold().find(label.casefold()))
    by_normalized = {normalized_label(label): label for label in labels}
    groups, methods, failures = [], [], 0
    queries = goal_queries(goal)
    for query in queries:
        try:
            result = await retriever.search(course_id, user_id, query, 6)
            methods.append(result["method"])
            matches = []
            for item in result["items"]:
                for part in reversed(item["section"].split(" / ")):
                    label = by_normalized.get(normalized_label(part))
                    if label:
                        if label not in matches:
                            matches.append(label)
                        break  # A hit represents its most specific existing concept, not every ancestor.
            groups.append(matches)
        except Exception:
            failures += 1
    matched = direct.copy()
    # One primary concept per intent: lower-ranked neighboring sections are not new goals.
    for group in groups:
        if group and group[0] not in matched:
            matched.append(group[0])
    matched = matched[:6]
    if failures and not methods:
        notice = "目标检索暂不可用；保留能直接匹配的现有概念，按真实薄弱点与课程结构生成离线规则计划。"
    elif matched:
        notice = f"将目标拆为 {len(queries)} 个独立学习意图，通过本人教材{'、'.join(dict.fromkeys(methods)) or '直接标签匹配'}匹配已有概念，按各意图轮流选择。"
        if failures:
            notice += f"其中 {failures} 个意图检索暂不可用，相关部分按离线规则安排。"
    else:
        notice = "目标未匹配已提取的教材概念，按真实薄弱点与现有课程结构安排。"
    return matched, notice
