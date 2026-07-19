"""Export du projet sous trois profils complémentaires.

    --ai       (défaut) : un unique .txt curé, destiné à être communiqué à une IA.
                          Inclut le code ET la documentation (.md : CLAUDE, CODEMAP,
                          CONVENTIONS), précédé d'un manifeste (git, version, sommaire,
                          estimation de tokens). Exclut tools/, les scripts de lancement
                          (.bat/.command) et .gitattributes (bruit, déjà couverts par
                          docs/COMMANDES.md).
    --canicule          : export IA limité à l'app canicule + socle commun utile.
    --snow / --neige    : export IA limité à l'app neige + socle commun utile.
    --outline           : dossier d'architecture compact pour réfléchir avec un chat IA.
                          Conserve la documentation structurante et remplace le code Python
                          par ses imports, constantes, classes et signatures documentées.
                          Combinable avec --canicule, --snow/--neige ou --only.
    --backup            : un .zip complet et restaurable, destiné à la sauvegarde hors git.
                          Inclut TOUT le code source (y compris tools/ et docs/), hors
                          données (data/, legacy/) et binaires. Rotation des N plus récents.
    --only <chemins>    : restreint la collecte à un sous-ensemble de chemins (répétable ou
                          séparés par des virgules), pour réduire le volume envoyé à l'IA sur
                          une question ciblée. Le nom du fichier reflète le périmètre exporté ;
                          le manifeste liste les fichiers laissés hors périmètre. N'avance pas
                          le numéro de patch Z (un sous-ensemble du même commit n'est pas un
                          nouvel export).

Les trois profils partagent la même collecte de fichiers, paramétrée par profil : on ne
duplique jamais la logique de parcours. Export/ est gitignoré → aucun artefact n'est commité.
"""

import argparse
import ast
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


# Extraire les versions depuis les deux points d'entrée (sources de vérité, lues par regex)
def _get_version(relative_path, variable, fallback):
    entrypoint = project_dir / relative_path
    with open(entrypoint, "r", encoding="utf-8") as f:
        match = re.search(rf'{variable}\s*=\s*"(\d+)\.(\d+)\.(\d+)"', f.read())
        if match:
            return int(match.group(1)), int(match.group(2)), int(match.group(3))
        return fallback


APP_VERSION = _get_version("meteo_app.py", "APP_VERSION", (2, 0, 0))
SNOW_APP_VERSION = _get_version("snow_app.py", "SNOW_APP_VERSION", (0, 1, 0))

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
    ".pytest_cache",
    "golden",  # tools/golden/ : références de non-régression, régénérées localement
}

# Dossiers ignorés dans les profils IA (--ai et --outline)
EXCLUDED_DIRS_AI = {"tools"}

# Fichiers ignorés dans les profils IA (bruit / quasi-doublon pour l'analyse)
EXCLUDED_FILES_AI = {"Forecast_legacy.py"}

# Extensions/-noms de fichiers texte inclus, communes aux trois profils
INCLUDED_EXTENSIONS_COMMON = {
    ".py", ".txt", ".md", ".yml", ".yaml", ".json", ".toml", ".cfg", ".ini", ".gitignore",
}

# Extensions supplémentaires réservées au --backup : scripts de lancement et métadonnées
# git, faible densité d'information pour une IA (déjà couverts par docs/COMMANDES.md) mais
# nécessaires à une sauvegarde fidèle du dépôt.
INCLUDED_EXTENSIONS_BACKUP_EXTRA = {".bat", ".command", ".gitattributes"}

# Documents toujours collectés dans les profils IA même sous --only
AI_ALWAYS_KEEP = {"CLAUDE.md", "docs/CODEMAP.md", "docs/CONVENTIONS.md"}

