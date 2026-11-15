#!/usr/bin/env python3
"""
dsv_bal.py — Boite aux lettres du depot dossier-desavoie
Affaire DeSavoie / Chicherit

Canal asynchrone entre les scripts lances sur le poste Windows, les sessions
Claude et l'utilisateur. Un message = un fichier JSON dans _dsv/bal/ (un fichier
par message : deux depots simultanes ne peuvent pas entrer en conflit Git).

Hors session Claude, un script DEPOSE seulement : il ne sonne personne. C'est la
session Claude suivante qui releve la boite et decide qui prevenir (regles dans
.claude/CLAUDE.md).

Usage :
  python dsv_bal.py                       menu
  python dsv_bal.py lister [--a QUI] [--tous]
  python dsv_bal.py deposer --a QUI --sujet "..." [--corps "..."] [--urgence U] [--cle C]
  python dsv_bal.py acquitter IDENT [--reponse "..."]

Destinataires : utilisateur, claude, pipeline.   Urgences : info, action, bloquant.
"""
__version__ = "1.0"

import os, sys, re, json, argparse
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
from dsv_commun import B, R, RED, YLW, DIM, CYN, h, ok, warn, info, fail

BAL_DIR       = commun.DSV_DIR / "bal"
DESTINATAIRES = ("utilisateur", "claude", "pipeline")
URGENCES      = ("bloquant", "action", "info")      # ordre de tri : le plus urgent d'abord

