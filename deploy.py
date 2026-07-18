#!/usr/bin/env python3
"""
deploy.py — Deploiement versionne du dossier DeSavoie/Chicherit
Usage :
  python deploy.py                  detecte versions, met a jour manifest, push GitHub
  python deploy.py --local          serveur HTTP local uniquement (port 8080)
  python deploy.py --dry-run        simule sans push ni modification
  python deploy.py -m "commentaire" message de commit personnalise
  python deploy.py --hash-only      recalcule les hashes sans push

Strategie de versionnage :
  Windows ajoute " (1)", " (2)"... aux fichiers telecharges en double.
  Ce script detecte le numero le plus eleve disponible dans ~/Downloads,
  copie le fichier sous le nom canonique, calcule son SHA-256,
  met a jour manifest.json, puis git add/commit/push.
"""

import os, sys, re, json, hashlib, shutil, subprocess, argparse
import http.server, threading, time, webbrowser, traceback
from datetime import datetime, timezone
from pathlib import Path

# ─────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────
SCRIPT_DIR   = Path(__file__).parent.resolve()
DATA_DIR     = SCRIPT_DIR / "data"
MANIFEST     = SCRIPT_DIR / "manifest.json"
DOWNLOADS    = Path(os.path.expanduser("~")) / "Downloads"
# Canoniques promus par le pipeline (promote_seeds) — source autoritative des
# seeds enrichis. Voir find_latest().
DATA_DESAVOIE = DOWNLOADS / "data_desavoie"
LOCAL_PORT   = 8080

