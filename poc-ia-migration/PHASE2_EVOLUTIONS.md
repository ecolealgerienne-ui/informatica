# Phase 2 — Évolutions du pipeline : Support Multi-Sessions & Orchestration

**Statut** : Spécification — non implémenté  
**Prérequis** : Phase 1 validée (pipeline mono-session opérationnel sur LOW-HIGH)  
**Référence spec Phase 1** : [poc_ia_migration_spec.md](poc_ia_migration_spec.md) §9  
**Référence résultats Phase 1** : [RESULTS.md](RESULTS.md)

---

## Contexte et déclencheur

En Phase 1, le pipeline repose sur une hypothèse implicite :

> **1 workflow XML = 1 mapping = 1 script Python généré**

Cette hypothèse est valide pour les XMLs de test du POC. Elle ne l'est pas pour les workflows réels du client, où un workflow Informatica PowerCenter peut enchaîner **N sessions** (chacune liée à un mapping différent), reliées par des conditions de succès/échec.

### Ce que dit la documentation officielle Informatica (powrmart.dtd)

```
<WORKFLOW>
  ├── <SESSION name="s_load_clients"  mappingname="m_clients_dim">
  ├── <SESSION name="s_load_products" mappingname="m_products_dim">
  ├── <SESSION name="s_load_orders"   mappingname="m_orders_fact">
  ├── <WORKFLOWLINK fromtask="s_load_clients"  totask="s_load_products"
  │                 condition="$s_load_clients.Status = SUCCEEDED"/>
  ├── <WORKFLOWLINK fromtask="s_load_products" totask="s_load_orders"
  │                 condition="$s_load_products.Status = SUCCEEDED"/>
  └── <WORKFLOWVARIABLE> ...
</WORKFLOW>
```

Le chaînage est défini par `<WORKFLOWLINK>` avec `FROMTASK`, `TOTASK`, et une `CONDITION` optionnelle. C'est la structure à parser et à reproduire en Python.

### Autres dimensions non couvertes en Phase 1

| Dimension | Présent dans XML | Parsé en Phase 1 |
|---|---|---|
| `<SESSION>` (N par workflow) | ✅ | ⚠️ Partiel (1 session lue) |
| `<WORKFLOWLINK>` (graphe de dépendances) | ✅ | ❌ |
| `<TASKINSTANCE>` (fail parent si échec) | ✅ | ❌ |
| `<SESSIONEXTENSION>` + `<CONNECTIONREFERENCE>` | ✅ | ❌ |
| `<WORKFLOWVARIABLE>` (variables inter-sessions) | ✅ | ❌ |
| `<WORKLET>` (sous-workflow réutilisable) | ✅ (wf_smoke_test) | ❌ |
| `<MAPPINGVARIABLE>` complètes ($$MAX_ROWS, etc.) | ✅ | ⚠️ Partiel ($$BATCH_DATE seulement) |

---

## Architecture cible Phase 2

### Aujourd'hui (Phase 1)

```
wf_finance.xml
    │
    ▼
Parser ──→ canonical.json (1 mapping)
    │
    ▼
CodeGen ──→ wf_finance.py (1 script)
    │
    ▼
Fixer ──→ wf_finance_fixed.py
    │
    ▼
Documenter ──→ wf_finance_documented.py + explanation.md
    │
    ▼
QA ──→ rapport HTML
```

### Phase 2 cible

```
wf_finance.xml (3 sessions chaînées)
    │
    ▼
Parser ──→ canonical.json
              ├── sessions[]          ← N mappings
              ├── links[]             ← graphe WORKFLOWLINK
              └── workflow_variables  ← variables inter-sessions
    │
    ├──→ CodeGen × N ──→ s_clients_dim.py
    │                    s_products_dim.py
    │                    s_orders_fact.py
    │
    ├──→ Fixer × N  ──→ (vérifie chaque script)
    │
    ├──→ Documenter × N + Documenter global
    │                    ──→ explanation_global.md (graphe du workflow)
    │
    ├──→ OrchestratorAgent (NOUVEAU)
    │                    ──→ orchestrator.py (script maître)
    │
    └──→ QA ──→ exécute orchestrator.py + rapport HTML
```

---

## Évolutions par agent

### Agent 1 — Parser Agent

**Changement** : Réécriture de `_parse_workflow()`.

**Aujourd'hui** :
```python
def _parse_workflow(folder):
    wf = folder.find("WORKFLOW")
    task = wf.find("TASK")          # ← une seule tâche lue
    ...
```

