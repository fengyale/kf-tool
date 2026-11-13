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
ocr2docx.py —— 图片 / 扫描件转文字初稿（Word）

把一批图片或一个 PDF 识别成可编辑的 Word 初稿：
多遍识别（原尺寸 + 2 倍 + 3 倍锐化）取每行置信度最高的结果；
对低置信度或两次结果不一致的行，再按该行坐标裁出横带、放大 6–8 倍复识一次；
按行首缩进自动合并成中文段落；如页面存在明显表格网格线，尝试还原为表格。

用法：
    python ocr2docx.py 图片目录 -o 初稿.docx
    python ocr2docx.py 1.png 2.png 3.png -o 初稿.docx
    python ocr2docx.py 通知.pdf -o 初稿.docx
    python ocr2docx.py 图片目录 -o 初稿.docx --title "关于XX的通知"

产出：
    初稿.docx   识别结果排好版（依赖 gongwen.py 的版式）
    初稿.txt    同样内容的纯文本，便于人工校对后再用 gongwen.py 重排
    初稿.report.txt  逐页识别情况：行数、被复识修正的行、仍存疑的行

重要：这是初稿。标题层级靠中文序号规则推断，表格靠网格线检测，
识别文字未经校订。正式使用前必须人工通读一遍。
"""

import argparse
import glob
import io
import os
import re
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "vendor", "libs"))
sys.path.insert(0, HERE)

import numpy as np
from PIL import Image, ImageFilter, ImageEnhance

import gongwen


# ---------------- OCR 引擎 ----------------
_ENGINE = None


def engine():
    global _ENGINE
    if _ENGINE is None:
        from rapidocr_onnxruntime import RapidOCR
        _ENGINE = RapidOCR()
    return _ENGINE


def ocr_array(arr):
    """识别 numpy 图像，返回 [{text,score,x,y,w,h}]，按阅读顺序排序。"""
    res, _ = engine()(arr)
    out = []
    if not res:
        return out
    for box, text, score in res:
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        out.append({
            "text": text.strip(),
            "score": float(score),
            "x": min(xs),
            "y": min(ys),
            "w": max(xs) - min(xs),
            "h": max(ys) - min(ys),
            "xc": (min(xs) + max(xs)) / 2.0,
            "yc": (min(ys) + max(ys)) / 2.0,
        })
    # 不能直接按 (y, x) 排：同一行内 y 有 1–2px 抖动，
    # 会把左中右的顺序打乱。先按 y 聚成行，行内再按 x 排。
    out.sort(key=lambda d: (d["yc"], d["x"]))
    rows = []
    for d in out:
        placed = False
        for r in rows:
            if abs(d["yc"] - r["yc"]) <= max(d["h"], r["h"]) * 0.6:
                r["items"].append(d)
                placed = True
                break
        if not placed:
            rows.append({"yc": d["yc"], "h": d["h"], "items": [d]})
    rows.sort(key=lambda r: r["yc"])
    out = [d for r in rows for d in sorted(r["items"], key=lambda d: d["x"])]
    return out


def prep_pass(im_gray, scale, sharpen=False, contrast=1.0):
    big = im_gray.resize((int(im_gray.width * scale), int(im_gray.height * scale)),
                         Image.LANCZOS)
    if sharpen:
        big = big.filter(ImageFilter.UnsharpMask(radius=2, percent=180, threshold=2))
    if contrast != 1.0:
        big = ImageEnhance.Contrast(big).enhance(contrast)
    return np.array(big.convert("RGB")), scale


# ---------------- 行合并 ----------------
def merge_passes(passes):
    """多遍结果按 y 重叠聚成行；每行选置信度最高的那一遍的文字。

    各遍是在不同放大倍数下识别的，坐标必须除回原始像素尺度才能互相比较，
    否则 2 倍/3 倍遍的 y 值偏大，行会被错并成一堆。
    """
    items = []
    for pi, (lines, scale) in enumerate(passes):
        sc = float(scale) if scale else 1.0
        for ln in lines:
            d = dict(ln)
            d["pass"] = pi
            for k in ("x", "y", "w", "h", "xc", "yc"):
                d[k] = d[k] / sc
            items.append(d)
    items.sort(key=lambda d: d["yc"])

    rows = []
    for it in items:
        placed = False
        for row in rows:
            ry = row["yc"]
            tol = max(row["h"], it["h"]) * 0.55
            if abs(it["yc"] - ry) <= tol:
                row["members"].append(it)
                tot = sum(abs(m["h"]) + 1 for m in row["members"])
                row["yc"] = sum(m["yc"] * (abs(m["h"]) + 1) for m in row["members"]) / tot
                row["h"] = max(row["h"], it["h"])
                placed = True
                break
        if not placed:
            rows.append({"yc": it["yc"], "h": it["h"], "members": [it]})

    out = []
    for row in rows:
        members = sorted(row["members"], key=lambda d: -d["score"])
        best = members[0]
        distinct = set(m["text"] for m in members)
        out.append({
            "text": best["text"],
            "score": best["score"],
            "x": min(m["x"] for m in members),
            "w": max(m["w"] for m in members),
            # 存行顶而不是中心：recheck_line 按行顶计算裁切框
            "y": row["yc"] - row["h"] / 2.0,
            "yc": row["yc"],
            "h": row["h"],
            "disagree": len(distinct) > 1,
            "cands": [m["text"] for m in members],
        })
    out.sort(key=lambda d: (d["y"], d["x"]))
    return out


def _is_dup(text, *others):
    """判断补回的行是否其实是相邻行的残缺副本。

    裁切框留了容差，会把相邻行的顶边/底边带进来，OCR 于是把那半行也读出来，
    结果是「上一行的前半截」或「下一行的前半截」。这类文本与相邻行互为前缀，
    直接相等判断抓不到，必须比前缀。
    """
    t = text.strip()
    if not t:
        return True
    for o in others:
        o = (o or "").strip()
        if not o:
            continue
        if t == o:
            return True
        short, long_ = (t, o) if len(t) <= len(o) else (o, t)
        # 短的是长的前缀，且占比够大（避免把"公司"这种巧合前缀判成重复）
        if long_.startswith(short) and len(short) >= max(12, len(long_) * 0.5):
            return True
        # 一行的头/尾被截进另一行
        if len(short) >= 16 and (long_.endswith(short) or short in long_):
            return True
    return False


def fill_gaps(im_gray, lines, report):
    """OCR 偶尔会整行漏识别（行间距明显偏大处），在空隙里补识别。

    多遍识别与逐行复识都发现不了「少了一行」——因为那一行根本没有任何 box，
    谈不上低置信。唯一可靠的信号是纵向行距异常：正常行距约等于行高，
    若相邻两行之间空出了一个行高以上，多半是中间那行被吞了。
    """
    if len(lines) < 3:
        return lines, 0
    hs = [ln["h"] for ln in lines if ln["h"] > 4]
    if not hs:
        return lines, 0
    med_h = float(np.median(hs))
    if med_h <= 0:
        return lines, 0

    added = []
    for i in range(len(lines) - 1):
        a, b = lines[i], lines[i + 1]
        top_gap = b["y"] - (a["y"] + a["h"])
        if top_gap < med_h * 0.8:
            continue
        c0 = max(0, int(a["y"] + a["h"] - med_h * 0.15))
        c1 = min(im_gray.height, int(b["y"] + med_h * 0.15))
        if c1 - c0 < med_h * 0.55:
            continue
        x0 = max(0, int(min(a["x"], b["x"]) - med_h))
        x1 = min(im_gray.width,
                 int(max(a["x"] + a["w"], b["x"] + b["w"]) + med_h))
        if x1 - x0 < med_h * 2:
            continue
        crop = im_gray.crop((x0, c0, x1, c1))
        best = []
        for s_ in (4, 6, 8):
            big = crop.resize((crop.width * s_, crop.height * s_), Image.LANCZOS)
            big = big.filter(ImageFilter.UnsharpMask(radius=2, percent=180,
                                                     threshold=2))
            big = ImageEnhance.Contrast(big).enhance(1.4)
            got = ocr_array(np.array(big.convert("RGB")))
            if got:
                best.append(got)
        if not best:
            continue
        # 取产出 box 最多的那一遍（漏字越少越好），同分取平均置信度高者
        got = max(best, key=lambda g: (len(g),
                                       sum(d["score"] for d in g) / len(g)))
        for d0, d1 in group_rows(got, med_h):
            text = "".join(g["text"] for g in sorted(d0, key=lambda t: t["x"]))
            text = normalize_punct(apply_confusion(text.strip()))
            if len(text) < 4:
                continue
            # 与上下文重复就不算补回。注意裁切框为了容差会带进上下行的顶边/底边，
            # OCR 常把相邻行读成「半截前缀」，所以前缀/后缀/子串都要挡掉。
            if _is_dup(text, a["text"], b["text"]):
                continue
            sc = min(g["score"] for g in d0)
            if sc < 0.7:
                continue
            added.append({
                "text": text, "score": sc,
                "x": min(g["x"] for g in d0) / s_ + x0,
                "y": min(g["y"] for g in d0) / s_ + c0,
                "h": max(g["h"] for g in d0) / s_,
                "w": max(g["x"] + g["w"] for g in d0) / s_ - min(g["x"] for g in d0) / s_,
                "disagree": False, "cands": [text],
            })
            added[-1]["yc"] = added[-1]["y"] + added[-1]["h"] / 2.0

    if not added:
        return lines, 0
    for ln in added:
        report.append("补漏行 score=%.2f  %s" % (ln["score"], ln["text"][:36]))
    out = sorted(lines + added, key=lambda d: (d["y"], d["x"]))
    return out, len(added)


def group_rows(boxes, med_h):
    """把一组 OCR box 按 y 聚成行，返回 [(同行的 box 列表, 下一个 box 下标)]。"""
    boxes = sorted(boxes, key=lambda d: d["y"])
    rows = []
    for it in boxes:
        if rows and abs(it["y"] - rows[-1][-1]["y"]) <= med_h * 0.6:
            rows[-1].append(it)
        else:
            rows.append([it])
    return [(r, 0) for r in rows]


def recheck_line(im_gray, line, note):
    """对低置信/有分歧的行，按坐标裁横带、放大 6–8 倍复识。返回最佳文本或 None。"""
    x0 = max(0, int(line["x"] - line["h"] * 0.8))
    x1 = min(im_gray.width, int(line["x"] + line["w"] + line["h"] * 0.8))
    y0 = max(0, int(line["y"] - line["h"] * 0.45))
    y1 = min(im_gray.height, int(line["y"] + line["h"] * 1.45))
    if x1 - x0 < 8 or y1 - y0 < 8:
        return None
    crop = im_gray.crop((x0, y0, x1, y1))
    best = None
    # 倍率不能从大开始：行高仅 20px 时 6 倍会过度锐化反而乱码，
    # 实测 2–4 倍最稳。从小到大试，一旦足够好就停。
    for s in (2, 3, 4, 6):
        big = crop.resize((crop.width * s, crop.height * s), Image.LANCZOS)
        big = big.filter(ImageFilter.UnsharpMask(radius=2, percent=180, threshold=2))
        big = ImageEnhance.Contrast(big).enhance(1.5)
        got = ocr_array(np.array(big.convert("RGB")))
        if not got:
            continue
        text = "".join(g["text"] for g in sorted(got, key=lambda d: d["x"]))
        score = min(g["score"] for g in got) if got else 0.0
        cand = {"text": text, "score": score, "scale": s}
        if best is None or cand["score"] > best["score"]:
            best = cand
        # 第一档已足够好就不再试第二档，省一半时间
        if best and best["score"] >= max(0.9, line["score"]):
            break
    if best and best["score"] >= line["score"] and best["text"]:
        note.append("修正 score %.2f→%.2f  %s  →  %s"
                    % (line["score"], best["score"], line["text"][:28], best["text"][:28]))
        return best["text"], best["score"]
    return None


# ---------------- 表格检测 ----------------
def _longest_run(mask):
    """返回 ((最长连续 True 长度), 起始, 结束)"""
    best = cur = 0
    bs = be = 0
    s0 = 0
    i = 0
    for v in mask:
        if v:
            if cur == 0:
                s0 = i
            cur += 1
            if cur > best:
                best, bs, be = cur, s0, i
        else:
            cur = 0
        i += 1
    return best, bs, be


def _spans(lines):
    """取一组线的中位跨度，用于互相验证。"""
    a = sorted(l[1] for l in lines)
    b = sorted(l[2] for l in lines)
    return a[len(a) // 2], b[len(b) // 2]


def _overlap(a0, a1, b0, b1):
    lo, hi = max(a0, b0), min(a1, b1)
    if hi <= lo:
        return 0.0
    return float(hi - lo) / max(1, min(a1 - a0, b1 - b0))


def detect_grid(gray_arr):
    """用「最长连续暗像素段」找表格网格线。
    不能用整页暗像素总数判断——页面下半部的小表格会漏检。
    返回 (xs, ys) 或 None。"""
    try:
        h, w = gray_arr.shape
        dark = gray_arr < 160

        vcand = []
        for x in range(w):
            best, y0, y1 = _longest_run(dark[:, x])
            if best >= max(30, int(h * 0.05)):
                vcand.append((x, y0, y1, best))
        hcand = []
        for y in range(h):
            best, x0, x1 = _longest_run(dark[y, :])
            if best >= max(40, int(w * 0.08)):
                hcand.append((y, x0, x1, best))
        if len(vcand) < 2 or len(hcand) < 2:
            return None

        # 按坐标聚类（粗线会占多个像素）
        def cluster(cands, key=0, gap=6):
            out = []
            for c in sorted(cands, key=lambda t: t[0]):
                if out and c[0] - out[-1][-1][0] <= gap:
                    out[-1].append(c)
                else:
                    out.append([c])
            return out

        vgroups = cluster(vcand)
        hgroups = cluster(hcand)

        # 筛掉长度明显短于最长组的零散线
        vmax = max(g[0][3] for g in vgroups)
        hmax = max(g[0][3] for g in hgroups)
        vkeep = [g for g in vgroups if max(c[3] for c in g) >= vmax * 0.75]
        hkeep = [g for g in hgroups if max(c[3] for c in g) >= hmax * 0.75]
        if len(vkeep) < 2 or len(hkeep) < 2:
            return None

        vs = [(int(sum(c[0] for c in g) / len(g)), min(c[1] for c in g),
               max(c[2] for c in g)) for g in vkeep]
        hs = [(int(sum(c[0] for c in g) / len(g)), min(c[1] for c in g),
               max(c[2] for c in g)) for g in hkeep]

        # 互相验证：竖线的纵跨度要对得上，横线的横跨度要对得上
        vy0, vy1 = _spans(vs)
        vs = [v for v in vs if _overlap(v[1], v[2], vy0, vy1) >= 0.7]
        if len(vs) < 2:
            return None
        hx0, hx1 = _spans(hs)
        hs = [v for v in hs if _overlap(v[1], v[2], hx0, hx1) >= 0.7]
        if len(hs) < 2:
            return None

        # 网格必须真正相交：竖线的 y 范围覆盖横线，反之亦然
        ty0, ty1 = min(v[1] for v in vs), max(v[2] for v in vs)
        tx0, tx1 = min(v[1] for v in hs), max(v[2] for v in hs)
        hs = [h_ for h_ in hs if ty0 - 8 <= h_[0] <= ty1 + 8]
        vs = [v for v in vs if tx0 - 8 <= v[0] <= tx1 + 8]
        if len(vs) < 2 or len(hs) < 2:
            return None
        if len(vs) > 12 or len(hs) > 30:
            return None
        return [v[0] for v in vs], [h_[0] for h_ in hs]
    except Exception:
        return None


def group_peaks(idx, gap=6):
    if not idx:
        return []
    idx = sorted(idx)
    groups = [[idx[0]]]
    for v in idx[1:]:
        if v - groups[-1][-1] <= gap:
            groups[-1].append(v)
        else:
            groups.append([v])
    return [int(sum(g) / float(len(g))) for g in groups]


def join_cell_lines(items):
    """单元格内可能有多行文字。先按 y 聚成行，行内按 x 排序，再逐行拼接。
    直接按 (y, x) 排序会在 y 略有抖动时把同一行的两段调换顺序。"""
    if not items:
        return ""
    items = sorted(items, key=lambda d: d["y"])
    rows = []
    for it in items:
        if rows and abs(it["y"] - rows[-1]["y"]) <= max(it["h"], rows[-1]["h"]) * 0.6:
            rows[-1]["items"].append(it)
        else:
            rows.append({"y": it["y"], "h": it["h"], "items": [it]})
    out = []
    for r in rows:
        seg = "".join(x["text"] for x in sorted(r["items"], key=lambda d: d["x"]))
        out.append(seg)
    return "".join(out).strip()


def ocr_table(im_gray, xs, ys):
    rows = []
    for i in range(len(ys) - 1):
        cells = []
        for j in range(len(xs) - 1):
            x0, x1 = xs[j] + 2, xs[j + 1] - 2
            y0, y1 = ys[i] + 2, ys[i + 1] - 2
            if x1 - x0 < 6 or y1 - y0 < 6:
                cells.append("")
                continue
            crop = im_gray.crop((x0, y0, x1, y1))
            big = crop.resize((crop.width * 4, crop.height * 4), Image.LANCZOS)
            cells.append(normalize_punct(apply_confusion(
                join_cell_lines(ocr_array(np.array(big.convert("RGB")))))))
        rows.append(cells)
    return rows


# ---------------- 常见 OCR 混淆纠正 ----------------
# 只放「几乎不会出现在正确文本里」的错字，避免把对的字改错。
# 形近字纠正是高置信度错误的唯一有效手段——这类错误 OCR 自评分数很高，
# 多遍识别与裁切复识都纠正不了，只能靠字表。
CONFUSION = [
    # 责任 / 职责
    ("资任", "责任"), ("负资人", "负责人"), ("负资", "负责"), ("责资", "职责"),
    ("履资", "履责"), ("尽职资", "尽职"),
    # 警告 / 奖
    ("整告", "警告"), ("梦告", "警告"), ("紊奖", "嘉奖"), ("奖厉", "奖励"),
    # 需要 / 需求
    ("篇要", "需要"), ("篇求", "需求"), ("斋求", "需求"), ("确篇", "确需"),
    # 晋升 / 等级
    ("普升", "晋升"), ("音升", "晋升"), ("开级", "升级"), ("筝级", "等级"),
    # 患者 / 健康
    ("息者", "患者"), ("使康", "健康"),
    # 禁止 / 撤销
    ("蔡止", "禁止"), ("撒销", "撤销"), ("撒离", "擅离"), ("撒自", "擅自"),
    # 累计 / 给予 / 迟到
    ("察计", "累计"), ("给子", "给予"), ("退到", "迟到"), ("退延", "拖延"),
    # 推诿 / 隐瞒 / 瞒报
    ("推读", "推诿"), ("隐喘", "隐瞒"), ("睛报", "瞒报"),
    # 问题 / 程度
    ("间题", "问题"), ("程废", "程度"), ("复杂废", "复杂度"),
    # 基础 / 严重 / 污秽
    ("蒸础", "基础"), ("产置", "严重"), ("污移", "污秽"), ("污秒", "污秽"),
    # 备份 / 磁盘 / 越权 / 篡改
    ("各份", "备份"), ("碰盘", "磁盘"), ("藏权", "越权"), ("集改", "篡改"),
    # 探索 / 浪费 / 简化
    ("探素", "探索"), ("浪裂", "浪费"), ("筒化", "简化"),
    # 阻止 / 损坏 / 缺陷
    ("阻挽", "阻挠"), ("损环", "损坏"), ("证陷", "缺陷"),
    # 制除 / 删除
    ("制除", "删除"),
    # 专有名词
    ("0A系统", "OA系统"), ("P0C", "POC"),
    # ---- 以下为全量对比实测补充的高频词级形近字 ----
    # 这些字本身都是常用字（子、递、造、置、素…），不能整字替换，
    # 只能按「几乎不会出现在正确文本里」的词组替换。
    # “职责”被识成“职资”
    ("职资", "职责"),
    # “于/子”（于被识成子）
    ("属子", "属于"), ("用子", "用于"), ("对子", "对于"),
    ("至子", "至于"), ("基子", "基于"),
    # “违/递”（违被识成递）
    ("递反", "违反"), ("递纪", "违纪"), ("递规", "违规"),
    ("递约", "违约"), ("递法", "违法"),
    # “适/造”（适被识成造）
    ("造用", "适用"), ("造当", "适当"), ("造合", "适合"),
    # “重/置”（重被识成置）——“严重警告”误成“严置警告”很常见
    ("严置", "严重"), ("置复", "重复"), ("置大", "重大"),
    ("置要", "重要"), ("置点", "重点"), ("置新", "重新"),
    # “索/素”互误
    ("素赔", "索赔"),
    # 其他高频词组
    ("男行", "另行"), ("款意", "故意"), ("暂学手册", "哲学手册"),
    ("繁改", "篡改"), ("设各", "设备"), ("各案", "备案"),
    ("股打", "殴打"), ("缺告", "诬告"), ("被追", "被迫"),
    ("剧新", "刷新"), ("量然", "虽然"), ("意息", "意思"),
    ("待遗", "待遇"), ("案体合同", "集体合同"), ("事岗", "串岗"),
    ("妙架", "吵架"), ("一级故障十", "一级故障+"),
    # ---- 第二轮全量对比补充 ----
    # 注意：这里同样只放词组，不放单字。
    ("瘫滨", "瘫痪"), ("量不完全", "虽不完全"), ("不子", "不予"),
    ("因亲", "因素"), ("隐关键", "隐瞒关键"), ("归资", "归责"),
    ("线素", "线索"), ("斗股", "斗殴"), ("迫究", "追究"),
    ("胁追", "胁迫"), ("相造应", "相适应"), ("具各", "具备"),
    ("因集关系", "因果关系"), ("妙闹", "吵闹"), ("范图", "范围"),
    ("、缺陷等严重", "、诬陷等严重"), ("息想", "思想"),
    ("遗突发", "遇突发"), ("案体", "集体"), ("置属", "直属"),
    ("应子", "应予"), ("隐财务", "隐瞒财务"), ("限子", "限于"),
    ("等同子", "等同于"), ("隐确", "隐瞒"), ("影响招、", "影响招聘、"),
    ("推、抱怨", "推诿、抱怨"), ("故意隐、", "故意隐瞒、"),
    ("故障十", "故障+"), ("不真同时", "不再同时"),
    # 用户实际文本里"需求"常被拆成"篇\n求"，按词组纠正
    ("修改篇", "修改需"), ("实际求", "实际需求"),
    ("，求变更", "，需求变更"), ("客户原因，求", "客户原因，需"),
    ("钉钉0A", "钉钉OA"), ("钉钉0a", "钉钉OA"),
    ("员工隐事实", "员工隐瞒事实"), ("原因、求变更", "原因、需求变更"),
]


CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩"
RE_PAREN = re.compile(r"[（(]\s*(\d{1,2})\s*[）)]")
RE_LETTER_ITEM = re.compile(
    r"(?<=[\u4e00-\u9fff；;：:。，,])\s*([a-fA-F])[.．](?=[\u4e00-\u9fff])")


def fix_circled(text):
    """圈号序号被 OCR 认错时按出现顺序重新编号。
    只在「已经乱序」时动手（如 ①②③⑤⑤ → ①②③④⑤），本来就正确的段落原样返回。"""
    pos = [(i, CIRCLED.index(c)) for i, c in enumerate(text) if c in CIRCLED]
    if len(pos) < 2:
        return text
    idx = [p[1] for p in pos]
    if all(idx[i] < idx[i + 1] for i in range(len(idx) - 1)):
        return text
    out = list(text)
    for n, (i, _) in enumerate(pos):
        if n < len(CIRCLED):
            out[i] = CIRCLED[n]
    return "".join(out)


def fix_parens(text):
    """同一段里全角/半角括号混用时统一成全角。
    中文公文不会出现半角括号与全角右括号并存的情况，这是安全的判据。"""
    if "(" in text and "）" in text:
        text = text.replace("(", "（")
    if ")" in text and "（" in text:
        text = text.replace(")", "）")
    # 序号括号一律全角：(1) → （1）
    return RE_PAREN.sub(lambda m: "（%s）" % m.group(1), text)


def fix_letter_items(text):
    """a.b.c. 这类夹在句子中间的列表项，OCR 会丢全角点。还原成 a．b．"""
    return RE_LETTER_ITEM.sub(lambda m: m.group(1) + "．", text)


def apply_confusion(text):
    for bad, good in CONFUSION:
        if bad in text:
            text = text.replace(bad, good)
    return fix_letter_items(fix_parens(fix_circled(text)))


CJK = "\u4e00-\u9fff"
RE_HALF_AFTER_CJK = re.compile(r"(?<=[%s])([(),;:!?])" % CJK)
RE_HALF_BEFORE_CJK = re.compile(r"([(),;:!?])(?=[%s])" % CJK)
HALF2FULL = {",": "\uff0c", "(": "\uff08", ")": "\uff09", ";": "\uff1b",
             ":": "\uff1a", "!": "\uff01", "?": "\uff1f"}


def normalize_punct(text):
    """把中文上下文里的半角标点换成全角标点。
    不碰英文与数字周围的标点（如 5.0、A/B、(试行) 不会被改）。"""
    def rep_left(m):
        return HALF2FULL[m.group(1)]

    def rep_right(m):
        return HALF2FULL[m.group(1)]
    text = RE_HALF_AFTER_CJK.sub(rep_left, text)
    text = RE_HALF_BEFORE_CJK.sub(rep_right, text)
    return text



SENT_END = "。；：！？…”』」）)】"
RE_HEAD_H1 = re.compile(r"^\s*[一二三四五六七八九十百]+、")
RE_HEAD_H2 = re.compile(r"^\s*[（(]\s*[一二三四五六七八九十百]+\s*[）)]")
RE_HEAD_H3 = re.compile(r"^\s*\d+\s*[、．.]\s*")


def drop_page_numbers(lines, im_h, im_w):
    """去掉页脚页码：位于页面底部、内容为纯数字（或 -1-、第1页 之类）。"""
    keep = []
    for ln in lines:
        t = ln["text"].strip()
        near_bottom = ln["y"] > im_h * 0.92
        centerish = abs(ln["x"] + ln["w"] / 2.0 - im_w / 2.0) < im_w * 0.2
        if near_bottom and centerish and re.fullmatch(
                r"[-—\s]*\d{1,3}[-—\s]*|第\s*\d{1,3}\s*页|共\s*\d+\s*页", t or ""):
            continue
        keep.append(ln)
    return keep


def is_heading_like(t):
    """粗判一行是不是标题/附件名（这种行永远不该与上一页合并）。"""
    t = (t or "").strip()
    if not t:
        return False
    if len(t) > 40:
        return False
    return bool(RE_HEAD_H1.match(t) or RE_HEAD_H2.match(t)
                or RE_HEAD_H3.match(t) or re.match(r"^\s*附件\s*\d+", t))


def lines_to_blocks(lines, state=None):
    """按行首缩进判断段首，把行拼成段落，并粗判标题层级。

    state 是可选 dict，会填入本页首行的缩进信息，供跨页断段合并使用：
      state["first_indented"]  —— 首行是否缩进（缩进=新段落；顶格=承接上一页）
      state["first_text"]      —— 首行文本
    """
    if not lines:
        return []
    lefts = [ln["x"] for ln in lines if len(ln["text"]) > 3]
    if not lefts:
        lefts = [ln["x"] for ln in lines]
    base_left = min(lefts) if lefts else 0.0
    heights = [ln["h"] for ln in lines if ln["h"] > 0] or [20.0]
    med_h = float(np.median(heights))
    indent_gap = med_h * 0.85
    if state is not None:
        state["first_indented"] = bool(lines[0]["x"] > base_left + indent_gap)
        state["first_text"] = lines[0]["text"]

    paras = []
    cur = None
    for ln in lines:
        text = ln["text"]
        if not text:
            continue
        is_new = (cur is None) or (ln["x"] > base_left + indent_gap)
        if cur is not None and not is_new:
            if cur["text"] and cur["text"][-1] in SENT_END:
                is_new = True
        if is_new:
            if cur:
                paras.append(cur)
            cur = {"text": text, "x": ln["x"], "score": ln["score"]}
        else:
            cur["text"] += text
            cur["score"] = min(cur["score"], ln["score"])
    if cur:
        paras.append(cur)

    blocks = []
    for p in paras:
        t = normalize_punct(apply_confusion(p["text"].strip()))
        if not t:
            continue
        if RE_HEAD_H1.match(t) and len(t) <= 30:
            blocks.append(("h1", t))
        elif RE_HEAD_H2.match(t) and len(t) <= 30:
            blocks.append(("h2", t))
        elif RE_HEAD_H3.match(t) and len(t) <= 40:
            blocks.append(("h3", t))
        elif re.match(r"^\s*附件\s*\d+", t) and len(t) <= 40:
            blocks.append(("caption", t))
        else:
            blocks.append(("body", t))
    return blocks


def is_reference_grid(rows):
    """过滤明显误检的网格（单元格几乎全空或行数过少）。"""
    if len(rows) < 2:
        return False
    filled = sum(1 for r in rows for c in r if c)
    total = sum(len(r) for r in rows)
    return total > 0 and filled >= max(2, total * 0.2)


# ---------------- PDF：文字层直接抽取 ----------------
RE_CJK = "\u4e00-\u9fff\u3400-\u4dbf"
RE_CJK_PUNCT = "\u3000-\u303f\uff00-\uffef"
RE_ASCII = "0-9A-Za-z"

# PDF 导出时 Word 会在「汉字 ↔ 汉字 / 数字 / 字母 / 全角标点」之间插入
# 自动间距，抽取出来就是多余的空格（"5 个工作日"、"钉钉OA 发起"）。
# 规则：只要空格左边或右边有一侧是汉字/全角标点，就去掉这个空格；
# 两侧都是 ASCII（"Windows 10"、"A B"）时保留，英文不会被粘连。
_LEFT_CJK = re.compile(r"(?<=[%s%s])[ \t]+" % (RE_CJK, RE_CJK_PUNCT))
_RIGHT_CJK = re.compile(r"[ \t]+(?=[%s%s])" % (RE_CJK, RE_CJK_PUNCT))


def despace_cjk(t):
    """去掉抽取器在汉字周围插入的自动间距；两侧都是 ASCII 的空格保留。"""
    if not t:
        return t
    prev = None
    while prev != t:
        prev = t
        t = _LEFT_CJK.sub("", t)
        t = _RIGHT_CJK.sub("", t)
    return t


def _cell_text(pg, rect):
    """按单元格矩形取文字。

    不能用 Table.extract()——它会把单元格里被折行的内容拆成
    "口头表扬\\n、\\n书面表扬" 这种碎片。这里取词框、按行聚、行内按 x 排，
    再按字符类型决定要不要补空格。
    """
    x0, y0, x1, y1 = rect
    words = [w for w in pg.get_text("words")
             if w[0] >= x0 - 2 and w[2] <= x1 + 2
             and w[1] >= y0 - 2 and w[3] <= y1 + 2]
    if not words:
        return ""
    words.sort(key=lambda w: (round((w[1] + w[3]) / 2.0), w[0]))

    rows, cur = [], []
    for w in words:
        yc = (w[1] + w[3]) / 2.0
        if cur and abs(yc - cur[0]) > max(3.0, (w[3] - w[1]) * 0.6):
            rows.append(cur[1])
            cur = []
        if not cur:
            cur = [yc, []]
        cur[1].append(w)
    if cur:
        rows.append(cur[1])

    parts = []
    for ws in rows:
        ws.sort(key=lambda w: w[0])
        line = ""
        for w in ws:
            s = w[4]
            if line and line[-1].isascii() and line[-1].isalnum() \
                    and s[:1].isascii() and s[:1].isalnum():
                line += " "
            line += s
        parts.append(despace_cjk(line))
    return "".join(parts)


def pdf_extract(pdf_path):
    """把 PDF 每页拆成「文字页」或「图片页」描述。

    返回 [(名字, kind, payload)]：
      kind == "text"  → payload = {lines, tables, w, h}
      kind == "image" → payload = PIL 灰度图（走 OCR）
    """
    import pymupdf

    out = []
    base = os.path.basename(pdf_path)
    doc = pymupdf.open(pdf_path)
    try:
        for i in range(len(doc)):
            pg = doc[i]
            name = "%s#p%d" % (base, i + 1)
            raw = pg.get_text("text") or ""
            nchar = len(re.sub(r"\s", "", raw))

            # 判断这页到底该按文字处理还是按图片处理
            img_area = 0.0
            try:
                for info in pg.get_images(full=True):
                    for r in pg.get_image_rects(info[0]):
                        img_area += abs(r.width * r.height)
            except Exception:
                pass
            page_area = max(1.0, abs(pg.rect.width * pg.rect.height))
            big_image = img_area / page_area >= 0.7

            as_text = nchar >= 80 and not (big_image and nchar < 400)

            if as_text:
                lines = []
                d = pg.get_text("dict")
                for blk in d.get("blocks", []):
                    if blk.get("type") != 0:
                        continue
                    for ln in blk.get("lines", []):
                        txt = "".join(s.get("text", "") for s in ln.get("spans", []))
                        txt = despace_cjk(txt)
                        if not txt.strip():
                            continue
                        x0, y0, x1, y1 = ln["bbox"]
                        lines.append({"text": txt.strip(), "x": float(x0), "y": float(y0),
                                      "w": max(1.0, float(x1 - x0)),
                                      "h": max(1.0, float(y1 - y0)), "score": 1.0})
                lines.sort(key=lambda d: (round(d["y"], 1), d["x"]))

                tables = []
                try:
                    tf = pg.find_tables()
                    for t in getattr(tf, "tables", []) or []:
                        try:
                            bbox = tuple(t.bbox)
                            rows = []
                            # 注意：t.cells 是所有单元格矩形的扁平列表，
                            # 按行取必须用 t.rows[i].cells。
                            for row in t.rows:
                                cells = []
                                for c in row.cells:
                                    if not c:
                                        cells.append("")
                                        continue
                                    cx0 = min(c[0], c[2])
                                    cy0 = min(c[1], c[3])
                                    cx1 = max(c[0], c[2])
                                    cy1 = max(c[1], c[3])
                                    cells.append(_cell_text(pg, (cx0, cy0, cx1, cy1)))
                                rows.append(cells)
                        except Exception:
                            continue
                        if rows and any(any(c for c in r) for r in rows):
                            tables.append({"rows": rows, "bbox": bbox})
                except Exception:
                    pass
                tables.sort(key=lambda d: d["bbox"][1])

                out.append((name, "text", {"lines": lines, "tables": tables,
                                           "w": float(pg.rect.width),
                                           "h": float(pg.rect.height)}))
            else:
                # 渲染成图走 OCR。
                # 优先按「页面里那张大图的原始像素数」渲染，而不是固定 dpi——
                # 扫描件 PDF 里嵌的就是原图，按原分辨率取像素，结果与直接喂 PNG 一致；
                # 固定 200dpi 会把 150dpi 的图放大 1.33 倍，字符间隙变大，
                # OCR 就会多识别出一堆空格。
                best = None  # (原始像素宽, 在页面上占的宽度 pt)
                try:
                    for info in pg.get_images(full=True):
                        xref = info[0]
                        placed = None
                        for r in pg.get_image_rects(xref):
                            if r.width > 1 and (placed is None or r.width > placed):
                                placed = float(r.width)
                        if not placed:
                            continue
                        try:
                            pxw = int(doc.extract_image(xref).get("width") or 0)
                        except Exception:
                            pxw = 0
                        if pxw > 1 and (best is None or pxw > best[0]):
                            best = (pxw, placed)
                except Exception:
                    pass

                pix = None
                if best:
                    # 图的原始像素宽 / 它在页面上占的点数 = 该图的原生 dpi
                    native_dpi = int(round(72.0 * best[0] / best[1]))
                    native_dpi = max(72, min(400, native_dpi))
                    try:
                        pix = pg.get_pixmap(dpi=native_dpi)
                    except Exception:
                        pix = None
                if pix is None:
                    pix = pg.get_pixmap(dpi=200)

                img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
                out.append((name, "image", img.convert("L")))
    finally:
        doc.close()
    return out


def process_text_page(name, page, args, report):
    """处理有文字层的 PDF 页：直接取文字，不跑 OCR。"""
    lines = list(page["lines"])
    lines = drop_page_numbers(lines, page["h"], page["w"])

    tables = [] if args.no_tables else list(page["tables"])
    keep_tables = []
    for tb in tables:
        rows = tb["rows"]
        if len(rows) >= 2 and sum(1 for r in rows for c in r if c) >= 2:
            keep_tables.append(tb)
    tables = keep_tables
    for tb in tables:
        report.append("[%s] 文字层表格 %d 行 × %d 列"
                      % (name, len(tb["rows"]), len(tb["rows"][0])))

    # 表格框内的行不再当正文
    def in_table(ln):
        for tb in tables:
            x0, y0, x1, y1 = tb["bbox"]
            cx = ln["x"] + ln["w"] / 2.0
            cy = ln["y"] + ln["h"] / 2.0
            if x0 - 3 <= cx <= x1 + 3 and y0 - 3 <= cy <= y1 + 3:
                return True
        return False

    body = [ln for ln in lines if not in_table(ln)]

    blocks = []
    meta = {}
    state = {}
    cursor = 0
    y_marks = sorted(tb["bbox"][1] for tb in tables)
    for y in y_marks:
        part = [ln for ln in body[cursor:] if ln["y"] < y]
        if part:
            state = {}
            blocks.extend(lines_to_blocks(part, state))
        cursor += len(part)
        tb = next(t for t in tables if t["bbox"][1] == y)
        blocks.append(("table", tb["rows"]))
    rest = body[cursor:]
    if rest:
        state = {}
        blocks.extend(lines_to_blocks(rest, state))
    if not tables:
        state = {}
        blocks = lines_to_blocks(body, state)
    meta = dict(state) if state else {}
    if not meta and lines:
        meta = {"first_indented": False, "first_text": lines[0]["text"]}
    return blocks, [], meta


# ---------------- 主流程 ----------------
def load_pages(paths):
    """返回 [(名字, kind, payload)]；kind 为 'image' 或 'text'。"""
    pages = []
    for p in paths:
        if p.lower().endswith(".pdf"):
            pages.extend(pdf_extract(p))
        else:
            pages.append((os.path.basename(p), "image",
                          Image.open(p).convert("L")))
    return pages


def collect_inputs(args):
    paths = []
    for a in args.input:
        if os.path.isdir(a):
            for ext in ("*.png", "*.jpg", "*.jpeg", "*.bmp", "*.tif", "*.tiff",
                        "*.webp", "*.pdf"):
                paths.extend(glob.glob(os.path.join(a, ext)))
                paths.extend(glob.glob(os.path.join(a, ext.upper())))
        elif os.path.isfile(a):
            paths.append(a)
        else:
            sys.stderr.write("找不到：%s\n" % a)

    def k(p):
        b = os.path.splitext(os.path.basename(p))[0]
        digits = re.findall(r"\d+", b)
        return (0, int(digits[0])) if digits else (1, b)
    return sorted(set(paths), key=k)


def process_page(name, im_gray, args, report):
    raw = np.array(im_gray)

    # 先看有没有表格，但不能像以前那样直接 return——
    # 那会把整页正文（包括附件标题行与表格上下方的段落）全部丢掉。
    table = None
    if not args.no_tables:
        grid = detect_grid(raw)
        if grid:
            rows = ocr_table(im_gray, *grid)
            if is_reference_grid(rows):
                report.append("[%s] 检测到表格 %d 行 × %d 列"
                              % (name, len(rows), len(rows[0]) if rows else 0))
                table = {"rows": rows, "y0": grid[1][0], "y1": grid[1][-1],
                         "x0": grid[0][0], "x1": grid[0][-1]}

    plans = [(1.0, False, 1.0), (2.0, False, 1.0), (2.5, True, 1.35)]
    plans = plans[:max(1, min(3, args.passes))]
    passes = []
    for scale, sharp, contrast in plans:
        arr, sc = prep_pass(im_gray, scale, sharp, contrast)
        passes.append((ocr_array(arr), sc))

    merged = merge_passes(passes)
    merged = drop_page_numbers(merged, im_gray.height, im_gray.width)

    # 表格区域内的行不再当正文（否则与表格内容重复）
    if table:
        keep = []
        for ln in merged:
            inside = (table["y0"] - 6 <= ln["yc"] <= table["y1"] + 6
                      and ln["x"] + ln["w"] > table["x0"]
                      and ln["x"] < table["x1"])
            if not inside:
                keep.append(ln)
        merged = keep

    # 补回整行被吞掉的行
    merged, nfill = fill_gaps(im_gray, merged, report)

    fixed = []
    budget = max(0, int(args.max_recheck))
    for ln in merged:
        if len(fixed) >= budget:
            break
        low = ln["score"] < args.recheck_below
        # 高置信但多遍不一致的行不值得复识：几乎都是对的，只会白花时间
        disagree = ln["disagree"] and ln["score"] < 0.95
        if (low or disagree) and ln["h"] > 8:
            got = recheck_line(im_gray, ln, fixed)
            if got:
                ln["text"], ln["score"] = got
    report.append("[%s] %d 行，复识修正 %d 行" % (name, len(merged), len(fixed)))
    for f in fixed:
        report.append("    " + f)

    skipped = sum(1 for ln in merged
                  if (ln["score"] < args.recheck_below
                      or (ln["disagree"] and ln["score"] < 0.95)) and ln["h"] > 8)
    if skipped > len(fixed):
        report.append("    复识预算用完：%d 行未复识" % (skipped - len(fixed)))
    sus = [ln for ln in merged if ln["score"] < args.recheck_below]
    meta = {"first_indented": True, "first_text": ""}
    if table:
        up = [ln for ln in merged if ln["yc"] < table["y0"]]
        dn = [ln for ln in merged if ln["yc"] > table["y1"]]
        b_up = lines_to_blocks(up, meta if up else None)
        if not up and dn:
            lines_to_blocks(dn, meta)
        blocks = b_up + [("table", table["rows"])] + lines_to_blocks(dn)
    else:
        blocks = lines_to_blocks(merged, meta)
    return blocks, sus, meta


def _looks_like_sentence(t):
    """长且带句号/分号的行其实是正文，不是标题——只是恰好以序号开头。"""
    t = (t or "").strip()
    return len(t) >= 30 and ("。" in t or "；" in t)


def merge_across_pages(prev_blocks, prev_meta, blocks, meta):
    """跨页断段合并。

    上一页最后一段如果既没写完（结尾不是句末标点）又恰好停在半句上，
    而下一页首行是顶格（没有段首缩进）、本身不是标题，
    那说明这一句被页边界切开了，应当接回去。
    """
    if not prev_blocks or not blocks or not prev_meta or not meta:
        return prev_blocks, blocks
    last = prev_blocks[-1]
    # 以序号开头但其实是整句正文的行会被误判成标题，这种也要允许合并
    if not (last[0] == "body"
            or (last[0] in ("h2", "h3") and _looks_like_sentence(last[1]))):
        return prev_blocks, blocks
    tail = last[1].rstrip()
    if not tail or tail[-1] in SENT_END:
        return prev_blocks, blocks
    if not ("\u4e00" <= tail[-1] <= "\u9fff"):
        return prev_blocks, blocks   # 断在英文/数字/标点上，不像被切断
    if meta.get("first_indented", True):
        return prev_blocks, blocks   # 下一页首行有缩进，是新段落
    if is_heading_like(meta.get("first_text", "")):
        return prev_blocks, blocks
    head = blocks[0]
    if head[0] != "body":
        return prev_blocks, blocks
    # 拼接后必须再过一遍错字表：断在「纪律资|任」这种地方，
    # 单看前半截和后半截都认不出错，拼起来才认得出。
    merged = ("body", normalize_punct(apply_confusion(tail + head[1])))
    # 注意：必须同时把上一页那个半截段删掉，否则会输出两遍
    return prev_blocks[:-1], [merged] + blocks[1:]


def join_split_paragraphs(blocks):
    """把被误判成两个段落的半句接回去。

    现象：同一句话被切成「…第一负」+「资人认定的。」两段。
    判据很保守——上一段不带句末标点，下一段不含序号/标题标记且极短。
    接回去之后要再过一遍错字表：断字处的形近字只有拼起来才认得出来。
    """
    out = []
    for b in blocks:
        if out and b[0] == "body" and out[-1][0] in ("body", "h3"):
            prev = out[-1][1].rstrip()
            cur = b[1].strip()
            if (prev and cur and len(cur) <= 12
                    and prev[-1] not in SENT_END
                    and "\u4e00" <= prev[-1] <= "\u9fff"
                    and not is_heading_like(cur)
                    and not re.match(r"^\s*[（(]\s*\d", cur)):
                out[-1] = ("body",
                           normalize_punct(apply_confusion(prev + cur)))
                continue
        out.append(b)
    return out


def dedupe_blocks(blocks):
    """去掉相邻的「半截重复段」——同一段被识别了两遍（一遍完整、一遍只到半句）。

    只处理相邻两块且长度/前缀关系都很强的情况，正常公文不会出现这种相邻重复。
    """
    out = []
    for i, b in enumerate(blocks):
        if out and b[0] == "body" and out[-1][0] == "body":
            t, p = b[1].strip(), out[-1][1].strip()
            if t and p and t != p:
                short, long_ = (t, p) if len(t) < len(p) else (p, t)
                if (long_.startswith(short) and len(short) >= 20
                        and len(short) >= len(long_) * 0.45):
                    # 保留更长的那一份
                    if len(p) < len(t):
                        out[-1] = b
                    continue
        out.append(b)
    return out


RE_TAIL_MARK = re.compile(r"[（(]\s*(\d{1,2})\s*[）)]\s*$")


def move_tail_markers(blocks):
    """把被挤到上一行末尾的序号搬到下一段开头。

    现象：原文「（4）持续时间与恢复方式：……」在识别时序号漂到了上一行行尾，
    变成「……局部可恢复；（4）」+「持续时间与恢复方式：……」。
    """
    out = list(blocks)
    for i in range(len(out) - 1):
        if out[i][0] != "body" or out[i + 1][0] != "body":
            continue
        t = out[i][1]
        m = RE_TAIL_MARK.search(t)
        if not m:
            continue
        nxt = out[i + 1][1]
        if RE_PAREN.match(nxt.lstrip()) or RE_HEAD_H2.match(nxt):
            continue
        head = t[:m.start()].rstrip()
        if len(head) < 6:
            continue
        out[i] = ("body", head)
        out[i + 1] = ("body", "（%s）" % m.group(1) + nxt.lstrip())
    return out


def renumber_paren_runs(blocks):
    """连续编号的「（1）（2）（3）……」段落里出现跳号/重号时，按顺序重编。

    只在第一段确实是（1）、且至少 3 段、且确实有异常时才动手。
    """
    out = list(blocks)
    i, n = 0, len(out)
    while i < n:
        if out[i][0] != "body":
            i += 1
            continue
        m = RE_PAREN.match(out[i][1].lstrip())
        if not m or int(m.group(1)) != 1:
            i += 1
            continue
        j = i
        nums = []
        while j < n and out[j][0] == "body":
            mm = RE_PAREN.match(out[j][1].lstrip())
            if not mm:
                break
            v = int(mm.group(1))
            if v == 1 and nums:
                break          # 新的一组列表开始了，不是同一串
            nums.append(v)
            j += 1
        expect = list(range(1, len(nums) + 1))
        if len(nums) >= 3 and nums != expect:
            for k, val in enumerate(nums):
                t = out[i + k][1]
                out[i + k] = ("body", RE_PAREN.sub("（%d）" % (k + 1), t, count=1))
        i = max(j, i + 1)
    return out


RE_FIRST_CIRCLED = re.compile(r"^\s*(.)")


def renumber_circled_runs(blocks):
    """①②③ 这类圈号在连续段落里出现重号/跳号时按顺序重编。

    允许段间夹着不带圈号的续行；遇到圈号回到 ① 或者非正文块就停。
    """
    out = list(blocks)
    i, n = 0, len(out)
    while i < n:
        if out[i][0] != "body":
            i += 1
            continue
        m = RE_FIRST_CIRCLED.match(out[i][1])
        if not m or m.group(1) != CIRCLED[0]:
            i += 1
            continue
        j, marks = i, []
        while j < n and out[j][0] == "body":
            mm = RE_FIRST_CIRCLED.match(out[j][1])
            if mm and mm.group(1) in CIRCLED:
                idx = CIRCLED.index(mm.group(1))
                if idx == 0 and marks:
                    break
                marks.append((j, idx))
            j += 1
        if len(marks) >= 3 and [k for _, k in marks] != list(range(len(marks))):
            for order, (pos, _) in enumerate(marks):
                if order < len(CIRCLED):
                    t = out[pos][1].lstrip()
                    out[pos] = ("body", CIRCLED[order] + t[1:])
        i = max(j, i + 1)
    return out


def blocks_to_text(blocks):
    out = []
    for kind, payload in blocks:
        if kind == "table":
            for r in payload:
                out.append("| " + " | ".join(r) + " |")
        else:
            out.append(payload)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="把图片或 PDF 识别成 Word 初稿（需人工校对）")
    ap.add_argument("input", nargs="+", help="图片文件、图片目录或 PDF")
    ap.add_argument("-o", "--output", default=None, help="输出 docx（默认 初稿.docx）")
    ap.add_argument("--title", default=None, help="文档标题；不填则取首页首行")
    ap.add_argument("--recheck-below", type=float, default=0.86,
                    help="置信度低于此值的行做裁切复识，默认 0.86")
    ap.add_argument("--passes", type=int, default=2, choices=(1, 2, 3),
                    help="识别遍数，默认 2；越多越慢")
    ap.add_argument("--max-recheck", type=int, default=8,
                    help="每页最多复识行数，默认 12；0 表示不复识")
    ap.add_argument("--no-tables", action="store_true", help="不做表格检测")
    ap.add_argument("--no-docx", action="store_true", help="只输出 txt 与报告")
    ap.add_argument("--owner", default=gongwen.DEFAULT_OWNER,
                    help="版权归属方，默认 %s；传空字符串可关闭" % gongwen.DEFAULT_OWNER)
    ap.add_argument("--version", default=gongwen.DEFAULT_VERSION,
                    help="版本号，默认 %s；传空字符串可关闭" % gongwen.DEFAULT_VERSION)
    ap.add_argument("--no-copyright-note", action="store_true",
                    help="不显示文末版权声明（页脚仍显示）")
    ap.add_argument("--no-copyright", action="store_true",
                    help="完全不显示版权与版本号（页脚只留页码）")
    args = ap.parse_args(argv)

    if args.no_copyright:
        args.owner = ""
        args.version = ""
        args.no_copyright_note = True

    paths = collect_inputs(args)
    if not paths:
        sys.stderr.write("没有找到可用的图片或 PDF。\n")
        return 2

    pages = load_pages(paths)
    n_text = sum(1 for _, k, _ in pages if k == "text")
    print("共 %d 页待处理（其中 %d 页有文字层，直接抽取；%d 页走识别）"
          % (len(pages), n_text, len(pages) - n_text), flush=True)

    all_blocks = []
    report = []
    all_sus = []
    prev_meta = None
    for idx, (name, kind, payload) in enumerate(pages, 1):
        if kind == "text":
            print("  抽取 %d/%d  %s  (PDF 文字层)" % (idx, len(pages), name),
                  flush=True)
            blocks, sus, meta = process_text_page(name, payload, args, report)
        else:
            print("  识别 %d/%d  %s" % (idx, len(pages), name), flush=True)
            blocks, sus, meta = process_page(name, payload, args, report)
        if prev_meta is not None:
            all_blocks, blocks = merge_across_pages(all_blocks, prev_meta,
                                                    blocks, meta)
        prev_meta = meta
        all_blocks.extend(blocks)
        all_sus.extend((name, s) for s in sus)

    # 收尾：接回被切开的半句、去掉相邻的重复半段、修正漂移的序号
    all_blocks = join_split_paragraphs(dedupe_blocks(all_blocks))
    all_blocks = move_tail_markers(all_blocks)
    all_blocks = renumber_paren_runs(all_blocks)
    all_blocks = renumber_circled_runs(all_blocks)

    # 首页首行作为标题
    if args.title:
        all_blocks.insert(0, ("title", args.title))
    elif all_blocks and all_blocks[0][0] == "body" and len(all_blocks[0][1]) <= 60:
        all_blocks[0] = ("title", all_blocks[0][1])

    out = args.output or "初稿.docx"
    base = os.path.splitext(out)[0]

    # txt 底稿（人工校对用）
    lines = blocks_to_text(all_blocks)
    txt_path = base + ".txt"
    with io.open(txt_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    if not args.no_docx:
        gongwen.build_from_blocks(all_blocks, out,
                                 owner=args.owner or None,
                                 version=args.version or None,
                                 copyright_note=not args.no_copyright_note)
        print("已生成：%s" % out)
    print("已生成：%s" % txt_path)

    # 报告
    rp = base + ".report.txt"
    with io.open(rp, "w", encoding="utf-8") as f:
        f.write("\n".join(report))
        f.write("\n\n===== 仍存疑的行（建议人工核对）=====\n")
        if all_sus:
            for name, s in all_sus:
                f.write("[%s] score=%.2f  %s\n" % (name, s["score"], s["text"]))
        else:
            f.write("无\n")
    print("已生成：%s" % rp)
    print("\n提示：这是初稿，标题层级与表格为自动推断，文字未经校订，请通读后再用。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