def find_git():
    """
    Cherche git dans le PATH puis dans les emplacements standard Windows.
    Nécessaire en double-clic : Python hérite d'un PATH restreint qui peut
    exclure Git même s'il est installé.
    """
    candidates = [
        "git",
        r"C:\Program Files\Git\cmd\git.exe",
        r"C:\Program Files (x86)\Git\cmd\git.exe",
        str(Path.home() / "AppData" / "Local" / "Programs" / "Git" / "cmd" / "git.exe"),
        str(Path.home() / "scoop" / "apps" / "git" / "current" / "cmd" / "git.exe"),
    ]
    for c in candidates:
        r = subprocess.run(
            f'"{c}" --version', shell=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        if r.returncode == 0:
            return c
    return None

GIT = find_git() or "git"  # résolu au chargement du module

# ─────────────────────────────────────────────
# Console helpers
# ─────────────────────────────────────────────
R="\033[0m"; B="\033[1m"; RED="\033[31m"; GRN="\033[32m"
YLW="\033[33m"; CYN="\033[36m"; DIM="\033[2m"

def h(t):    print(f"\n{B}{CYN}> {t}{R}")
def ok(t):   print(f"  {GRN}ok{R}  {t}")
def warn(t): print(f"  {YLW}!{R}   {t}")
def info(t): print(f"  {DIM}{t}{R}")
def fail(t): print(f"\n  {RED}{B}ERREUR :{R} {t}"); sys.exit(1)

def run(cmd, cwd=None, check=True):
    full_cmd = cmd.replace("git ", f'"{GIT}" ', 1) if cmd.startswith("git ") else cmd
    proc = subprocess.Popen(
        full_cmd, shell=True, cwd=cwd or SCRIPT_DIR,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    raw_out, raw_err = proc.communicate()
    r_stdout = raw_out.decode("utf-8", errors="replace")
    r_stderr = raw_err.decode("utf-8", errors="replace")

    class Result:
        def __init__(self, rc, out, err):
            self.returncode = rc; self.stdout = out; self.stderr = err

    r = Result(proc.returncode, r_stdout, r_stderr)
    if check and r.returncode != 0:
        print(f"  stdout : {r.stdout.strip()}")
        print(f"  stderr : {r.stderr.strip()}")
        fail(f"Commande echouee : {cmd}")
    return r

def sha256(path):
    h_ = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h_.update(chunk)
    return h_.hexdigest()

# ─────────────────────────────────────────────
# Empreintes textuelles des fichiers du projet
# ─────────────────────────────────────────────
# Chaîne unique présente dans CHAQUE fichier légitime du projet.
# find_best() ne retient un candidat que si son contenu contient
# cette empreinte — protège contre l'écrasement par un fichier
# homonyme d'un autre projet, quelle que soit la numérotation Windows.
FINGERPRINTS = {
    "index.html":       "Affaire DeSavoie / Chicherit — Frise",
    "manifest.json":    "affaire-desavoie-chicherit",
    "deploy.py":        "Deploiement versionne du dossier DeSavoie",
    "setup.py":         "REPO_NAME        = \"dossier-desavoie\"",
    "init_git.py":      "dossier-desavoie",
    # Fichiers JSONL : tous portent un champ "axis" propre au projet
    ".ftm.jsonl":       '"schema"',   # empreinte générique pour tous les JSONL
}

def fingerprint_for(name):
    """Retourne l'empreinte attendue pour un nom de fichier donné."""
    if name in FINGERPRINTS:
        return FINGERPRINTS[name]
    for suffix_key, fp in FINGERPRINTS.items():
        if suffix_key.startswith(".") and name.endswith(suffix_key):
            return fp
    return None

def file_matches(path, fingerprint):
    """Vérifie que le fichier contient l'empreinte (lecture partielle, max 8 Ko)."""
    if fingerprint is None:
        return True   # pas d'empreinte définie = on accepte sans vérification
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            head = f.read(8192)
        return fingerprint in head
    except OSError:
        return False

# ─────────────────────────────────────────────
# Détection robuste : version Windows + empreinte
# ─────────────────────────────────────────────
# Seuil de réduction de taille déclenchant un avertissement (20 %)
SIZE_SHRINK_THRESHOLD = 0.20

# Seeds curatés par le pipeline (promote_seeds.SEED_FILENAMES). Pour ceux-ci, le
# canonique data_desavoie/<nom> est autoritative (déjà sélectionné + fusionné :
# max-total + récupération forensique courtName). On lui fait confiance.
PROMOTED_SEEDS = {
    "desavoie_groupe.ftm.jsonl",
    "zimmermann.ftm.jsonl",
    "victimes_mch.ftm.jsonl",
    "procedures.ftm.jsonl",
    "reseau_goldberg.ftm.jsonl",
    "shams_indonesie.ftm.jsonl",
    "sainte_foy_consolidated.ftm.jsonl",
}

def find_latest(canonical_path):
    """
    Sélectionne la version d'un fichier dans Downloads / dossier courant.

    Trois tests successifs, avec avertissements explicites en cas de divergence :

    Test A — numéro de version Windows le plus élevé (critère naïf).
    Test B — filtrage par empreinte textuelle (critère de contenu).
    Test C — comparaison de taille entre le candidat retenu et le candidat
              du test A : si la version empreinte-valide est significativement
              plus petite (> SIZE_SHRINK_THRESHOLD), l'utilisateur est averti.

    Si A et B désignent des fichiers différents → avertissement de divergence.
    Retourne (chemin, numéro) du meilleur candidat validé, ou (None, 0).
    """
    name   = canonical_path.name

    # Seed curaté par le pipeline : le canonique promu (data_desavoie/<nom>) est
    # autoritative — on le publie directement, sans re-deviner via les numéros
    # Windows. Corrige la déconnexion historique où deploy ignorait la sortie du
    # pipeline (et régressait ex. zimmermann à un vieux fichier sans courtName).
    if name in PROMOTED_SEEDS:
        pipeline_canonical = DATA_DESAVOIE / name
        if pipeline_canonical.is_file() and pipeline_canonical.stat().st_size > 0:
            return pipeline_canonical, 0

    suffix = canonical_path.suffix
    stem   = canonical_path.stem
    fp     = fingerprint_for(name)

    # Collecte de tous les candidats (base + toutes versions numérotées Windows)
    # Scan réel du répertoire — pas de borne supérieure sur le numéro de version.
    candidates = []
    for search_dir in [DOWNLOADS, canonical_path.parent]:
        if not search_dir.exists():
            continue
        base = search_dir / name
        if base.exists():
            candidates.append((base, 0))
        # Parcourir le répertoire et extraire tous les " (N)" pour ce stem+suffix
        try:
            for entry in search_dir.iterdir():
                m = re.fullmatch(
                    re.escape(stem) + r" \((\d+)\)" + re.escape(suffix),
                    entry.name,
                )
                if m:
                    candidates.append((entry, int(m.group(1))))
        except OSError:
            pass

    if not candidates:
        return None, 0

    # ── Test A : candidat par numéro de version le plus élevé ──
    best_by_version = max(candidates, key=lambda x: (x[1], x[0].stat().st_size))

    # ── Test B : filtrage par empreinte ──
    valid = [(p, n) for p, n in candidates if file_matches(p, fp)]

    if not valid:
        # Aucun candidat ne porte l'empreinte du projet
        warn(f"{name} : {len(candidates)} candidat(s) trouve(s) — "
             f"aucun ne contient l'empreinte du projet DeSavoie.")
        warn(f"  → Candidat ignoré : {best_by_version[0].name} (v{best_by_version[1]}, "
             f"{best_by_version[0].stat().st_size:,} o)")
        warn(f"  Le fichier en production n'est PAS mis a jour.")
        return None, 0

    # Meilleur candidat validé par empreinte
    best_by_fp = max(valid, key=lambda x: (x[1], x[0].stat().st_size))

    # ── Divergence A ≠ B ──
    if best_by_version[0] != best_by_fp[0]:
        warn(f"{name} : DIVERGENCE entre version et empreinte :")
        warn(f"  Numéro le plus élevé : {best_by_version[0].name} "
             f"(v{best_by_version[1]}, {best_by_version[0].stat().st_size:,} o) "
             f"— empreinte INVALIDE (autre projet ?)")
        warn(f"  Sélectionné par empreinte : {best_by_fp[0].name} "
             f"(v{best_by_fp[1]}, {best_by_fp[0].stat().st_size:,} o)")

    # ── Test C : réduction de taille suspecte ──
    # Comparer la version retenue avec l'existant en production (si présent)
    ref_size = None
    if canonical_path.exists():
        ref_size = canonical_path.stat().st_size
    elif best_by_version[0] != best_by_fp[0]:
        # Pas de fichier en production : comparer avec le candidat A
        ref_size = best_by_version[0].stat().st_size

    if ref_size and ref_size > 0:
        new_size  = best_by_fp[0].stat().st_size
        shrinkage = (ref_size - new_size) / ref_size
        if shrinkage > SIZE_SHRINK_THRESHOLD:
            warn(f"{name} : taille en baisse de {shrinkage:.0%} "
                 f"({ref_size:,} o → {new_size:,} o) — vérifiez le contenu.")

    return best_by_fp

# ─────────────────────────────────────────────
# Serveur local
# ─────────────────────────────────────────────
def serve_local():
    os.chdir(SCRIPT_DIR)
    class QuietHandler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a): pass
        def log_request(self, *a): pass

    with http.server.HTTPServer(("", LOCAL_PORT), QuietHandler) as httpd:
        url = f"http://localhost:{LOCAL_PORT}/"
        print(f"\n  {B}Serveur local demarre{R}")
        print(f"  URL    : {CYN}{url}{R}")
        print(f"  Dossier: {SCRIPT_DIR}")
        print(f"  {DIM}Ctrl+C pour arreter{R}\n")
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n  Serveur arrete.")

# ─────────────────────────────────────────────
# Config deploy
# ─────────────────────────────────────────────
def load_config():
    p = SCRIPT_DIR / ".deploy_config.json"
    if p.exists():
        with open(p) as f:
            return json.load(f)
    return {}

# ─────────────────────────────────────────────
# Pipeline principal
# ─────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Deploiement dossier DeSavoie")
    parser.add_argument("-m", "--message",   default="", help="Message de commit")
    parser.add_argument("--local",     action="store_true")
    parser.add_argument("--dry-run",   action="store_true")
    parser.add_argument("--hash-only", action="store_true")
    parser.add_argument("--no-open",   action="store_true")
    args = parser.parse_args()

    if args.local:
        serve_local()
        return

    h("Resolution des fichiers racine  (index.html, manifest.json)")

    # index.html et manifest.json ne figurent pas dans manifest["files"] mais
    # peuvent exister en version numerotée dans Downloads (index (1).html, etc.).
    # On les résout ici avant toute autre opération.
    ROOT_FILES = ["index.html", "manifest.json"]
    for rname in ROOT_FILES:
        canon = SCRIPT_DIR / rname
        found, ver_n = find_latest(canon)
        if found is not None and found != canon and ver_n > 0:
            # Pour manifest.json : préserver les champs d'intégrité (sha256, version,
            # size, deployed) calculés lors du déploiement précédent, afin d'éviter
            # de repasser en statut "non vérifié" à chaque import depuis Downloads.
            if rname == "manifest.json" and canon.exists():
                try:
                    old_manifest = json.loads(canon.read_text(encoding="utf-8"))
                    new_manifest = json.loads(found.read_text(encoding="utf-8"))
                    old_by_path  = {e.get("path"): e for e in old_manifest.get("files", [])}
                    for entry in new_manifest.get("files", []):
                        old = old_by_path.get(entry.get("path"))
                        if not old:
                            continue
                        # Recopier les champs d'intégrité si absents du nouveau manifest
                        for field in ("sha256", "version", "size", "deployed"):
                            if field not in entry and field in old:
                                entry[field] = old[field]
                    # Préserver _last_deploy
                    if "_last_deploy" not in new_manifest and "_last_deploy" in old_manifest:
                        new_manifest["_last_deploy"] = old_manifest["_last_deploy"]
                    canon.write_text(
                        json.dumps(new_manifest, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                    ok(f"{rname} : version {ver_n} fusionnee depuis {found.name} (sha256 preserves)")
                except Exception as exc:
                    warn(f"{rname} : fusion impossible ({exc}) — copie brute.")
                    shutil.copy2(found, canon)
                    ok(f"{rname} : version {ver_n} copiee depuis {found.name}")
            else:
                shutil.copy2(found, canon)
                ok(f"{rname} : version {ver_n} copiee depuis {found.name}")
        elif found == canon:
            info(f"{rname} : deja a jour (base).")
        else:
            info(f"{rname} : non trouve dans Downloads — conserve tel quel.")

    h("Detection des versions  (Downloads puis dossier data)")

    if not MANIFEST.exists():
        fail(f"manifest.json introuvable dans {SCRIPT_DIR}")
    with open(MANIFEST, encoding="utf-8") as f:
        manifest_data = json.load(f)

    files = manifest_data.get("files", [])
    updates = []
    rows = []

    ST_OK  = "conforme"
    ST_NEW = "nouvelle version"
    ST_UNK = "non verifie"
    ST_MIS = "absent"

    for entry in files:
        rel   = entry.get("path", "")
        canon = SCRIPT_DIR / rel
        found, ver_n = find_latest(canon)

        if found is None:
            rows.append((canon.name, "—", ST_MIS, "—", ""))
            continue

        if not args.dry_run and found != canon:
            canon.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(found, canon)

        target  = found if args.dry_run else canon
        h_val   = sha256(target)
        size    = target.stat().st_size
        expected = entry.get("sha256", "")

        if not expected:      status = ST_UNK
        elif h_val == expected: status = ST_OK
        else:                 status = ST_NEW

        ver_label = f"v{ver_n}" if ver_n > 0 else "base"
        rows.append((canon.name, ver_label, status, f"{size:,} o", h_val))
        updates.append({"path": rel, "sha256": h_val, "version": ver_n, "size": size})

    # Tableau de statut
    w = max((len(r[0]) for r in rows), default=20) + 2
    print(f"\n  {'Fichier':<{w}} {'Ver.':<8} {'Integrite':<18} Taille")
    print(f"  {'─'*w} {'─'*8} {'─'*18} {'─'*12}")
    for name, ver, status, size, _ in rows:
        c = GRN if status == ST_OK else YLW if status in (ST_NEW, ST_UNK) else RED
        print(f"  {name:<{w}} {ver:<8} {c}{status:<18}{R} {size}")

    if args.dry_run:
        info("Mode dry-run : aucune modification.")
        return

    # Mettre a jour manifest.json
    changed = False
    umap = {u["path"]: u for u in updates}
    for entry in files:
        u = umap.get(entry.get("path", ""))
        if u and entry.get("sha256", "") != u["sha256"]:
            entry["sha256"]   = u["sha256"]
            entry["version"]  = u["version"]
            entry["size"]     = u["size"]
            entry["deployed"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            changed = True
    manifest_data["_last_deploy"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with open(MANIFEST, "w", encoding="utf-8") as f:
        json.dump(manifest_data, f, ensure_ascii=False, indent=2)
    if changed:
        ok("manifest.json mis a jour.")
    else:
        info("manifest.json inchange.")

    if args.hash_only:
        return

    # ── Nettoyage des fichiers non reconnus à la racine ──────────────────
    h("Nettoyage des fichiers non reconnus")

    # Fichiers autorisés à la racine du dépôt
    ALLOWED_ROOT = {
        "index.html", "manifest.json", "deploy.py", "setup.py", "init_git.py",
        ".gitignore", ".deploy_config.json", "README.md", "readme.md",
    }
    # Interroger Git directement plutôt qu'itérer sur le système de fichiers :
    # évite de scanner l'intégralité de Downloads si le dépôt y est hébergé.
    tracked_output = run("git ls-files", check=False).stdout.strip()
    strangers = []
    for line in tracked_output.splitlines():
        line = line.strip()
        if not line or "/" in line:   # ignorer les fichiers dans des sous-dossiers
            continue
        if line.startswith("."):
            continue
        if line not in ALLOWED_ROOT:
            strangers.append(SCRIPT_DIR / line)

    if strangers:
        print()
        print(f"  {YLW}{B}Fichiers non reconnus suivis par Git :{R}")
        for s in strangers:
            print(f"    {RED}\u2022{R} {s.name}  ({s.stat().st_size:,} o)")
        print()
        print(f"  Ces fichiers sont publics sur GitHub mais n'appartiennent")
        print(f"  pas au projet DeSavoie.")
        print()
        try:
            rep = input(f"  Retirer du suivi Git et ajouter au .gitignore ? [o/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            rep = "n"
        if rep == "o":
            gitignore = SCRIPT_DIR / ".gitignore"
            existing  = gitignore.read_text(encoding="utf-8") if gitignore.exists() else ""
            additions = []
            for s in strangers:
                run(f'git rm --cached "{s.name}"', check=False)
                if s.name not in existing:
                    additions.append(s.name)
                ok(f"{s.name} retire du suivi Git.")
            if additions:
                with open(gitignore, "a", encoding="utf-8") as f:
                    f.write("\n# Fichiers non-DeSavoie exclus automatiquement\n")
                    for a in additions:
                        f.write(f"{a}\n")
                ok(f".gitignore mis a jour ({len(additions)} entree(s) ajoutee(s)).")
        else:
            info("Nettoyage annule — fichiers conserves tels quels.")
    else:
        ok("Aucun fichier non reconnu a la racine.")

    # Git
    h("Git")
    if not run("git status --porcelain", check=False).stdout.strip():
        ok("Aucun changement — deja a jour.")
        cfg = load_config()
        if cfg.get("pages_url"):
            print(f"\n  URL : {CYN}{cfg['pages_url']}{R}\n")
        return

    run("git add -A")

    ts  = datetime.now().strftime("%Y-%m-%d %H:%M")
    new_files = [r[0] for r in rows if r[2] == ST_NEW]
    auto_msg  = f"deploy {ts}"
    if new_files:
        auto_msg += f" — {', '.join(new_files[:3])}"
        if len(new_files) > 3:
            auto_msg += f" +{len(new_files)-3}"
    msg = args.message or auto_msg
    run(f'git commit -m "{msg}"')
    ok(f"Commit : {msg}")

    # Push
    cfg = load_config()
    remote = cfg.get("remote_auth") or "origin"
    push_cmd = f"git push {remote} main" if remote != "origin" else "git push origin main"
    pr = run(push_cmd, check=False)
    if pr.returncode != 0:
        warn(f"Push echoue : {pr.stderr.strip()}")
        warn("Relancez manuellement : git push origin main")
    else:
        ok("Push effectue.")

    pages_url = cfg.get("pages_url", "")
    if pages_url:
        print(f"\n  {B}Deploiement termine{R}")
        print(f"  URL : {CYN}{pages_url}{R}")
        print(f"  {DIM}(mise a jour Pages dans ~30 secondes){R}\n")
        if not args.no_open:
            time.sleep(2)
            webbrowser.open(pages_url)
    else:
        print(f"\n  {B}Deploiement termine.{R} Lancez setup.py pour configurer GitHub Pages.\n")

if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        pass
    except Exception:
        # [PATCH_TRACE_VISIBLE_v1] (rev.32 B4 du 12/06/2026)
        # Forcer traceback.print_exc() sur sys.stdout pour que la trace
        # apparaisse sur le même flux que le print() du label. Le BAT
        # redirige stdout vers le log mais pas nécessairement stderr —
        # sans ce file=sys.stdout, on observait "Erreur inattendue :" sans
        # trace (run du 11/06/2026, [DEPLOY] phase Detection des versions).
        # flush=True garantit que le label soit écrit avant la trace.
        print(f"\n  {RED}{B}Erreur inattendue :{R}", flush=True)
        traceback.print_exc(file=sys.stdout)
        sys.stdout.flush()
    finally:
        # Pas de pause en mode --local (le serveur tourne en boucle, Ctrl+C pour quitter)
        # [PATCH_NO_PAUSE_v1] (rev.32 B4 bis) Skip input() bloquant si pipeline
        # non-interactif (variable PIPELINE_NO_PAUSE=1 ou stdin non-TTY).
        if "--local" not in sys.argv and not (os.environ.get("PIPELINE_NO_PAUSE") or not sys.stdin.isatty()):
            try:
                input("\n  Appuyez sur Entree pour fermer...")
            except Exception:
                pass
