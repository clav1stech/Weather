"""Export du projet sous deux profils complémentaires.

    --ai       (défaut) : un unique .txt curé, destiné à être communiqué à une IA.
                          Inclut le code ET la documentation (.md : CLAUDE, CODEMAP,
                          CONVENTIONS), précédé d'un manifeste (git, version, sommaire,
                          estimation de tokens). Exclut tools/, les scripts de lancement
                          (.bat/.command) et .gitattributes (bruit, déjà couverts par
                          docs/COMMANDES.md).
    --backup            : un .zip complet et restaurable, destiné à la sauvegarde hors git.
                          Inclut TOUT le code source (y compris tools/ et docs/), hors
                          données (data/, legacy/) et binaires. Rotation des N plus récents.
    --only <chemins>    : restreint la collecte à un sous-ensemble de chemins (répétable ou
                          séparés par des virgules), pour réduire le volume envoyé à l'IA sur
                          une question ciblée. Le nom du fichier reflète le périmètre exporté ;
                          le manifeste liste les fichiers laissés hors périmètre. N'avance pas
                          le numéro de patch Z (un sous-ensemble du même commit n'est pas un
                          nouvel export).

Les deux profils partagent la même collecte de fichiers, paramétrée par profil : on ne
duplique jamais la logique de parcours. Export/ est gitignoré → aucun artefact n'est commité.
"""

import argparse
import pathlib
import re
import subprocess
import zipfile
from datetime import datetime

# Configuration — le script vit dans tools/, la racine du projet est son parent
project_dir = pathlib.Path(__file__).parent.parent
export_dir = project_dir / "Export"

# Nombre de sauvegardes zip conservées (rotation) ; les plus anciennes sont purgées
BACKUP_KEEP = 15


# Extraire la version depuis meteo_app.py (source de vérité unique, lue par regex)
def _get_app_version():
    meteo_app = project_dir / "meteo_app.py"
    with open(meteo_app, "r", encoding="utf-8") as f:
        match = re.search(r'APP_VERSION\s*=\s*"(\d+)\.(\d+)\.(\d+)"', f.read())
        if match:
            return int(match.group(1)), int(match.group(2)), int(match.group(3))
        return 2, 0, 0


VERSION_X, VERSION_Y, VERSION_Z = _get_app_version()

# Dossiers ignorés dans TOUS les profils (données intouchables, artefacts, config locale)
EXCLUDED_DIRS_ALWAYS = {
    "data",
    "legacy",
    "Export",
    "__pycache__",
    ".git",
    ".claude",
    ".devcontainer",
    ".venv",
    "golden",  # tools/golden/ : références de non-régression, régénérées localement
}

# Dossiers ignorés uniquement en profil --ai (utilitaires hors périmètre d'analyse)
EXCLUDED_DIRS_AI = {"tools"}

# Fichiers ignorés uniquement en profil --ai (bruit / quasi-doublon pour l'analyse IA)
EXCLUDED_FILES_AI = {"Forecast_legacy.py"}

# Extensions/-noms de fichiers texte inclus, communes aux deux profils
INCLUDED_EXTENSIONS_COMMON = {
    ".py", ".txt", ".md", ".yml", ".yaml", ".json", ".toml", ".cfg", ".ini", ".gitignore",
}

# Extensions supplémentaires réservées au --backup : scripts de lancement et métadonnées
# git, faible densité d'information pour une IA (déjà couverts par docs/COMMANDES.md) mais
# nécessaires à une sauvegarde fidèle du dépôt.
INCLUDED_EXTENSIONS_BACKUP_EXTRA = {".bat", ".command", ".gitattributes"}

# Documents toujours conservés en profil --ai même sous --only (contexte minimal indispensable)
AI_ALWAYS_KEEP = {"CLAUDE.md", "docs/CODEMAP.md", "docs/CONVENTIONS.md"}

# Indice de langage pour les fences, aide au parsing côté IA
_LANG_BY_SUFFIX = {
    ".py": "python", ".md": "markdown", ".yml": "yaml", ".yaml": "yaml",
    ".json": "json", ".toml": "toml", ".bat": "bat", ".command": "sh",
}


def _get_git_info():
    """Branche + hash court + sujet du HEAD, pour tracer l'export. Tolérant à l'absence de git."""
    def _run(args):
        try:
            return subprocess.check_output(
                args, cwd=project_dir, stderr=subprocess.DEVNULL, text=True
            ).strip()
        except Exception:
            return "?"
    return {
        "branch": _run(["git", "rev-parse", "--abbrev-ref", "HEAD"]),
        "commit": _run(["git", "rev-parse", "--short", "HEAD"]),
        "subject": _run(["git", "log", "-1", "--pretty=%s"]),
    }


