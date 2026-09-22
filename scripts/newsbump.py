#!/usr/bin/env python3
"""Turn news fragments into a version, a tag, and release notes.

A **fragment** is a file in the news directory whose *extension* is the bump it asks
for: ``news/adds-a-flag.minor`` asks for a minor bump, and its text is one line of
release notes. A pull request adds at least one; merging spends them.

The rules, all of them:

* the bump for a release is the **largest** kind present, so one ``.major`` in a batch
  of ``.fix`` fragments still releases a major;
* the version is computed from the **latest tag**, never from the package file. Tags are
  the record of what was released; a version written in a file is a claim about it, and
  the two drift. (They already had, in the repositories this was written for: every
  package said 0.1.0 while the tags were several releases ahead.) Writing the computed
  version back into the file is what repairs that drift on the first release;
* releasing **deletes** the fragments it spent, so the next release starts empty and the
  workflow that runs on every push to the default branch is a no-op until someone adds
  one.

This module is plain Python with no dependencies so it can be unit-tested away from a
runner; the workflows are thin wrappers around the commands here.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

#: The kinds, weakest first. The extension is the kind, so this is also the set of
#: extensions a fragment may have.
KINDS = ("fix", "minor", "major")

#: A version as this tool writes it: three numbers, nothing else. A prefix (``v``) is a
#: tag-formatting question and is handled where tags are read and written, never here.
VERSION = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")

#: The ``version = "..."`` line of a PEP 621 ``[project]`` table. Matched rather than
#: round-tripped through a TOML writer: this edits one line and leaves every comment,
#: string style and blank line in the file exactly as the author wrote them.
PROJECT_VERSION = re.compile(
    r'(?P<head>^\[project\]$.*?^version\s*=\s*)(?P<quote>["\'])(?P<version>[^"\']+)(?P=quote)',
    re.MULTILINE | re.DOTALL,
)


@dataclass(frozen=True)
class Fragment:
    """One news file: where it is, what bump it asks for, what it says."""

    path: Path
    kind: str
    text: str


def read_fragments(news_dir: Path) -> list[Fragment]:
    """Every valid fragment in ``news_dir``, sorted by name for stable notes.

    Files whose extension is not a kind are ignored rather than refused: a README or a
    ``.gitkeep`` has to be able to live in the directory. Validation of what a *pull
    request* added is a separate job — see ``check`` — and it is strict.
    """
    if not news_dir.is_dir():
        return []
    found = []
    for path in sorted(news_dir.iterdir()):
        kind = path.suffix.lstrip(".")
        if path.is_file() and kind in KINDS:
            found.append(Fragment(path=path, kind=kind, text=path.read_text().strip()))
    return found


def bump_for(fragments: list[Fragment]) -> str | None:
    """The kind to release: the largest present, or None when there is nothing to do."""
    kinds = {fragment.kind for fragment in fragments}
    for kind in reversed(KINDS):
        if kind in kinds:
            return kind
    return None


def next_version(current: str, kind: str, *, zero_major: str = "minor") -> str:
    """The version after ``current`` for a bump of ``kind``.

    ``zero_major`` decides what ``major`` means before 1.0.0, where semver says the
    public API is not stable yet and every release may break:

    * ``"minor"`` (the default) — ``0.4.0`` becomes ``0.5.0``. Reaching 1.0.0 stays a
      deliberate act by a person, not something a merge can do by accident.
    * ``"major"`` — strict arithmetic: ``0.4.0`` becomes ``1.0.0``.
    """
    match = VERSION.match(current)
    if match is None:
        raise ValueError(f"not a version this tool can bump: {current!r}")
    major, minor, fix = (int(part) for part in match.groups())

    if kind == "fix":
        return f"{major}.{minor}.{fix + 1}"
    if kind == "minor":
        return f"{major}.{minor + 1}.0"
    if kind == "major":
        if major == 0 and zero_major == "minor":
            return f"{major}.{minor + 1}.0"
        return f"{major + 1}.0.0"
    raise ValueError(f"unknown kind {kind!r}; use one of {', '.join(KINDS)}")


def notes(fragments: list[Fragment], version: str) -> str:
    """The release body: every fragment's text, grouped by kind, largest first."""
    lines = [f"## {version}", ""]
    headings = {"major": "Breaking", "minor": "Added", "fix": "Fixed"}
    for kind in reversed(KINDS):
        chosen = [fragment for fragment in fragments if fragment.kind == kind]
        if not chosen:
            continue
        lines.append(f"### {headings[kind]}")
        for fragment in chosen:
            # A fragment may be several lines; the first is the summary and the rest are
            # indented under it, so a long note does not break the bullet list.
            head, *rest = fragment.text.splitlines() or [""]
            lines.append(f"- {head.strip()}")
            lines.extend(f"  {line.strip()}" for line in rest if line.strip())
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def latest_tag(prefix: str = "") -> str | None:
    """The highest released version, read from tags. None when nothing is tagged yet.

    Tags that are not versions are skipped, so a repository can carry other tags without
    confusing this. Sorting is numeric (``sort -V`` semantics via ``git``'s own version
    sort) rather than lexical, or 0.10.0 would lose to 0.9.0.
    """
    out = subprocess.run(
        ["git", "tag", "--list", f"{prefix}*", "--sort=-v:refname"],
        capture_output=True, text=True, check=True,
    ).stdout.split()
    for tag in out:
        if VERSION.match(tag.removeprefix(prefix)):
            return tag
    return None