# Présélections par application pour transmettre un contexte cohérent sans embarquer
# l'autre dashboard. Les chemins communs couvrent les imports partagés, les conventions,
# les dépendances et le workflow qui orchestre les deux pipelines.
AI_APP_COMMON = {
    ".github/workflows/run_forecast.yml",
    "AGENTS.md",
    "CLAUDE.md",
    "README.md",
    "apps/__init__.py",
    "core",
    "docs/CODEMAP.md",
    "docs/COMMANDES.md",
    "docs/CONVENTIONS.md",
    "docs/DESIGN_archivage_pipeline.md",
    "pyrightconfig.json",
    "requirements.txt",
    "tests/test_hot_cold.py",
}

AI_APP_PATHS = {
    "canicule": AI_APP_COMMON | {
        "CHANGELOG.md",
        "Forecast.py",
        "apps/canicule",
        "config.py",
        "fetch_montsouris_vintages.py",
        "fetch_observations.py",
        "fetch_observations_6m.py",
        "forecast_t2m_hd.py",
        "meteo_app.py",
        "run_dual.py",
        "tests/test_heatwave_episode.py",
        "validate_cross_pipeline.py",
    },
    "snow": AI_APP_COMMON | {
        "apps/snow",
        "snow_app.py",
        "tests/test_snow_domain.py",
        "tests/test_snow_observations.py",
        "tests/test_snow_operational_pages.py",
        "tests/test_snow_pipeline.py",
    },
}

# Ces documents donnent la carte et les conventions : ils restent intégraux dans l'outline.
# CLAUDE.md conserve toutes ses règles mais chaque ligne est bornée : les invariants restent
# visibles sans leurs longs développements. Les autres Markdown sont ramenés à leur structure.
OUTLINE_FULL_DOCS = {
    "docs/CODEMAP.md",
    "docs/CONVENTIONS.md",
}

OUTLINE_PURPOSE = """Ce document est destiné à un chat IA chargé d'aider à réfléchir
à l'architecture, aux améliorations et aux optimisations du projet. L'agent de code qui
appliquera ensuite les changements a accès au dépôt complet : ne demande pas ici les corps
de fonctions manquants. Appuie-toi sur la vue d'ensemble, les responsabilités des modules,
les dépendances, la configuration et les invariants pour proposer des pistes structurées.
"""

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


def _resolve_patch_version(suffix, advance, version):
    """Numéro de patch Z pour le couple (X.Y, extension) dans Export/.

    advance=True  → prochain Z disponible (nouvel export plein).
    advance=False → Z déjà utilisé le plus récent, sans avancer : un export partiel IA
                    (--only) sur le même commit ne mérite pas un nouveau numéro de version.
    """
    version_x, version_y, _ = version
    pattern = re.compile(rf"_v{version_x}\.{version_y}\.(\d+)$")
    max_z = -1
    for f in export_dir.iterdir():
        if not f.is_file() or f.suffix != suffix:
            continue
        m = pattern.search(f.stem)
        if m:
            max_z = max(max_z, int(m.group(1)))
    return max_z + 1 if advance else max(max_z, 0)


def collect_files(profile, only=None, app=None):
    """Fichiers texte pertinents pour le profil, triés par chemin.

    profile == "ai"     → exclut tools/ et Forecast_legacy.py (analyse curée).
    profile == "outline"→ même collecte que "ai", contenu condensé à l'écriture.
    profile == "backup" → tout le code source, hors données/binaires (sauvegarde fidèle).
    only                → limite aux chemins (fichier ou dossier) listés, en gardant toujours
                          AI_ALWAYS_KEEP en profil "ai" (contexte minimal indispensable).
    app                 → présélection "canicule" ou "snow" (profils IA/outline).
    """
    if app is not None:
        if profile not in {"ai", "outline"}:
            raise ValueError(
                "Les présélections par application sont réservées au profil IA "
                "et au profil outline."
            )
        if only is not None:
            raise ValueError("Une présélection par application ne se combine pas avec --only.")
        try:
            only = sorted(AI_APP_PATHS[app])
        except KeyError as exc:
            raise ValueError(f"Application inconnue : {app}") from exc

    is_ai_profile = profile in {"ai", "outline"}
    excluded_dirs = EXCLUDED_DIRS_ALWAYS | (EXCLUDED_DIRS_AI if is_ai_profile else set())
    excluded_files = EXCLUDED_FILES_AI if is_ai_profile else set()
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
        if is_ai_profile:
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


