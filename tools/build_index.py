#!/usr/bin/env python3
"""由 README.md 预渲染首页 index.html。

.nojekyll 关掉了 Jekyll 对 README.md 的自动渲染，
因此首页必须是提交进仓库的静态文件，用本脚本刷新。

    python3 tools/build_index.py
    GITHUB_TOKEN=xxx python3 tools/build_index.py   # 提高 API 配额
"""
import json, os, re, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
README, OUTPUT = ROOT / "README.md", ROOT / "index.html"


def render_gfm(text: str) -> str:
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "oh-my-solution-build-index",
        "Accept": "application/vnd.github+json",
    }
    if token := os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(
        "https://api.github.com/markdown",
        data=json.dumps({"text": text, "mode": "gfm"}).encode(),
        headers=headers,
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8")


def normalize(body: str) -> str:
    """GitHub 网页会给块级元素加 dir="auto"，API 不加，这里补齐。"""
    return re.sub(r'<(h[1-6]|p|td|th|li)(?=[ >])', r'<\1 dir="auto"', body).strip()


def main() -> None:
    body = normalize(render_gfm(README.read_text(encoding="utf-8")))
    html = OUTPUT.read_text(encoding="utf-8")
    # 只换 <article> 里的正文，<head> 和内嵌 CSS 原样保留
    new_html, n = re.subn(
        r'(<article class="markdown-body">\n).*?(\n</article>)',
        lambda m: m.group(1) + body + m.group(2),
        html, count=1, flags=re.S,
    )
    if n != 1:
        raise SystemExit("未能在 index.html 中定位 <article> 正文块")
    if new_html != html:
        OUTPUT.write_text(new_html, encoding="utf-8")
        print("index.html 已更新")
    else:
        print("index.html 已是最新，无需改动")


if __name__ == "__main__":
    main()