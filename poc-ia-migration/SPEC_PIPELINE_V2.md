# Spec technique — Pipeline d'analyse Migration Informatica PowerCenter V2

> Version : 2.0 — Juillet 2026  
> Statut : DRAFT — en attente de validation

---

## Table des matières

1. [Vision et architecture globale](#1-vision-et-architecture-globale)
2. [Composant 1 — XML Splitter](#2-composant-1--xml-splitter)
3. [Composant 2 — Phase 1 Parser](#3-composant-2--phase-1-parser)
4. [Composant 3 — Rapport HTML (Option B)](#4-composant-3--rapport-html-option-b)
5. [Schéma JSON canonique (contrat Phase 1 → Phase 2)](#5-schéma-json-canonique-contrat-phase-1--phase-2)
6. [Audit du code existant — gaps et plan de migration](#6-audit-du-code-existant--gaps-et-plan-de-migration)
7. [Dépendances Python](#7-dépendances-python)
8. [Structure des fichiers output](#8-structure-des-fichiers-output)

---

## 1. Vision et architecture globale

### 1.1 Pipeline complet

```
XML Global (export Designer UI)
         │
         ▼
┌─────────────────────┐
│   XML Splitter      │  xml_splitter.py
│   (nouveau)         │  → un XML par workflow
└─────────────────────┘
         │
         ├── output/FOLDER_A/wf_clients_dim.xml
         ├── output/FOLDER_A/wf_orders_fact.xml
         ├── output/FOLDER_B/wf_sales_monthly.xml
         └── splitter_warnings.json
         │
         ▼
┌─────────────────────┐
│   Phase 1 Parser    │  parser_agent_v2.py
│   (refonte)         │  → un JSON par workflow
└─────────────────────┘
         │
         ├── Phase A : GlobalLibrary (MAPPLET, REUSABLE, WORKLET)
         ├── Phase B : Parsing XML unitaire
         ├── Phase C : SQL via sqlglot
         ├── Phase D : Scoring direct + transitif
         └── Phase E : IA optionnelle (--with-ai)
         │
         ├── output/canonical_json/FOLDER_A/wf_clients_dim.json
         ├── output/canonical_json/global_objects.json
         └── parser_warnings.json
         │
         ▼
┌─────────────────────┐
│  HTML Reporter V2   │  html_reporter_v2.py
│  (Option B)         │  → index + folder + workflow
└─────────────────────┘
         │
         ├── output/html_report/index.html
         ├── output/html_report/global_library.html
         ├── output/html_report/FOLDER_A/index.html
         └── output/html_report/FOLDER_A/wf_clients_dim.html
         │
         ▼
┌─────────────────────┐
│   Phase 2 LLM       │  (existant — non modifié dans ce sprint)
│   (inchangé)        │  JSON → code généré
└─────────────────────┘
```

### 1.2 Principes non négociables

- **Streaming** : lxml iterparse partout — jamais de chargement complet en mémoire
- **Généricité** : aucun type de transformation hardcodé — tout est extrait dynamiquement depuis le XML
- **Résilience** : une erreur sur un workflow ne bloque pas les autres — warnings loggés, traitement continue
- **Séparation stricte** : le scoring est 100% Python déterministe — le LLM ne touche jamais au scoring
- **Contrat JSON** : le JSON canonique est le seul lien entre Phase 1 et Phase 2 — s'il est incomplet, Phase 2 échoue

---

## 2. Composant 1 — XML Splitter

### 2.1 Rôle

Lire un XML PowerCenter global (multi-FOLDER, tous types d'objets) et produire un XML unitaire auto-contenu par WORKFLOW, avec toutes ses dépendances embarquées.

### 2.2 Fichier

`poc-ia-migration/agents/xml_splitter.py`

### 2.3 Structure du package

```
agents/xml_splitter.py
  ├── models.py          IndexEntry, MappingDeps, XmlIndex
  ├── indexer.py         Passe 1 : stream_index()
  ├── resolver.py        Passe 2 : resolve_workflow()
  ├── generator.py       Passe 3 : generate_workflow_xml()
  ├── warnings.py        WarningCollector, codes W001–W013
  └── utils.py           safe_filename(), lxml helpers
```

### 2.4 Modèles de données

```python
@dataclass
class IndexEntry:
    tag: str           # "MAPPLET", "TRANSFORMATION", "WORKFLOW", etc.
    name: str          # valeur de l'attribut NAME
    folder: str        # folder d'appartenance
    is_shared: bool    # FOLDER SHARED="SHARED"
    element: Any       # référence lxml Element (passe 1 uniquement)
    raw_xml: bytes     # XML sérialisé verbatim (après passe 1)

@dataclass
class MappingDeps:
    mapplet_refs: list[str]      # MAPPLETNAME attrs trouvés dans le MAPPING
    reusable_refs: list[str]     # INSTANCE[@REUSABLE="YES"] dans le MAPPING
    source_names: list[str]      # SOURCE et SQ référencés
    target_names: list[str]      # TARGET et Target Definition référencés
    worklet_refs: list[str]      # WORKLET[@REUSABLE="YES"] dans le WORKFLOW

@dataclass
class XmlIndex:
    # Clé : (folder_name, object_name)
    workflows:      dict[tuple, IndexEntry]
    mappings:       dict[tuple, IndexEntry]
    sessions:       dict[tuple, IndexEntry]
    mapplets:       dict[tuple, IndexEntry]
    transformations: dict[tuple, IndexEntry]  # REUSABLE="YES" hors MAPPING
    worklets:       dict[tuple, IndexEntry]   # REUSABLE="YES" au niveau FOLDER
    sources:        dict[tuple, IndexEntry]
    targets:        dict[tuple, IndexEntry]
    mapping_deps:   dict[tuple, MappingDeps]  # deps pré-calculées par mapping
    shared_folder:  str | None                # nom du FOLDER SHARED si présent

    def lookup(self, store, folder, name) -> IndexEntry | None:
        """Cherche dans folder, puis dans SHARED si non trouvé."""
        entry = store.get((folder, name))
        if entry is None and self.shared_folder:
            entry = store.get((self.shared_folder, name))
        return entry
```

### 2.5 Algorithme — 3 passes

#### Passe 1 : Indexation (streaming)

```python
def stream_index(xml_path: Path) -> XmlIndex:
    """
    Parcourt le XML avec iterparse en mode start/end.
    Indexe chaque objet au moment de son tag end (quand l'élément est complet).
    Appelle elem.clear() après chaque élément de niveau FOLDER et sous-éléments
    non conservés pour libérer la mémoire.
    """
```

Éléments indexés :

| Tag XML | Condition | Stocké dans |
|---|---|---|
| `FOLDER` | toujours | contexte courant (folder_name, is_shared) |
| `MAPPLET` | enfant direct de FOLDER | `index.mapplets` |
| `MAPPING` | enfant direct de FOLDER | `index.mappings` + `mapping_deps` |
| `WORKFLOW` | enfant direct de FOLDER | `index.workflows` |
| `SESSION` | enfant direct de FOLDER (standalone) | `index.sessions` |
| `WORKLET` | enfant direct de FOLDER + REUSABLE="YES" | `index.worklets` |
| `TRANSFORMATION` | enfant direct de FOLDER + REUSABLE="YES" | `index.transformations` |
| `SOURCE` | enfant direct de FOLDER | `index.sources` |
| `TARGET` | enfant direct de FOLDER | `index.targets` |

**Contrainte critique** : `elem.clear()` est appelé uniquement après sérialisation verbatim (`lxml.etree.tostring(elem)`). La référence `raw_xml` contient le XML brut tel qu'il était dans le fichier source, sans re-sérialisation.

#### Passe 2 : Résolution des dépendances par workflow

```python
def resolve_workflow(wf_entry: IndexEntry, index: XmlIndex) -> list[IndexEntry]:
    """
    DFS sur le graphe de dépendances.
    Retourne la liste ordonnée des IndexEntry à inclure dans le XML produit.
    Ordre DTD : SOURCE, TARGET, MAPPLET, TRANSFORMATION (reusable), MAPPING, SESSION, WORKFLOW.
    """
```

Algorithme DFS avec détection de cycles :

```
resolve_workflow(wf):
  visiting = set()   # cycle detection
  visited = set()    # deduplication

  1. Trouver le nom de la session (inline WORKFLOW ou via TASK TYPE="Session")
  2. Trouver la session → trouver le MAPPINGNAME
  3. Résoudre le MAPPING → extraire mapping_deps
  4. Pour chaque dep dans mapping_deps :
     - SOURCE/TARGET → lookup dans index
     - MAPPLET (via MAPPLETNAME) → lookup + résoudre récursivement ses deps
     - TRANSFORMATION REUSABLE → lookup
     - WORKLET REUSABLE → lookup + résoudre récursivement
  5. Retourner [sources, targets, mapplets, transformations, mapping, session, workflow]
     dans l'ordre DTD
```

Priorité de résolution : folder courant → SHARED folder.

#### Passe 3 : Génération du XML

```python
def generate_workflow_xml(
    wf_name: str,
    resolved: list[IndexEntry],
    repo_meta: dict,
    output_path: Path,
    warnings: WarningCollector
) -> None:
    """
    Écrit le XML unitaire en concaténant les raw_xml des IndexEntry résolus.
    Wrappe dans l'enveloppe POWERMART > REPOSITORY > FOLDER.
    N'utilise pas lxml pour la génération — écriture verbatim bytes.
    """
```

Structure du XML produit :

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE POWERMART SYSTEM "powrmart.dtd">
<POWERMART CREATION_DATE="{original}" REPOSITORY_VERSION="{original}">
  <REPOSITORY NAME="{original}" VERSION="{original}" CODEPAGE="{original}" DATABASETYPE="{original}">
    <FOLDER NAME="{folder_name}" ...attrs originaux...>
      <!-- Dans l'ordre DTD strict : -->
      {raw_xml de chaque SOURCE}
      {raw_xml de chaque TARGET}
      {raw_xml de chaque MAPPLET résolu}
      {raw_xml de chaque TRANSFORMATION REUSABLE résolu}
      {raw_xml du MAPPING}
      {raw_xml de la SESSION standalone si applicable}
      {raw_xml du WORKFLOW}
    </FOLDER>
  </REPOSITORY>
</POWERMART>
```

### 2.6 Conventions de nommage

```
output/
  split_xml/
    FOLDER_NAME/              ← safe_filename(folder_name)
      WF_NAME.xml             ← safe_filename(workflow_name) + ".xml"
  splitter_warnings.json
```

`safe_filename()` : `re.sub(r"[^A-Za-z0-9_\-]", "_", name).strip("_")`

### 2.7 Codes de warning

| Code | Gravité | Déclencheur | Comportement |
|---|---|---|---|
| W001 | WARNING | Element sans NAME attr | Skip + log |
| W002 | WARNING | MAPPLET référencé non trouvé dans l'index | Skip dep + log |
| W003 | WARNING | TRANSFORMATION REUSABLE référencé non trouvé | Skip dep + log |
| W004 | WARNING | SESSION introuvable pour un WORKFLOW | XML produit sans SESSION |
| W005 | WARNING | MAPPING introuvable pour une SESSION | XML produit sans MAPPING |
| W006 | WARNING | Cycle de dépendances détecté | Briser le cycle + log |
| W007 | WARNING | SOURCE/TARGET non trouvé | Skip + log |
| W008 | INFO | WORKLET REUSABLE non trouvé | Skip + log |
| W009 | ERROR | Workflow sans MAPPINGNAME résolvable | Workflow exclu du output |
| W010 | WARNING | Ambiguïté : objet présent dans folder ET SHARED | Folder prioritaire + log |
| W011 | WARNING | Fichier output déjà existant | Écrasement + log |
| W012 | ERROR | Erreur d'écriture fichier | Skip workflow + log |
| W013 | INFO | FOLDER sans WORKFLOW | FOLDER ignoré + log |

### 2.8 CLI

```
python agents/xml_splitter.py [OPTIONS] XML_PATH

Arguments :
  XML_PATH              Chemin vers le XML global PowerCenter

Options :
  --output      DIR     Dossier de sortie (défaut: output/split_xml)
  --folder      NAME    Traiter uniquement ce FOLDER
  --workflow    NAME    Traiter uniquement ce WORKFLOW (dans tous les folders)
  --fail-on-warning     Exit code 1 si des warnings existent

Codes de sortie :
  0   Succès sans warning
  1   Succès avec warnings (--fail-on-warning activé)
  2   Erreur fatale (XML illisible, dossier output non créable)
```

---

## 3. Composant 2 — Phase 1 Parser

### 3.1 Rôle

Parser un XML unitaire (produit par le Splitter) et produire un JSON canonique exhaustif. Ce JSON est le **contrat d'entrée pour Phase 2**.

### 3.2 Fichier

`poc-ia-migration/agents/parser_agent_v2.py` (coexiste avec l'existant)

### 3.3 Structure du package

```
parser_agent_v2.py
  ├── extractors/
  │   ├── mapping.py        _parse_transformations(), _parse_connectors()
  │   ├── session.py        _parse_session_inline(), _parse_session_standalone()
  │   ├── sources.py        _parse_sources(), _parse_targets()
  │   └── variables.py      _parse_variables(), _parse_valuepairs()
  ├── sql/
  │   ├── extractor.py      extract_all_sql_from_xml()
  │   └── analyzer.py       detect_sql_complexity() via sqlglot
  ├── scoring/
  │   ├── direct.py         score_transformation(), compute_direct_score()
  │   └── transitive.py     build_global_library(), compute_inherited_score()
  ├── ai/
  │   └── enricher.py       enrich_with_ai() — optionnel (--with-ai)
  └── schema/
      └── validator.py      validate_canonical_json() — vérification schéma
```

### 3.4 Algorithme en 5 phases

#### Phase A — Construction de la GlobalLibrary

Exécutée **une fois** avant de parser les workflows individuels. Lit tous les XMLs produits par le Splitter pour construire l'index des objets partagés.

```python
@dataclass
class GlobalLibraryEntry:
    name: str
    type: str                    # "MAPPLET", "TRANSFORMATION", "WORKLET", "STORED_PROCEDURE", "JAVA_TRANSFORMATION"
    folder: str
    is_shared: bool
    complexity_score: int
    flag: str                    # LOW/MEDIUM/HIGH/CRITICAL
    patterns_detected: list[str]
    propagates_critical: bool    # True si Java Transformation ou Stored Procedure dedans
    used_by_workflows: list[str] # rempli pendant Phase B
    description: str             # rempli par IA si --with-ai
```

#### Phase B — Parsing complet du XML

**Transformations (générique)** :

```python
def _parse_transformations(mapping: Element) -> list[dict]:
    """
    Itère sur TOUS les enfants TRANSFORMATION sans filtre sur TYPE.
    Le TYPE est extrait tel quel depuis l'attribut XML.
    Pour chaque TRANSFORMATION :
      - attrs communs : NAME, TYPE, ISVALID, REUSABLE, DESCRIPTION, OBJECTVERSION
      - ports : tous les TRANSFORMFIELD avec leurs attrs
      - config : tous les TABLEATTRIBUTE en dict {name: value}
      - mapplet_ref : si TYPE="Mapplet", extraire MAPPLETNAME attr
    """
```

**SESSION — deux formes** :

```python
def _parse_session_inline(workflow: Element) -> dict | None:
    """SESSION enfant direct de WORKFLOW → SESSIONATTRIBUTE elements."""

def _parse_session_standalone(folder: Element, mapping_name: str) -> dict | None:
    """SESSION enfant de FOLDER, ou TASK TYPE="Session" dans WORKFLOW → ATTRIBUTE elements."""
```

Champs extraits pour les deux formes :

```python
{
    "name": str,
    "mapping_name": str,                    # MAPPINGNAME attr ou ATTRIBUTE "Mapping Name"
    "source_connection": str,               # "$Source connection value"
    "target_connection": str,               # "$Target connection value"
    "treat_source_rows_as": str,            # "Data Driven" | "Insert"
    "recovery_strategy": str,
    "parameter_file": str,                  # "Parameter Filename"
    "pre_sql": str,                         # "Pre SQL"
    "post_sql": str,                        # "Post SQL"
    "partition_type": str,                  # "Partition type"
    "num_partitions": int,                  # "Number of Partitions"
    "bulk_mode": bool,                      # "Bulk Mode" == "YES"
    "variables_override": list[dict],       # VALUEPAIR ou SESSTRANSFORMATIONINST > ATTRIBUTE $$
    "per_transformation_overrides": list[dict]  # SESSTRANSFORMATIONINST
}
```

**CONNECTOR — deux dialectes normalisés** :

```python
def _normalize_connector(elem: Element) -> dict:
    """
    Dialect 1 : FROMTRANSFORMATION/TOTRANSFORMATION
    Dialect 2 : FROMINSTANCE/TOINSTANCE avec FROMINSTANCETYPE/TOINSTANCETYPE
    → Sortie normalisée identique pour les deux :
    {
        "from_instance": str,
        "from_field": str,
        "from_group": str | None,    # Router FROMGROUP
        "to_instance": str,
        "to_field": str,             # peut contenir "FIELD[N]" pour Normalizer
        "from_type": str | None,     # FROMINSTANCETYPE si dialect 2
        "to_type": str | None        # TOINSTANCETYPE si dialect 2
    }
    """
```

#### Phase C — Extraction et analyse SQL

Sources SQL à extraire (toutes) :

| Source | Element | Attribut |
|---|---|---|
| SQL override SQ | TABLEATTRIBUTE NAME="Sql Query" | VALUE |
| Source Filter SQ | TABLEATTRIBUTE NAME="Source Filter" | VALUE |
| User Defined Join | TABLEATTRIBUTE NAME="User Defined Join" | VALUE |
| Filter condition | TABLEATTRIBUTE NAME="Filter Condition" | VALUE |
| Lookup condition | TABLEATTRIBUTE NAME="Lookup Condition" | VALUE |
| Joiner condition | TABLEATTRIBUTE NAME="Join Condition" | VALUE |
| Update Strategy expr | TABLEATTRIBUTE NAME="Update Strategy Expression" | VALUE |
| Router group condition | TABLEATTRIBUTE NAME="OutputN.Group Condition" | VALUE |
| Expression field | TRANSFORMFIELD EXPRESSION attr | — |
| Pre SQL session | SESSIONATTRIBUTE NAME="Pre SQL" | VALUE |
| Post SQL session | SESSIONATTRIBUTE NAME="Post SQL" | VALUE |

**Fonction de détection SQL via sqlglot** :

```python
def detect_sql_complexity(sql: str, dialect: str = "oracle") -> dict:
    """
    Parse le SQL avec sqlglot et traverse l'AST pour détecter :
    - window_function      : exp.Window (ROW_NUMBER, RANK, LAG, LEAD...)
    - nested_subqueries    : exp.Subquery count > 1
    - connect_by           : exp.Connect (Oracle hiérarchique)
    - pivot                : exp.Pivot
    - merge                : isinstance(tree, exp.Merge)
    - oracle_hints         : "/*+" dans le SQL brut
    - conditional_aggregation : exp.Sum/Count/Avg contenant exp.Case
    - correlated_subquery  : sous-requête référençant l'outer query
    - distinct_count       : exp.Count avec distinct=True
    - multiple_joins       : count(exp.Join) >= 3
    - oracle_functions     : TO_DATE, NVL, DECODE, ROWNUM, SYSDATE, TRUNC, INITCAP

    Retourne :
    {
        "available": True,
        "dialect": "oracle",
        "level": "LOW" | "MEDIUM" | "HIGH" | "CRITICAL",
        "patterns": ["window_function:RowNumber", "connect_by_hierarchical"],
        "tables_referenced": ["ORDERS", "PRODUCTS"],
        "spark_sql": str | None    # conversion sqlglot si possible
    }
    """
```

Niveaux :

| Patterns | Niveau |
|---|---|
| Aucun | LOW |
| oracle_functions, distinct_count, simple subquery | MEDIUM |
| window_function, multiple_joins, nested_subqueries | HIGH |
| connect_by, merge, correlated_subquery | CRITICAL |

#### Phase D — Scoring

**Score direct** (par transformation) :

```python
def score_transformation(t: dict, sql_analysis: dict | None) -> tuple[int, dict]:
    """
    Utilise complexity_matrix.json pour le score de base par TYPE.
    Modificateurs additifs selon les patterns détectés :
      +3 si sql_analysis.level == HIGH
      +5 si sql_analysis.level == CRITICAL
      +2 si PORTTYPE RETURN (unconnected lookup ou stored proc)
      +1 si REUSABLE == YES
    Retourne (score, breakdown_dict)
    """
```

**Score transitif** (héritage GlobalLibrary) :

```python
def compute_inherited_score(transformations: list[dict], library: GlobalLibrary) -> tuple[int, list[dict]]:
    """
    Pour chaque transformation avec mapplet_ref :
      - Chercher dans GlobalLibrary par nom
      - Ajouter library_entry.complexity_score au score hérité
      - Si library_entry.propagates_critical → forcer flag CRITICAL sur le workflow
    Retourne (inherited_score, inherited_objects_list)
    """
```

**Score final** :

```python
total_score = direct_score + inherited_score
flag = compute_flag(total_score)
# Forçage CRITICAL si Java Transformation détectée (direct ou héritée)
if has_java_transformation or any_inherited_propagates_critical:
    flag = "CRITICAL"
```

#### Phase E — Enrichissement IA (optionnel)

Déclenché par `--with-ai`. N'envoie **jamais** le XML brut au LLM. Envoie un résumé structuré en texte :

```python
def build_ai_prompt(canonical: dict) -> str:
    """
    Construit un résumé textuel structuré depuis le JSON canonique :
    - Nom du workflow, sources, cibles
    - Types de transformations présents
    - SQL notable (extrait, pas complet)
    - Flags de complexité
    → Demande au LLM une description métier en 2-3 phrases
    """
```

Résultat ajouté dans le JSON sous `ai_enrichment.business_description`.

### 3.5 Codes de warning parser

| Code | Déclencheur |
|---|---|
| W001 | FOLDER sans MAPPING |
| W002 | MAPPING sans TRANSFORMATION |
| W003 | SESSION introuvable (inline et standalone) |
| W004 | SQL non parseable par sqlglot |
| W005 | MAPPLET référencé (MAPPLETNAME) absent de la GlobalLibrary |
| W006 | REUSABLE TRANSFORMATION référencée absent de la GlobalLibrary |
| W007 | CONNECTOR référence une TRANSFORMATION inconnue |
| W008 | SOURCE/TARGET absent du XML (seulement en Source Qualifier mode) |
| W009 | Variable $$ dans expression sans MAPPINGVARIABLE correspondante |
| W010 | TABLEATTRIBUTE "Sql Query" non vide mais sqlglot retourne erreur de parse |
| W011 | TRANSFORMFIELD EXPRESSION contient :LKP. (unconnected lookup call) |
| W012 | Pre SQL ou Post SQL présent mais non parseable |
| W013 | WORKLET référencé absent du XML |
| W014 | Score > 20 (workflow exceptionnellement complexe) |

### 3.6 CLI

```
python agents/parser_agent_v2.py SOUS-COMMANDE [OPTIONS]

Sous-commandes :
  parse      Parser un seul XML unitaire
  batch      Parser tous les XMLs d'un dossier
  build-library  Construire global_objects.json depuis un dossier de XMLs splittés

Options communes :
  --input    PATH    XML ou dossier d'entrée
  --output   DIR     Dossier de sortie JSON (défaut: output/canonical_json)
  --with-ai          Activer enrichissement LLM (Phase E)
  --dialect  STR     Dialecte SQL pour sqlglot (défaut: oracle)
  --matrix   PATH    Chemin vers complexity_matrix.json
  --warnings PATH    Chemin vers le fichier de warnings output
  --verbose          Log détaillé

Codes de sortie :
  0   Succès
  1   Succès avec warnings
  2   Erreur partielle (certains workflows échoués)
  3   Erreur fatale
  4   Dépendance manquante (sqlglot non installé)
  5   complexity_matrix.json introuvable
```

---

## 4. Composant 3 — Rapport HTML (Option B)

### 4.1 Rôle

Générer un rapport HTML statique multi-niveaux depuis les JSONs canoniques. Zéro framework, zéro CDN, auto-contenu.

### 4.2 Fichier

`poc-ia-migration/agents/html_reporter_v2.py` (coexiste avec phase1_reporter.py)

### 4.3 Structure des fichiers output

```
output/html_report/
  index.html                    ← Vue Repository
  global_library.html           ← Bibliothèque globale
  FOLDER_A/
    index.html                  ← Vue Sous-projet
    wf_clients_dim.html         ← Vue Workflow
    wf_orders_fact.html
  FOLDER_B/
    index.html
    wf_sales_monthly.html
```

### 4.4 Ordre de génération

```python
def generate_all(input_dir, output_dir, project_name):
    workflows = load_canonical_jsons(input_dir)
    global_objects = load_global_objects(input_dir)  # global_objects.json
    folders = group_by_folder(workflows)

    # 1. Pages workflow (dans leur sous-dossier)
    for folder_name, wf_list in folders.items():
        for wf in wf_list:
            generate_workflow_page(wf, folder_name, output_dir / folder_name)

    # 2. Index de folders
    for folder_name, wf_list in folders.items():
        generate_folder_index(folder_name, wf_list, global_objects, output_dir / folder_name)

    # 3. Bibliothèque globale
    generate_global_library(global_objects, workflows, output_dir)

    # 4. Index repository (en dernier — agrège tout)
    generate_repository_index(folders, global_objects, output_dir)
```

### 4.5 Page 1 — `index.html` (Vue Repository)

**Sections :**

1. En-tête : nom repository, date export, date analyse, bouton dark/light mode
2. Bloc warnings globaux (si présents)
3. KPI Cards (4) : Folders / Workflows / Score moyen / Objets globaux
4. Barre de répartition par niveau (stacked bar colorée LOW/MEDIUM/HIGH/CRITICAL)
5. Graphique donut SVG
6. Tableau des folders :

| Folder | Workflows | Complexité dominante | Score max | Dépendances globales | Statut | Lien |
|---|---|---|---|---|---|---|

   - Statut : `OK` (vert) / `Revue` (orange) / `Bloqué` (rouge)
   - Tri : Bloqué > Revue > OK, puis score max décroissant

7. Lien vers `global_library.html`
8. Footer

### 4.6 Page 2 — `global_library.html` (Bibliothèque globale)

**Sections :**

1. Breadcrumb : Repository › Bibliothèque globale
2. En-tête : N objets, K CRITICAL, M workflows impactés
3. Bloc alerte CRITICAL (si objets CRITICAL présents)
4. Tableau principal :

| Nom | Type | Score | Niveau | Patterns | Workflows impactés | Impact propagé |
|---|---|---|---|---|---|---|

5. Matrice d'impact croisé (expandable) : objets × folders
6. État vide si `global_objects.json` absent

**Note** : chaque objet a un `id` HTML pour ancrage depuis les pages workflow (`global_library.html#NOM_OBJET`).

### 4.7 Page 3 — `FOLDER/index.html` (Vue Sous-projet)

**Sections :**

1. Breadcrumb : Repository › FOLDER_NAME
2. Card en-tête folder : nom, owner, description, date
3. KPI Cards (4) : Workflows / Score moyen / Objets locaux / Dépendances globales
4. Mini donut SVG de répartition du folder
5. Tableau des workflows :

| Workflow | Score direct | Score hérité | Score total | Niveau | Transformations clés | Connexions | Lien |
|---|---|---|---|---|---|---|---|

6. Dépendances vers bibliothèque globale (si présentes)
7. Connexions du folder (sources et cibles uniques agrégées)
8. Warnings du folder

### 4.8 Page 4 — `FOLDER/wf_NOM.html` (Vue Workflow)

**Sections (dans l'ordre) :**

1. Breadcrumb : Repository › FOLDER › wf_nom
2. Warning banner (si CRITICAL ou HIGH)
3. Card en-tête enrichi :
   - Nom workflow + mapping + folder
   - Score décomposé visuellement : `Direct Xpts + Hérité Ypts = Total Zpts`
   - Niveau, estimation jours, plateforme cible
4. **[NOUVEAU]** Bloc connexions Source → Cible (connexions nommées + tables physiques)
5. Sources et cibles (tables avec schéma, db, type SGBD, nb champs)
6. **[NOUVEAU]** Variables `$$` et fichier de paramètres
7. **[NOUVEAU]** Commandes de session (Pre SQL / Post SQL) — si présents
8. Data flow SVG + modal zoom/pan (existant, conservé intact)
9. Tableau des transformations (enrichi d'une colonne Expression/SQL)
10. **[NOUVEAU]** Sections SQL détaillées (une par transformation avec SQL override)
11. **[NOUVEAU]** Objets hérités (mapplets, reusable) avec lien vers global_library
12. Décomposition du score
13. Points de vigilance (requires_human_review)
14. **[NOUVEAU]** Description métier (si --with-ai activé)
15. Navigation bas de page : ← Folder / ⌂ Repository / 📚 Bibliothèque

### 4.9 Composants réutilisables

```python
_flag_badge(flag)               # Badge LOW/MEDIUM/HIGH/CRITICAL
_kpi_card(title, value, sub)    # Carte KPI
_complexity_bar(score, max=20)  # Barre de progression colorée
_warning_box(messages, level)   # Boîte alerte (warning/error/info)
_breadcrumb(parts)              # Fil d'Ariane avec liens relatifs
_donut_svg(flag_counts)         # Donut SVG (repris existant)
_score_decomp(direct, inherited, total)  # Score décomposé visuel
_conn_flow(source_conn, target_conn, sources, targets)  # Flux connexions
_sql_block(sql, dialect)        # Bloc SQL coloré (oracle=rouge, spark=bleu)
```

### 4.10 Dark mode

Variables CSS complètes avec `[data-theme="dark"]` + `prefers-color-scheme` + toggle bouton + `localStorage`. Script injecté en fin de `<head>`.

### 4.11 Palette de couleurs

```python
FLAG_COLORS = {
    "LOW":      {"badge_bg": "#10b981", "badge_text": "#fff"},
    "MEDIUM":   {"badge_bg": "#f59e0b", "badge_text": "#000"},
    "HIGH":     {"badge_bg": "#ef4444", "badge_text": "#fff"},
    "CRITICAL": {"badge_bg": "#b91c1c", "badge_text": "#fff"},
}
```

### 4.12 Gestion des champs manquants

| Champ absent | Comportement |
|---|---|
| `folder_name` | Folder fictif "DEFAULT" |
| `direct_score` / `inherited_score` | `total_score` / `0` |
| `inherited_objects` | Section absente |
| `session.pre_sql` / `post_sql` | Section absente |
| `session.connections` | Section connexions absente |
| `warnings` | Section absente |
| `data_flow` vide | Section data flow absente |
| `global_objects.json` absent | global_library.html avec état vide |

### 4.13 CLI

```
python agents/html_reporter_v2.py [OPTIONS]

Options :
  --input       DIR    Dossier canonical JSON (défaut: output/canonical_json)
  --output      DIR    Dossier HTML (défaut: output/html_report)
  --project     STR    Nom du repository
  --export-date STR    Date export Informatica (YYYY-MM-DD)
  --no-library         Ne pas générer global_library.html
```

---

## 5. Schéma JSON canonique (contrat Phase 1 → Phase 2)

```json
{
  "meta": {
    "schema_version": "2.0",
    "parsed_at": "2026-07-12T10:30:00Z",
    "parser_version": "2.0.0",
    "warnings": ["W004: SQL non parseable dans SQ_ORDERS"]
  },

  "workflow": {
    "id": "wf_clients_dim",
    "server": "INT_SERVER",
    "is_enabled": true,
    "scheduler_type": "ONDEMAND",
    "description": "Chargement dimension clients"
  },

  "folder": {
    "name": "DIM_Finance",
    "owner": "equipe_finance",
    "description": "Dimensions financières",
    "is_shared": false
  },

  "mapping": {
    "id": "m_clients_dim",
    "is_valid": true,
    "description": ""
  },

  "sources": [
    {
      "name": "CLIENTS",
      "owner": "DW_OWNER",
      "database": "PROD_DB",
      "db_type": "Oracle",
      "fields": [
        {"name": "CLIENT_ID", "datatype": "number", "precision": 10, "scale": 0,
         "nullable": false, "key_type": "PRIMARY KEY"}
      ]
    }
  ],

  "targets": [
    {
      "name": "DIM_CLIENTS",
      "owner": "DW_OWNER",
      "database": "DW_DB",
      "db_type": "Oracle",
      "fields": [
        {"name": "CLIENT_SK", "datatype": "number", "precision": 10,
         "nullable": false, "key_type": "PRIMARY KEY"}
      ]
    }
  ],

  "transformations": [
    {
      "name": "SQ_CLIENTS",
      "type": "Source Qualifier",
      "is_valid": true,
      "reusable": false,
      "description": "",
      "complexity_score": 3,
      "complexity_flag": "MEDIUM",
      "score_breakdown": {"base": 1, "sql_complexity": 2},
      "mapplet_ref": null,
      "config": {
        "sql_query": "SELECT CLIENT_ID FROM CLIENTS WHERE ROWNUM <= $$MAX_ROWS",
        "source_table": "CLIENTS",
        "source_filter": "",
        "select_distinct": false
      },
      "sql_analysis": {
        "available": true,
        "dialect": "oracle",
        "level": "MEDIUM",
        "patterns": ["oracle_functions:ROWNUM"],
        "tables_referenced": ["CLIENTS"],
        "spark_sql": "SELECT CLIENT_ID FROM CLIENTS LIMIT ${MAX_ROWS}"
      },
      "ports": [
        {"name": "CLIENT_ID", "datatype": "number", "port_type": "OUTPUT",
         "precision": 10, "scale": 0, "expression": null}
      ]
    },
    {
      "name": "MLT_CLEAN_ADDR_INST",
      "type": "Mapplet",
      "mapplet_ref": "MLT_CLEAN_ADDRESS",
      "reusable": false,
      "complexity_score": 0,
      "ports": [
        {"name": "IN_ADDR", "datatype": "string", "port_type": "INPUT"},
        {"name": "OUT_ADDR", "datatype": "string", "port_type": "OUTPUT"}
      ],
      "config": {}
    }
  ],

  "connectors": [
    {
      "from_instance": "SQ_CLIENTS",
      "from_field": "CLIENT_ID",
      "from_group": null,
      "to_instance": "EXP_CALC",
      "to_field": "IN_CLIENT_ID",
      "from_type": null,
      "to_type": null
    }
  ],

  "data_flow": [
    {"from": "SQ_CLIENTS", "to": "EXP_CALC", "fields": ["CLIENT_ID"]}
  ],

  "session": {
    "name": "s_clients_dim",
    "mapping_name": "m_clients_dim",
    "source_connection": "CONN_ORA_PROD",
    "target_connection": "CONN_DWH",
    "treat_source_rows_as": "Data Driven",
    "recovery_strategy": "Fail task and continue workflow",
    "parameter_file": "$PMRootDir/parameter_files/clients.par",
    "pre_sql": "",
    "post_sql": "",
    "partition_type": null,
    "num_partitions": null,
    "bulk_mode": false,
    "variables": [
      {"name": "$$BATCH_DATE", "default": "SYSDATE-1", "override": "2026-07-01"}
    ]
  },

  "variables": [
    {"name": "$$BATCH_DATE", "datatype": "string", "default": "SYSDATE-1"},
    {"name": "$$MAX_ROWS", "datatype": "decimal", "default": "500000"}
  ],

  "complexity": {
    "direct_score": 6,
    "inherited_score": 4,
    "total_score": 10,
    "flag": "HIGH",
    "estimated_migration_days": "3-5",
    "auto_conversion": false,
    "propagates_critical": false,
    "score_breakdown": {
      "per_transformation": {"SQ_CLIENTS": 3, "EXP_CALC": 2, "LKP_STATUS": 1},
      "global_modifiers": {"unconnected_lookup": 1, "mappingvariable": 0},
      "inherited": {"MLT_CLEAN_ADDRESS": 4}
    }
  },

  "inherited_objects": [
    {
      "name": "MLT_CLEAN_ADDRESS",
      "type": "MAPPLET",
      "score": 4,
      "flag": "MEDIUM",
      "source": "SHARED_REUSABLE",
      "propagates_critical": false
    }
  ],

  "flags": {
    "has_java_transformation": false,
    "has_stored_procedure": false,
    "has_custom_transformation": false,
    "has_unconnected_lookup": false,
    "has_normalizer": false,
    "has_dynamic_lookup": false,
    "has_sql_override": true,
    "has_parameter_file": true,
    "has_pre_sql": false,
    "has_post_sql": false,
    "has_bulk_load": false,
    "has_partitioning": false,
    "oracle_functions_detected": ["ROWNUM", "TO_DATE"],
    "requires_human_review": ["SQ_CLIENTS: ROWNUM à remplacer par ROW_NUMBER() OVER (...)"]
  },

  "routing_decision": {
    "target_platform": "pyspark",
    "auto_conversion_feasibility": "MEDIUM",
    "human_intervention_required": true,
    "rationale": "SQL override avec ROWNUM Oracle nécessite réécriture manuelle."
  },

  "ai_enrichment": {
    "available": false,
    "business_description": null,
    "generated_at": null
  }
}
```

---

## 6. Audit du code existant — gaps et plan de migration

### 6.1 Ce qu'on garde intact

| Fichier | Fonctions | Raison |
|---|---|---|
| `parser_agent.py` | `_parse_fields()`, `_parse_attributes()` | Génériques, parfaits |
| `parser_agent.py` | `_parse_connectors()`, `build_data_flow()` | Solides |
| `parser_agent.py` | `analyse_sql()` | Complet, réutilisable pour Pre/Post SQL |
| `parser_agent.py` | `call_claude()`, `extract_json()` | Génériques subprocess LLM |
| `parser_agent.py` | `score_transformation()`, `compute_complexity()` | Base du scoring à enrichir |
| `utils.py` | Tout | `slim_canonical`, `select_rag_sections`, `apply_function_patches` |
| `phase1_reporter.py` | `_flag_badge()`, `_donut_svg()`, `_data_flow_svg()` | Réutilisés dans v2 |
| `phase1_reporter.py` | `DATA_FLOW_MODAL_JS`, `COMMON_CSS` | Copiés dans html_reporter_v2.py |

### 6.2 Ce qu'on refactorise

| Fichier | Fonction | Changement |
|---|---|---|
| `parser_agent.py` | `parse_xml()` ligne 195 | `repo.find("FOLDER")` → `repo.findall("FOLDER")`, retourne liste |
| `parser_agent.py` | `_parse_workflow()` ligne 171 | Refactoriser en `_parse_session_inline()` + `_parse_session_standalone()` |
| `parser_agent.py` | `_parse_transformations()` ligne 119 | Ajouter `mapplet_ref`, `worklet_ref` |
| `parser_agent.py` | `_detect_lookup_flags()` ligne 378 | Corriger `lookup_connected_dynamic` (toujours False — bug ligne 387) |
| `parser_agent.py` | `compute_complexity()` ligne 442 | Ajouter scoring transitif |

### 6.3 Ce qu'on crée (nouveau)

| Nouveau fichier | Rôle |
|---|---|
| `agents/xml_splitter.py` | Splitter complet |
| `agents/parser_agent_v2.py` | Parser refactorisé |
| `agents/html_reporter_v2.py` | Reporter Option B |

### 6.4 Code mort identifié

- `MODEL = "claude-haiku-4-5-20251001"` hardcodé → externaliser en config
- Chemins hardcodés sur `wf_clients_dim` dans `codegen_agent.py`, `fixer_agent.py`, `qa_agent.py`

### 6.5 Bug connu

`_detect_lookup_flags()` ligne 387 : `lookup_connected_dynamic` est toujours `False` — la condition de détection n'est jamais vraie. À corriger dans la v2.

---

## 7. Dépendances Python

### Existantes (à conserver)

```
xml.etree.ElementTree  # parsing XML (remplacé par lxml dans splitter)
sqlglot                # analyse SQL (rendre obligatoire, pas optionnel)
subprocess             # appel LLM CLI
```

### À ajouter

```
lxml                   # streaming iterparse pour le Splitter
```

### À ne PAS ajouter

```
pandas                 # déjà absent du parser/reporter — bien ainsi
Jinja2                 # templates via f-strings Python uniquement
networkx               # scoring transitif calculable sans graphe externe
```

---

## 8. Structure des fichiers output

```
output/
  split_xml/                        ← Splitter output
    splitter_warnings.json
    FOLDER_A/
      wf_clients_dim.xml
      wf_orders_fact.xml
    FOLDER_B/
      wf_sales_monthly.xml

  canonical_json/                   ← Parser output
    parser_warnings.json
    global_objects.json             ← GlobalLibrary sérialisée
    FOLDER_A/
      wf_clients_dim.json
      wf_orders_fact.json
    FOLDER_B/
      wf_sales_monthly.json

  html_report/                      ← Reporter output
    index.html
    global_library.html
    FOLDER_A/
      index.html
      wf_clients_dim.html
      wf_orders_fact.html
    FOLDER_B/
      index.html
      wf_sales_monthly.html

  01_canonical_json/                ← Existant (Phase 1 actuelle) — non modifié
  phase1_report/                    ← Existant (reporter actuel) — non modifié
```

---

*Fin de spec — SPEC_PIPELINE_V2.md*
