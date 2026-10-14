# `tools/` — 归档链接体检

本目录存放对 `archive/` 归档做检查的小工具：

| 文件 | 说明 |
| --- | --- |
| `check_missing_links.py` | 找出 `archive/` 下被渲染成 `missing`、但其实指向已存在页面的站内链接 |
| `fix_missing_links.py` | 把上述链接改回 `<a class="btex-link" href="...">`（默认 dry-run，`--apply` 才写入） |
| `report_unresolved_refs.py` | 整理未解析的交叉引用 `??`（`\ref` 找不到 `\label`），供人工检查 |
| `reports/missing_links.txt` | 最近一次扫描（修复前）的人类可读报告 |
| `reports/missing_links.json` | 最近一次扫描（修复前）的机读报告，含精确行/列号 |
| `reports/fix_plan.json` | 实际应用的 13 处修改计划 |
| `reports/fix_changelog.txt` | 实际应用的紧凑修改记录（-旧片段/+新片段） |
| `reports/unresolved_refs.md` | 23 处未解析交叉引用的逐条清单（人工检查用） |
| `reports/unresolved_refs.json` | 同上，机读版 |
| `PROMPTS.md` | 生成这些代码/报告时使用的提示词，便于复现 |

> `check_missing_links.py` 与 `report_unresolved_refs.py` **只读**；
> `fix_missing_links.py` 只在显式加 `--apply` 时修改 `archive/**.html`，
> 且**从不**改动 `*.btx` 源文件。


---

## 为什么会有“假 missing”

`archive/**.html` 是由 `*.btx` 在本地编译生成的。站内链接
`[[页面名|显示文字]]` 被渲染成两种形式：

```html
<a    class="btex-link"         href="...html">显示文字</a>      <!-- 解析成功 -->
<span class="btex-link missing" title="page not found: 页面名">显示文字</span>  <!-- 被判为不存在 -->
```

生成器判断“页面不存在”时做了**逐字符**比较，没有做 MediaWiki 的标题归一化，
于是把一些本来能打开的页面也标成了 `missing`。已确认的差异来源有三类：

1. **命名空间别名**：`User:` 与 `用户:` 是同一个命名空间，归档里实际用 `用户:`。
2. **空白 / 下划线**：MediaWiki 里页面名的空格等价于 `_`，首尾空白、
   冒号与斜杠两侧的空白都不重要（如 `临时页面 3` → `临时页面_3.html`）。
3. **多余的前导 `_`**：归档里存在 `_习题:_数理方程第二章.html` 这类文件名，
   而链接写的是 `习题: 数理方程第二章`。这类是否算“同一页”需要人工判断，
   单独归入 `fuzzy` 级别。

---

## 用法

```bash
# 人类可读报告（默认扫描仓库根目录下的 archive/）
python3 tools/check_missing_links.py

# 同时写一份 JSON，并附带 unresolved reference 明细
python3 tools/check_missing_links.py --list-unresolved --json tools/reports/missing_links.json

# 只看高置信度结果（exact / namespace / whitespace），排除 fuzzy
python3 tools/check_missing_links.py --min-level whitespace

# CI：发现即返回退出码 1
python3 tools/check_missing_links.py --fail-on-found
```

常用参数：

| 参数 | 说明 |
| --- | --- |
| `--archive DIR` | 归档目录，默认 `<仓库根>/archive` |
| `--json FILE` | 额外写出 JSON 报告 |
| `--min-level L` | 只报告不低于 `L` 的级别，可选 `exact`/`namespace`/`whitespace`/`fuzzy`（默认 `fuzzy`，即全部） |
| `--list-unresolved` | 附带列出 `unresolved reference` 标签 |
| `--fail-on-found` | 有发现时退出码为 1 |

匹配级别从强到弱：

| 级别 | 含义 | 置信度 |
| --- | --- | --- |
| `exact` | 页面名与文件名完全一致 | 确定 |
| `namespace` | 仅 `User:` / `用户:` 不同 | 确定 |
| `whitespace` | 仅空白 / 下划线 / 冒号两侧空白不同 | 确定 |
| `fuzzy` | 在上一级基础上再忽略多余的下划线 | 需人工复核 |

## 修复

```bash
# 预览将要做的替换（默认 dry-run）
python3 tools/fix_missing_links.py

# 实际写回 archive/**.html，并留档计划与 diff
python3 tools/fix_missing_links.py --apply \
    --json tools/reports/fix_plan.json --diff tools/reports/fix_changelog.txt
```

