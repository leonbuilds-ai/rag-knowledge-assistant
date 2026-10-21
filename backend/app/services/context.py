"""Bounded, complete evidence units; no byte-clipped half sentence or code fence."""

import re

from app.services.evidence import clean_text, coverage, useful_sentence


def units(text):
    blocks = re.split(r"(```[^\n]*\n.*?```|~~~[^\n]*\n.*?~~~)", text, flags=re.S)
    for block in blocks:
        if block.lstrip().startswith(("```", "~~~")):
            yield block.strip()
        else:
            for line in block.splitlines():
                for sentence in re.split(r"(?<=[。！？])", line):
                    if sentence.strip():
                        yield sentence.strip()


def fit_evidence(question, text, budget):
    text = clean_text(text)
    if len(text.encode()) <= budget:
        return text
    from app.services.evidence import comparison_subjects

    subjects = comparison_subjects(question) or [question]
    parts = list(dict.fromkeys(units(text)))
    scored = sorted(
        enumerate(parts),
        key=lambda item: (
            max(coverage(subject, item[1]) for subject in subjects),
            useful_sentence(question, item[1]),
            not item[1].startswith("#"),
        ),
        reverse=True,
    )
    chosen, size = [], 0
    for index, part in scored:
        cost = len(part.encode()) + 2
        if size + cost <= budget:
            chosen.append((index, part))
            size += cost
    return "\n\n".join(part for _, part in sorted(chosen)) or "【此段超出上下文预算，未展示正文，请查看原文】"
