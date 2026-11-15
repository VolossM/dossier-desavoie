#!/usr/bin/env python3
"""
deploy.py — Deploiement versionne du dossier DeSavoie/Chicherit
Depot : dossier-desavoie. Point d'entree unique recommande : python dsv.py

Usage :
  python deploy.py                  detecte versions, met a jour manifest, push GitHub
  python deploy.py --local          serveur HTTP local uniquement (port 8080)
  python deploy.py --dry-run        simule sans push ni modification
  python deploy.py -m "commentaire" message de commit personnalise
  python deploy.py --hash-only      recalcule les hashes sans push
  python deploy.py --purger-intrus  retire du suivi Git les fichiers etrangers au projet

Aucune question n'est posee en cours d'execution : tout ce qui demande une
decision (publication refusee, fichier etranger, push echoue) est depose dans
la boite aux lettres _dsv/bal (voir dsv_bal.py).

Strategie de versionnage :
  Windows ajoute " (1)", " (2)"... aux fichiers telecharges en double.
  Ce script detecte le numero le plus eleve disponible dans ~/Downloads,
  copie le fichier sous le nom canonique, calcule son SHA-256,
  met a jour manifest.json, puis git add/commit/push.
"""
__version__ = "2.0"

import os, sys, re, json, shutil, argparse
import http.server, threading, time, webbrowser, traceback
from datetime import datetime, timezone
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
from dsv_commun import (R, B, RED, GRN, YLW, CYN, DIM, h, ok, warn, info, fail,
                        sha256, signaler)

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

def run(cmd, cwd=None, check=True, env=None):
    """Commande exécutée dans le dossier de deploy.py (voir dsv_commun.run)."""
    return commun.run(cmd, cwd=cwd or SCRIPT_DIR, check=check, env=env)

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

