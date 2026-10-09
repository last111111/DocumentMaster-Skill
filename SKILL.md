---
name: DocsManagement
description: 项目级溯源与布局的对账与规划工具。管整个项目的五个层（inputs / derivatives / analyses / writing / archive）：每个目录是什么、读了谁、用哪段代码算的、指纹是什么、规划了什么产出、现在什么状态。像 git 一样对账，不把门。新建分析、登记目录、规划产出、移动、归档、冻结基线、验证迁移、入账都走它。
type: project
---

# DocsManagement — agent 核心（50 行）

完整规范（字段、规则、校验表、命令选项）在同目录 `SPEC.md`，需要细节时再读；本文件够日常使用。
引擎是纯 Python + git，任何能跑 shell 的 agent 和人都能用。`--json` 给机器，`--help` 给人。

## 读项目的顺序（省 token）

1. 项目根 `AGENTS.md`（= `CLAUDE.md`）：项目是什么、环境、已知的坑。
2. 根 `README.md`：索引，每目录一行（ID · 目录 · 一句话 · 状态 · 产出进度 · 输入 · 被谁用），目录名链到它的 README。
3. 只打开要用的目录的 `README.md`。
4. **不要整读 `DocsManagement/PROVENANCE.txt`**；要某个块就 `grep -A12 '^\[A06\]'`，要历史就 `DocsManagement log A06`。

## 节奏

- 开工：`DocsManagement status`。红 = 错误，先修；黄 = 待办。
- 要产出东西之前：`DocsManagement plan --new <层/目录> --desc … --inputs <ID…> --expect '<文件模式>'`，先写下要产出什么。
- 建分析：`DocsManagement new <问题>`（不要手 mkdir）。登记人手建的目录：`DocsManagement adopt <路径>`。
- 跑完：`DocsManagement adopt <ID> --key <主结果文件>` 打指纹；`DocsManagement plan <ID>` 看产出 ✓/✗。
- 收工：填掉块和 README 里的 TODO → `DocsManagement commit` → `DocsManagement status` 无红再结束。

## 硬规则

- 顶层不放新东西；`inputs/`、`derivatives/` 对分析只读；要改先拷进自己的 `data/`。
- 不 `rm`，用 `DocsManagement trash <路径> -m <原因>`；不 `cp` 大数据；不手改根 `README.md`。
- 搬目录用 `DocsManagement mv`；手动挪了就 `DocsManagement adopt --moved <旧> <新>` 补账。
- 名字不含空格、括号、冒号；新分析带 `_YYYY-MM-DD`；已有目录不改名。
- 跨目录引用只写 `[ID]`；分析只读 `inputs/`、`derivatives/` 和更早分析的 `stats/`。
- 删除、覆盖、改被试集、动远端数据——先列清单问人。
- `STATUS: final` 之前：块无 TODO、有 KEY、README 无 TODO、`plan <ID>` 全 ✓。

## 命令一览

`status [--strict] [--staged] [--json]` · `plan [ID] [--new 路径] [--expect 模式]` · `new` · `adopt` · `mv` · `trash` · `undo` · `commit` · `log [ID]` · `freeze` · `verify` · `init` · `install`

## 登记块长这样（细节见 SPEC.md §2）

```
[D03]  derivatives/gen4_tent/
       ROLE    : derivative
       DESC    : gen4：TENT 残差 → FC → 梯度；云端算，本地只回传轻量结果
       INPUTS  : [I05] inputs/Mototopy_TENT/   [I01] inputs/masks/
       EXPECT  : wholebrain/<subj>/<subj>_gradient_aligned_{01..10}.nii.gz
                 wholebrain/group_average_gradients.npy
       CODE    : skills/dm-workflow/   git:<commit>
       KEY     : wholebrain/group_average_gradients.npy  sha256:…
       STATUS  : planned
```