**Phase 2** :
```python
def _parse_workflow(folder):
    wf = folder.find("WORKFLOW")

    # Lire toutes les sessions
    sessions = []
    for sess in wf.findall("SESSION"):
        sessions.append({
            "name":        sess.get("NAME"),
            "mapping":     sess.get("MAPPINGNAME"),
            "write_mode":  _get_attr(sess, "Treat source rows as", "Insert"),
            "connections": _parse_session_connections(sess),
            "fail_parent": _get_taskinstance_attr(wf, sess.get("NAME"),
                                                  "FAIL_PARENT_IF_INSTANCE_FAILS"),
        })

    # Lire le graphe de chaînage
    links = []
    for link in wf.findall("WORKFLOWLINK"):
        links.append({
            "from":      link.get("FROMTASK"),
            "to":        link.get("TOTASK"),
            "condition": link.get("CONDITION", ""),
        })

    # Variables workflow (inter-sessions)
    wf_variables = [
        {"name": v.get("NAME"), "default": v.get("DEFAULTVALUE"), "datatype": v.get("DATATYPE")}
        for v in wf.findall("WORKFLOWVARIABLE")
    ]

    return {
        "workflow_name":      wf.get("NAME"),
        "server":             wf.get("SERVERNAME"),
        "is_enabled":         wf.get("ISENABLED") == "YES",
        "concurrent":         wf.get("CONCURRENT", "NO") == "YES",
        "max_errors":         int(wf.get("MAXERRORS", 1)),
        "sessions":           sessions,
        "links":              links,
        "workflow_variables": wf_variables,
    }
```

**Canonical JSON résultant** :
```json
{
  "workflow": {
    "sessions": [
      {"name": "s_clients_dim",  "mapping": "m_clients_dim",  "write_mode": "Insert"},
      {"name": "s_orders_fact",  "mapping": "m_orders_fact",  "write_mode": "Data Driven"}
    ],
    "links": [
      {"from": "s_clients_dim", "to": "s_orders_fact",
       "condition": "$s_clients_dim.Status = SUCCEEDED"}
    ],
    "workflow_variables": [],
    "concurrent": false,
    "max_errors": 1
  }
}
```

**Effort estimé** : 2-3 jours  
**Impact** : Aucun changement pour les workflows mono-session (rétrocompatible)

---

### Agent 2 — CodeGen Agent

**Changement** : Boucle sur les sessions, génère un script par session.

**Aujourd'hui** : 1 appel Claude → 1 script.

**Phase 2** :
```python
for session in canonical["workflow"]["sessions"]:
    session_canonical = extract_mapping_for_session(canonical, session["mapping"])
    script = codegen(session_canonical)
    write(f"output/{workflow_name}/{session['name']}.py", script)
```

Chaque script est identique à ce que Phase 1 produit aujourd'hui — aucun changement de prompt, aucun changement de logique de génération.

**Effort estimé** : 1-2 jours  
**Impact** : Rétrocompatible — pour 1 session, comportement identique à Phase 1

---

### Agent 3 — Fixer Agent

**Changement** : Boucle sur N scripts, un Fixer par session.

**Aujourd'hui** : `fixer(script_path)` → 1 appel.

**Phase 2** :
```python
for session_script in session_scripts:
    fixer(session_script)   # identique à aujourd'hui
```

Aucun changement de logique interne. Seule la boucle d'appel change dans le pipeline orchestrateur.

**Effort estimé** : 0.5 jour  
**Impact** : Nul sur la logique du Fixer

---

### Agent 4 — Documenter Agent

**Changement** : Documentation par session + documentation globale du workflow.

**Phase 2** :
1. Documenter tourne N fois (une par session) — identique à aujourd'hui
2. Un appel supplémentaire génère `workflow_overview.md` avec :
   - Le graphe de dépendances entre sessions (ASCII ou Mermaid)
   - Les variables workflow inter-sessions
   - L'ordre d'exécution et les conditions de branchement

**Effort estimé** : 1 jour  
**Impact** : Additif — ne modifie pas le comportement existant

---

### Nouvel Agent — OrchestratorAgent (NOUVEAU)

**Rôle** : Lire le graphe `links[]` du canonical JSON et générer un script maître Python qui enchaîne les sessions selon les conditions `WORKFLOWLINK`.

