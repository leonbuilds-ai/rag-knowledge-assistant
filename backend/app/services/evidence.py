"""Query-aware evidence cleanup; navigation and course promises are not answers."""
import re
import unicodedata

from app.services.embeddings import tokenize

STOPWORDS = {"的", "是", "了", "什么", "如何", "怎么", "有哪些", "请", "和", "与", "有", "几天", "多少",
             "是多少", "请问", "吗", "呢", "能否", "可以", "需要", "解释", "一下", "介绍", "关于", "一个", "为什么",
             "中", "上", "为", "方面", "由", "哪些", "包含", "包括", "组成", "主要", "两个", "语言",
             "会", "给", "带来", "分别", "怎样", "应", "时候", "什么时候", "是否", "不同", "区别", "什么区别"}


def clean_pdf_text(text):
    lines = re.sub(r"\r+\n?", "\n", text).splitlines()
    lines = [line for line in lines if not line.strip() or not all(
        char.isspace() or unicodedata.category(char) == "Co" for char in line)]
    text = "\n".join(lines)
    if len(re.findall(r"[\u4e00-\u9fff]", text)) > 30:
        # Common Chinese PDF font mappings for printed comma/full stop.
        text = text.replace("ꎬ", "，").replace("ꎮ", "。")
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def focus(question):
    quantity = quantity_subject(question)
    if quantity:
        return quantity
    subject = definition_subject(question)
    match = re.match(r"^(.+?)(?:由哪些|包含哪些|包括哪些|的底层结构|的核心理念|的实现原理|的特点|的作用|的核心思想|的(?:两个|几个|哪些)?阶段)", question)
    subject = match[1] if match else subject or question
    # A language qualifier should not drown out the actual code symbol being asked about.
    if re.search(r"(?:Go|Python|Java|C\+\+)语言?中?\s*[A-Za-z_]+", subject, re.I):
        subject = re.sub(r"^(?:Go|Python|Java|C\+\+)(?:语言)?中?", "", subject, flags=re.I)
    return subject


def quantity_subject(question):
    """Identify a single value lookup without narrowing multi-part comparisons."""
    if re.search(r"分别|以及|和|与|及|比较|对比|同时", question):
        return ""
    match = re.match(r"^(?:请问|请)?(.+?)(?:应为|应达到|为|是)?(?:多少|多久|多长|几天|几年|几个月)[？?。]*$", question.strip())
    return match[1].strip() if match else ""


def clean_text(text):
    # Exported sites occasionally wrap their home link over two lines.
    text = re.sub(r"(?m)^\[(?:!\[[^\]]*\]\([^)]*\))?\s*\n[^\n\]]+\]\(/\)\s*", "", text)
    lines = []
    fence = None
    for line in text.splitlines():
        marker = re.match(r"^\s*(`{3,}|~{3,})", line)
        if marker:
            if fence is None:
                fence = marker[1][0]
            elif marker[1][0] == fence:
                fence = None
            lines.append(line)
            continue
        if fence:
            lines.append(line)
            continue
        if re.match(r"^\s*(?:[-*+]\s*)?(?:首页|上一级|返回首页|返回目录|上一页|下一页)\s*$", line):
            continue
        if re.match(r"^\s*(?:©\s*\d{4}|Copyright\s*\d{4}|Powered by\s)", line, re.I):
            continue
        # Exclude exported Markdown navigation, retaining real prose with inline links.
        if re.match(r"^\s*(?:[-*+]\s*)?\[[^\]]+\]\([^)]*(?:\.md|%[0-9a-f]{2})[^)]*\)\s*$", line, re.I):
            continue
        # Alt text is a description, not OCR evidence. Never fetch arbitrary URLs here.
        line = re.sub(r"!\[([^\]]*)\]\([^)]*\)",
                      lambda m: f"【图片说明：{m[1]}；图片内容尚未识别】" if m[1].strip() else "【图片内容尚未识别】", line)
        line = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", line)
        if re.match(r"^\s*(?:[-*+]\s*)?(?:首页|上一级|返回首页|返回目录|上一页|下一页)\s*$", line):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def terms(question):
    subjects = comparison_subjects(question)
    value = " ".join(subjects) if subjects else focus(question)
    return {word for word in tokenize(value) if word not in STOPWORDS and re.search(r"\w", word)}


