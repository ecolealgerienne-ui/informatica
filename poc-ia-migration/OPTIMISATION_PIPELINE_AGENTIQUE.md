# Optimisation des Pipelines Agentiques LLM
## Retour d'expérience — POC IA Migration Informatica → Databricks

**Date** : Juin 2026  
**Pipeline** : 5 agents LLM (Parser → CodeGen → Fixer → Documenter → QA)  
**Modèles** : Claude Haiku 4.5 (Parser, Documenter, QA) · Claude Sonnet 4.6 (CodeGen, Fixer)  
**Objectif** : Réduire les coûts et la latence sans dégrader la qualité des migrations

---

## Table des matières

1. [Anatomie du pipeline et volumes observés](#1-anatomie-du-pipeline-et-volumes-observés)
2. [Optimisation des tokens INPUT](#2-optimisation-des-tokens-input)
3. [Optimisation des tokens OUTPUT](#3-optimisation-des-tokens-output)
4. [Stratégies LLMOps avancées](#4-stratégies-llmops-avancées)
5. [Feuille de route universelle](#5-feuille-de-route-universelle)
6. [Plan d'implémentation — POC IA Migration](#6-plan-dimplémentation--poc-ia-migration)

---

## 1. Anatomie du pipeline et volumes observés

### 1.1 Description du pipeline

```
XML Informatica
     │
     ▼
┌─────────────┐   canonical.json
│  PARSER     │ ──────────────────────────────────────┐
│  (Haiku)    │                                        │
└─────────────┘                                        │
     │ canonical.json                                  │
     ▼                                                 │
┌─────────────┐   draft.py                             │
│  CODEGEN    │ ───────────────────────┐               │
│  (Sonnet)   │                        │               │
└─────────────┘                        │               │
     │ draft.py                        │               │
     ▼                                 │               │
┌─────────────┐   fixed.py             │               │
│  FIXER      │ ──────────────┐        │               │
│  (Sonnet)   │ ← 1-3 cycles  │        │               │
└─────────────┘               │        │               │
     │ fixed.py               │        │               │
     ▼                        │        │               │
┌─────────────┐   documented.py        │               │
│  DOCUMENTER │ ──────────────────┐    │               │
│  (Haiku)    │                   │    │               │
└─────────────┘                   │    │               │
     │ documented.py              │    │               │
     ▼                            ▼    ▼               ▼
┌─────────────┐
│  QA AGENT   │   rapport HTML + JSON
│  (Haiku)    │ ──────────────────────────────────────▶
└─────────────┘
```

### 1.2 Volumes de tokens observés (baseline POC)

| Agent | Tokens INPUT | Tokens OUTPUT | Modèle | Durée obs. |
|---|---|---|---|---|
| Parser | ~3 000 | ~600 | Haiku | ~100s |
| CodeGen | ~5 000–8 000 | ~2 000–2 500 | Sonnet | ~140–286s |
| Fixer (cycle 1) | ~7 000–10 000 | ~2 000–2 500 | Sonnet | ~37–103s |
| Fixer (cycle 2–3) | ~7 000–10 000 | ~2 000–2 500 | Sonnet | ~50s/cycle |
| Documenter | ~4 000–6 000 | ~2 500–3 000 | Haiku | ~130–149s |
| QA | ~2 000 | ~300 | Haiku | ~30s |

**Constat clé** : Les tokens OUTPUT sont facturés 3–5× plus cher que les tokens INPUT sur les modèles Sonnet/Haiku. Le Fixer en multi-cycles est le poste de coût dominant — il réécrit le script complet à chaque cycle alors qu'il ne modifie que 2–5 lignes.

### 1.3 Répartition des coûts estimée (avant optimisation)

```
Parser      ░░░ 5%
CodeGen     ████████████████ 35%
Fixer       ████████████████████ 40%   ← priorité absolue
Documenter  ██████ 15%
QA          ░░ 5%
```

---

## 2. Optimisation des tokens INPUT

> **Principe** : Le LLM doit recevoir uniquement l'information nécessaire à sa tâche. Tout contexte superflu augmente le coût ET dégrade la qualité (le modèle est distrait par du bruit).

### 2.1 RAG sélectif — P0

**Problème** : L'intégralité du RAG (`transformation_map.json` ~1 200 tokens + `python_templates.md` ~800 tokens) est injectée à chaque appel, même pour des transformations simples.

**Solution** : Sélectionner uniquement les sections RAG correspondant aux patterns détectés dans le canonical JSON.

```python
def select_rag_sections(canonical: dict, rag_map: dict) -> str:
    patterns_detected = canonical.get("patterns", [])
    selected_sections = []
    for pattern in patterns_detected:
        if pattern in rag_map:
            selected_sections.append(rag_map[pattern])
    return "\n\n".join(selected_sections)
```

**Impact estimé** : −40 à −60% tokens INPUT sur Parser et CodeGen  
**Effet qualité** : Positif — le LLM se concentre sur les patterns pertinents, moins d'hallucinations  
**Applicable à** : Parser, CodeGen (cycle 1 uniquement)  
**Ne pas appliquer à** : Fixer cycles 2–3 (voir §2.3), Documenter (voir §2.4), QA

---

### 2.2 Slim canonical JSON par niveau — P0

**Problème** : Le canonical JSON contient des métadonnées techniques (`precision`, `scale`, `nullable`, `is_primary_key`) utiles pour le Parser mais inutiles pour la génération et la correction de code.

**Solution** : Définir 3 niveaux de slim appliqués selon l'agent destinataire.

```python
def slim_canonical(canonical: dict, level: str = "medium") -> dict:
    """
    level='full'    → Parser (toutes les métadonnées)
    level='medium'  → CodeGen (sans precision/scale/nullable)
    level='minimal' → Fixer cycles 2-3 (name + datatype + expression uniquement)
    """
    if level == "full":
        return canonical

    remove_medium  = {"precision", "scale", "nullable", "is_primary_key", "default_value"}
    remove_minimal = remove_medium | {"datatype", "length", "description"}

    keys_to_remove = remove_medium if level == "medium" else remove_minimal

    slim = copy.deepcopy(canonical)
    for source in slim.get("sources", []):
        for field in source.get("fields", []):
            for key in keys_to_remove:
                field.pop(key, None)
    return slim
```

**Impact estimé** : −30% tokens INPUT sur CodeGen et Fixer  
**Effet qualité** : Neutre à positif — le LLM se concentre sur la sémantique plutôt que les détails techniques  
**Matrice d'application** :

| Agent | Niveau slim |
|---|---|
| Parser | `full` |
| CodeGen | `medium` |
| Fixer cycle 1 | `medium` |
| Fixer cycles 2–3 | `minimal` |
| Documenter | non injecté (voir §2.4) |
| QA | non injecté |

---

### 2.3 Fixer sans RAG après cycle 1 — P1

**Problème** : Le RAG complet est injecté à chaque cycle de correction. Or les cycles 2–3 ciblent des erreurs spécifiques — le contexte RAG global est du bruit pur à ce stade.

**Solution** : Injecter le RAG complet uniquement au cycle 1. Dès le cycle 2, le prompt se concentre sur le code courant + le rapport d'erreur uniquement.

```python
def build_fixer_prompt(code: str, error: str, canonical: dict, cycle: int) -> str:
    if cycle == 1:
        rag_section = load_rag()  # RAG complet
        canonical_section = slim_canonical(canonical, "medium")
    else:
        rag_section = ""          # Aucun RAG
        canonical_section = slim_canonical(canonical, "minimal")

    return FIXER_PROMPT_TEMPLATE.format(
        rag=rag_section,
        canonical=json.dumps(canonical_section),
        code=code,
        error=error,
        cycle=cycle
    )
```

**Impact estimé** : −50 à −65% tokens INPUT sur Fixer cycles 2–3  
**Effet qualité** : Neutre à positif — le Fixer se concentre sur l'erreur immédiate

---

### 2.4 Documenter sans canonical JSON — P1

**Problème** : Le Documenter reçoit le canonical JSON complet alors que sa mission est d'annoter le code Python. Le code lui-même contient déjà toute la logique de transformation.

**Solution** : Le Documenter reçoit uniquement le code Python annoté + le `workflow_id` pour le contexte métier.

**Impact estimé** : −20% tokens INPUT sur Documenter  
**Effet qualité** : Neutre — la documentation s'appuie sur le code, source de vérité plus directe que le canonical

---

### 2.5 Format de prompt compact — P2

**Problème** : Les LLM génèrent systématiquement du texte d'introduction et de conclusion autour de leur réponse utile (`"Voici le script Python corrigé qui résout le problème de..."`) — des tokens facturés qui n'ont aucune valeur.

**Solution** : Ajouter en fin de chaque prompt système :

```
CONSIGNE FORMAT : Réponds UNIQUEMENT avec le contenu demandé.
Sans introduction. Sans conclusion. Sans balise markdown.
```

Pour les agents dont l'output est structuré (Parser, QA) : utiliser `--output-format json` dans l'appel CLI, ce qui force un format compact et parseable.

**Impact estimé** : −15 à −20% tokens OUTPUT sur tous les agents  
**Effort** : Minimal — modification d'une ligne par prompt

---

### 2.6 `--max-tokens` par agent — P2

**Problème** : Sans limite explicite, un LLM peut générer des réponses anormalement longues, entraînant timeouts, coûts imprévus et échecs de parsing.

**Solution** : Définir des limites calibrées **par agent** sur la base des outputs observés.

| Agent | max-tokens recommandé | Justification |
|---|---|---|
| Parser | 2 000 | JSON canonique ~600 tokens, marge ×3 |
| CodeGen | 4 000 | Script ~2 500 tokens, marge ×1.6 |
| Fixer cycle 1 | 4 000 | Script complet ~2 500 tokens |
| Fixer cycles 2–3 | 2 000 | Fonctions corrigées seulement (voir §3.2) |
| Documenter | 3 000 | Docstrings dict ~800 tokens (voir §3.3) |
| QA | 1 500 | Rapport JSON ~300 tokens, marge ×5 |

> ⚠️ Ne pas définir une valeur unique pour tous les agents. Une valeur trop haute sur Parser ou QA laisse au LLM l'espace de divaguer. Une valeur trop basse sur CodeGen tronque le script généré.

---

## 3. Optimisation des tokens OUTPUT

> **Principe fondateur** : Les tokens OUTPUT coûtent 3–5× plus cher que les tokens INPUT. La stratégie la plus impactante est de changer **ce que le LLM retourne**, pas seulement ce qu'il reçoit.

### 3.1 Model downgrade sur Fixer cycles 2–3 — P0 (Priorité absolue)

**Problème** : Le Fixer utilise Sonnet pour tous les cycles, y compris les cycles 2–3 qui corrigent des erreurs mineures identifiées par le cycle précédent.

**Solution** : Router les cycles de correction vers Haiku dès le cycle 2.

```python
def get_fixer_model(cycle: int) -> str:
    if cycle == 1:
        return "claude-sonnet-4-6"   # Logique complexe, analyse initiale
    else:
        return "claude-haiku-4-5"    # Corrections mineures, Haiku suffit
```

**Impact estimé** : −80% coût OUTPUT sur Fixer cycles 2–3  
**Risque** : Quasi nul — les cycles 2–3 corrigent des erreurs simples (cast de type, guard empty df)  
**Effort** : 30 minutes — modification d'une variable dans `fixer_agent.py`

> **Pourquoi c'est la priorité absolue** : C'est le seul changement qui ne touche pas à l'architecture. Même format de sortie, même logique, même prompt — juste un modèle moins cher. Zéro risque de régression.

---

### 3.2 Fixer retourne uniquement les fonctions corrigées + remplacement AST — P1

**Problème** : Le Fixer retourne le script complet à chaque cycle (~2 500 tokens) alors qu'il ne modifie que 1–3 fonctions.

**Pourquoi les diffs ligne-par-ligne sont dangereux** : Les numéros de ligne décalent après chaque correction. Le texte de correspondance varie selon le whitespace. Sans outil de merge professionnel (git, patch), l'application d'un diff LLM est fragile et provoque des corruptions silencieuses.

**Solution robuste** : Travailler au niveau **fonction nommée** — une granularité stable que Python gère nativement avec le module `ast`.

**Prompt Fixer cycles 2–3** :
```
Retourne UNIQUEMENT les fonctions Python qui nécessitent une correction.
Chaque fonction doit être complète et correctement nommée.
Ne retourne PAS les fonctions qui n'ont pas changé.
```

**Application côté Python** :
```python
import ast
import astor

def apply_function_patches(original_code: str, patched_functions_code: str) -> str:
    original_tree = ast.parse(original_code)
    patch_tree    = ast.parse(patched_functions_code)

    # Indexer les fonctions corrigées par nom
    patches = {
        node.name: node
        for node in ast.walk(patch_tree)
        if isinstance(node, ast.FunctionDef)
    }

    # Remplacer dans l'arbre original
    for node in ast.walk(original_tree):
        if isinstance(node, ast.FunctionDef) and node.name in patches:
            node.body = patches[node.name].body
            node.args = patches[node.name].args
            node.decorator_list = patches[node.name].decorator_list

    return astor.to_source(original_tree)
```

**Pourquoi c'est robuste** :
- Les noms de fonctions sont stables entre cycles
- `ast.parse()` ne fait pas de matching approximatif
- Si une fonction n'est pas trouvée dans le patch, le code original est conservé
- Pas de dépendance aux numéros de ligne ou au whitespace

**Impact estimé** : −60 à −70% tokens OUTPUT sur Fixer cycles 2–3 (combiné avec §3.1 : −85 à −90%)  
**Prérequis** : `pip install astor`

---

### 3.3 Documenter retourne uniquement les docstrings — P1

**Problème** : Le Documenter retourne le script complet avec les annotations insérées (~3 000 tokens = code original + docstrings), alors que le code n'a pas changé.

**Solution** : Le Documenter retourne uniquement un dictionnaire de docstrings, injecté ensuite programmatiquement avec `ast`.

**Prompt Documenter** :
```
Retourne UNIQUEMENT un JSON avec les docstrings à injecter.
Format strict :
{
  "__module__": "Description du module...",
  "extract_sources": "Docstring de la fonction extract_sources...",
  "lookup_dim_accounts": "Docstring de la fonction lookup..."
}
```

**Application côté Python** :
```python
def inject_docstrings(code: str, docstrings: dict) -> str:
    tree = ast.parse(code)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            if node.name in docstrings:
                docstring_node = ast.Expr(value=ast.Constant(value=docstrings[node.name]))
                node.body.insert(0, docstring_node)
        elif isinstance(node, ast.Module):
            if "__module__" in docstrings:
                docstring_node = ast.Expr(value=ast.Constant(value=docstrings["__module__"]))
                node.body.insert(0, docstring_node)
    return astor.to_source(tree)
```

**Impact estimé** : −55 à −60% tokens OUTPUT sur Documenter  
**Effet qualité** : Neutre — le code documenté final est identique

---

### 3.4 CodeGen par blocs sur workflows CRITICAL — P2

**Problème** : Sur les workflows CRITICAL (SCD2, Router multi-groupes, Sequence Generator), le LLM doit gérer simultanément 5–6 patterns complexes dans un seul appel. La qualité en souffre et le script généré est parfois incohérent.

**Solution** : Décomposer le CodeGen en sous-appels par fonction, conditionnellement à la complexité détectée par le Parser.

```python
def run_codegen(canonical: dict, workflow_name: str) -> str:
    complexity = canonical.get("complexity", "LOW")

    if complexity in ("LOW", "MEDIUM"):
        # Appel unique — optimal pour cas simples
        return codegen_monolithic(canonical, workflow_name)
    else:
        # Appels séquentiels par fonction — pour CRITICAL
        functions = codegen_by_function(canonical, workflow_name)
        return codegen_assemble(functions, canonical, workflow_name)
```

**Impact estimé** : −20 à −30% tokens OUTPUT sur CodeGen CRITICAL, +Qualité  
**Attention** : Ne pas appliquer aux cas LOW/MEDIUM — le ratio coût/bénéfice est défavorable (3 appels au lieu de 1 pour un gain marginal)

---

## 4. Stratégies LLMOps avancées

### 4.1 Caching RAG en mémoire — P1

**Problème** : `transformation_map.json` et `python_templates.md` sont rechargés depuis le disque à chaque appel agent, même s'ils ne changent jamais pendant l'exécution du pipeline.

**Solution** : Cache en mémoire au niveau `run_pipeline.py` — un dict Python suffit, pas besoin de Redis à ce stade.

```python
_rag_cache: dict = {}

def load_rag_cached(path: str) -> str:
    if path not in _rag_cache:
        _rag_cache[path] = Path(path).read_text()
    return _rag_cache[path]
```

**Impact** : Latence I/O réduite, base pour un cache inter-runs (hash du prompt → réponse LLM) si le volume de workflows similaires augmente.

**Cache inter-runs (Phase 2)** : Si deux workflows partagent les mêmes patterns Informatica détectés, la section RAG sélectionnée est identique. On peut la cacher sur disque avec `hash(frozenset(patterns))` comme clé.

---

### 4.2 Routing complexité/modèle — P0 (déjà partiellement en place)

Le pipeline utilise déjà Haiku/Sonnet selon l'agent. La matrice complète de routing optimal :

| Agent | Cas LOW | Cas MEDIUM | Cas HIGH | Cas CRITICAL |
|---|---|---|---|---|
| Parser | Haiku | Haiku | Haiku | Haiku |
| CodeGen | Haiku | Sonnet | Sonnet | Sonnet |
| Fixer cycle 1 | Haiku | Sonnet | Sonnet | Sonnet |
| Fixer cycles 2–3 | Haiku | Haiku | Haiku | Haiku |
| Documenter | Haiku | Haiku | Haiku | Haiku |
| QA | Haiku | Haiku | Haiku | Haiku |

> Le CodeGen sur cas LOW peut passer sur Haiku — les transformations simples (LTRIM, UPPER, filtre date) ne nécessitent pas la puissance de Sonnet.

---

### 4.3 Self-correction structurée du Fixer — P2

**Ce qui est implémentable maintenant sans infrastructure supplémentaire** :

**Guard `if df.empty`** dans le prompt CodeGen : Ajouter l'instruction systématique de générer une sortie anticipée après chaque étape d'extraction.

```python
# À ajouter dans le prompt CodeGen :
"""
RÈGLE OBLIGATOIRE : Après chaque étape d'extraction de données,
ajouter un guard de sortie anticipée :
    if df.empty:
        print(f"[WARNING] Aucune ligne extraite — pipeline terminé proprement")
        return
"""
```

**RAG interrogé avec le message d'erreur** : Avant le cycle 2, chercher dans `transformation_map.json` si l'erreur correspond à un pattern connu et injecter la solution documentée directement dans le prompt du Fixer.

**Ce qui nécessite de l'infrastructure (Phase 3)** :
- Tests unitaires automatiques (runner Spark local ou mock)
- Analyse statique avec Bandit/SonarQube
- Variantes parallèles de correction

---

### 4.4 Distillation de modèles — P3 (hors scope POC)

L'utilisation de modèles fine-tunés pour les cas LOW est pertinente à l'échelle mais prématurée ici. Le fine-tuning nécessite un dataset de 50–100 migrations validées minimum. Le routing Haiku/Sonnet déjà en place est une forme implicite de distillation — suffisant pour ce stade.

**Critère de déclenchement** : Envisager P3 quand le volume atteint 100+ workflows migrés et que les cas LOW représentent >60% du volume.

---

## 5. Feuille de route universelle

> Cette section est applicable à tout pipeline agentique LLM, indépendamment du domaine métier.

### Phase 1 — Quick Wins (Semaine 1)

**Objectif** : Gains immédiats sans risque architectural.

| Action | Impact | Effort | Risque |
|---|---|---|---|
| Model downgrade sur cycles répétitifs | −80% coût cycles N+1 | 30 min | Nul |
| Format prompt compact (no prose) | −15% tous agents | 30 min | Nul |
| `--max-tokens` calibré par agent | Guard sécurité | 1h | Nul |
| Cache RAG en mémoire | Latence I/O | 30 min | Nul |

### Phase 2 — Stabilisation (Semaines 2–3)

**Objectif** : Réduction structurelle des tokens sans dégrader la qualité.

| Action | Impact | Effort | Risque |
|---|---|---|---|
| RAG sélectif par patterns | −40–60% INPUT CodeGen | 3–4h | Faible |
| Slim canonical par niveau | −30% INPUT CodeGen/Fixer | 2h | Faible |
| Fixer sans RAG cycles 2–3 | −65% INPUT Fixer | 1h | Faible |
| Documenter sans canonical | −20% INPUT Documenter | 30 min | Nul |
| Fixer → fonctions only + AST merge | −65% OUTPUT Fixer | 4–5h | Moyen |
| Documenter → docstrings dict + AST inject | −55% OUTPUT Documenter | 3h | Moyen |

### Phase 3 — Industrialisation (Mois 2–3)

**Objectif** : Fiabilité et scalabilité à volume.

| Action | Prérequis | Impact |
|---|---|---|
| CodeGen par blocs (CRITICAL only) | Résultats Phase 2 stables | Qualité CRITICAL |
| Cache inter-runs (hash patterns → RAG) | Volume > 20 workflows | −5–15% appels LLM |
| Self-correction avancée (tests + static analysis) | CI/CD setup | Autonomie pipeline |
| Distillation modèles (cas LOW) | 100+ migrations validées | −70% coût LOW |

---

### Principes transversaux

**1. Mesurer avant d'optimiser** : Logger systématiquement les tokens INPUT/OUTPUT par agent et par cycle. Sans baseline, impossible d'évaluer les gains réels.

**2. Optimiser OUTPUT avant INPUT** : Les tokens OUTPUT coûtent 3–5× plus cher. Le ROI est mécaniquement plus élevé.

**3. Model routing > prompt engineering** : Changer de modèle (Sonnet → Haiku) pour les tâches répétitives ou simples réduit le coût plus efficacement que n'importe quelle optimisation de prompt.

**4. Robustesse > ingéniosité** : Les diffs LLM ligne-par-ligne, le streaming partiel, le parsing regex sur output LLM — tout cela est fragile. Préférer des granularités stables (fonction nommée, JSON structuré, AST) aux constructions fragiles.

**5. La complexité des agents doit être conditionnelle** : Prompt chaining, CodeGen par blocs, RAG enrichi — n'appliquer ces stratégies qu'aux cas où la complexité le justifie (seuil de `complexity_score`). Pour les cas simples, le monolithique est optimal.

**6. Ne pas patcher, remplacer** : Quand un LLM doit modifier du code existant, lui demander de retourner l'unité complète à remplacer (fonction, classe, bloc JSON) plutôt qu'un diff. Utiliser ensuite un outil déterministe (AST, JSONPatch officiel) pour l'application.

---

## 6. Plan d'implémentation — POC IA Migration

### Ordre de priorité strict

#### Semaine 1 — Impact maximal, risque nul

```
┌─────────────────────────────────────────────────────────────────┐
│ #1 — Model downgrade Fixer cycles 2-3                           │
│      fixer_agent.py : if cycle > 1: model = "claude-haiku-4-5" │
│      Effort : 30 min — Impact : −80% coût Fixer multi-cycles   │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│ #2 — Format prompt compact                                      │
│      Ajouter "CONSIGNE FORMAT : ..." à chaque prompt système    │
│      Effort : 1h — Impact : −15% OUTPUT tous agents            │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│ #3 — --max-tokens par agent                                     │
│      Parser:2000, CodeGen:4000, Fixer:4000/2000, Doc:3000      │
│      Effort : 1h — Impact : sécurité + élimination timeouts    │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│ #4 — Guard if df.empty dans prompt CodeGen                      │
│      Résout les CRASHes QA sur 0 lignes extraites              │
│      Effort : 30 min — Impact : stabilité pipeline             │
└─────────────────────────────────────────────────────────────────┘
```

#### Semaine 2 — Réduction structurelle INPUT

```
┌─────────────────────────────────────────────────────────────────┐
│ #5 — slim_canonical(canonical, level) dans utils.py            │
│      Créer utils.py, 3 niveaux full/medium/minimal             │
│      Effort : 2h — Impact : −30% INPUT CodeGen/Fixer           │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│ #6 — Fixer sans RAG cycles 2-3                                 │
│      fixer_agent.py : if cycle > 1: rag_section = ""          │
│      Effort : 1h — Impact : −65% INPUT Fixer cycles 2-3        │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│ #7 — Documenter sans canonical JSON                            │
│      documenter_agent.py : retirer canonical du prompt         │
│      Effort : 30 min — Impact : −20% INPUT Documenter          │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│ #8 — select_rag_sections(canonical, rag_map) dans utils.py     │
│      Structurer rag_map par pattern Informatica                │
│      Effort : 3–4h — Impact : −40–60% INPUT CodeGen            │
└─────────────────────────────────────────────────────────────────┘
```

#### Semaine 3 — Réduction structurelle OUTPUT

```
┌─────────────────────────────────────────────────────────────────┐
│ #9 — Fixer retourne fonctions corrigées + AST merge            │
│      Modifier prompt Fixer + apply_function_patches() pipeline │
│      Effort : 4–5h — Impact : −65% OUTPUT Fixer cycles 2-3    │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│ #10 — Documenter retourne docstrings dict + AST inject         │
│       Modifier prompt Documenter + inject_docstrings() pipeline│
│       Effort : 3h — Impact : −55% OUTPUT Documenter            │
└─────────────────────────────────────────────────────────────────┘
```

### Impact total cumulé estimé (après Semaine 3)

| Poste | Avant | Après | Réduction |
|---|---|---|---|
| Tokens INPUT total | ~21 000 | ~10 500 | −50% |
| Tokens OUTPUT Fixer | ~7 500 | ~1 500 | −80% |
| Tokens OUTPUT Documenter | ~3 000 | ~1 350 | −55% |
| **Coût total estimé** | **100%** | **~35–40%** | **−60 à −65%** |

---

## 7. Extensions pour l'industrialisation

> Ces trois recommandations, issues de la revue expert du document, constituent la feuille de route au-delà du POC. Elles sont classées par priorité d'implémentation dans notre contexte.

### 7.1 LLM-as-a-Judge — Agent évaluateur de qualité (Phase 2)

**Contexte** : Notre QA actuel est un **Data Diff** — il compare les lignes produites avec un golden dataset. Il ne juge pas la qualité intrinsèque du code généré. Pour les workflows sans golden data (la majorité), le verdict QA est aveugle à la lisibilité, à la maintenabilité et à la correction sémantique de la migration.

**Solution** : Ajouter un 6ème agent `JudgeAgent` (Haiku, tâche d'évaluation structurée) qui évalue le code généré selon une grille de scoring fixe.

```python
JUDGE_PROMPT = """
Évalue le script PySpark généré selon ces critères. Retourne UNIQUEMENT ce JSON :
{
  "scores": {
    "pattern_fidelity": <0-10>,      // Patterns Informatica correctement traduits
    "pyspark_standards": <0-10>,     // Respect des standards Databricks/PySpark
    "readability": <0-10>,           // Lisibilité et maintenabilité
    "error_handling": <0-10>,        // Guards empty df, gestion des cas limites
    "semantic_correctness": <0-10>   // Logique métier préservée
  },
  "blocking_issues": [],             // Problèmes qui invalident la migration
  "warnings": [],                    // Points d'attention non bloquants
  "overall": <0-10>
}
"""
```

**Intégration dans le pipeline** :
```
Parser → CodeGen → Fixer → Documenter → Judge → QA
```

Le Judge s'insère entre Documenter et QA. Son rapport JSON est intégré dans le rapport HTML final avec une section "Code Quality Score".

**Pourquoi Haiku suffit** : L'évaluation selon une grille fixe est une tâche structurée — pas de génération complexe. Haiku est 20× moins cher que Sonnet pour le même niveau de précision sur ce type de tâche.

**Valeur ajoutée clé** : Donne de la confiance sur les workflows qu'on ne peut pas valider avec des données golden (wf_unconnected_lkp, wf_xml_normalizer, wf_transactions_hist).

---

### 7.2 Non-Regression Testing des prompts (Drift Detection) (Phase 3 / dès production)

**Le risque invisible** : Anthropic met à jour Sonnet et Haiku en continu via l'API. Un prompt parfaitement optimisé en juin 2026 peut produire des résultats sensiblement différents en décembre 2026 sans aucun changement de notre côté. Ce phénomène de **dérive de modèle** est le plus grand risque en production pour un pipeline LLM.

**Symptômes typiques** :
- Le Fixer commence à retourner des fonctions incomplètes après une mise à jour silencieuse du modèle
- Le Parser classe différemment la complexité des mêmes patterns
- Le Documenter génère des docstrings dans un format non parseable

**Solution** : Un jeu de **Golden Runs** — les workflows de référence validés (wf_clients_dim, wf_orders_fact, wf_accounts_scd2) rejoués mensuellement avec comparaison des outputs contre des snapshots figés.

```bash
# Script de non-régression (à lancer mensuellement en CI)
#!/bin/bash
GOLDEN_WORKFLOWS=("wf_clients_dim" "wf_orders_fact" "wf_accounts_scd2")
SNAPSHOT_DIR="tests/golden_snapshots"

for wf in "${GOLDEN_WORKFLOWS[@]}"; do
    python pipeline/run_pipeline.py "xml/${wf}.xml" --force
    diff "output/03_fixed_code/${wf}_fixed.py" "${SNAPSHOT_DIR}/${wf}_fixed.py.snap" \
        || echo "DRIFT DETECTED on ${wf}"
done
```

**Ce qu'on compare** :
- Structure du canonical JSON (même clés, même types de complexité)
- Présence des fonctions attendues dans le code généré
- Score Judge (si implémenté) — alerte si score global chute de >1 point

**Ce qu'on ne compare pas** : Le code ligne-par-ligne (trop fragile, les LLM varient stylistiquement). On compare la **structure** et le **comportement**, pas la forme exacte.

**Effort d'implémentation** : Faible. C'est `run_pipeline.py` + un script de comparaison structurelle. À documenter maintenant, à automatiser en CI dès la mise en production.

---

### 7.3 Orchestration par graphe (LangGraph) (Phase 3)

**Quand LangGraph devient pertinent** : Notre pipeline actuel est linéaire avec un seul cycle conditionnel (Fixer). LangGraph apporte de la valeur quand les graphes de dépendances entre agents deviennent non-linéaires — par exemple :

```
Parser
  │
  ├─ [CRITICAL] → CodeGen (par blocs) → Fixer → Judge
  │                                        │
  │                              [score < 6] → Escalade humaine
  │                                        │
  │                              [score ≥ 6] → Documenter → QA
  │
  └─ [LOW/MEDIUM] → CodeGen (monolithique) → Fixer → Documenter → QA
```

LangGraph permet de maintenir un **état partagé** entre tous les agents (canonical, code courant, historique des erreurs, scores) et de définir des transitions conditionnelles formelles.

**Prérequis avant d'adopter LangGraph** :
1. Le pipeline traite des workflows en parallèle (volume > 10 simultanés)
2. Les branchements conditionnels dépassent 3 niveaux de complexité
3. L'état partagé entre agents devient difficile à gérer manuellement

**Notre situation actuelle** : Nos agents communiquent via fichiers (checkpoints). C'est simple, debuggable, et suffisant pour le POC. LangGraph ajouterait une dépendance externe et une courbe d'apprentissage sans bénéfice mesurable avant Phase 3.

---

### Synthèse des extensions

| Extension | Phase | Effort | Impact | Prérequis |
|---|---|---|---|---|
| LLM-as-a-Judge | 2 | 3–4h | Confiance sur workflows sans golden data | Agents stables |
| Non-Regression Testing | 3 / dès prod | 2h | Détection dérive modèle | 3+ workflows validés |
| LangGraph | 3 | 2–3 jours | Orchestration complexe | Volume > 10 workflows parallèles |

---

## Références

- Expert report interne : *Rapport d'Optimisation du Pipeline de Migration ETL assisté par IA* (Juin 2026)
- Anthropic : *Claude API Documentation — Model Pricing & Token Limits* (2026)
- Python `ast` module : https://docs.python.org/3/library/ast.html
- `astor` library : https://astor.readthedocs.io
- LLMOps practices : *Building Production-Ready LLM Applications* — divers auteurs (2025–2026)
