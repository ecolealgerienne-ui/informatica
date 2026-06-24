# Analyse du Rapport d'Expertise — Retours Techniques

**Document** : Réponse au rapport `rapport_expertise_migration_ia.md`  
**Date** : Juin 2026  
**Contexte** : Analyse comparative POC IA Migration vs solutions industrielles (LeapLogic, Bitwise, BladeBridge, TCS)

---

## Vue d'ensemble

Le rapport est globalement juste et bien positionné. Le verdict final — *"architecture saine, alignée sur les visions les plus modernes"* — est cohérent avec les choix faits. Les trois recommandations techniques (SQL Agent, RAG dynamique, sampling QA) sont pertinentes et hiérarchisées différemment selon l'effort et la valeur apportée.

Mon analyse point par point ci-dessous.

---

## 1. Moteur de Parsing — Verdict : Aligné

### Ce que dit le rapport
> Les industriels utilisent des graphes de métadonnées pour gérer la lignée (lineage) complexe que le LLM peut avoir du mal à reconstituer sur de gros volumes.

### Mon avis
**Correct, et déjà anticipé dans notre architecture.**

Notre choix de séparer le parsing déterministe (XML → JSON structuré) de l'analyse sémantique LLM est précisément ce qui protège contre ce risque. Le lineage — qui vient d'où, qui alimente quoi — est extrait par `_parse_connectors()` et `build_data_flow()` de façon déterministe. Le LLM ne touche pas à la structure, il analyse uniquement la sémantique (complexité, fonctions propriétaires, routing).

**Ce que les industriels font en plus :** les graphes de métadonnées permettent de tracer le lineage à l'échelle de centaines de mappings et de les relier entre eux (un champ calculé dans un mapping qui alimente un autre mapping). C'est un sujet d'infrastructure de gouvernance, pas de parsing — pertinent pour la Phase 3 si on veut un catalogue de métadonnées global.

**Action Phase 1 : rien à changer. Action Phase 3 : évaluer un outil type Apache Atlas ou DataHub pour le lineage inter-mappings.**

---

## 2. SQL Agent (`sqlglot`) — Verdict : Pertinent, Actionnable en Phase 2

### Ce que dit le rapport
> Intégrez un "SQL Agent" spécialisé qui utilise un parseur SQL (type `sqlglot`) pour normaliser les requêtes avant la génération du code Python.

### Mon avis
**C'est la recommandation la plus concrète et la plus impactante du rapport.**

Aujourd'hui, quand le Parser détecte un `sql_override` sur une Source Qualifier, il envoie la requête brute Oracle au LLM avec l'instruction "traduire en pandas". Sur du SQL simple, ça marche. Sur du SQL avec `ROW_NUMBER() OVER`, `CONNECT BY`, `MERGE`, ou des sous-requêtes imbriquées — le LLM peut halluciner ou produire un équivalent fonctionnellement incorrect.

`sqlglot` apporte quelque chose que le LLM seul n'a pas : **une grammaire validée**. Il parse le SQL Oracle, le représente en AST, et peut le transpiler vers Spark SQL ou le décomposer en éléments (tables, colonnes, fonctions, clauses). Le LLM reçoit ensuite un contexte structuré au lieu d'une chaîne de caractères opaque.

**Concrètement, ce que ça changerait dans notre pipeline :**

```
sql_override (Oracle) 
    → sqlglot.parse() → AST 
    → détection : sous-requêtes, fonctions analytiques, dialects spécifiques
    → LLM reçoit : AST structuré + liste des constructions à traduire
    → génération pandas/PySpark plus fiable
```

**Effort estimé : 1 semaine. Valeur : élevée sur les cas MEDIUM/HIGH.**

---

## 3. RAG Dynamique — Verdict : Juste sur le fond, Prématuré maintenant

### Ce que dit le rapport
> Évoluez vers un RAG Dynamique. Capturez les corrections faites par les humains lors de l'étape `ESCALATE` et réinjectez-les comme "Few-Shot Examples" dans le prompt du CodeGen Agent.

### Mon avis
**L'idée est excellente sur le principe, mais elle a une précondition : avoir des cas `ESCALATE` réels capitalisés.**

Le RAG dynamique n'a de valeur que si on a un historique de corrections humaines. Aujourd'hui, on a 0 cas en production — le RAG dynamique serait vide. Le mettre en place maintenant reviendrait à construire un moteur de recommandation sans données.

**La bonne séquence :**
1. Passer en production sur 20-30 mappings réels
2. Capturer chaque cas `ESCALATE` + la correction humaine associée
3. Construire un store de few-shot examples (JSON structuré : problème → solution)
4. Injecter les 3-5 exemples les plus proches (par type de transformation) dans le prompt CodeGen

