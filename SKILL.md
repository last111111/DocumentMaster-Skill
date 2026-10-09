---
name: DocsManagement
description: 项目级溯源与布局规范及其对账工具。管整个项目：五个层（inputs / derivatives / analyses / writing / archive）里每个目录是什么、读了谁、用哪段代码算的、指纹是什么、现在什么状态；以及目录怎么建、怎么搬、怎么归档。像 git 一样对账，不把门。新建分析、登记目录、移动、归档、冻结基线、验证迁移都走它。
type: project
---

# SKILL.md — DocsManagement 项目溯源与布局规范 / Project-level provenance & layout convention

版本 v0.1.0 · 2026-10-07 · 适用于所有在本项目里建目录、搬目录、写结果的人和 agent。
与 `writing/abstract/agent.md` 的关系：那份管 `abstract/` 内部**逐文件**；本份管整个项目**逐目录**。两级嵌套，不冲突——`abstract/` 在本表里是一个块，它的 `readme.txt` 是那个块的指纹。

## 0. 用途

`DocsManagement/` 是**管理层**：记录项目里每个有意义的目录从哪来、到哪去、现在算不算数。它不放数据、不放分析、不产生数值。

- 粒度是**目录**，不是文件。五个层下每个一级目录是一个登记单元，全项目约 30 个。逐文件登记只在 `writing/abstract/` 里做。
- 工作方式像 git：**不拦你写，只对账。** 你可以手建目录、手 `mv`、手改登记表；`DocsManagement status` 告诉你磁盘、登记表、git 三方哪里对不上，并给出补账的那一条命令。
- 每个登记的目录都要能回答四个问题：**它是什么**（`QUESTION`/`DESC`）、**读了谁**（`INPUTS`）、**用哪段代码的哪个版本算的**（`CODE`）、**内容变没变**（`KEY` 指纹）。答不上的写 `TODO`，`status` 会一直黄着提醒。

### 0.1 不是 Claude 也能用

- 引擎是纯 Python（标准库）+ git + sh，没有任何 Claude 专属依赖；任何能跑 shell 的 agent 和人都能用。
- 入口文件是 `AGENTS.md`（跨工具通用的名字）；`CLAUDE.md`、`GEMINI.md` 是指向它的链接。项目里已有真实的 `CLAUDE.md` 时，`init` 保留它为正本，把别的名字链过去。
- 本文件开头的 `---` 元数据是 Claude Code 发现 skill 用的，其它 agent 忽略。
- 要机器可读：`DocsManagement status --json`，输出 `{"red": n, "yellow": n, "findings": [{level, code, path, msg}]}`。退出码：0 正常；`--strict` 下有红 = 1；用法错 = 2。
- 每个命令都有 `--help`。

## 1. 目录结构

```
DocsManagement/
  DocsManagement.py     引擎（标准库，Python >= 3.11）          ┐ 代码四样：`install` 只拷这些
  SKILL.md              本规范                                  │
  templates/            config.toml · gitignore · pre-commit · launcher.sh · AGENTS.project.md · README.{input,derivative,analysis,writing,archive}.md
  VERSION                                                       ┘
  config.toml           本项目的规则：层、顶层白名单、模板、命名、大小上限、旧路径、外部检查（人改）
  PROVENANCE.txt        登记表（人改 + 引擎改，见 §2）
  README.md             索引，引擎从登记表生成，不手改；项目根的 README.md 是指向它的符号链接
  PATHMAP.tsv           移动账本，append-only：时间 · 操作(mv/moved/undo) · 旧 · 新
  DELETED.tsv           删除账本，append-only：时间 · 路径 · sha256 · 字节 · 文件数 · trash 位置 · 原因
  baseline_<名>/        冻结的全树 manifest（path · bytes · mtime_ns · sha256）、tree.txt、du.tsv
```

它管理的项目布局（层和白名单在 `config.toml` 里改）：