`fix_missing_links.py` 会把

```html
<span class="btex-link missing" title="page not found: 页面名">文字</span>
```

替换成

```html
<a class="btex-link" href="相对路径.html">文字</a>
```

`href` 与生成器风格一致：相对**当前文件所在目录**、百分号编码 `safe="/()"`。
脚本幂等：改完后这些 `<a>` 不再匹配 `missing`，重复运行不会重复处理。
它复用 `check_missing_links.py` 的匹配逻辑，因此 `--min-level` 同样可用。

---

## 最近一次扫描结果（修复前）

> 本节记录修复**之前**的状态（`missing_links.txt`/`.json` 即为该次扫描），
> 下表中的 13 条现已全部修复；当前重跑 `check_missing_links.py` 结果为
> `page not found` 78 条、可访问的假 missing **0 条**。

扫描时间点：仓库当前快照。`archive/` 共 **345** 个 HTML 文件，
站内 `missing` 链接 **114** 条，其中 `page not found:` **91** 条、
`unresolved reference`（不是页面链接）23 条。

在 91 条 `page not found` 中，有 **13 条实际可访问**，已全部按归档真实文件名修复：


### 确定（7 条）

| 级别 | 来源页面 | 显示文字 | 标记的页面名 | 实际文件 |
| --- | --- | --- | --- | --- |
| `namespace` | `用户:Solution.html` | 页面指南 | `User:Solution/页面指南` | `archive/用户:Solution/页面指南.html` |
| `namespace` | `_习题:_楼分析/序列极限.html` | 这里 | `User:Solution/临时页面` | `archive/用户:Solution/临时页面.html` |
| `namespace` | `_习题:_楼分析/函数列与函数项级数.html` | 转存 | `User:Solution/转存` | `archive/用户:Solution/转存.html` |
| `namespace` | `_习题:_楼分析/广义积分与含参变量广义积分.html` | 这里 | `User:Solution/临时库房` | `archive/用户:Solution/临时库房.html` |
| `whitespace` | `_试卷:_高等代数_(英才班).html` | 这里 | `用户:Solution/临时页面 3` | `archive/用户:Solution/临时页面_3.html` |
| `whitespace` | `临时页面.html` | 这里 | `User:Solution/临时页面 2` | `archive/用户:Solution/临时页面_2.html` |
| `exact` | `_试卷:_微分几何.html` | 嵇庆春《微分几何简明教程》练习 8.1 | `用户:Solution/_习题:_嵇庆春_微分几何简明教程` | `archive/用户:Solution/_习题:_嵇庆春_微分几何简明教程.html` |

> 其中 `_习题:_楼分析/序列极限.html` 结尾处的“临时页面”即最初人工发现的那一处。

### 需人工复核（6 条，`fuzzy`）

这些链接的页面名与文件名只差一个前导/多余的下划线；已按归档真实文件名修复：

| 来源页面 | 标记的页面名 | 实际文件 |
| --- | --- | --- |
| `_试卷:_表示论基础_(H).html` | `User:Solution/  试卷: 现代代数学II` | `archive/用户:Solution/_试卷:_现代代数学II.html` |
| `_习题:_泛函分析续论.html` | `用户: Solution/  习题: 泛函分析 第三章` | `archive/用户:Solution/_习题:_泛函分析_第三章.html` |
| `_习题:_楼分析/Riemann积分.html` | `User:Solution/  习题: 楼分析/曲线积分与曲面积分` | `archive/用户:Solution/_习题:_楼分析/曲线积分与曲面积分.html` |
| `_习题:_楼分析/微分中值定理和Taylor展式.html` | `用户:Solution/ 习题: 数理方程第二章` | `archive/用户:Solution/_习题:_数理方程第二章.html` |
| `_习题:_楼分析/Fourier级数.html` | `用户: Solution/ 解答: GTM249 Chapter 2 Part I` | `archive/用户:Solution/_解答:_GTM249_Chapter_2_Part_I.html` |
| `_习题:_楼分析/导数与微分.html` | `User:Solution/  习题: 数理方程第一章` | `archive/用户:Solution/_习题:_数理方程第一章.html` |

