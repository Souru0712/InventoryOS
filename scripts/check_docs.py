"""Check that the product, audit and roadmap documents stay mutually consistent; exit 1 on any problem."""

from __future__ import annotations

import collections
import re
import sys
from itertools import pairwise
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRODUCT, AUDIT, ROADMAP = ROOT / "docs/product", ROOT / "docs/audit", ROOT / "docs/roadmap"
TASK = r"\bT(?:\d|4B)\.\d{2}\b"
FR = r"[A-Z]{3}-FR-\d+[a-z]?"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def slug(heading: str) -> str:
    return re.sub(r"[^\w\- ]", "", heading.strip().lower()).replace(" ", "-")


def anchors(path: Path) -> set[str]:
    return {slug(m.group(1)) for m in re.finditer(r"^#+\s+(.*)$", read(path), re.M)}


def check_links(folders: list[Path]) -> list[str]:
    problems = []
    for folder in folders:
        for doc in sorted(folder.glob("*.md")):
            for target in re.findall(r"\]\(([^)\s]+)\)", read(doc)):
                if target.startswith(("http", "mailto:", "#terminal")):
                    continue
                path, _, frag = target.partition("#")
                dest = (doc.parent / path).resolve() if path else doc.resolve()
                if not dest.exists():
                    problems.append(f"{doc.name}: missing link target {target}")
                elif frag and dest.suffix == ".md" and frag not in anchors(dest):
                    problems.append(f"{doc.name}: missing anchor {target}")
    return problems


PRODUCT_ID = (
    r"\b(?:PD|DR|AC-[A-Z]+|A|C|E|WF|SEC|REL|PERF|SCL|UX|MNT|OBS|SAAS-[A-Z]+|[A-Z]{3}-FR)"
    r"-\d+[a-z]?\b"
)


def check_product_ids() -> list[str]:
    defined, referenced = set(), {}
    for doc in sorted(PRODUCT.glob("*.md")):
        text = read(doc)
        for line in text.splitlines():
            m = re.match(r"^\|\s*\**(" + PRODUCT_ID + r")\**\s*\|", line) or re.match(
                r"^#+\s+(" + PRODUCT_ID + r")\b", line
            )
            if m:
                defined.add(m.group(1))
        for ref in re.findall(PRODUCT_ID, text):
            referenced.setdefault(ref, doc.name)
    return [
        f"product: undefined id {r} (first in {d})"
        for r, d in sorted(referenced.items())
        if r not in defined
    ]


def spec_releases() -> dict[str, str]:
    out = {}
    for line in read(PRODUCT / "PRODUCT_SPECIFICATION.md").splitlines():
        m = re.match(r"^\|\s*(" + FR + r")\s*\|", line)
        if m:
            out[m.group(1)] = [c.strip() for c in line.split("|")][-3]
    return out


def check_traceability() -> list[str]:
    spec = set(spec_releases())
    section = read(AUDIT / "INVENTORYOS_REQUIREMENTS_TRACEABILITY.md").split("## 2.")[0]
    seen = collections.Counter()
    for line in section.splitlines():
        if re.match(r"^\|\s*\d+\s*\|", line):
            seen.update(re.findall(FR, [c.strip() for c in line.split("|")][3]))
    problems = [f"traceability: spec requirement {r} missing" for r in sorted(spec - set(seen))]
    problems += [f"traceability: unknown requirement {r}" for r in sorted(set(seen) - spec)]
    problems += [f"traceability: {r} listed {n} times" for r, n in sorted(seen.items()) if n > 1]
    return problems


def task_index(breakdown: str) -> tuple[list[str], dict[str, list[str]], dict[str, str]]:
    lines = breakdown.split("## Index")[1].split("\n---\n")[0].splitlines()
    header = [
        c.strip() for c in next(line for line in lines if line.startswith("| ID |")).split("|")
    ]
    dcol, mcol = header.index("Depends on"), header.index("Milestone")
    order, deps, milestones = [], {}, {}
    for line in lines:
        m = re.match(r"^\| (T\d\.\d+) \|", line)
        if m:
            cells = [c.strip() for c in line.split("|")]
            order.append(m.group(1))
            deps[m.group(1)] = re.findall(TASK, cells[dcol])
            milestones[m.group(1)] = cells[mcol]
    return order, deps, milestones


def ancestors(task: str, deps: dict[str, list[str]], seen: set[str] | None = None) -> set[str]:
    seen = set() if seen is None else seen
    for d in deps.get(task, []):
        if d not in seen:
            seen.add(d)
            ancestors(d, deps, seen)
    return seen


def task_section(breakdown: str, task: str) -> str:
    start = breakdown.find(f"### {task} ")
    if start < 0:
        return ""
    ends = [
        i for i in (breakdown.find("\n### ", start + 4), breakdown.find("\n---\n", start)) if i > 0
    ]
    return breakdown[start : min(ends) if ends else len(breakdown)]


