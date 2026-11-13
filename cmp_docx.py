# -*- coding: utf-8 -*-
# DocToolkit —— 中文公文排版与图片 / PDF 转文字工具
# Copyright (C) 2026 KingFeng
#
# 本程序是自由软件：你可以依据自由软件基金会发布的 GNU Affero 通用公共许可证
# （第 3 版，或你选择的任何更高版本）的条款重新分发和 / 或修改它。
# 本程序分发时希望它有用，但不提供任何担保，甚至不提供适销性或特定用途适用性
# 的默示担保。详见 GNU Affero 通用公共许可证。
# 你应当已随本程序收到该许可证的副本；如果没有，见 <https://www.gnu.org/licenses/>。
"""对比两份 docx 的正文文本，输出字符级一致率与差异片段。

用法：python cmp_docx.py 基准.docx 待比对.docx [-o 报告.txt]
"""
import io
import sys
import difflib
import argparse

from docx import Document

PUNCT_MAP = {",": "，", ";": "；", ":": "：", "!": "！", "?": "？",
             "(": "（", ")": "）", ".": "．"}


def read(path):
    """取出所有非空段落文本，按出现顺序返回列表。"""
    d = Document(path)
    out = []
    for p in d.paragraphs:
        t = p.text.strip()
        if t:
            out.append(t)
    # 也带上表格内容，按行列拼
    for tbl in d.tables:
        for row in tbl.rows:
            cells = [c.text.strip() for c in row.cells]
            if any(cells):
                out.append("| " + " | ".join(cells) + " |")
    return out


def norm(s):
    for a, b in PUNCT_MAP.items():
        s = s.replace(a, b)
    return s


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("gold")
    ap.add_argument("auto")
    ap.add_argument("-o", "--output", default=None)
    args = ap.parse_args(argv)

    g = read(args.gold)
    a = read(args.auto)
    gs, as_ = "".join(norm(x) for x in g), "".join(norm(x) for x in a)

    sm = difflib.SequenceMatcher(None, gs, as_, autojunk=False)
    same = sum(b.size for b in sm.get_matching_blocks())

    lines = [
        "基准：%s  段落 %d  字符 %d" % (args.gold, len(g), len(gs)),
        "自动：%s  段落 %d  字符 %d" % (args.auto, len(a), len(as_)),
        "字符级一致率：%.2f%%（%d/%d）" % (same * 100.0 / max(1, len(gs)), same, len(gs)),
        "",
        "== 差异片段 ==",
    ]
    n = 0
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        n += 1
        lines.append("%3d. [%s] 基准 %r  ->  自动 %r" % (n, tag, gs[i1:i2], as_[j1:j2]))
        lines.append("     上下文：…%s【%s|%s】%s…"
                     % (gs[max(0, i1 - 18):i1], gs[i1:i2], as_[j1:j2],
                        gs[i2:i2 + 18]))
    lines.insert(4, "差异片段数：%d" % n)

    txt = "\n".join(lines)
    if args.output:
        io.open(args.output, "w", encoding="utf-8").write(txt)
    sys.stdout.write(txt.split("\n== 差异片段 ==")[0] + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
