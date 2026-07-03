"""End-to-end MCP scenarios: real EI documents in Sebastien's personal space.

Uploads a handful of real Euro-Information files into the ``perso-seb`` volume,
then drives the config-a2a agent through six credible user tasks that together
exercise the whole mcp-fs surface: tree/list, mkdir/move, fs.extract_text over
PPTX/DOCX/MD/PDF/image, fs.grep, fs.write (HTML) and fs.write_docx.

Each scenario asserts the agent completed, used the expected tools, and left the
right artifacts on the volume. Run:  uv run python scenarios/run_scenarios.py
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from scenarios.client import AgentClient, FsClient, Turn

EI = Path("/Users/sebastien/Documents/IBM/Clients/Euro-Information")

# real EI file -> name on the volume
FIXTURES: dict[str, str] = {
    "EAEF-presentation-fr.pptx": "EAEF-presentation-fr.pptx",
    "note_cadrage_chantiers_agentique_HD40.docx": "note_cadrage.docx",
    "architecture-plateforme-agentique-ei.md": "architecture-plateforme.md",
    "20260511 - Harness engineering.pdf": "harness-engineering.pdf",
    "Feuille de route 2026 - Agentic Fundation 2026-06-22.png": "feuille-de-route.png",
}


def setup(fs: FsClient) -> None:
    for folder in ("/inbox", "/library", "/syntheses", "/newsletter"):
        try:
            fs.delete(folder)
        except Exception:  # noqa: BLE001  # pylint: disable=broad-exception-caught
            pass
    fs.mkdir("/inbox")
    for source, name in FIXTURES.items():
        path = EI / source
        if not path.is_file():
            print(f"  ! missing fixture {path}")
            continue
        fs.upload(path, "/inbox", name)


@dataclass
class Scenario:
    """One user task: a prompt plus its expected tools, keywords and artifact check."""

    name: str
    prompt: str
    expect_tools: tuple[str, ...] = ()
    expect_keywords: tuple[str, ...] = ()
    check: Callable[[FsClient], tuple[bool, str]] | None = None


def _has_tool(turn: Turn, needle: str) -> bool:
    return any(needle in call for call in turn.tool_calls)


def _library_organized(fs: FsClient) -> tuple[bool, str]:
    try:
        pres = {e["name"] for e in fs.list("/library/presentations")}
        docs = {e["name"] for e in fs.list("/library/documents")}
        imgs = {e["name"] for e in fs.list("/library/images")}
    except Exception as exc:  # noqa: BLE001  # pylint: disable=broad-exception-caught
        return False, f"library not organized: {exc}"
    ok = "EAEF-presentation-fr.pptx" in pres and "feuille-de-route.png" in imgs and len(docs) >= 2
    return ok, f"presentations={sorted(pres)} documents={sorted(docs)} images={sorted(imgs)}"


def _docx_synthesis(fs: FsClient) -> tuple[bool, str]:
    candidates = [e["name"] for e in fs.list("/syntheses")] if fs.exists("/syntheses") else []
    docx = next((c for c in candidates if c.lower().endswith(".docx")), None)
    if not docx:
        return False, f"no .docx under /syntheses (found {candidates})"
    data = fs.download(f"/syntheses/{docx}")
    return (len(data) > 2000 and data[:2] == b"PK"), f"/syntheses/{docx} = {len(data)} bytes"


def _html_newsletter(fs: FsClient) -> tuple[bool, str]:
    if not fs.exists("/newsletter"):
        return False, "no /newsletter folder"
    files = [e["name"] for e in fs.list("/newsletter")]
    html = next((f for f in files if f.lower().endswith((".html", ".htm"))), None)
    if not html:
        return False, f"no .html under /newsletter (found {files})"
    body = fs.download(f"/newsletter/{html}").decode("utf-8", "replace").lower()
    return ("<html" in body or "<!doctype" in body or "<h1" in body), f"/newsletter/{html} ({len(body)} chars)"


SCENARIOS: list[Scenario] = [
    Scenario(
        name="organize-inbox",
        prompt=(
            "Dans le dossier /inbox il y a des fichiers en vrac. Crée les dossiers "
            "/library/presentations, /library/documents et /library/images, puis déplace chaque fichier "
            "de /inbox vers le bon dossier selon son type : .pptx dans presentations, .pdf/.docx/.md dans "
            "documents, .png dans images. Confirme le rangement à la fin."
        ),
        expect_tools=("fs.move",),
        check=_library_organized,
    ),
    Scenario(
        name="read-pptx-summary",
        prompt=(
            "Retrouve la présentation EAEF (un .pptx) dans mon espace, lis son contenu et résume-la "
            "en 5 puces maximum."
        ),
        expect_tools=("fs.extract_text",),
        expect_keywords=("EAEF",),
    ),
    Scenario(
        name="read-pdf-question",
        prompt=(
            "Ouvre le PDF 'harness-engineering' et explique en 3 phrases de quoi il parle."
        ),
        expect_tools=("fs.extract_text",),
        expect_keywords=("harness",),
    ),
    Scenario(
        name="docx-synthesis",
        prompt=(
            "Lis la note de cadrage (le .docx) et génère une synthèse d'une page, structurée avec un titre, "
            "des sections et des puces, dans le fichier /syntheses/cadrage.docx."
        ),
        expect_tools=("fs.extract_text", "fs.write_docx"),
        check=_docx_synthesis,
    ),
    Scenario(
        name="html-newsletter",
        prompt=(
            "Lis le document markdown sur l'architecture de la plateforme agentique et rédige une courte "
            "newsletter interne au format HTML (titre, intro, 3 points clés). Écris-la dans "
            "/newsletter/index.html."
        ),
        expect_tools=("fs.write",),
        check=_html_newsletter,
    ),
    Scenario(
        name="image-honest-degrade",
        prompt=(
            "Que peux-tu extraire du fichier image 'feuille-de-route.png' ? Utilise l'outil d'extraction "
            "et dis-moi honnêtement ce que tu obtiens."
        ),
        expect_tools=("fs.extract_text",),
    ),
]


def run() -> int:
    """Upload fixtures, run every scenario, print a report; return 0 iff all pass."""
    fs = FsClient()
    print(f"# Setup: uploading {len(FIXTURES)} EI files into '{fs.mount}' /inbox")
    setup(fs)
    agent = AgentClient()
    results: list[tuple[str, bool, str]] = []
    for scenario in SCENARIOS:
        print(f"\n=== {scenario.name} ===")
        turn = agent.converse(scenario.prompt)
        reasons: list[str] = []
        ok = turn.ok
        if not ok:
            reasons.append(f"state={turn.state} final={turn.final_text[:80]!r}")
        for tool in scenario.expect_tools:
            if not _has_tool(turn, tool):
                ok = False
                reasons.append(f"missing tool {tool} (used {turn.tool_calls})")
        for keyword in scenario.expect_keywords:
            if keyword.lower() not in turn.final_text.lower():
                ok = False
                reasons.append(f"missing keyword {keyword!r}")
        if scenario.check:
            passed, detail = scenario.check(fs)
            reasons.append(detail)
            ok = ok and passed
        results.append((scenario.name, ok, "; ".join(reasons)))
        print(f"  tools: {turn.tool_calls}")
        print(f"  final: {turn.final_text[:220]}")
        print(f"  -> {'PASS' if ok else 'FAIL'}  {'; '.join(reasons)}")

    passed = sum(1 for _, ok, _ in results if ok)
    print(f"\n{'=' * 60}\nRESULT: {passed}/{len(results)} scenarios passed")
    for name, ok, reason in results:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}  {reason if not ok else ''}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(run())
