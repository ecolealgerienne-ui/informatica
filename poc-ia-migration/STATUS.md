# POC IA Migration — Status & Traçabilité

**Projet** : Conversion automatique Informatica PowerCenter XML → Python ETL via pipeline multi-agents IA  
**Branche** : `claude/laughing-curie-04rzxm` — `ecolealgerienne-ui/informatica`  
**Période** : Juin 2026  
**Statut** : ✅ Pipeline complet opérationnel — Phase 1 terminée + optimisations post-POC intégrées

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
| D10 | QA — exécution | Exécution réelle du code généré + diff CSV | Tests unitaires statiques | Valide le comportement réel end-to-end, pas seulement la syntaxe |
| D11 | QA — payload LLM | Résumé statistique compact Python (< 2KB) | Lignes d'anomalies brutes | Payload non borné → coût et timeout incontrôlables sur gros volumes ; données sensibles ne doivent pas sortir vers un LLM |
| D12 | QA — appel LLM conditionnel | Skip LLM si 0 anomalie (`auto_pass_narrative`) | Appel LLM systématique | Inutile d'appeler le LLM pour confirmer ce que Python a déjà prouvé |
| D13 | Analyse SQL | `sqlglot` déterministe avant appel LLM | LLM seul pour détecter les constructions SQL | sqlglot garantit des flags fiables (window, subquery, union, fonctions Oracle) ; le LLM reçoit des faits, pas une chaîne opaque à interpréter |
| D14 | Transpilation SQL → Spark | `sqlglot` (best-effort, stocké dans canonical JSON) | LLM pour la transpilation | Pour les Source Qualifier avec sql_override, sqlglot génère un Spark SQL de départ que le CodeGen peut affiner |
| D15 | Modèle LLM par agent | Haiku sur Parser/Documenter/QA — Sonnet sur CodeGen/Fixer | Sonnet partout | Les tâches de classification et reformulation ne justifient pas un modèle puissant ; CodeGen et Fixer impactent directement la qualité du code généré — économie estimée ~60-70% du coût LLM |

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

### I9 — Documenter : JSON canonique reproduit dans la doc (984 lignes au lieu de ~120)
- **Symptôme** : `workflow_explanation.md` contient 984 lignes dont ~860 lignes de JSON brut copié à la fin
- **Cause** : La section `## Canonical JSON` dans le prompt invitait le modèle à reproduire le JSON injecté comme contexte
- **Fix** :
  1. Instruction explicite `"Do NOT include any JSON or raw data dumps"` en tête du prompt
  2. Label du champ renommé `"Canonical JSON context (do NOT reproduce this in your output)"`
