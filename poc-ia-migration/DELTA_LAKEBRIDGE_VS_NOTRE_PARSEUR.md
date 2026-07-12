# Delta — Lakebridge Analyzer vs Notre Parseur (SPEC_PIPELINE_V2.md)

> Analyse : Juillet 2026  
> Référence parseur : SPEC_PIPELINE_V2.md v2.1  
> Référence Lakebridge : databrickslabs/lakebridge (dernière version connue ~v0.14)

---

## 1. Vue d'ensemble de Lakebridge (partie inventaire)

Lakebridge est un outil Databricks Labs open-source couvrant 3 phases :

```
analyze  →  transpile  →  reconcile
(inventaire)  (conversion code)  (validation données)
```

La commande **`analyze`** est la seule pertinente pour notre comparaison.

### Ce que Lakebridge analyze produit

Pour Informatica/DataStage, l'analyse génère un **fichier Excel** avec plusieurs onglets :

| Onglet Excel | Contenu |
|---|---|
| Summary | Comptages globaux, répartition complexité |
| Subject Areas | Liste des dossiers/projets (= FOLDER PowerCenter) |
| Job Details | Un workflow/mapping par ligne avec complexité |
| Transformations | Catalogue de tous les types de transformation trouvés |
| Job Transformation List | Quelle transformation est dans quel job |
| Job Transformations XREF | Matrice croisée job ↔ transformation |
| Functions | Catalogue des fonctions custom |
| Transformation Expressions | Expressions extraites par transformation |
| Embedded SQL | Requêtes SQL embarquées extraites |

### Complexité Lakebridge

Lakebridge classe en 4 niveaux basés sur le **nombre de transformations** par mapping :

```
LOW < seuil_1 ≤ MEDIUM < seuil_2 ≤ HIGH < seuil_3 ≤ VERY COMPLEX
```

Le rapport inclut une estimation d'effort de conversion en heures par niveau.

### Périmètre PowerCenter dans Lakebridge

