"""The wire-fact record lives in this repo and is cited by its in-repo path.

The record is `docs/api-contract.md` plus the endpoint catalogue
(`docs/api-endpoints.md`); each carries a banner saying it is maintained here.
App statics and current behavior live in the reference document
`docs/api-ws-user.md`.
That is what makes this repo self-contained: the protocol layer is vendored
because it has to execute here, and the document that explains it travels with
the code for the same reason.

These checks keep that arrangement honest:

- no tracked text cites the retired `api-notes.md`;
- the shipped code, tests and tool cite the in-repo `docs/` paths; an external
  checkout is never presented as the place to read a wire fact;
- each record file still carries its banner;
- app statics are reference material, not a second wire record;
- no shipped code, test or tool points at agent-facing or local-only
  files: `AGENTS.md`, the retired `.clinerules/` and `memory-bank/` names,
  and the local `notes/` directory are never cited there.
"""

from __future__ import annotations

import importlib
from pathlib import Path
import re

REPO_ROOT = Path(__file__).resolve().parents[1]
SCANNED_DIRS = ("custom_components", "tests", "tools", "docs")
SCANNED_ROOT_FILES = (
    "README.md",
    "CHANGELOG.md",
    "CONTRIBUTING.md",
    "AGENTS.md",
)
RETIRED = "api-notes"
RECORD_DOCS = ("api-contract.md", "api-endpoints.md")
# Repo-authored reference docs: their `§N` citations resolve like the record's,
# but they carry no banner.
REFERENCE_DOCS = ("api-ws-user.md",)
BANNER_BEGIN = "<!-- record:begin -->"
BANNER_END = "<!-- record:end -->"


def _scanned_files() -> list[Path]:
    files = [REPO_ROOT / name for name in SCANNED_ROOT_FILES]
    for directory in SCANNED_DIRS:
        files += sorted((REPO_ROOT / directory).rglob("*"))
    # this module has to name the banned file to ban it
    return [
        path
        for path in files
        if path.is_file()
        and path.suffix in (".py", ".md")
        and path != Path(__file__).resolve()
    ]


def _citations(name: str) -> list[str]:
    """``path:line`` for every scanned file mentioning ``name``."""
    found = []
    for path in _scanned_files():
        text = path.read_text(encoding="utf-8", errors="ignore")
        found += [
            f"{path.relative_to(REPO_ROOT)}:{number}"
            for number, line in enumerate(text.splitlines(), start=1)
            if name in line
        ]
    return found


def test_no_tracked_text_cites_the_retired_wire_notes() -> None:
    offenders = _citations(RETIRED)
    assert not offenders, (
        f"these cite the retired {RETIRED}.md; use docs/api-contract.md with the "
        f"section that carries the fact: {offenders}"
    )


def test_shipped_code_cites_the_vendored_copy() -> None:
    offenders = _citations("../smartbox/api-contract.md")
    assert not offenders, (
        "shipped code, tests and tools must cite the in-repo copy "
        f"(docs/api-contract.md): {offenders}"
    )


def test_record_docs_carry_their_banner() -> None:
    for name in RECORD_DOCS:
        path = REPO_ROOT / "docs" / name
        assert path.is_file(), f"docs/{name} is missing"
        text = path.read_text(encoding="utf-8")
        assert text.startswith(BANNER_BEGIN), f"docs/{name} lost its banner"
        assert "**Maintained here.**" in text
        assert BANNER_END in text


# --- citations resolve ---

# A section heading, e.g. `### 5.3 Confirmation frames` -> "5.3".
_SECTION = re.compile(r"^#{2,3}\s+(\d+(?:\.\d+)*)\.?\s+\S", re.MULTILINE)
# A citation, e.g. "§5.3" / "§ 5.3".
_CITATION = re.compile(r"§\s*(\d+(?:\.\d+)*)")
# How a citation names its document. Both spellings in use: the in-repo path
# and the bare name the record uses in prose ("api-endpoints §4"), plus
# "contract" for `api-contract.md` (THE contract, as the record calls itself).
_DOC_MENTION = re.compile(
    r"(?:(?:docs/)?(api-contract|api-endpoints|api-ws-user)\.md"
    r"|\b(api-endpoints|api-ws-user)\b|\bcontract\b)",
)
# The mention must sit immediately before the citation to scope it. Prose and
# table rows mention a document and cite the OTHER one in the same line
# (api-contract's evidence table is full of `[doc api-ws-user.md §N]` tags
# beside `§N` references to itself), so a line-wide or look-back scope
# mis-attributes them.
_MENTION_LOOKBEHIND = 40
_CODE_DIRS = ("custom_components", "tests", "tools")


