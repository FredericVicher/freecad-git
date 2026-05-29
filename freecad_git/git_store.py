"""Selective git storage for FreeCAD documents.

A `GitStore` wraps a pygit2 Repository and commits Document.xml + a curated
set of .brp blobs as a flat tree, directly in the object database. No
working tree is used; all I/O is in-memory.

The repository is created bare on first use. Push/fetch with remotes works
normally; users never need to manipulate the working tree.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

import pygit2  # type: ignore[import-not-found]


BRANCH = "refs/heads/main"


@dataclass(frozen=True)
class Author:
    name: str
    email: str

    def signature(self) -> "pygit2.Signature":
        return pygit2.Signature(self.name, self.email)


class GitStore:
    """In-memory git interface for selective FreeCAD versioning."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        discovered = pygit2.discover_repository(str(self.path)) if self.path.exists() else None
        if discovered:
            self.repo = pygit2.Repository(discovered)
        else:
            self.path.mkdir(parents=True, exist_ok=True)
            self.repo = pygit2.init_repository(str(self.path), bare=True)
            # libgit2 defaults HEAD to refs/heads/master; we commit on
            # refs/heads/main, so point HEAD at our branch from the start.
            self.repo.set_head(BRANCH)

    @property
    def has_head(self) -> bool:
        return BRANCH in self.repo.references

    def current_commit(self) -> str | None:
        """Return the currently loaded commit OID (from pull), or None."""
        marker = self.path / "CURRENT_COMMIT"
        if marker.exists():
            return marker.read_text().strip()
        return None

    def head_oid(self) -> str | None:
        if not self.has_head:
            return None
        return str(self.repo.references[BRANCH].target)

    def resolve_ref(self, ref: str) -> str:
        """Return the commit OID hex string for the given ref (HEAD, branch, oid).

        Falls back to the tracked branch when "HEAD" points at a non-existent
        ref (e.g. legacy repos initialized with HEAD->refs/heads/master while
        we commit on refs/heads/main).
        """
        try:
            return str(self.repo.revparse_single(ref).peel(pygit2.Commit).id)
        except KeyError:
            if ref == "HEAD" and self.has_head:
                # Re-point HEAD so future lookups are consistent.
                self.repo.set_head(BRANCH)
                return str(self.repo.references[BRANCH].target)
            raise

    def set_branch_to(self, oid: str) -> None:
        """Move the tracked branch to the given commit OID (destructive)."""
        if self.has_head:
            self.repo.references[BRANCH].set_target(oid)
        else:
            self.repo.references.create(BRANCH, oid)
        self.repo.set_head(BRANCH)

    def commit(
        self,
        *,
        document_xml: bytes,
        blobs: Mapping[str, bytes],
        message: str,
        author: Author,
    ) -> str:
        """Build a flat tree from the given content and commit it.

        The same `author` is used as committer. Returns the new commit OID
        as a hex string. If a commit already exists on the branch, it is
        used as the parent.
        """
        builder = self.repo.TreeBuilder()
        builder.insert(
            "Document.xml",
            self.repo.create_blob(document_xml),
            pygit2.GIT_FILEMODE_BLOB,
        )
        for filename, data in blobs.items():
            builder.insert(
                filename,
                self.repo.create_blob(data),
                pygit2.GIT_FILEMODE_BLOB,
            )
        tree_oid = builder.write()

        sig = author.signature()
        parents = [self.repo.references[BRANCH].target] if self.has_head else []
        commit_oid = self.repo.create_commit(
            BRANCH, sig, sig, message, tree_oid, parents,
        )
        return str(commit_oid)

    def read_tree_at(self, ref: str = BRANCH) -> tuple[bytes, dict[str, bytes]]:
        """Return (Document.xml bytes, {filename: bytes}) for the given ref."""
        commit = self.repo.revparse_single(ref).peel(pygit2.Commit)
        tree = commit.tree
        xml = bytes(self.repo[tree["Document.xml"].id].data)
        blobs: dict[str, bytes] = {}
        for entry in tree:
            if entry.name == "Document.xml":
                continue
            blobs[entry.name] = bytes(self.repo[entry.id].data)
        return xml, blobs

    def document_xml_at(self, ref: str = BRANCH) -> bytes:
        """Return Document.xml bytes at the given ref (cheaper than read_tree_at)."""
        commit = self.repo.revparse_single(ref).peel(pygit2.Commit)
        return bytes(self.repo[commit.tree["Document.xml"].id].data)

    def diff_paths_between(self, old_ref: str, new_ref: str) -> list[str]:
        """Return the list of tree entries that differ between two commits."""
        old_tree = self.repo.revparse_single(old_ref).peel(pygit2.Commit).tree
        new_tree = self.repo.revparse_single(new_ref).peel(pygit2.Commit).tree
        diff = old_tree.diff_to_tree(new_tree)
        return [p.delta.new_file.path or p.delta.old_file.path for p in diff]