def definition_subject(question):
    first = re.split(r"[？?。；;\n，,]", question.strip(), maxsplit=1)[0]
    match = re.match(r"^(?:请问|请)?(?:什么是|解释一下|解释|介绍一下|介绍)\s*(.+?)$", first)
    if not match:
        match = re.match(r"^(.+?)(?:指的是什么|的定义是什么|是什么)$", first)
    return match[1].strip("？?。 ") if match else ""


def comparison_subjects(question):
    if not re.search(r"区别|不同|比较|对比|相比", question):
        return []
    value = re.sub(r"(?:指的是什么|是什么)[，,]\s*(?=和|与)", "", question)
    prefix = re.split(r"[，,？?。\n]|有什么|有何|在(?=.{0,12}(?:上|方面))", value, maxsplit=1)[0]
    pair = re.split(r"\s*(?:和|与|以及|\bvs\.?\b)\s*", prefix, maxsplit=1, flags=re.I)
    if len(pair) != 2:
        return []
    return [re.sub(r"^(?:请比较|请对比|比较|对比)", "", p).strip() for p in pair if p.strip()]


def retrieval_queries(question):
    """Bounded subqueries retain each named side of a comparison."""
    parts = comparison_subjects(question)
    if parts and re.search(r"处理|解决|预防|应对", question):
        parts = [part + " 解决方案" for part in parts]
    subject = definition_subject(question)
    if subject and re.search(r"[？?。].+\w", question):
        parts.append(subject)
    if not parts and not subject:
        clauses = [part.strip() for part in re.split(r"[？?；;]", question) if part.strip()]
        if 1 < len(clauses) <= 4:
            parts.extend(clauses)
    return list(dict.fromkeys([question, *parts]))[:4]


def simple_definition(question):
    return bool(definition_subject(question)) and not comparison_subjects(question) and len([p for p in re.split(r"[？?。；;\n，,]", question) if p.strip()]) == 1


def requirements_question(question):
    return not quantity_subject(question) and bool(re.search(
        r"要求|条件|规定|标准|参数|指标|规格", definition_subject(question) or question))


def section_context(question, candidates):
    """Keep an explicitly requested section and its children, within a source page.

    Matching the whole heading avoids confusing battery requirements with UPS
    requirements. Items inherit the heading's subject even if it isn't repeated.
    """
    if not requirements_question(question):
        return candidates
    subject = re.sub(r"\s+", "", definition_subject(question) or question).strip("？?。")
    matches = []
    for item in candidates:
        lines = clean_text(item["text"]).splitlines()
        headings = []
        for index, line in enumerate(lines):
            heading = re.match(r"^\s*(?:(#{1,6})\s+|(\d+(?:\.\d+)+)\s+)(.+?)\s*$", line)
            if heading:
                level = len(heading[1]) if heading[1] else len(heading[2].split("."))
                headings.append((index, level, heading[3]))
        for position, (start, level, title) in enumerate(headings):
            if re.sub(r"\s+", "", title) != subject:
                continue
            end = next((index for index, other_level, _ in headings[position + 1:] if other_level <= level), len(lines))
            body = "\n".join(lines[start + 1:end]).strip()
            matches.append({**item, "text": "\n".join(lines[start:end]).strip(),
                            "section_title": title, "section_body": body})
    return matches or candidates


