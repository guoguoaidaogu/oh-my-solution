#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_missing_links.py — 找出 archive/ 下被标记为 missing、但其实可以访问的内部链接。

背景
----
本仓库 `archive/` 下的 `*.html` 是由 `*.btx` 本地编译生成的"纯净版"页面。
站内链接（`[[页面名|显示文字]]`）被渲染成两种形式：

    <a    class="btex-link"          href="...html">显示文字</a>   # 解析成功
    <span class="btex-link missing"  title="page not found: 页面名">显示文字</span>  # 解析失败

生成器判定"页面不存在"时，对页面名做了**过于严格**的字符串比较，因此会把一些
实际存在的页面也标成 missing。已知的差异来源：

1. 命名空间别名：`User:` 与 `用户:` 指的是同一个命名空间；
2. 空白/下划线：MediaWiki 页面名中的空格等价于 `_`，且名字首尾的空白、
   冒号与斜杠两侧的空白都不重要；
3. 上述两点叠加后仍差一个前导 `_` 的情况（如 `习题: 数理方程第二章`
   对应文件 `_习题:_数理方程第二章.html`），归入 fuzzy 级别供人工复核。

本脚本**只读**：不会修改 `archive/` 下的任何文件，只输出报告。

用法
----
    python3 tools/check_missing_links.py                     # 人类可读报告
    python3 tools/check_missing_links.py --json report.json  # 同时写 JSON
    python3 tools/check_missing_links.py --fail-on-found     # 发现即退出码 1（CI 用）
    python3 tools/check_missing_links.py --min-level fuzzy   # 只看 fuzzy 及以上
    python3 tools/check_missing_links.py --list-unresolved   # 附带列出 unresolved reference

匹配级别（从强到弱）
--------------------
    exact      页面名与文件路径完全相同
    namespace  仅命名空间别名不同（User: <-> 用户:）
    whitespace 仅空白/下划线/冒号两侧空白不同
    fuzzy      在 whitespace 基础上再忽略多余的下划线（需人工复核，可能有误报）

退出码
------
    0  正常运行（默认；是否发现结果都返回 0）
    1  使用了 --fail-on-found 且确有发现
    2  参数或运行错误（例如 archive 目录不存在）
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable, Iterator

# ---------------------------------------------------------------------------
# 常量与正则
# ---------------------------------------------------------------------------

PAGE_NOT_FOUND_PREFIX = "page not found:"
UNRESOLVED_REFERENCE = "unresolved reference"

#: 各种 Unicode 空白（含全角空格、em space、nbsp 等），统一成普通空格
UNICODE_SPACES_RE = re.compile(r"[\u00a0\u1680\u2000-\u200b\u202f\u205f\u3000]")

#: 匹配任意 <span ...> 开始标签
SPAN_OPEN_RE = re.compile(r"<span\b[^>]*>", re.IGNORECASE)
ATTR_CLASS_RE = re.compile(r'class\s*=\s*"([^"]*)"', re.IGNORECASE)
ATTR_TITLE_RE = re.compile(r'title\s*=\s*"([^"]*)"', re.IGNORECASE)

#: 用于平衡嵌套 <span> 的分词正则
SPAN_TOKEN_RE = re.compile(r"<span\b|</span\s*>", re.IGNORECASE)
ANY_TAG_RE = re.compile(r"<[^>]*>")

#: 匹配级别的强弱顺序
LEVEL_ORDER = ["exact", "namespace", "whitespace", "fuzzy"]


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


@dataclass
class Finding:
    """一条"被标 missing 但实际存在"的链接。"""

    source: str          # 相对仓库根目录的 HTML 路径
    line: int            # 出现位置的行号（1 起）
    column: int          # 行内字符偏移（1 起；生成页正文常在一行内，故列号更有用）
    text: str            # 链接显示文字
    target: str          # HTML title 中记录的原始页面名
    match_level: str     # exact / namespace / whitespace / fuzzy
    page_file: str       # 命中的 archive/ 下的文件（相对 archive 的路径）


@dataclass
class ScanReport:
    archive: str
    html_files: int
    missing_links: int          # 所有 class 含 missing 的站内链接数
    page_not_found: int         # 其中 title 为 "page not found: ..." 的
    unresolved_reference: int   # 其中 title 为 "unresolved reference" 的
    other_missing: int          # 其余（未知 title）
    findings: list[Finding] = field(default_factory=list)
    unresolved_targets: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 页面名归一化
# ---------------------------------------------------------------------------


def unify_spaces(text: str) -> str:
    """把所有 Unicode 空白统一成普通空格，并去掉首尾空白。"""
    return UNICODE_SPACES_RE.sub(" ", text).strip()


def strip_fragment(text: str) -> str:
    """去掉 `#锚点` 部分。"""
    return text.split("#", 1)[0]


