# 提示词存档（可复用）

本文件保留生成 `tools/check_missing_links.py` 及对应报告时使用的提示词，
目的是之后归档更新（或需要同类体检）时可以照抄复现。

---

## 1. 原始需求（逐字保留）

```text
写一个 `python` 脚本，检查 `archive/` 下所有 html 中的超链接，是否存在可以访问但是标记为 `missing` 的链接，但暂时不作改动。目前我已发现一处： `archive/_习题:_楼分析/序列极限.html` 中结尾处 `临时页面`。保留过程中生成的代码和提示词以便重复使用。
```

## 2. 据此拆解出的工作指令

```text
你是本仓库（oh-my-solution 归档站）的维护助手。任务：只读排查，不修改 archive/。

背景：
- archive/**/*.html 由 *.btx 本地编译生成。
- 站内链接 [[页面名|显示文字]] 解析成功时渲染为
  <a class="btex-link" href="...">，失败时渲染为
  <span class="btex-link missing" title="page not found: 页面名">。
- 需要找出“其实能访问、却被标成 missing”的假阴性。

要求：
1. 写一个可重复运行的 Python 脚本（放在 tools/），遍历 archive/ 下所有 *.html，
   提取所有 class 含 btex-link 且含 missing、title 以 "page not found:" 开头的
   链接；同时建立 archive/ 内所有 *.html 的页面名索引。
2. 判定“可访问”时按如下归一化分级，并在报告中标注级别：
   exact      页面名与文件路径完全一致；
   namespace  仅命名空间别名不同（User: <-> 用户:）；
   whitespace 仅空白/下划线/冒号两侧空白不同；
   fuzzy      再忽略多余的下划线（如 习题: X 对应 _习题:_X.html），需人工复核。
3. 脚本必须只读；输出人类可读报告，并支持 --json 写机读报告、
   --min-level 过滤级别、--list-unresolved 附带未解析引用、--fail-on-found 供 CI。
4. 报告需给出：来源文件、行:列、显示文字、标记的页面名、实际文件。
   注意正文通常在同一行，列号才是有效定位坐标。
5. 运行脚本生成 tools/reports/missing_links.{txt,json}。
6. 把脚本、报告、这份提示词一起保留在仓库里，便于复用。

验收：
- 必须命中已知样例：archive/用户:Solution/_习题:_楼分析/序列极限.html 结尾处
  显示文字为“这里”、标记名 User:Solution/临时页面 的链接，
  实际页为 archive/用户:Solution/临时页面.html。
- 不得修改 archive/ 下的任何文件。
```

## 3. 复用方式

归档更新后重新体检，只需：

```bash
python3 tools/check_missing_links.py --list-unresolved \
    --json tools/reports/missing_links.json > tools/reports/missing_links.txt
```

若只想看高置信度结果：加上 `--min-level whitespace`。
若要接入 CI 让假 missing 直接导致失败：加上 `--fail-on-found`。

需要让 AI 助手再来一轮时，可直接把第 2 节的指令连同“第 1 节原始需求”一起发出。

---

## 4. 本次执行中得到的关键结论（供下一轮参考）

- 生成器做的是逐字符比较，缺少三类归一化：命名空间别名 `User:`↔`用户:`、
  空白↔下划线、冒号/斜杠两侧空白。
- 91 条 `page not found` 中有 13 条为假阴性（7 条确定 + 6 条 fuzzy 待复核）。
- 归档内另有 23 条 `btex-ref missing`（渲染为 `??`）是未解析的交叉引用，
  不是页面链接，不在本任务范围内。

---

## 5. 修复阶段的原始需求（逐字保留）

```text
修复上述发现 13 条「可访问却标记为 missing」的链接。
```

追问确认后得到的两项约束：

```text
修复范围：只修 HTML（不动 btx 源文件）
fuzzy 处理：全部按归档真实文件名修复（6 条 fuzzy 也改）
```

