# POC IA Migration — Spécification Résumée

**Objectif** : Convertir automatiquement des workflows Informatica PowerCenter (XML) en code Python ETL via un pipeline d'agents IA.

---

## 1. Contexte

Les migrations Informatica PowerCenter sont longues, coûteuses et répétitives. Ce POC démontre qu'un pipeline d'agents IA peut automatiser l'essentiel de la conversion : analyse, génération de code, correction, documentation et validation — sans intervention humaine pour les cas simples.

**LLM utilisé** : Claude Code CLI (`claude -p`), authentifié via OAuth. Aucune clé API, aucun coût d'infrastructure supplémentaire.

---

## 2. Architecture générale

Le pipeline enchaîne 5 agents en séquence. Chaque agent lit ses entrées depuis le disque et écrit ses sorties avant de passer la main au suivant.

```
wf_clients_dim.xml
        │
        ▼
┌─────────────────┐
│  1. Parser      │  XML → JSON canonique + analyse de complexité
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  2. CodeGen     │  JSON → code Python batch (draft)
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  3. Fixer       │  Vérification statique + correction sémantique
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  4. Documenter  │  Documentation métier + code annoté
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  5. QA          │  Exécution réelle + data diff + rapport HTML
└─────────────────┘
```

**Règle d'arrêt** : si un agent retourne une erreur ou un statut `ESCALATE`, le pipeline s'arrête et demande une intervention humaine.

---

## 3. Les 5 agents

### Agent 1 — Parser
- **Entrée** : fichier XML Informatica
- **Sortie** : `output/01_canonical_json/wf_clients_dim.json`
- **Fonctionnement** :
  - Parsing XML **déterministe** (pas de LLM) — extrait sources, cibles, transformations, connecteurs
  - Appel LLM pour l'**analyse sémantique** : détection de fonctions propriétaires, routing decision, scoring de complexité
  - Fusionne les deux → JSON canonique, source de vérité pour tous les agents suivants
- **Scoring de complexité** : basé sur une grille formelle (`complexity_matrix.json`) — score par transformation + modificateurs globaux → flag LOW / MEDIUM / HIGH / CRITICAL + estimation en jours

### Agent 2 — CodeGen
- **Entrée** : JSON canonique + RAG Base (mapping fonctions + templates)
- **Sortie** : `output/02_generated_code/wf_clients_dim.py`
- **Fonctionnement** : appel LLM avec le contexte complet du mapping et les patterns de code imposés (pandas vectorisé, structure batch, lookup comme merge, load idempotent)

### Agent 3 — Fixer
- **Entrée** : code Python draft + JSON canonique
- **Sortie** : `output/03_fixed_code/wf_clients_dim_fixed.py` + `fix_report.json`
- **Fonctionnement** :
  - Checks statiques **sans LLM** : syntaxe (`ast.parse`), fonctions obligatoires, patterns interdits (`iterrows`, `apply`)
  - Si problèmes détectés → appel LLM pour correction sémantique
  - Boucle max 3 cycles
  - Statut final : `OK` ou `ESCALATE` (intervention humaine requise)

### Agent 4 — Documenter
- **Entrée** : code corrigé + JSON canonique
- **Sortie** : `workflow_explanation.md` + `wf_clients_dim_documented.py`
- **Fonctionnement** : deux appels LLM distincts — (1) documentation métier bilingue FR/EN avec diagramme du data flow, (2) code annoté avec commentaires métier et techniques inline

### Agent 5 — QA
- **Entrée** : code annoté + `tests/expected_output.csv` (golden dataset Informatica)
- **Sortie** : `data_diff_report.json` + `data_diff_report.html`
- **Fonctionnement** :
  - Exécute le code Python généré dans un subprocess isolé
  - Compare la sortie réelle vs attendue avec une **matrice de tolérance** (exact / ±1 / exclu)
  - Calcule un **résumé statistique compact** (taux d'anomalies, mean/max diff, 3 samples max) — jamais de données brutes
  - Appel LLM **uniquement si anomalies** et avec le résumé seulement (payload toujours < 2KB)
  - Si 0 anomalie → pas d'appel LLM, verdict PASS automatique
  - Génère un rapport HTML interactif

---

## 4. RAG Base

Trois fichiers statiques injectés dans les prompts pour cadrer les réponses du LLM :

| Fichier | Contenu |
|---|---|
| `transformation_map.json` | Correspondances Informatica → pandas/numpy (LTRIM, DATEDIFF, DECODE, lookups…) |
| `complexity_matrix.json` | Grille de scoring : base score + modificateurs par type de transformation, seuils, règles de routing |
| `python_templates.md` | Templates imposés : structure batch, lookup comme merge, `_calc_age()` vectorisé, load idempotent |

---

## 5. Routing automatique

Le Parser décide de la plateforme cible selon le score de complexité total :

| Score | Flag | Plateforme | Jours estimés |
|---|---|---|---|
| 0 – 3 | LOW | Python pur | 0.5 j |
| 4 – 8 | MEDIUM | Python pur | 1 – 2 j |
| 9 – 14 | HIGH | PySpark | 3 – 5 j |
| ≥ 15 | CRITICAL | Databricks / humain | > 5 j |

Les cas CRITICAL (Java Transformation, Custom Function) déclenchent un flag `human_intervention_required`.

---

## 6. Données de test

- **Source** : `tests/golden_dataset.csv` — 30 lignes clients générées
- **Référence** : `tests/expected_output.csv` — 23 lignes après application des règles métier (filtre `STATUT_CODE != 'I'`, calcul AGE, lookup STATUT)
- **SYSDATE fixé** à `2026-06-24` pour reproductibilité des calculs d'âge

---

## 7. Principes de conception

| Principe | Application |
|---|---|
| **Déterminisme d'abord** | Tout ce qui peut être fait en Python pur l'est — le LLM n'intervient que pour l'analyse sémantique et la génération |
| **LLM payload borné** | Jamais de données brutes envoyées au LLM — uniquement des résumés structurés et compacts |
| **Traçabilité complète** | Chaque sortie intermédiaire est écrite sur disque avant de passer à l'étape suivante |
| **Vectorisation obligatoire** | Aucun `apply()` ni `iterrows()` — détecté et corrigé par le Fixer Agent |
| **Load idempotent** | Écriture vers `.tmp` puis `os.replace()` — protège contre les interruptions |

---

## 8. Outputs produits par run

```
output/
├── 01_canonical_json/wf_clients_dim.json       — analyse complète du mapping
├── 02_generated_code/wf_clients_dim.py         — code Python draft
├── 03_fixed_code/
│   ├── wf_clients_dim_fixed.py                 — code corrigé
│   ├── fix_report.json                         — détail des corrections
│   ├── workflow_explanation.md                 — documentation métier
│   └── wf_clients_dim_documented.py            — code annoté final
└── 04_data_diff_report/
    ├── data_diff_report.json                   — résultats du data diff
    └── data_diff_report.html                   — rapport HTML interactif
```

---

## 9. Limites du POC (Phase 1)

- Un seul fichier XML traité à la fois (pas de batch multi-mappings)
- Support PySpark non implémenté (placeholder uniquement)
- Pas d'interface utilisateur — exécution en ligne de commande
- Golden dataset limité à 30 lignes — volumétrie non testée en production
