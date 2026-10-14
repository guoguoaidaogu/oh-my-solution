#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fix_missing_links.py — 把「可访问却标记为 missing」的站内链接改回正常超链接。

它复用 `check_missing_links.py` 的扫描与归一化逻辑：

    <span class="btex-link missing" title="page not found: 页面名">文字</span>
        ->  <a class="btex-link" href="相对路径.html">文字</a>

其中 `href` 与生成器保持一致：相对**当前文件所在目录**、百分号编码
（`safe="/()"`，即 `/` `(` `)` `-` `.` 不编码，其余非 ASCII/保留字符编码）。

默认是 **dry-run**，只打印计划；加 `--apply` 才真正写回文件。
脚本只改 `*.html`，**不会**改动 `*.btx` 源文件。

用法
----
    python3 tools/fix_missing_links.py                  # 预览将要做的修改
    python3 tools/fix_missing_links.py --apply          # 实际写入
    python3 tools/fix_missing_links.py --apply --min-level whitespace
    python3 tools/fix_missing_links.py --apply --json tools/reports/fix_plan.json
"""

from __future__ import annotations

import argparse
import html
import json
import os
import sys
import urllib.parse
from dataclasses import asdict, dataclass
from pathlib import Path

sys.dont_write_bytecode = True  # 避免在 tools/ 下生成 __pycache__
sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_missing_links as C  # noqa: E402  (同目录模块)

#: 与生成器一致的百分号编码：保留 `/()`
HREF_SAFE = "/()"


@dataclass
class Edit:
    """一处待应用的替换。"""

    source: str          # 仓库相对路径
    line: int
    column: int
    start: int           # 原文字符偏移（起点）
    end: int             # 原文字符偏移（终点，不含）
    text: str            # 显示文字（纯文本）
    target: str          # 原 title 中的页面名
    match_level: str
    page_file: str       # 命中的 archive 相对文件
    href: str            # 新生成的 href
    old_html: str        # 原始 <span ...>...</span>
    new_html: str        # 替换后的 <a ...>...</a>


def make_href(source_abs: Path, archive: Path, page_rel: str) -> str:
    """生成从 source_abs 所在目录指向 archive/<page_rel> 的相对 href。"""
    src_dir = source_abs.parent
    target = archive / page_rel
    rel = os.path.relpath(target, src_dir).replace(os.sep, "/")
    return urllib.parse.quote(rel, safe=HREF_SAFE)


def plan(archive: Path, repo_root: Path, min_level: str) -> list[Edit]:
    index = C.PageIndex(archive)
    threshold = C.LEVEL_ORDER.index(min_level)
    edits: list[Edit] = []

    for path in sorted(archive.rglob("*.html")):
        raw = path.read_bytes()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            print(f"跳过（非 UTF-8）：{path}", file=sys.stderr)
            continue

        for sp in C.iter_link_spans_ex(text):
            if not sp.title.startswith(C.PAGE_NOT_FOUND_PREFIX):
                continue
            target = html.unescape(sp.title[len(C.PAGE_NOT_FOUND_PREFIX):]).strip()
            level, page_rel = index.match(target)
            if not level or C.LEVEL_ORDER.index(level) > threshold:
                continue

            href = make_href(path, archive, page_rel)  # type: ignore[arg-type]
            new_html = f'<a class="btex-link" href="{href}">{sp.inner_html}</a>'
            line, column = C.line_column_of(text, sp.start)
            edits.append(
                Edit(
                    source=path.relative_to(repo_root).as_posix(),
                    line=line,
                    column=column,
                    start=sp.start,
                    end=sp.end,
                    text=html.unescape(C.ANY_TAG_RE.sub("", sp.inner_html)).strip(),
                    target=target,
                    match_level=level,
                    page_file=page_rel or "",
                    href=href,
                    old_html=text[sp.start:sp.end],
                    new_html=new_html,
                )
            )
    return edits


def format_changelog(edits: list[Edit]) -> str:
    """紧凑的修改记录：每处只保留被替换的片段，不输出整行。

    生成页正文常在一整行内（单行可达 1MB+），逐行 unified diff 会膨胀到几十 MB，
    因此这里只记录 `-旧片段 / +新片段`，完整信息仍在 fix_plan.json。
    """
    chunks = []
    for e in edits:
        chunks.append(
            f"--- {e.source}:{e.line}:{e.column}  [{e.match_level}]\n"
            f"-{e.old_html}\n"
            f"+{e.new_html}\n"
        )
    return "\n".join(chunks)


def apply_edits(archive: Path, repo_root: Path, edits: list[Edit]) -> int:
    """按文件分组、从后往前替换，返回实际改动的文件数。"""
    by_file: dict[Path, list[Edit]] = {}
    for e in edits:
        by_file.setdefault(repo_root / e.source, []).append(e)

    changed_files = 0
    for path, items in sorted(by_file.items()):
        raw = path.read_bytes()
        text = raw.decode("utf-8")
        # 用记录下来的字符偏移定位，从后往前替换，避免前面的替换使偏移失效
        located = [(e.start, e.end, e) for e in items]
        located.sort(key=lambda t: t[0], reverse=True)
        new_text = text
        for start, end, e in located:
            if new_text[start:end] != e.old_html:
                print(f"警告：偏移不匹配，跳过 {e.source}:{e.line}:{e.column}", file=sys.stderr)
                continue
            new_text = new_text[:start] + e.new_html + new_text[end:]
        if new_text == text:
            continue

        path.write_bytes(new_text.encode("utf-8"))
        changed_files += 1
    return changed_files

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="把可访问却标记为 missing 的站内链接改回正常超链接（默认 dry-run）。",
    )
    default_archive = Path(__file__).resolve().parent.parent / "archive"
    parser.add_argument("--archive", type=Path, default=default_archive,
                        help=f"归档目录（默认：{default_archive}）")
    parser.add_argument("--apply", action="store_true",
                        help="真正写入文件；缺省只预览")
    parser.add_argument("--min-level", choices=C.LEVEL_ORDER, default="fuzzy",
                        help="只修复不低于该级别的匹配（默认：fuzzy，即全部）")
    parser.add_argument("--json", type=Path, default=None,
                        help="把修改计划写到 JSON")
    parser.add_argument("--diff", type=Path, default=None,
                        help="把紧凑修改记录（-旧/+新）写到文件")
    args = parser.parse_args(argv)

    archive: Path = args.archive
    if not archive.is_dir():
        print(f"错误：找不到归档目录 {archive}", file=sys.stderr)
        return 2

    repo_root = archive.resolve().parent
    edits = plan(archive, repo_root, args.min_level)

    print(f"待修复链接：{len(edits)} 条"
          f"（级别阈值 {args.min_level}，模式 {'apply' if args.apply else 'dry-run'}）")
    for e in edits:
        print(f"  [{e.match_level:10}] {e.source}:{e.line}:{e.column}")
        print(f"       {e.old_html[:70]}{'…' if len(e.old_html) > 70 else ''}")
        print(f"    -> <a class=\"btex-link\" href=\"{e.href}\">")

    if args.json:
        args.json.write_text(
            json.dumps([asdict(e) for e in edits], ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"\n修改计划已写入：{args.json}")

    if not edits:
        return 0

    if not args.apply:
        print("\n（dry-run：未写入任何文件；加 --apply 实际修改）")
        return 0

    changed = apply_edits(archive, repo_root, edits)
    if args.diff:
        args.diff.write_text(format_changelog(edits), encoding="utf-8")
        print(f"\n修改记录已写入：{args.diff}")
    print(f"\n已修复 {len(edits)} 条链接，涉及 {changed} 个文件。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
