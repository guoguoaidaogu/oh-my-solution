#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""report_unresolved_refs.py — 整理未解析的交叉引用（`??`），供人工检查。

在生成页里，未解析的 `\\ref{标签}` 会被渲染成：

    <span class="btex-link missing" title="unresolved reference">
      <span><span class="btex-ref missing">??</span></span>
    </span>

本脚本把这些 `??` 与 `.btx` 源文件中的 `\\ref{...}` 逐一对齐，给出：

* HTML 中的精确位置（行:列）与渲染上下文；
* 对应的 `\\ref{标签}` 与 btx 中的位置、引用片段；
* 标签状态：
  - `cross-page` 该标签定义在同一合集的**另一页**，静态单页 HTML 无法解析；
  - `dangling`   全归档都没有定义（疑似笔误或漏写 `\\label`）；
* 形近/前后缀候选标签，帮助定位可能的正确目标；
* 标签定义处所在环境（定理/引理/推论……）与摘要。

输出 Markdown 报告与 JSON，供人工逐条判断。

用法
----
    python3 tools/report_unresolved_refs.py
    python3 tools/report_unresolved_refs.py --md tools/reports/unresolved_refs.md \
        --json tools/reports/unresolved_refs.json
"""

from __future__ import annotations

import argparse
import difflib
import html
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_missing_links as C  # noqa: E402

REF_RE = re.compile(r"\\ref\{([^}]*)\}")
LABEL_RE = re.compile(r"\\label\{([^}]*)\}")
BEGIN_RE = re.compile(r"\\begin\{([^}]*)\}")
END_RE = re.compile(r"\\end\{[^}]*\}")
REF_SPAN_RE = re.compile(r'<span class="btex-ref missing">\?\?</span>')
ANY_TAG_RE = re.compile(r"<[^>]*>")

ENV_CN = {
    "theorem": "定理",
    "proposition": "命题",
    "lemma": "引理",
    "corollary": "推论",
    "definition": "定义",
    "example": "例",
    "remark": "注",
    "claim": "断言",
    "conjecture": "猜想",
}


@dataclass
class RefEntry:
    index: int
    page_title: str          # 页面名（archive 内相对路径去掉扩展名）
    html_file: str           # 仓库相对路径
    html_line: int
    html_column: int
    html_context: str        # 渲染后的上下文（纯文本，含 ??）
    btx_file: str
    btx_line: int
    ref_label: str           # \ref{...} 中的标签
    ref_kind: str            # 引用处的类型词，如 推论 / Theorem
    btx_context: str         # 引用所在 btx 行片段
    status: str              # cross-page / dangling / local(late)
    defined_in: list         # 定义位置 [{file,line,env,env_cn,snippet}]
    suggestions: list        # [{label, reason, file, line, env, env_cn, snippet}]


def line_of(text: str, index: int) -> int:
    return text.count("\n", 0, index) + 1


def global_labels(archive: Path) -> dict[str, list[tuple[Path, int]]]:
    """标签 -> [(btx 路径, 位置)]，跨全部 *.btx。"""
    index: dict[str, list[tuple[Path, int]]] = {}
    for btx in sorted(archive.rglob("*.btx")):
        text = btx.read_text(encoding="utf-8", errors="replace")
        for m in LABEL_RE.finditer(text):
            index.setdefault(m.group(1), []).append((btx, m.start()))
    return index


def label_context(text: str, pos: int) -> tuple[str | None, str]:
    """返回标签定义处的 (环境名, 摘要文本)。"""
    begins = [(m.start(), m.group(1)) for m in BEGIN_RE.finditer(text) if m.start() < pos]
    ends = [m.start() for m in END_RE.finditer(text) if m.start() < pos]
    env = None
    seg_start = text.rfind("\n", 0, pos) + 1
    if begins:
        bstart, envname = begins[-1]
        if not any(e > bstart for e in ends):
            env, seg_start = envname, bstart
    seg = text[seg_start:pos]
    seg = re.sub(r"\\begin\{[^}]*\}|\\label\{[^}]*\}|\\end\{[^}]*\}", " ", seg)
    seg = re.sub(r"\s+", " ", seg).strip(" 。.;,，、}])")
    if len(seg) > 180:
        seg = "…" + seg[-180:]
    return env, seg


def ref_kind(text: str, start: int) -> str:
    """从 `\\ref` 前的文本推断引用类型词（中文或英文）。"""
    pre = text[max(0, start - 24):start]
    m = re.search(r"([\u4e00-\u9fff]{1,6})\s*\[?\s*$", pre)
    if m:
        return m.group(1)
    m = re.search(r"([A-Za-z]{3,})\s*~?\s*$", pre)
    return m.group(1) if m else ""


def find_suggestions(label: str, index: dict[str, list[tuple[Path, int]]],
                     archive: Path, repo_root: Path) -> list[dict]:
    """给出形近/前后缀候选标签。"""
    cands = list(index.keys())
    found: list[tuple[str, str]] = []
    if ":" in label:
        bare = label.split(":", 1)[1]
        if bare in index:
            found.append((bare, "去掉前缀"))
    for c in cands:
        if ":" in c and c.split(":", 1)[1] == label:
            found.append((c, "补上前缀"))
    for c in difflib.get_close_matches(label, cands, n=3, cutoff=0.72):
        found.append((c, "形近"))

    out, seen = [], set()
    for cand, reason in found:
        if cand in seen or cand == label:
            continue
        seen.add(cand)
        btx, pos = index[cand][0]
        text = btx.read_text(encoding="utf-8", errors="replace")
        env, snippet = label_context(text, pos)
        out.append({
            "label": cand,
            "reason": reason,
            "file": btx.relative_to(repo_root).as_posix(),
            "line": line_of(text, pos),
            "env": env,
            "env_cn": ENV_CN.get(env or "", env or ""),
            "snippet": snippet,
        })
    return out


def html_context(text: str, start: int, end: int, width: int = 140,
                 window: int = 800) -> str:
    """以 `??` 为中心取一段干净的纯文本上下文。

    先用哨兵标记目标 span，再去标签、折叠空白，避免切片把标签截断后残留半截标签。
    """
    raw = (
        text[max(0, start - window):start]
        + "\x00"
        + text[end:min(len(text), end + window)]
    )
    cleaned = html.unescape(ANY_TAG_RE.sub("", raw))
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    i = cleaned.find("\x00")
    if i == -1:
        return cleaned[: width * 2]
    cleaned = cleaned.replace("\x00", "??")
    i = cleaned.find("??")
    lo, hi = max(0, i - width), min(len(cleaned), i + 2 + width)
    return ("…" if lo > 0 else "") + cleaned[lo:hi] + ("…" if hi < len(cleaned) else "")


def collect(archive: Path, repo_root: Path) -> list[RefEntry]:
    index = global_labels(archive)
    entries: list[RefEntry] = []

    for hfile in sorted(archive.rglob("*.html")):
        htext = hfile.read_text(encoding="utf-8", errors="replace")
        spans = list(REF_SPAN_RE.finditer(htext))
        if not spans:
            continue

        bfile = hfile.with_suffix(".btx")
        if not bfile.exists():
            print(f"警告：找不到对应 btx：{hfile}", file=sys.stderr)
            continue
        btext = bfile.read_text(encoding="utf-8", errors="replace")

        local_labels = set(LABEL_RE.findall(btext))
        undefined = [
            m for m in REF_RE.finditer(btext) if m.group(1) not in local_labels
        ]
        if len(undefined) != len(spans):
            print(
                f"警告：{hfile.name} 的 ?? 数（{len(spans)}）与未定义 \\ref 数"
                f"（{len(undefined)}）不一致，将按顺序取较短者。",
                file=sys.stderr,
            )

        page_title = hfile.relative_to(archive).as_posix()[: -len(".html")]
        for i, (span, ref) in enumerate(zip(spans, undefined), start=1):
            label = ref.group(1)
            definitions = index.get(label, [])
            if not definitions:
                status = "dangling"
            elif any(p.resolve() == bfile.resolve() for p, _ in definitions):
                status = "local-late"
            else:
                status = "cross-page"

            defs = []
            for btx, pos in definitions:
                t = btx.read_text(encoding="utf-8", errors="replace")
                env, snippet = label_context(t, pos)
                defs.append({
                    "file": btx.relative_to(repo_root).as_posix(),
                    "line": line_of(t, pos),
                    "env": env,
                    "env_cn": ENV_CN.get(env or "", env or ""),
                    "snippet": snippet,
                })

            b_line_start = btext.rfind("\n", 0, ref.start()) + 1
            b_line_end = btext.find("\n", ref.start())
            if b_line_end == -1:
                b_line_end = len(btext)
            entries.append(
                RefEntry(
                    index=i,
                    page_title=page_title,
                    html_file=hfile.relative_to(repo_root).as_posix(),
                    html_line=line_of(htext, span.start()),
                    html_column=span.start() - htext.rfind("\n", 0, span.start()),
                    html_context=html_context(htext, span.start(), span.end()),
                    btx_file=bfile.relative_to(repo_root).as_posix(),
                    btx_line=line_of(btext, ref.start()),
                    ref_label=label,
                    ref_kind=ref_kind(btext, ref.start()),
                    btx_context=re.sub(r"\s+", " ", btext[b_line_start:b_line_end]).strip(),
                    status=status,
                    defined_in=defs,
                    suggestions=find_suggestions(label, index, archive, repo_root),
                )
            )
    # 重新编号：每个页面内部从 1 开始
    per_page: dict[str, int] = {}
    for e in entries:
        per_page[e.page_title] = per_page.get(e.page_title, 0) + 1
        e.index = per_page[e.page_title]
    return entries


def render_markdown(entries: list[RefEntry], archive: Path) -> str:
    by_page: dict[str, list[RefEntry]] = {}
    for e in entries:
        by_page.setdefault(e.page_title, []).append(e)

    n_cross = sum(1 for e in entries if e.status == "cross-page")
    n_dangling = sum(1 for e in entries if e.status == "dangling")
    n_local = sum(1 for e in entries if e.status == "local-late")

    out = []
    out.append("# 未解析交叉引用清单（人工检查用）\n")
    out.append(f"- 扫描目录：`{archive}`")
    out.append(f"- 合计：**{len(entries)}** 处，分布在 **{len(by_page)}** 个页面")
    out.append(f"- 状态：跨页引用 `cross-page` **{n_cross}** 处；"
               f"悬空引用 `dangling` **{n_dangling}** 处；"
               f"同页后置定义 `local-late` **{n_local}** 处\n")

    out.append("| 页面 | 处数 | 跨页 | 悬空 |")
    out.append("| --- | ---: | ---: | ---: |")
    for page, items in by_page.items():
        out.append(
            f"| `{page}` | {len(items)} | "
            f"{sum(1 for e in items if e.status == 'cross-page')} | "
            f"{sum(1 for e in items if e.status == 'dangling')} |"
        )
    out.append("")

    status_cn = {"cross-page": "跨页引用", "dangling": "悬空引用", "local-late": "同页后置定义"}
    out.append("## 速查表\n")
    out.append("| 页面 | # | 标签 | 引用词 | 状态 | 候选 / 定义页 |")
    out.append("| --- | ---: | --- | --- | --- | --- |")
    for e in entries:
        page_short = e.page_title.split("/")[-1]
        if e.suggestions:
            hint = "；".join(f"`{s['label']}`({s['reason']})" for s in e.suggestions[:2])
        elif e.defined_in:
            hint = "`" + e.defined_in[0]["file"].split("/")[-1] + "`"
        else:
            hint = "—"
        out.append(
            f"| {page_short} | {e.index} | `{e.ref_label}` | {e.ref_kind or '—'} | "
            f"{status_cn.get(e.status, e.status)} | {hint} |"
        )
    out.append("")
    out.append("> “渲染上下文”来自 KaTeX 生成的 HTML，可能夹带公式碎片；"
               "判断时以“btx 原文”为准。\n")

    status_cn = {"cross-page": "跨页引用", "dangling": "悬空引用", "local-late": "同页后置定义"}
    for page, items in by_page.items():
        out.append(f"## `{page}`（{len(items)} 处）\n")
        for e in items:
            out.append(f"### {e.index}. `{e.ref_label}` — {status_cn.get(e.status, e.status)}\n")
            out.append(f"- HTML：`{e.html_file}:{e.html_line}:{e.html_column}`")
            out.append(f"- btx ：`{e.btx_file}:{e.btx_line}`")
            out.append(f"- 渲染上下文：`{e.html_context}`")
            out.append(f"- btx 原文：`{e.btx_context}`")
            if e.ref_kind:
                out.append(f"- 引用处类型词：**{e.ref_kind}**")
            if e.status == "dangling":
                out.append("- 定义位置：**全归档未找到 `\\label{...}`**")
            else:
                for d in e.defined_in:
                    env = f"{d['env_cn']}（`{d['env']}`）" if d["env"] else "（未识别环境）"
                    out.append(f"- 定义于：`{d['file']}:{d['line']}` — {env}")
                    if d["snippet"]:
                        out.append(f"  - 摘要：{d['snippet']}")
            if e.suggestions:
                out.append("- 可能的目标：")
                for s in e.suggestions:
                    env = f"{s['env_cn']}" if s["env"] else "?"
                    out.append(
                        f"  - `{s['label']}`（{s['reason']}）→ "
                        f"`{s['file']}:{s['line']}` [{env}] {s['snippet']}"
                    )
            else:
                out.append("- 可能的目标：**无自动匹配，需人工判断**")
            out.append("")

    out.append("---\n")
    out.append("## 处理建议\n")
    out.append("- `cross-page`：标签定义在同合集的另一页。静态单页 HTML 无法生成跨页锚点，"
               "可考虑把引用改为文字描述，或让生成器支持跨页定位。")
    out.append("- `dangling`：全归档没有该 `\\label`。优先核对“可能的目标”里的候选；"
               "若确为漏写标签，需要在对应 `btx` 中补 `\\label{}`（本轮未改动任何 btx）。")
    out.append("- 本报告只读取文件，不修改 `archive/`。")
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="整理 archive/ 下未解析的交叉引用（??），供人工检查（只读）。")
    default_archive = Path(__file__).resolve().parent.parent / "archive"
    parser.add_argument("--archive", type=Path, default=default_archive,
                        help=f"归档目录（默认：{default_archive}）")
    parser.add_argument("--md", type=Path, default=None, help="写入 Markdown 报告")
    parser.add_argument("--json", type=Path, default=None, help="写入 JSON 报告")
    args = parser.parse_args(argv)

    archive: Path = args.archive
    if not archive.is_dir():
        print(f"错误：找不到归档目录 {archive}", file=sys.stderr)
        return 2

    repo_root = archive.resolve().parent
    entries = collect(archive, repo_root)

    print(f"未解析交叉引用：{len(entries)} 处，"
          f"{len({e.page_title for e in entries})} 个页面")
    for e in entries:
        mark = {"cross-page": "跨页", "dangling": "悬空", "local-late": "同页"}[e.status]
        print(f"  [{mark}] {e.page_title} #{e.index}  "
              f"{e.ref_label}  ({e.html_file}:{e.html_line})")

    if args.md:
        args.md.write_text(render_markdown(entries, archive), encoding="utf-8")
        print(f"\nMarkdown 报告已写入：{args.md}")
    if args.json:
        payload = {
            "count": len(entries),
            "pages": sorted({e.page_title for e in entries}),
            "entries": [asdict(e) for e in entries],
        }
        args.json.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"JSON 报告已写入：{args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
