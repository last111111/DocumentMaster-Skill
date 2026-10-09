# DocumentMaster Skill (`DocsManagement`)

A project-level **provenance and layout reconciler** for research projects — one engine file, standard library only, git underneath.
Works with any agent that can run a shell (Claude Code, Codex, Gemini CLI, Cursor) and with humans.

项目级的**溯源与布局对账工具**：每个目录是什么、读了谁、用哪段代码算的、内容变没变、现在算不算数。
像 git，不像门卫——不拦你写，只告诉你磁盘、登记表、git 三方哪里对不上，并给出补账的那一条命令。

## Install into a project / 装进一个项目

```sh
git clone https://github.com/<you>/DocumentMaster-Skill
python3 DocumentMaster-Skill/DocsManagement.py install /path/to/project   # copies the four code files
cd /path/to/project
python3 DocsManagement/DocsManagement.py init --layout --install-launcher   # config, registry, hook, AGENTS.md, git init
DocsManagement status
```

Only `DocsManagement.py`, `SKILL.md`, `templates/`, `VERSION` are copied; the project's registry, ledgers and config stay in the project.

## What you get / 得到什么

```
<project>/
  AGENTS.md  (CLAUDE.md, GEMINI.md -> AGENTS.md)   instructions every agent reads
  README.md -> DocsManagement/README.md            generated index, one line per registered directory
  DocsManagement/
    DocsManagement.py  SKILL.md  templates/  VERSION   code
    config.toml        PROVENANCE.txt  README.md        rules · registry · index
    PATHMAP.tsv        DELETED.tsv     baseline_*/      ledgers · frozen manifests
  inputs/  derivatives/  analyses/  writing/  archive/  skills/  docs/
```

- `PROVENANCE.txt`: one block per directory — `ROLE QUESTION/DESC INPUTS CODE KEY STATUS REPORT USED_BY`.
- Every registered directory gets a role-specific `README.md` (input / derivative / analysis / writing / archive).
- `status` reports red (errors) and yellow (todo); the pre-commit hook blocks commits on red only.
- Every operation is one git commit; `log <ID>` gives the timeline of a directory.

## Commands / 命令

`init` · `status [--strict] [--staged] [--json]` · `adopt` · `new` · `mv` · `trash` · `undo` · `freeze` · `verify` · `commit` · `log` · `install`

Full specification: [SKILL.md](SKILL.md).

## Requirements

Python ≥ 3.11 (uses `tomllib`), git, POSIX shell. No third-party packages.