def export_ai(files, base_name, git, app_version, version_label, excluded=None):
    """Écrit le .txt curé : manifeste (git/version/sommaire/tokens) puis fichiers balisés.

    excluded, en export ciblé (--only ou application), liste les fichiers du périmètre
    --ai complet laissés hors de ce sous-ensemble, pour que le manifeste s'auto-décrive.
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
        out.write(f"# {version_label:<12}: {'.'.join(map(str, app_version))}\n")
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
            out.write(f"#\n# NON INCLUS ({len(excluded)} fichiers hors périmètre ciblé)\n")
            for f in excluded:
                out.write(f"#   {f.relative_to(project_dir).as_posix()}\n")
        out.write("\n")

        for f in files:
            rel = f.relative_to(project_dir).as_posix()
            lang = _LANG_BY_SUFFIX.get(f.suffix, "")
            out.write(f"===== {rel} =====\n")
            out.write(f"```{lang}\n{contents[f]}\n```\n\n")

    print(f"Export IA   : {txt_path}  (~{approx_tokens:,} tokens)".replace(",", " "))


def _one_line(text, limit=260):
    """Aplatit un texte descriptif et le borne sans couper silencieusement le sens."""
    flat = " ".join((text or "").split())
    return flat if len(flat) <= limit else flat[:limit - 1].rstrip() + "…"


def _short_expr(node, limit=320):
    """Représentation compacte d'une constante/configuration AST, sans corps de code."""
    try:
        value = ast.unparse(node)
    except Exception:
        return f"<{type(node).__name__}>"
    value = " ".join(value.split())
    if len(value) <= limit:
        return value
    if isinstance(node, ast.Dict):
        keys = [_one_line(ast.unparse(k), 50) for k in node.keys[:12] if k is not None]
        tail = ", …" if len(node.keys) > len(keys) else ""
        return f"dict[{len(node.keys)}] (clés: {', '.join(keys)}{tail})"
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return f"{type(node).__name__.lower()}[{len(node.elts)}]: {_one_line(value, limit)}"
    return _one_line(value, limit)


def _function_signature(node, indent=""):
    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    try:
        args = ast.unparse(node.args)
    except Exception:
        args = "…"
    returns = f" -> {_short_expr(node.returns, 100)}" if node.returns else ""
    lines = []
    for decorator in node.decorator_list:
        lines.append(f"{indent}@{_short_expr(decorator, 140)}")
    lines.append(f"{indent}{prefix} {node.name}({args}){returns}")
    doc = ast.get_docstring(node, clean=True)
    if doc:
        lines.append(f'{indent}    """{_one_line(doc, 180)}"""')
    return lines


def _python_outline(content):
    """Carte statique d'un module Python : aucune instruction des fonctions n'est exportée."""
    try:
        tree = ast.parse(content)
    except SyntaxError as exc:
        return f"[Analyse AST impossible : {exc}]"

    lines = []
    module_doc = ast.get_docstring(tree, clean=True)
    if module_doc:
        lines.extend(["Rôle du module:", f"  {_one_line(module_doc, 500)}", ""])

    imports = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module)
    imports = sorted(set(imports))
    if imports:
        lines.extend(["Dépendances:", f"  {', '.join(imports)}", ""])

    constants = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if (
                isinstance(target, ast.Name)
                and target.id.isupper()
                and not target.id.startswith("_")
            ):
                constants.append(f"  {target.id} = {_short_expr(node.value)}")
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if (
                node.target.id.isupper()
                and not node.target.id.startswith("_")
                and node.value is not None
            ):
                constants.append(f"  {node.target.id} = {_short_expr(node.value)}")
    if constants:
        lines.append("Configuration / constantes publiques:")
        lines.extend(constants)
        lines.append("")

    declarations = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not node.name.startswith("_"):
                declarations.extend(_function_signature(node))
        elif isinstance(node, ast.ClassDef):
            bases = ", ".join(_short_expr(base, 100) for base in node.bases)
            declarations.append(f"class {node.name}({bases})" if bases else f"class {node.name}")
            doc = ast.get_docstring(node, clean=True)
            if doc:
                declarations.append(f'    """{_one_line(doc, 180)}"""')
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if not child.name.startswith("_") or child.name == "__init__":
                        declarations.extend(_function_signature(child, indent="    "))
    if declarations:
        lines.append("API / points d'extension:")
        lines.extend(declarations)

    return "\n".join(lines).rstrip() or "(module sans API publique déclarée)"


