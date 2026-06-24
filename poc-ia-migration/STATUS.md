# POC IA Migration — Status & Traçabilité

**Projet** : Conversion automatique Informatica PowerCenter XML → Python ETL via pipeline multi-agents IA  
**Branche** : `claude/laughing-curie-04rzxm` — `ecolealgerienne-ui/informatica`  
**Période** : Juin 2026  
**Statut** : ✅ Pipeline complet opérationnel — Phase 1 terminée

---

## 1. Chronologie des étapes

### Étape 0 — Initialisation du projet (`354dd31`)
- Lecture et analyse du document de spécification fourni
- Création de la branche `claude/laughing-curie-04rzxm`
- Sauvegarde du spec dans `poc_ia_migration_spec.md`
- Création de la structure de dossiers : `agents/`, `pipeline/`, `rag_base/`, `setup/`, `input/`, `tests/`, `output/`

**Livrable** : Repo initialisé, spec tracée dans git

---

### Étape 1 — Fichiers input & RAG Base (`8306179`)

**Fichiers produits :**

| Fichier | Rôle |
|---|---|
| `setup/generate_sample_xml.py` | Génère `input/wf_clients_dim.xml` — mapping Informatica fictif réaliste |
| `setup/generate_golden_dataset.py` | Génère `tests/golden_dataset.csv` (30 lignes), `tests/ref_statut.csv`, `tests/expected_output.csv` (23 lignes après filtre) |
| `rag_base/transformation_map.json` | Mapping Informatica fonctions → équivalents pandas/numpy |
| `rag_base/python_templates.md` | Templates de code obligatoires (structure batch, lookup, `_calc_age`) |
| `rag_base/pyspark_patterns.md` | Placeholder Phase 2 |
| `requirements.txt` | `pandas>=2.0.0`, `openpyxl`, `python-dotenv`, `jinja2` |
| `.env.example` | `BATCH_DATE=2026-01-01` |

**Décision** : SYSDATE fixé à `2026-06-24` dans le golden dataset pour que le calcul AGE soit reproductible à chaque run.

---

### Étape 2 — Environnement local (`3105793`)
- Création de `SETUP_LOCAL.md` — guide complet WSL2 + Conda + VSCode
- Configuration `.vscode/launch.json` — debug configs pour chaque agent + pipeline, avec `envFile`

**Environnement cible :**
- WSL2 Ubuntu
- Conda env `poc-ia`, Python 3.11
- VSCode avec extensions Python + Pylance
- Claude Code CLI authentifié via OAuth (`claude login`)

---

### Étape 3 — Agent 1 : Parser Agent (`65eeb3f`)

**Rôle** : XML Informatica → JSON canonique  
**Architecture** :
- Parsing XML **déterministe** via `xml.etree.ElementTree` (pas de LLM ici)
- Appel Claude Code CLI pour **analyse sémantique** uniquement (détection fonctions propriétaires, routing decision)

**Sorties** :
- `output/01_canonical_json/wf_clients_dim.json`

**Méthode d'appel Claude Code** :
```python
subprocess.run(["claude", "-p", "--output-format", "text"],
               input=prompt, capture_output=True, text=True, timeout=180)
```

---

### Étape 4 — Agent 2 : CodeGen Agent (`b36e363` + `4ddb960` + `df5b25c`)

**Rôle** : JSON canonique → code Python batch draft  
**Architecture** :
- Lit le JSON canonique + injecte RAG Base (transformation_map + python_templates)
- Un seul appel Claude Code, extraction du bloc `python` de la réponse

**Sorties** :
- `output/02_generated_code/wf_clients_dim.py`