> 注：`_习题:_楼分析/导数与微分.html` 该处链接的**显示文字**是“数理方程第二章”，
> 而链接目标写的是“数理方程第一章”。修复遵循链接**目标名**（第一章），
> 与原始 `btx` 一致；显示文字未改动。

未列出的其余 `page not found` 链接（如 `复分析`、`Yoneda 引理`、
`samples/demo3/...` 等）在 `archive/` 内没有对应文件，属于真正的缺页。

---

## 修复记录

- 修复范围：仅 `archive/**.html`，13 个文件各 1 处，共 13 条；
  `*.btx` 源文件未改动。
- 落地内容：`reports/fix_plan.json`（逐条计划）、`reports/fix_changelog.txt`（紧凑的 -旧/+新 记录）。
- 校验：修复后重跑扫描为 0 条假 missing；全库 312 条
  `<a class="btex-link">` 相对链接 100% 命中真实文件。
- 已知取舍：`_试卷:_微分几何.html` 指向嵇庆春页面的原链接带锚点
  `#两类特殊参数化`，但 HTML 的 `title` 已丢失锚点信息，故修复后的链接
  指向页面本身（锚点仍在，如需要可手工补 `#两类特殊参数化`）。

---

## 未解析交叉引用（人工检查清单）

`btex-ref missing`（渲染为 `??`）是 `\ref{标签}` 找不到对应 `\label{标签}` 的结果，
不是页面链接，因此不在上面 13 条的修复范围内。用专门脚本整理成人工清单：

```bash
python3 tools/report_unresolved_refs.py \
    --md tools/reports/unresolved_refs.md \
    --json tools/reports/unresolved_refs.json
```

共 **23** 处，分布在 4 个页面：

| 页面 | 处数 | 跨页引用 | 悬空引用 |
| --- | ---: | ---: | ---: |
| `用户:Solution/_讲义:_几何测度论/_Chapter_1_Measure_Theory` | 1 | 0 | 1 |
| `用户:Solution/讲义:_现代代数学_II/前言` | 3 | 3 | 0 |
| `用户:Solution/讲义:_现代代数学_II/基本概念` | 1 | 1 | 0 |
| `用户:Solution/讲义:_现代代数学_II/特征标理论` | 18 | 12 | 6 |

状态含义：

- `cross-page`（16 处）：标签定义在同一合集的**另一页**（如 `前言` 引用
  `特征标理论` 的推论）。静态单页 HTML 无法生成跨页锚点，属生成器能力限制。
- `dangling`（7 处）：全归档都没有该 `\label`。其中 6 处是“差一个前缀”的写法，
  脚本已给出候选，例如：

  | 引用写成 | 疑为 | 定义处 |
  | --- | --- | --- |
  | `lem:acffdireEndk`（2 处） | `acffdireEndk` | `基本概念.btx:547`（引理） |
  | `acfofAbgirepeg`（2 处） | `exm:acfofAbgirepeg` | `基本概念.btx:679`（例） |
  | `pro:propcogclasNeqdimcent` | `propcogclasNeqdimcent` | `基本概念.btx:625`（命题） |
  | `1repGandGqGGprop` | `pro:1repGandGqGGprop` | `基本概念.btx:738`（命题） |

  剩下 1 处 `thm:radon-restriction`
  （`_讲义:_几何测度论/_Chapter_1_Measure_Theory.btx:575`，写作
  `Apply Theorem~\ref{thm:hausdorff-borel-regular} and Theorem~\ref{thm:radon-restriction}`）
  全归档无相近标签，需人工判断是漏写标签还是应删除该引用。

清单包含每条的 HTML `行:列`、btx 位置、渲染上下文、btx 原文、定义处环境与摘要、
候选标签，详见 `reports/unresolved_refs.md`。本轮**未改动任何 btx**，
`dangling` 的修正需要人工决定后再改源文件并重新编译。

---

## 注意

- 报告中的行号几乎都是 `762`：生成的正文整体位于同一行，因此
  `行:列` 里的**列号**才是定位用的坐标。
- `btex-ref missing`（渲染为 `??`）是未解析的交叉引用，不是页面链接，
  `check_missing_links.py` 不处理，请用 `report_unresolved_refs.py`（见上一节）。
- 若将来重新用生成器编译 `btx`，本文的 HTML 修复会被覆盖；届时需要
  先修生成器的页面名归一化（`User:`/`用户:`、空白↔下划线、锚点），
  或在重新生成后再跑一次 `fix_missing_links.py --apply`。
