"""Consistent renaming of corpus paths.

Structural positions are replaced outright (GitHub/GitLab owner and project,
web domain, catalog project title); the snapshot hash suffix and every
non-identifying component are kept, so the scorer can still match files by
path. Remaining components (file names) go through the same content scrubber,
so an identity gets the same pseudonym in a path as inside any file.
"""
from .identities import distinctive
from .pseudo import pseudonym


class Renamer:
    def __init__(self, scrubber):
        self.sc = scrubber
        self.cache = {}

    def component(self, name: str) -> str:
        """A single non-structural path component (file/folder name)."""
        if name not in self.cache:
            self.cache[name] = self.sc.scrub_text(name)
        return self.cache[name]

    def _repo_dir(self, top: str) -> str:
        suffix = ""
        if top.endswith(".zip"):
            top, suffix = top[:-4], ".zip"
        bits = top.split("__")
        if len(bits) >= 2:
            bits[0] = pseudonym(bits[0], "org")
            if distinctive(bits[1]):
                bits[1] = pseudonym(bits[1], "project")
            else:
                bits[1] = self.component(bits[1])
        return "__".join(bits) + suffix

    def key(self, key: str) -> str:
        p = key.split("/")
        out = list(p)
        if p[0] == "archives" and len(p) > 1:
            out[1] = self._repo_dir(p[1])
            rest = 2
        elif p[0] == "sources" and len(p) > 2 and p[1] == "github":
            out[2] = self._repo_dir(p[2])
            rest = 3
        elif p[0] == "sources" and len(p) > 2 and p[1] == "gitlab":
            bits = p[2].split("__")
            host, mid = bits[0], bits[1:]
            last = len(mid) - 1
            for i, b in enumerate(mid):
                if not b or b in ("project", "projects"):
                    continue
                if i < last:
                    mid[i] = pseudonym(b, "org")
                elif distinctive(b):
                    mid[i] = pseudonym(b, "project")
            out[2] = "__".join([host] + mid)
            rest = 3
        elif p[0] == "sources" and len(p) > 2 and p[1] == "web":
            out[2] = pseudonym(p[2], "domain")
            rest = 3
        elif p[0] == "sources" and len(p) > 2 and p[1] == "catalog":
            t = p[2]
            if "." in t and " " not in t and "_" not in t:
                out[2] = pseudonym(t, "domain")
            else:
                out[2] = self.component(t)  # curated institution/person/project tokens only
            rest = 3
        else:
            rest = 1
        for i in range(rest, len(p)):
            out[i] = self.component(p[i])
        return "/".join(out)