# Contrôle d'empreinte et comptage d'entités : implémentés dans dsv_commun.
# count_ftm_entities sert de garde anti-perte au déploiement : le pipeline émet
# parfois des .ftm.jsonl de 2 octets (crash writer, résultat vide) dans
# data_desavoie — un simple `st_size > 0` les laisse passer, ce contrôle non.
file_matches       = commun.contient
count_ftm_entities = commun.compter_entites

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
    "coeur_desavoie.ftm.jsonl",     # [2026-07-18] cœur commun : entités partagées ≥2 seeds
                                    # (consolidées par le résolveur NK ; jadis en doublon).
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
            # ── Garde anti-perte (fail-closed) ──────────────────────────────
            # Le retour anticipé « je fais confiance au canonique promu » court-
            # circuite le Test C (réduction de taille) plus bas. Or le pipeline
            # émet parfois des .ftm.jsonl de 2 o dans data_desavoie (crash writer,
            # résultat vide). Sans ce contrôle, deploy publierait ces 2 o par-
            # dessus la production (des Mo), recalculerait le sha256 et pousserait
            # sur GitHub Pages — perte totale, zéro erreur. On compte les ENTITÉS
            # (pas les octets) et on REFUSE de régresser la production.
            n_new, n_bad = count_ftm_entities(pipeline_canonical)
            allow_shrink = os.environ.get("DEPLOY_ALLOW_SHRINK") == "1"
            if n_bad:
                warn(f"{name} : {n_bad} ligne(s) JSONL invalide(s) dans le "
                     f"canonique promu ({pipeline_canonical.name}).")
            if n_new == 0:
                warn(f"{name} : canonique promu SANS ENTITÉ valide "
                     f"({pipeline_canonical.stat().st_size:,} o) — "
                     f"publication REFUSÉE, production conservée.")
                return None, 0
            # [2026-07-21] Référence de production ABSOLUE, en défense de fond.
            # `canonical_path` dérive de SCRIPT_DIR : si deploy est lancé depuis un
            # autre répertoire que la racine (ex. data_desavoie), il pointe vers un
            # arbre inexistant, `exists()` est faux, et TOUTE cette moitié du garde
            # est sautée en silence — on ne protège plus contre la régression, on
            # croit seulement le faire. On vise donc la production réelle, avec repli
            # sur le chemin dérivé si elle est absente.
            _prod_ref = DOWNLOADS / "data" / name
            if not _prod_ref.is_file():
                _prod_ref = canonical_path
            if _prod_ref.exists():
                n_prod, _ = count_ftm_entities(_prod_ref)
                if n_prod > 0:
                    shrink = (n_prod - n_new) / n_prod
                    if shrink > SIZE_SHRINK_THRESHOLD and not allow_shrink:
                        warn(f"{name} : chute d'entités de {shrink:.0%} "
                             f"({n_prod:,} → {n_new:,}) — publication REFUSÉE.")
                        warn(f"  Si ce rétrécissement est voulu : "
                             f"DEPLOY_ALLOW_SHRINK=1 python deploy.py")
                        return None, 0
            return pipeline_canonical, 0
        # Canonique promu absent ou de taille nulle : comportement historique
        # préservé — on retombe sur la résolution par numéro Windows ci-dessous
        # (le fichier de 0 o de data_desavoie n'est de toute façon pas candidat,
        # le scan ne parcourt que Downloads et data/).

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
    parser.add_argument("--purger-intrus", action="store_true",
                        help="Retire du suivi Git les fichiers etrangers au projet")
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
            # Garde anti-corruption avant écrasement (manifest : JSON valide requis).
            accepte, motif = commun.garde_ecrasement(found, canon)
            if not accepte:
                warn(f"{rname} : {found.name} REFUSÉ ({motif}) — version en place conservée.")
                signaler("utilisateur", f"deploy : {rname} refusé",
                         f"{found} n'a pas remplacé {canon} : {motif}.",
                         urgence="action", cle=f"deploy-refus-{rname}")
                continue
            if motif:
                warn(f"{rname} : {motif}.")
            if args.dry_run:
                info(f"{rname} : version {ver_n} disponible ({found.name}) — non copiée (dry-run).")
                continue
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
                    commun.diagnostic("fusion-manifest", exc=exc, source=str(found), cible=str(canon))
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
    ST_REF = "refuse"
    refuses = []
    absents = []

    for entry in files:
        rel   = entry.get("path", "")
        canon = SCRIPT_DIR / rel
        found, ver_n = find_latest(canon)

        if found is None:
            rows.append((canon.name, "—", ST_MIS, "—", ""))
            absents.append(canon.name)
            continue

        if found != canon:
            # Garde anti-régression avant écrasement de la production : mêmes règles
            # que pour les seeds promus (entités comptées, pas octets).
            accepte, motif = commun.garde_ecrasement(
                found, canon, os.environ.get("DEPLOY_ALLOW_SHRINK") == "1")
            if not accepte:
                warn(f"{canon.name} : {found.name} REFUSÉ ({motif}) — production conservée.")
                refuses.append(f"{canon.name} <- {found} : {motif}")
                rows.append((canon.name, f"v{ver_n}" if ver_n > 0 else "base", ST_REF, "—", ""))
                continue
            if motif:
                warn(f"{canon.name} : {motif}.")
            if not args.dry_run:
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

    # Publications refusées ou fichiers introuvables : une décision est attendue.
    # (find_latest renvoie None aussi bien pour « absent » que pour « refusé par
    # la garde des seeds promus » : les avertissements ci-dessus font foi.)
    if refuses or absents:
        signaler("utilisateur", "deploy : fichiers non publiés",
                 "\n".join(["Refusés par la garde anti-régression :"] + refuses
                           + ["Absents ou refusés en amont (voir le journal du run) :"] + absents
                           + ["Si une réduction est voulue : DEPLOY_ALLOW_SHRINK=1 python deploy.py"]),
                 urgence="action", cle="deploy-non-publies")

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
    ALLOWED_ROOT = commun.FICHIERS_RACINE
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
        if args.purger_intrus:
            gitignore = SCRIPT_DIR / ".gitignore"
            existing  = gitignore.read_text(encoding="utf-8") if gitignore.exists() else ""
            additions = []
            for s_ in strangers:
                run(["git", "rm", "--cached", s_.name], check=False)
                if s_.name not in existing:
                    additions.append(s_.name)
                ok(f"{s_.name} retire du suivi Git.")
            if additions:
                with open(gitignore, "a", encoding="utf-8") as f:
                    f.write("\n# Fichiers non-DeSavoie exclus automatiquement\n")
                    for a_ in additions:
                        f.write(f"{a_}\n")
                ok(f".gitignore mis a jour ({len(additions)} entree(s) ajoutee(s)).")
        else:
            warn("Fichiers conserves. Pour les retirer : python deploy.py --purger-intrus")
            signaler("utilisateur", "deploy : fichiers étrangers suivis par Git",
                     "Suivis par Git à la racine sans appartenir au projet : "
                     + ", ".join(x.name for x in strangers)
                     + ". Retrait : python deploy.py --purger-intrus",
                     urgence="action", cle="deploy-intrus")
    else:
        ok("Aucun fichier non reconnu a la racine.")

    # Git
    h("Git")
    cfg = load_config()
    remote = cfg.get("remote_auth") or "origin"

    # Les .jsonl sont stockés sans conversion de fins de ligne (.gitattributes) :
    # réaligner l'index sur les octets réellement hachés dans manifest.json.
    run("git add --renormalize -- data", check=False)
    if run("git status --porcelain", check=False).stdout.strip():
        run("git add -A")
        ts  = datetime.now().strftime("%Y-%m-%d %H:%M")
        new_files = [r[0] for r in rows if r[2] == ST_NEW]
        auto_msg  = f"deploy {ts}"
        if new_files:
            auto_msg += f" — {', '.join(new_files[:3])}"
            if len(new_files) > 3:
                auto_msg += f" +{len(new_files)-3}"
        msg = args.message or auto_msg
        run(["git", "commit", "-m", msg])
        ok(f"Commit : {msg}")
    else:
        ok("Aucun changement local.")

    # Synchronisation : intégrer ce que le dépôt distant a reçu entre-temps
    # (branche fusionnée depuis une session Claude, réponse en boîte aux lettres).
    # Sans cela, le push serait rejeté (non-fast-forward) dès que main a avancé.
    a_pousser = True
    fetch = run(["git", "fetch", remote, "main"], check=False)
    if fetch.returncode != 0:
        warn(f"Lecture du depot distant impossible : {commun.masquer(fetch.stderr.strip())}")
    else:
        retard = run("git rev-list --count HEAD..FETCH_HEAD", check=False).stdout.strip()
        avance = run("git rev-list --count FETCH_HEAD..HEAD", check=False).stdout.strip()
        if retard not in ("", "0"):
            rb = run("git rebase FETCH_HEAD", check=False)
            if rb.returncode != 0:
                run("git rebase --abort", check=False)
                diag = commun.diagnostic("git-rebase", code=rb.returncode,
                                         stdout=rb.stdout.strip(), stderr=rb.stderr.strip())
                warn("Le depot distant a diverge et la fusion automatique a echoue — rien n'est pousse.")
                signaler("claude", "deploy : divergence avec origin/main",
                         f"{retard} commit(s) distants en conflit avec le commit local ; "
                         "rebase annulé, dépôt local intact, push non effectué.",
                         urgence="bloquant", cle="deploy-divergence", diag=diag)
                return
            ok(f"{retard} commit(s) distant(s) integre(s).")
        a_pousser = avance not in ("", "0")

    if not a_pousser:
        ok("Deja a jour avec le depot distant.")
        if cfg.get("pages_url"):
            print(f"\n  URL : {CYN}{cfg['pages_url']}{R}\n")
        return

    pr = run(["git", "push", remote, "HEAD:main"], check=False)
    if pr.returncode != 0:
        diag = commun.diagnostic("git-push", code=pr.returncode,
                                 stdout=pr.stdout.strip(), stderr=pr.stderr.strip(),
                                 statut=run("git status -sb", check=False).stdout[:500])
        warn(f"Push echoue : {commun.masquer(pr.stderr.strip())}")
        warn("Relancez manuellement : git push origin main")
        signaler("utilisateur", "deploy : push échoué",
                 "Le commit local existe mais n'a pas été poussé sur origin/main.",
                 urgence="action", cle="deploy-push", diag=diag)
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
    except SystemExit as exc:
        # Code de sortie inchangé (0) pour ne pas modifier le comportement du BAT
        # appelant ; l'échec est tracé dans la boîte aux lettres.
        if exc.code not in (None, 0):
            signaler("claude", "deploy : arrêt sur erreur",
                     "deploy.py s'est arrêté sur fail() ; diagnostic de la dernière action joint.",
                     urgence="bloquant", cle="deploy-arret", diag=commun.DERNIER_DIAG)
    except Exception as exc:
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
        signaler("claude", "deploy : erreur inattendue", f"{type(exc).__name__}: {exc}",
                 urgence="bloquant", cle="deploy-exception",
                 diag=commun.diagnostic("deploy-exception", exc=exc, arguments=sys.argv[1:]))
    finally:
        # Pas de pause en mode --local (le serveur tourne en boucle, Ctrl+C pour quitter)
        # [PATCH_NO_PAUSE_v1] (rev.32 B4 bis) Pas de pause si pipeline non interactif
        # (PIPELINE_NO_PAUSE=1 ou stdin non-TTY) : voir dsv_commun.pause_finale.
        if "--local" not in sys.argv:
            commun.pause_finale()
