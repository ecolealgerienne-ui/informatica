# Contexte complet — POC IA Migration Informatica → Python

**À coller au début de chaque nouvelle session Claude pour avoir le contexte complet sans répétition.**

---

## Projet

Conversion automatique de workflows **Informatica PowerCenter** (XML) vers **Python/pandas/Databricks** via un pipeline multi-agents IA basé sur **Claude Code CLI**.

- **Repo** : `ecolealgerienne-ui/informatica`
- **Branche de travail** : `claude/laughing-curie-04rzxm`
- **Répertoire de travail** : `/home/user/informatica/poc-ia-migration/`
- **Environnement** : `.venv` Python 3.11, Claude Code CLI authentifié

---

## Architecture du pipeline — 5 agents

```
XML Informatica
    ↓
[1] Parser Agent       → output/01_canonical_json/{wf}.json
    ↓
[2] CodeGen Agent      → output/02_generated_code/{wf}.py
    ↓
[3] Fixer Agent        → output/03_fixed_code/{wf}_fixed.py
    ↓
[4] Documenter Agent   → output/03_fixed_code/{wf}_documented.py
                         output/03_fixed_code/workflow_explanation.md
    ↓
[5] QA Agent           → output/04_data_diff_report/data_diff_report.html
                         output/04_data_diff_report/data_diff_report.json
```

**Lancer le pipeline** :
```bash
cd /home/user/informatica/poc-ia-migration
python pipeline/run_pipeline.py input/wf_clients_dim.xml          # complet
python pipeline/run_pipeline.py input/wf_clients_dim.xml --force  # tout refaire
python pipeline/run_pipeline.py input/wf_clients_dim.xml --from-step 4  # depuis step 4
```

**Règle importante** : toujours utiliser `--from-step N` sauf si le XML ou le Parser ont changé. Chaque run complet consomme beaucoup de tokens.

---

## Fichiers clés

| Fichier | Rôle |
|---|---|
| `agents/parser_agent.py` | Parsing XML déterministe + analyse sémantique Claude |
| `agents/codegen_agent.py` | Génération script Python depuis canonical JSON |
| `agents/fixer_agent.py` | Boucle correction statique + sémantique (max 3 cycles) |
| `agents/documenter_agent.py` | Documentation métier FR/EN + docstrings AST |
| `agents/qa_agent.py` | Exécution script + data diff + rapport HTML |
| `agents/utils.py` | `inject_docstrings()`, `select_rag_sections()`, `slim_canonical()` |
| `pipeline/run_pipeline.py` | Orchestrateur + checkpoint system |
| `rag_base/python_templates.md` | Templates obligatoires (structure batch, lookup, `_calc_age`) |
| `rag_base/transformation_map.json` | Mapping fonctions Informatica → pandas |
| `poc_ia_migration_spec.md` | Spec Phase 1 complète |
| `PHASE2_EVOLUTIONS.md` | Spec Phase 2 — multi-sessions, OrchestratorAgent |
| `RESULTS.md` | Résultats campagne tests + analyse marché + dimensions manquantes |
| `STATUS.md` | Chronologie complète des étapes |

---

## Modèles utilisés par agent

| Agent | Modèle | Raison |
|---|---|---|
| Parser | `claude-haiku-4-5-20251001` | Analyse sémantique légère |
| CodeGen | `claude-sonnet-4-6` | Génération complexe |
| Fixer cycle 1 | `claude-sonnet-4-6` | Correction sémantique |
| Fixer cycles 2-3 | `claude-haiku-4-5-20251001` | Itérations légères |
| Documenter | `claude-haiku-4-5-20251001` | Reformulation structurée |
| QA | `claude-haiku-4-5-20251001` | Interprétation anomalies |

---

## Principes de développement — NE JAMAIS VIOLER

1. **Zéro hardcoding XML-spécifique** dans les agents (D16) — noms de colonnes, tables, tolérances : tout dérivé du canonical JSON
2. **`--from-step N`** toujours, jamais `--force` sauf si XML ou Parser modifiés
3. **Généricité stricte** — le pipeline doit fonctionner sur n'importe quel XML Informatica sans modification des agents
4. **Fixtures par table source** — chaque source utilise ses propres colonnes XML, pas un superset global

---

## État actuel — Phase 1 terminée

### Campagne de tests (8 workflows)

| Workflow | Difficulté | QA | Notes |
|---|---|---|---|
| wf_smoke_test | SMOKE | ✅ PASS | |
| wf_clients_dim | LOW | ✅ PASS | |
| wf_products_dim | LOW | ✅ PASS* | 0 rows (filtre synthétique) |
| wf_orders_fact | MEDIUM | ⚠️ FAIL* | 0 rows BATCH_DATE |
| wf_sales_monthly | MEDIUM | ✅ PASS* | 0 rows (2 sources) |
| wf_unconnected_lkp | MEDIUM/HIGH | ✅ PASS* | Lookup non connecté `:LKP.` |
| wf_xml_normalizer | HIGH | ✅ PASS* | Normalizer (dépivotage) |
| wf_accounts_scd2 | HIGH | ⚠️ CRASH | Bug SCD2 guard — à corriger |
| wf_transactions_hist | CRITICAL | ❌ Non testé | Dernier verrou Phase 1 |

