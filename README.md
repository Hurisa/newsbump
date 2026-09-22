# newsbump

Release a Python package from the notes its pull requests wrote.

Each pull request adds one small file saying what it changed. The file's **extension** is
the size of the change:

```
news/reset-button.minor      "Add a button that puts the scene back."
news/blank-canvas.fix        "Stop the canvas blanking when a request is refused."
news/rename-the-port.major   "Rename generate() to propose(); callers must be updated."
```

A check on every pull request fails when the branch adds none, so the note is written
while the change is fresh, by the person who made it — not reconstructed from commit
subjects months later.

When the pull request merges, a workflow on the default branch reads the fragments, takes
the **largest** bump among them, computes the next version from the last tag, writes it
into `pyproject.toml`, deletes the fragments, commits, tags, and publishes a release whose
notes are the fragments themselves.

```
0.4.0  +  one .minor and two .fix   ->  0.5.0, tagged, released, news/ empty again
```

## Use it

Two files, copied from [`examples/`](examples/) into the repository being released:

```yaml
# .github/workflows/pull-request.yml
on:
  pull_request:
    types: [opened, synchronize, reopened, labeled, unlabeled]
jobs:
  news:
    uses: OWNER/newsbump/.github/workflows/news-check.yml@main
```

```yaml
# .github/workflows/release.yml
on:
  push:
    branches: [main]
jobs:
  release:
    permissions:
      contents: write
    uses: OWNER/newsbump/.github/workflows/release.yml@main
```

Then make the directory and let the first pull request add the first fragment:

```bash
mkdir news && touch news/.gitkeep
echo "What this changes, in one line." > news/short-summary.minor
```

## Settings

| Input | Default | What it does |
|---|---|---|
| `news-dir` | `news` | where fragments live |
| `pyproject` | `pyproject.toml` | the file whose `[project] version` is rewritten |
| `tag-prefix` | `""` | bare `1.2.3` tags; set `v` for `v1.2.3` |
| `zero-major` | `minor` | what `.major` means below 1.0.0 (see below) |
| `skip-label` | `skip-news` | a pull-request label that waives the check |
| `python-version` | `3.12` | the runner's Python |

## Decisions worth knowing before you adopt it

**Tags are the record; the version in the file is a copy of it.** The next version is
computed from the last tag, never from `pyproject.toml`. That is the only ordering that
survives a file being edited by hand, and it means the first release repairs a version
that had drifted.

**`.major` below 1.0.0 is a minor bump by default.** Semver says 0.x has no stable API and
every release may break, so a breaking change there is ordinary. Reaching 1.0.0 is a claim
about maturity that a merge should not make on your behalf — set `zero-major: major` if
you disagree, and it becomes strict arithmetic.

**Nothing to release is not an error.** The release workflow runs on every push to the
default branch and exits quietly when `news/` is empty. That is also why it does not react
to its own release commit.

**One fragment per change, not per pull request.** A pull request that does three things
adds three fragments and gets three bullets. The bump is the largest of them.

**Editing an existing fragment is not contributing one.** The check asks what the branch
*added* since it left the base branch, so an unreleased fragment someone else wrote does
not count for you, and a base branch that moved on does not count against you.

**It does not run your tests.** This publishes what merged; whether what merged is good is
a separate workflow's job, and keeping them apart means a flaky test suite cannot leave
the version and the tag disagreeing.

## What it does not do

No changelog file is assembled or committed: the notes live on the tag and the release,
which is where a reader looks. No packages are published to an index. No other repository
is notified. Each of those is a deliberate omission — add them in the repository that
needs them, not here.

## Working on newsbump itself

```bash
python -m pytest tests -q
```

The rules live in [`scripts/newsbump.py`](scripts/newsbump.py) as plain functions with no
dependencies, so they are tested away from a runner: the version arithmetic, the note
assembly, the one-line edit of a version file, and the git questions, against a real
repository in a temporary directory. The workflows are thin wrappers around those
commands.

This repository releases itself with its own workflows.
