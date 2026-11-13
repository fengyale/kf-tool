# -*- coding: utf-8 -*-
# DocToolkit —— 中文公文排版与图片 / PDF 转文字工具
# Copyright (C) 2026 KingFeng
#
# 本程序是自由软件：你可以依据自由软件基金会发布的 GNU Affero 通用公共许可证
# （第 3 版，或你选择的任何更高版本）的条款重新分发和 / 或修改它。
# 本程序分发时希望它有用，但不提供任何担保，甚至不提供适销性或特定用途适用性
# 的默示担保。详见 GNU Affero 通用公共许可证。
# 你应当已随本程序收到该许可证的副本；如果没有，见 <https://www.gnu.org/licenses/>。
"""
add_copyright.py —— 给已有的 Word 文档加版权归属、版本号和页码

用法：
    python add_copyright.py 文档.docx
    python add_copyright.py 文档.docx -o 加了版权的.docx
    python add_copyright.py 文档.docx --owner "KingFeng" --version "V2.0"
    python add_copyright.py 文档.docx --no-copyright-note      # 只要页脚，不要文末声明
    python add_copyright.py 文档.docx --no-copyright           # 整个不要

作用：
    1. 页脚居中加「版权所有 © 归属方　版本号：xxx　第 X 页」（页码是 Word 域，会自动更新）
    2. 文档正文最后加一条细横线 + 一行小字版权声明

不动正文任何一个字、不动任何表格和段落格式。
"""

import argparse
import os
import sys

from docx import Document
from docx.shared import Pt
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.oxml import OxmlElement

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gongwen  # 复用字体、页边距、页脚构造逻辑


def add_copyright(doc, owner, version, note=True, page_number=True):
    """就地给 doc 加版权。返回是否做了改动。"""
    # ---------- 页脚 ----------
    for sec in doc.sections:
        f = sec.footer.paragraphs[0]
        # 清空原有页脚内容
        for r in list(f.runs):
            r._r.getparent().remove(r._r)
        f.alignment = WD_ALIGN_PARAGRAPH.CENTER
        f.paragraph_format.space_before = Pt(0)
        f.paragraph_format.space_after = Pt(0)
        f.paragraph_format.line_spacing = 1.0

        if owner:
            gongwen.set_run(f.add_run("版权所有 © %s" % owner), size=9)
            if version:
                gongwen.set_run(f.add_run("　版本号：%s" % version), size=9)
        if page_number:
            if owner:
                gongwen.set_run(f.add_run("　"), size=9)
            gongwen.set_run(f.add_run("第 "), size=9)
            r = f.add_run()
            gongwen.set_run(r, size=9)
            f1 = OxmlElement("w:fldChar"); f1.set(qn("w:fldCharType"), "begin")
            it = OxmlElement("w:instrText"); it.set(qn("xml:space"), "preserve"); it.text = " PAGE "
            f2 = OxmlElement("w:fldChar"); f2.set(qn("w:fldCharType"), "end")
            r._r.append(f1); r._r.append(it); r._r.append(f2)
            gongwen.set_run(f.add_run(" 页"), size=9)

    # ---------- 文末声明 ----------
    if not (owner and note):
        return True

    p0 = doc.add_paragraph()
    p0.paragraph_format.space_before = Pt(18)
    p0.paragraph_format.space_after = Pt(2)
    pbdr = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), "4")
    bottom.set(qn("w:space"), "1")
    bottom.set(qn("w:color"), "808080")
    pbdr.append(bottom)
    p0._p.get_or_add_pPr().append(pbdr)

    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    pf = p.paragraph_format
    pf.space_before = Pt(0)
    pf.space_after = Pt(0)
    pf.line_spacing = 1.0
    gongwen.set_run(p.add_run(gongwen.COPYRIGHT_NOTE.format(owner=owner)), size=9)
    if version:
        gongwen.set_run(p.add_run("　版本号：%s" % version), size=9)
    return True


def main(argv=None):
    ap = argparse.ArgumentParser(description="给已有 Word 文档加版权归属、版本号和页码")
    ap.add_argument("input", help="要处理的 .docx")
    ap.add_argument("-o", "--output", help="输出路径（默认覆盖原文件）")
    ap.add_argument("--owner", default=gongwen.DEFAULT_OWNER,
                    help="版权归属方，默认 %s" % gongwen.DEFAULT_OWNER)
    ap.add_argument("--version", default=gongwen.DEFAULT_VERSION,
                    help="版本号，默认 %s" % gongwen.DEFAULT_VERSION)
    ap.add_argument("--no-copyright-note", action="store_true", help="不显示文末版权声明")
    ap.add_argument("--no-copyright", action="store_true", help="完全不显示版权与版本号")
    ap.add_argument("--no-page-number", action="store_true", help="不显示页码")
    args = ap.parse_args(argv)

    if args.no_copyright:
        args.owner = ""
        args.version = ""
        args.no_copyright_note = True

    if not os.path.isfile(args.input):
        sys.stderr.write("找不到文件：%s\n" % args.input)
        return 2

    doc = Document(args.input)
    add_copyright(doc, args.owner or None, args.version or None,
                  note=not args.no_copyright_note,
                  page_number=not args.no_page_number)
    out = args.output or args.input
    d = os.path.dirname(os.path.abspath(out))
    if d and not os.path.isdir(d):
        os.makedirs(d)
    doc.save(out)
    print("已生成：%s" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
