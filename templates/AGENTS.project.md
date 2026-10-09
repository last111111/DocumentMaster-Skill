# {name} — 项目规范（AGENTS.md）

给所有进入本项目的 agent（Claude Code、Codex、Gemini CLI、Cursor …）和人。`CLAUDE.md`、`GEMINI.md` 是指向本文件的链接，内容只有这一份。
本项目用 DocsManagement 管理目录与溯源。**动任何目录前先读 `DocsManagement/SKILL.md`**（50 行；它开头的 `---` 元数据是 Claude Code 用的，其它 agent 忽略即可）；字段和校验的细节在 `DocsManagement/SPEC.md`。
回复用中文；代码、注释、文件内容用英文（除非另有要求）。

## 先看哪里

1. `README.md` — 索引：每个登记目录一行（生成物，不手改）
2. `DocsManagement/PROVENANCE.txt` — 每个目录一个块：作用、输入、代码、指纹、状态
3. 各分析自己的 `README.md` / `REPORT.md`

## 节奏

- 开工：`DocsManagement status`（要机器可读加 `--json`；退出码 0 = 无红，`--strict` 时有红 = 1，用法错 = 2）
- 收工：`DocsManagement status` 没有红色再结束；人手加的目录用 `DocsManagement adopt` 登记；字段还是 TODO 的填掉；手改过的用 `DocsManagement commit` 入账

## 布局

`inputs/`（只读）· `derivatives/`（流程产物，按代）· `analyses/`（一问一目录）· `writing/` · `archive/` · `skills/` · `docs/`

- 要产出东西先 `DocsManagement plan --new <路径> --expect …`；新分析：`DocsManagement new <问题>`，不手建
- 不新建顶层目录；不 `rm`（用 `DocsManagement trash`）；不手改 `README.md`；不往 `inputs/`、`derivatives/` 写
- 删除、覆盖、改被试集、动远端数据——先列清单问人

## 项目是什么

TODO — 一段话：研究什么、数据是什么、流程是什么、当前哪一代产物可用

## 计算环境

TODO — 用哪个 python / R 环境、重活在哪台机器跑、路径

## 已知的坑

TODO — 踩过的，写在这里别再踩