**Correctifs appliqués** (voir §3) :
- Timeout : prompt passé via stdin au lieu d'argument CLI
- Extraction : `clean_code()` robuste sur 3 patterns (bloc \`\`\`python, bloc générique, raw Python)

---

### Étape 5 — Agent 3 : Fixer Agent (`fcf34a7`)

**Rôle** : Vérification statique + correction sémantique en boucle (max 3 cycles)  
**Architecture** :
- Checks statiques **sans LLM** : `ast.parse`, présence de `_load_data/transform/load`, patterns interdits (`iterrows`, `apply`)
- Détection spécifique du pattern `_calc_age` fragile (tuple comparison avec NaT)
- Appel Claude Code pour correction sémantique si des problèmes détectés
- Statut final : `OK` | `ESCALATE`

**Sorties** :
- `output/03_fixed_code/wf_clients_dim_fixed.py`
- `output/03_fixed_code/fix_report.json`

---

### Étape 6 — Agent 4 : Documenter Agent (`ccf5540`)

**Rôle** : Générer documentation métier + code annoté  
**Architecture** :
- **Appel 1** → `workflow_explanation.md` : doc structurée bilingue FR/EN avec diagramme ASCII du data flow
- **Appel 2** → `wf_clients_dim_documented.py` : code annoté (commentaires FR=règles métier, EN=notes techniques)

**Sorties** :
- `output/03_fixed_code/workflow_explanation.md`
- `output/03_fixed_code/wf_clients_dim_documented.py`

**Motivation** : Ajout décidé en cours de session — le code généré seul ne suffit pas pour la revue métier.

---

### Étape 7 — Agent 5 : QA Agent (`f36ae90` + `599f2a8`)

**Rôle** : Exécuter le code généré, comparer avec le golden dataset, produire un rapport  
**Architecture** :
- Exécute le fichier annoté via `subprocess.run(["python", annotated_code_file])`
- Data diff avec matrice de tolérance (exact / ±1 / exclus)
- Appel Claude Code pour interprétation narrative des anomalies
- Génération rapport JSON + HTML via Jinja2

**Sorties** :
- `output/04_data_diff_report/data_diff_report.json`
- `output/04_data_diff_report/data_diff_report.html`

**Verdict** : `PASS` | `FAIL`

**Correctif** : `select_dtypes(include=["object", "str"])` pour compatibilité pandas 4.

---

### Étape 8 — Pipeline orchestrateur (`c593ede`)

**Rôle** : Enchaîner les 5 agents, gérer les erreurs, produire un résumé  
**Fichier** : `pipeline/run_pipeline.py`

**Comportement** :
- Stop immédiat si un agent retourne une erreur ou `ESCALATE`
- Exit code 1 si QA != PASS
- Log horodaté avec temps écoulé par étape

---

### Étape 9 — Matrice de complexité formelle (`71f0ef0`)

**Motivation** : Les flags de complexité générés par le LLM étaient incohérents d'un run à l'autre car aucun critère objectif n'était défini.

**Solution** : Créer une grille de scoring déterministe injectée dans le prompt du Parser Agent.

**Fichier** : `rag_base/complexity_matrix.json`

**Contenu** :
- `transformation_scores` : base_score + modificateurs par type de transformation
- `global_modifiers` : paramètre fichier, count transformations, cibles multiples, etc.
- `thresholds` : LOW (0-3) / MEDIUM (4-8) / HIGH (9-14) / CRITICAL (15+)
- `routing_rules` : python / pyspark / databricks selon score
- Estimations en jours par seuil : 0.5j / 1-2j / 3-5j / >5j

**Impact sur le Parser Agent** :
- Charge `complexity_matrix.json` et l'injecte dans le prompt
- Le JSON canonique inclut maintenant `workflow_complexity` (score global, flag, jours estimés) et `complexity_score` + `score_breakdown` par transformation

---

## 2. Décisions techniques

| # | Décision | Choix retenu | Alternative rejetée | Raison |
|---|---|---|---|---|
| D1 | Backend LLM | Claude Code CLI (`claude -p`) via subprocess + OAuth | Anthropic API directe avec clé API | Pas de clé API nécessaire — authentification réutilise la session Claude Code existante |
| D2 | Mode d'appel CLI | `input=prompt` (stdin) | Prompt en argument CLI (`-p "..."`) | Les prompts longs (>8KB) plantent avec arg CLI — stdin n'a pas de limite |
| D3 | Parsing XML | `xml.etree.ElementTree` déterministe | LLM pour parser le XML | Le LLM introduit de la variabilité sur une tâche structurée ; le parsing déterministe garantit la reproductibilité |
| D4 | Transmission des données entre agents | JSON canonique sur disque | Passage en mémoire (objets Python) | Traçabilité, reprise possible après crash, inspection manuelle |
| D5 | Reproducibilité des données de test | SYSDATE fixé `2026-06-24` dans le golden dataset | SYSDATE dynamique | Les calculs AGE varient avec la date — fixé = résultats stables |
| D6 | Vectorisation des calculs | Opérations pandas vectorisées uniquement | `apply()` / `iterrows()` | Performances sur grand volume ; le Fixer Agent détecte et signale les violations |
| D7 | Load idempotent | Écriture vers `.tmp` puis `os.replace()` | Écriture directe | Protège contre la corruption en cas d'interruption |
| D8 | Scoring de complexité | Matrice JSON déterministe injectée dans le prompt | Score calculé librement par le LLM | Cohérence inter-runs et auditabilité des décisions de routing |
| D9 | Agent Documenter | Deux appels LLM séparés (doc MD + code annoté) | Un seul appel LLM | Contexte plus propre, qualité meilleure sur chaque tâche distincte |
| D10 | QA | Exécution réelle du code généré + diff CSV | Tests unitaires statiques | Valide le comportement réel end-to-end, pas seulement la syntaxe |

---

## 3. Incidents & corrections

### I1 — `TimeoutExpired` sur Parser et CodeGen
- **Symptôme** : `subprocess.TimeoutExpired` après 120s
- **Cause** : Prompt passé comme argument CLI (`-p "prompt..."`) — taille > limite shell
- **Fix** : `subprocess.run(input=prompt, ...)` — stdin sans limite de taille
- **Commit** : `4ddb960`

### I2 — CodeGen retourne du Markdown prose au lieu de code Python
- **Symptôme** : Fichier généré = 22 lignes de texte Markdown, pas de code
- **Cause** : Prompt sans contrainte d'output explicite ; Claude répond naturellement en prose
- **Fix** :
  1. Règle `CRITICAL OUTPUT RULE` au début du system prompt : "répondre avec un seul bloc \`\`\`python"
  2. `clean_code()` robuste : extraction sur 3 patterns (bloc \`\`\`python, bloc générique, raw Python)
- **Commit** : `df5b25c`

### I3 — `_calc_age()` fragile avec valeurs NaT
- **Symptôme** : Code généré utilise `(ref_date.month, ref_date.day) >= pd.Series(list(zip(...)))` — fragile sur NaT
- **Cause** : Pattern non présent dans le RAG Base, LLM a improvisé
- **Fix** : Mise à jour de `rag_base/python_templates.md` avec le pattern correct (comparaisons booléennes séparées month/day)
- **Détection** : Fixer Agent détecte et corrige en 1 cycle
- **Commit** : `54fd56d`

### I4 — `select_dtypes` pandas 4 deprecation warning
- **Symptôme** : Warning `FutureWarning: select_dtypes(include='object')` — supprimé en pandas 4
- **Cause** : `pandas>=4.0` — `"object"` seul plus accepté
- **Fix** : `include=["object", "str"]`
- **Commit** : `599f2a8`

### I5 — `claude -p` retourne "Invalid API key"
- **Symptôme** : Erreur `Invalid API key · Please run /login` à la première exécution
- **Cause** : Session Claude Code non authentifiée dans le nouvel environnement WSL
- **Fix** : `claude` → `/login` → OAuth browser flow → session persistée
- **Impact** : Documentation ajoutée dans `SETUP_LOCAL.md` étape 8

### I6 — `git clone claude/laughing-curie-04rzxm informatica`
- **Symptôme** : Erreur git — tentative de cloner un nom de branche comme URL
- **Cause** : Confusion URL repo vs nom de branche
- **Fix** : Cloner l'URL HTTPS du repo, puis `git checkout claude/laughing-curie-04rzxm`

---

## 4. État actuel

### Fichiers produits

```
poc-ia-migration/
├── poc_ia_migration_spec.md          # Spec originale
├── requirements.txt                  # Dépendances Python
├── .env.example                      # Variables d'environnement
├── SETUP_LOCAL.md                    # Guide installation WSL2+Conda+VSCode
├── STATUS.md                         # Ce fichier
│
├── input/
│   └── wf_clients_dim.xml            # Mapping Informatica sample
│
├── tests/
│   ├── golden_dataset.csv            # 30 lignes source
│   ├── ref_statut.csv                # Table référence STATUT
│   └── expected_output.csv           # 23 lignes attendues (après filtre STATUT_CODE != 'I')
│
├── rag_base/
│   ├── transformation_map.json       # Informatica → pandas/numpy mapping
│   ├── complexity_matrix.json        # Grille de scoring de complexité
│   ├── python_templates.md           # Templates de code obligatoires
│   └── pyspark_patterns.md           # Placeholder Phase 2
│
├── setup/
│   ├── generate_sample_xml.py        # Génère input/wf_clients_dim.xml
│   └── generate_golden_dataset.py    # Génère tests/*.csv
│
├── agents/
│   ├── parser_agent.py               # Agent 1 — XML → JSON canonique
│   ├── codegen_agent.py              # Agent 2 — JSON → Python draft
│   ├── fixer_agent.py                # Agent 3 — Static checks + correction
│   ├── documenter_agent.py           # Agent 4 — Doc MD + code annoté
│   └── qa_agent.py                   # Agent 5 — Data diff + rapport HTML
│
├── pipeline/
│   └── run_pipeline.py               # Orchestrateur 5 agents en séquence
│
└── output/
    ├── 01_canonical_json/
    │   └── wf_clients_dim.json       # JSON canonique avec complexity scoring
    ├── 02_generated_code/
    │   └── wf_clients_dim.py         # Code Python draft généré
    ├── 03_fixed_code/
    │   ├── wf_clients_dim_fixed.py   # Code corrigé
    │   ├── fix_report.json           # Rapport des corrections
    │   ├── workflow_explanation.md   # Documentation métier bilingue
    │   └── wf_clients_dim_documented.py  # Code annoté final
    └── 04_data_diff_report/
        ├── data_diff_report.json     # Résultats du data diff
        └── data_diff_report.html     # Rapport HTML interactif
```

### Résultats de la dernière exécution complète

| Étape | Statut | Détail |
|---|---|---|
| Parser Agent | ✅ | JSON canonique généré avec complexity scoring |
| CodeGen Agent | ✅ | Code Python généré |
| Fixer Agent | ✅ | Status=OK, 1 cycle utilisé |
| Documenter Agent | ✅ | MD + code annoté générés |
| QA Agent | ✅ | Verdict=PASS, 0 anomalies |
| **Pipeline complet** | ✅ | **Exit code 0** |

### Git log

| Commit | Description |
|---|---|
| `71f0ef0` | feat(parser): add formal complexity scoring matrix |
| `c593ede` | feat: pipeline orchestrateur |
| `599f2a8` | fix: qa_agent pandas 4 deprecation |
| `f36ae90` | feat: QA Agent |
| `ccf5540` | feat: Documenter Agent |
| `fcf34a7` | feat: Fixer Agent |
| `54fd56d` | fix: pattern _calc_age() dans RAG Base |
| `df5b25c` | fix: codegen extraction robuste + prompt strict |
| `4ddb960` | fix: prompts via stdin |
| `b36e363` | feat: CodeGen Agent |
| `65eeb3f` | feat: Parser Agent |
| `3105793` | chore: SETUP_LOCAL.md + VSCode config |
| `8306179` | feat: fichiers input, RAG base, scripts setup |
| `354dd31` | chore: initialisation projet + spec |

---

## 5. Prochaines étapes (Phase 2)

| Priorité | Tâche |
|---|---|
| P1 | Tester le Parser Agent après ajout de la complexity_matrix — vérifier le JSON canonique |
| P1 | Valider la cohérence des scores sur un deuxième mapping Informatica |
| P2 | Implémenter `rag_base/pyspark_patterns.md` pour les workflows HIGH/CRITICAL |
| P2 | Étendre le CodeGen Agent pour router vers PySpark si `target_platform=pyspark` |
| P3 | Interface web légère (Streamlit ou FastAPI) pour uploader un XML et voir le rapport |
| P3 | Support multi-mappings — pipeline batch sur un dossier d'XML |