> \* 0 rows = script s'exécute sans crash, fixtures synthétiques éliminent toutes les lignes (BATCH_DATE). Pas un bug.

### Ce qui fonctionne
- Parser : 100% sur tous les XMLs testés
- CodeGen : 0 crash de syntaxe
- Fixer : 1 cycle max sur tous les runs
- Documenter : 183-216 lignes post-fix `{code}` dans le prompt
- QA : fixtures par table source, tolérance dynamique depuis canonical JSON

### Points ouverts
| # | Problème | Priorité |
|---|---|---|
| P1 | wf_accounts_scd2 CRASH — SCD2 guard sur 0 lignes | HIGH |
| P2 | wf_transactions_hist non testé (CRITICAL) | HIGH |
| P3 | `Tolerance: 1 col, PK: *` sur certains XMLs — target non détecté | MEDIUM |
| P4 | 0 rows partout — golden data réels nécessaires pour validation complète | MEDIUM |

---

## Phase 2 — Spécification (voir PHASE2_EVOLUTIONS.md)

Le pipeline Phase 1 suppose **1 workflow = 1 mapping = 1 script**. En production le client a des workflows avec N sessions chaînées via `<WORKFLOWLINK>`.

**Ce qui manque dans le parser** :
- `<WORKFLOWLINK>` — graphe de chaînage entre sessions (FROMTASK, TOTASK, CONDITION)
- `<SESSION>` multiples par workflow
- `<TASKINSTANCE>` — FAIL_PARENT_IF_INSTANCE_FAILS
- `<SESSIONEXTENSION>` + `<CONNECTIONREFERENCE>` — connexions physiques
- `<WORKFLOWVARIABLE>` — variables inter-sessions

**Nouvel agent à créer** : `OrchestratorAgent` — génère un script maître Python ou un manifest Databricks Workflows à partir du graphe `WORKFLOWLINK`.

**Effort estimé Phase 2** : 9-12 jours. ~75% du code Phase 1 réutilisé.

---

## Dimensions Informatica non couvertes (hors Phase 2)

Ces éléments ne sont PAS dans les XMLs de mapping — ils seront gérés par config générique :

| Dimension | Solution |
|---|---|
| Connexions Oracle (`SRC_ORACLE_PROD`) | Fichier `.env` / `connections.yaml` générique |
| Dépendances Control-M entre workflows | Manuel (reconstruit dans Databricks Workflows) |
| Worklets partagés | Export XML séparé → passer dans le pipeline |
| Parameter Files `.prm` | `os.environ.get()` — déjà fait pour `$$BATCH_DATE` |

**Ce qui EST dans le XML et que le parser doit lire en Phase 2** : `<SESSION>`, `<WORKFLOWLINK>`, `<TASKINSTANCE>`, `<WORKFLOWVARIABLE>`.

---

## Contexte client

- Environnement cible : **Databricks**
- Scheduler actuel : **Control-M** (pilote les workflows Informatica)
- Les scripts Python générés sont **compatibles Control-M** : `sys.exit(0/1)`, écriture atomique, `BATCH_DATE` en env var
- En migration : Control-M reste ou est remplacé par **Databricks Workflows** (les deux options supportées)

---

## Analyse marché (résumé)

Notre pipeline se positionne sur un **segment non couvert** : automatisation élevée + cible Python/Databricks + coût marginal quasi-nul.

- WhereScape : SQL-centric, pas de boucle QA, pas de doc auto
- SSIS Assistant : SSIS-only
- Big 4 : 3 000-8 000 €/workflow, main d'œuvre
- LLM artisanal : non reproductible

Notre pipeline : **< 1 € LLM + 30min relecture** · **4-7 minutes** par workflow · **0 hardcoding** · **dossier auditable** (canonical JSON + explanation.md + rapport QA)

Fenêtre d'avance estimée : **12-18 mois**.

---

## Pour continuer

**Prochaine action prioritaire** :
1. Tester `wf_transactions_hist.xml` (CRITICAL, dernier non testé) : `python pipeline/run_pipeline.py input/wf_transactions_hist.xml --force`
2. Corriger wf_accounts_scd2 (SCD2 guard) : analyser le crash puis fix dans `fixer_agent.py`
3. Obtenir un XML réel client avec plusieurs sessions chaînées pour démarrer Phase 2

**Git** :
```bash
git add <fichiers>
git commit -m "message"
git push -u origin claude/laughing-curie-04rzxm
```
