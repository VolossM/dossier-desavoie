#!/usr/bin/env python3
"""
init_git.py — Initialisation Git dans Downloads et lancement de setup.py
Affaire DeSavoie / Chicherit

Ce script est le seul point d'entrée manuel. Il :
  1. Vérifie les prérequis (Python 3.8+, git)
  2. Se place dans le dossier Downloads de l'utilisateur courant
  3. Crée le .gitignore qui exclut tout sauf les fichiers du projet
  4. Initialise le dépôt Git local (idempotent)
  5. Détecte la version la plus récente de setup.py dans Downloads
     (Windows peut l'avoir renommé "setup (1).py", "setup (2).py"…)
  6. Délègue à setup.py pour la création du dépôt GitHub et le push initial

Usage : double-clic sur init_git.py depuis n'importe quel emplacement.
Prérequis : Git installé (https://git-scm.com/downloads), Python 3.8+.
"""
__version__ = "2.0"

import os, sys, re, subprocess, traceback
from pathlib import Path

def _amorce(nom="dsv_commun"):
    """Charge le socle commun (seul code duplique entre scripts). Ordre : `__version__`
    du corps du fichier, puis numero Windows " (N)", puis horodatage."""
    import importlib.util
    if nom in sys.modules:
        return sys.modules[nom]
    vus, c = set(), []
    for d in (Path(__file__).resolve().parent, Path.home() / "Downloads"):
        for p in (d.glob(nom + "*.py") if d.is_dir() else ()):
            m = re.fullmatch(re.escape(nom) + r"(?: \((\d+)\))?\.py", p.name)
            if not m or p.resolve() in vus:
                continue
            vus.add(p.resolve())
            tete = p.read_text(encoding="utf-8", errors="replace")[:8192]
            v = re.search(r"^__version__\s*=\s*[\"']([\d.]+)[\"']", tete, re.M)
            c.append((tuple(int(x) for x in v.group(1).split(".") if x) if v else (),
                      int(m.group(1) or 0), p.stat().st_mtime, p))
    if not c:
        sys.exit(f"ERREUR : {nom}.py introuvable a cote de {Path(__file__).name} "
                 f"ni dans {Path.home() / 'Downloads'}.")
    spec = importlib.util.spec_from_file_location(nom, max(c)[3])
    mod = importlib.util.module_from_spec(spec)
    sys.modules[nom] = mod
    spec.loader.exec_module(mod)
    return mod


commun = _amorce()
from dsv_commun import R, B, RED, GRN, YLW, CYN, DIM, h, ok, warn, info, fail

def run(cmd, cwd=None, check=True):
    """Commande executee dans Downloads (voir dsv_commun.run)."""
    return commun.run(cmd, cwd=cwd or DOWNLOADS, check=check)

# ─────────────────────────────────────────────
# Corps principal
# ─────────────────────────────────────────────
DOWNLOADS = Path.home() / "Downloads"

def main():
    # 1. Prérequis
    h("Verification des prerequis")
    if sys.version_info < (3, 8):
        fail("Python 3.8+ requis.")
    ok(f"Python {sys.version_info.major}.{sys.version_info.minor}")

    if commun.find_git() is None:
        fail("Git non trouve. Installez Git : https://git-scm.com/downloads")
    ok(f"{run('git --version', cwd=Path.home()).stdout.strip()}  (chemin : {commun.git()})")

    if not DOWNLOADS.exists():
        fail(f"Dossier Downloads introuvable : {DOWNLOADS}")
    ok(f"Downloads : {DOWNLOADS}")

    # 2. .gitignore
    h("Configuration .gitignore")
    gi = DOWNLOADS / ".gitignore"
    if gi.exists():
        warn(".gitignore existant — remplacement.")
    gi.write_text(commun.GITIGNORE, encoding="utf-8")
    ok(".gitignore ecrit (exclusion totale de Downloads sauf fichiers projet).")

    # 3. Git init (idempotent)
    h("Initialisation Git locale")
    git_dir = DOWNLOADS / ".git"
    if git_dir.exists():
        warn(".git existant — reutilisation (idempotent).")
    else:
        run("git init -b main")
        ok("git init -b main")

    # Credential Manager Windows : desactiver pour eviter l'interference
    run('git config credential.helper ""')
    ok("Credential Manager desactive pour ce depot.")

    # 4. Détection de setup.py (avec versionning Windows)
    h("Detection de setup.py")
    # Empreinte REPO_NAME : écarte un setup.py homonyme d'un autre projet.
    setup_path, _ = commun.meilleure_version(
        "setup.py", [DOWNLOADS], empreinte='REPO_NAME        = "dossier-desavoie"')
    if setup_path is None:
        commun.diagnostic("detection-setup", dossier=str(DOWNLOADS),
                          candidats=sorted(p.name for p in DOWNLOADS.glob("setup*.py")))
        fail("setup.py du projet introuvable dans Downloads. Telechargez-le d'abord.")

    if setup_path.name != "setup.py":
        # Copier la version détectée sous le nom canonique, sous garde anti-corruption
        canon = DOWNLOADS / "setup.py"
        warn(f"Version trouvee : {setup_path.name} — copie vers setup.py canonique.")
        copie, motif = commun.copier_garde(setup_path, canon)
        if not copie:
            fail(f"{setup_path.name} refuse ({motif}) — setup.py en place conserve.")
        setup_path = canon
    ok(f"setup.py : {setup_path}")

    # 5. Lancer setup.py
    h("Lancement de setup.py")
    info("Le script setup.py va prendre le relais pour :")
    info("  - Authentification GitHub (token)")
    info("  - Creation du depot distant")
    info("  - Commit initial et push")
    info("  - Activation de GitHub Pages")
    print()

    result = subprocess.run(
        [sys.executable, str(setup_path)],
        cwd=str(DOWNLOADS)
    )
    # setup.py gère lui-même sa pause finale — on ne ré-affiche pas d'erreur


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        pass
    except Exception as exc:
        print(f"\n  {RED}{B}Erreur inattendue :{R}", flush=True)
        traceback.print_exc(file=sys.stdout)
        commun.diagnostic("init-git-exception", exc=exc)
    finally:
        commun.pause_finale("init_git.py termine. Appuyez sur Entree pour fermer...")
