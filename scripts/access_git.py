"""Local Git object identities and raw worktree bytes, without conversion filters."""

from __future__ import annotations

import hashlib
import os
import re
import stat
import subprocess
from collections.abc import Iterable
from pathlib import Path

if __package__:
    from .check_application_access import PlanError
else:
    from check_application_access import PlanError


def git(root: Path, *args: str) -> bytes:
    # These plumbing commands never convert worktree contents through attributes.
    if not args or args[0] not in ("rev-parse", "ls-tree", "ls-files"):
        raise PlanError("revision: unsupported Git operation")
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_SYSTEM=os.devnull,
               GIT_CONFIG_GLOBAL=os.devnull, GIT_ATTR_NOSYSTEM="1", GIT_ALLOW_PROTOCOL="",
               GIT_NO_LAZY_FETCH="1", GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0")
    try:
        return subprocess.run(
            ["git", "--no-pager", "--no-lazy-fetch", "--no-replace-objects", "--literal-pathspecs",
             "-c", "core.fsmonitor=false", "-c", "core.hooksPath=" + os.devnull,
             "-c", "core.untrackedCache=false", "-c", "protocol.allow=never",
             "-c", "maintenance.auto=false", "-c", "gc.auto=0", "-C", str(root), *args],
            env=env, check=True, capture_output=True, timeout=30,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        raise PlanError("revision: local Git objects unavailable; fetching is prohibited") from None


class GitSnapshot:
    """HEAD tree metadata plus byte comparisons; no status/diff/hash-object filters."""

    def __init__(self, root: Path):
        self.root = root.resolve()
        if Path(os.fsdecode(git(self.root, "rev-parse", "--show-toplevel").rstrip(b"\n"))).resolve() != self.root:
            raise PlanError("revision: supplied root must be a Git checkout root")
        self.sha = self.resolve("HEAD")
        self.entries = {}
        for record in git(self.root, "ls-tree", "-rz", "--full-tree", self.sha).split(b"\0"):
            if not record:
                continue
            header, name = record.split(b"\t", 1)
            mode, kind, oid = header.decode("ascii").split()
            name = os.fsdecode(name)
            if Path(name).is_absolute() or any(p in ("..", ".git") for p in Path(name).parts):
                raise PlanError("revision: unsupported committed path")
            self.entries[name] = (mode, kind, oid)

    def resolve(self, ref: str) -> str:
        sha = git(self.root, "rev-parse", "--verify", ref + "^{commit}").strip().decode("ascii")
        if not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", sha):
            raise PlanError("revision: invalid local Git identity")
        return sha

    def require(self, path: Path) -> tuple:
        try:
            relative = path.relative_to(self.root)
            entry = self.entries.get(relative.as_posix())
            if entry is None or entry[0] not in ("100644", "100755") or entry[1] != "blob":
                raise PlanError("revision: selected platform file is not a committed regular blob")
            for part in (path, *path.parents):
                if part == self.root:
                    break
                if part.is_symlink():
                    raise PlanError("revision: selected symlink payload unsupported")
            return entry
        except PlanError:
            raise
        except (OSError, ValueError):
            raise PlanError("revision: unsupported selected platform path") from None

    @staticmethod
    def matches(data: bytes, oid: str) -> bool:
        digest = hashlib.sha1() if len(oid) == 40 else hashlib.sha256()
        digest.update(b"blob " + str(len(data)).encode("ascii") + b"\0")
        digest.update(data)
        return digest.hexdigest() == oid

    def read(self, path: Path) -> bytes:
        mode, _, oid = self.require(path)
        try:
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode):
                raise PlanError("revision: selected platform payload is not a regular file")
            data = path.read_bytes()
            if bool(info.st_mode & stat.S_IXUSR) != (mode == "100755") or not self.matches(data, oid):
                raise PlanError("revision: selected platform bytes differ from committed tree")
            return data
        except OSError:
            raise PlanError("revision: cannot read selected platform blob") from None

    def modified(self, consumed: Iterable[Path] = ()) -> bool:
        # Consumed candidate inputs need provenance even if Git ignores them.
        for path in consumed:
            try:
                self.require(path)
            except PlanError:
                return True
        for name, (mode, kind, oid) in self.entries.items():
            path = self.root / name
            try:
                if any(p.is_symlink() for p in path.parents if p != self.root and p.is_relative_to(self.root)):
                    return True
                if mode == "120000" and path.is_symlink():
                    if not self.matches(os.fsencode(os.readlink(path)), oid):
                        return True
                elif kind != "blob":
                    return True
                else:
                    self.read(path)
            except (OSError, PlanError):
                return True
        # Names only, never content conversion. Ignored caches are not read. An
        # ignored/untracked selected platform payload still fails require/read.
        names = git(self.root, "ls-files", "--cached", "--others", "--exclude-standard", "-z")
        return any(os.fsdecode(name) not in self.entries for name in names.split(b"\0") if name)
