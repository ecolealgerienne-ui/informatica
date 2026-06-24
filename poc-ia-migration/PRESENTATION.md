# POC IA Migration — Présentation

> **Contexte** : Présentation interne — Migration Informatica PowerCenter → Python  
> **Audience** : Management / Responsable technique  
> **Format** : 5 slides

---

## SLIDE 1 — Contexte & Problème

### Migration Informatica : un chantier coûteux

**La situation actuelle**
- Des centaines de workflows Informatica PowerCenter à migrer vers Python / PySpark
- Migration manuelle : longue, répétitive, source d'erreurs
- Compétences Informatica rares → coût élevé, délais longs

**L'opportunité IA**
- Les LLMs comprennent le code Informatica et savent générer du Python
- La structure XML d'Informatica est analysable automatiquement
- Un agent IA peut faire en minutes ce qui prend des jours manuellement

---

## SLIDE 2 — Notre Solution : Pipeline Multi-Agents

### 5 agents IA en séquence, 0 intervention humaine

```
XML Informatica
      │
      ▼
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│  1. PARSER  │────▶│  2. CODEGEN │────▶│  3. FIXER   │
│  XML → JSON │     │ JSON → Py   │     │ Correction  │
└─────────────┘     └─────────────┘     └─────────────┘
                                                │
                          ┌─────────────────────┘
                          ▼
                ┌──────────────────┐     ┌─────────────┐
                │  4. DOCUMENTER   │────▶│   5. QA     │
                │  Doc + Annoté    │     │ Validation  │
                └──────────────────┘     └─────────────┘
                                                │
                                                ▼
                                         Rapport HTML
                                         Verdict PASS/FAIL
```

**Technologie** : Claude Code CLI (IA) + Python + Pandas

---

## SLIDE 3 — Ce que le Pipeline Produit

### Pour chaque workflow Informatica migré

| Livrable | Contenu |
|---|---|
| 📋 **Analyse de complexité** | Score automatique LOW/MEDIUM/HIGH/CRITICAL + estimation en jours |
| 🐍 **Code Python prêt** | Batch vectorisé, patterns pandas optimisés |
| 📝 **Documentation bilingue** | Règles métier FR + notes techniques EN |
| ✅ **Rapport QA** | Comparaison données attendues vs générées, 0 anomalie |

**Scoring de complexité automatique**
- Score calculé sur grille de critères définie (15+ critères objectifs)
- Routing automatique : Python / PySpark / Databricks selon la complexité
- Détection des cas nécessitant intervention humaine (Java, fonctions custom)

---

## SLIDE 4 — Résultats du POC

### Workflow testé : `wf_clients_dim` (mapping clients dimension)

| Mesure | Résultat |
|---|---|
| Temps de migration (manuel) | ~2 jours |
| Temps de migration (pipeline IA) | **< 5 minutes** |
| Qualité du code généré | ✅ Syntaxe valide, 0 pattern interdit |
| Validation données | ✅ PASS — 0 anomalie sur 23 lignes testées |
| Documentation produite | ✅ Doc métier + code annoté |

**Économie estimée sur 100 workflows**
- Manuel : ~150 jours/homme
- Avec le pipeline IA : ~20 jours/homme (revue + cas complexes)
- **Gain estimé : −87%**

---

## SLIDE 5 — Roadmap & Prochaines Étapes

### Phase 1 — TERMINÉE ✅
- Architecture pipeline multi-agents
- Workflow simple (pandas, Python pur)
- Validation QA automatisée

### Phase 2 — À planifier
| Étape | Contenu | Délai estimé |
|---|---|---|
| **2.1** | Support PySpark pour workflows HIGH/CRITICAL | 2 semaines |
| **2.2** | Batch multi-fichiers — traiter un dossier de XML | 1 semaine |
| **2.3** | Interface web — upload XML, visualiser rapport | 2 semaines |
| **2.4** | Intégration CI/CD — validation automatique à chaque migration | 1 semaine |

### Demande
> Validation pour passer en Phase 2 — priorité PySpark + batch multi-fichiers

---

*POC réalisé sur branche `claude/laughing-curie-04rzxm` — repo `ecolealgerienne-ui/informatica`*
