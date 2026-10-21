"""Markdown blocks with original line locators and self-contained code fences."""

import re

from app.services.evidence import clean_text

# Some site exports flatten topic and detail headings to H6. Keep a topic's
# repeated detail labels attached to it so that standalone solutions have context.
DETAIL_HEADINGS = {"问题描述", "原因分析", "业务场景", "解决方案", "注意事项", "实现方式", "使用方法", "示例"}


def markdown_chunks(raw, size, overlap):
    from langchain_text_splitters import RecursiveCharacterTextSplitter

    warnings = []
    images = sum(not re.search(r"favicon", target, re.I) for target in re.findall(r"!\[[^\]]*\]\(([^)]*)\)", raw))
    if images:
        warnings.append(f"包含 {images} 处图片引用；已保留图片说明，图片内容尚未识别。需要识别时请另行上传图片文件。")
    if re.search(r"(?m)^#\s*(?:捐赠|赞助|网站日志)\s*$", raw):
        warnings.append("此文档包含捐赠或站点维护内容，建议与业务知识分库存放。")
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=size, chunk_overlap=overlap, separators=["。", "！", "？", "\n", " ", ""]
    )
    lines = raw.splitlines()
    headings, output, pending = [], [], []

    def flush():
        if not pending:
            return
        prefix = "\n".join(f"{'#' * min(level, 6)} {title}" for level, title in headings)
        text = "\n\n".join(part[0] for part in pending)
        output.append(
            {
                "text": (prefix + "\n\n" + text).strip(),
                "page": 1,
                "locator": {
                    "section": [title for _, title in headings],
                    "line_start": min(p[1] for p in pending),
                    "line_end": max(p[2] for p in pending),
                    "warnings": warnings,
                },
            }
        )
        pending.clear()

    index = 0
    while index < len(lines):
        start = index
        line = lines[index]
        heading = re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", line)
        if heading:
            flush()
            level = len(heading[1])
            if level == 6 and heading[2] in DETAIL_HEADINGS and any(h[0] == 6 and h[1] not in DETAIL_HEADINGS for h in headings):
                level = 7
            headings[:] = [h for h in headings if h[0] < level] + [(level, heading[2])]
            index += 1
            continue
        fence = re.match(r"^\s*(`{3,}|~{3,})(.*)$", line)
        index += 1
        if fence:
            while index < len(lines):
                closing = re.match(
                    r"^\s*" + re.escape(fence[1][0]) + "{" + str(len(fence[1])) + r",}\s*$", lines[index]
                )
                index += 1
                if closing:
                    break
        else:
            while (
                index < len(lines)
                and lines[index].strip()
                and not re.match(r"^(?:#{1,6}\s|\s*`{3,}|\s*~{3,})", lines[index])
            ):
                index += 1
        text = clean_text("\n".join(lines[start:index])).strip()
        if not text or re.fullmatch(r"[-_*\s]+", text):
            continue
        budget = max(100, size - min(200, sum(len(h[1]) + h[0] + 2 for h in headings)) - 4)
        if fence and len(text) > budget:
            # Repeat opening and closing fences for each continuation, never
            # slice a code statement or strip its language marker.
            body = lines[start + 1 : index]
            if body and re.match(r"^\s*[`~]{3,}\s*$", body[-1]):
                body = body[:-1]
            pieces, group = [], []
            for code_line in body:
                if group and sum(len(v) + 1 for v in group) + len(code_line) > budget - len(line) - 8:
                    pieces.append(line + "\n" + "\n".join(group) + "\n" + fence[1])
                    group = []
                group.append(code_line)
            if group:
                pieces.append(line + "\n" + "\n".join(group) + "\n" + fence[1])
        elif text.startswith("|") and len(text) > budget:
            table = text.splitlines()
            header = table[:2]
            pieces = []
            group = []
            for row in table[2:]:
                if group and len("\n".join(header + group + [row])) > budget:
                    pieces.append("\n".join(header + group))
                    group = []
                group.append(row)
            if group:
                pieces.append("\n".join(header + group))
        else:
            pieces = [text] if len(text) <= budget else splitter.split_text(text)
        for piece in pieces:
            if pending and sum(len(p[0]) + 2 for p in pending) + len(piece) > budget:
                flush()
            pending.append((piece, start + 1, index))
            if len(pieces) > 1:
                flush()
    flush()
    # Exact repeated body blocks in one document do not need duplicate vectors.
    seen = set()
    return [item for item in output if not (item["text"] in seen or seen.add(item["text"]))]
