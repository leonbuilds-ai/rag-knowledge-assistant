import json

import pytest
from app.config import Settings
from app.services.evidence import clean_text, comparison_subjects, definition_subject, retrieval_queries, terms
from app.services.markdown import markdown_chunks
from app.services.verification import factual_claims, normalize_answer, verify_answer


def test_completion_rule_cannot_turn_example_values_into_an_extra_requirement():
    from app.services.verification import adds_completion_condition

    claim = "标记归零且结果为空时遍历结束。"
    assert adds_completion_condition(claim, "标记从非零变为零表示遍历结束。")
    assert not adds_completion_condition(claim, "标记归零且结果为空时遍历结束。")
    assert not adds_completion_condition("标记归零时遍历结束。", "标记从非零变为零表示遍历结束。")


def test_question_headings_are_not_factual_claims_but_their_answers_are():
    answer = "**COUNT 是否保证固定数量：**\n关于 COUNT 能否保证固定数量：\n*   **处理方法**：\n不能保证固定数量 [1]。\n**是否支持回滚：不支持**"
    claims = factual_claims(answer)
    assert len(claims) == 2 and "不能保证" in claims[0] and "不支持" in claims[1]


def test_observed_behavior_is_not_a_prohibition():
    from app.services.verification import adds_prohibition

    assert adds_prohibition("不应保留空结果。", "系统通常不会保留空结果。")
    assert not adds_prohibition("不应保留空结果。", "禁止保留空结果。")
    assert not adds_prohibition("系统不会保留空结果。", "系统通常不会保留空结果。")


@pytest.mark.parametrize("supported,quote,passed", [
    (False, "满足退出条件即可结束。", False),
    (True, "来源中不存在的引文", False),
    (True, "满足退出条件即可结束。", True),
])
def test_critical_conditions_need_an_independent_verdict_and_real_quote(monkeypatch, supported, quote, passed):
    from app.services import answering
    from langchain_core.messages import AIMessage

    calls = []

    class Judge:
        def invoke(self, messages):
            calls.append(messages)
            if len(calls) == 1:
                return AIMessage(content=json.dumps({"answerable": True, "complete": True, "checks": [
                    {"claim_id": 1, "supported": True, "relevant": True, "source_ids": [1]}]}))
            return AIMessage(content=json.dumps({"checks": [
                {"claim_id": 1, "supported": supported, "source_id": 1, "quote": quote}]}))

    monkeypatch.setattr(answering, "configured_llm", lambda *args: Judge())
    result = verify_answer("只有满足退出条件时才可结束 [1]。", [{"text": "满足退出条件即可结束。"}],
                           Settings(_env_file=None, citation_verifier="llm"), "何时结束？")
    assert result["passed"] is passed and result["model_calls"] == 2 and result["critical_checked"]
    assert len(calls) == 2


def test_compound_definition_keeps_subject_and_both_retrieval_queries():
    question = "什么是缓存穿透？为什么会给数据库带来压力？"
    assert definition_subject(question) == "缓存穿透"
    assert terms(question) == terms("缓存穿透")
    assert retrieval_queries(question) == [question, "缓存穿透"]
    assert retrieval_queries("任务何时结束？重试次数有上限吗？") == ["任务何时结束？重试次数有上限吗？", "任务何时结束", "重试次数有上限吗"]
    assert comparison_subjects("Redis 事务和分布式数据库的两阶段提交，在回滚与协调方式上有什么不同？") == [
        "Redis 事务",
        "分布式数据库的两阶段提交",
    ]
    assert comparison_subjects("发布前置时间指的是什么，和需求前置时间有什么不同？") == ["发布前置时间", "需求前置时间"]


def test_definition_heading_is_not_an_answer_but_acronym_definition_is(client):
    from app.services.evidence import definition_context, useful_sentence
    from app.services.retrieval import rerank_candidates
    assert not useful_sentence("什么是分布式缓存？", "# 32 一个典型的分布式缓存系统是什么样的？")
    assert definition_context("什么是分布式缓存？", [{"text":"# 分布式缓存\n本课时介绍如何构建。"}]) == []
    assert useful_sentence("什么是持续集成（CI）？", "CI是一种软件开发实践，团队成员频繁提交，自动触发验证。")
    source = {"id":"a", "text":"# 电池技术性能要求\n循环次数≥2000次。", "document_name":"设备标准.md"}
    assert rerank_candidates("电池技术性能要求是什么", [source], 5) == [source]


def test_navigation_cleanup_is_idempotent_and_preserves_code_and_image_alt():
    raw = "[\n技术文章摘抄](/)\n* 首页\n* 上一级\n# 手册\n正文[链接](https://example.org)。\n![系统架构](x.png)\n```md\n* 首页\n[代码](test.md)\n```\n© 2019 - 2023 Someone\nPowered by theme"
    cleaned = clean_text(raw)
    assert "技术文章摘抄" not in cleaned and "Powered by" not in cleaned
    assert cleaned.count("* 首页") == 1 and "[代码](test.md)" in cleaned
    assert "系统架构" in cleaned and "尚未识别" in cleaned
    assert clean_text(cleaned) == cleaned
    assert clean_text("[![](/static/favicon.png)\n技术文章摘抄](/)\n* [首页](/)\n* [上一级](../)\n正文") == "正文"


