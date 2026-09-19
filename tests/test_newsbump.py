"""The rules, tested away from a runner: no git, no network, no GitHub.

Everything here is a pure function over a temporary directory. The workflows are thin
wrappers, so what is worth testing is the arithmetic, the note assembly, and the two
places a mistake would be expensive: writing a version into a file that has comments in
it, and deciding whether a branch contributed a fragment.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import newsbump  # noqa: E402


# ------------------------------------------------------------------ version arithmetic


@pytest.mark.parametrize(
    ("current", "kind", "expected"),
    [
        ("1.2.3", "fix", "1.2.4"),
        ("1.2.3", "minor", "1.3.0"),
        ("1.2.3", "major", "2.0.0"),
        ("0.9.9", "fix", "0.9.10"),
        ("0.9.9", "minor", "0.10.0"),
    ],
)
def test_the_arithmetic(current: str, kind: str, expected: str) -> None:
    assert newsbump.next_version(current, kind) == expected


def test_a_major_before_one_point_oh_is_a_minor_by_default() -> None:
    """Semver says 0.x has no stable API, so everything may break. Reaching 1.0.0 is a
    person's decision about maturity, not something a merged pull request should do."""
    assert newsbump.next_version("0.4.0", "major") == "0.5.0"


def test_a_major_before_one_point_oh_can_be_strict_instead() -> None:
    assert newsbump.next_version("0.4.0", "major", zero_major="major") == "1.0.0"


def test_one_point_oh_onwards_is_always_strict() -> None:
    assert newsbump.next_version("1.4.0", "major", zero_major="minor") == "2.0.0"


def test_a_version_it_cannot_bump_is_refused() -> None:
    for bad in ("1.2", "v1.2.3", "1.2.3-rc1", ""):
        with pytest.raises(ValueError):
            newsbump.next_version(bad, "fix")


def test_an_unknown_kind_is_refused() -> None:
    with pytest.raises(ValueError):
        newsbump.next_version("1.2.3", "patch")


# ------------------------------------------------------------------------- fragments


def write(directory: Path, name: str, text: str = "a change") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(text)
    return path


def test_the_largest_kind_present_wins(tmp_path: Path) -> None:
    """One breaking change in a batch of fixes still releases a major."""
    news = tmp_path / "news"
    write(news, "a.fix")
    write(news, "b.major")
    write(news, "c.minor")
    assert newsbump.bump_for(newsbump.read_fragments(news)) == "major"


def test_nothing_to_release_is_not_an_error(tmp_path: Path) -> None:
    """The release workflow runs on every push to the default branch, so the ordinary
    case is a push with no fragments. That is a no-op, not a failure."""
    assert newsbump.bump_for(newsbump.read_fragments(tmp_path / "news")) is None


def test_a_readme_in_the_news_directory_is_not_a_fragment(tmp_path: Path) -> None:
    news = tmp_path / "news"
    write(news, "README.md", "how to write a fragment")
    write(news, ".gitkeep", "")
    assert newsbump.read_fragments(news) == []


def test_notes_group_by_kind_largest_first(tmp_path: Path) -> None:
    news = tmp_path / "news"
    write(news, "one.fix", "Stop the canvas blanking on a refusal.")
    write(news, "two.major", "Rename the port.")
    write(news, "three.minor", "Add a reset button.")
    body = newsbump.notes(newsbump.read_fragments(news), "1.0.0")

    assert body.splitlines()[0] == "## 1.0.0"
    assert body.index("Breaking") < body.index("Added") < body.index("Fixed")
    assert "- Rename the port." in body


def test_a_multi_line_fragment_keeps_its_shape(tmp_path: Path) -> None:
    news = tmp_path / "news"
    write(news, "long.minor", "Add a reset button.\nIt drops the last result.")
    body = newsbump.notes(newsbump.read_fragments(news), "0.2.0")
    assert "- Add a reset button.\n  It drops the last result." in body


# --------------------------------------------------------------------- the version file


PYPROJECT = '''\
[build-system]
requires = ["hatchling"]

[project]
name = "example"
# the version is written by the release workflow
version = "0.1.0"
dependencies = ["something>=2"]

[tool.example]
version = "not this one"
'''


def test_writing_a_version_touches_one_line(tmp_path: Path) -> None:
    """A TOML round-trip would reformat the file and drop its comments. The point of a
    targeted edit is that the diff of a release is one line."""
    path = tmp_path / "pyproject.toml"
    path.write_text(PYPROJECT)

    assert newsbump.write_project_version(path, "0.2.0") is True
    after = path.read_text()

    assert 'version = "0.2.0"' in after
    assert 'version = "not this one"' in after, "a version elsewhere is not the project's"
    assert "# the version is written by the release workflow" in after
    assert len(after.splitlines()) == len(PYPROJECT.splitlines())


def test_writing_the_version_it_already_has_changes_nothing(tmp_path: Path) -> None:
    path = tmp_path / "pyproject.toml"
    path.write_text(PYPROJECT)
    assert newsbump.write_project_version(path, "0.1.0") is False


def test_a_repository_that_is_not_a_package_still_releases(tmp_path: Path) -> None:
    """A workspace or a docs repository has no [project] version. It gets a tag and a
    release; there is simply no file to write."""
    assert newsbump.write_project_version(tmp_path / "pyproject.toml", "1.0.0") is False

    path = tmp_path / "pyproject.toml"
    path.write_text("[tool.ruff]\nline-length = 100\n")
    assert newsbump.write_project_version(path, "1.0.0") is False


