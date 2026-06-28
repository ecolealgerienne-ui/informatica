# Architecture Technique — Plateforme d'Intelligence de Migration

> Document destiné aux architectes et DSI. Une page. Pour la documentation complète : voir [README.md](README.md).

---

## Vue d'ensemble

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    PLATEFORME D'INTELLIGENCE DE MIGRATION                │
│                                                                          │
│  Entrée : XML Informatica PowerCenter                                    │
│  Sortie : Code Python/PySpark + Documentation métier + Rapport QA       │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Pipeline 5 agents

```
XML Informatica
      │
      ▼
┌─────────────┐     Parsing déterministe (pas de LLM)
│  1. PARSER  │  +  Analyse sémantique LLM (fonctions propriétaires, routing)
└──────┬──────┘     Scoring de complexité (grille formelle)
       │
       ▼
  ┌─────────────────────────────────────────────────────────────┐
  │                    CANONICAL JSON                           │
  │  Source de vérité unique — représentation normalisée        │
  │  de la logique métier, indépendante de la source et cible   │
  └─────────────────────────────────────────────────────────────┘
       │                    │                    │
       ▼                    ▼                    ▼
┌─────────────┐    ┌──────────────┐    ┌─────────────────────┐
│  2. CODEGEN │    │  4. DOCUMENT │    │  Futures cibles      │
│  Python/    │    │  Phase 1 :   │    │  DBT / Airflow /     │
│  PySpark    │    │  Fiche fonct.│    │  Snowflake / Fabric  │
└──────┬──────┘    └──────────────┘    └─────────────────────┘
       │
       ▼
┌─────────────┐     Vérification statique sans LLM (ast.parse)
│  3. FIXER   │  +  Correction sémantique LLM si nécessaire
└──────┬──────┘     Max 3 cycles → OK ou ESCALATE
       │
       ▼
┌─────────────┐     Documentation métier bilingue FR/EN
│  4. DOCUM.  │  +  Code annoté (docstrings injectés par AST)
└──────┬──────┘     Phase 1 : depuis JSON seul (sans code)
       │
       ▼
┌─────────────┐     Exécution dans subprocess isolé
│   5. QA     │  +  Data diff vs golden dataset (matrice de tolérance)
└─────────────┘     Rapport HTML interactif
```

---

## Routing automatique par complexité

| Score | Flag | Cible | Estimation |
|---|---|---|---|
| 0 – 3 | LOW | Python / pandas | 0,5 j |
| 4 – 8 | MEDIUM | Python / pandas | 1 – 2 j |
| 9 – 14 | HIGH | PySpark | 3 – 5 j |
| ≥ 15 | CRITICAL | Databricks + humain | > 5 j |

Le Parser décide du routing **sans LLM** — scoring basé sur une grille formelle (`complexity_matrix.json`).

---

## Composants techniques

| Composant | Technologie | Rôle |
|---|---|---|
| LLM | Claude (Haiku / Opus) via CLI | Analyse sémantique, génération, correction, documentation |
| RAG Base | 3 fichiers JSON/MD statiques | Contexte métier injecté dans les prompts (transformation map, complexity matrix, templates) |
| Vérification statique | Python `ast` module | Syntaxe, patterns interdits (`iterrows`, `apply`) — sans LLM |
| Data diff | pandas + matrice de tolérance | Comparaison exacte ou ±1 selon la colonne |
| Rapport QA | Jinja2 HTML | Rapport interactif livré au client |

---

## Décisions architecturales clés

**Canonical JSON comme actif central**
Le JSON canonique n'est pas un format intermédiaire temporaire — c'est la représentation normalisée de la logique métier. Il permet de cibler Python, PySpark, DBT, Airflow ou Snowflake depuis la même analyse, et sert de base pour l'audit et l'analyse d'impact.

**LLM uniquement pour l'irréductible sémantique**
Tout ce qui peut être fait en Python pur l'est : parsing XML, scoring de complexité, vérification syntaxique, data diff. Le LLM intervient uniquement pour l'analyse sémantique et la génération de code — là où un algorithme déterministe ne suffit pas.

**Payload LLM toujours borné**
Aucune donnée brute n'est envoyée au LLM. Le QA agent envoie uniquement un résumé statistique compact (< 2 KB). Le Fixer envoie uniquement les extraits problématiques, pas le code entier.

**Traçabilité complète**
Chaque étape écrit son output sur disque avant de passer la main. En cas d'échec, le pipeline peut reprendre depuis n'importe quel point sans re-exécuter les étapes précédentes.

---

## Structure des outputs

```
output/
├── 01_canonical_json/     ← Source de vérité — réutilisable par tous les agents
├── 02_generated_code/     ← Draft Python (avant correction)
├── 03_fixed_code/         ← Code corrigé + documenté + rapport de corrections
├── 04_data_diff_report/   ← Rapport QA JSON + HTML
└── phase1_docs/           ← Fiches fonctionnelles métier (Phase 1 Analyse)
```

---

## Évolutions prévues

| Horizon | Évolution |
|---|---|
| Court terme | Logs structurés JSON par agent (coût tokens, latence, nb cycles) |
| Moyen terme | RAG vectoriel (embeddings sur corpus de migrations validées) |
| Long terme | Fine-tuning sur modèle open-source (50-200 exemples validés) |
| Cible | Multi-sources : Talend, SSIS, AbInitio → même Canonical JSON |