```
<ROOT>/
  inputs/          只读。不是本项目流程算出来的：mask、被试表、atlas、上游 GLM 输出
  derivatives/     标准流程的产物，按代：gen3_zavg/ gen4_tent/ …，多个分析共用
  analyses/        一问一目录，自包含：README.md code/ data/ stats/ figures/ per_subject/ validation/ docs/
  writing/         汇总与写作：abstract/（自带 agent.md）、reviews/、briefs/、literature/
  archive/         被取代的产物 + trash_<日期>/（trash 命令的去处）
  skills/          科学 skill（dm-workflow、group-3dLME …），与 Jetstream2 卷根的 skill/ 同构
  docs/            人写的文档
  DocsManagement/  本层
  AGENTS.md（正本）  CLAUDE.md GEMINI.md -> AGENTS.md  README.md -> DocsManagement/README.md  .gitignore  .git/
```

## 2. `PROVENANCE.txt` 格式

UTF-8 纯文本。开头是说明段（可写 `#` 注释），之后每个目录一个块，块之间空一行。字段名左对齐 8 字符宽，续行缩进到冒号之后。引擎读写**往返保真**：块顺序和你加的字段都保留，只有 `USED_BY` 每次由引擎重算。

### 2.1 块头与 ID

```
[A06]  analyses/S1_crossnobis_2026-09-02/
```

- ID = 层前缀 + 两位序号。前缀：`I` inputs · `D` derivatives · `A` analyses · `W` writing · `X` archive。
- ID 由引擎分配（该层最大号 + 1）。**已有 ID 不得改指向、不得复用**：目录搬了只改路径（`mv` / `adopt --moved` 自动改）；目录进了 trash，块留着、`STATUS` 改 `trashed`。
- 路径相对项目根。过渡期允许登记不在五个层下的目录（`adopt <路径> --layer <层>`），搬进层里时路径自动更新。

### 2.2 字段

```
[A06]  analyses/S1_crossnobis_2026-09-02/
       ROLE    : analysis
       QUESTION: 左 S1 五个身体部位的 crossnobis 形状构型是否有组间差异（TD15 vs ID7）
       INPUTS  : [D01] derivatives/gen3_zavg/result_with_fisherz/motor-whole-brain-z/<subj>/<subj>_gradient_aligned_0{1,2}.nii.gz
                 [A03] analyses/TD_defined_bilateralBP_activity_clusters/stats/   （ROI 定义）
                 [I03] inputs/atlas_S1M1_strip_bins/data/
       CODE    : analyses/S1_crossnobis_2026-09-02/code/   git:3f9c1a2
       KEY     : stats/permanova_summary.csv  sha256:<完整 64 位>
       STATUS  : final
       REPORT  : docs/NOTES_crossnobis_S1.md
       NOTE    : hand mask 由 TD 组定义，operator 对两组不对称；结果按 exploratory 引用
       USED_BY : [A09] [W01]
```

| 字段 | 要求 |
|---|---|
| `ROLE` | `input` / `derivative` / `analysis` / `writing` / `archive`。`adopt` 按层填，可 `--role` 覆盖 |
| `QUESTION`（分析）/ `DESC`（其它） | 一句话。分析写它回答的问题；输入写**来源**（谁、什么时候、哪台机器、哪个脚本）；产物写是哪一代、什么方法 |
| `INPUTS` | 读了谁。**只许用 `[ID]` 引用本表里的块**，后面跟具体路径（可用 `<subj>`、`{1,2}` 占位，引擎按 glob 核对存在）。外部来源（Jetstream2、别的项目、旧机器）写完整路径并带 `外部` 或 `jetstream` 字样，引擎不去本地找。`input` 角色不需要此字段 |
| `CODE` | 用哪段代码算的：路径 + `git:<commit>`。找不到写 `UNKNOWN (原因)`，**不留空**。分析和产物必填 |
| `KEY` | 1–3 个代表性文件 + 完整 sha256，作为目录指纹：产物用组模板 npy，分析用主结果表，写作层用它自己的 `readme.txt`。用 `adopt <ID> --key <文件>` 算，不手抄。文件重跑过就必须重新指纹，否则红 |
| `STATUS` | `active` 进行中 · `final` 定稿 · `exploratory` 探索性 · `archived` 归档 · `superseded-by: [ID]` 被取代 · `trashed` 已进 trash。**不删旧块** |
| `REPORT` | 人看的叙述在哪（相对块路径）：`REPORT.md` / `docs/NOTES_*.md`。分析必填 |
| `REMOTE` | 可选。重数据在 Jetstream2 的路径（本地只有回传的轻量结果时） |
| `NOTE` | 可选。限定、口径、坑。分支 NOTES 里标过 exploratory / 小 ROI 不稳定 / operator 不对称 / 未过可靠性闸门的，**必须**写进来 |
| `USED_BY` | 引擎回填（谁的 `INPUTS` 引用了它），不手写 |

