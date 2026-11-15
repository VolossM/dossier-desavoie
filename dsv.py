#!/usr/bin/env python3
"""
dsv.py — Point d'entree unique du depot dossier-desavoie
Affaire DeSavoie / Chicherit

Charge la meilleure version de chaque module (voir dsv_commun.cle_version) et
enchaine les etapes sans interruption : toutes les questions sont posees au
debut (menu) ou passees en arguments.

Usage :
  python dsv.py                       menu
  python dsv.py etat                  indicateurs du dossier + boite aux lettres
  python dsv.py deployer [options de deploy.py]   deploiement puis etat
  python dsv.py local                 serveur HTTP local (deploy.py --local)
  python dsv.py bal [arguments de dsv_bal.py]
  python dsv.py audit [--sans-bal]    audit des ressources du poste
  python dsv.py installer             premiere installation (init_git.py puis setup.py)

Bibliotheque standard uniquement : aucune dependance a installer.
"""
__version__ = "1.0"

import os, sys, re, json, hashlib
from collections import Counter
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
from dsv_commun import B, R, RED, GRN, YLW, DIM, h, ok, warn, info, fail

RACINE      = commun.RACINE
SUIVI_DIR   = commun.DSV_DIR / "suivi"
INDICATEURS = SUIVI_DIR / "indicateurs.jsonl"

def module(nom_fichier):
    """Charge un module du depot ; echec explicite avec diagnostic s'il manque."""
    m = commun.charger_module(nom_fichier)
    if m is None:
        commun.diagnostic("chargement-module", module=nom_fichier,
                          presents=sorted(p.name for p in RACINE.glob("*.py")))
        fail(f"{nom_fichier} introuvable dans {RACINE} ni dans {commun.DOWNLOADS}.")
    return m

# ─────────────────────────────────────────────
# Etat : indicateurs mesures du dossier
# ─────────────────────────────────────────────
def mesurer():
    """Mesure l'etat du dossier : entites par fichier, conformite au manifeste,
    entites uniques, anciennete du dernier deploiement, boite aux lettres."""
    manifeste = json.loads((RACINE / "manifest.json").read_text(encoding="utf-8"))
    declares = {e.get("path"): e for e in manifeste.get("files", [])}
    chemins = list(declares)
    for dossier in ("data", "data_desavoie"):
        for p in sorted((RACINE / dossier).glob("*.jsonl")):
            rel = p.relative_to(RACINE).as_posix()
            if rel not in declares:
                chemins.append(rel)
    fichiers, ids, schemas = {}, set(), Counter()
    for rel in chemins:
        p = RACINE / rel
        if not p.is_file():
            fichiers[rel] = {"manifeste": "absent"}
            continue
        n_ok = n_bad = 0
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            for ligne in f:
                ligne = ligne.strip()
                if not ligne:
                    continue
                try:
                    obj = json.loads(ligne)
                except ValueError:
                    n_bad += 1
                    continue
                if isinstance(obj, dict) and obj.get("id"):
                    n_ok += 1
                    ids.add(obj["id"])
                    schemas[obj.get("schema", "?")] += 1
                else:
                    n_bad += 1
        if rel not in declares:
            statut = "non declare"
        elif not declares[rel].get("sha256"):
            statut = "non verifie"
        elif commun.sha256(p) == declares[rel]["sha256"]:
            statut = "conforme"
        else:
            # Empreinte calculee sous Windows (CRLF) et fichier extrait en LF, ou l'inverse
            brut = p.read_bytes().replace(b"\r\n", b"\n")
            proches = (brut, brut.replace(b"\n", b"\r\n"))
            statut = ("conforme hors fins de ligne"
                      if any(hashlib.sha256(x).hexdigest() == declares[rel]["sha256"] for x in proches)
                      else "divergent")
        fichiers[rel] = {"entites": n_ok, "invalides": n_bad,
                         "octets": p.stat().st_size, "manifeste": statut}
    etat = {
        "horodatage": commun.horodatage(),
        "fichiers": fichiers,
        "entites_total": sum(f.get("entites", 0) for f in fichiers.values()),
        "entites_uniques": len(ids),
        "schemas": dict(schemas.most_common()),
        "dernier_deploiement": manifeste.get("_last_deploy"),
    }
    bal = commun.charger_module("dsv_bal.py")
    if bal is not None:
        ouverts = bal.lister()
        etat["bal"] = {
            "ouverts": len(ouverts),
            "par_urgence": dict(Counter(m.get("urgence") for m in ouverts)),
            "par_destinataire": dict(Counter(m.get("destinataire") for m in ouverts)),
        }
    return etat

def _comparable(etat):
    # deploy.py reecrit _last_deploy a chaque execution : seul, il ne justifie pas un releve.
    return {k: v for k, v in etat.items() if k not in ("horodatage", "dernier_deploiement")}

def _precedent():
    """Dernier releve valide de l'historique (les lignes illisibles sont ignorees)."""
    if not INDICATEURS.is_file():
        return None
    for ligne in reversed(INDICATEURS.read_text(encoding="utf-8").splitlines()):
        try:
            return json.loads(ligne)
        except ValueError:
            continue
    return None

def _delta(nouveau, ancien):
    if ancien is None or nouveau == ancien:
        return ""
    d = nouveau - ancien
    return f" {GRN if d > 0 else RED}({d:+,}){R}"

