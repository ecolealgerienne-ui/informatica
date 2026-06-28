# Standards Industriels de Complexité ETL/ELT
## Référence pour la calibration de la matrice de scoring Informatica PowerCenter

> Document de référence — synthèse de 4 agents de recherche indépendants  
> Sources : IEEE, Databricks Lakebridge, WhereScape, TOGAF/DAMA, SI factories, FPA  
> Date : Juin 2026

---

## Constat préliminaire

**Il n'existe pas de standard universel publié pour scorer la complexité des transformations Informatica.**

Chaque acteur (outil commercial, cabinet de conseil, chercheur académique) utilise sa propre grille. Ce document synthétise l'ensemble des approches trouvées, les compare, et en tire une matrice de référence adaptée à notre contexte (Informatica PowerCenter → Python/PySpark).

---

## 1. Databricks Lakebridge — 4 niveaux, metadata-driven

**Source** : [Lakebridge Overview](https://databrickslabs.github.io/lakebridge/docs/overview/) · [Medium](https://medium.com/@senthilkumarr.ma/lakebridge-modernizing-datastage-informatica-etl-to-databricks-6e03d97da6a0)

### Échelle
| Niveau | Signification |
|---|---|
| LOW | Transformations simples, mappables 1-pour-1 en SQL/Python |
| MEDIUM | Logique conditionnelle, jointures standard |
| HIGH | Patterns complexes nécessitant une réécriture |
| VERY_COMPLEX | SCD2, Java, logique métier dense, interdépendances fortes |

### Méthode de scoring
- Analyse automatisée des métadonnées du mapping (pas de règle par transformation publiée)
- Scan du code source Informatica via Lakebridge Analyzer
- Seuils propriétaires, non publiés

### Critique
Lakebridge **compte les appels de fonctions SQL** — il ne reconnaît pas les objets Informatica natifs (SCD2, Normalizer, Router, Unconnected Lookup). Résultat : tous les workflows Informatica tendent à scorer LOW, ce qui sous-estime systématiquement le risque de migration.

> **Leçon** : L'échelle 4 niveaux est la bonne. Les critères de Lakebridge sont insuffisants pour Informatica natif.

---

## 2. WhereScape RED / 3D — Approche binaire

**Source** : [WhereScape](https://www.wherescape.com/)

### Échelle
Approche binaire : **migrable automatiquement** vs **nécessite intervention manuelle**.

### Méthode
- Pattern matching sur les dépendances et le schéma
- Profiling automatique des objets ETL
- Génération d'un rapport "what needs rework"

### Critique
Pas de granularité intermédiaire. Utile pour un premier tri, insuffisant pour estimer l'effort par workflow. WhereScape s'appuie sur son propre moteur de génération de code — la complexité est masquée par l'outil, pas mesurée.

> **Leçon** : Le concept de "migrable sans humain" vs "nécessite expert" est utile. Correspond à notre flag `auto_conversion`.

---

## 3. Modèle IEEE / Académique — Estimation continue par régression

**Source** : [IEEE Forward Stepwise Regression ETL Effort](https://ieeexplore.ieee.org/document/7389209/) · [IEEE Big Data ETL](https://ieeexplore.ieee.org/document/9759873)

### Échelle
Pas de niveaux discrets — **estimation continue en heures/jours** par régression sur 6 variables.

### Les 6 variables (220+ projets industriels)
| Variable | Description | Impact |
|---|---|---|
| Nombre de types de sources | Hétérogénéité des sources | Fort |
| Nombre de tables impliquées | Volumétrie du mapping | Modéré |
| Expérience de l'équipe | Connaissance du domaine ETL | Fort |
| Complexité des transformations | Nature des opérations (pas le nombre) | Très fort |
| Qualité de la documentation | Reverse engineering nécessaire | Modéré |
| Adéquation source-cible | Mapping structurel direct vs restructuration | Modéré |

### Points clés
- La **nature** des transformations prime sur leur **nombre**
- L'expérience équipe est un facteur aussi important que la complexité technique
- Aligné avec COCOMO II pour l'estimation de coût logiciel

> **Leçon** : Ne pas pénaliser le nombre de transformations mais leur nature. Un workflow avec 10 filtres simples ≠ 3 transformations SCD2.

---

## 4. TOGAF / DAMA-DMBOK — Cadre architectural

**Source** : [TOGAF Phase C](https://pubs.opengroup.org/architecture/togaf91-doc/arch/chap10.html) · [DAMA-DMBOK 3.0](https://atlan.com/dama-dmbok-framework/)

### Approche
Pas de scoring technique des transformations — cadre **gouvernance et architecture**.

### Facteurs de complexité identifiés par TOGAF Phase C
- Niveau de transformation, nettoyage et dédoublonnage requis
- Hétérogénéité des systèmes sources
- Dépendances entre objets (lineage)
- Risque de perte de données

### DAMA-DMBOK 3.0 (2025)
Identifie la complexité à travers les fonctions de gouvernance :
- Qualité des données source
- Traçabilité (lineage) et métadonnées
- Règles métier embarquées dans les ETL

> **Leçon** : La complexité n'est pas que technique — les règles métier embarquées dans un ETL (IIF imbriqués, DECODE multi-valeurs) représentent un risque de gouvernance, pas seulement de code.

---

## 5. Factories de migration SI (TCS, Capgemini, Accenture)

**Source** : TCS CDIF, Capgemini/Informatica IDMC Framework, Accenture Cloud Migration Factory

### Approche commune : 3 à 5 tiers

| Tier | Critères généraux | Automatisation |
|---|---|---|
| T1 — Simple | Source unique, transformations directes, pas de règle métier complexe | 80–90% |
| T2 — Standard | Jointures, lookups, expressions calculées, SQL standard | 60–75% |
| T3 — Complexe | SCD2, agrégations avancées, sources multiples hétérogènes | 40–60% |
| T4 — Critique | Java, Custom, logique propriétaire, dépendances circulaires | 10–30% |
| T5 — Spécifique | Cas uniques nécessitant réécriture complète (selon certains frameworks) | 0–10% |

### Facteurs discriminants utilisés en pratique
- Présence de **Java Transformation** → T4 automatique
- Présence de **Custom Transformation** → T4 automatique
- **SCD2** → T3/T4 selon la mise en œuvre
- **SQL override avec sous-requête** → T3
- **SQL override simple (WHERE clause)** → T1/T2 (considéré trivial)
- **Lookup non connecté** → T3 (pattern différent)
- **Normalizer** → T3 (dépivotage non trivial)

> **Leçon** : Les grandes usines de migration ne pénalisent pas le SQL override simple. Elles séparent clairement Java/Custom (T4) de tout le reste.

---

## 6. Function Point Analysis (FPA) adapté ETL

**Source** : [IFPUG White Paper — FPA for Data Warehouse](https://ifpug.org/2018/10/03/new-ifpug-white-paper-applying-function-point-analysis-to-data-warehouse-analytics-systems/) · Data Vault 2.0

### Principe
Adaptation du FPA logiciel (IFPUG) aux projets ETL/DW :
- **Raw Data Vault loads** = LOW complexity (chargement sans transformation)
- **Business Vault** = MEDIUM à HIGH (règles métier appliquées)
- **Mart/Reporting layer** = variable selon les agrégations

### Calibration Data Vault 2.0
| Type de charge | FPA équivalent | Complexité |
|---|---|---|
| Hub (clé métier simple) | 3-5 points | LOW |
| Link (association) | 4-7 points | LOW-MEDIUM |
| Satellite (attributs) | 5-10 points | MEDIUM |
| Business Rule (calcul) | 8-15 points | MEDIUM-HIGH |
| SCD Type 2 | 12-20 points | HIGH-CRITICAL |

> **Leçon** : FPA non standardisé pour ETL — chaque organisation calibre. Mais la logique de base (transformation directe = faible, règle métier dense = élevé) est universelle.

---

## 7. Gartner / Forrester — Perspective stratégique

**Source** : [Gartner Cloud Data Migration Framework](https://www.leanix.net/en/blog/gartner-data-migration)

### Ce que Gartner mesure
Pas de scoring technique — focus sur les **facteurs d'échec** :
- 79% des projets : pipelines non documentés
- 78% des équipes : difficultés d'orchestration complexe
- 83% des projets de data migration échouent (planning/testing, pas la complexité technique)

### Insight clé
> « La complexité perçue est souvent une complexité de gouvernance, pas de code. »

Les plateformes low-code modernes réduisent 83-90% du temps sur les cas simples. Les plateformes IA (comme notre POC) s'inscrivent dans cette tendance.

---

## 8. Consensus inter-standards : ce qui fait VRAIMENT la complexité

En croisant toutes les sources, **7 signaux consensuels** émergent :

### Signaux forts (unanimes)
| Signal | Justification |
|---|---|
| Java Transformation | Réécriture complète obligatoire, aucun mapping possible |
| Custom Transformation | Logique propriétaire inconnue, risque maximal |
| SCD2 (Slowly Changing Dim.) | Pattern multi-étapes complexe, clés composites, gestion d'historique |
| Sous-requêtes SQL imbriquées | Logique relationnelle difficile à vectoriser |
| Fonctions analytiques SQL (ROW_NUMBER, LAG) | Pas d'équivalent direct simple en pandas |

### Signaux modérés (majorité des sources)
| Signal | Justification |
|---|---|
| Normalizer | Dépivotage — pattern non standard en Python |
| Lookup non connecté | Appel syntaxique `:LKP.` différent des merges standard |
| Router avec 4+ groupes | Logique conditionnelle dense |
| Aggregator avec running total | `cumsum()` — non trivial selon le contexte |
| Sources hétérogènes multiples | Jointures cross-système |

### Signaux faibles (minorité ou contestés)
| Signal | Pourquoi faible | Recommandation |
|---|---|---|
| SQL override simple (WHERE) | SQL réutilisable directement | Score 0 |
| TO_DATE / ROWNUM Oracle | One-liners en Python | Score 0 |
| Nombre de transformations (5-10) | Taille ≠ complexité | Supprimer |
| DATEDIFF | `(date1-date2).days` trivial | Score 0 |
| Fichier de paramètres | Variable d'environnement en Python | Score 0 |
| Expression base | Présence d'une Expression ≠ complexité | Base 0 |

---

## Synthèse : principes de calibration recommandés

1. **Échelle** : 4 niveaux (LOW/MEDIUM/HIGH/CRITICAL) — consensus Lakebridge + SI factories
2. **Score de base** : ne scorer que ce qui résiste vraiment à l'automatisation
3. **SQL override simple** : score 0 — c'est un accélérateur, pas un frein
4. **Transformation count** : supprimer le modificateur global de décompte — compter la nature, pas le nombre
5. **Séparation Java/Custom** : flag automatique CRITICAL indépendamment du score
6. **SCD2** : détecter via le pattern Router + Sequence Generator + multiple targets (pas via un flag explicite dans l'XML)
7. **CRITICAL ≤ 25%** d'un parc typique — benchmark industrie (Accenture, TCS)
8. **Automatisation cible** : 80-90% T1, 60-75% T2, 40-60% T3, 10-30% T4

---

## Références complètes

| Source | Lien | Type |
|---|---|---|
| IEEE ETL Effort Estimation (régression) | https://ieeexplore.ieee.org/document/7389209/ | Académique |
| IEEE Big Data ETL Effort | https://ieeexplore.ieee.org/document/9759873 | Académique |
| Databricks Lakebridge Overview | https://databrickslabs.github.io/lakebridge/docs/overview/ | Outil commercial |
| Lakebridge Informatica Migration | https://medium.com/@senthilkumarr.ma/lakebridge-modernizing-datastage-informatica-etl-to-databricks-6e03d97da6a0 | Technique |
| WhereScape Data Automation | https://www.wherescape.com/ | Outil commercial |
| TOGAF Phase C Data Architecture | https://pubs.opengroup.org/architecture/togaf91-doc/arch/chap10.html | Standard |
| DAMA-DMBOK 3.0 | https://atlan.com/dama-dmbok-framework/ | Standard |
| IFPUG FPA for Data Warehouse | https://ifpug.org/2018/10/03/new-ifpug-white-paper-applying-function-point-analysis-to-data-warehouse-analytics-systems/ | Standard adapté |
| ETL Effort Estimation (ResearchGate) | https://www.researchgate.net/publication/297312608 | Académique |
| Informatica SCD2 Implementation | https://www.disoln.org/2012/08/slowly-changing-dimension-type-2-implementation-using-informatica.html | Technique |
| BPCS ETL Modernization Framework | https://bpcs.com/blog/6-stage-etl-modernization-framework | Pratique |
| Data Migration Success Rates 2026 | https://kaopizglobal.medium.com/the-hidden-complexity-of-data-migration-why-83-of-projects-fail-and-how-to-beat-the-odds-191c65f55dcd | Étude |