### 2.3 每个登记目录的 `README.md`

登记块回答「是什么 / 读了谁 / 哪段代码 / 变没变」各一行；**目录里的 `README.md` 负责让人和 agent 真正看懂它**——相当于 `abstract/` 里每个主题的 `RESULTS_<topic>.md` + `source/`，只是按角色定模板。`adopt` / `new` 发现没有就从模板写一份（带 `[ID]`、日期、TODO 段），不覆盖已有的。`status` 对没有 README 的报 `readme_missing`，对还留 TODO 的报 `readme_todo`。

| 角色 | 模板 | 必答的段 |
|---|---|---|
| input | `README.input.md` | 是什么 · **来源**（谁 / 机器 / 原路径 / 日期 / 生成方式 / 原件指纹）· **格式**（文件清单、形状、网格、覆盖范围）· **怎么读**（可运行的几行）· 已知问题 |
| derivative | `README.derivative.md` | 是什么（第几代、方法、与上代区别）· 输入 `[ID]` · **怎么算的**（代码 + commit、完整命令、环境、被试集、参数）· **输出布局** · 验证 · 怎么用 · 已知问题 |
| analysis | `README.analysis.md` | 问题 · 输入 · 方法 · **一段话结论（含限定）** · 报告在哪 · 布局 |
| writing | `README.writing.md` | 用途 · 内容清单 · 引用的 `[ID]` |
| archive | `README.archive.md` | 这是什么 · **为什么归档 / 被谁取代** · 原来在哪 · 怎么重建 |
| skill（`skills/*/`） | 沿用你现有的 `SKILL.md` 写法（作用 / 文件 / 流程 / 配置 / 用法 / 注意） | `status` 对缺 `SKILL.md` 的报 `skill_doc_missing` |

每个 README 末尾有「变更」段，追加一行即可；大改走 git log。这些 README 全部进 git（`inputs/ derivatives/ archive/` 只放行各自的 `README.md`，其余照样剪掉）。

agent 进项目的阅读深度因此是三层：`CLAUDE.md`（项目是什么）→ `README.md` 索引（每目录一行，目录名直接链到它的 README）→ 目录 `README.md`（看懂并能动手）。

### 2.4 变更史在 git 里

没有单独的 CHANGELOG 段：每个 `DocsManagement` 操作就是一个 commit（`adopt: [A06] …`、`mv: … -> …`、`trash: …`）。人手改了登记表或 README，用 `DocsManagement commit [-m …]` 入账（先跑一遍检查，有红不让提交）。查一个目录的来龙去脉用 `DocsManagement log <ID 或路径>`：列出改过它的 commit、搬过它的 PATHMAP 行、删过它的 DELETED 行。

## 3. 操作规则