def _markdown_outline(content):
    """Titres et puces d'un document secondaire, en supprimant les longs paragraphes."""
    kept = []
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or re.match(r"^[-*+]\s+", stripped):
            kept.append(line.rstrip())
    return "\n".join(kept) or "(document sans structure Markdown détectée)"


def _rules_outline(content):
    """Toutes les règles de CLAUDE.md, avec leurs longues justifications bornées."""
    kept = []
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        indent = line[:len(line) - len(line.lstrip())]
        kept.append(indent + _one_line(stripped, 300))
    return "\n".join(kept)


def _yaml_outline(content):
    """Structure opérationnelle d'un YAML : jobs, étapes, actions et cadences."""
    kept = []
    for line in content.splitlines():
        stripped = line.strip()
        indent = len(line) - len(line.lstrip())
        if not stripped or stripped.startswith("#"):
            continue
        if indent <= 2 or re.match(r"^(name|uses|cron):", stripped):
            kept.append(line.rstrip())
    return "\n".join(kept) or "(structure YAML vide)"


def _outline_content(path, content):
    rel = path.relative_to(project_dir).as_posix()
    if rel in OUTLINE_FULL_DOCS:
        return content, "contenu intégral"
    if rel == "CLAUDE.md":
        return _rules_outline(content), "règles complètes condensées"
    if path.suffix == ".py":
        return _python_outline(content), "structure Python (corps omis)"
    if path.suffix == ".md":
        return _markdown_outline(content), "structure Markdown"
    if path.suffix in {".yml", ".yaml"}:
        return _yaml_outline(content), "structure YAML"
    if len(content) <= 8000:
        return content, "contenu intégral (fichier court)"
    return _one_line(content, 2000), "aperçu tronqué"


def export_outline(files, base_name, git, app_version, version_label, excluded=None):
    """Écrit un dossier d'architecture compact, orienté réflexion et optimisation."""
    txt_path = export_dir / f"{base_name}.txt"
    rendered = {}
    source_sizes = {}
    modes = {}
    for f in files:
        try:
            source = f.read_text(encoding="utf-8")
            rendered[f], modes[f] = _outline_content(f, source)
            source_sizes[f] = len(source)
        except Exception as exc:
            rendered[f] = f"[Erreur lecture/analyse : {exc}]"
            modes[f] = "erreur"
            source_sizes[f] = 0

    total_chars = sum(len(c) for c in rendered.values())
    source_chars = sum(source_sizes.values())
    approx_tokens = total_chars // 4
    source_tokens = source_chars // 4

    with open(txt_path, "w", encoding="utf-8", newline="\n") as out:
        out.write(f"# ===== OUTLINE PROJET {project_dir.name} =====\n")
        out.write(f"# Date        : {datetime.now():%Y-%m-%d %H:%M:%S}\n")
        out.write(f"# Branche     : {git['branch']}\n")
        out.write(f"# Commit      : {git['commit']} \"{git['subject']}\"\n")
        out.write(f"# {version_label:<12}: {'.'.join(map(str, app_version))}\n")
        out.write(
            f"# Fichiers    : {len(files)} | Source : ~{source_tokens:,} tokens "
            f"| Outline : ~{approx_tokens:,} tokens\n".replace(",", " ")
        )
        out.write("\n# OBJECTIF DE CE DOCUMENT\n")
        out.write(OUTLINE_PURPOSE.strip() + "\n")
        out.write("\n# ARBORESCENCE ET NIVEAU DE DÉTAIL\n")
        for f in files:
            rel = f.relative_to(project_dir).as_posix()
            out.write(f"- {rel} — {modes[f]}\n")
        if excluded:
            out.write(f"\n# NON INCLUS ({len(excluded)} fichiers hors périmètre ciblé)\n")
            for f in excluded:
                out.write(f"- {f.relative_to(project_dir).as_posix()}\n")
        out.write("\n")

        for f in files:
            rel = f.relative_to(project_dir).as_posix()
            out.write(f"===== {rel} [{modes[f]}] =====\n")
            out.write(rendered[f] + "\n\n")

    print(
        f"Outline IA  : {txt_path}  (~{approx_tokens:,} tokens, "
        f"source ~{source_tokens:,})".replace(",", " ")
    )


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


