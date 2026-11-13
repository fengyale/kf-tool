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
gongwen.py —— 中文公文 / 制度类文档排版工具

把普通文本（或带轻量标记的文本）排成符合单位规范的 Word：
A4、上3.0/下2.5/左2.8/右2.6cm、正文宋体小四、1.5 倍行距、首行缩进 2 字符、
两端对齐、黑体分级标题、页脚居中页码、表格固定列宽不越版心。

用法：
    python gongwen.py 正文.txt                     # 输出 正文.docx
    python gongwen.py 正文.txt -o 通知.docx
    python gongwen.py 正文.txt --title "关于XX的通知" --subtitle "（试行）"
    python gongwen.py - < 正文.txt                 # 从标准输入读

标记（可混用；全都不写时按中文序号自动识别层级）：
    %T 标题            文档大标题（也可由 --title 指定）
    %S 副标题          副标题，如“（试行）”
    # 一级标题         == 一、二、三、……
    ## 二级标题        == （一）（二）（三）……
    ### 三级标题       == 1、2、3、……
    %C 文字            居中段落
    %R 文字            右对齐段落（落款、日期）
    %PAGE              分页符
    | 列1 | 列2 |      表格行；连续多行组成一张表，首行为表头
    |---|---|          表格分隔行（可写可不写，写了会跳过）

自动识别（未加 # 的行）：
    一、/ 二、          → 一级标题
    （一）/ （二）      → 二级标题
    1、/ 2、            → 三级标题
    首行且不像上面任何一种 → 作为文档标题