1. **新分析**：`DocsManagement new <问题>` → `analyses/<问题>_YYYY-MM-DD/` 模板 + 块（`CODE`、`REPORT` 预填）+ README → 干活 → `adopt <ID> --key <主结果表>` → 填块里的 `QUESTION` / `INPUTS` / `CODE` 和 README 里的 TODO → `status` 无红无黄才算完成。**登记 = 块 + README 两样齐。**人手 `mkdir` 的也行，之后 `adopt <路径>` 补模板和块。
2. **产物换代**：`mkdir derivatives/genN_<tag>/` → 放产物 → `adopt derivatives/genN_<tag> --supersedes <旧ID> --key <组模板>`。旧代块留着、`STATUS` 变 `superseded-by`；**不覆盖旧代**。
3. **搬目录**：`DocsManagement mv <旧> <新>`：同盘 rename（mtime 不变）、旧位置留相对符号链接、PATHMAP 记一行、块路径和字段里的路径自动改。人手 `mv` 过的：`adopt --moved <旧> <新>` 补同样的账。`undo [N]` 回放最近 N 次。
4. **删东西**：不 `rm`。`DocsManagement trash <路径> -m <原因>` 移进 `archive/trash_<日期>/`，DELETED 记指纹和原因。登记过的目录要 `--force`，块保留、`STATUS` 变 `trashed`。真正 `rm` 等 `verify` 通过、放一段时间再说。
5. **大搬迁前后**：先 `freeze` 冻结全树 manifest；搬完 `verify`（默认比字节数，`--full` 比 sha256）；`缺失 = 变化 = 0` 才算搬完。基线永久留在 `baseline_<名>/`，进 git。
6. **只读区**：`inputs/`、`derivatives/` 对分析只读；要改先拷进自己的 `data/`。
7. **读谁**：分析只读 `inputs/`、`derivatives/`、和**日期早于自己**的 `analyses/*/stats/`；不读别人的 `data/`。引擎只查日期先后（黄），`stats/` 这条靠 review。
8. **限定随块走**：exploratory、小 ROI 不稳定、operator 不对称、未过闸门——写进 `STATUS` 或 `NOTE`；汇总进 `writing/` 时跟着走（`abstract/agent.md` 第 4 条是同一规则的文件级版本）。
9. **命名**：禁止空格、括号、冒号（AFNI 会出错）。新建分析带 `_YYYY-MM-DD` 后缀；**已有目录不改名**。
10. **顶层**：只许 `config.toml` 列的条目；过渡期的符号链接除外。新东西不落顶层。
11. **不手改 `README.md`**（生成物）。
12. **收工前** `DocsManagement status`：无红才结束；黄色当天能填的填掉。

## 4. 校验：`status` 查什么

红 = 错误，`--strict` 退出 1，pre-commit 用它拦人的提交；黄 = 待办，只提示。引擎自己的记账提交绕过 hook，但先做二进制 / 大文件检查。

| 颜色 | 代码 | 判定 |
|---|---|---|
| 红 | `top_level_stray` | 顶层条目不在白名单（过渡期可在 config 里降为黄） |
| 红 | `naming` | 五个层下任何路径分量含禁用字符（`trash_*/` 除外） |
| 红 | `missing` | 块路径不在磁盘上（`trashed` 除外） |
| 红 | `id_dangling` | `INPUTS` / `superseded-by` 引用了未定义的 ID |
| 红 | `status_invalid` | `STATUS` 不在允许值里 |
| 红 | `key_changed` | KEY 文件丢失，或 sha256 与登记不符 |
| 红 | `legacy_path` | `scan_dirs` 下的代码出现 `/mnt/mydisk` 等旧路径（config 可降为黄） |
| 红 | `staged_binary` / `staged_big` | `--staged`：暂存区有 `*.npy *.h5 *.nii*` 等二进制，或单文件超过 20 MB |
| 红 | `external_check` | `writing/abstract/check_readme.py` 退出非零 |
| 黄 | `unregistered` | 层下一级目录没有块 → `adopt` |
| 黄 | `moved?` | 有块缺目录、有目录缺块，且同名或 KEY 指纹一致 → `adopt --moved` |
| 黄 | `todo_field` | `QUESTION`/`DESC`、`INPUTS`、`CODE`、`REPORT`、`STATUS` 缺或 TODO |
| 黄 | `key_missing` | 没有 KEY |
| 黄 | `readme_missing` / `readme_todo` | 登记目录没有 README.md，或 README.md 里还有 TODO |
| 黄 | `template_missing` | 分析目录缺模板子目录 |
| 黄 | `skill_doc_missing` | `skills/*/` 缺 SKILL.md |
| 黄 | `dag_date` | 分析引用了日期晚于自己的分析 |
| 黄 | `input_path_missing` | `INPUTS` 路径在本地匹配不到（外部来源除外） |
| 黄 | `uncommitted` | `DocsManagement/` 下有手改未提交 → `DocsManagement commit` |