**Ce qui est actionnable maintenant** : créer la structure de capture dès aujourd'hui — un fichier `rag_base/escalate_history.json` vide mais avec le bon schéma, pour que les corrections futures soient capitalisées dès le premier cas réel.

**Effort Phase 2 : 2 jours (structure + injection). Valeur : croît avec le nombre de mappings traités.**

---

## 4. Validation QA et Sampling — Verdict : Partiellement d'accord

### Ce que dit le rapport
> L'Agent QA devrait être capable de demander des "échantillons de lignes en erreur" spécifiques pour les analyser, plutôt que de se baser uniquement sur un résumé.

### Mon avis
**D'accord sur l'objectif, pas sur la méthode.**

Le rapport suggère d'envoyer des échantillons de lignes au LLM pour l'analyse fine. On vient justement de corriger ça — les données brutes ne doivent pas partir vers un LLM externe (confidentialité, coût, volume).

**Ce que je propose à la place : un sampling intelligent côté HTML uniquement.**

Le rapport mentionne Datafold et le "stratified sampling" — c'est la bonne direction, mais sans LLM. Concrètement :

```
Anomalies détectées sur colonne AGE (342 cas sur 1M lignes)
→ Sampling stratifié Python : 
   - 5 cas où diff = +1
   - 5 cas où diff = -1  
   - 5 cas où la valeur est null
→ Affichage dans le rapport HTML (section "Échantillons diagnostiques")
→ Le LLM reçoit toujours seulement le résumé statistique
```

L'ingénieur qui lit le rapport HTML a ses exemples concrets pour débugger. Le LLM reste sur l'analyse de pattern, pas sur les données.

**Effort : 3 jours. Valeur : améliore significativement le debugging sans compromettre la sécurité des données.**

---

## 5. Réconciliation Distribuée et Gouvernance — Verdict : Hors scope Phase 1-2

### Ce que dit le rapport
> Le passage à l'échelle nécessitera d'ajouter une couche de gouvernance des métadonnées et un moteur de réconciliation distribué pour la validation.

### Mon avis
**Correct, mais ce sont des sujets d'infrastructure, pas d'agents IA.**

Un moteur de réconciliation distribué (Spark-based, type Datafold ou Great Expectations sur Spark) est pertinent quand on valide des tables de plusieurs milliards de lignes. Ce n'est pas un agent à construire — c'est un outil à intégrer dans la Phase 3, une fois que le pipeline produit du code PySpark.

La gouvernance des métadonnées (lineage, catalogues, data contracts) est un chantier transverse qui dépasse le périmètre de ce POC et implique d'autres équipes.

**Ce ne sont pas des gaps de notre architecture — ce sont des dépendances d'écosystème pour la mise en production à grande échelle.**

---

## Synthèse et Priorisation

| Recommandation | Mon verdict | Priorité | Effort |
|---|---|---|---|
| SQL Agent (`sqlglot`) | ✅ Pertinent et actionnable | Phase 2 | 1 semaine |
| Sampling stratifié dans rapport HTML | ✅ Pertinent, adapté sans LLM | Phase 2 | 3 jours |
| Structure de capture ESCALATE | ✅ Préparer maintenant | Phase 2 | 2 jours |
| RAG dynamique (few-shot) | ⏳ Juste mais prématuré | Phase 3 | Après 20+ mappings réels |
| Réconciliation distribuée (Spark) | ⏳ Hors scope Phase 1-2 | Phase 3 | Dépend infra Spark |
| Gouvernance métadonnées / lineage | ⏳ Chantier transverse | Phase 3+ | Autre équipe |

### Ce que le rapport confirme
- Notre séparation parsing déterministe / analyse LLM est la bonne approche
- Notre décision de borner le payload LLM dans le QA Agent est validée (*"brillant pour la sécurité"*)
- La boucle Fixer Agent est alignée avec les pratiques industrielles modernes
- On couvre les 80% de cas (LOW/MEDIUM) — c'est l'objectif d'un POC Phase 1

### Ce que le rapport apporte de nouveau
- `sqlglot` comme pré-processeur SQL avant le LLM — pas dans notre radar initial, forte valeur
- La notion de "Semantic Context Layer" (patterns de transformation groupés, pas seulement fonction par fonction) — à garder en tête pour faire évoluer le `transformation_map.json`
- La référence aux initiatives Databricks Agent Bricks, LTIMindtree et TCS confirme qu'on est dans la bonne direction au niveau marché