"""

import argparse
import io
import os
import re
import sys

from docx import Document
from docx.shared import Pt, Cm, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml.ns import qn
from docx.oxml import OxmlElement

CN_FONT = "宋体"
CN_HEAD = "黑体"
EN_FONT = "Times New Roman"

# ---------- 页面与正文参数（如换单位规范，只改这里） ----------
PAGE_W, PAGE_H = 21.0, 29.7
M_TOP, M_BOTTOM, M_LEFT, M_RIGHT = 3.0, 2.5, 2.8, 2.6
BODY_PT = 12          # 小四
LINE_SPACING = 1.5
TITLE_PT, SUBTITLE_PT = 18, 16
H1_PT, H2_PT, H3_PT = 14, 12, 12
TABLE_PT = 10.5

CN_NUM = "一二三四五六七八九十百"
RE_H1 = re.compile(r"^\s*[%s]+、" % CN_NUM)
RE_H2 = re.compile(r"^\s*[（(]\s*[%s]+\s*[）)]" % CN_NUM)
RE_H3 = re.compile(r"^\s*\d+\s*[、．.]\s*")
RE_ATTACH = re.compile(r"^\s*附件\s*\d+")

# ---------- 版权与版本号（默认关闭，由使用者用 --owner / --version 显式开启） ----------
# 开源默认值留空：陌生人的文档里不该自动出现别人的版权所有。
DEFAULT_OWNER = ""
DEFAULT_VERSION = "V1.0"
COPYRIGHT_LINE = "版权所有 © {owner}　　版本号：{version}"
COPYRIGHT_NOTE = "本文件内容版权归 {owner} 所有，未经书面许可，不得复制、转载或用于商业用途。"


def set_run(run, size=BODY_PT, bold=False, font=CN_FONT, color=None):
    """显式设置中西文字体，避免 Word 里显示宋体、实际却是等线。"""
    run.font.name = EN_FONT
    run.font.size = Pt(size)
    run.font.bold = bold
    rpr = run._element.get_or_add_rPr()
    rf = rpr.find(qn("w:rFonts"))
    if rf is None:
        rf = OxmlElement("w:rFonts")
        rpr.append(rf)
    rf.set(qn("w:ascii"), EN_FONT)
    rf.set(qn("w:hAnsi"), EN_FONT)
    rf.set(qn("w:eastAsia"), font)
    if color:
        run.font.color.rgb = RGBColor(*color)


def fix_table(tbl, widths):
    """强制固定列宽。python-docx 单设 cell.width 会被 Word 的 autofit 覆盖，
    必须同时写 tblLayout=fixed 并改写 tblGrid/gridCol，列宽才真正生效。"""
    tbl.autofit = False
    tblPr = tbl._tbl.tblPr
    layout = OxmlElement("w:tblLayout")
    layout.set(qn("w:type"), "fixed")
    tblPr.append(layout)
    grid = tbl._tbl.find(qn("w:tblGrid"))
    if grid is not None:
        for gc, w in zip(grid.findall(qn("w:gridCol")), widths):
            gc.set(qn("w:w"), str(int(w.twips)))
    for row in tbl.rows:
        for j, w in enumerate(widths):
            if j < len(row.cells):
                row.cells[j].width = w


class DocBuilder(object):
    def __init__(self, page_number=True, body_pt=BODY_PT, line_spacing=LINE_SPACING,
                 indent=True, owner=None, version=None, copyright_note=True):
        self.body_pt = body_pt
        self.line_spacing = line_spacing
        self.indent = indent
        self.owner = owner
        self.version = version
        self.copyright_note = copyright_note
        self.doc = Document()

        sec = self.doc.sections[0]
        sec.page_width = Cm(PAGE_W)
        sec.page_height = Cm(PAGE_H)
        sec.top_margin = Cm(M_TOP)
        sec.bottom_margin = Cm(M_BOTTOM)
        sec.left_margin = Cm(M_LEFT)
        sec.right_margin = Cm(M_RIGHT)
        self.content_width_cm = PAGE_W - M_LEFT - M_RIGHT

        st = self.doc.styles["Normal"]
        st.font.name = EN_FONT
        st.font.size = Pt(body_pt)
        st.element.rPr.rFonts.set(qn("w:eastAsia"), CN_FONT)

        if page_number or self.owner:
            self._add_footer(sec, page_number)

    # ---------- 基础构件 ----------
    def _add_footer(self, sec, page_number=True):
        """页脚：版权所有 © 归属方 + 版本号 + 页码（居中，小五）。"""
        p = sec.footer.paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.space_before = Pt(0)
        p.paragraph_format.space_after = Pt(0)

        if self.owner:
            set_run(p.add_run("版权所有 © %s" % self.owner), size=9)
            if self.version:
                set_run(p.add_run("　版本号：%s" % self.version), size=9)
        if page_number:
            if self.owner:
                set_run(p.add_run("　"), size=9)
            r = p.add_run()
            set_run(r, size=9)
            set_run(p.add_run("第 "), size=9)
            r2 = p.add_run()
            set_run(r2, size=9)
            f1 = OxmlElement("w:fldChar"); f1.set(qn("w:fldCharType"), "begin")
            it = OxmlElement("w:instrText"); it.set(qn("xml:space"), "preserve"); it.text = " PAGE "
            f2 = OxmlElement("w:fldChar"); f2.set(qn("w:fldCharType"), "end")
            r2._r.append(f1); r2._r.append(it); r2._r.append(f2)
            set_run(p.add_run(" 页"), size=9)

    def _para(self, text="", size=None, bold=False, font=CN_FONT, indent=None,
              align=WD_ALIGN_PARAGRAPH.JUSTIFY, before=0, after=0, right_indent=0):
        size = self.body_pt if size is None else size
        indent = self.indent if indent is None else indent
        p = self.doc.add_paragraph()
        p.alignment = align
        pf = p.paragraph_format
        pf.space_before = Pt(before)
        pf.space_after = Pt(after)
        pf.line_spacing = self.line_spacing
        if right_indent:
            pf.right_indent = Pt(right_indent)
        if indent:
            pf.first_line_indent = Pt(size * 2)
        if text:
            set_run(p.add_run(text), size=size, bold=bold, font=font)
        return p

    def title(self, text):
        return self._para(text, size=TITLE_PT, bold=True, font=CN_HEAD, indent=False,
                          align=WD_ALIGN_PARAGRAPH.CENTER, before=6, after=6)

    def subtitle(self, text):
        return self._para(text, size=SUBTITLE_PT, bold=True, font=CN_HEAD, indent=False,
                          align=WD_ALIGN_PARAGRAPH.CENTER, before=0, after=18)

    def h1(self, text):
        return self._para(text, size=H1_PT, bold=True, font=CN_HEAD,
                          align=WD_ALIGN_PARAGRAPH.LEFT, before=10, after=4)

    def h2(self, text):
        return self._para(text, size=H2_PT, bold=True, font=CN_HEAD,
                          align=WD_ALIGN_PARAGRAPH.LEFT, before=6, after=2)

    def h3(self, text):
        return self._para(text, size=H3_PT, bold=True, font=CN_FONT,
                          align=WD_ALIGN_PARAGRAPH.LEFT, before=4, after=2)

    def body(self, text):
        return self._para(text)

    def center(self, text):
        return self._para(text, indent=False, align=WD_ALIGN_PARAGRAPH.CENTER)

    def right(self, text):
        return self._para(text, indent=False, align=WD_ALIGN_PARAGRAPH.RIGHT,
                          before=6, right_indent=24)

    def page_break(self):
        self.doc.add_page_break()

    def copyright_notice(self):
        """文末版权声明：一条分隔线 + 一行小字声明。"""
        if not (self.owner and self.copyright_note):
            return None
        # 上方一条细横线
        p0 = self.doc.add_paragraph()
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

        p = self._para(size=9, bold=False, font=CN_FONT, indent=False,
                       align=WD_ALIGN_PARAGRAPH.CENTER, before=0, after=0)
        set_run(p.add_run(COPYRIGHT_NOTE.format(owner=self.owner)), size=9)
        if self.version:
            set_run(p.add_run("　版本号：%s" % self.version), size=9)
        return p

    def caption(self, text):
        return self._para(text, size=14, bold=True, font=CN_HEAD, indent=False,
                          align=WD_ALIGN_PARAGRAPH.CENTER, after=10)

    def table(self, rows, widths_cm=None, size=TABLE_PT):
        if not rows:
            return None
        ncol = max(len(r) for r in rows)
        rows = [list(r) + [""] * (ncol - len(r)) for r in rows]
        t = self.doc.add_table(rows=len(rows), cols=ncol)
        t.style = "Table Grid"
        t.alignment = WD_TABLE_ALIGNMENT.CENTER
        for i, r in enumerate(rows):
            for j, v in enumerate(r):
                cell = t.cell(i, j)
                cell.text = ""
                p = cell.paragraphs[0]
                p.alignment = WD_ALIGN_PARAGRAPH.CENTER if (i == 0 or j == 0) \
                    else WD_ALIGN_PARAGRAPH.LEFT
                p.paragraph_format.space_before = Pt(3)
                p.paragraph_format.space_after = Pt(3)
                p.paragraph_format.line_spacing = 1.2
                set_run(p.add_run(v), size=size, bold=(i == 0))
        if widths_cm is None:
            widths_cm = self._auto_widths(rows, ncol)
        fix_table(t, [Cm(w) for w in widths_cm])
        return t

    def _auto_widths(self, rows, ncol):
        """按各列最长内容分配宽度，最小 2cm，总宽等于版心。"""
        weights = []
        for j in range(ncol):
            longest = max(len(r[j]) for r in rows) or 1
            weights.append(max(longest, 4))
        total = float(sum(weights))
        avail = self.content_width_cm
        widths = [max(2.0, avail * w / total) for w in weights]
        scale = avail / sum(widths)
        return [w * scale for w in widths]

    def save(self, path):
        d = os.path.dirname(os.path.abspath(path))
        if d and not os.path.isdir(d):
            os.makedirs(d)
        self.doc.save(path)
        return path


# ---------- 文本解析 ----------
def parse(text, title=None, subtitle=None):
    """把文本解析为 block 列表：(kind, payload)。"""
    blocks = []
    if title:
        blocks.append(("title", title))
    if subtitle:
        blocks.append(("subtitle", subtitle))

    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    seen_content = bool(title)

    i = 0
    while i < len(lines):
        raw = lines[i].rstrip()
        s = raw.strip()
        i += 1
        if not s:
            continue

        # 显式标记
        if s in ("%PAGE", "---", "***"):
            blocks.append(("page", ""))
            continue
        if s.startswith("%T "):
            blocks.append(("title", s[3:].strip()))
            seen_content = True
            continue
        if s.startswith("%S "):
            blocks.append(("subtitle", s[3:].strip()))
            seen_content = True
            continue
        if s.startswith("%C "):
            blocks.append(("center", s[3:].strip()))
            seen_content = True
            continue
        if s.startswith("%R "):
            blocks.append(("right", s[3:].strip()))
            seen_content = True
            continue
        if s.startswith("%P "):
            blocks.append(("body", s[3:].strip()))
            seen_content = True
            continue
        if s.startswith("|"):
            rows = []
            while True:
                cells = [c.strip() for c in s.strip("|").split("|")]
                if not all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                    rows.append(cells)
                if i >= len(lines):
                    break
                nxt = lines[i].strip()
                if not nxt.startswith("|"):
                    break
                s = nxt
                i += 1
            blocks.append(("table", rows))
            seen_content = True
            continue

        for mark, kind in (("### ", "h3"), ("## ", "h2"), ("# ", "h1")):
            if s.startswith(mark):
                blocks.append((kind, s[len(mark):].strip()))
                seen_content = True
                break
        else:
            if RE_ATTACH.match(s):
                blocks.append(("caption", s))
            elif RE_H1.match(s):
                blocks.append(("h1", s))
            elif RE_H2.match(s):
                blocks.append(("h2", s))
            elif RE_H3.match(s):
                blocks.append(("h3", s))
            elif not seen_content and len(s) <= 60:
                blocks.append(("title", s))
            else:
                blocks.append(("body", s))
            seen_content = True
    return blocks


def build_from_blocks(blocks, out_path, page_number=True, body_pt=BODY_PT,
                      line_spacing=LINE_SPACING, indent=True, table_widths=None,
                      owner=None, version=None, copyright_note=True):
    b = DocBuilder(page_number=page_number, body_pt=body_pt, line_spacing=line_spacing,
                   indent=indent, owner=owner, version=version,
                   copyright_note=copyright_note)
    for kind, payload in blocks:
        if kind == "title":
            b.title(payload)
        elif kind == "subtitle":
            b.subtitle(payload)
        elif kind == "h1":
            b.h1(payload)
        elif kind == "h2":
            b.h2(payload)
        elif kind == "h3":
            b.h3(payload)
        elif kind == "caption":
            b.caption(payload)
        elif kind == "center":
            b.center(payload)
        elif kind == "right":
            b.right(payload)
        elif kind == "page":
            b.page_break()
        elif kind == "table":
            b.table(payload, widths_cm=table_widths)
        else:
            b.body(payload)
    if owner:
        b.copyright_notice()
    return b.save(out_path)


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="把文本排成符合公文规范的 Word 文档")
    ap.add_argument("input", help="输入文本文件；用 - 表示从标准输入读取")
    ap.add_argument("-o", "--output", help="输出 docx 路径（默认与输入同名）")
    ap.add_argument("--title", help="文档标题（不使用文本首行）")
    ap.add_argument("--subtitle", help="副标题，如 （试行）")
    ap.add_argument("--encoding", default="utf-8", help="输入编码，默认 utf-8")
    ap.add_argument("--no-page-number", action="store_true", help="不显示页码")
    ap.add_argument("--no-indent", action="store_true", help="正文不首行缩进")
    ap.add_argument("--font-size", type=float, default=BODY_PT, help="正文字号磅值，默认 12")
    ap.add_argument("--line-spacing", type=float, default=LINE_SPACING, help="行距倍数，默认 1.5")
    ap.add_argument("--owner", default=DEFAULT_OWNER,
                    help="版权归属方，默认 %s；传空字符串可关闭版权信息" % DEFAULT_OWNER)
    ap.add_argument("--version", default=DEFAULT_VERSION,
                    help="版本号，默认 %s；传空字符串可关闭版本号" % DEFAULT_VERSION)
    ap.add_argument("--no-copyright-note", action="store_true",
                    help="不显示文末版权声明（页脚仍显示）")
    ap.add_argument("--no-copyright", action="store_true",
                    help="完全不显示版权与版本号（页脚只留页码）")
    args = ap.parse_args(argv)

    if args.no_copyright:
        args.owner = ""
        args.version = ""
        args.no_copyright_note = True

    if args.input == "-":
        text = sys.stdin.read()
        base = "输出"
    else:
        with io.open(args.input, "r", encoding=args.encoding, errors="replace") as f:
            text = f.read()
        base = os.path.splitext(os.path.basename(args.input))[0]

    out = args.output or (base + ".docx")
    blocks = parse(text, title=args.title, subtitle=args.subtitle)
    if not blocks:
        sys.stderr.write("输入为空，未生成文档。\n")
        return 2
    build_from_blocks(blocks, out,
                      page_number=not args.no_page_number,
                      body_pt=args.font_size,
                      line_spacing=args.line_spacing,
                      indent=not args.no_indent,
                      owner=args.owner or None,
                      version=args.version or None,
                      copyright_note=not args.no_copyright_note)
    print("已生成：%s（%d 个段落块）" % (out, len(blocks)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