引擎管不了的：脚本运行时实际 open 了什么文件。`INPUTS` 是声明，不是监控。

## 5. 命令

| 命令 | 做什么 | commit |
|---|---|---|
| `init [--layout] [--install-launcher]` | 装进当前项目：config、登记表骨架、README 链接、allowlist `.gitignore`、pre-commit、`.claude/skills/DocsManagement/SKILL.md` 链接、`AGENTS.md`（没有才写，含 TODO 段）+ `CLAUDE.md`/`GEMINI.md` 链接，`git init`；`--layout` 顺便建空的层目录。可重复运行 | ✓ |
| `status [--strict] [--staged] [--json]` | 三方对账（§4）；顺手重生成 `README.md`（`--staged` 时不写）；`--json` 给 agent 和脚本 | — |
| `adopt <路径> [--desc] [--key 文件 …] [--status] [--supersedes ID] [--role] [--layer]` | 登记目录；补 README（按角色模板）和分析模板目录；已登记则更新 | ✓ |
| `adopt <ID> [--key …] [--status …] [--desc …] [--supersedes …]` | 更新已有块 | ✓ |
| `adopt --moved <旧> <新>` | 人手移动后补账 | ✓ |
| `new <问题> [--desc]` | 建模板分析目录 + 块 | ✓ |
| `mv <旧> <新>` | 移动 + 留链接 + PATHMAP | ✓ |
| `trash <路径> -m <原因> [--force]` | 进 trash + DELETED | ✓ |
| `undo [N]` | 回放最近 N 次移动 | ✓ |
| `freeze [--name] [--force]` | 全树 manifest | ✓ |
| `verify [--full] [--mtime] [--baseline 名] [--report 文件]` | 基线 × PATHMAP × DELETED 核对磁盘 | — |
| `commit [-m 信息]` | 人手改动入账：同 hook 的检查 → 暂存放行区 → 一个 commit | ✓ |
| `log [ID 或路径] [-n N]` | 时间线：改过它的 commit + PATHMAP / DELETED 行；无参数 = 最近的 commit | — |
| `install <项目根> [--force]` | 把代码四样拷到另一个项目 | — |

全局选项：`--no-commit`（只改文件不提交）、`--trailer`（commit 附加文本，或环境变量 `DOCSMANAGEMENT_TRAILER`）。
调用：PATH 上的启动器 `DocsManagement`（`init --install-launcher` 写入 `~/.local/bin/`，在项目任何子目录都能用），或 `python3 <ROOT>/DocsManagement/DocsManagement.py`。

## 6. 装到别的项目 / 升级

```
DocsManagement install /media/z/Disk1/Imitation     # 只拷代码四样
cd /media/z/Disk1/Imitation && DocsManagement init --layout   # 生成它自己的 config / 登记表 / 账本 / hook / 链接 / CLAUDE.md，建空层目录
```

升级 = `install <项目> --force` 覆盖代码四样；状态文件不受影响。各项目的 `config.toml` 独立。

## 7. 这份规范怎么改

- 引擎会查的规则改 `config.toml`（层、顶层白名单、模板、禁用字符、大小上限、旧路径、外部检查）。
- 散文规则改本文件；改了更新顶部版本号和 `VERSION`，commit message 写 `SKILL.md v0.x`。
- 已登记的块不因规范修订而重写；新字段只对之后的块生效。