# ─────────────────────────────────────────────
# Lecture / ecriture
# ─────────────────────────────────────────────
def _ecrire(chemin, message):
    """Ecriture atomique : fichier temporaire puis remplacement."""
    chemin.parent.mkdir(parents=True, exist_ok=True)
    tmp = chemin.with_suffix(".tmp")
    tmp.write_text(json.dumps(message, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, chemin)

def _lire_tous():
    """Tous les messages lisibles. Un fichier illisible est signale, jamais supprime."""
    messages = []
    if not BAL_DIR.is_dir():
        return messages
    for chemin in sorted(BAL_DIR.glob("*.json")):
        try:
            m = json.loads(chemin.read_text(encoding="utf-8"))
            m["_chemin"] = chemin
            messages.append(m)
        except (OSError, ValueError) as exc:
            commun.diagnostic("bal-lecture", exc=exc, fichier=chemin.name)
    return messages

def _slug(texte):
    s = re.sub(r"[^a-z0-9]+", "-", texte.lower().encode("ascii", "ignore").decode()).strip("-")
    return s[:40] or "message"

# ─────────────────────────────────────────────
# API
# ─────────────────────────────────────────────
def deposer(destinataire, sujet, corps="", urgence="info", expediteur="manuel",
            cle=None, diagnostic=None):
    """Depose un message. Si `cle` designe un message encore ouvert, celui-ci est
    mis a jour (compteur d'occurrences) au lieu d'en creer un second : une panne
    qui se repete ne noie pas la boite. Retourne le chemin du fichier."""
    if destinataire not in DESTINATAIRES:
        raise ValueError(f"destinataire inconnu : {destinataire} (attendu : {DESTINATAIRES})")
    if urgence not in URGENCES:
        raise ValueError(f"urgence inconnue : {urgence} (attendu : {URGENCES})")
    maintenant = commun.horodatage()
    corps = commun.masquer(corps)
    if cle:
        for m in _lire_tous():
            if m.get("cle") == cle and m.get("etat") == "ouvert":
                chemin = m.pop("_chemin")
                m.update(sujet=sujet, corps=corps, urgence=urgence, dernier=maintenant,
                         occurrences=m.get("occurrences", 1) + 1)
                if diagnostic is not None:
                    m["diagnostic"] = diagnostic
                _ecrire(chemin, m)
                return chemin
    ident = f"{maintenant.replace('-', '').replace(':', '')}-{destinataire}-{_slug(cle or sujet)}"
    chemin, n = BAL_DIR / f"{ident}.json", 1
    while chemin.exists():                      # deux depots dans la meme seconde
        n += 1
        chemin = BAL_DIR / f"{ident}-{n}.json"
    message = {
        "id": chemin.stem, "etat": "ouvert", "urgence": urgence,
        "destinataire": destinataire, "expediteur": expediteur,
        "sujet": sujet, "corps": corps,
        "cree": maintenant, "dernier": maintenant, "occurrences": 1,
    }
    if cle:
        message["cle"] = cle
    if diagnostic is not None:
        message["diagnostic"] = diagnostic
    _ecrire(chemin, message)
    return chemin

def lister(destinataire=None, tous=False):
    """Messages ouverts (ou tous), du plus urgent au moins urgent puis du plus ancien
    au plus recent."""
    messages = [m for m in _lire_tous()
                if (tous or m.get("etat") == "ouvert")
                and (destinataire is None or m.get("destinataire") == destinataire)]
    rang = {u: i for i, u in enumerate(URGENCES)}
    messages.sort(key=lambda m: (m.get("etat") != "ouvert",
                                 rang.get(m.get("urgence"), 99), m.get("cree", "")))
    return messages

def acquitter(ident, reponse="", par="manuel"):
    """Clot un message designe par son identifiant ou un fragment non ambigu.
    Le fichier est conserve (etat « traite ») : il sert d'historique des decisions."""
    trouves = [m for m in _lire_tous() if ident in m.get("id", "")]
    exacts = [m for m in trouves if m.get("id") == ident]
    trouves = exacts or trouves
    if len(trouves) != 1:
        raise LookupError(f"« {ident} » designe {len(trouves)} message(s) : "
                          + ", ".join(m["id"] for m in trouves[:5]))
    m = trouves[0]
    chemin = m.pop("_chemin")
    m.update(etat="traite", traite=commun.horodatage(), traite_par=par)
    if reponse:
        m["reponse"] = commun.masquer(reponse)
    _ecrire(chemin, m)
    return chemin

def age_jours(message):
    try:
        cree = datetime.strptime(message["cree"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - cree).total_seconds() / 86400
    except (KeyError, ValueError):
        return 0.0

def afficher(messages, detail=False):
    if not messages:
        info("Boite aux lettres : aucun message.")
        return
    for m in messages:
        c = RED if m.get("urgence") == "bloquant" else YLW if m.get("urgence") == "action" else DIM
        etat = "" if m.get("etat") == "ouvert" else f" {DIM}[traite]{R}"
        rep = f" x{m['occurrences']}" if m.get("occurrences", 1) > 1 else ""
        print(f"  {c}{m.get('urgence', '?'):<8}{R} -> {m.get('destinataire', '?'):<11} "
              f"{B}{m.get('sujet', '')}{R}{rep}{etat}")
        print(f"  {DIM}         {m.get('id')}  ·  {m.get('expediteur')}  ·  "
              f"{age_jours(m):.0f} j{R}")
        if detail:
            for ligne in str(m.get("corps", "")).splitlines():
                print(f"           {ligne}")
            if m.get("reponse"):
                print(f"           {DIM}reponse : {m['reponse']}{R}")
            if m.get("diagnostic") is not None:
                print(f"           {DIM}diagnostic : "
                      f"{json.dumps(m['diagnostic'], ensure_ascii=False)[:1500]}{R}")

# ─────────────────────────────────────────────
# Ligne de commande
# ─────────────────────────────────────────────
def _menu():
    """Menu interactif : toutes les questions sont posees avant toute action."""
    h("Boite aux lettres — dossier DeSavoie")
    print("  1. Lister les messages ouverts\n  2. Lister tous les messages (detail)\n"
          "  3. Deposer un message\n  4. Acquitter un message\n  0. Quitter")
    choix = input("  Choix : ").strip()
    if choix == "1":
        return ["lister"]
    if choix == "2":
        return ["lister", "--tous", "--detail"]
    if choix == "3":
        a = input(f"  Destinataire {DESTINATAIRES} [claude] : ").strip() or "claude"
        u = input(f"  Urgence {URGENCES} [info] : ").strip() or "info"
        s = input("  Sujet : ").strip()
        c = input("  Corps (une ligne) : ").strip()
        return ["deposer", "--a", a, "--urgence", u, "--sujet", s, "--corps", c]
    if choix == "4":
        afficher(lister())
        i = input("  Identifiant (ou fragment) : ").strip()
        r = input("  Reponse / decision : ").strip()
        return ["acquitter", i, "--reponse", r]
    return None

def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        argv = _menu()
        if argv is None:
            return 0
    p = argparse.ArgumentParser(description="Boite aux lettres du dossier DeSavoie")
    sp = p.add_subparsers(dest="commande", required=True)
    l = sp.add_parser("lister");     l.add_argument("--a", choices=DESTINATAIRES)
    l.add_argument("--tous", action="store_true"); l.add_argument("--detail", action="store_true")
    d = sp.add_parser("deposer");    d.add_argument("--a", required=True, choices=DESTINATAIRES)
    d.add_argument("--sujet", required=True);      d.add_argument("--corps", default="")
    d.add_argument("--urgence", default="info", choices=URGENCES)
    d.add_argument("--cle");                       d.add_argument("--de", default="manuel")
    a = sp.add_parser("acquitter");  a.add_argument("ident")
    a.add_argument("--reponse", default="");       a.add_argument("--par", default="manuel")
    args = p.parse_args(argv)
    try:
        if args.commande == "lister":
            afficher(lister(args.a, args.tous), detail=args.detail or bool(args.a))
        elif args.commande == "deposer":
            ok(f"Message depose : {deposer(args.a, args.sujet, args.corps, args.urgence, args.de, args.cle).name}")
        else:
            ok(f"Message acquitte : {acquitter(args.ident, args.reponse, args.par).name}")
    except (LookupError, ValueError, OSError) as exc:
        commun.diagnostic("bal-" + args.commande, exc=exc, dossier=str(BAL_DIR),
                          fichiers=sorted(x.name for x in BAL_DIR.glob("*"))[:30] if BAL_DIR.is_dir() else [])
        fail(str(exc))
    return 0

if __name__ == "__main__":
    sys.exit(main())