def canonical_namespace(text: str) -> str:
    """把 `User:` 命名空间别名统一成归档中实际使用的 `用户:`。"""
    text = unify_spaces(text)
    for alias in ("User", "user", "USER"):
        if text.startswith(alias + ":"):
            return "用户:" + text[len(alias) + 1:]
    return text


def strong_key(text: str) -> str:
    """whitespace 级别归一化：空白 -> `_`，去掉各段首尾空白与冒号两侧空白。"""
    text = strip_fragment(canonical_namespace(text))
    segments = []
    for seg in text.split("/"):
        seg = seg.strip()
        seg = re.sub(r"\s*:\s*", ":", seg)
        seg = re.sub(r"\s+", "_", seg)
        segments.append(seg)
    return "/".join(segments)


def fuzzy_key(text: str) -> str:
    """fuzzy 级别归一化：在 strong_key 基础上再忽略多余的下划线。"""
    return re.sub(r"[_\s]+", "", strong_key(text))


# ---------------------------------------------------------------------------
# 解析 HTML
# ---------------------------------------------------------------------------


def line_column_of(text: str, index: int) -> tuple[int, int]:
    """返回 (行号, 列号)，均从 1 开始。"""
    line = text.count("\n", 0, index) + 1
    last_nl = text.rfind("\n", 0, index)
    column = index - last_nl
    return line, column


def _span_inner(text: str, open_end: int) -> tuple[str, int, int] | None:
    """从 <span ...> 结束处开始，返回 (内部 HTML, 结束标签起点, 结束标签终点)。"""
    depth = 1
    for token in SPAN_TOKEN_RE.finditer(text, open_end):
        if token.group(0).lower().startswith("</"):
            depth -= 1
            if depth == 0:
                return text[open_end:token.start()], token.start(), token.end()
        else:
            depth += 1
    return None


@dataclass
class LinkSpan:
    """一个 `btex-link missing` 的 <span>，带完整位置与原始内部 HTML。"""

    start: int          # <span ...> 起始偏移
    end: int            # 配对 </span> 结束偏移（不含）
    classes: str        # class 属性原文
    title: str          # title 属性（已做 HTML 反转义）
    inner_html: str     # 标签之间的原始 HTML（用于无损重建）



def iter_link_spans_ex(text: str) -> Iterator[LinkSpan]:
    """产出所有 `btex-link missing` 的 span（含内部 HTML 与偏移）。"""
    for m in SPAN_OPEN_RE.finditer(text):
        attrs = m.group(0)
        cm = ATTR_CLASS_RE.search(attrs)
        if not cm:
            continue
        classes = cm.group(1).split()
        if "btex-link" not in classes or "missing" not in classes:
            continue
        tm = ATTR_TITLE_RE.search(attrs)
        title = html.unescape(tm.group(1)) if tm else ""
        inner = _span_inner(text, m.end())
        if inner is None:
            continue
        inner_html, _close_start, close_end = inner
        yield LinkSpan(
            start=m.start(),
            end=close_end,
            classes=cm.group(1),
            title=title,
            inner_html=inner_html,
        )


def iter_link_spans(text: str) -> Iterator[tuple[int, str, str, str]]:
    """产出 (起始位置, class, title, 显示文字) 四元组（向后兼容扫描器）。"""
    for sp in iter_link_spans_ex(text):
        label = html.unescape(ANY_TAG_RE.sub("", sp.inner_html)).strip()
        yield sp.start, sp.classes, sp.title, label


# ---------------------------------------------------------------------------
# 索引与匹配
# ---------------------------------------------------------------------------


class PageIndex:
    """archive/ 下所有 HTML 文件构成的页面名索引。"""

    def __init__(self, archive: Path) -> None:
        self.archive = archive
        self.pages: dict[str, str] = {}
        for path in sorted(archive.rglob("*.html")):
            rel = path.relative_to(archive).as_posix()
            title = rel[: -len(".html")]
            self.pages.setdefault(title, rel)
        self.strong: dict[str, str] = {}
        self.fuzzy: dict[str, str] = {}
        for title, rel in self.pages.items():
            self.strong.setdefault(strong_key(title), rel)
            self.fuzzy.setdefault(fuzzy_key(title), rel)

    def match(self, target: str) -> tuple[str | None, str | None]:
        """返回 (匹配级别, archive 内的相对文件路径)。"""
        raw = unify_spaces(target)
        if raw in self.pages:
            return "exact", self.pages[raw]

        alias = canonical_namespace(raw)
        if alias in self.pages:
            return "namespace", self.pages[alias]

        sk = strong_key(raw)
        if sk in self.strong:
            return "whitespace", self.strong[sk]

        fk = fuzzy_key(raw)
        if fk in self.fuzzy:
            return "fuzzy", self.fuzzy[fk]

        return None, None