def read_project_version(pyproject: Path) -> str | None:
    """The ``[project] version`` in a pyproject file, or None if it has none."""
    if not pyproject.is_file():
        return None
    data = tomllib.loads(pyproject.read_text())
    version = data.get("project", {}).get("version")
    return str(version) if version is not None else None


def write_project_version(pyproject: Path, version: str) -> bool:
    """Write ``version`` into the ``[project]`` table. True when the file changed.

    Returns False rather than raising when there is no version to write: a repository
    that is not a Python package (a workspace, a docs repo) still gets tags and
    releases, and that is a legitimate way to use this.
    """
    if not pyproject.is_file():
        return False
    text = pyproject.read_text()
    match = PROJECT_VERSION.search(text)
    if match is None:
        return False
    if match.group("version") == version:
        return False
    start, end = match.span("version")
    pyproject.write_text(text[:start] + version + text[end:])
    return True


def added_files(base_ref: str, news_dir: Path) -> list[Path]:
    """Files added under ``news_dir`` relative to ``base_ref``.

    Added, not modified: editing an existing fragment is not contributing one. The
    three-dot range asks what this branch added since it diverged, so a base branch that
    moved on underneath does not count as this branch's work.
    """
    out = subprocess.run(
        ["git", "diff", "--name-only", "--diff-filter=A", f"{base_ref}...HEAD", "--", str(news_dir)],
        capture_output=True, text=True, check=True,
    ).stdout.split()
    return [Path(name) for name in out]


def check(base_ref: str, news_dir: Path) -> list[str]:
    """Problems with this branch's news, as messages. Empty means it is fine."""
    added = added_files(base_ref, news_dir)
    fragments = [path for path in added if path.suffix.lstrip(".") in KINDS]

    if not fragments:
        problems = [
            f"this branch adds no news fragment under {news_dir}/.",
            "Add one file per change, named <summary>.<kind> where <kind> is "
            + ", ".join(KINDS) + ".",
            f'For example: echo "What this changes, in one line." > '
            f"{news_dir}/short-summary.minor",
        ]
        # A file added there that is not a fragment is a likely typo when it is the only
        # thing added — say so, rather than leaving someone to spot `.patch` by eye.
        for path in added:
            problems.append(f"({path} is not a fragment: {path.suffix or 'no extension'})")
        return problems

    # Anything else added alongside them is not claiming to be a fragment: a README
    # explaining the convention, a .gitkeep holding the directory. `read_fragments`
    # ignores those, and so does this — refusing them would refuse this tool's own
    # documented layout.
    return [
        f"{path}: empty. The text is the release note, so write one line."
        for path in fragments
        if not path.is_file() or not path.read_text().strip()
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--news-dir", type=Path, default=Path("news"))
    sub = parser.add_subparsers(dest="command", required=True)

    checker = sub.add_parser("check", help="does this branch add a valid fragment?")
    checker.add_argument("--base-ref", required=True, help="the branch this one merges into")

    planner = sub.add_parser("plan", help="print the next version, or nothing to release")
    planner.add_argument("--tag-prefix", default="")
    planner.add_argument("--zero-major", choices=("minor", "major"), default="minor")

    applier = sub.add_parser("apply", help="write the version and the notes, spend the fragments")
    applier.add_argument("--version", required=True)
    applier.add_argument("--pyproject", type=Path, default=Path("pyproject.toml"))
    applier.add_argument("--notes-out", type=Path, default=Path("RELEASE_NOTES.md"))

    args = parser.parse_args(argv)

    if args.command == "check":
        problems = check(args.base_ref, args.news_dir)
        for problem in problems:
            print(problem, file=sys.stderr)
        return 1 if problems else 0

    if args.command == "plan":
        fragments = read_fragments(args.news_dir)
        kind = bump_for(fragments)
        if kind is None:
            return 0                       # nothing to release; the caller sees no output
        current = latest_tag(args.tag_prefix)
        base = current.removeprefix(args.tag_prefix) if current else "0.0.0"
        print(next_version(base, kind, zero_major=args.zero_major))
        return 0

    fragments = read_fragments(args.news_dir)
    args.notes_out.write_text(notes(fragments, args.version))
    changed = write_project_version(args.pyproject, args.version)
    print(f"version file {'updated' if changed else 'unchanged (no [project] version)'}")
    for fragment in fragments:
        fragment.path.unlink()
    print(f"spent {len(fragments)} fragment(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