def test_markdown_code_continuations_have_fences_headings_and_original_line_ranges():
    raw = (
        "# 指南\n\n## 代码示例\n\n```go\n"
        + "\n".join(f'fmt.Println("行{i}")' for i in range(40))
        + "\n```\n\n## 结尾\n独立正文。"
    )
    chunks = markdown_chunks(raw, 240, 20)
    code = [c for c in chunks if "```" in c["text"]]
    assert len(code) > 1 and all(c["text"].count("```") == 2 for c in code)
    assert all(c["locator"]["section"] == ["指南", "代码示例"] for c in code)
    assert all(c["locator"]["line_start"] <= 5 and c["locator"]["line_end"] >= 45 for c in code)
    assert all(f"行{i}" in "\n".join(c["text"] for c in code) for i in range(40))
    assert chunks[-1]["locator"]["section"] == ["指南", "结尾"]


def test_markdown_images_are_not_silently_lost_or_fetched(tmp_path):
    from app.services.parsing import chunk_pages, parse_document

    path = tmp_path / "guide.md"
    path.write_text("# 说明\n正文。\n![结构图](http://127.0.0.1/private)\n", encoding="utf-8")
    chunks = chunk_pages(parse_document(path, path.name))
    assert "结构图" in chunks[0]["text"]
    assert "图片内容尚未识别" in chunks[0]["text"]
    assert chunks[0]["locator"]["warnings"] and chunks[0]["locator"]["line_end"] == 3


def test_flattened_site_detail_headings_keep_their_subject():
    from app.services.evidence import section_context

    raw = "# 指南\n###### 超时问题\n###### 问题描述\n请求超时。\n###### 业务场景\n繁忙时段。\n###### 解决方案\n降低负载。\n###### 新主题\n另一段内容。"
    chunks = markdown_chunks(raw, 800, 80)
    solution = next(c for c in chunks if "降低负载" in c["text"])
    assert solution["locator"]["section"] == ["指南", "超时问题", "解决方案"]
    assert chunks[-1]["locator"]["section"] == ["指南", "新主题"]
    assert section_context("什么是超时问题？为什么发生？", [solution]) == [solution]


def test_comparison_retains_each_topics_solution_despite_shared_document_title(client):
    from app.services.retrieval import rerank_candidates

    title = "缓存穿透和缓存雪崩怎么处理"
    candidates = []
    for topic in ["缓存穿透", "缓存雪崩"]:
        for section, body in [("问题描述", f"{topic}的问题。"), ("解决方案", "采用相应的保护措施。")]:
            candidates.append({"id": topic + section, "document_name": title,
                "text": f"# {title}\n###### {topic}\n###### {section}\n{body}",
                "locator": {"section": [title, topic, section]}})
    question = "缓存穿透和缓存雪崩有什么区别，分别应怎样处理？"
    selected = rerank_candidates(question, candidates, 4)
    assert {item["id"] for item in selected} == {item["id"] for item in candidates}
    assert selected[0]["locator"]["section"][1] == "缓存穿透"
    assert selected[1]["id"] == "缓存穿透解决方案"
    assert retrieval_queries(question) == [question, "缓存穿透 解决方案", "缓存雪崩 解决方案"]


def test_definition_windows_never_cut_the_last_sentence():
    from app.services.evidence import definition_context

    first = "缓存是一种临时存储，" + "甲" * 950 + "。"
    second = "缓存是一种加速机制，" + "乙" * 950 + "。"
    result = definition_context("什么是缓存？", [{"text": first + second}])
    assert result[0]["text"] == first


def test_paragraph_citations_are_checked_for_each_sentence_and_not_shared_across_items(monkeypatch):
    from app.services import answering
    from langchain_core.messages import AIMessage

    seen = []

    class Judge:
        def invoke(self, messages):
            data = json.loads(messages[-1].content)
            seen.extend(data["claims"])
            return AIMessage(
                content=json.dumps(
                    {
                        "answerable": True,
                        "complete": True,
                        "checks": [
                            {"claim_id": c["claim_id"], "supported": True, "relevant": True, "source_ids": [1]}
                            for c in data["claims"]
                        ],
                    }
                )
            )

    monkeypatch.setattr(answering, "configured_llm", lambda *args: Judge())
    text = "在 Go 语言中，new 和 make 的主要区别如下：\nnew 分配内存。make 初始化对象 [1]。"
    verdict = verify_answer(
        text, [{"text": "new 分配内存，make 初始化对象"}], Settings(_env_file=None, citation_verifier="llm")
    )
    assert verdict["passed"] and len(seen) == 2
    assert all(c["citations"] == [1] for c in seen)
    assert not verify_answer("不会回滚。\n另外一条有引用 [1]。", [{"text": "另外一条"}], Settings(_env_file=None))[
        "passed"
    ]
    assert factual_claims("* 无引用事实。\n* 另一条 [1]。") == ["* 无引用事实。", "* 另一条 [1]。"]
    assert normalize_answer("查看 ht[0] 和 arr2[1] [2]。") == "查看 ht[0] 和 arr2[1] [2]。"