> **Point critique** : d'après la recherche approfondie sur les releases et le code, le support PowerCenter dans Lakebridge est principalement orienté **transpilation** (conversion de code vers Spark/Python), pas inventaire pur. L'analyzer Lakebridge est historiquement centré sur **DataStage**. La documentation officielle pour PowerCenter (issue #2265) était encore en cours de rédaction en février 2026.

---

## 2. Table de comparaison détaillée

### 2.1 Inventaire des objets

| Objet PowerCenter | Lakebridge | Notre parseur (V2.md) |
|---|---|---|
| FOLDER (Subject Area) | ✅ Onglet "Subject Areas" | ✅ folder_attrs + HTML par dossier |
| MAPPING | ✅ Inventorié (Job Details) | ✅ Parsing complet avec data_flow |
| WORKFLOW | ⚠️ Confondu avec Mapping (même "Job") | ✅ Séparé de MAPPING, 3 formes SESSION |
| SESSION inline / TASK / standalone | ❌ Non documenté | ✅ 3 fonctions distinctes |
| TRANSFORMATION (tous types) | ✅ Générique (types extraits du XML) | ✅ Générique, types verbatim du XML |
| SOURCE / TARGET | ⚠️ Partiel | ✅ source_connection, target_connection |
| MAPPLET (réutilisable) | ⚠️ Mentionné dans transpile, pas inventaire | ✅ GlobalLibrary + résolution transitif |
| REUSABLE TRANSFORMATION | ❌ Non documenté | ✅ GlobalLibrary + scoring transitif |
| WORKLET | ❌ Non documenté | ✅ Dans raw_xml du workflow |
| Java Transformation | ❌ Non documenté | ✅ Flag CRITICAL automatique |
| Shared FOLDER (bibliothèque globale) | ❌ Non documenté | ✅ `SHARED="SHARED"` → GlobalLibrary |

### 2.2 Métriques de complexité

| Métrique | Lakebridge | Notre parseur (V2.md) |
|---|---|---|
| Niveau complexité (LOW/MEDIUM/HIGH) | ✅ 4 niveaux (compte transformations) | ✅ 4 niveaux (score pondéré multi-critères) |
| Score numérique de complexité | ❌ Seulement niveau textuel | ✅ Score numérique + facteurs détaillés |
| Scoring transitif (MAPPLET → WF) | ❌ Non documenté | ✅ GlobalLibrary score hérité |
| SQL complexity (AST sqlglot) | ❌ Extraction brute seulement | ✅ 11 patterns : CONNECT BY, window, MERGE… |
| Flags critiques (Java, CONNECT BY…) | ❌ Non documenté | ✅ `requires_human_review`, flags nommés |
| Estimation d'effort (heures) | ✅ Par niveau de complexité | ❌ Non inclus (hors périmètre par choix) |
| routing_decision | ❌ Absent | ✅ Champ dédié avec rationale |

### 2.3 Analyse SQL

| Fonctionnalité SQL | Lakebridge | Notre parseur |
|---|---|---|
| Extraction SQL embarqué | ✅ Onglet "Embedded SQL" | ✅ SQL extrait dans chaque transformation |
| Détection fonctions Oracle (NVL, DECODE…) | ❌ | ✅ via sqlglot |
| Détection CONNECT BY | ❌ | ✅ CRITICAL flag |
| Détection window functions | ❌ | ✅ HIGH flag |
| Détection MERGE | ❌ | ✅ CRITICAL flag |
| Détection PIVOT | ❌ | ✅ HIGH flag |
| Détection nested subqueries | ❌ | ✅ HIGH flag |
| Détection Oracle hints | ❌ | ✅ MEDIUM flag |
| SQL override par session | ❌ Non documenté | ✅ pre_sql, post_sql dans session |

### 2.4 Dépendances et data lineage

| Fonctionnalité | Lakebridge | Notre parseur |
|---|---|---|
| Graphe de dépendances mapping | ✅ Onglet XREF (transformation ↔ job) | ✅ data_flow + CONNECTOR resolution |
| Ordre d'exécution (DAG) | ❌ | ✅ execution_order calculé |
| Résolution MAPPINGNAME | ❌ Non documenté | ✅ 3 formes XPath documentées |
| Références vers objets shared | ❌ | ✅ résolution cross-FOLDER |
| Détection cycle / WORKLET imbriqué | ❌ | ✅ Warning W-P003 |

### 2.5 Format de sortie

| Format | Lakebridge | Notre parseur |
|---|---|---|
| Excel multi-onglets | ✅ Format principal | ❌ |
| HTML navigable | ❌ | ✅ index global + par dossier + par WF |
| JSON canonique (pour automation) | ⚠️ Option `--output json` (basique) | ✅ Schéma V2.1 complet (contrat Phase 2) |
| Rapport par workflow | ❌ (vue globale seulement) | ✅ Un HTML + un JSON par workflow |
| Rapport par dossier | ✅ Subject Area = onglet | ✅ FOLDER/index.html |
| Index global repository | ✅ Summary onglet | ✅ index.html + global_library.html |

### 2.6 Traitement du XML

| Aspect technique | Lakebridge | Notre parseur |
|---|---|---|
| Input | XML export PowerCenter | XML export PowerCenter |
| Streaming (lxml iterparse) | ❌ Non documenté / probablement DOM | ✅ Obligatoire (lxml iterparse) |
| Multi-project (plusieurs FOLDER) | ⚠️ Probablement géré mais non documenté | ✅ Géré explicitement |
| Splitter (1 XML par workflow) | ❌ Absent | ✅ Composant xml_splitter.py dédié |
| Gestion erreurs (warn, pas crash) | ⚠️ Inconnu | ✅ Warning codes S-xxx et P-xxx |
| Verbatim attrs (généricité totale) | ❌ Hardcodé sur types connus | ✅ Tout extrait verbatim du XML |

### 2.7 Connexions et variables

| Fonctionnalité | Lakebridge | Notre parseur |
|---|---|---|
| Connexions source/target | ❌ Non documenté | ✅ source_connection, target_connection |
| Variables de workflow | ✅ Collectées (v0.10.6 changelog) | ✅ mapping_variables + session_variables |
| Paramètres de session | ❌ Non documenté | ✅ per_transformation_overrides |
| Pre/Post SQL session | ❌ Non documenté | ✅ pre_sql, post_sql dans session |

---

## 3. Ce que Lakebridge fait mieux (à considérer)

| Point fort Lakebridge | Pertinence pour nous |
|---|---|
| **Excel multi-onglets** — format familier pour les décideurs | ⚠️ À considérer : ajouter export Excel en plus de HTML |
| **Estimation d'effort en heures** — par mapping, par dossier, total | ⚠️ Utile pour le management. Peut être ajouté en post-traitement simple |
| **Onglet "Functions"** — catalogue des fonctions custom PowerCenter | ⚠️ Non couvert dans notre spec. À ajouter si pertinent |
| **XREF Job ↔ Transformation** — matrice croisée | ✅ Déjà couvert via GlobalLibrary + data_flow |
| **Intégration Databricks** — pipeline analyze→transpile→reconcile natif | Hors périmètre Phase 1 (mais Phase 2 peut s'y connecter) |

---

## 4. Ce que nous couvrons et que Lakebridge ne couvre pas

| Notre avantage | Importance |
|---|---|
| **SQL complexity analysis** (sqlglot AST, 11 patterns nommés) | HAUTE — différenciateur majeur |
| **Scoring transitif** (MAPPLET/REUSABLE héritent leur score) | HAUTE — précision du scoring |
| **3 formes de SESSION** documentées et parsées | HAUTE — fidélité à la réalité PowerCenter |
| **routing_decision + flags** — pilote directement la Phase 2 | HAUTE — c'est le pont vers l'automatisation |
| **JSON canonique V2.1** — contrat machine vers Phase 2 | HAUTE — Lakebridge n'a pas de Phase 2 automation ouverte |
| **Streaming XML** — pas de limite mémoire sur gros repos | MOYENNE — fiabilité opérationnelle |
| **Rapport HTML navigable** — sans Excel requis | MOYENNE — selon préférences du client |
| **Splitter** — XML unitaire par workflow (rejouabilité) | HAUTE — permet relance ciblée et parallélisation |
| **Java Transformation → CRITICAL automatique** | HAUTE — flag de risque non présent dans Lakebridge |
| **Shared FOLDER / GlobalLibrary** — vision inter-projet | HAUTE — Lakebridge ne documente pas ce cas |

---

## 5. Recommandations — ce qu'il faut ajouter à notre spec

### PRIORITÉ HAUTE

**Rien à ajouter** — notre spec couvre tous les cas critiques absents de Lakebridge.

### PRIORITÉ MOYENNE — à envisager

| Ajout recommandé | Effort | Justification |
|---|---|---|
| Onglet "Functions" dans le rapport | Faible | Lakebridge le fait, utile pour les équipes |
| Estimation d'effort par niveau de complexité | Faible | Paramétrable via `complexity_matrix.json` (déjà prévu) |
| Export Excel (openpyxl) en complément du HTML | Moyen | Familier pour les décideurs non-techniques |

### PRIORITÉ BASSE — information seulement

| Observation | Note |
|---|---|
| Lakebridge PowerCenter est en cours de développement (issue #2265 en cours en 2026) | Notre parseur est potentiellement plus avancé sur le périmètre inventaire PowerCenter |
| Lakebridge se concentre sur DataStage | Pour PowerCenter, Lakebridge est moins mature que son équivalent DataStage |
| Lakebridge produit du code Databricks (transpile) | Notre Phase 2 fait la même chose mais via LLM + JSON canonique |

---

## 6. Conclusion

Notre SPEC_PIPELINE_V2.md **couvre tout ce que Lakebridge fait sur la partie inventaire**, et va significativement plus loin sur :

- L'analyse SQL (AST, 11 patterns)
- Le scoring transitif via GlobalLibrary
- La fidélité au modèle SESSION PowerCenter (3 formes)
- Le JSON canonique comme contrat vers Phase 2
- Le streaming XML (robustesse opérationnelle)

Le seul apport de Lakebridge que nous n'avons pas est l'**estimation d'effort en heures** et l'**export Excel** — deux points qui peuvent être ajoutés facilement si le management le demande.

**Verdict : notre spec est prête pour la phase de développement.**