def _sections(path: Path) -> set[str]:
    """Return the section numbers a document actually has."""
    return set(_SECTION.findall(path.read_text(encoding="utf-8")))


def _citation_sites(text: str) -> list[tuple[int, str, str | None]]:
    """``(line, section, named_doc)`` for every citation in ``text``."""
    sites = []
    for number, line in enumerate(text.splitlines(), 1):
        for match in _CITATION.finditer(line):
            before = line[max(0, match.start() - _MENTION_LOOKBEHIND) : match.start()]
            named = _DOC_MENTION.findall(before)
            doc = (named[-1][0] or named[-1][1] or "api-contract") if named else None
            sites.append((number, match.group(1), doc))
    return sites


def test_every_citation_resolves_to_a_real_section() -> None:
    """Every `§N` names a section that exists, in the document it names.

    The record is maintained by hand here, so a renumbered or removed
    section would otherwise rot silently: a comment keeps citing `§5.3`
    after `§5.3` stops existing, and nothing else reads the document. A
    citation that names no document is checked against the ambiguity the
    documents actually have - they number their sections
    independently (§2, §4, §5 and §7 overlap), so an unnamed one
    must be unambiguous by number alone. Inside a record document an
    unnamed citation resolves to that document; unnamed citations in any
    other document are rejected, so the record must be named there.

    Not checked: whether the cited section is the RIGHT one for the claim.
    A legitimate citation need not echo its section's title (the record's
    titles are terse: `§5.1` is "Key-count rule" and is correctly cited by
    prose about "ONE mutable key per POST"), so a topical check would be
    noise. That judgement stays with review.
    """
    sections = {
        name.removesuffix(".md"): _sections(REPO_ROOT / "docs" / name)
        for name in (*RECORD_DOCS, *REFERENCE_DOCS)
    }
    problems: list[str] = []
    for path in _scanned_files():
        # Inside a record document an unnamed citation means that document.
        # Code comments may resolve one by number when a single record has it;
        # any other document must name the record beside the citation.
        containing = path.stem if path.parent == REPO_ROOT / "docs" else None
        for line, section, named in _citation_sites(path.read_text(encoding="utf-8")):
            doc = named or containing
            if doc is None:
                holders = [
                    name for name, numbers in sections.items() if section in numbers
                ]
                if len(holders) != 1:
                    problems.append(
                        f"{path.relative_to(REPO_ROOT)}:{line} cites §{section} without "
                        f"naming a document, and it exists in {holders or 'neither'}"
                    )
            elif doc not in sections:
                problems.append(
                    f"{path.relative_to(REPO_ROOT)}:{line} cites §{section} of "
                    f"{doc}, which is not a registered record document"
                )
            elif section not in sections[doc]:
                problems.append(
                    f"{path.relative_to(REPO_ROOT)}:{line} cites §{section} of {doc}, "
                    "which has no such section"
                )
    assert not problems, (
        "these citations do not resolve (name the document, or the section it "
        f"really is - a list item is `§4 item 2`, not `§4.2`): {problems}"
    )


def test_the_citation_gate_resolves_a_citation_to_its_document() -> None:
    """The gate is only worth having if it attributes and can fail.

    Pins the resolution rule - the mention immediately before a citation
    scopes it, an unnamed one carries no document - and the two facts the
    ambiguity branch rests on: `§9` exists in one document only, `§5` in
    more than one.
    """
    assert _citation_sites("per api-endpoints §4 and docs/api-contract.md §5.1") == [
        (1, "4", "api-endpoints"),
        (1, "5.1", "api-contract"),
    ]
    assert _citation_sites("see docs/api-contract.md §1.9, §5.2 and §7") == [
        (1, "1.9", "api-contract"),
        (1, "5.2", "api-contract"),
        (1, "7", "api-contract"),
    ]
    assert _citation_sites("see §9") == [(1, "9", None)]

    sections = {
        name.removesuffix(".md"): _sections(REPO_ROOT / "docs" / name)
        for name in (*RECORD_DOCS, *REFERENCE_DOCS)
    }
    assert "5.1" in sections["api-contract"]  # the heading parse still works
    assert "5.1" not in sections["api-endpoints"]
    assert [name for name, numbers in sections.items() if "9" in numbers] == [
        "api-contract"
    ]
    assert sorted(name for name, numbers in sections.items() if "5" in numbers) == [
        "api-contract",
        "api-endpoints",
        "api-ws-user",
    ]


# Agent instructions change freely and local-only files do not ship:
# neither is a reference channel for shipped code. The retired process
# names stay banned so a stale citation cannot reappear.
_LOCAL_ONLY_REFERENCES = re.compile(
    r"(\bmemory[-_ ]bank|\.clinerules|AGENTS\.md|\bnotes/)",
    re.IGNORECASE,
)