def run(profile, only=None, app=None):
    export_dir.mkdir(parents=True, exist_ok=True)
    files = collect_files(profile, only=only, app=app)
    if not files:
        print("Aucun fichier trouvé.")
        return

    suffix = ".txt" if profile in {"ai", "outline"} else ".zip"
    raw_scope = app or _scope_tag(only)
    scope = f"outline_{raw_scope}" if profile == "outline" else raw_scope
    is_partial_ai = profile == "outline" or (
        profile == "ai" and (only is not None or app is not None)
    )
    app_version = SNOW_APP_VERSION if app == "snow" else APP_VERSION
    version_label = "SNOW_APP_VERSION" if app == "snow" else "APP_VERSION"
    version_x, version_y, _ = app_version
    z = _resolve_patch_version(suffix, advance=not is_partial_ai, version=app_version)
    version_tag = f"v{version_x}.{version_y}.{z}"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    # Le scope apparaît dans le nom pour que le fichier s'identifie sans être ouvert
    base_name = f"export_{project_dir.name}_{scope}_{timestamp}_{version_tag}"

    if profile in {"ai", "outline"}:
        excluded = None
        if only is not None or app is not None:
            included = set(files)
            excluded = [f for f in collect_files("ai") if f not in included]
        exporter = export_outline if profile == "outline" else export_ai
        exporter(files, base_name, _get_git_info(), app_version, version_label,
                 excluded=excluded)
    else:
        export_backup(files, base_name)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Export du projet (IA ou sauvegarde).")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--ai", action="store_const", dest="profile", const="ai",
                       help="Fichier .txt curé pour communication à une IA (défaut).")
    group.add_argument("--outline", action="store_const", dest="profile", const="outline",
                       help="Vue d'architecture compacte, sans corps de fonctions.")
    group.add_argument("--backup", action="store_const", dest="profile", const="backup",
                       help="Archive .zip complète pour sauvegarde hors git.")
    parser.set_defaults(profile="ai")
    scope_group = parser.add_mutually_exclusive_group()
    scope_group.add_argument(
        "--only", action="append", default=None,
        help="Limiter l'export à un ou plusieurs chemins (répétable, ou séparés par des "
             "virgules), ex. --only apps/canicule/app/domains/heatwave. Dans les profils "
             "IA, CLAUDE.md et "
             "docs/ (CODEMAP, CONVENTIONS) restent toujours inclus.",
    )
    scope_group.add_argument(
        "--canicule", action="store_const", dest="app", const="canicule",
        help="Exporter uniquement l'app canicule et le socle commun utile.",
    )
    scope_group.add_argument(
        "--snow", "--neige", action="store_const", dest="app", const="snow",
        help="Exporter uniquement l'app neige et le socle commun utile.",
    )
    args = parser.parse_args()
    if args.profile == "backup" and args.app is not None:
        parser.error(
            "--canicule/--snow/--neige sont réservés aux profils --ai et --outline"
        )
    only = None
    if args.only:
        only = [seg.strip() for item in args.only for seg in item.split(",") if seg.strip()]
    run(args.profile, only=only, app=args.app)