def _resolve_patch_version(suffix, advance):
    """Numéro de patch Z pour le couple (X.Y, extension) dans Export/.

    advance=True  → prochain Z disponible (nouvel export plein).
    advance=False → Z déjà utilisé le plus récent, sans avancer : un export partiel IA
                    (--only) sur le même commit ne mérite pas un nouveau numéro de version.
    """
    pattern = re.compile(rf"_v{VERSION_X}\.{VERSION_Y}\.(\d+)$")
    max_z = -1
    for f in export_dir.iterdir():
        if not f.is_file() or f.suffix != suffix:
            continue
        m = pattern.search(f.stem)
        if m:
            max_z = max(max_z, int(m.group(1)))
    return max_z + 1 if advance else max(max_z, 0)


def collect_files(profile, only=None):
    """Fichiers texte pertinents pour le profil, triés par chemin.

    profile == "ai"     → exclut tools/ et Forecast_legacy.py (analyse curée).
    profile == "backup" → tout le code source, hors données/binaires (sauvegarde fidèle).
    only                → limite aux chemins (fichier ou dossier) listés, en gardant toujours
                          AI_ALWAYS_KEEP en profil "ai" (contexte minimal indispensable).
    """
    excluded_dirs = EXCLUDED_DIRS_ALWAYS | (EXCLUDED_DIRS_AI if profile == "ai" else set())
    excluded_files = EXCLUDED_FILES_AI if profile == "ai" else set()
    included_extensions = INCLUDED_EXTENSIONS_COMMON | (
        INCLUDED_EXTENSIONS_BACKUP_EXTRA if profile == "backup" else set()
    )
    files = []
    for path in sorted(project_dir.rglob("*")):
        if not path.is_file():
            continue
        rel_parts = path.relative_to(project_dir).parts
        # "data" exclu SEULEMENT en premier niveau (le data/ racine, données
        # binaires) : un `any(part in ...)` sur tous les segments exclurait à
        # tort app/data/ (code, module central du dashboard, cf. CODEMAP) qui
        # partage le même nom. Les autres dossiers exclus (__pycache__, golden…)
        # gardent le matching « à toute profondeur », nécessaire pour __pycache__
        # qui réapparaît sous chaque sous-package.
        if rel_parts[0] == "data" or any(
            part in (excluded_dirs - {"data"}) for part in rel_parts[:-1]
        ):
            continue
        if path.name in excluded_files:
            continue
        # .gitignore/.gitattributes n'ont pas d'extension : on retombe sur le nom
        suffix = path.suffix if path.suffix else path.name
        if suffix not in included_extensions:
            continue
        files.append(path)

    if only:
        prefixes = tuple(o.strip("/") for o in only)

        def _matches(rel_posix):
            return any(rel_posix == p or rel_posix.startswith(p + "/") for p in prefixes)

        scoped = [f for f in files if _matches(f.relative_to(project_dir).as_posix())]
        if profile == "ai":
            scoped_set = set(scoped)
            for f in files:
                if f.relative_to(project_dir).as_posix() in AI_ALWAYS_KEEP and f not in scoped_set:
                    scoped.append(f)
            scoped.sort()
        files = scoped

    return files


def _prune_backups(suffix, keep):
    """Purge les zips de sauvegarde au-delà des `keep` plus récents (rotation hors git)."""
    zips = sorted(
        (f for f in export_dir.iterdir() if f.is_file() and f.suffix == suffix),
        key=lambda f: f.stat().st_mtime,
    )
    for f in zips[:-keep] if keep > 0 else []:
        try:
            f.unlink()
        except OSError:
            pass