def test_reading_the_declared_version(tmp_path: Path) -> None:
    path = tmp_path / "pyproject.toml"
    path.write_text(PYPROJECT)
    assert newsbump.read_project_version(path) == "0.1.0"


# ------------------------------------------------------------------- against real git


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A real repository: these functions shell out to git, so the test does too."""
    def run(*args: str) -> None:
        subprocess.run(args, cwd=tmp_path, check=True, capture_output=True)

    run("git", "init", "-q", "-b", "main")
    run("git", "config", "user.email", "test@example.invalid")
    run("git", "config", "user.name", "Test")
    (tmp_path / "README.md").write_text("start\n")
    run("git", "add", "-A")
    run("git", "commit", "-qm", "first")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def test_the_latest_tag_is_the_highest_not_the_newest(repo: Path) -> None:
    """Lexical sorting would put 0.9.0 above 0.10.0, and the next release would go
    backwards."""
    for tag in ("0.9.0", "0.10.0", "0.2.0"):
        git(repo, "tag", tag)
    assert newsbump.latest_tag() == "0.10.0"


def test_tags_that_are_not_versions_are_ignored(repo: Path) -> None:
    git(repo, "tag", "nightly")
    git(repo, "tag", "0.1.0")
    assert newsbump.latest_tag() == "0.1.0"


def test_no_tags_yet_means_no_latest(repo: Path) -> None:
    assert newsbump.latest_tag() is None


def test_a_branch_that_adds_a_fragment_passes(repo: Path) -> None:
    git(repo, "switch", "-qc", "feature")
    write(repo / "news", "adds-a-thing.minor", "Add a thing.")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "work")
    assert newsbump.check("main", Path("news")) == []


def test_a_branch_with_no_fragment_is_told_how_to_add_one(repo: Path) -> None:
    git(repo, "switch", "-qc", "feature")
    (repo / "README.md").write_text("changed\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "work")

    problems = newsbump.check("main", Path("news"))
    assert problems and "adds no news fragment" in problems[0]
    assert any(".minor" in line for line in problems), "the message shows the shape"


def test_editing_someone_elses_fragment_is_not_contributing_one(repo: Path) -> None:
    write(repo / "news", "existing.fix", "An earlier change.")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "an unreleased fragment")

    git(repo, "switch", "-qc", "feature")
    (repo / "news" / "existing.fix").write_text("An earlier change, reworded.")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "work")

    assert newsbump.check("main", Path("news")), "modifying is not adding"


def test_a_fragment_with_a_bad_extension_is_named(repo: Path) -> None:
    git(repo, "switch", "-qc", "feature")
    write(repo / "news", "oops.patch", "Fix a thing.")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "work")

    problems = newsbump.check("main", Path("news"))
    assert any("patch" in problem and "not a kind" in problem for problem in problems)


def test_an_empty_fragment_is_refused(repo: Path) -> None:
    """The text is the release note. An empty file releases a version nobody can read."""
    git(repo, "switch", "-qc", "feature")
    write(repo / "news", "silent.fix", "   \n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "work")

    problems = newsbump.check("main", Path("news"))
    assert any("empty" in problem for problem in problems)


def test_a_base_branch_that_moved_on_does_not_count_against_the_feature(repo: Path) -> None:
    """Three-dot: what this branch added since it diverged. Two-dot would credit the
    branch with a fragment someone else merged into main in the meantime."""
    git(repo, "switch", "-qc", "feature")
    (repo / "README.md").write_text("feature work\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "work")

    git(repo, "switch", "-q", "main")
    write(repo / "news", "someone-else.minor", "Their change.")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "their work")
    git(repo, "switch", "-q", "feature")

    assert newsbump.check("main", Path("news")), "their fragment is not this branch's"


# ------------------------------------------------------------------------ the commands


def test_plan_prints_the_next_version(repo: Path, capsys: pytest.CaptureFixture) -> None:
    git(repo, "tag", "0.4.0")
    write(repo / "news", "a.minor", "Add a thing.")
    assert newsbump.main(["plan"]) == 0
    assert capsys.readouterr().out.strip() == "0.5.0"


def test_plan_prints_nothing_when_there_is_nothing_to_release(
    repo: Path, capsys: pytest.CaptureFixture
) -> None:
    git(repo, "tag", "0.4.0")
    assert newsbump.main(["plan"]) == 0
    assert capsys.readouterr().out.strip() == ""


def test_plan_starts_at_the_first_version_when_nothing_is_tagged(
    repo: Path, capsys: pytest.CaptureFixture
) -> None:
    write(repo / "news", "a.minor", "The first release.")
    assert newsbump.main(["plan"]) == 0
    assert capsys.readouterr().out.strip() == "0.1.0"


def test_apply_writes_the_notes_and_spends_the_fragments(repo: Path) -> None:
    (repo / "pyproject.toml").write_text(PYPROJECT)
    write(repo / "news", "a.minor", "Add a thing.")
    write(repo / "news", "b.fix", "Fix a thing.")

    assert newsbump.main(["apply", "--version", "0.2.0"]) == 0

    assert 'version = "0.2.0"' in (repo / "pyproject.toml").read_text()
    body = (repo / "RELEASE_NOTES.md").read_text()
    assert "- Add a thing." in body and "- Fix a thing." in body
    assert list((repo / "news").glob("*.minor")) == [], "spent"
    assert list((repo / "news").glob("*.fix")) == [], "spent"


def test_check_exits_non_zero_so_the_job_fails(repo: Path) -> None:
    git(repo, "switch", "-qc", "feature")
    (repo / "README.md").write_text("changed\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "work")
    assert newsbump.main(["check", "--base-ref", "main"]) == 1