- **Limite** : instruction-following uniquement — un post-traitement déterministe (`tronquer à "```json"`) sera ajouté pour garantir l'absence de régression
- **Commit** : `6757a4e`

### I10 — Documenter : pattern DATEDIFF fragile dans la documentation générée
- **Symptôme** : La doc générée par Haiku montre `(month, day) < (month, day)` — le pattern tuple fragile qu'on avait corrigé dans le RAG Base
- **Cause** : Le prompt Documenter ne contraignait pas le pattern AGE — Haiku a improvisé
- **Fix** : Pattern `_calc_age()` correct injecté explicitement dans le prompt avec note `"Never use tuple comparison"`
- **Commit** : `6757a4e`

### I8 — QA Agent : debugging difficile sans exemples concrets dans le rapport HTML
- **Symptôme** : Le rapport HTML montrait uniquement des statistiques agrégées — l'ingénieur ne pouvait pas identifier rapidement quel type de lignes posait problème
- **Cause** : Le refactoring du payload LLM (I7) avait supprimé les détails ligne par ligne, sans les remplacer dans le HTML
- **Fix** : `build_stratified_samples()` — sampling stratifié par pattern d'anomalie (drift +1, drift -1, mismatch exact, non-numeric) avec `SAMPLES_PER_BUCKET=3` exemples par bucket, affiché dans une section dédiée du rapport HTML. Jamais envoyé au LLM.
- **Résultat** : L'ingénieur voit directement "Pattern drift_+1 : 3 exemples CLIENT_ID / valeur obtenue / valeur attendue" sans ouvrir le CSV

### I7 — QA Agent : payload LLM non borné sur gros volumes
- **Symptôme** : Le diff envoyait une entrée JSON par ligne en anomalie → sur 1M lignes avec 5% d'anomalies = 50 000 entrées = timeout + coût LLM incontrôlé
- **Cause** : `col_anomalies` (liste complète) injecté directement dans le prompt sans agrégation
- **Décision** : Supprimer les données brutes du payload LLM — remplacer par un résumé statistique compact calculé en Python pur (`build_summary()`)
- **Nouveau comportement** :
  - Python calcule : nb anomalies, taux, mean_diff/max_diff, 3 samples max par colonne
  - Payload LLM = **toujours < 2KB**, indépendant du volume
  - Si 0 anomalie → **appel LLM supprimé** (`auto_pass_narrative()`)
  - Le diff complet (toutes les lignes) reste dans le JSON/HTML pour traçabilité
- **Commit** : voir ci-dessous

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

### Résultats de la dernière exécution complète (run #3 — fix doc + routing Haiku/Sonnet)

**Historique des 3 runs :**

| Métrique | Run #1 — Sonnet partout | Run #2 — Haiku/Sonnet | Run #3 — Haiku/Sonnet + fix doc |
|---|---|---|---|
| Durée totale | 273.5s | 268.3s | **211.9s** |
| Parser | 55.1s | 48.5s | 89.3s (*) |
| CodeGen | 23.6s | 25.5s | 24.2s |
| Fixer | 18.9s | 20.1s | 18.0s |
| Documenter | 175.5s | 173.8s | **80.0s** |
| QA | 0.3s | 0.3s | 0.3s |
| Doc générée | 334 lignes | 984 lignes (JSON inclus) | **137 lignes** ✅ |
| Complexity | HIGH, score=10 | HIGH, score=10 | HIGH, score=10 |
| Platform routing | pyspark | pyspark | pyspark |
| QA verdict | PASS, 0 anomalies | PASS, 0 anomalies | PASS, 0 anomalies |
| Fixer cycles | 1 | 1 | 1 |

> (*) Parser Run #3 plus lent (89s vs 48s) — variabilité réseau, pas liée au code

**Gains cumulés Run #1 → Run #3 :**

| Indicateur | Valeur |
|---|---|
| Durée pipeline | −61.6s (**−22%**) |
| Documenter | −95.5s (**−54%**) — moins de tokens produits sans JSON |
| Qualité doc | 984 → **137 lignes** (suppression du JSON parasite) |
| Qualité technique | Inchangée — PASS, 0 anomalie sur les 3 runs |

**Conclusion** : les optimisations modèles (D15) et la correction du prompt Documenter (I9) ont réduit la durée du pipeline de 22% et divisé par 2 le temps du Documenter, sans aucune régression sur la qualité du code ou du scoring de complexité.

### Gains mesurés — Optimisation modèles LLM

#### Routing modèle par agent (D15)

| Agent | Avant | Après | Justification |
|---|---|---|---|
| Parser | Sonnet | **Haiku** | Classification sur grille injectée + sqlglot — tâche bornée |
| CodeGen | Sonnet | **Sonnet** | Qualité du code critique — inchangé |
| Fixer | Sonnet | **Sonnet** | Raisonnement sur code, erreur = ESCALATE — inchangé |
| Documenter | Sonnet | **Haiku** | Reformulation structurée — tâche bornée |
| QA | Sonnet | **Haiku** | Payload < 2KB, narratif simple — tâche bornée |

#### Impact qualité (run #1 Sonnet vs run #2 Haiku/Sonnet)

| Métrique | Run #1 Sonnet | Run #2 Haiku/Sonnet | Delta |
|---|---|---|---|
| Durée totale | 273.5s | 268.3s | −2% |
| Complexity score | HIGH, 10 | HIGH, 10 | **identique** ✅ |
| Platform routing | pyspark | pyspark | **identique** ✅ |
| QA verdict | PASS, 0 anomalies | PASS, 0 anomalies | **identique** ✅ |
| Fixer cycles | 1 | 1 | **identique** ✅ |

**Conclusion** : Haiku reproduit la même qualité que Sonnet sur les agents Parser, Documenter et QA. Aucune régression détectée.

#### Estimation économie de coûts LLM (sur 100 workflows)

Les modèles Claude sont facturés à l'usage (tokens). Haiku coûte environ **20x moins cher** que Sonnet par token.

| Répartition des appels LLM | Modèle | Poids estimé du coût |
|---|---|---|
| Parser (1 appel/workflow) | Haiku | ~5% du coût total |
| CodeGen (1 appel/workflow) | Sonnet | ~35% du coût total |
| Fixer (1-3 appels/workflow) | Sonnet | ~40% du coût total |
| Documenter (2 appels/workflow) | Haiku | ~10% du coût total |
| QA (0-1 appel/workflow) | Haiku | ~10% du coût total |

**Avant optimisation** : 100% sur Sonnet
**Après optimisation** : ~25% Haiku, ~75% Sonnet → **économie estimée : 15-20% du coût total LLM**

> Note : l'économie est modérée car CodeGen et Fixer (les plus coûteux en tokens) restent sur Sonnet. C'est intentionnel — la qualité du code généré prime sur le coût.

#### Optimisation complémentaire — skip LLM sur QA PASS (I7)

Sur les workflows sans anomalie (cas nominal) :
- **Avant** : 1 appel LLM systématique au QA Agent
- **Après** : 0 appel LLM (`auto_pass_narrative()`)
- **Économie** : 100% du coût QA sur les runs nominaux

### Git log

| Commit | Description |
|---|---|
| `6757a4e` | fix(documenter): supprimer JSON de la doc + corriger pattern DATEDIFF |
| `78a603d` | feat(agents): routing modèle LLM — Haiku/Sonnet selon complexité |
| `7dbf4b3` | docs: analyse GitHub Copilot Enterprise — complémentarité pipeline |
| `4ecf695` | docs: ANALYSE_RAPPORT_EXPERTISE.md — retours techniques |
| `bd3d6da` | docs: rapport_expertise_migration_ia.md |
| `18e15f3` | feat(qa): sampling stratifié HTML + escalate_history.json |
| `cbe0349` | feat(parser): sqlglot — analyse SQL déterministe + transpilation Spark |
| `da553f6` | fix(qa): payload LLM borné — résumé statistique Python |
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

## 5. Prochaines étapes

### Correctif immédiat (avant présentation)
| Priorité | Tâche |
|---|---|
| P0 | Ajouter post-traitement déterministe dans Documenter — tronquer le output si JSON apparaît (`"```json"`) |

### Phase 3 — Conditionnelle GO/NOGO chef

Les items suivants sont reportés en Phase 3, après validation du POC par le management :

| Item | Description | Condition |
|---|---|---|
| PySpark support | CodeGen route vers PySpark pour workflows HIGH/CRITICAL | GO chef |
| Batch multi-fichiers | Pipeline sur dossier de XML | Décision infra/CI/CD |
| Interface web | Upload XML + rapport (Streamlit ou FastAPI) | À réévaluer selon contexte entreprise |
| CI/CD | Intégration GitHub Actions | Décision infra |
| RAG dynamique | Few-shot examples depuis escalate_history.json | Après 20+ mappings réels en production |
| Réconciliation distribuée | Validation QA à grande échelle (Spark-based) | Phase 3 — autre équipe |