def export_ai(files, base_name, git, excluded=None):
    """Écrit le .txt curé : manifeste (git/version/sommaire/tokens) puis fichiers balisés.

    excluded, en export partiel (--only), liste les fichiers du périmètre --ai complet
    laissés hors de ce sous-ensemble, pour que le manifeste s'auto-décrive.
    """
    txt_path = export_dir / f"{base_name}.txt"

    # Pré-lecture pour le sommaire et l'estimation de volume (une seule lecture par fichier)
    contents = {}
    for f in files:
        try:
            contents[f] = f.read_text(encoding="utf-8")
        except Exception as e:
            contents[f] = f"[Erreur lecture : {e}]"

    total_chars = sum(len(c) for c in contents.values())
    approx_tokens = total_chars // 4  # heuristique ~4 caractères / token

    with open(txt_path, "w", encoding="utf-8", newline="\n") as out:
        out.write(f"# ===== EXPORT PROJET {project_dir.name} =====\n")
        out.write(f"# Date        : {datetime.now():%Y-%m-%d %H:%M:%S}\n")
        out.write(f"# Branche     : {git['branch']}\n")
        out.write(f"# Commit      : {git['commit']} \"{git['subject']}\"\n")
        out.write(f"# APP_VERSION : {VERSION_X}.{VERSION_Y}.{VERSION_Z}\n")
        out.write(
            f"# Fichiers    : {len(files)} | Taille : {total_chars // 1024} Ko "
            f"| ~Tokens : ~{approx_tokens:,}\n".replace(",", " ")
        )
        out.write("#\n# SOMMAIRE\n")
        for f in files:
            rel = f.relative_to(project_dir).as_posix()
            n_lines = contents[f].count("\n") + 1
            out.write(f"#   {rel}  ({n_lines} lignes, {len(contents[f]) // 1024} Ko)\n")
        if excluded:
            out.write(f"#\n# NON INCLUS ({len(excluded)} fichiers hors périmètre --only)\n")
            for f in excluded:
                out.write(f"#   {f.relative_to(project_dir).as_posix()}\n")
        out.write("\n")

        for f in files:
            rel = f.relative_to(project_dir).as_posix()
            lang = _LANG_BY_SUFFIX.get(f.suffix, "")
            out.write(f"===== {rel} =====\n")
            out.write(f"```{lang}\n{contents[f]}\n```\n\n")

    print(f"Export IA   : {txt_path}  (~{approx_tokens:,} tokens)".replace(",", " "))


def export_backup(files, base_name):
    """Écrit le .zip complet et restaurable, puis applique la rotation."""
    zip_path = export_dir / f"{base_name}.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for f in files:
            zf.write(f, arcname=f.relative_to(project_dir).as_posix())
    _prune_backups(".zip", BACKUP_KEEP)
    print(f"Sauvegarde  : {zip_path}  ({len(files)} fichiers)")


def _scope_tag(only):
    """Segment de nom de fichier reflétant le périmètre exporté (le .txt doit s'auto-décrire)."""
    if not only:
        return "full"
    tags = [pathlib.PurePosixPath(o.strip("/")).name for o in only]
    tag = "+".join(tags)
    return tag if len(tag) <= 60 else f"{tags[0]}+{len(tags) - 1}autres"


def run(profile, only=None):
    export_dir.mkdir(parents=True, exist_ok=True)
    files = collect_files(profile, only=only)
    if not files:
        print("Aucun fichier trouvé.")
        return

    suffix = ".txt" if profile == "ai" else ".zip"
    scope = _scope_tag(only)
    is_partial_ai = profile == "ai" and only is not None
    z = _resolve_patch_version(suffix, advance=not is_partial_ai)
    version_tag = f"v{VERSION_X}.{VERSION_Y}.{z}"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    # Le scope apparaît dans le nom pour que le fichier s'identifie sans être ouvert
    base_name = f"export_{project_dir.name}_{scope}_{timestamp}_{version_tag}"

    if profile == "ai":
        excluded = None
        if only is not None:
            included = set(files)
            excluded = [f for f in collect_files("ai") if f not in included]
        export_ai(files, base_name, _get_git_info(), excluded=excluded)
    else:
        export_backup(files, base_name)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Export du projet (IA ou sauvegarde).")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--ai", action="store_const", dest="profile", const="ai",
                       help="Fichier .txt curé pour communication à une IA (défaut).")
    group.add_argument("--backup", action="store_const", dest="profile", const="backup",
                       help="Archive .zip complète pour sauvegarde hors git.")
    parser.set_defaults(profile="ai")
    parser.add_argument(
        "--only", action="append", default=None,
        help="Limiter l'export à un ou plusieurs chemins (répétable, ou séparés par des "
             "virgules), ex. --only app/domains/heatwave. En profil --ai, CLAUDE.md et "
             "docs/ (CODEMAP, CONVENTIONS) restent toujours inclus.",
    )
    args = parser.parse_args()
    only = None
    if args.only:
        only = [seg.strip() for item in args.only for seg in item.split(",") if seg.strip()]
    run(args.profile, only=only)