def test_missing_side_of_comparison_fails_even_when_claims_are_supported(monkeypatch):
    from app.services import answering
    from langchain_core.messages import AIMessage

    class Judge:
        def invoke(self, messages):
            return AIMessage(
                content='{"answerable":true,"complete":false,"missing_parts":["提交阶段"],"checks":[{"claim_id":1,"supported":true,"relevant":true,"source_ids":[1]}]}'
            )

    monkeypatch.setattr(answering, "configured_llm", lambda *args: Judge())
    result = verify_answer(
        "第一阶段是准备阶段 [1]。",
        [{"text": "准备与提交两个阶段"}],
        Settings(_env_file=None, citation_verifier="llm"),
        "两个阶段分别是什么？",
    )
    assert not result["passed"] and "incomplete_answer" in result["issues"]


def test_missing_citations_require_semantic_attribution_and_numeric_validation(monkeypatch):
    from app.services import answering
    from langchain_core.messages import AIMessage
    class Judge:
        def invoke(self, messages):
            data = json.loads(messages[-1].content)
            return AIMessage(content=json.dumps({"answerable": True, "complete": True, "checks": [
                {"claim_id": claim["claim_id"], "supported": True, "relevant": True, "source_ids": [1]}
                for claim in data["claims"]]}))
    monkeypatch.setattr(answering, "configured_llm", lambda *args: Judge())
    settings = Settings(_env_file=None, citation_verifier="llm")
    result = verify_answer("运行时不会回滚。", [{"text": "不支持运行时错误的事务回滚"}], settings, allow_attribution=True)
    assert result["passed"] and result["attributed_answer"] == "运行时不会回滚 [1]。"
    wrong = verify_answer("上限为999元。", [{"text": "上限为500元"}], settings, allow_attribution=True)
    assert not wrong["passed"] and "numeric_mismatch" in wrong["issues"]


def test_context_budget_distributes_between_sources_and_keeps_complete_units(client):
    from app.services.answering import _budget_context

    sources = [
        {"id": "one", "document_id": "a", "document_name": "a.md", "text": "第一阶段先投票。" * 1000},
        {"id": "two", "document_id": "b", "document_name": "b.md", "text": "第二阶段提交或取消。" * 1000},
    ]
    _, selected = _budget_context("两个阶段分别是什么？", [], sources)
    assert len(selected) == 2 and "第二阶段提交或取消。" in selected[1]["text"]
    assert all(c["text"].endswith("。") for c in selected)


def test_repair_uses_original_sources_instead_of_incomplete_excerpt(client, monkeypatch):
    from app.config import get_settings
    from app.services import answering
    from langchain_core.messages import AIMessage

    settings = get_settings()
    for name, value in {
        "cache_ttl": 0,
        "llm_provider": "ollama",
        "llm_model": "fixture",
        "citation_verifier": "llm",
    }.items():
        monkeypatch.setattr(settings, name, value)
    source = {
        "id": "c",
        "chunk_id": "c",
        "document_id": "d",
        "knowledge_base_id": "k",
        "document_name": "流程.md",
        "text": "准备阶段进行投票。提交阶段提交或取消。",
        "score": 0.03,
    }
    monkeypatch.setattr(answering, "retrieve", lambda *a, **k: [source])

    class Model:
        def invoke(self, messages):
            data = json.loads(messages[-1].content)
            if "核验器" in messages[0].content:
                complete = "提交阶段" in data["answer"]
                return AIMessage(
                    content=json.dumps(
                        {
                            "answerable": True,
                            "complete": complete,
                            "missing_parts": [] if complete else ["提交阶段"],
                            "checks": [
                                {"claim_id": c["claim_id"], "supported": True, "relevant": True, "source_ids": [1]}
                                for c in data["claims"]
                            ],
                        }
                    )
                )
            if "证据整理助手" in messages[0].content:
                assert "提交阶段" in data["sources"][0]["text"]
                return AIMessage(content="准备阶段进行投票 [1]。提交阶段提交或取消 [1]。")
            return AIMessage(content="准备阶段进行投票 [1]。")

    monkeypatch.setattr(answering, "configured_llm", lambda *a: Model())
    result = answering.answer_question("两个阶段分别是什么？", [source])
    assert result["verification"]["passed"] and "提交阶段" in result["answer"]