## 6. 修复阶段的工作指令

```text
在已有 tools/check_missing_links.py 基础上，新增 tools/fix_missing_links.py：

1. 复用 check_missing_links 的扫描与归一化逻辑，找出所有可访问的假 missing 链接。
2. 把
     <span class="btex-link missing" title="page not found: 页面名">文字</span>
   替换为
     <a class="btex-link" href="相对路径.html">文字</a>
   - href 与生成器一致：相对当前文件所在目录、百分号编码 safe="/()"；
   - 内部 HTML 必须原样保留（可能含 KaTeX 等嵌套标签），不得只保留纯文本。
3. 默认 dry-run，只有 --apply 才写文件；支持 --min-level、--json、--diff。
4. 只允许改 archive/**.html，绝不改 *.btx。
5. 修复后必须验证：
   - 重跑 check_missing_links.py 结果为 0 条假 missing；
   - 全库所有 <a class="btex-link" href> 都能解析到真实文件；
   - git diff 只含 .html。
6. 更新 tools/README.md 与 tools/PROMPTS.md，保留 fix_plan.json 与 fix_changelog.txt。
```

复用命令：

```bash
python3 tools/fix_missing_links.py            # 预览
python3 tools/fix_missing_links.py --apply \
    --json tools/reports/fix_plan.json --diff tools/reports/fix_changelog.txt
```

## 7. 修复阶段结论

- 13 条全部修复，涉及 13 个 HTML 文件，每文件 1 处；`btx` 未改动。
- 修复后重扫：`page not found` 由 91 降到 78，假 missing 为 0。
- 全库 312 条 `btex-link` 相对链接全部命中真实文件。
- 唯一取舍：嵇庆春那处原链接带锚点 `#两类特殊参数化`，HTML 的 `title`
  已丢失锚点，修复后链接指向页面本身（锚点在目标页中仍然存在）。

---

## 8. 未解析交叉引用阶段的原始需求（逐字保留）

```text
整理 23 处未解析的交叉引用，用于人工检查。
```

## 9. 未解析交叉引用阶段的工作指令

```text
在 tools/ 下新增 report_unresolved_refs.py（只读）：

1. 定位渲染为 ?? 的未解析交叉引用：
   <span class="btex-link missing" title="unresolved reference">
     <span><span class="btex-ref missing">??</span></span></span>
   这些是 \ref{标签} 找不到 \label{标签} 的结果，共 23 处。
2. 与同目录同名 .btx 对齐：
   - 取该 btx 中所有本地 \label{...}；
   - 列出 \ref{...} 中标签未在本地定义的项，按出现顺序与 HTML 的 ?? 一一配对
     （两者数量必须相等，不等则告警）。
3. 为每处输出：HTML 行:列、btx 行、渲染上下文、btx 原文、引用类型词、
   标签状态（cross-page 跨页 / dangling 悬空）、定义处环境与摘要。
4. 对标签建立全局索引（跨全部 *.btx），对 dangling 项给出候选：
   去掉前缀、补上前缀、difflib 形近，并附候选定义处摘要。
5. 产出 Markdown 清单（含速查表）与 JSON；只读，不改动任何文件。
6. 把脚本、报告、提示词保留在仓库中。
```

复用命令：

```bash
python3 tools/report_unresolved_refs.py \
    --md tools/reports/unresolved_refs.md \
    --json tools/reports/unresolved_refs.json
```

## 10. 未解析交叉引用阶段结论

- 23 处，4 个页面：前后页引用 16 处（cross-page），悬空 7 处（dangling）。
- 悬空项里 6 处是“差一个前缀”的写法（`lem:`/`pro:` 前缀、或该加 `exm:` 前缀），
  脚本已给出候选与定义处摘要。
- 余下 1 处 `thm:radon-restriction`（几何测度论 Chapter 1）全归档无相近标签，
  需人工判断。
- 本轮未改动任何 btx / html；报告仅作人工检查依据。

