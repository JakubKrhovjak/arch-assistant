"""Searching for new software in the Arch repos, the AUR and Flathub for the Browse tab. No Qt here.

Every source is searched independently: one that fails (no network for the AUR, no flatpak) only adds an error,
the others still answer. Results are sorted by relevance: exact name first, then names containing the term, then
the rest (AUR packages by popularity within each rank).
"""
from __future__ import annotations

import json
import re
import subprocess
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable

AUR_RPC = "https://aur.archlinux.org/rpc/v5/search/{term}?by=name-desc"
AUR_TIMEOUT = 15
COMMAND_TIMEOUT = 60
MIN_TERM = 2
MAX_PER_SOURCE = 150
SOURCES = ("repo", "aur", "flatpak")
# Flatpak refs that are not apps a user installs on their own
FLATPAK_SKIP_SUFFIXES = (".Debug", ".Locale", ".Sources")
REPO_LINE_RE = re.compile(r"^(?P<repo>[\w.-]+)/(?P<name>\S+) (?P<version>\S+)(?P<rest>.*)$")


@dataclass(frozen=True)
class Result:
    name: str            # display name (Flatpak app name, else the package name)
    id: str              # package name or Flatpak app id
    version: str
    description: str
    source: str          # repo | aur | flatpak
    installed: bool = False
    origin: str = ""     # repo name (core, extra, …) or Flatpak remote
    votes: int = 0       # AUR only
    popularity: float = 0.0

    @property
    def key(self) -> tuple[str, str]:
        return (self.source, self.id)

    @property
    def install_arg(self) -> str:
        """Argument of actions/install.sh."""
        return f"{self.source}:{self.id}"


@dataclass
class SearchResult:
    term: str
    results: list[Result] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def normalize(term: str) -> str:
    """Letters, digits, spaces and the characters package names use; everything else is dropped."""
    return " ".join(re.sub(r"[^A-Za-z0-9 @._+-]", " ", term).split())


def _run(cmd: list[str], codes: tuple[int, ...] = (0,)) -> str | None:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=COMMAND_TIMEOUT, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return proc.stdout if proc.returncode in codes else None


def _regex(word: str) -> str:
    """A literal word as a pacman (POSIX extended) regex."""
    return "".join(f"[{c}]" if c in ".+" else c for c in word)


def parse_pacman_ss(text: str, installed: set[str]) -> list[Result]:
    """`pacman -Ss` output: a "repo/name version [(groups)] [[installed]]" line followed by an indented description."""
    out: list[Result] = []
    current = None
    for line in text.splitlines():
        m = REPO_LINE_RE.match(line)
        if m:
            if current:
                out.append(current)
            name = m["name"]
            current = Result(name, name, m["version"], "", "repo", "[installed" in m["rest"] or name in installed,
                             m["repo"])
        elif current and line.startswith((" ", "\t")):
            current = Result(current.name, current.id, current.version, line.strip(), "repo", current.installed,
                             current.origin)
    if current:
        out.append(current)
    return out


def parse_aur(data: dict, installed: set[str]) -> list[Result]:
    """AUR RPC v5 search response."""
    return [Result(str(r.get("Name", "")), str(r.get("Name", "")), str(r.get("Version", "")),
                   str(r.get("Description") or ""), "aur", str(r.get("Name", "")) in installed, "AUR",
                   int(r.get("NumVotes") or 0), float(r.get("Popularity") or 0.0))
            for r in data.get("results", []) if r.get("Name")]


def parse_flatpak_search(text: str, installed: set[str]) -> list[Result]:
    """`flatpak search --columns=application,name,description,version,remotes` output ("No matches found" = none)."""
    out = []
    for line in text.splitlines():
        cols = (line.split("\t") + [""] * 5)[:5]
        app_id = cols[0].strip()
        if "\t" not in line or not app_id or app_id.endswith(FLATPAK_SKIP_SUFFIXES):
            continue
        out.append(Result(cols[1].strip() or app_id, app_id, cols[3].strip(), cols[2].strip(), "flatpak",
                          app_id in installed, cols[4].strip().split(",")[0]))
    return out


def relevance(r: Result, term: str) -> tuple:
    t = term.lower()
    names = {r.id.lower(), r.name.lower(), r.id.lower().rsplit(".", 1)[-1]}
    rank = 0 if t in names else 1 if any(t in n for n in names) else 2
    return (rank, -r.popularity, len(r.id), r.id.lower())


def fetch_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "arch-assistant"})
    with urllib.request.urlopen(req, timeout=AUR_TIMEOUT) as resp:   # noqa: S310 (fixed https URL)
        return json.load(resp)


def _search_repo(words: list[str], installed: set[str]) -> list[Result]:
    text = _run(["pacman", "-Ss", "--", *(_regex(w) for w in words)], codes=(0, 1))   # 1 = no match
    if text is None:
        raise RuntimeError("pacman -Ss failed")
    return parse_pacman_ss(text, installed)


def _search_aur(words: list[str], installed: set[str], fetch: Callable[[str], dict]) -> list[Result]:
    # the RPC takes one term: search the longest word, then keep what matches every word
    word = max(words, key=len)
    data = fetch(AUR_RPC.format(term=urllib.parse.quote(word)))
    if data.get("type") == "error":
        raise RuntimeError(str(data.get("error", "AUR error")))
    rows = parse_aur(data, installed)
    return [r for r in rows if all(w.lower() in f"{r.id} {r.description}".lower() for w in words)]


def _search_flatpak(words: list[str], installed_apps: Callable[[], set[str]]) -> list[Result]:
    text = _run(["flatpak", "search", "--columns=application,name,description,version,remotes", " ".join(words)])
    if text is None:
        raise RuntimeError("flatpak search failed")
    return parse_flatpak_search(text, installed_apps())


def _installed_packages() -> set[str]:
    return set((_run(["pacman", "-Qq"]) or "").split())


def _installed_flatpaks() -> set[str]:
    return set((_run(["flatpak", "list", "--app", "--columns=application"]) or "").split())


def search(term: str, fetch: Callable[[str], dict] = fetch_json) -> SearchResult:
    """Search all three sources in parallel. Network and subprocesses: call it off the GUI thread."""
    term = normalize(term)
    out = SearchResult(term)
    words = term.split()
    if len(term) < MIN_TERM:
        return out
    installed = _installed_packages()
    jobs = {"repo": lambda: _search_repo(words, installed),
            "aur": lambda: _search_aur(words, installed, fetch),
            "flatpak": lambda: _search_flatpak(words, _installed_flatpaks)}
    labels = {"repo": "Arch repos", "aur": "AUR", "flatpak": "Flatpak"}
    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        futures = {source: pool.submit(job) for source, job in jobs.items()}
        for source in SOURCES:
            try:
                rows = sorted(futures[source].result(), key=lambda r: relevance(r, term))
                out.results += rows[:MAX_PER_SOURCE]
            except Exception as e:   # one source failing must not hide the others
                out.errors.append(f"{labels[source]}: {e}")
    out.results.sort(key=lambda r: relevance(r, term)[0])   # stable: keeps the per-source order within a rank
    return out
