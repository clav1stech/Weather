"""Régénère CHANGELOG.md à partir des commits de versioning (`vX.Y.Z – résumé`).

Usage : python tools/generate_changelog.py

Source de vérité = messages de commit, pas une saisie manuelle séparée.
Idempotent : n'ajoute que les versions absentes du fichier existant, sans
toucher aux entrées déjà présentes (utile après un merge de branche qui
apporte plusieurs commits de version d'un coup, ou avant un tag de release
pour vérifier que rien n'a été oublié).
"""

import re
import subprocess
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
CHANGELOG_PATH = BASE_DIR / "CHANGELOG.md"
HEADER = "# Changelog\n"
VERSION_RE = re.compile(r"^v(\d+\.\d+\.\d+)\s*[–-]\s*(.+)$")
COMMIT_SEP = "\x1f"  # séparateur improbable dans un message de commit
ENTRY_SEP = "\x1e"


def _existing_versions() -> set[str]:
    if not CHANGELOG_PATH.exists():
        return set()
    return set(re.findall(r"^## \[(\d+\.\d+\.\d+)\]", CHANGELOG_PATH.read_text(), re.MULTILINE))


def _commits() -> list[tuple[str, str, str, str]]:
    """Retourne (version, date, résumé, corps) pour chaque commit vX.Y.Z, du plus récent au plus ancien."""
    fmt = f"%H{COMMIT_SEP}%ad{COMMIT_SEP}%s{COMMIT_SEP}%b{ENTRY_SEP}"
    raw = subprocess.run(
        ["git", "log", "main", "--date=short", f"--pretty=format:{fmt}"],
        cwd=BASE_DIR, capture_output=True, text=True, check=True,
    ).stdout
    out = []
    for entry in raw.split(ENTRY_SEP):
        entry = entry.strip("\n")
        if not entry:
            continue
        _hash, date, subject, body = entry.split(COMMIT_SEP, 3)
        m = VERSION_RE.match(subject.strip())
        if not m:
            continue
        version, summary = m.groups()
        out.append((version, date, summary.strip(), body.strip()))
    return out


def main() -> None:
    known = _existing_versions()
    commits = _commits()
    new_entries = [c for c in commits if c[0] not in known]
    if not new_entries:
        print("CHANGELOG.md déjà à jour, rien à ajouter.")
        return

    blocks = []
    for version, date, summary, body in new_entries:
        block = f"## [{version}] - {date}\n{summary}.\n"
        if body:
            block += f"\n{body}\n"
        blocks.append(block)

    existing_body = ""
    if CHANGELOG_PATH.exists():
        existing_body = CHANGELOG_PATH.read_text()
        if existing_body.startswith(HEADER):
            existing_body = existing_body[len(HEADER):].lstrip("\n")

    new_content = HEADER + "\n" + "\n".join(blocks)
    if existing_body:
        new_content += "\n" + existing_body
    CHANGELOG_PATH.write_text(new_content.rstrip() + "\n")
    print(f"{len(new_entries)} entrée(s) ajoutée(s) : {', '.join(v for v, *_ in new_entries)}")


if __name__ == "__main__":
    main()