**Script généré** (`orchestrator.py`) :
```python
import subprocess, sys

def run_session(script_path: str) -> int:
    result = subprocess.run(["python", script_path], capture_output=True, text=True)
    print(result.stdout)
    if result.returncode != 0:
        print(f"[ERROR] {script_path} failed", file=sys.stderr)
    return result.returncode

def main():
    # Graphe généré depuis WORKFLOWLINK
    if run_session("s_clients_dim.py") != 0:
        sys.exit(1)
    if run_session("s_products_dim.py") != 0:
        sys.exit(1)
    if run_session("s_orders_fact.py") != 0:
        sys.exit(1)
    sys.exit(0)

if __name__ == "__main__":
    main()
```

Pour les branchements conditionnels (condition != SUCCEEDED simple) :
```python
# Condition : $s_clients_dim.Status = SUCCEEDED OR $s_clients_dim.Status = FAILED
ret = run_session("s_clients_dim.py")
if ret == 0 or ret == 1:     # SUCCEEDED or FAILED → continuer
    run_session("s_notify.py")
```

**Alternative pour Databricks Workflows** : générer un `workflow_manifest.json` :
```json
{
  "name": "wf_finance_daily",
  "tasks": [
    {"task_key": "s_clients_dim",  "python_file": "s_clients_dim.py",  "depends_on": []},
    {"task_key": "s_orders_fact",  "python_file": "s_orders_fact.py",  "depends_on": ["s_clients_dim"]}
  ]
}
```

Ce manifest peut être importé directement dans Databricks Workflows ou converti en JCL Control-M.

**Effort estimé** : 2-3 jours  
**Impact** : Nouveau composant — pas de régression possible sur Phase 1

---

### Agent 5 — QA Agent

**Changement** : Exécuter le script orchestrateur au lieu du script de session unique.

**Phase 2** :
```python
# Au lieu de :
subprocess.run(["python", session_script])

# Phase 2 :
subprocess.run(["python", "orchestrator.py"])
# puis comparer les sorties de chaque session
```

La logique de data diff reste identique — une comparaison par table cible.

**Effort estimé** : 1 jour  
**Impact** : Faible

---

### Pipeline Orchestrateur (`run_pipeline.py`)

**Changement** : Boucle sur les sessions + étape OrchestratorAgent.

**Phase 2 — structure** :
```
Step 1 : Parser        → canonical.json (avec sessions[] et links[])
Step 2 : CodeGen × N   → session_scripts/
Step 3 : Fixer × N     → session_scripts_fixed/
Step 4 : OrchestratorAgent → orchestrator.py
Step 5 : Documenter × N + global → docs/
Step 6 : QA            → rapport HTML
```

Le checkpoint system s'adapte : checkpoints par session (`{wf_name}/{session_name}.py`).

**Effort estimé** : 1-2 jours  
**Impact** : Rétrocompatible — pour 1 session, comportement identique à Phase 1

---

## Récapitulatif des efforts

| Composant | Type de changement | Effort | Priorité |
|---|---|---|---|
| Parser — `_parse_workflow()` | Réécriture | 2-3 jours | P0 — bloquant |
| CodeGen — boucle N sessions | Adaptation | 1-2 jours | P0 — bloquant |
| OrchestratorAgent | Nouveau | 2-3 jours | P0 — bloquant |
| Pipeline — boucle + checkpoints | Adaptation | 1-2 jours | P0 — bloquant |
| Fixer — boucle | Adaptation | 0.5 jour | P1 |
| Documenter — doc globale | Extension | 1 jour | P1 |
| QA — exécution orchestrateur | Adaptation | 1 jour | P1 |

**Effort total estimé : 9-12 jours**

---

## Ce qui ne change pas

- La logique de transformation dans chaque script généré (SQ, Expression, Filter, Lookup, Aggregator, Normalizer)
- Le Fixer checklist (statique + sémantique)
- Les fixtures synthétiques et la logique de data diff
- La RAG Base
- La généricité D16 — zéro hardcoding XML-spécifique
- Le modèle de routing (Haiku / Sonnet / Opus par agent)

**Estimation de réutilisation du code Phase 1 : ~75%** — seule l'enveloppe d'orchestration change, pas le cœur de génération.

---

## Prérequis avant de démarrer Phase 2

1. **Obtenir un export XML réel du client** avec un workflow multi-sessions (3+ sessions chaînées) pour valider les hypothèses de parsing
2. **Valider wf_transactions_hist (CRITICAL)** en Phase 1 — dernier workflow non testé
3. **Corriger wf_accounts_scd2 (SCD2 guard)** — le mode `Data Driven` est central en Phase 2
4. **Décision architecture** : script maître Python ou manifest Databricks Workflows ou les deux

---

*Document créé le 25 juin 2026 — à mettre à jour dès réception des XMLs réels client.*