# --- referenced repo paths and [file:line] ranges ---

_REFERENCED_PATH = re.compile(
    r"`((?:tools|tests|custom_components|docs|\.github)/[^`\s]+?)`"
)
_FILE_LINE = re.compile(r"\[([A-Za-z0-9_]+\.py):(\d+)(?:-(\d+))?\]")
_LIB_SYMBOL = re.compile(r"\[lib ([A-Za-z0-9_.]+):([A-Za-z0-9_.]+)\]")
_PATH_SUFFIXES = (".py", ".md", ".json", ".yaml", ".yml", ".png", ".sh")
_PLACEHOLDER = re.compile(r"[<>{}$*?…]")


def _doc_sources() -> list[Path]:
    """Shipped prose that cites repository paths (not agent-only files)."""
    files = [REPO_ROOT / name for name in SCANNED_ROOT_FILES if name != "AGENTS.md"]
    files += sorted((REPO_ROOT / "docs").glob("*.md"))
    return [path for path in files if path.is_file()]


def _repo_python_files() -> dict[str, Path]:
    """Map a bare module filename to its integration or library path."""
    found: dict[str, Path] = {}
    for path in (REPO_ROOT / "custom_components" / "smartbox").rglob("*.py"):
        found.setdefault(path.name, path)

    import smartbox  # noqa: PLC0415 - optional extraction dependency

    package_root = Path(smartbox.__file__).resolve().parent
    for layer in ("protocol", "core"):
        for path in (package_root / layer).glob("*.py"):
            found.setdefault(path.name, path)
    return found


def test_referenced_repo_paths_exist() -> None:
    """A shipped doc must not cite a file the repo does not contain."""
    missing: list[str] = []
    for path in _doc_sources():
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for match in _REFERENCED_PATH.finditer(line):
                target = match.group(1)
                if (
                    _PLACEHOLDER.search(target)
                    or not target.endswith(_PATH_SUFFIXES)
                    or target.startswith("docs/status.md")
                ):
                    continue
                if not (REPO_ROOT / target).is_file():
                    missing.append(
                        f"{path.relative_to(REPO_ROOT)}:{number} -> {target}"
                    )
    assert not missing, f"references to missing repo files: {missing}"


def test_bare_file_line_citations_are_in_range() -> None:
    """Every `[file.py:N]` names a line the file actually has.

    The citation gate already resolves `§N` to a section; this closes the
    other half: a code edit that grows/shrinks a module must not leave the
    record pointing past its end.
    """
    modules = _repo_python_files()
    problems: list[str] = []
    for path in _doc_sources():
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for match in _FILE_LINE.finditer(line):
                name, start, end = match.groups()
                module = modules.get(name)
                if module is None:
                    problems.append(
                        f"{path.relative_to(REPO_ROOT)}:{number} cites {name}, "
                        "which is not an integration or library module"
                    )
                    continue
                total = len(module.read_text(encoding="utf-8").splitlines())
                last = int(end or start)
                if int(start) < 1 or last > total:
                    problems.append(
                        f"{path.relative_to(REPO_ROOT)}:{number} cites "
                        f"{name}:{start}{'-' + end if end else ''}, "
                        f"but {name} has {total} lines"
                    )
    assert not problems, f"file:line citations out of range: {problems}"


def test_library_symbol_citations_resolve() -> None:
    """Every `[lib module:Symbol]` names a real object in the installed library.

    The wire record cites library behavior by symbol so a refactor cannot
    invalidate a line range silently. Resolving the symbol is the mechanical
    half; whether it is the RIGHT symbol for the claim stays with review.
    """
    problems: list[str] = []
    for path in _doc_sources():
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for match in _LIB_SYMBOL.finditer(line):
                module_name, symbol = match.groups()
                try:
                    target: object = importlib.import_module(module_name)
                except ImportError as e:
                    problems.append(
                        f"{path.relative_to(REPO_ROOT)}:{number} cites "
                        f"{module_name}, which does not import: {e}"
                    )
                    continue
                for attr in symbol.split("."):
                    try:
                        target = getattr(target, attr)
                    except AttributeError:
                        problems.append(
                            f"{path.relative_to(REPO_ROOT)}:{number} cites "
                            f"{module_name}:{symbol}, but {attr} is missing"
                        )
                        break
    assert not problems, f"library symbol citations do not resolve: {problems}"


_ENDPOINT_ID_ROW = re.compile(r"^\|\s*([ESX]\d{1,2})\s*\|", re.MULTILINE)
_ENDPOINT_ID = re.compile(r"\b([ESX]\d{1,2})\b")


def _catalog_endpoint_ids() -> list[str]:
    text = (REPO_ROOT / "docs" / "api-endpoints.md").read_text(encoding="utf-8")
    return _ENDPOINT_ID_ROW.findall(text)


