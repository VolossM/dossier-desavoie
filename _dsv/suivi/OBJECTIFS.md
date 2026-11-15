# Objectifs et suivi — dossier DeSavoie / Chicherit

Document de pilotage. Les valeurs « mesurées » proviennent de `python dsv.py etat`,
qui les historise dans `_dsv/suivi/indicateurs.jsonl` ; les autres sont signalées comme
telles. Mettre à jour les lignes existantes plutôt qu'en ajouter à la fin.

Dernière mise à jour : 2026-11-15.

## 1. Indicateurs de référence (mesurés le 2026-11-15, branche `claude/sleepy-hopper-15h56m`)

| Indicateur | Valeur | Source |
|---|---|---|
| Lignes d'entités valides, tous fichiers | 114 908 | `dsv.py etat` |
| Identifiants d'entités uniques | 74 855 | `dsv.py etat` |
| Lignes JSONL invalides | 0 | `dsv.py etat` |
| Fichiers déclarés au manifeste | 8 | `manifest.json` |
| Fichiers `.jsonl` présents mais non déclarés | 6 (7 915 lignes)[^1] | `dsv.py etat` |
| Empreintes du manifeste conformes aux octets stockés par Git | 0 sur 8[^2] | `dsv.py etat` |
| Dernier déploiement inscrit au manifeste | 2026-08-07 (100 jours) | `manifest.json`, champ `_last_deploy` |
| Messages ouverts en boîte aux lettres | voir `dsv.py etat` | `_dsv/bal/` |
| Ressources du poste (mémoire, disque, Downloads, `git status`) | **non mesuré** | `dsv.py audit`, à lancer sur le poste |

## 2. Objectifs

Aucun objectif d'enquête n'a été formulé par l'utilisateur à ce jour : les lignes
ci-dessous sont des **propositions de Claude, à valider, amender ou rejeter**. Elles ne
portent que sur ce que le dépôt permet de constater. Les cibles et échéances sont à
fixer par l'utilisateur.

| # | Objectif proposé | Indicateur | Départ (2026-11-15) | Cible | Échéance | Statut |
|---|---|---|---|---|---|---|
| O1 | Une seule ligne de développement : arbitrer entre `main` et `main-3663ct`[^3] | Branches portant des versions différentes de `deploy.py` | 3 (`main`, `main-3663ct`, branche de session) | 1 | à fixer | **décision attendue** |
| O2 | Intégrité vérifiable sur le site publié | Empreintes conformes aux octets stockés | 0 / 8 | 8 / 8 | 1er déploiement suivant la fusion | correctif écrit, non déployé |
| O3 | Statuer sur chaque fichier non déclaré : l'intégrer au manifeste, ou le documenter comme hors frise | Fichiers `.jsonl` non déclarés sans statut | 6 | 0 | à fixer | à valider |
| O4 | Manifeste à jour des données versionnées | Âge de `_last_deploy` au moment d'un commit de données | 100 jours[^4] | à fixer | à fixer | à valider |
| O5 | Établir la référence de performance du poste | Rapport `dsv_audit_poste_*.json` disponible | aucun | 1 rapport | à fixer | à valider |
| O6 | Aucune décision en souffrance | Âge du plus ancien message `bloquant` ou `action` ouvert | boîte créée ce jour | à fixer | permanent | à valider |
| O7 | Objectifs d'enquête (pistes à clore, entités à documenter) | à définir par l'utilisateur | — | — | — | **à formuler** |

## 3. Rituel de suivi

- **À chaque session Claude** : début et fin de session selon `.claude/CLAUDE.md`
  (relevé d'état, lecture de ce document, traitement puis acquittement des messages).
- **À chaque déploiement** : `python dsv.py deployer` enchaîne le déploiement et le
  relevé ; un relevé n'est historisé que si une valeur a changé.
- **Revue périodique** (rythme à fixer par l'utilisateur) : relire le tableau des
  objectifs, comparer le premier et le dernier relevé de `indicateurs.jsonl`, clore ou
  reformuler ce qui n'a pas bougé.

## 4. Journal des décisions

| Date | Décision | Par | Motif |
|---|---|---|---|
| 2026-11-15 | Les `.jsonl` sont stockés sans conversion de fins de ligne (`.gitattributes`) | Claude, à confirmer au 1er déploiement | Les empreintes du manifeste sont calculées sur des fichiers CRLF ; Git stockait du LF[^2] |
| 2026-11-15 | `deploy.py` ne pose plus de question en cours d'exécution ; les décisions passent par arguments ou boîte aux lettres | Claude, sur demande de l'utilisateur (« fatigue du consentement ») | Exécution sans interruption |
| 2026-11-15 | La garde anti-régression (refus au-delà de 20 % d'entités perdues) s'applique à tous les `.jsonl`, plus seulement aux seeds promus | Claude | Auparavant simple avertissement de taille ; contournement : `DEPLOY_ALLOW_SHRINK=1` |
| 2026-11-15 | `setup.py` ne force plus le push sans `--forcer-push` ou réécriture d'historique dans la même exécution | Claude | Incident du même jour : `main` écrasée par un essai, puis restaurée sur `4927dae` avec l'accord de l'utilisateur |

[^1]: `data/desavoie_critical_missing.ftm.jsonl` (13), `data/desavoie_shareholders_new.ftm.jsonl` (10), `data/lbr_B269483.ftm.jsonl` (1), `data_desavoie/leads_controle_pv_oracle.ftm.jsonl` (4 141), `data_desavoie/leads_rne_recovered_v1.ftm.jsonl` (2 485), `data_desavoie/leads_statuts_fondateur.ftm.jsonl` (1 265). Les trois derniers ont été ajoutés par les commits `77b038b`, `05b70df` et `4927dae` (2026-09-30 et 2026-10-03), sans modification de `manifest.json`.
[^2]: Vérifié le 2026-11-15 : pour les 8 fichiers, la taille inscrite au manifeste égale la taille du fichier stocké augmentée d'un octet par ligne, et le SHA-256 du manifeste est celui du fichier converti en CRLF. `index.html` recalcule l'empreinte sur le fichier servi et la compare à celle du manifeste : sur le site publié, les 8 fichiers devraient donc s'afficher « nouvelle version » au lieu de « conforme ». Cette conséquence est **déduite du code, non observée** sur le site.
[^3]: `main-3663ct` part du commit `eb35e5d` (2026-08-07) et porte trois commits absents de `main` (`9495071` du 2026-09-26, `1cf9f40` et `44f642a` du 2026-10-05 : venv canonique, dossiers secondaires Sainte-Foy, sélecteur de dossier) qui modifient `deploy.py`, `setup.py`, `init_git.py` et `index.html`. `main` porte trois commits absents de `main-3663ct` (leads du 2026-09-30 et du 2026-10-03). La refonte du 2026-11-15 est fondée sur `main` : sa fusion avec `main-3663ct` demandera une résolution manuelle dans `deploy.py`.
[^4]: Écart entre `_last_deploy` (2026-08-07) et la date du relevé. Les commits de données postérieurs (2026-09-30, 2026-10-03) n'ont pas modifié `manifest.json`.