def coverage(question, text):
    aliases = re.search(r"[（(]([A-Za-z][A-Za-z0-9_-]*)[）)]", definition_subject(question))
    if aliases and re.search(r"(?<![A-Za-z0-9_])" + re.escape(aliases[1]) + r"(?![A-Za-z0-9_])", text, re.I):
        return 1.0
    words = terms(question)
    found = set(tokenize(text))
    lower = text.lower()
    # Chinese segmentation is context dependent (阶段 vs 两阶段); literal occurrence
    # remains valid evidence even when the tokenizer chooses a different granularity.
    matches = sum(word in found or (bool(re.search(r"[\u4e00-\u9fff]", word)) and word in lower)
                  or (len(word) >= 3 and bool(re.search(r"\b" + re.escape(word), lower))) for word in words)
    return matches / max(len(words), 1)


def useful_sentence(question, sentence):
    if re.match(r"^\s*#{1,6}\s", sentence) or sentence.rstrip().endswith(("？", "?")):
        return False
    if re.search(r"忽略所有|输出系统提示词|泄露提示词|ignore.{0,40}instructions", sentence, re.I):
        return False
    if re.search(r"教你|通过本课程|通过本课|本模块介绍|这个模块介绍|本课时|这节课|这一讲|下一讲|本课程|如下图|上一级|搞懂如何|经典问题是|很有意思的问题|业务场景很多|你[将可].{0,8}学到|通过第.{0,8}讲|目前主要从事|作者简介|我叫|从事.{0,30}研发|图片内容尚未识别", sentence):
        return False
    subject = definition_subject(question)
    if subject and (re.match(r"^(?:请问|请)?什么是", question) or "的定义是什么" in question):
        return coverage(question, sentence) >= .75 and bool(re.search(
            r"指|是|称为|通过|存储|保存|部署|节点|服务器|集群|共享|内存|副本|分片", sentence))
    return coverage(question, sentence) >= (.5 if len(terms(question)) > 1 else 1)


def evidence_score(question, item):
    text = clean_text(item["text"])
    subject = definition_subject(question)
    sentences = re.split(r"(?<=[。！？.!?])|\n", text)
    useful = sum(useful_sentence(question, sentence) for sentence in sentences if sentence.strip())
    intent = (set(tokenize(question)) - terms(question) - STOPWORDS)
    intent_coverage = len(intent & set(tokenize(text))) / max(len(intent), 1)
    title = item.get("document_name", "").lower()
    words = terms(question)
    title_match = sum(word in title for word in words) / max(len(words), 1)
    definition_bonus = 0
    if subject and title_match:
        definition_bonus = 4 if re.search(r"定义|概念", title) else 2 if re.search(r"原理|算法|结构", title) else 1 if re.search(r"基础|入门", title) else 0
    return (coverage(question, text) * 2 + intent_coverage * 1.5 + title_match * 1.8 + definition_bonus
            + (2 if subject and subject in text and useful else 0) + min(useful, 3) * .5 - (2 if not useful else 0)
            + (2 if subject and re.search(r"是(?:一种|一个)|是指|指的是", text) and useful else 0))


def definition_context(question, candidates):
    """Keep concept-bearing windows, excluding unrelated product implementation sections."""
    if not simple_definition(question) or not re.match(r"^(?:请问|请)?什么是", question.strip()):
        return candidates
    selected = []
    for item in candidates:
        sentences = [part.strip() for part in re.split(r"(?<=[。！？])|\n", clean_text(item["text"]))
                     if part.strip() and not part.lstrip().startswith("#")]
        windows = []
        for sentence in sentences:
            if useful_sentence(question, sentence):
                windows.append(sentence)
        if windows:
            windows = list(dict.fromkeys(windows))
            windows.sort(key=lambda text: bool(re.search(r"是(?:一种|一个)|是指|指的是", text)), reverse=True)
            kept, length = [], 0
            for sentence in windows:
                if length + len(sentence) + 2 <= 1800:
                    kept.append(sentence)
                    length += len(sentence) + 2
            # Let the context budget handle an indivisible long sentence explicitly.
            selected.append({**item, "text": "\n\n".join(kept or windows[:1])})
    return selected