# ---------------------------------------------------------------------------
# 扫描
# ---------------------------------------------------------------------------


def scan(archive: Path, repo_root: Path) -> ScanReport:
    index = PageIndex(archive)
    report = ScanReport(
        archive=str(archive),
        html_files=len(list(archive.rglob("*.html"))),
        missing_links=0,
        page_not_found=0,
        unresolved_reference=0,
        other_missing=0,
    )
    unresolved_seen: set[str] = set()

    for path in sorted(archive.rglob("*.html")):
        text = path.read_text(encoding="utf-8", errors="replace")
        for pos, _classes, title, label in iter_link_spans(text):
            report.missing_links += 1
            if title.startswith(PAGE_NOT_FOUND_PREFIX):
                report.page_not_found += 1
                target = html.unescape(title[len(PAGE_NOT_FOUND_PREFIX):]).strip()
                level, page_file = index.match(target)
                if level:
                    line, column = line_column_of(text, pos)
                    report.findings.append(
                        Finding(
                            source=path.relative_to(repo_root).as_posix(),
                            line=line,
                            column=column,
                            text=label,
                            target=target,
                            match_level=level,
                            page_file=page_file or "",
                        )
                    )
            elif title == UNRESOLVED_REFERENCE:
                report.unresolved_reference += 1
                if label:
                    unresolved_seen.add(label)
            else:
                report.other_missing += 1

    report.unresolved_targets = sorted(unresolved_seen)
    return report


# ---------------------------------------------------------------------------
# 输出
# ---------------------------------------------------------------------------


def filter_by_level(findings: Iterable[Finding], min_level: str) -> list[Finding]:
    threshold = LEVEL_ORDER.index(min_level)
    return [f for f in findings if LEVEL_ORDER.index(f.match_level) <= threshold]


def print_human(report: ScanReport, min_level: str, list_unresolved: bool) -> None:
    findings = filter_by_level(report.findings, min_level)

    print(f"扫描目录        : {report.archive}")
    print(f"HTML 文件数     : {report.html_files}")
    print(f"missing 链接总数: {report.missing_links}")
    print(f"  page not found: {report.page_not_found}")
    print(f"  unresolved ref: {report.unresolved_reference}")
    print(f"  其它 missing  : {report.other_missing}")
    print()

    if not findings:
        print("未发现「可访问却标记为 missing」的链接。")
        return

    print(f"发现 {len(findings)} 条「可访问却标记为 missing」的链接：")
    print("=" * 100)
    grouped: dict[str, list[Finding]] = {lvl: [] for lvl in LEVEL_ORDER}
    for f in findings:
        grouped[f.match_level].append(f)

    for level in LEVEL_ORDER:
        items = grouped[level]
        if not items:
            continue
        print(f"\n[{level}] {len(items)} 条")
        for f in items:
            print(f"  来源  : {f.source}:{f.line}:{f.column}")
            print(f"  显示  : {f.text or '(无显示文字)'}")
            print(f"  标记名: {f.target}")
            print(f"  实际页: archive/{f.page_file}")
            print()

    if list_unresolved and report.unresolved_targets:
        print("-" * 100)
        print(f"附：unresolved reference 共 {report.unresolved_reference} 条，"
              f"{len(report.unresolved_targets)} 个不同标签：")
        for t in report.unresolved_targets:
            print(f"  - {t}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="检查 archive/ 下被标记为 missing 却实际存在的站内链接（只读）。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    default_archive = Path(__file__).resolve().parent.parent / "archive"
    parser.add_argument("--archive", type=Path, default=default_archive,
                        help=f"归档目录（默认：{default_archive}）")
    parser.add_argument("--json", type=Path, default=None,
                        help="把完整报告写到指定 JSON 文件")
    parser.add_argument("--min-level", choices=LEVEL_ORDER, default="fuzzy",
                        help="只报告不低于该级别的匹配（默认：fuzzy，即全部）")
    parser.add_argument("--list-unresolved", action="store_true",
                        help="附带列出 unresolved reference 标签")
    parser.add_argument("--fail-on-found", action="store_true",
                        help="发现结果时返回退出码 1（适合 CI）")
    args = parser.parse_args(argv)

    archive: Path = args.archive
    if not archive.is_dir():
        print(f"错误：找不到归档目录 {archive}", file=sys.stderr)
        return 2

    repo_root = archive.resolve().parent
    report = scan(archive, repo_root)

    print_human(report, args.min_level, args.list_unresolved)

    if args.json:
        payload = asdict(report)
        payload["findings"] = [
            asdict(f) for f in filter_by_level(report.findings, args.min_level)
        ]
        payload["min_level"] = args.min_level
        args.json.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"\nJSON 报告已写入：{args.json}")

    has_findings = bool(filter_by_level(report.findings, args.min_level))
    if args.fail_on_found and has_findings:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
