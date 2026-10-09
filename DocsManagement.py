#!/usr/bin/env python3
"""DocsManagement -- provenance and layout reconciler for a research project.

Git-like, not a gatekeeper: it never stops you from writing files. It tells you
what is out of sync between the disk, the registry (PROVENANCE.txt) and git,
and gives you one command to fix each item.

Location: <ROOT>/DocsManagement/DocsManagement.py
Code files: DocsManagement.py, SKILL.md, templates/, VERSION. Everything else in DocsManagement/ is project state.
Standard library only, Python >= 3.11.
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import os
import re
import shutil
import subprocess
import sys
import time
import tomllib
from datetime import date, datetime
from pathlib import Path

VERSION = "0.1.0"
ENGINE_DIR = Path(__file__).resolve().parent
ROOT = ENGINE_DIR.parent
DM = ROOT / "DocsManagement"
TEMPLATES = ENGINE_DIR / "templates"
REGISTRY = DM / "PROVENANCE.txt"
INDEX = DM / "README.md"
PATHMAP = DM / "PATHMAP.tsv"
DELETED = DM / "DELETED.tsv"

HEAD_RE = re.compile(r"^\[([A-Z]\d{2,3})\]\s+(\S+?)/?\s*$")
FIELD_RE = re.compile(r"^\s+([A-Z_]+)\s*:\s?(.*)$")
REF_RE = re.compile(r"\[([A-Z]\d{2,3})\]")
DATE_RE = re.compile(r"_(\d{4}-\d{2}-\d{2})$")
FIELD_ORDER = ["ROLE", "QUESTION", "DESC", "INPUTS", "EXPECT", "CODE", "KEY", "STATUS", "REPORT", "REMOTE", "NOTE", "USED_BY"]
STATUS_VALUES = {"planned", "active", "final", "exploratory", "archived", "trashed"}
ROLE_OF = {"inputs": "input", "derivatives": "derivative", "analyses": "analysis", "writing": "writing", "archive": "archive"}
PATHMAP_HEADER = "timestamp\top\told\tnew\ttag\n"
DELETED_HEADER = "timestamp\tpath\tsha256\tbytes\tfiles\ttrash\treason\ttag\n"

DEFAULTS = {
    "layout": {
        "layers": ["inputs", "derivatives", "analyses", "writing", "archive"],
        "top_level_allowed": ["inputs", "skills", "derivatives", "analyses", "writing", "archive", "docs",
                              "DocsManagement", "CLAUDE.md", "AGENTS.md", "README.md", ".gitignore", ".git",
                              ".claude", ".goal-task"],
        "transition_symlinks_ok": True,
        "top_level_strict": True,
    },
    "analysis_template": {"dirs": ["code", "data", "stats", "figures", "per_subject", "validation", "docs"],
                          "date_suffix": True},
    "naming": {"forbidden_chars": [" ", "(", ")", ":"]},
    "git": {"max_file_mb": 20,
            "binary_ext": [".npy", ".npz", ".h5", ".hdf5", ".nii", ".nii.gz", ".BRIK", ".BRIK.gz", ".HEAD", ".mat"]},
    "paths": {"legacy_patterns": ["/mnt/mydisk"], "legacy_level": "red", "scan_dirs": ["skills", "analyses/*/code"],
              "scan_ext": [".py", ".sh", ".R", ".txt", ".csv", ".tsv", ".json", ".env"]},
    "ids": {"prefix": {"inputs": "I", "derivatives": "D", "analyses": "A", "writing": "W", "archive": "X"}},
    "checks": {"scripts": ["writing/abstract/check_readme.py", "abstract/check_readme.py"], "skills_dir": "skills"},
    "agents": {"instructions": "AGENTS.md", "aliases": ["CLAUDE.md", "GEMINI.md"]},
    "subjects": {"file": "", "column": "subject"},
    "plan": {"scan_depth": 3},
}

DEFAULT_PREAMBLE = """DocsManagement PROVENANCE REGISTRY
One block per first-level directory under each layer. Edit by hand or via `DocsManagement adopt`.
Fields: ROLE, QUESTION|DESC, INPUTS ([ID] path ...), CODE, KEY (file sha256:...), STATUS, REPORT, REMOTE, NOTE.
USED_BY is recomputed by the engine. Blank line ends a block. Comments (#) only in this header.
"""

# ----------------------------------------------------------------- output
def _tty() -> bool:
    return sys.stdout.isatty() and os.environ.get("NO_COLOR") is None

RED, YEL, GRN, DIM, RST = ("\033[31m", "\033[33m", "\033[32m", "\033[2m", "\033[0m") if _tty() else ("",) * 5


def die(msg: str, code: int = 2):
    print(f"{RED}error:{RST} {msg}", file=sys.stderr)
    sys.exit(code)


def rel(p: Path) -> str:
    return os.path.relpath(p, ROOT).replace(os.sep, "/")


def norm_rel(s: str) -> str:
    """Path argument -> project-relative posix path without trailing slash."""
    p = Path(os.path.abspath(s))
    try:
        r = p.relative_to(ROOT)
    except ValueError:
        die(f"{s} is outside the project root {ROOT}")
    return r.as_posix().rstrip("/")


def now() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


# ----------------------------------------------------------------- config
def load_config() -> dict:
    cfg = {k: dict(v) for k, v in DEFAULTS.items()}
    p = DM / "config.toml"
    if p.exists():
        with p.open("rb") as f:
            user = tomllib.load(f)
        for sec, vals in user.items():
            cfg.setdefault(sec, {}).update(vals)
    return cfg


# ----------------------------------------------------------------- registry
class Block:
    def __init__(self, bid: str, path: str):
        self.id = bid
        self.path = path.strip("/")
        self.fields: dict[str, list[str]] = {}

    def get(self, name: str, default: str = "") -> str:
        v = self.fields.get(name)
        return v[0].strip() if v and v[0].strip() else default

    def lines(self, name: str) -> list[str]:
        return [l for l in self.fields.get(name, []) if l.strip()]

    def set(self, name: str, value):
        self.fields[name] = [value] if isinstance(value, str) else list(value)

    @property
    def layer(self) -> str:
        return self.path.split("/")[0]

    @property
    def date(self):
        m = DATE_RE.search(self.path.split("/")[-1])
        return m.group(1) if m else None

    def refs(self, name: str) -> list[str]:
        return [r for l in self.lines(name) for r in REF_RE.findall(l)]

    def status(self) -> str:
        return self.get("STATUS")

    def superseded_by(self):
        s = self.status()
        m = REF_RE.search(s) if s.startswith("superseded-by") else None
        return m.group(1) if m else None


def parse_registry(text: str):
    preamble, blocks, cur, last, seen = [], [], None, None, False
    for raw in text.splitlines():
        line = raw.rstrip("\n")
        m = HEAD_RE.match(line)
        if m:
            cur = Block(m.group(1), m.group(2))
            blocks.append(cur)
            last, seen = None, True
            continue
        if cur is None:
            if not seen:
                preamble.append(line)
            continue
        if not line.strip():
            cur, last = None, None
            continue
        if line.lstrip().startswith("#"):
            continue
        fm = FIELD_RE.match(line)
        if fm:
            last = fm.group(1)
            cur.fields.setdefault(last, []).append(fm.group(2).rstrip())
        elif last is not None:
            cur.fields[last].append(line.strip())
    return preamble, blocks


def render_registry(preamble, blocks) -> str:
    out = list(preamble) if any(l.strip() for l in preamble) else DEFAULT_PREAMBLE.splitlines()
    while out and not out[-1].strip():
        out.pop()
    out.append("")
    for b in blocks:
        out.append(f"[{b.id}]  {b.path}/")
        names = [n for n in FIELD_ORDER if n in b.fields] + [n for n in b.fields if n not in FIELD_ORDER]
        for n in names:
            vals = b.fields[n] or [""]
            out.append(f"       {n:<8}: {vals[0]}")
            out.extend(f"                 {v}" for v in vals[1:])
        out.append("")
    return "\n".join(out) + "\n"


def load_registry():
    if not REGISTRY.exists():
        return [], []
    return parse_registry(REGISTRY.read_text(encoding="utf-8"))


def recompute_used_by(blocks):
    users = {b.id: [] for b in blocks}
    for b in blocks:
        for r in b.refs("INPUTS"):
            if r in users and b.id not in users[r]:
                users[r].append(b.id)
    for b in blocks:
        if users[b.id]:
            b.set("USED_BY", " ".join(f"[{u}]" for u in sorted(users[b.id])))
        elif "USED_BY" in b.fields:
            del b.fields["USED_BY"]


def save_registry(preamble, blocks):
    recompute_used_by(blocks)
    DM.mkdir(exist_ok=True)
    REGISTRY.write_text(render_registry(preamble, blocks), encoding="utf-8")


def next_id(blocks, prefix: str) -> str:
    nums = [int(b.id[1:]) for b in blocks if b.id[0] == prefix]
    return f"{prefix}{max(nums, default=0) + 1:02d}"


def apply_move_to_registry(blocks, old: str, new: str) -> list[str]:
    """Rewrite block paths and any path strings in fields after old -> new."""
    changed = []
    pat = re.compile(r"(?<![\w./-])" + re.escape(old) + r"(?=/|\s|$)")
    for b in blocks:
        if b.path == old or b.path.startswith(old + "/"):
            b.path = new + b.path[len(old):]
            changed.append(b.id)
        for n, vals in b.fields.items():
            b.fields[n] = [pat.sub(new, v) for v in vals]
    return changed


# ----------------------------------------------------------------- index
def render_index(blocks, cfg):
    layers = cfg["layout"]["layers"]
    subjects = load_subjects(cfg)
    esc = lambda s: s.replace("|", "\\|")
    out = [f"# {ROOT.name} — index", "",
           f"Generated by DocsManagement {VERSION} on {datetime.now():%Y-%m-%d %H:%M}. "
           "Do not edit; edit `DocsManagement/PROVENANCE.txt` and rerun any DocsManagement command.", ""]
    by_layer: dict[str, list[Block]] = {}
    for b in blocks:
        by_layer.setdefault(b.layer if b.layer in layers else "other", []).append(b)
    for layer in layers + ["other"]:
        bl = by_layer.get(layer)
        if not bl:
            continue
        out += [f"## {layer}", "", "| ID | directory | what | status | outputs | inputs | used by |", "|---|---|---|---|---|---|---|"]
        for b in sorted(bl, key=lambda x: x.id):
            what = b.get("QUESTION") or b.get("DESC") or "TODO"
            ins = " ".join(f"[{r}]" for r in b.refs("INPUTS")) or "—"
            cell = f"[`{b.path}/`]({b.path}/README.md)" if (ROOT / b.path / "README.md").is_file() else f"`{b.path}/`"
            pr = progress(b, subjects) if (ROOT / b.path).exists() else None
            outs = f"{pr[0]}/{pr[1]}" if pr else "—"
            out.append(f"| {b.id} | {cell} | {esc(what)} | {esc(b.status() or 'TODO')} | {outs} | {ins} | {b.get('USED_BY') or '—'} |")
        out.append("")
    DM.mkdir(exist_ok=True)
    INDEX.write_text("\n".join(out), encoding="utf-8")


# ----------------------------------------------------------------- git
def git(*args):
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True)


def is_repo() -> bool:
    return (ROOT / ".git").exists() and git("rev-parse", "--git-dir").returncode == 0


def git_commit(paths, msg: str, trailer: str | None, enabled: bool = True):
    if not enabled or not is_repo():
        return None
    paths = [rel(Path(p)) if isinstance(p, Path) else p for p in paths]
    for p in dict.fromkeys(paths):
        if git("ls-files", "--", p).stdout.strip():
            git("add", "-u", "--", p)
        fp = ROOT / p
        if fp.exists() and git("check-ignore", "-q", "--", p).returncode != 0:
            r = git("-c", "advice.addIgnoredFile=false", "add", "-A", "--", p)
            if r.returncode != 0 and r.stderr.strip():
                print(f"{YEL}git add {p}:{RST} {r.stderr.strip().splitlines()[0]}")
    if git("diff", "--cached", "--quiet").returncode == 0:
        return None
    bad = staged_findings(load_config())
    if bad:
        print_findings(bad)
        die("refusing to commit: binary or oversized file staged")
    full = msg + (f"\n\n{trailer}" if trailer else "")
    r = git("commit", "-q", "--no-verify", "-m", full)
    if r.returncode != 0:
        print(f"{YEL}git commit failed:{RST} {r.stderr.strip() or r.stdout.strip()}")
        return None
    h = git("rev-parse", "--short", "HEAD").stdout.strip()
    print(f"  commit {h}  {msg}")
    return h


# ----------------------------------------------------------------- hashing / ledgers
def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


KEYCACHE = DM / ".keycache.json"
_CACHE: dict | None = None


def key_sha(p: Path) -> str:
    """sha256 of a KEY file, recomputed only when size or mtime changed (large files stay cheap)."""
    global _CACHE
    import json
    if _CACHE is None:
        try:
            _CACHE = json.loads(KEYCACHE.read_text()) if KEYCACHE.exists() else {}
        except ValueError:
            _CACHE = {}
    st = p.stat()
    ent = _CACHE.get(rel(p))
    if ent and ent[0] == st.st_size and ent[1] == st.st_mtime_ns:
        return ent[2]
    h = sha256_file(p)
    _CACHE[rel(p)] = [st.st_size, st.st_mtime_ns, h]
    try:
        KEYCACHE.write_text(json.dumps(_CACHE))
    except OSError:
        pass
    return h


def tree_digest(p: Path):
    """(sha256, bytes, nfiles) for a file or a directory tree (symlinks skipped)."""
    if p.is_file():
        return sha256_file(p), p.stat().st_size, 1
    h, size, n = hashlib.sha256(), 0, 0
    for dp, dn, fn in os.walk(p):
        dn.sort()
        for f in sorted(fn):
            fp = Path(dp) / f
            if fp.is_symlink():
                continue
            s = sha256_file(fp)
            size += fp.stat().st_size
            n += 1
            h.update(f"{fp.relative_to(p).as_posix()}\t{s}\n".encode())
    return h.hexdigest(), size, n


def ledger_append(path: Path, header: str, row: list[str]):
    DM.mkdir(exist_ok=True)
    if not path.exists():
        path.write_text(header)
    with path.open("a") as f:
        f.write("\t".join(row) + "\n")


def read_ledger(path: Path) -> list[dict]:
    if not path.exists():
        return []
    lines = path.read_text().splitlines()
    if not lines:
        return []
    cols = lines[0].split("\t")
    return [dict(zip(cols, l.split("\t"))) for l in lines[1:] if l.strip()]


def fix_symlinks(new_root: Path, old_root: Path) -> int:
    """After moving old_root -> new_root, re-point relative symlinks inside that targeted things outside the moved tree."""
    n = 0
    for dp, dn, fn in os.walk(new_root):
        for name in dn + fn:
            L = Path(dp) / name
            if not L.is_symlink():
                continue
            target = os.readlink(L)
            if os.path.isabs(target):
                continue
            old_dir = old_root / Path(dp).relative_to(new_root)
            old_abs = Path(os.path.normpath(old_dir / target))
            try:
                old_abs.relative_to(old_root)
                continue                       # pointed inside the moved tree: still valid
            except ValueError:
                pass
            if os.path.lexists(old_abs):
                nt = os.path.relpath(old_abs, L.parent)
                if nt != target:
                    L.unlink()
                    os.symlink(nt, L)
                    n += 1
    return n


def make_symlink(old: Path, new: Path):
    old.parent.mkdir(parents=True, exist_ok=True)
    os.symlink(os.path.relpath(new, old.parent), old)


def guard(path: str, cfg):
    top = path.split("/")[0]
    if top in {".git", "DocsManagement", ".claude"} or path in cfg["layout"]["layers"] or path == "":
        die(f"refusing to move/trash {path or '/'} (protected)")


# ----------------------------------------------------------------- planning (EXPECT)
def load_subjects(cfg) -> list[str]:
    sc = cfg.get("subjects", {})
    f = sc.get("file")
    if not f or not (ROOT / f).is_file():
        return []
    import csv
    col = sc.get("column", "subject")
    with (ROOT / f).open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    if not rows or col not in rows[0]:
        return []
    return [r[col].strip() for r in rows if (r.get(col) or "").strip()]


def expand_braces(pat: str) -> list[str]:
    """{a,b,c} and {01..10} (zero-padded when the start has a leading zero)."""
    m = re.search(r"\{([^{}]*)\}", pat)
    if not m:
        return [pat]
    body = m.group(1)
    if ".." in body:
        lo, hi = body.split("..", 1)
        width = len(lo) if len(lo) > 1 and lo.startswith("0") else 0
        items = [str(i).zfill(width) for i in range(int(lo), int(hi) + 1)]
    else:
        items = body.split(",")
    out = []
    for it in items:
        out.extend(expand_braces(pat[:m.start()] + it + pat[m.end():]))
    return out


def expect_patterns(b: Block) -> list[str]:
    pats = []
    for l in b.lines("EXPECT"):
        l = re.split(r"\s{2,}|\s[（(#]", l.strip())[0].strip()
        if l and l != "strict":
            pats.append(l)
    return pats


def expect_report(b: Block, subjects: list[str]):
    """[(pattern, missing_items, n_items)] — items are subjects for <subj> patterns, else expanded patterns."""
    base = ROOT / b.path
    out = []
    for pat in expect_patterns(b):
        if "<subj>" in pat:
            if subjects:
                miss = [s for s in subjects if not any(glob.glob(str(base / q)) for q in expand_braces(pat.replace("<subj>", s)))]
                out.append((pat, miss, len(subjects)))
            else:
                ok = any(glob.glob(str(base / q)) for q in expand_braces(pat.replace("<subj>", "*")))
                out.append((pat, [] if ok else ["(no subjects file configured)"], 1))
        else:
            qs = expand_braces(pat)
            out.append((pat, [q for q in qs if not glob.glob(str(base / q))], len(qs)))
    return out


def unexpected_files(b: Block, cfg, subjects: list[str]) -> list[str]:
    import fnmatch
    base = ROOT / b.path
    depth = cfg.get("plan", {}).get("scan_depth", 3)
    pats = []
    for pat in expect_patterns(b):
        for s in (subjects or ["*"]) if "<subj>" in pat else [None]:
            pats.extend(expand_braces(pat.replace("<subj>", s) if s else pat))
    extra = []
    for dp, dn, fn in os.walk(base):
        relp = Path(dp).relative_to(base)
        if len(relp.parts) >= depth:
            dn[:] = []
        for f in sorted(fn):
            if f == "README.md" or f.startswith("."):
                continue
            r = (relp / f).as_posix()
            if not any(fnmatch.fnmatch(r, q) for q in pats):
                extra.append(r)
    return extra


def progress(b: Block, subjects: list[str]):
    rep = expect_report(b, subjects)
    if not rep:
        return None
    total = sum(n for _, _, n in rep)
    return total - sum(len(m) for _, m, _ in rep), total


# ----------------------------------------------------------------- status
def layer_dirs(cfg) -> dict[str, list[str]]:
    out = {}
    for layer in cfg["layout"]["layers"]:
        d = ROOT / layer
        if d.is_dir():
            out[layer] = sorted(e.name for e in os.scandir(d)
                                if e.is_dir(follow_symlinks=False) and not e.name.startswith(".") and not e.name.startswith("trash_"))
    return out


def has_forbidden(name: str, chars) -> bool:
    return any(c in name for c in chars)


def status_findings(cfg, staged: bool = False) -> list[tuple]:
    F: list[tuple] = []
    lay = cfg["layout"]
    allowed = set(lay["top_level_allowed"])
    stray_level = "red" if lay.get("top_level_strict", True) else "yellow"
    for e in sorted(os.scandir(ROOT), key=lambda e: e.name):
        if e.name in allowed:
            continue
        if e.is_symlink() and lay.get("transition_symlinks_ok", True):
            continue
        F.append((stray_level, "top_level_stray", e.name + ("/" if e.is_dir() else ""), "not in layout.top_level_allowed"))
    chars = cfg["naming"]["forbidden_chars"]
    for layer in lay["layers"]:
        base = ROOT / layer
        if not base.is_dir():
            continue
        for dp, dn, fn in os.walk(base):
            dn[:] = [x for x in dn if not x.startswith("trash_")]
            if len(Path(dp).relative_to(base).parts) >= 3:
                dn[:] = []
            for n in dn + fn:
                if has_forbidden(n, chars):
                    F.append(("red", "naming", rel(Path(dp) / n), "forbidden character (space / paren / colon)"))
    preamble, blocks = load_registry()
    byid = {b.id: b for b in blocks}
    bypath = {b.path: b for b in blocks}
    present = layer_dirs(cfg)
    for layer, names in present.items():
        for n in names:
            if f"{layer}/{n}" not in bypath:
                F.append(("yellow", "unregistered", f"{layer}/{n}/", f"no block -> DocsManagement adopt {layer}/{n}"))
    missing = []
    for b in blocks:
        if b.status() in ("trashed", "planned"):
            continue
        if not (ROOT / b.path).exists():
            F.append(("red", "missing", b.path + "/", f"[{b.id}] registered but not on disk"))
            missing.append(b)
    subjects = load_subjects(cfg)
    for b in blocks:
        st = b.status()
        if st == "trashed":
            continue
        d = ROOT / b.path
        exists = d.exists()
        role = b.get("ROLE")
        todo: list[str] = []
        for r in b.refs("INPUTS"):
            if r not in byid:
                F.append(("red", "id_dangling", b.path + "/", f"[{b.id}] INPUTS refers to undefined [{r}]"))
        if not st:
            F.append(("yellow", "todo_field", b.path + "/", f"[{b.id}] STATUS missing"))
            todo.append("STATUS")
        elif st.startswith("superseded-by"):
            t = b.superseded_by()
            if not t or t not in byid:
                F.append(("red", "id_dangling", b.path + "/", f"[{b.id}] superseded-by target undefined"))
        elif st not in STATUS_VALUES:
            F.append(("red", "status_invalid", b.path + "/", f"[{b.id}] STATUS '{st}' not in {sorted(STATUS_VALUES)}"))
        what = b.get("QUESTION") or b.get("DESC")
        if not what or what.upper().startswith("TODO"):
            F.append(("yellow", "todo_field", b.path + "/", f"[{b.id}] QUESTION/DESC is TODO"))
            todo.append("QUESTION/DESC")
        if b.get("INPUTS", "TODO").upper().startswith("TODO") and role != "input":
            F.append(("yellow", "todo_field", b.path + "/", f"[{b.id}] INPUTS is TODO"))
            todo.append("INPUTS")
        if role in ("analysis", "derivative") and (not b.get("CODE") or "TODO" in b.get("CODE")):
            F.append(("yellow", "todo_field", b.path + "/", f"[{b.id}] CODE missing or TODO (path + git:<commit>, or UNKNOWN (reason))"))
            todo.append("CODE")
        if role == "analysis" and not b.get("REPORT"):
            F.append(("yellow", "todo_field", b.path + "/", f"[{b.id}] REPORT missing"))
            todo.append("REPORT")
        if st == "planned":
            if exists:
                F.append(("yellow", "planned_ready", b.path + "/", f"[{b.id}] directory exists now -> adopt {b.id} --status active"))
            continue
        if exists:
            keys = b.lines("KEY")
            kmt = 0
            if not keys:
                F.append(("yellow", "key_missing", b.path + "/", f"[{b.id}] no KEY -> adopt --key <file> {b.id}"))
                todo.append("KEY")
            for k in keys:
                kp, _, ksha = k.partition("sha256:")
                kp, ksha = kp.strip(), ksha.strip()
                f = d / kp
                if not f.is_file():
                    F.append(("red", "key_changed", f"{b.path}/{kp}", f"[{b.id}] KEY file missing"))
                else:
                    kmt = max(kmt, f.stat().st_mtime_ns)
                    if ksha and key_sha(f) != ksha:
                        F.append(("red", "key_changed", f"{b.path}/{kp}", f"[{b.id}] sha256 differs -> adopt --key {kp} {b.id}"))
            rd = d / "README.md"
            if not rd.is_file():
                F.append(("yellow", "readme_missing", b.path + "/", f"[{b.id}] no README.md -> adopt {b.path} (writes the template)"))
                todo.append("README")
            else:
                n = rd.read_text(errors="ignore").count("TODO")
                if n:
                    F.append(("yellow", "readme_todo", b.path + "/README.md", f"[{b.id}] {n} TODO left in README.md"))
                    todo.append(f"README({n} TODO)")
                if kmt and rd.stat().st_mtime_ns < kmt:
                    F.append(("yellow", "readme_stale", b.path + "/README.md", f"[{b.id}] README.md is older than its KEY file(s); update it (变更 section)"))
            if b.layer == "analyses":
                miss = [t for t in cfg["analysis_template"]["dirs"] if not (d / t).is_dir()]
                if miss:
                    F.append(("yellow", "template_missing", b.path + "/", f"[{b.id}] missing {' '.join(miss)} -> adopt {b.path}"))
            rep = expect_report(b, subjects)
            inc = [(pat, m, n) for pat, m, n in rep if m]
            if inc:
                pat, m, n = inc[0]
                F.append(("yellow", "expected_missing", b.path + "/", f"[{b.id}] {len(inc)}/{len(rep)} EXPECT patterns incomplete; {pat}: missing {' '.join(m[:6])}{' …' if len(m) > 6 else ''}"))
                todo.append(f"EXPECT({len(inc)} incomplete)")
            if rep and "strict" in [l.strip() for l in b.lines("EXPECT")]:
                extra = unexpected_files(b, cfg, subjects)
                if extra:
                    F.append(("yellow", "unexpected_file", b.path + "/", f"[{b.id}] {len(extra)} files match no EXPECT pattern, e.g. {' '.join(extra[:4])}"))
        if b.layer == "analyses" and b.date:
            for r in b.refs("INPUTS"):
                o = byid.get(r)
                if o and o.layer == "analyses" and o.date and o.date > b.date:
                    F.append(("yellow", "dag_date", b.path + "/", f"[{b.id}] reads [{r}] dated {o.date}, later than {b.date}"))
        for l in b.lines("INPUTS"):
            m = REF_RE.match(l)
            pth = (l[m.end():] if m else l).strip()
            pth = re.split(r"\s[（(#]", pth)[0].strip()
            if not pth or pth.upper().startswith("TODO") or pth.startswith("/") or "外部" in l or "jetstream" in l.lower():
                continue
            pat = re.sub(r"<[^>]+>", "*", pth)
            pat = re.sub(r"\{[^}]*\}", "*", pat)
            if not glob.glob(str(ROOT / pat)):
                F.append(("yellow", "input_path_missing", b.path + "/", f"[{b.id}] INPUTS path matches nothing: {pth}"))
        if st == "final" and todo:
            F.append(("red", "final_incomplete", b.path + "/", f"[{b.id}] STATUS final but still open: {', '.join(todo)}"))
    for b in missing:
        base = b.path.split("/")[-1]
        keys = [(kp.strip(), ksha.strip()) for kp, _, ksha in (k.partition("sha256:") for k in b.lines("KEY"))]
        for layer, names in present.items():
            for n in names:
                np_ = f"{layer}/{n}"
                if np_ in bypath:
                    continue
                hit = n == base
                for kp, ksha in ([] if hit else keys):
                    f = ROOT / np_ / kp
                    if ksha and f.is_file() and key_sha(f) == ksha:
                        hit = True
                        break
                if hit:
                    F.append(("yellow", "moved?", b.path + "/", f"looks like it is now at {np_}/ -> adopt --moved {b.path} {np_}"))
    sd = ROOT / cfg["checks"].get("skills_dir", "skills")
    if sd.is_dir():
        for e in sorted(os.scandir(sd), key=lambda e: e.name):
            if e.is_dir(follow_symlinks=False) and not e.name.startswith((".", "_")) and not (Path(e.path) / "SKILL.md").is_file():
                F.append(("yellow", "skill_doc_missing", f"{rel(Path(e.path))}/", "no SKILL.md (what it does, how to run, inputs/outputs)"))
    pats = cfg["paths"]["legacy_patterns"]
    exts = tuple(cfg["paths"]["scan_ext"])
    for sd in cfg["paths"]["scan_dirs"]:
        for d in glob.glob(str(ROOT / sd)):
            if Path(d).resolve() != Path(os.path.abspath(d)):
                continue
            for dp, dn, fn in os.walk(d):
                dn[:] = [x for x in dn if x != "__pycache__"]
                for f in fn:
                    if not f.endswith(exts):
                        continue
                    fp = Path(dp) / f
                    try:
                        txt = fp.read_text(errors="ignore")
                    except OSError:
                        continue
                    for i, line in enumerate(txt.splitlines(), 1):
                        if any(p in line for p in pats):
                            F.append((cfg["paths"].get("legacy_level", "red"), "legacy_path", f"{rel(fp)}:{i}", line.strip()[:80]))
                            break
    if staged:
        F += staged_findings(cfg)
    for s in cfg["checks"]["scripts"]:
        sp = ROOT / s
        if sp.exists():
            r = subprocess.run([sys.executable, sp.name], cwd=sp.parent, capture_output=True, text=True)
            if r.returncode != 0:
                tail = (r.stdout + r.stderr).strip().splitlines()
                F.append(("red", "external_check", s, tail[-1] if tail else "failed"))
    return F


def staged_findings(cfg) -> list[tuple]:
    F: list[tuple] = []
    if not is_repo():
        return F
    r = git("diff", "--cached", "--name-only", "-z")
    exts_b = tuple(cfg["git"]["binary_ext"])
    mx = cfg["git"]["max_file_mb"] * 1024 * 1024
    for f in filter(None, r.stdout.split("\0")):
        if f.startswith("DocsManagement/baseline_"):
            continue
        p = ROOT / f
        if f.endswith(exts_b):
            F.append(("red", "staged_binary", f, "binary data must not enter git"))
        elif p.exists() and p.stat().st_size > mx:
            F.append(("red", "staged_big", f, f"{p.stat().st_size / 1048576:.1f} MB > {cfg['git']['max_file_mb']} MB"))
    return F


def print_findings(F):
    if not F:
        print(f"{GRN}clean{RST} — disk, registry and git agree")
        return
    order = {"red": 0, "yellow": 1}
    for lvl, code, path, msg in sorted(F, key=lambda x: (order[x[0]], x[1], x[2])):
        col = RED if lvl == "red" else YEL
        print(f"{col}{'RED' if lvl == 'red' else 'YEL'}{RST}  {code:<18} {path}")
        print(f"     {DIM}{msg}{RST}")
    nr = sum(1 for f in F if f[0] == "red")
    print(f"\n{nr} red, {len(F) - nr} yellow")


# ----------------------------------------------------------------- scaffolding / registration
README_TEMPLATES = {"input": "README.input.md", "derivative": "README.derivative.md", "analysis": "README.analysis.md",
                    "writing": "README.writing.md", "archive": "README.archive.md"}


def scaffold(d: Path, cfg, role: str, name: str, bid: str):
    """README.md for every role; template subdirectories for analyses. Never touches existing files."""
    made = []
    if role == "analysis":
        for t in cfg["analysis_template"]["dirs"]:
            if not (d / t).is_dir():
                (d / t).mkdir(parents=True)
                made.append(t)
    rd = d / "README.md"
    if not rd.exists():
        tpl = (TEMPLATES / README_TEMPLATES.get(role, "README.input.md")).read_text(encoding="utf-8")
        rd.write_text(tpl.replace("{name}", name).replace("{date}", date.today().isoformat()).replace("{id}", bid), encoding="utf-8")
        made.append("README.md")
    stray = [e.name for e in os.scandir(d) if e.is_file() and e.name != "README.md" and not e.name.startswith(".")] if role == "analysis" else []
    return made, stray


def register(blocks, path: str, cfg, role=None, desc=None, layer=None, status=None):
    """Create a block for an existing directory. Returns (block, touched_paths)."""
    layer = layer or path.split("/")[0]
    if layer not in cfg["layout"]["layers"]:
        die(f"{path} is not under a registered layer; add --layer <inputs|derivatives|analyses|writing|archive>")
    b = Block(next_id(blocks, cfg["ids"]["prefix"][layer]), path)
    role = role or ROLE_OF[layer]
    b.set("ROLE", role)
    b.set("QUESTION" if role == "analysis" else "DESC", desc or "TODO")
    if role != "input":
        b.set("INPUTS", "TODO")
    if role == "analysis":
        b.set("CODE", f"{path}/code/   git:TODO")
        b.set("REPORT", "README.md")
    b.set("STATUS", status or ("archived" if layer == "archive" else "active"))
    blocks.append(b)
    print(f"  new block [{b.id}] {path}/  ({role})")
    touched = []
    made, stray = scaffold(ROOT / path, cfg, role, Path(path).name, b.id)
    if made:
        print("  scaffolded:", " ".join(made))
        touched.append(path)
    for s in stray:
        print(f"  note: loose file {s} at the top of the analysis dir — consider code/ or data/")
    return b, touched


def set_key(b: Block, keys: list[str]):
    lines = []
    for k in keys:
        f = ROOT / b.path / k
        if not f.is_file():
            die(f"KEY file not found: {b.path}/{k}")
        lines.append(f"{k}  sha256:{key_sha(f)}")
    b.set("KEY", lines)
    for l in lines:
        print("  KEY:", l)


# ----------------------------------------------------------------- commands
def cmd_init(a):
    cfg_path = DM / "config.toml"
    DM.mkdir(exist_ok=True)
    made = []
    if not cfg_path.exists():
        shutil.copy(TEMPLATES / "config.toml", cfg_path)
        made.append("DocsManagement/config.toml")
    cfg = load_config()
    if not REGISTRY.exists():
        REGISTRY.write_text(DEFAULT_PREAMBLE + "\n", encoding="utf-8")
        made.append("DocsManagement/PROVENANCE.txt")
    for p, h in ((PATHMAP, PATHMAP_HEADER), (DELETED, DELETED_HEADER)):
        if not p.exists():
            p.write_text(h)
            made.append(rel(p))
    gi = ROOT / ".gitignore"
    if not gi.exists():
        shutil.copy(TEMPLATES / "gitignore", gi)
        made.append(".gitignore")
    elif "DocsManagement allowlist" not in gi.read_text(errors="ignore"):
        print(f"{YEL}note:{RST} .gitignore exists and is not the DocsManagement allowlist; template left at DocsManagement/templates/gitignore")
    rd = ROOT / "README.md"
    if not rd.exists() and not rd.is_symlink():
        os.symlink("DocsManagement/README.md", rd)
        made.append("README.md -> DocsManagement/README.md")
    elif not rd.is_symlink():
        print(f"{YEL}note:{RST} README.md is a regular file; index stays at DocsManagement/README.md")
    sk = ROOT / ".claude" / "skills" / "DocsManagement"
    if sk.is_symlink():
        sk.unlink()
    sk.mkdir(parents=True, exist_ok=True)
    link = sk / "SKILL.md"
    if not link.exists() and not link.is_symlink():
        os.symlink("../../../DocsManagement/SKILL.md", link)
        made.append(".claude/skills/DocsManagement/SKILL.md -> ../../../DocsManagement/SKILL.md")
    ag = cfg["agents"]
    canon = ROOT / ag["instructions"]
    existing = [n for n in [ag["instructions"], *ag["aliases"]] if (ROOT / n).exists() and not (ROOT / n).is_symlink()]
    if existing:
        canon = ROOT / existing[0]          # a real instructions file already exists (e.g. CLAUDE.md): keep it canonical
        if "DocsManagement" not in canon.read_text(errors="ignore"):
            print(f"{YEL}note:{RST} {canon.name} exists but does not mention DocsManagement; add the 节奏 lines from templates/AGENTS.project.md")
    else:
        tpl = (TEMPLATES / "AGENTS.project.md").read_text(encoding="utf-8")
        canon.write_text(tpl.replace("{name}", ROOT.name), encoding="utf-8")
        made.append(f"{canon.name} (from template; fill the TODO sections)")
    for n in [ag["instructions"], *ag["aliases"]]:
        lp = ROOT / n
        if lp != canon and not lp.exists() and not lp.is_symlink():
            os.symlink(canon.name, lp)
            made.append(f"{n} -> {canon.name}")
    if a.layout:
        for d in cfg["layout"]["layers"] + ["skills", "docs"]:
            if not (ROOT / d).exists():
                (ROOT / d).mkdir()
                made.append(d + "/")
    gd = ROOT / ".git"
    if gd.exists() and not (gd / "HEAD").exists():
        die(".git exists but is not a repository (no HEAD). Move it aside first, e.g. mv .git .git.broken")
    if not gd.exists():
        r = git("init", "-q", "-b", "main")
        if r.returncode != 0:
            die("git init failed: " + r.stderr.strip())
        made.append(".git (git init)")
    hook = gd / "hooks" / "pre-commit"
    if not hook.exists() or "DocsManagement" in hook.read_text(errors="ignore"):
        hook.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(TEMPLATES / "pre-commit", hook)
        hook.chmod(0o755)
        made.append(".git/hooks/pre-commit")
    else:
        print(f"{YEL}note:{RST} a foreign pre-commit hook exists; add: DocsManagement status --strict --staged")
    if a.install_launcher:
        lp = Path.home() / ".local" / "bin" / "DocsManagement"
        lp.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(TEMPLATES / "launcher.sh", lp)
        lp.chmod(0o755)
        made.append(str(lp))
    preamble, blocks = load_registry()
    render_index(blocks, cfg)
    for m in made:
        print("  created", m)
    if not made:
        print("  already initialised")
    git_commit([DM, ".gitignore", "README.md", "CLAUDE.md", "AGENTS.md"], "DocsManagement init", a.trailer, not a.no_commit)


def cmd_status(a):
    cfg = load_config()
    F = status_findings(cfg, staged=a.staged)
    if is_repo() and not a.staged:
        dirty = git("status", "--porcelain", "--", "DocsManagement").stdout.strip()
        if dirty:
            F.append(("yellow", "uncommitted", "DocsManagement/", "hand edits not committed yet -> DocsManagement commit"))
    if a.json:
        import json
        nr = sum(1 for f in F if f[0] == "red")
        print(json.dumps({"root": str(ROOT), "red": nr, "yellow": len(F) - nr,
                          "findings": [{"level": l, "code": c, "path": p, "msg": m} for l, c, p, m in F]},
                         ensure_ascii=False, indent=1))
    else:
        print_findings(F)
    if not a.staged:
        _, blocks = load_registry()
        render_index(blocks, cfg)
    if a.strict and any(f[0] == "red" for f in F):
        sys.exit(1)


def cmd_adopt(a):
    cfg = load_config()
    preamble, blocks = load_registry()
    byid = {b.id: b for b in blocks}
    touched = [DM]
    if a.moved:
        old, new = norm_rel(a.moved[0]), norm_rel(a.moved[1])
        op, np_ = ROOT / old, ROOT / new
        if not np_.exists():
            die(f"{new} does not exist")
        if op.is_symlink():
            op.unlink()
        elif op.exists():
            die(f"{old} still exists; nothing to reconcile")
        changed = apply_move_to_registry(blocks, old, new)
        make_symlink(op, np_)
        ledger_append(PATHMAP, PATHMAP_HEADER, [now(), "moved", old, new, a.tag or ""])
        print(f"  recorded move {old} -> {new}; symlink left at {old}; blocks updated: {' '.join(changed) or 'none'}")
        save_registry(preamble, blocks)
        render_index(blocks, cfg)
        git_commit(touched + [old, new], f"adopt --moved: {old} -> {new}", a.trailer, not a.no_commit)
        return
    if not a.target:
        die("adopt needs a path or an ID")
    if re.fullmatch(r"[A-Z]\d{2,3}", a.target) and a.target in byid:
        b = byid[a.target]
    else:
        path = norm_rel(a.target)
        if not (ROOT / path).exists():
            die(f"{path} does not exist")
        bypath = {b.path: b for b in blocks}
        if path in bypath:
            b = bypath[path]
            print(f"  already registered as [{b.id}]; updating")
        else:
            b, t = register(blocks, path, cfg, role=a.role, desc=a.desc, layer=a.layer, status=a.status)
            touched += t
            byid[b.id] = b
    if b.status() == "planned" and (ROOT / b.path).is_dir() and not a.status:
        b.set("STATUS", "active")
        print(f"  [{b.id}] activated: planned -> active")
    if (ROOT / b.path).is_dir():
        made, _ = scaffold(ROOT / b.path, cfg, b.get("ROLE") or ROLE_OF.get(b.layer, "input"), Path(b.path).name, b.id)
        if made:
            print("  scaffolded:", " ".join(made))
            touched.append(b.path)
    if a.desc and b.get("QUESTION", "TODO").startswith("TODO") and b.get("DESC", "TODO").startswith("TODO"):
        b.set("QUESTION" if b.get("ROLE") == "analysis" else "DESC", a.desc)
    if a.key:
        set_key(b, a.key)
    if a.status:
        b.set("STATUS", a.status)
    if a.supersedes:
        o = byid.get(a.supersedes)
        if not o:
            die(f"unknown ID {a.supersedes}")
        o.set("STATUS", f"superseded-by: [{b.id}]")
        print(f"  [{o.id}] STATUS -> superseded-by: [{b.id}]")
    save_registry(preamble, blocks)
    render_index(blocks, cfg)
    git_commit(touched, f"adopt: [{b.id}] {b.path}", a.trailer, not a.no_commit)


def cmd_new(a):
    cfg = load_config()
    name = a.name.strip("/")
    if "/" in name or has_forbidden(name, cfg["naming"]["forbidden_chars"]):
        die("name must be a single path component without spaces, parentheses or colons")
    if cfg["analysis_template"]["date_suffix"] and not DATE_RE.search(name):
        name = f"{name}_{date.today().isoformat()}"
    d = ROOT / "analyses" / name
    if d.exists():
        die(f"{rel(d)} already exists")
    d.mkdir(parents=True)
    preamble, blocks = load_registry()
    planned = next((x for x in blocks if x.path == f"analyses/{name}" and x.status() == "planned"), None)
    if planned:
        b, touched = planned, [f"analyses/{name}"]
        b.set("STATUS", "active")
        if a.desc:
            b.set("QUESTION", a.desc)
        made, _ = scaffold(d, cfg, "analysis", name, b.id)
        print(f"  [{b.id}] activated: planned -> active; scaffolded: {' '.join(made)}")
    else:
        b, touched = register(blocks, f"analyses/{name}", cfg, role="analysis", desc=a.desc)
    save_registry(preamble, blocks)
    render_index(blocks, cfg)
    print(f"  created analyses/{name}/")
    git_commit([DM, f"analyses/{name}"], f"new: [{b.id}] analyses/{name}", a.trailer, not a.no_commit)


def op_mv(old: str, new: str, cfg, tag: str = "", trailer=None, commit=True, msg_prefix: str = ""):
    op, np_ = ROOT / old, ROOT / new
    if not op.exists() and not op.is_symlink():
        die(f"{old} does not exist")
    if np_.exists() or np_.is_symlink():
        die(f"{new} already exists")
    guard(old, cfg)
    guard(new, cfg)
    np_.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(op), str(np_))
    fixed = fix_symlinks(np_, op) if np_.is_dir() and not np_.is_symlink() else 0
    make_symlink(op, np_)
    preamble, blocks = load_registry()
    changed = apply_move_to_registry(blocks, old, new)
    ledger_append(PATHMAP, PATHMAP_HEADER, [now(), "mv", old, new, tag])
    save_registry(preamble, blocks)
    render_index(blocks, cfg)
    print(f"  moved {old} -> {new}; symlink left at {old}; blocks updated: {' '.join(changed) or 'none'}" + (f"; {fixed} inner symlinks re-pointed" if fixed else ""))
    git_commit([DM, old, new], f"{msg_prefix}mv: {old} -> {new}", trailer, commit)


def cmd_mv(a):
    op_mv(norm_rel(a.old), norm_rel(a.new), load_config(), a.tag or "", a.trailer, not a.no_commit)


def op_trash(path: str, message: str, cfg, force=False, tag: str = "", trailer=None, commit=True, msg_prefix: str = ""):
    p = ROOT / path
    if not p.exists() and not p.is_symlink():
        die(f"{path} does not exist")
    guard(path, cfg)
    preamble, blocks = load_registry()
    inside = [b for b in blocks if b.path == path or b.path.startswith(path + "/")]
    if inside and not force:
        die(f"{path} is registered ([{inside[0].id}]); use --force to trash it and mark the block(s) trashed")
    sha, size, n = ("symlink", 0, 0) if p.is_symlink() else tree_digest(p)
    tdir = ROOT / "archive" / f"trash_{date.today().isoformat()}"
    tdir.mkdir(parents=True, exist_ok=True)
    dest, i = tdir / p.name, 2
    while dest.exists() or dest.is_symlink():
        dest = tdir / f"{p.name}_{i}"
        i += 1
    shutil.move(str(p), str(dest))
    ledger_append(DELETED, DELETED_HEADER, [now(), path, sha, str(size), str(n), rel(dest), message, tag])
    for b in inside:
        b.set("STATUS", "trashed")
        b.set("NOTE", f"trashed {date.today().isoformat()}: {message}; see DELETED.tsv")
    save_registry(preamble, blocks)
    render_index(blocks, cfg)
    print(f"  trashed {path} -> {rel(dest)}  ({n} files, {size / 2**20:.1f} MiB)")
    git_commit([DM, path, rel(dest)], f"{msg_prefix}trash: {path} ({message})", trailer, commit)


def cmd_trash(a):
    op_trash(norm_rel(a.path), a.message, load_config(), a.force, a.tag or "", a.trailer, not a.no_commit)


def active_moves(tag: str | None = None):
    active = []
    for r in read_ledger(PATHMAP):
        if r["op"] in ("mv", "moved"):
            active.append((r["old"], r["new"], r.get("tag", "")))
        elif r["op"] == "undo":
            for j in range(len(active) - 1, -1, -1):
                if active[j][:2] == (r["new"], r["old"]):
                    active.pop(j)
                    break
    return [m for m in active if tag is None or m[2] == tag]


def cmd_undo(a):
    cfg = load_config()
    moves = active_moves(a.tag)
    todo = moves[::-1] if a.tag else moves[-a.n:][::-1]
    restored_trash = {r["old"] for r in read_ledger(PATHMAP) if r["op"] == "restore"}
    trash_rows = [r for r in read_ledger(DELETED) if a.tag and r.get("tag", "") == a.tag and r["trash"] not in restored_trash]
    if not todo and not trash_rows:
        die("nothing to undo" + (f" for tag {a.tag}" if a.tag else ""))
    preamble, blocks = load_registry()
    touched = [DM]
    for old, new, tag in todo:
        op, np_ = ROOT / old, ROOT / new
        if not np_.exists() and not np_.is_symlink():
            die(f"{new} is missing; cannot undo")
        if op.is_symlink():
            op.unlink()
        elif op.exists():
            die(f"{old} exists and is not a symlink; refusing")
        shutil.move(str(np_), str(op))
        if op.is_dir() and not op.is_symlink():
            fix_symlinks(op, np_)
        apply_move_to_registry(blocks, new, old)
        ledger_append(PATHMAP, PATHMAP_HEADER, [now(), "undo", new, old, tag])
        touched += [old, new]
        print(f"  undone: {new} -> {old}")
    for r in trash_rows[::-1]:
        src, dst = ROOT / r["trash"], ROOT / r["path"]
        if not src.exists() and not src.is_symlink():
            print(f"  {YEL}cannot restore{RST} {r['path']}: {r['trash']} is gone")
            continue
        if dst.exists() or dst.is_symlink():
            print(f"  {YEL}cannot restore{RST} {r['path']}: something is there now")
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        ledger_append(PATHMAP, PATHMAP_HEADER, [now(), "restore", r["trash"], r["path"], a.tag])
        for b in blocks:
            if b.status() == "trashed" and (b.path == r["path"] or b.path.startswith(r["path"] + "/")):
                b.set("STATUS", "active")
        touched += [r["path"], r["trash"]]
        print(f"  restored: {r['trash']} -> {r['path']}")
    save_registry(preamble, blocks)
    render_index(blocks, cfg)
    git_commit(touched, f"undo: {'tag ' + a.tag if a.tag else str(len(todo)) + ' move(s)'}", a.trailer, not a.no_commit)


def cmd_freeze(a):
    stamp = a.name or date.today().isoformat()
    bdir = DM / f"baseline_{stamp}"
    if bdir.exists() and not a.force:
        die(f"{rel(bdir)} exists; use --force to overwrite")
    bdir.mkdir(parents=True, exist_ok=True)
    du: dict[str, int] = {}
    nfiles = nbytes = 0
    t0 = time.time()
    with (bdir / "manifest.tsv").open("w") as man, (bdir / "tree.txt").open("w") as tree:
        man.write("path\tbytes\tmtime_ns\tsha256\n")
        for dp, dn, fn in os.walk(ROOT):
            dpp = Path(dp)
            dn[:] = sorted(d for d in dn if not (dpp == ROOT and d == ".git") and not (dpp == DM and d.startswith("baseline_")))
            for d in dn:
                full = dpp / d
                tree.write(f"l\t{rel(full)}\t{os.readlink(full)}\n" if full.is_symlink() else f"d\t{rel(full)}/\n")
            for f in sorted(fn):
                full = dpp / f
                r = rel(full)
                if full.is_symlink():
                    tree.write(f"l\t{r}\t{os.readlink(full)}\n")
                    man.write(f"{r}\t0\t0\tsymlink:{os.readlink(full)}\n")
                    continue
                st = full.stat()
                try:
                    s = sha256_file(full)
                except OSError as e:
                    s = f"error:{e.errno}"
                man.write(f"{r}\t{st.st_size}\t{st.st_mtime_ns}\t{s}\n")
                tree.write(f"f\t{r}\n")
                top = r.split("/")[0]
                du[top] = du.get(top, 0) + st.st_size
                nfiles += 1
                nbytes += st.st_size
                if nfiles % 500 == 0:
                    print(f"\r  {nfiles} files  {nbytes / 2**30:.1f} GiB  {time.time() - t0:.0f}s", end="", file=sys.stderr, flush=True)
    (bdir / "du.tsv").write_text("".join(f"{k}\t{v}\n" for k, v in sorted(du.items(), key=lambda x: -x[1])))
    print(f"\r  baseline {stamp}: {nfiles} files, {nbytes / 2**30:.1f} GiB, {time.time() - t0:.0f}s -> {rel(bdir)}/", file=sys.stderr)
    git_commit([bdir], f"freeze: baseline {stamp} ({nfiles} files, {nbytes / 2**30:.1f} GiB)", a.trailer, not a.no_commit)


def cmd_verify(a):
    bdirs = sorted(DM.glob("baseline_*"))
    bdir = DM / f"baseline_{a.baseline}" if a.baseline else (bdirs[-1] if bdirs else None)
    if not bdir or not (bdir / "manifest.tsv").exists():
        die("no baseline found; run freeze first")
    rows = [r for r in read_ledger(PATHMAP) if r["op"] in ("mv", "moved", "undo", "restore")]
    restored_trash = {r["old"] for r in rows if r["op"] == "restore"}
    deleted = [d["path"] for d in read_ledger(DELETED) if d["trash"] not in restored_trash]

    def map_path(p: str) -> str:
        for r in rows:
            o, n = r["old"], r["new"]
            if p == o or p.startswith(o + "/"):
                p = n + p[len(o):]
        return p

    ok = chg = miss = dele = 0
    problems = []
    with (bdir / "manifest.tsv").open() as f:
        next(f)
        for line in f:
            path, size, mtime, sha = line.rstrip("\n").split("\t")
            if sha.startswith("symlink:") or path.startswith("DocsManagement/"):
                continue
            cur = map_path(path)
            p = ROOT / cur
            if not p.exists():
                if any(cur == d or cur.startswith(d + "/") for d in deleted):
                    dele += 1
                else:
                    miss += 1
                    problems.append(("missing", path, cur))
                continue
            st = p.stat()
            if a.full:
                bad = sha256_file(p) != sha
            else:
                bad = st.st_size != int(size) or (a.mtime and st.st_mtime_ns != int(mtime))
            if bad:
                chg += 1
                problems.append(("changed", path, cur))
            else:
                ok += 1
    print(f"baseline {bdir.name}: ok {ok}  changed {chg}  missing {miss}  recorded-deleted {dele}  ({'sha256' if a.full else 'size' + ('+mtime' if a.mtime else '')}; DocsManagement/ state files not compared)")
    for kind, old, cur in problems[: a.limit]:
        print(f"  {kind:<8} {old}" + (f"  (now {cur})" if cur != old else ""))
    if len(problems) > a.limit:
        print(f"  ... {len(problems) - a.limit} more")
    if a.report:
        Path(a.report).write_text("".join(f"{k}\t{o}\t{c}\n" for k, o, c in problems))
    sys.exit(1 if (chg or miss) else 0)


def cmd_commit(a):
    """Book hand edits: same checks as the hook, stage the allowed region, one commit."""
    if not is_repo():
        die("not a git repository; run DocsManagement init")
    cfg = load_config()
    F = status_findings(cfg)
    reds = [f for f in F if f[0] == "red"]
    if reds:
        print_findings(reds)
        die("fix the red items first")
    _, blocks = load_registry()
    render_index(blocks, cfg)
    git("-c", "advice.addIgnoredFile=false", "add", "-A", "--", ".")
    bad = staged_findings(cfg)
    if bad:
        print_findings(bad)
        die("binary or oversized file staged")
    names = [n for n in git("diff", "--cached", "--name-only").stdout.splitlines() if n]
    if not names:
        print("  nothing to commit")
        return
    msg = a.message or ("edit: " + ", ".join(names[:4]) + (f" (+{len(names) - 4} more)" if len(names) > 4 else ""))
    full = msg + (f"\n\n{a.trailer}" if a.trailer else "")
    r = git("commit", "-q", "--no-verify", "-m", full)
    if r.returncode != 0:
        die("git commit failed: " + (r.stderr.strip() or r.stdout.strip()))
    print(f"  commit {git('rev-parse', '--short', 'HEAD').stdout.strip()}  {msg}  ({len(names)} files)")


def cmd_log(a):
    """Timeline of a block (by ID) or a path: commits that touched it + ledger rows."""
    if not is_repo():
        die("not a git repository")
    fmt = "--format=%h  %ad  %s"
    if not a.target:
        print(git("log", f"-n{a.n}", "--date=short", fmt).stdout.rstrip())
        return
    _, blocks = load_registry()
    byid = {b.id: b for b in blocks}
    if a.target in byid:
        b = byid[a.target]
        paths = {b.path}
        print(f"[{b.id}] {b.path}/   STATUS {b.status() or '-'}")
        print("-- commits that changed this block in PROVENANCE.txt")
        out = git("log", f"-n{a.n}", "--date=short", fmt, "-G", re.escape(f"[{b.id}]"), "--", rel(REGISTRY)).stdout.rstrip()
        print(out or "   (none)")
    else:
        paths = {norm_rel(a.target)}
        print(f"{next(iter(paths))}")
    for pth in sorted(paths):
        print(f"-- commits that touched {pth}")
        out = git("log", f"-n{a.n}", "--date=short", fmt, "--", pth).stdout.rstrip()
        print(out or "   (none tracked)")
        rows = [r for r in read_ledger(PATHMAP) if pth in (r["old"], r["new"]) or r["old"].startswith(pth + "/") or r["new"].startswith(pth + "/")]
        if rows:
            print(f"-- PATHMAP rows")
            for r in rows:
                print(f"   {r['timestamp']}  {r['op']:<6} {r['old']} -> {r['new']}")
        rows = [r for r in read_ledger(DELETED) if r["path"] == pth or r["path"].startswith(pth + "/")]
        if rows:
            print(f"-- DELETED rows")
            for r in rows:
                print(f"   {r['timestamp']}  {r['path']} -> {r['trash']}  ({r['reason']})")


def cmd_plan(a):
    cfg = load_config()
    preamble, blocks = load_registry()
    byid = {b.id: b for b in blocks}
    bypath = {b.path: b for b in blocks}
    subjects = load_subjects(cfg)
    if a.new:
        path = a.new.strip("/")
        if path in bypath:
            die(f"{path} is already registered as [{bypath[path].id}]")
        layer = a.layer or path.split("/")[0]
        if layer not in cfg["layout"]["layers"]:
            die("path must start with a layer, or pass --layer")
        b = Block(next_id(blocks, cfg["ids"]["prefix"][layer]), path)
        role = a.role or ROLE_OF[layer]
        b.set("ROLE", role)
        b.set("QUESTION" if role == "analysis" else "DESC", a.desc or "TODO")
        b.set("INPUTS", [f"[{i}]" for i in a.inputs] if a.inputs else "TODO")
        if a.expect:
            b.set("EXPECT", list(a.expect))
        b.set("STATUS", "planned")
        blocks.append(b)
        save_registry(preamble, blocks)
        render_index(blocks, cfg)
        print(f"  planned [{b.id}] {path}/  ({role}); create the directory later with `new` or `adopt` and it activates")
        git_commit([DM], f"plan: [{b.id}] {path}", a.trailer, not a.no_commit)
        return
    if a.target:
        b = byid.get(a.target) or bypath.get(norm_rel(a.target) if "/" in a.target or (ROOT / a.target).exists() else a.target)
        if not b:
            die(f"unknown ID or unregistered path: {a.target}")
        if a.expect:
            b.set("EXPECT", b.lines("EXPECT") + list(a.expect))
            save_registry(preamble, blocks)
            render_index(blocks, cfg)
            print(f"  [{b.id}] EXPECT += {len(a.expect)} pattern(s)")
            git_commit([DM], f"plan: [{b.id}] +{len(a.expect)} EXPECT", a.trailer, not a.no_commit)
        rep = expect_report(b, subjects) if (ROOT / b.path).exists() else []
        print(f"[{b.id}] {b.path}/   STATUS {b.status() or '-'}   subjects file: {cfg['subjects'].get('file') or '(none)'}")
        if not b.lines("EXPECT"):
            print("  no EXPECT lines; add with:  DocsManagement plan {} --expect '<pattern>'".format(b.id))
        elif not (ROOT / b.path).exists():
            for pat in expect_patterns(b):
                print(f"  (planned) {pat}")
        for pat, m, n in rep:
            mark = "ok      " if not m else f"{n - len(m):>3}/{n:<4}"
            print(f"  {mark} {pat}" + (f"   missing: {' '.join(m[:8])}{' …' if len(m) > 8 else ''}" if m else ""))
        return
    planned = [b for b in blocks if b.status() == "planned"]
    print("planned (no directory yet):" if planned else "planned: none")
    for b in planned:
        what = b.get("QUESTION") or b.get("DESC") or "TODO"
        print(f"  [{b.id}] {b.path}/  {what}  inputs: {' '.join(f'[{r}]' for r in b.refs('INPUTS')) or '-'}")
    rows = []
    for b in blocks:
        if b.status() in ("planned", "trashed") or not (ROOT / b.path).exists():
            continue
        pr = progress(b, subjects)
        if pr and pr[0] < pr[1]:
            rows.append((b, pr))
    print("in progress (EXPECT incomplete):" if rows else "in progress: none")
    for b, (ok, total) in rows:
        print(f"  [{b.id}] {b.path}/  {ok}/{total}")



# ----------------------------------------------------------------- rebuild (existing projects)
PLAN_COLS = ["action", "src", "dst", "role", "desc", "reason", "status"]
SKIP_TOP = {"DocsManagement", ".git", ".gitignore", ".claude", ".goal-task", ".codex", ".agents", "AGENTS.md", "CLAUDE.md", "GEMINI.md", "README.md"}


def _guess(e, cfg) -> tuple[str, str, str, str]:
    """(action, dst, role, reason) for a top-level entry — suggestions only."""
    n, low = e.name, e.name.lower()
    layers = cfg["layout"]["layers"]
    if n in layers or n in ("docs", "skills"):
        return "keep", "", "", "already in layout"
    if e.is_file(follow_symlinks=False):
        if n.startswith("stdout:") or n.endswith((".pyc", ".tmp")):
            return "trash", "", "", "junk"
        if n.endswith((".tar.xz", ".tar.gz", ".tgz", ".zip", ".7z")):
            return "mv", f"archive/tarballs/{n}", "", "snapshot archive"
        if n.endswith((".py", ".sh", ".R")):
            return "mv", f"skills/pipeline/{n}", "", "loose script"
        if n.endswith((".md", ".txt")):
            return "mv", f"docs/{n}", "", "loose document"
        return "ask", "", "", "loose file"
    if e.is_symlink():
        return "keep", "", "", "symlink"
    if n == "__pycache__" or n.endswith("_tmp"):
        return "trash", "", "", "cache/temp"
    try:
        if not any(os.scandir(e.path)):
            return "trash", "", "", "empty directory"
    except OSError:
        pass
    if has_forbidden(n, cfg["naming"]["forbidden_chars"]):
        return "ask", "", "", "name has forbidden characters; choose a new name"
    sub = {x.name for x in os.scandir(e.path)}
    if DATE_RE.search(n) or {"code", "stats"} <= sub or "REPORT.md" in sub:
        return "mv", f"analyses/{n}", "analysis", "analysis-shaped (code/stats/REPORT or dated)"
    if any(k in low for k in ("mask", "atlas", "raw", "input", "subject", "glm", "tent", "bids")):
        return "mv", f"inputs/{n}", "input", "input-like name"
    if any(k in low for k in ("abstract", "paper", "review", "writing", "brief", "manuscript")):
        return "mv", f"writing/{n}", "writing", "writing-like name"
    if any(k in low for k in ("old", "test", "archive", "backup", "legacy", "deprecated")):
        return "mv", f"archive/{n}", "archive", "archive-like name"
    if any(k in low for k in ("result", "gradient", "deriv", "output", "fc", "matrix")):
        return "mv", f"derivatives/{n}", "derivative", "derivative-like name"
    return "ask", "", "", "unclassified"


def read_plan(path: Path) -> list[dict]:
    rows = []
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
        if not line.strip() or line.startswith("#") or (i == 0 and line.startswith("action\t")):
            continue
        parts = (line.split("\t") + [""] * len(PLAN_COLS))[: len(PLAN_COLS)]
        rows.append(dict(zip(PLAN_COLS, [x.strip() for x in parts])))
    return rows


def write_plan(path: Path, rows: list[dict]):
    path.write_text("\t".join(PLAN_COLS) + "\n" + "".join("\t".join(r.get(c, "") for c in PLAN_COLS) + "\n" for r in rows), encoding="utf-8")


def check_plan(rows: list[dict], cfg) -> list[str]:
    errs = []
    layers = set(cfg["layout"]["layers"]) | {"docs", "skills"}
    moved_src, dsts = [], set()
    for i, r in enumerate(rows, 1):
        act, src, dst = r["action"], r["src"].strip("/"), r["dst"].strip("/")
        if r.get("status") == "done":
            moved_src.append(src)
            continue
        if act == "ask":
            errs.append(f"row {i}: still 'ask' ({src}) — decide mv / trash / keep")
            continue
        if act not in ("mv", "trash", "adopt", "keep"):
            errs.append(f"row {i}: unknown action {act!r}")
            continue
        if act in ("mv", "trash"):
            if not src:
                errs.append(f"row {i}: {act} needs src")
            elif not (ROOT / src).exists() and not (ROOT / src).is_symlink():
                errs.append(f"row {i}: src missing: {src}")
            for m in moved_src:
                if src == m or src.startswith(m + "/"):
                    errs.append(f"row {i}: src {src} is inside {m}, which an earlier row already moves — order the inner row first")
            if src.split("/")[0] in {".git", "DocsManagement"}:
                errs.append(f"row {i}: protected: {src}")
        if act == "mv":
            if not dst:
                errs.append(f"row {i}: mv needs dst")
            else:
                if dst.split("/")[0] not in layers and not dst.startswith("archive/"):
                    errs.append(f"row {i}: dst must be under a layer, docs/ or skills/: {dst}")
                if (ROOT / dst).exists() or (ROOT / dst).is_symlink():
                    errs.append(f"row {i}: dst already exists: {dst}")
                if dst in dsts:
                    errs.append(f"row {i}: duplicate dst: {dst}")
                if any(has_forbidden(part, cfg["naming"]["forbidden_chars"]) for part in dst.split("/")):
                    errs.append(f"row {i}: dst has forbidden characters: {dst}")
                dsts.add(dst)
            moved_src.append(src)
        if act == "adopt" and not dst:
            errs.append(f"row {i}: adopt needs dst")
    return errs


def ref_counts(names: list[str], cfg) -> dict[str, int]:
    """How many text files mention each old top-level name (outside that path) — symlink load after the move."""
    exts = tuple(cfg["paths"]["scan_ext"]) + (".md", ".txt", ".csv", ".json")
    counts = {n: 0 for n in names}
    for dp, dn, fn in os.walk(ROOT):
        relp = Path(dp).relative_to(ROOT)
        dn[:] = [d for d in dn if d not in (".git", "__pycache__", "DocsManagement") and not (Path(dp) / d).is_symlink()]
        if len(relp.parts) >= 4:
            dn[:] = []
        for f in fn:
            if not f.endswith(exts):
                continue
            fp = Path(dp) / f
            try:
                if fp.stat().st_size > 2_000_000:
                    continue
                txt = fp.read_text(errors="ignore")
            except OSError:
                continue
            for n in names:
                top = n.split("/")[0]
                if (str(relp) == top or str(relp).startswith(top + "/")):
                    continue
                if n in txt:
                    counts[n] += 1
    return counts


def cmd_rebuild(a):
    cfg = load_config()
    if a.survey:
        rows = []
        for e in sorted(os.scandir(ROOT), key=lambda e: e.name):
            if e.name in SKIP_TOP:
                continue
            act, dst, role, reason = _guess(e, cfg)
            rows.append({"action": act, "src": e.name, "dst": dst, "role": role, "desc": "", "reason": reason, "status": ""})
        out = DM / (a.survey if a.survey != "-" else f"rebuild_{date.today().isoformat()}.tsv")
        if out.exists() and not a.force:
            die(f"{rel(out)} exists; edit it, or --force to overwrite")
        write_plan(out, rows)
        n = {k: sum(1 for r in rows if r["action"] == k) for k in ("mv", "trash", "adopt", "keep", "ask")}
        print(f"  wrote {rel(out)}: {len(rows)} rows — " + ", ".join(f"{k} {v}" for k, v in n.items() if v))
        print("  edit it (tab-separated: action src dst role desc reason), resolve every 'ask', then: rebuild --check <file>")
        return
    plan = Path(a.plan) if a.plan else None
    if not plan or not plan.exists():
        die("give a plan file: rebuild --survey  →  rebuild --check PLAN  →  rebuild --apply PLAN")
    rows = read_plan(plan)
    errs = check_plan(rows, cfg)
    for e in errs:
        print(f"{RED}✗{RST} {e}")
    pending = [r for r in rows if r.get("status") != "done"]
    n = {k: sum(1 for r in pending if r["action"] == k) for k in ("mv", "trash", "adopt", "keep", "ask")}
    print(f"plan {plan.name}: {len(rows)} rows, {len(pending)} pending — " + ", ".join(f"{k} {v}" for k, v in n.items() if v))
    if a.check or errs:
        mv_src = [r["src"].strip("/") for r in pending if r["action"] == "mv"]
        if mv_src:
            print("  references to paths being moved (text files outside them; symlinks will carry these):")
            for k, v in ref_counts(mv_src, cfg).items():
                print(f"    {v:5d}  {k}")
        if errs:
            die(f"{len(errs)} problem(s); fix the plan")
        print(f"{GRN}plan is valid{RST}; apply with: rebuild --apply {plan.name}")
        return
    # ---- apply ----
    name = a.name or plan.stem
    if not sorted(DM.glob("baseline_*")):
        if a.freeze:
            a2 = argparse.Namespace(name=None, force=False, trailer=a.trailer, no_commit=a.no_commit)
            cmd_freeze(a2)
        else:
            die("no baseline; run `freeze` first (or rebuild --apply --freeze)")
    mig = DM / "migrations"
    mig.mkdir(exist_ok=True)
    record = mig / f"{name}.tsv"
    for d in cfg["layout"]["layers"] + ["docs", "skills"]:
        (ROOT / d).mkdir(exist_ok=True)
    done = 0
    for i, r in enumerate(rows, 1):
        if r.get("status") == "done" or r["action"] == "keep":
            continue
        act, src, dst = r["action"], r["src"].strip("/"), r["dst"].strip("/")
        pre = f"[rebuild:{name}] "
        try:
            if act == "mv":
                op_mv(src, dst, cfg, tag=name, trailer=a.trailer, commit=not a.no_commit, msg_prefix=pre)
                if r["role"] or dst.count("/") == 1 and dst.split("/")[0] in cfg["layout"]["layers"]:
                    preamble, blocks = load_registry()
                    if dst not in {b.path for b in blocks}:
                        b, _ = register(blocks, dst, cfg, role=r["role"] or None, desc=r["desc"] or None)
                        save_registry(preamble, blocks)
                        render_index(blocks, cfg)
                        git_commit([DM, dst], f"{pre}adopt: [{b.id}] {dst}", a.trailer, not a.no_commit)
            elif act == "trash":
                op_trash(src, r["reason"] or r["desc"] or "rebuild", cfg, force=True, tag=name, trailer=a.trailer, commit=not a.no_commit, msg_prefix=pre)
            elif act == "adopt":
                preamble, blocks = load_registry()
                if dst not in {b.path for b in blocks}:
                    b, _ = register(blocks, dst, cfg, role=r["role"] or None, desc=r["desc"] or None)
                    save_registry(preamble, blocks)
                    render_index(blocks, cfg)
                    git_commit([DM, dst], f"{pre}adopt: [{b.id}] {dst}", a.trailer, not a.no_commit)
        except SystemExit:
            r["status"] = "failed"
            write_plan(record, rows)
            write_plan(plan, rows)
            print(f"{RED}stopped at row {i}{RST} ({act} {src}); fix and re-run --apply to resume")
            raise
        r["status"] = "done"
        done += 1
        write_plan(record, rows)
        write_plan(plan, rows)
    print(f"  {done} rows applied; record: {rel(record)}")
    git_commit([DM], f"rebuild {name}: {done} rows applied", a.trailer, not a.no_commit)
    print("  verifying against the latest baseline …")
    a3 = argparse.Namespace(baseline=None, full=False, mtime=False, limit=20, report=None)
    try:
        cmd_verify(a3)
    except SystemExit as e:
        if e.code:
            print(f"{YEL}verify reported problems; inspect before going further (undo --tag {name} reverts everything){RST}")

CODE_FILES = ["DocsManagement.py", "SKILL.md", "SPEC.md", "VERSION", "templates"]


def cmd_install(a):
    dest = Path(os.path.abspath(a.project)) / "DocsManagement"
    if dest.resolve() == DM.resolve():
        die("that is this project")
    dest.mkdir(parents=True, exist_ok=True)
    for name in CODE_FILES:
        src, dst = ENGINE_DIR / name, dest / name
        if dst.exists() and not a.force:
            print(f"  exists, kept: {dst}  (use --force to overwrite)")
            continue
        if src.is_dir():
            shutil.rmtree(dst, ignore_errors=True)
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)
        print(f"  copied {name} -> {dst}")
    print(f"next: cd {dest.parent} && python3 DocsManagement/DocsManagement.py init")


# ----------------------------------------------------------------- cli
def main(argv=None):
    ap = argparse.ArgumentParser(prog="DocsManagement", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", action="version", version=f"DocsManagement {VERSION} (root {ROOT})")
    ap.add_argument("--trailer", default=os.environ.get("DOCSMANAGEMENT_TRAILER"), help="text appended to every commit message")
    ap.add_argument("--no-commit", action="store_true", help="change files but do not git commit")
    sp = ap.add_subparsers(dest="cmd", required=True)

    p = sp.add_parser("init", help="install into this project: config, registry, .gitignore, hook, skill link, git init")
    p.add_argument("--install-launcher", action="store_true", help="also write ~/.local/bin/DocsManagement")
    p.add_argument("--layout", action="store_true", help="also create the empty layer directories (fresh project)")
    p.set_defaults(fn=cmd_init)

    p = sp.add_parser("status", help="reconcile disk, registry and git; report red (errors) and yellow (todo)")
    p.add_argument("--strict", action="store_true", help="exit 1 if anything is red")
    p.add_argument("--staged", action="store_true", help="also check the git staging area (pre-commit)")
    p.add_argument("--json", action="store_true", help="machine-readable output for agents and scripts")
    p.set_defaults(fn=cmd_status)

    p = sp.add_parser("adopt", help="register a directory, update a block, or record a manual move")
    p.add_argument("target", nargs="?", help="directory path, or an existing ID to update")
    p.add_argument("--moved", nargs=2, metavar=("OLD", "NEW"), help="record a move done by hand")
    p.add_argument("--tag", help="with --moved: group under a name")
    p.add_argument("--role", choices=sorted(set(ROLE_OF.values())))
    p.add_argument("--layer", choices=DEFAULTS["layout"]["layers"], help="layer for a directory not under a layer dir (transition)")
    p.add_argument("--desc", help="QUESTION / DESC one-liner")
    p.add_argument("--key", action="append", metavar="FILE", help="KEY file relative to the directory (repeatable)")
    p.add_argument("--status", help="active | final | exploratory | archived | superseded-by: [ID]")
    p.add_argument("--supersedes", metavar="ID", help="mark that block as superseded by this one")
    p.set_defaults(fn=cmd_adopt)

    p = sp.add_parser("new", help="create analyses/<name>_<date>/ from the template and register it")
    p.add_argument("name")
    p.add_argument("--desc")
    p.set_defaults(fn=cmd_new)

    p = sp.add_parser("mv", help="move a path, leave a symlink behind, record it in PATHMAP")
    p.add_argument("old")
    p.add_argument("new")
    p.add_argument("--tag", help="group this move under a name (undo --tag reverts the group)")
    p.set_defaults(fn=cmd_mv)

    p = sp.add_parser("trash", help="move a path into archive/trash_<date>/ and record it in DELETED")
    p.add_argument("path")
    p.add_argument("-m", "--message", required=True, help="reason")
    p.add_argument("--force", action="store_true", help="allow trashing a registered directory")
    p.add_argument("--tag", help="group this under a name (undo --tag restores the group)")
    p.set_defaults(fn=cmd_trash)

    p = sp.add_parser("undo", help="replay the last N moves backwards, or a whole tagged group (moves + trash restores)")
    p.add_argument("n", nargs="?", type=int, default=1)
    p.add_argument("--tag", help="undo every move/trash recorded under this tag (e.g. a rebuild name)")
    p.set_defaults(fn=cmd_undo)

    p = sp.add_parser("rebuild", help="restructure an existing project from a reviewed plan: --survey → --check → --apply")
    p.add_argument("plan", nargs="?", help="plan file (tab-separated) from --survey, edited by you")
    p.add_argument("--survey", nargs="?", const="-", metavar="FILE", help="scan the top level and write a draft plan")
    p.add_argument("--check", action="store_true", help="validate the plan and report reference counts; change nothing")
    p.add_argument("--apply", action="store_true", help="execute pending rows (needs a baseline); resumable")
    p.add_argument("--name", help="migration tag (default: plan file stem)")
    p.add_argument("--freeze", action="store_true", help="with --apply: take a baseline first if none exists")
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_rebuild)

    p = sp.add_parser("freeze", help="write a full manifest (path, size, mtime, sha256) of the tree")
    p.add_argument("--name", help="baseline name (default: today)")
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_freeze)

    p = sp.add_parser("commit", help="book hand edits: run the checks, stage the allowed region, one commit")
    p.add_argument("-m", "--message")
    p.set_defaults(fn=cmd_commit)

    p = sp.add_parser("log", help="timeline of a block ID or a path (commits + ledger rows); no target = recent commits")
    p.add_argument("target", nargs="?")
    p.add_argument("-n", type=int, default=20)
    p.set_defaults(fn=cmd_log)

    p = sp.add_parser("plan", help="forward planning: roadmap, per-directory expected outputs, or register a planned block")
    p.add_argument("target", nargs="?", help="ID or path to show (and extend with --expect)")
    p.add_argument("--new", metavar="PATH", help="register a planned block for a directory that does not exist yet")
    p.add_argument("--expect", action="append", metavar="PATTERN", help="expected output pattern, relative to the directory; <subj> and {01..10} expand")
    p.add_argument("--inputs", nargs="*", metavar="ID", help="with --new: input block IDs")
    p.add_argument("--desc")
    p.add_argument("--role", choices=sorted(set(ROLE_OF.values())))
    p.add_argument("--layer", choices=DEFAULTS["layout"]["layers"])
    p.set_defaults(fn=cmd_plan)

    p = sp.add_parser("install", help="copy the code files (not the registry) into another project")
    p.add_argument("project", help="root of the other project")
    p.add_argument("--force", action="store_true", help="overwrite an existing copy (upgrade)")
    p.set_defaults(fn=cmd_install)

    p = sp.add_parser("verify", help="check the tree against a baseline through PATHMAP and DELETED")
    p.add_argument("--baseline", help="baseline name (default: latest)")
    p.add_argument("--full", action="store_true", help="recompute sha256 (slow); default compares size")
    p.add_argument("--mtime", action="store_true", help="with size, also require identical mtime")
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--report", help="write all problems to this file")
    p.set_defaults(fn=cmd_verify)

    a = ap.parse_args(argv)
    if a.cmd not in ("init", "install") and not (DM / "config.toml").exists():
        die(f"{rel(DM)}/config.toml not found; run DocsManagement init")
    a.fn(a)


if __name__ == "__main__":
    main()
