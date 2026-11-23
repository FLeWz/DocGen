"""Bare-repo access via the git CLI. Fixed argv lists, no shell. Paths come from UUIDs only;
revs are validated (hex sha or tag name) so no user string can become an option or a path."""
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from uuid import UUID

from .. import config

ROOT = Path(config.REPOS_ROOT).resolve()
HEX = re.compile(r"[0-9a-f]{7,40}")
TAG = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
ZERO = "0" * 40


class GitError(Exception):
    pass


class NotFound(GitError):
    pass


def repo_path(org_id, folder_id) -> Path:
    p = (ROOT / str(UUID(str(org_id))) / f"{UUID(str(folder_id))}.git").resolve()
    assert p.is_relative_to(ROOT) and p != ROOT
    return p


def _git(repo, *args, input=None, env=None) -> bytes:
    e = {"PATH": os.environ["PATH"], "HOME": "/tmp", "GIT_CONFIG_NOSYSTEM": "1",
         "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_TERMINAL_PROMPT": "0", **(env or {})}
    r = subprocess.run(["git", "--git-dir", str(repo), *args], input=input, capture_output=True, env=e, timeout=30)
    if r.returncode:
        raise GitError(r.stderr.decode(errors="replace").strip())
    return r.stdout


def init(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(["git", "init", "--bare", "--quiet", "--initial-branch=main", str(path)],
                       capture_output=True, env={"PATH": os.environ["PATH"], "HOME": "/tmp",
                                                 "GIT_CONFIG_GLOBAL": "/dev/null"}, timeout=30)
    if r.returncode:
        raise GitError(r.stderr.decode(errors="replace"))


def remove(path: Path) -> None:
    path = path.resolve()
    assert path.is_relative_to(ROOT) and path != ROOT
    shutil.rmtree(path, ignore_errors=True)


def remove_org(org_id) -> None:
    remove(ROOT / str(UUID(str(org_id))))


def head(repo) -> str | None:
    try:
        return _git(repo, "rev-parse", "--verify", "--quiet", "HEAD").decode().strip()
    except GitError:
        return None  # unborn


def resolve(repo, rev: str) -> str:
    """sha-or-tag -> full commit sha; NotFound for anything else."""
    if HEX.fullmatch(rev):
        ref = rev
    elif TAG.fullmatch(rev):
        ref = f"refs/tags/{rev}"
    else:
        raise NotFound(rev)
    try:
        return _git(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}").decode().strip()
    except GitError:
        raise NotFound(rev)


def show(repo, sha: str, path: str) -> bytes | None:
    try:
        return _git(repo, "cat-file", "blob", f"{sha}:{path}")
    except GitError:
        return None


def slug_at(repo, sha: str, snippet_id) -> str | None:
    """A snippet's dir name at a commit (slugs change; the uuid in meta.yaml does not)."""
    try:
        out = _git(repo, "grep", "-l", "-E", "-e", f"^id: {UUID(str(snippet_id))}$", sha,
                   "--", "snippets/*/meta.yaml").decode().split()
    except GitError:
        return None
    return out[0].split(":", 1)[1].split("/")[1] if out else None


def commit(repo, name: str, email: str, message: str, writes: dict[str, bytes],
           moves: list[tuple[str, str]] = ()) -> str:
    """One commit on main: apply moves (dir old->new), then writes, via a throwaway index."""
    parent = head(repo)
    with tempfile.TemporaryDirectory() as td:
        env = {"GIT_INDEX_FILE": f"{td}/index"}
        if parent:
            _git(repo, "read-tree", parent, env=env)
        lines = []
        for old, new in moves:
            for ent in _git(repo, "ls-files", "-s", "--", old + "/", env=env).decode().splitlines():
                meta, path = ent.split("\t", 1)
                mode, sha, _ = meta.split()
                lines += [f"0 {ZERO}\t{path}", f"{mode} {sha}\t{new}/{path[len(old) + 1:]}"]
        for path, data in writes.items():
            sha = _git(repo, "hash-object", "-w", "--stdin", input=data).decode().strip()
            lines.append(f"100644 {sha}\t{path}")
        _git(repo, "update-index", "--index-info", input=("\n".join(lines) + "\n").encode(), env=env)
        tree = _git(repo, "write-tree", env=env).decode().strip()
    ident = {"GIT_AUTHOR_NAME": name, "GIT_AUTHOR_EMAIL": email,
             "GIT_COMMITTER_NAME": name, "GIT_COMMITTER_EMAIL": email}
    args = ["commit-tree", tree, *(["-p", parent] if parent else []), "-m", message]
    new = _git(repo, *args, env=ident).decode().strip()
    _git(repo, "update-ref", "HEAD", new, parent or ZERO)  # fails if HEAD moved meanwhile
    return new


def log(repo, snippet_id=None, limit=200) -> list[dict]:
    extra = [f"--grep=Snippet-Id: {UUID(str(snippet_id))}", "--fixed-strings"] if snippet_id else []
    if not head(repo):
        return []
    out = _git(repo, "log", f"-n{limit}", "--format=%H%x1f%an%x1f%ae%x1f%aI%x1f%B%x1e", *extra).decode()
    rows = []
    for rec in out.split("\x1e"):
        if rec.strip():
            sha, an, ae, date, body = rec.strip("\n").split("\x1f", 4)
            rows.append(dict(sha=sha, author_name=an, author_email=ae, date=date, body=body))
    return rows


def tags(repo) -> list[dict]:
    out = _git(repo, "for-each-ref", "--format=%(refname:short) %(objectname)", "refs/tags").decode()
    return [dict(zip(("name", "sha"), l.split(" "))) for l in out.splitlines()]


def tag_add(repo, name: str, sha: str) -> None:
    if not TAG.fullmatch(name):
        raise GitError("invalid tag name")
    _git(repo, "tag", name, sha)


def tag_delete(repo, name: str) -> None:
    if not TAG.fullmatch(name) or name not in {t["name"] for t in tags(repo)}:
        raise NotFound(name)
    _git(repo, "tag", "-d", name)