def test_endpoint_ids_are_unique_and_references_resolve() -> None:
    """Every referenced endpoint ID exists in the catalogue, and no ID repeats.

    Endpoint IDs are stable keys used across the contract, apps doc, tests and
    code comments. A rename is only safe if every reference is visible; this
    gate covers table cells, backticked IDs and bare prose IDs. ``E2`` is also
    the app's extra-energy product code, so a bare ``E2`` in prose is ignored;
    the catalogue row and any backticked ``E2`` still resolve.
    """
    ids = _catalog_endpoint_ids()
    assert len(ids) == len(set(ids)), f"duplicate endpoint IDs: {ids}"
    known = set(ids)
    problems: list[str] = []
    for path in _scanned_files():
        for number, line in enumerate(
            path.read_text(encoding="utf-8", errors="ignore").splitlines(), 1
        ):
            refs: set[str] = set()
            if line.lstrip().startswith("|"):
                first_cell = line.strip().strip("|").split("|")[0]
                refs.update(re.findall(r"[ESX]\d{1,2}", first_cell))
            refs.update(re.findall(r"`([ESX]\d{1,2})`", line))
            for token in _ENDPOINT_ID.findall(line):
                if token == "E2" and token not in refs:
                    continue
                refs.add(token)
            problems.extend(
                f"{path.relative_to(REPO_ROOT)}:{number} cites {ref}, "
                "which is not in docs/api-endpoints.md"
                for ref in sorted(refs - known)
            )
    assert not problems, f"unknown endpoint IDs: {problems}"


_EVIDENCE_ID_ROW = re.compile(r"^- EV(\d{1,2}[a-c]?)\s", re.MULTILINE)
_EVIDENCE_ID = re.compile(r"\bEV(\d{1,2}[a-c]?)\b")


def test_evidence_ids_are_unique_and_references_resolve() -> None:
    """Evidence entries use EV# so they cannot collide with endpoint E#."""
    text = (REPO_ROOT / "docs" / "api-contract.md").read_text(encoding="utf-8")
    ids = _EVIDENCE_ID_ROW.findall(text)
    assert len(ids) == len(set(ids)), f"duplicate evidence IDs: {ids}"
    known = set(ids)
    problems: list[str] = []
    for path in _scanned_files():
        for number, line in enumerate(
            path.read_text(encoding="utf-8", errors="ignore").splitlines(), 1
        ):
            problems.extend(
                f"{path.relative_to(REPO_ROOT)}:{number} cites EV{ref}, "
                "which is not in api-contract.md Appendix 8"
                for ref in sorted(set(_EVIDENCE_ID.findall(line)) - known)
            )
    assert not problems, f"unknown evidence IDs: {problems}"


_ADAPTER_METHOD_ENDPOINTS = {
    "health_check": "E1",
    "get_api_version": "E2",
    "get_token": "E3",
    "get_node_power": "E33",
    "get_dev_data": "E46",
    "set_node_power_limit": "E47",
}


def _catalog_consumers() -> dict[str, str]:
    text = (REPO_ROOT / "docs" / "api-endpoints.md").read_text(encoding="utf-8")
    consumers: dict[str, str] = {}
    for line in text.splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) >= 4 and re.fullmatch(r"[ESX]\d+", cells[0]):
            consumers[cells[0]] = cells[3]
    return consumers


def test_catalogue_marks_adapter_called_endpoints() -> None:
    """A method the adapter calls must be labelled library+HA."""
    sources = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in (REPO_ROOT / "custom_components" / "smartbox").glob("*.py")
    )
    consumers = _catalog_consumers()
    for method, endpoint_id in _ADAPTER_METHOD_ENDPOINTS.items():
        if f".{method}(" in sources:
            assert consumers[endpoint_id] == "library+HA", (
                f"{endpoint_id} is called as {method} but labelled "
                f"{consumers[endpoint_id]}"
            )
    assert consumers["E28"] == "—"


def test_shipped_code_never_cites_local_only_files() -> None:
    """A shipped comment cites the durable docs, not agent instructions."""
    offenders: list[str] = []
    for directory in ("custom_components", "tests", "tools"):
        for path in sorted((REPO_ROOT / directory).rglob("*.py")):
            if path.resolve() == Path(__file__).resolve():
                continue  # this module has to name the excluded files to ban them
            text = path.read_text(encoding="utf-8")
            offenders += [
                f"{path.relative_to(REPO_ROOT)}:{number}"
                for number, line in enumerate(text.splitlines(), start=1)
                if _LOCAL_ONLY_REFERENCES.search(line)
            ]
    assert not offenders, (
        f"these lines cite agent-facing or local-only files: {offenders}"
    )