def check_roadmap_references(
    breakdown: str, order: list[str], deps: dict[str, list[str]]
) -> list[str]:
    docs = {p.name: read(p) for p in sorted(ROADMAP.glob("*.md"))}
    docs["CLAUDE.md"] = read(ROOT / "CLAUDE.md")
    defined_tasks = set(re.findall(r"^### (T\d[B]?\.\d+)", breakdown, re.M))
    defined_tasks |= set(re.findall(r"^\| (T\d[B]?\.\d+) \|", breakdown, re.M))
    decisions = set(re.findall(r"^\| (ID-\d+) \|", docs["IMPLEMENTATION_DECISIONS.md"], re.M))
    product_text = "\n".join(read(p) for p in PRODUCT.glob("*.md"))
    req = (
        r"\b(?:AC-[A-Z]+-\d+|DR-\d+|" + FR + r"|WF-\d+|PD-\d+|C-\d+|SAAS-[A-Z]-\d+|SEC-\d+|REL-\d+|"
        r"PERF-\d+|SCL-\d+|OBS-\d+|MNT-\d+|UX-\d+)\b"
    )
    product_ids = set(re.findall(req, product_text))
    problems = []
    for name, text in docs.items():
        problems += [
            f"{name}: undefined task {t}"
            for t in sorted(set(re.findall(TASK, text)) - defined_tasks)
        ]
        problems += [
            f"{name}: undefined decision {d}"
            for d in sorted(set(re.findall(r"\bID-\d+\b", text)) - decisions)
        ]
        problems += [
            f"{name}: requirement {r} not in docs/product"
            for r in sorted(set(re.findall(req, text)) - product_ids)
        ]
    pos = {t: i for i, t in enumerate(order)}
    for task, ds in deps.items():
        for d in ds:
            if d not in pos:
                problems.append(f"index: {task} depends on unknown {d}")
            elif pos[d] >= pos[task]:
                problems.append(f"index: {task} depends on later task {d}")
        line = re.search(r"\*\*Depends on:\*\* (.*)", task_section(breakdown, task))
        if line and set(re.findall(TASK, line.group(1))) != set(ds):
            problems.append(f"{task}: 'Depends on' differs between its entry and the index")
    return problems


def expand(spec: str) -> list[str]:
    out = []
    for part in re.split(r",\s*", spec):
        m = re.match(r"(T\d)\.(\d+)[–-](?:T\d\.)?(\d+)", part.strip())
        if m:
            out += [f"{m.group(1)}.{i:02d}" for i in range(int(m.group(2)), int(m.group(3)) + 1)]
        else:
            out += re.findall(TASK, part)
    return out


def milestone_ranges(master: str) -> dict[str, list[str]]:
    ranges = {
        m.group(1): expand(m.group(2).split(":")[0])
        for m in re.finditer(r'Milestone (M\d) "[^"]+" \| ([^|]+)\|', master)
    }
    for ms, stage in (("M0", r"### Stage 0"), ("M5", r"### Stage 3"), ("M6", r"### Stage 4 ")):
        m = re.search(stage + r".*?\| Tasks \| (T\d\.\d+[–-]T\d\.\d+) \|", master, re.S)
        if m:
            ranges[ms] = expand(m.group(1))
    return ranges


def check_alignment(
    breakdown: str, deps: dict[str, list[str]], milestones: dict[str, str]
) -> list[str]:
    master = read(ROADMAP / "MASTER_DEVELOPMENT_ROADMAP.md")
    migration = read(ROADMAP / "DATA_MIGRATION_ROADMAP.md")
    problems = []
    ranges = milestone_ranges(master)
    where = {t: ms for ms, ts in ranges.items() for t in ts}
    order = ["M0", "M1", "M2", "M3", "M4", "M5", "M6"]
    for task in deps:
        if task not in where:
            problems.append(f"master: {task} is in no milestone range")
        elif milestones.get(task) != where[task]:
            problems.append(
                f"index: {task} milestone {milestones.get(task)} but master says {where[task]}"
            )
    for task in where:
        if task not in deps:
            problems.append(f"master: milestone range names unknown task {task}")
    for task, ds in deps.items():
        for d in ds:
            if task in where and d in where and order.index(where[d]) > order.index(where[task]):
                problems.append(
                    f"{task} ({where[task]}) depends on {d} in later milestone {where[d]}"
                )
    critical = master.split("## 4. Critical path")[1].split("## 5")[0]
    for line in critical.splitlines():
        chain = re.findall(TASK, line)
        for a, b in pairwise(chain):
            if a not in ancestors(b, deps):
                problems.append(f"critical path: {a} -> {b} is not a dependency")
    for row in re.finditer(
        r"^\| (DM-\d+) \| [^|]+ \| ([^|]+) \| [^|]+ \| ([^|]+) \|", migration, re.M
    ):
        step, owners, precede = (
            row.group(1),
            re.findall(TASK, row.group(2)),
            re.findall(TASK, row.group(3)),
        )
        if not owners:
            problems.append(f"{step}: no owning task")
        for owner in owners:
            if owner not in deps:
                problems.append(f"{step}: unknown owning task {owner}")
                continue
            if not re.search(rf"\b{step}\b", task_section(breakdown, owner)):
                problems.append(f"{step}: owning task {owner} does not name it")
            for later in precede:
                if later in deps and owner not in ancestors(later, deps):
                    problems.append(f"{step}: {owner} is not a prerequisite of {later}")
    return problems


def main() -> int:
    breakdown = read(ROADMAP / "DEVELOPMENT_TASK_BREAKDOWN.md")
    order, deps, milestones = task_index(breakdown)
    problems = (
        check_links([PRODUCT, AUDIT, ROADMAP])
        + check_product_ids()
        + check_traceability()
        + check_roadmap_references(breakdown, order, deps)
        + check_alignment(breakdown, deps, milestones)
    )
    print(f"checked: {len(order)} indexed tasks, {len(spec_releases())} spec requirements")
    for p in problems:
        print(p.encode("ascii", "replace").decode())
    print(f"{len(problems)} problems")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