def etat(enregistrer=True):
    h("Etat du dossier")
    try:
        e = mesurer()
    except (OSError, ValueError) as exc:
        commun.diagnostic("etat", exc=exc, racine=str(RACINE))
        fail(f"Mesure impossible : {exc}")
    prec = _precedent()
    pf = (prec or {}).get("fichiers", {})
    largeur = max(len(x) for x in e["fichiers"]) + 2
    print(f"\n  {'Fichier':<{largeur}} {'Entites':>10}  {'Invalides':>9}  Manifeste")
    print(f"  {'─' * largeur} {'─' * 10}  {'─' * 9}  {'─' * 12}")
    for rel, f in e["fichiers"].items():
        if "entites" not in f:
            print(f"  {rel:<{largeur}} {'—':>10}  {'—':>9}  {RED}absent{R}")
            continue
        c = GRN if f["manifeste"] == "conforme" else RED if f["manifeste"] == "divergent" else YLW
        print(f"  {rel:<{largeur}} {f['entites']:>10,}  {f['invalides']:>9,}  {c}{f['manifeste']}{R}"
              + _delta(f["entites"], pf.get(rel, {}).get("entites")))
    print(f"\n  Entites : {B}{e['entites_total']:,}{R} lignes valides"
          + _delta(e["entites_total"], (prec or {}).get("entites_total"))
          + f", {B}{e['entites_uniques']:,}{R} identifiants uniques"
          + _delta(e["entites_uniques"], (prec or {}).get("entites_uniques")))
    dd = e.get("dernier_deploiement")
    if dd:
        try:
            age = (datetime.now(timezone.utc) - datetime.strptime(dd, "%Y-%m-%dT%H:%M:%SZ")
                   .replace(tzinfo=timezone.utc)).days
            print(f"  Dernier deploiement inscrit au manifeste : {dd} (il y a {age} j)")
        except ValueError:
            print(f"  Dernier deploiement inscrit au manifeste : {dd}")
    if prec:
        info(f"Ecarts calcules par rapport au releve du {prec.get('horodatage')}.")

    bal = commun.charger_module("dsv_bal.py")
    if bal is not None:
        h("Boite aux lettres — messages ouverts")
        bal.afficher(bal.lister(), detail=True)

    if enregistrer and (prec is None or _comparable(prec) != _comparable(e)):
        SUIVI_DIR.mkdir(parents=True, exist_ok=True)
        with open(INDICATEURS, "a", encoding="utf-8") as f:
            f.write(json.dumps(e, ensure_ascii=False, sort_keys=True) + "\n")
        info(f"Releve ajoute a {INDICATEURS.relative_to(RACINE).as_posix()}.")
    elif enregistrer:
        info("Aucun changement depuis le dernier releve — historique inchange.")
    return e

# ─────────────────────────────────────────────
# Commandes deleguees
# ─────────────────────────────────────────────
def deployer(arguments):
    """Execute deploy.py (meilleure version) dans ce processus, puis l'etat."""
    dep = module("deploy.py")
    ancien_argv, sys.argv = sys.argv, [str(Path(dep.__file__))] + list(arguments)
    code = 0
    try:
        dep.main()
    except SystemExit as exc:
        code = exc.code or 0
        if code:
            commun.signaler("claude", "deploy : arrêt sur erreur",
                            "deploy.py (lancé par dsv.py) s'est arrêté sur fail().",
                            urgence="bloquant", cle="deploy-arret", diag=commun.DERNIER_DIAG)
    except Exception as exc:
        code = 1
        commun.signaler("claude", "deploy : erreur inattendue", f"{type(exc).__name__}: {exc}",
                        urgence="bloquant", cle="deploy-exception",
                        diag=commun.diagnostic("deploy-exception", exc=exc, arguments=arguments))
    finally:
        sys.argv = ancien_argv
    if "--local" not in arguments and "--dry-run" not in arguments:
        etat()
    return code

def installer():
    return module("init_git.py").main() or 0

COMMANDES = ("etat", "deployer", "local", "bal", "audit", "installer")

def _menu():
    """Menu : toutes les questions sont posees ici, avant toute execution."""
    h("Dossier DeSavoie — point d'entree")
    print("  1. Etat du dossier et boite aux lettres\n  2. Deployer (Downloads -> manifest -> GitHub)\n"
          "  3. Deployer a blanc (--dry-run)\n  4. Serveur local\n  5. Boite aux lettres\n"
          "  6. Audit des ressources du poste\n  7. Premiere installation\n  0. Quitter")
    choix = input("  Choix : ").strip()
    if choix == "2":
        message = input("  Message de commit (vide = automatique) : ").strip()
        purge = input("  Retirer du suivi Git les fichiers etrangers eventuels ? [o/N] ").strip().lower()
        return ["deployer"] + (["-m", message] if message else []) \
                            + (["--purger-intrus"] if purge == "o" else [])
    return {"1": ["etat"], "3": ["deployer", "--dry-run"], "4": ["local"], "5": ["bal"],
            "6": ["audit", "--executer"], "7": ["installer"]}.get(choix)

def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        argv = _menu()
        if not argv:
            return 0
    commande, reste = argv[0], argv[1:]
    if commande not in COMMANDES:
        print(__doc__)
        fail(f"Commande inconnue : {commande} (attendu : {', '.join(COMMANDES)})")
    if commande == "etat":
        etat()
        return 0
    if commande == "deployer":
        return deployer(reste)
    if commande == "local":
        return deployer(["--local"])
    if commande == "bal":
        return module("dsv_bal.py").main(reste)
    if commande == "audit":
        return module("dsv_audit_poste.py").main(reste if "--executer" in reste else ["--executer"] + reste)
    return installer()

if __name__ == "__main__":
    try:
        code = main()
    except SystemExit as exc:
        code = exc.code or 0
    except Exception as exc:
        commun.diagnostic("dsv-exception", exc=exc, arguments=sys.argv[1:])
        code = 1
    commun.pause_finale()
    sys.exit(code)
