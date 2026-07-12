# Spec technique — Pipeline d'analyse Migration Informatica PowerCenter V2

> Version : 2.1 — Juillet 2026  
> Statut : VALIDÉ — corrigé après 2 passes de relecture critique

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
9. [Exemple de pipeline CLI complet](#9-exemple-de-pipeline-cli-complet)

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
         ├── output/split_xml/FOLDER_A/wf_clients_dim.xml    ← [B1: corrigé]
         ├── output/split_xml/FOLDER_A/wf_orders_fact.xml
         ├── output/split_xml/FOLDER_B/wf_sales_monthly.xml
         └── output/split_xml/splitter_warnings.json
         │
         ▼
┌─────────────────────┐
│   Phase 1 Parser    │  parser_agent_v2.py
│   (refonte)         │  → un JSON par workflow
└─────────────────────┘
         │
         ├── Phase A : GlobalLibrary (depuis XML global — voir §3.4)
         ├── Phase B : Parsing XML unitaire
         ├── Phase C : SQL via sqlglot
         ├── Phase D : Scoring direct + transitif
         └── Phase E : IA optionnelle (--with-ai)
         │
         ├── output/canonical_json/FOLDER_A/wf_clients_dim.json
         ├── output/canonical_json/global_objects.json
         └── output/canonical_json/parser_warnings.json
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
- **Généricité** : aucun type de transformation hardcodé dans le code de parsing/extraction. Le scoring utilise `complexity_matrix.json` comme référentiel externe ; tout type non référencé reçoit `base_score=0`
- **Résilience** : une erreur sur un workflow ne bloque pas les autres — warnings loggés, traitement continue
- **Séparation stricte** : le scoring est 100% Python déterministe — le LLM ne touche jamais au scoring ni à `routing_decision`
- **Contrat JSON** : le JSON canonique est le seul lien entre Phase 1 et Phase 2 — s'il est incomplet, Phase 2 échoue

### 1.3 Compatibilité backward (schema V1 → V2)

Le schéma JSON V2 est un breaking change par rapport à l'existant. Stratégie retenue : **alias de champs** dans le JSON produit. Le JSON V2 contient à la fois les nouveaux champs et les anciens pour ne pas casser `run_pipeline.py` et `phase1_reporter.py` existants durant la transition.

```json
{
  "workflow_id": "wf_clients_dim",          // alias V1 (legacy)
  "mapping_id": "m_clients_dim",            // alias V1 (legacy)
  "workflow_complexity": { ... },           // alias V1 (legacy, même structure)
  "workflow": { "id": "wf_clients_dim" },   // V2
  "mapping": { "id": "m_clients_dim" },     // V2
  "complexity": { ... }                     // V2
}
```

Les agents Phase 2 (`codegen_agent.py`, `fixer_agent.py`, `qa_agent.py`) et leurs chemins hardcodés sont hors scope de ce sprint — ils restent sur les JSONs V1.

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
  ├── warnings.py        WarningCollector, codes S-W001–S-W013
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
    raw_xml: bytes     # XML sérialisé verbatim après passe 1

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
    workflows:       dict[tuple, IndexEntry]
    mappings:        dict[tuple, IndexEntry]
    sessions:        dict[tuple, IndexEntry]   # SESSION standalone (FOLDER level)
    mapplets:        dict[tuple, IndexEntry]
    transformations: dict[tuple, IndexEntry]   # REUSABLE="YES" hors MAPPING
    worklets:        dict[tuple, IndexEntry]   # REUSABLE="YES" au niveau FOLDER
    sources:         dict[tuple, IndexEntry]
    targets:         dict[tuple, IndexEntry]
    mapping_deps:    dict[tuple, MappingDeps]  # deps pré-calculées par mapping

    # [B5/B7: corrigé] Attributs de l'enveloppe — capturés à start event
    powermart_attrs: dict         # CREATION_DATE, REPOSITORY_VERSION
    repository_attrs: dict        # NAME, VERSION, CODEPAGE, DATABASETYPE
    folder_attrs: dict[str, dict] # folder_name → tous les attrs XML verbatim

    shared_folder: str | None     # nom du FOLDER SHARED si présent

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
    
    [B5: corrigé] Stratégie mémoire :
    
    Événements START (avant que l'élément soit complet) :
      - "start POWERMART" → capturer dict(elem.attrib) dans index.powermart_attrs
      - "start REPOSITORY" → capturer dict(elem.attrib) dans index.repository_attrs
      - "start FOLDER" → capturer dict(elem.attrib) dans index.folder_attrs[NAME]
                         + mettre à jour current_folder, current_is_shared
      Ces attributs DOIVENT être capturés au START car l'élément n'est pas
      encore fermé — on ne peut pas attendre le END.

    Événements END (élément complet) :
      - Pour chaque objet TOP-LEVEL enfant de FOLDER (MAPPING, WORKFLOW, etc.) :
        1. Sérialiser : raw_xml = etree.tostring(elem, encoding="unicode")
        2. Créer IndexEntry et stocker dans l'index approprié
        3. elem.clear()
        4. if elem.getparent() is not None: elem.getparent().remove(elem)
           (pattern lxml recommandé pour libérer la mémoire réellement)
    
    Ne jamais appeler clear() sur FOLDER, REPOSITORY, POWERMART.
    """
```

Éléments indexés :

| Tag XML | Condition | Stocké dans | Quand |
|---|---|---|---|
| `FOLDER` | toujours | `folder_attrs` + contexte courant | START |
| `POWERMART` | toujours | `powermart_attrs` | START |
| `REPOSITORY` | toujours | `repository_attrs` | START |
| `MAPPLET` | enfant direct de FOLDER | `index.mapplets` | END |
| `MAPPING` | enfant direct de FOLDER | `index.mappings` + `mapping_deps` | END |
| `WORKFLOW` | enfant direct de FOLDER | `index.workflows` | END |
| `SESSION` | enfant direct de FOLDER (standalone) | `index.sessions` | END |
| `WORKLET` | enfant direct de FOLDER + REUSABLE="YES" | `index.worklets` | END |
| `TRANSFORMATION` | enfant direct de FOLDER + REUSABLE="YES" | `index.transformations` | END |
| `SOURCE` | enfant direct de FOLDER | `index.sources` | END |
| `TARGET` | enfant direct de FOLDER | `index.targets` | END |

**Note sur les WORKLETs inline** [B4: corrigé] : Un WORKLET avec `REUSABLE="NO"` est imbriqué dans le WORKFLOW lui-même. Il n'est PAS indexé séparément — il est inclus dans le `raw_xml` du WORKFLOW parent et copié tel quel dans le XML splitté. Son analyse est faite récursivement par le Parser depuis l'élément WORKFLOW. S'il contient des dépendances non résolues, le warning S-W013 est émis.

#### Passe 2 : Résolution des dépendances par workflow

```python
def resolve_workflow(wf_entry: IndexEntry, index: XmlIndex) -> list[IndexEntry]:
    """
    DFS sur le graphe de dépendances.
    Retourne la liste ordonnée des IndexEntry à inclure dans le XML produit.
    Ordre DTD : SOURCE, TARGET, MAPPLET, TRANSFORMATION (reusable), MAPPING, SESSION, WORKFLOW.
    """
```

**[B6: corrigé] Résolution du MAPPINGNAME** — trois formes dans l'ordre de priorité :

```
Forme A : SESSION enfant direct de WORKFLOW (attribut direct)
  → wf_elem.find("SESSION").get("MAPPINGNAME")

Forme B : TASK TYPE="Session" enfant de WORKFLOW (sous-élément ATTRIBUTE)
  → wf_elem.find("TASK[@TYPE='Session']")
    .find("ATTRIBUTE[@NAME='Mapping Name']").get("VALUE")

Forme C : SESSION standalone dans FOLDER (référencée via TASK REFSESSION)
  → session_name = wf_elem.find("TASK").get("REFSESSION")
    session = index.sessions.get((folder, session_name))
    → session_elem.get("MAPPINGNAME")
```

**[B3: corrigé] Trois formes de SESSION — distinctions** :

| Forme | Parent XML | Élément config | Attribut MAPPINGNAME |
|---|---|---|---|
| Inline | `WORKFLOW` | `<SESSIONATTRIBUTE>` | Attr direct `SESSION[@MAPPINGNAME]` |
| Via TASK | `WORKFLOW` | `<ATTRIBUTE>` sous `<TASK>` | `ATTRIBUTE[@NAME='Mapping Name']/@VALUE` |
| Standalone | `FOLDER` | `<ATTRIBUTE>` | Attr direct `SESSION[@MAPPINGNAME]` |

Algorithme DFS avec détection de cycles :

```
resolve_workflow(wf):
  visiting = set()   # cycle detection
  visited = set()    # deduplication

  1. Trouver le MAPPINGNAME (3 formes ci-dessus)
  2. Résoudre le MAPPING → extraire mapping_deps
  3. Pour chaque dep dans mapping_deps :
     - SOURCE/TARGET → lookup dans index
     - MAPPLET (via MAPPLETNAME) → lookup + résoudre récursivement ses deps internes
       [B4] Si MAPPLETNAME non trouvé dans index mais présent comme enfant du MAPPING
            courant (MAPPLET inline rare) → traiter comme opaque, warning S-W010
     - TRANSFORMATION REUSABLE → lookup
     - WORKLET REUSABLE="YES" → lookup + résoudre récursivement
     - WORKLET REUSABLE="NO" → inclus dans le raw_xml du WORKFLOW, rien à résoudre
  4. Retourner [sources, targets, mapplets, transformations, mapping, session, workflow]
     dans l'ordre DTD
```

**Exclusion mutuelle SESSION** [nouveau] : Si le WORKFLOW contient une SESSION inline ET une SESSION standalone est trouvée dans l'index pour le même mapping, la forme inline est prioritaire. La SESSION standalone n'est incluse dans le XML produit QUE si aucune SESSION inline n'existe dans le WORKFLOW. Warning S-W008 si les deux formes coexistent.

**Scoring transitif des MAPPLETs imbriqués** : Si un MAPPLET référence lui-même d'autres MAPPLETs, la résolution est récursive avec le même DFS. Le `complexity_score` stocké dans `GlobalLibraryEntry` est toujours le score transitif complet (calculé récursivement en Phase A). Limite de profondeur : 10 niveaux. Warning P-W014 si dépassé.

#### Passe 3 : Génération du XML

```python
def generate_workflow_xml(
    wf_name: str,
    folder_name: str,
    resolved: list[IndexEntry],
    index: XmlIndex,              # [B7: corrigé] pour accéder à powermart_attrs etc.
    output_path: Path,
    warnings: WarningCollector
) -> None:
    """
    Écrit le XML unitaire en concaténant les raw_xml des IndexEntry résolus.
    Wrappe dans l'enveloppe POWERMART > REPOSITORY > FOLDER.
    N'utilise pas lxml pour la génération — écriture verbatim bytes.
    """
```

**[B7: corrigé] Structure exacte du XML produit** :

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE POWERMART SYSTEM "powrmart.dtd">
<POWERMART
  CREATION_DATE="{index.powermart_attrs['CREATION_DATE']}"
  REPOSITORY_VERSION="{index.powermart_attrs['REPOSITORY_VERSION']}">
  <REPOSITORY
    NAME="{index.repository_attrs['NAME']}"
    VERSION="{index.repository_attrs['VERSION']}"
    CODEPAGE="{index.repository_attrs.get('CODEPAGE', 'UTF-8')}"
    DATABASETYPE="{index.repository_attrs.get('DATABASETYPE', 'Oracle')}">
    <FOLDER
      {tous les attrs de index.folder_attrs[folder_name] copiés verbatim}>
      <!-- Dans l'ordre DTD strict : -->
      {raw_xml de chaque SOURCE}
      {raw_xml de chaque TARGET}
      {raw_xml de chaque MAPPLET résolu}
      {raw_xml de chaque TRANSFORMATION REUSABLE résolu}
      {raw_xml du MAPPING}
      {raw_xml de la SESSION standalone — seulement si pas de SESSION inline dans WORKFLOW}
      {raw_xml du WORKFLOW}
    </FOLDER>
  </REPOSITORY>
</POWERMART>
```

Les attrs du FOLDER sont copiés depuis `index.folder_attrs[folder_name]` — tous les attributs XML verbatim (`NAME`, `DESCRIPTION`, `OWNER`, `GROUP`, `SHARED`, `PERMISSIONS`, `UUID` si présent, etc.).

### 2.6 Conventions de nommage

```
output/
  split_xml/
    FOLDER_NAME/              ← safe_filename(folder_name)
      WF_NAME.xml             ← safe_filename(workflow_name) + ".xml"
  splitter_warnings.json
```

`safe_filename()` : `re.sub(r"[^A-Za-z0-9_\-]", "_", name).strip("_")`

Le champ `workflow.id` dans le JSON canonique contient le **nom original XML** (attribut NAME), pas le safe_filename. Le Reporter fait le mapping `safe_filename(workflow.id)` pour construire les liens de fichiers.

### 2.7 Cas limite — XML sans WORKFLOW

Si le XML global entier ne contient aucun WORKFLOW après traitement de tous les FOLDER, le Splitter émet le warning S-W000 et termine avec exit code 2 (erreur fatale). Un output de zéro fichiers avec exit code 0 serait trompeur.

### 2.8 Codes de warning Splitter (préfixe S-)

| Code | Gravité | Déclencheur | Comportement |
|---|---|---|---|
| S-W000 | ERROR | Zéro WORKFLOW trouvé dans le XML entier | Exit code 2 |
| S-W001 | WARNING | Element sans NAME attr | Skip + log |
| S-W002 | WARNING | MAPPLET référencé (MAPPLETNAME) non trouvé dans l'index | Skip dep + log |
| S-W003 | WARNING | TRANSFORMATION REUSABLE référencée non trouvée | Skip dep + log |
| S-W004 | WARNING | SESSION introuvable pour un WORKFLOW (aucune des 3 formes) | XML produit sans SESSION |
| S-W005 | WARNING | MAPPING introuvable pour une SESSION | XML produit sans MAPPING |
| S-W006 | WARNING | Cycle de dépendances détecté | Briser le cycle + log |
| S-W007 | WARNING | SOURCE/TARGET non trouvé | Skip + log |
| S-W008 | WARNING | SESSION inline ET standalone coexistent pour le même workflow | Inline prioritaire + log |
| S-W009 | ERROR | Workflow sans MAPPINGNAME résolvable (aucune des 3 formes) | Workflow exclu du output |
| S-W010 | WARNING | MAPPLETNAME pointe un MAPPLET intra-MAPPING (non indexé) | Traité comme opaque + log |
| S-W011 | WARNING | Fichier output déjà existant | Écrasement + log |
| S-W012 | ERROR | Erreur d'écriture fichier | Skip workflow + log |
| S-W013 | INFO | FOLDER sans WORKFLOW | FOLDER ignoré + log |

### 2.9 CLI Splitter

```
python agents/xml_splitter.py [OPTIONS] XML_PATH

Arguments :
  XML_PATH              Chemin vers le XML global PowerCenter

Options :
  --output      DIR     Dossier de sortie (défaut: output/split_xml)
  --folder      NAME    Traiter uniquement ce FOLDER
  --workflow    NAME    Traiter uniquement ce WORKFLOW
  --fail-on-warning     Exit code 1 si des warnings existent

Codes de sortie :
  0   Succès sans warning
  1   Succès avec warnings (si --fail-on-warning)
  2   Erreur fatale (XML illisible, zéro workflow, dossier output non créable)
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
  │   ├── session.py        _parse_session_inline(), _parse_session_task(), _parse_session_standalone()
  │   ├── sources.py        _parse_sources(), _parse_targets()
  │   └── variables.py      _parse_mapping_variables(), _parse_session_variables()
  ├── sql/
  │   ├── extractor.py      extract_all_sql_from_xml()
  │   └── analyzer.py       detect_sql_complexity() via sqlglot
  ├── scoring/
  │   ├── direct.py         score_transformation(), compute_direct_score()
  │   └── transitive.py     build_global_library(), compute_inherited_score()
  ├── ai/
  │   └── enricher.py       enrich_with_ai() — optionnel (--with-ai)
  └── schema/
      └── validator.py      validate_canonical_json()
```

### 3.4 Algorithme en 5 phases

#### Phase A — Construction de la GlobalLibrary

**[B2: corrigé] Source de données** : La GlobalLibrary est construite depuis le **XML global original** (pas les XMLs splittés). Elle est générée via la sous-commande CLI `build-library` qui reçoit le XML global en entrée. Le `batch` mode appelle `build-library` automatiquement en premier si `global_objects.json` est absent.

Raison : construire la library depuis les XMLs splittés causerait des doublons (un même MAPPLET dans N XMLs). Construire depuis le XML global donne un index exhaustif unique.

```python
def build_global_library(xml_global_path: Path) -> dict[str, GlobalLibraryEntry]:
    """
    Lit le XML global en streaming (iterparse).
    Indexe UNIQUEMENT les objets de niveau FOLDER (hors MAPPING) :
      - MAPPLET (enfant direct de FOLDER)
      - TRANSFORMATION REUSABLE="YES" (enfant direct de FOLDER)
      - WORKLET REUSABLE="YES" (enfant direct de FOLDER)
    
    Déduplication : clé = (name, type, folder). Premier rencontré prioritaire.
    Si doublon avec score divergent → logger P-W007.
    
    Le score de chaque objet est calculé récursivement (transitif).
    Limite de profondeur DFS : 10 niveaux. Warning P-W014 si dépassé.
    
    Agrège used_by_workflows lors du batch parsing (Phase B).
    Sérialise le résultat dans global_objects.json.
    """
```

```python
@dataclass
class GlobalLibraryEntry:
    name: str
    type: str                    # "MAPPLET", "TRANSFORMATION", "WORKLET", "STORED_PROCEDURE", "JAVA_TRANSFORMATION"
    folder: str
    is_shared: bool
    complexity_score: int        # score TRANSITIF (inclut les dépendances récursives)
    flag: str                    # LOW/MEDIUM/HIGH/CRITICAL
    patterns_detected: list[str]
    propagates_critical: bool    # True si Java Transformation ou Stored Procedure dedans
    used_by_workflows: list[str] # rempli pendant Phase B (batch)
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
      - mapplet_ref : si TYPE="Mapplet", extraire attribut MAPPLETNAME
    """
```

**[B3: corrigé] SESSION — trois fonctions distinctes** :

```python
def _parse_session_inline(workflow: Element) -> dict | None:
    """
    SESSION enfant direct de WORKFLOW.
    Config via éléments <SESSIONATTRIBUTE NAME="..." VALUE="..."/>.
    MAPPINGNAME : attribut direct SESSION[@MAPPINGNAME].
    """

def _parse_session_task(workflow: Element) -> dict | None:
    """
    TASK TYPE="Session" enfant de WORKFLOW.
    Config via éléments <ATTRIBUTE NAME="..." VALUE="..."/>.
    MAPPINGNAME : ATTRIBUTE[@NAME='Mapping Name']/@VALUE (PAS un attribut direct).
    Variables override : éléments <VALUEPAIR NAME="..." VALUE="..."/>.
    """

def _parse_session_standalone(folder: Element, workflow: Element) -> dict | None:
    """
    SESSION enfant de FOLDER, référencée par le WORKFLOW.
    Config via éléments <ATTRIBUTE NAME="..." VALUE="..."/>.
    MAPPINGNAME : attribut direct SESSION[@MAPPINGNAME].
    Variables override : SESSTRANSFORMATIONINST > ATTRIBUTE[@NAME="$$VAR"].
    """
```

Champs extraits (communs aux trois formes, normalisés) :

```python
{
    "name": str,
    "mapping_name": str,
    "source_connection": str,               # "$Source connection value"
    "target_connection": str,               # "$Target connection value"
    "treat_source_rows_as": str,            # "Data Driven" | "Insert"
    "recovery_strategy": str,
    "parameter_file": str,
    "pre_sql": str,
    "post_sql": str,
    "partition_type": str | None,           # "Hash" | "Round-Robin" | "Key Range" | "Pass-through" | None
    "num_partitions": int | None,           # None = pas de partitionnement (équivalent à 1)
    "bulk_mode": bool,
    "session_variables": list[dict],        # [{"name": "$$VAR", "override": "val"}]
    "per_transformation_overrides": list[dict]  # SESSTRANSFORMATIONINST overrides
}
```

**Variables `$$`** [nouveau] :

```python
def _parse_mapping_variables(mapping: Element) -> list[dict]:
    """
    Lit les éléments MAPPINGVARIABLE dans le MAPPING.
    Retourne : [{"name": "$$BATCH_DATE", "datatype": "string", "default": "SYSDATE-1"}]
    """

def _extract_$$_from_expression(expr: str) -> list[str]:
    """
    Détecte les variables dans les expressions TRANSFORMFIELD.
    Pattern : re.findall(r'\$\$[A-Z_][A-Z0-9_]*', expr)  → mapping variables ($$)
    Pattern : re.findall(r'(?<!\$)\$[A-Z_][A-Z0-9_]*', expr)  → session params ($)
    Les deux sont extraites et distinguées dans le JSON.
    Warning P-W009 si $$VAR référencée sans MAPPINGVARIABLE correspondante.
    """
```

**CONNECTOR — deux dialectes normalisés** :

```python
def _normalize_connector(elem: Element) -> dict:
    """
    Dialect 1 : FROMTRANSFORMATION/TOTRANSFORMATION
    Dialect 2 : FROMINSTANCE/TOINSTANCE avec FROMINSTANCETYPE/TOINSTANCETYPE
    → Sortie normalisée :
    {
        "from_instance": str,
        "from_field": str,
        "from_group": str | None,
        "to_instance": str,
        "to_field": str,          # peut contenir "FIELD[N]" pour Normalizer
        "from_type": str | None,
        "to_type": str | None
    }
    """
```

#### Phase C — Extraction et analyse SQL

Sources SQL à extraire (toutes) :

| Source | Élément | Attribut |
|---|---|---|
| SQL override SQ | `TABLEATTRIBUTE NAME="Sql Query"` | VALUE |
| Source Filter SQ | `TABLEATTRIBUTE NAME="Source Filter"` | VALUE |
| User Defined Join | `TABLEATTRIBUTE NAME="User Defined Join"` | VALUE |
| Filter condition | `TABLEATTRIBUTE NAME="Filter Condition"` | VALUE |
| Lookup condition | `TABLEATTRIBUTE NAME="Lookup Condition"` | VALUE |
| Joiner condition | `TABLEATTRIBUTE NAME="Join Condition"` | VALUE |
| Update Strategy expr | `TABLEATTRIBUTE NAME="Update Strategy Expression"` | VALUE |
| Router group condition | `TABLEATTRIBUTE NAME="OutputN.Group Condition"` | VALUE |
| Expression field | `TRANSFORMFIELD EXPRESSION` attr | — |
| Pre SQL session | `SESSIONATTRIBUTE NAME="Pre SQL"` | VALUE |
| Post SQL session | `SESSIONATTRIBUTE NAME="Post SQL"` | VALUE |

**[B8: corrigé] Fonction de détection SQL via sqlglot** :

```python
def detect_sql_complexity(sql: str, dialect: str = "oracle") -> dict:
    """
    Parse le SQL avec sqlglot et traverse l'AST pour détecter 10 patterns
    (le pattern correlated_subquery est remplacé par l'heuristique outer_ref).
    
    Patterns détectables (tous vérifiés implémentables via sqlglot) :
    
    1. window_function      : exp.Window (ROW_NUMBER, RANK, LAG, LEAD...)
    2. nested_subqueries    : len(list(tree.find_all(exp.Subquery))) > 1
    3. connect_by           : tree.find(exp.Connect) — existe dans sqlglot
    4. pivot                : tree.find(exp.Pivot) — existe dans sqlglot
    5. merge                : isinstance(tree, exp.Merge) — existe dans sqlglot
    6. oracle_hints         : "/*+" in sql (détection textuelle suffisante)
    7. conditional_aggregation : exp.Sum/Count/Avg contenant exp.Case
    8. outer_ref_subquery   : heuristique — sous-requête avec Column.table
                              référençant une table non définie dans son propre FROM
                              (remplace correlated_subquery, moins précis mais implémentable)
    9. distinct_count       : exp.Count avec exp.Distinct
    10. multiple_joins      : len(list(tree.find_all(exp.Join))) >= 3
    11. oracle_functions    : TO_DATE, NVL, DECODE, ROWNUM, SYSDATE, TRUNC, INITCAP
                              détectés via tree.find_all(exp.Anonymous) + name check

    Niveaux :
      CRITICAL : connect_by, merge, outer_ref_subquery
      HIGH     : window_function, multiple_joins, nested_subqueries (count > 1), pivot
      MEDIUM   : oracle_functions, distinct_count, conditional_aggregation, oracle_hints
      LOW      : aucun pattern

    Retourne :
    {
        "available": True,
        "dialect": "oracle",
        "level": "LOW" | "MEDIUM" | "HIGH" | "CRITICAL",
        "patterns": ["window_function:RowNumber", "connect_by_hierarchical"],
        "tables_referenced": ["ORDERS", "PRODUCTS"],
        "spark_sql": str | None
    }
    
    Si sqlglot.parse_one() lève une exception : retourner {"available": False, "level": "UNKNOWN"}
    + warning P-W010. Ne jamais planter.
    """
```

#### Phase D — Scoring

**`complexity_matrix.json`** : chemin par défaut `rag_base/complexity_matrix.json`. Le fichier existe déjà. Clés utilisées par `score_transformation()` :

```json
{
  "transformation_scores": {
    "Source Qualifier": {"base_score": 1, "modifiers": {...}},
    "Java Transformation": {"base_score": 8, "modifiers": {}},
    "_default": {"base_score": 0}   ← pour tout TYPE non listé
  },
  "thresholds": {
    "LOW":      {"max": 4,  "migration_days": "1-2", "auto_conversion": true},
    "MEDIUM":   {"max": 9,  "migration_days": "2-3", "auto_conversion": true},
    "HIGH":     {"max": 14, "migration_days": "3-5", "auto_conversion": false},
    "CRITICAL": {"max": 99, "migration_days": "5+",  "auto_conversion": false}
  },
  "routing_rules": {...}
}
```

`estimated_migration_days` et `auto_conversion` sont lus depuis `thresholds[flag]` — pas de LLM.

**Score direct** (par transformation) :

```python
def score_transformation(t: dict, sql_analysis: dict | None, matrix: dict) -> tuple[int, dict]:
    """
    Score de base depuis matrix["transformation_scores"][t["type"]]["base_score"].
    Si type inconnu → matrix["transformation_scores"]["_default"]["base_score"] (= 0).
    
    Modificateurs additifs :
      +3 si sql_analysis.level == HIGH
      +5 si sql_analysis.level == CRITICAL
      +2 si PORTTYPE RETURN présent dans les ports (unconnected lookup, stored proc)
      +1 si REUSABLE == YES
    """
```

**Score transitif** (héritage GlobalLibrary) :

```python
def compute_inherited_score(transformations: list[dict], library: dict) -> tuple[int, list[dict]]:
    """
    Pour chaque transformation avec mapplet_ref non None :
      - Chercher dans library par nom (clé = name)
      - Ajouter library_entry.complexity_score au score hérité
      - Si library_entry.propagates_critical → forcer flag CRITICAL sur le workflow
    Retourne (inherited_score, inherited_objects_list)
    """
```

**Score final et routing** :

```python
total_score = direct_score + inherited_score
flag = compute_flag(total_score, matrix["thresholds"])

# Forçages
if flags["has_java_transformation"] or any(o["propagates_critical"] for o in inherited_objects):
    flag = "CRITICAL"

# routing_decision — 100% Python, pas de LLM
routing_decision = {
    "target_platform": matrix["routing_rules"][flag]["platform"],
    "auto_conversion_feasibility": matrix["thresholds"][flag]["auto_conversion"],
    "human_intervention_required": not matrix["thresholds"][flag]["auto_conversion"],
    "rationale": _build_rationale(flag, flags, sql_patterns)  # construit depuis les flags
}
```

#### Phase E — Enrichissement IA (optionnel)

Déclenché par `--with-ai`. Envoie un résumé textuel structuré (pas le XML brut) :

```python
def build_ai_prompt(canonical: dict) -> str:
    """Résumé textuel du workflow pour obtenir une description métier en 2-3 phrases."""
```

Résultat dans `ai_enrichment.business_description`. Si `--with-ai` absent : `ai_enrichment.available = False`.

### 3.5 Codes de warning Parser (préfixe P-)

| Code | Déclencheur |
|---|---|
| P-W001 | FOLDER sans MAPPING |
| P-W002 | MAPPING sans TRANSFORMATION |
| P-W003 | SESSION introuvable (aucune des 3 formes) |
| P-W004 | SQL non parseable par sqlglot |
| P-W005 | MAPPLET référencé (MAPPLETNAME) absent de la GlobalLibrary |
| P-W006 | REUSABLE TRANSFORMATION référencée absente de la GlobalLibrary |
| P-W007 | Doublon GlobalLibrary avec scores divergents |
| P-W008 | CONNECTOR référence une TRANSFORMATION inconnue |
| P-W009 | Variable $$ dans expression sans MAPPINGVARIABLE correspondante |
| P-W010 | TABLEATTRIBUTE SQL présent mais sqlglot retourne erreur de parse |
| P-W011 | TRANSFORMFIELD EXPRESSION contient `:LKP.` (unconnected lookup call) |
| P-W012 | Pre SQL ou Post SQL présent mais non parseable |
| P-W013 | WORKLET référencé absent du XML |
| P-W014 | Profondeur DFS > 10 niveaux (dépendances MAPPLETs imbriqués) |

### 3.6 CLI Parser

```
python agents/parser_agent_v2.py SOUS-COMMANDE [OPTIONS]

Sous-commandes :
  parse          Parser un seul XML unitaire (produit par le Splitter)
  batch          Parser tous les XMLs d'un dossier (appelle build-library si absent)
  build-library  Construire global_objects.json depuis le XML GLOBAL original

Options communes :
  --input    PATH    XML global (build-library) ou dossier split_xml/ (parse/batch)
  --output   DIR     Dossier de sortie JSON (défaut: output/canonical_json)
  --with-ai          Activer enrichissement LLM (Phase E)
  --dialect  STR     Dialecte SQL pour sqlglot (défaut: oracle)
  --matrix   PATH    Chemin vers complexity_matrix.json (défaut: rag_base/complexity_matrix.json)
  --verbose          Log détaillé

Codes de sortie : 0 succès / 1 warnings / 2 erreur partielle / 3 erreur fatale / 4 dépendance manquante
```

---

## 4. Composant 3 — Rapport HTML (Option B)

### 4.1 Rôle

Générer un rapport HTML statique multi-niveaux depuis les JSONs canoniques. Zéro framework, zéro CDN, auto-contenu.

### 4.2 Fichier

`poc-ia-migration/agents/html_reporter_v2.py`

### 4.3 Structure des fichiers output

```
output/html_report/
  index.html
  global_library.html
  FOLDER_A/
    index.html
    wf_clients_dim.html
  FOLDER_B/
    index.html
    wf_sales_monthly.html
```

### 4.4 Ordre de génération

```python
def generate_all(input_dir, output_dir, project_name):
    workflows = load_canonical_jsons(input_dir)
    global_objects = load_global_objects(input_dir)  # global_objects.json — [] si absent
    folders = group_by_folder(workflows)

    for folder_name, wf_list in folders.items():
        for wf in wf_list:
            generate_workflow_page(wf, folder_name, output_dir / folder_name)

    for folder_name, wf_list in folders.items():
        generate_folder_index(folder_name, wf_list, global_objects, output_dir / folder_name)

    generate_global_library(global_objects, workflows, output_dir)
    generate_repository_index(folders, global_objects, output_dir)
```

### 4.5 `group_by_folder()` — lecture du JSON V2 et V1

```python
def group_by_folder(workflows: list[dict]) -> dict[str, list[dict]]:
    """
    Compatibilité V1/V2 :
    folder_name = (wf.get("folder") or {}).get("name") or wf.get("folder_name", "DEFAULT")
    """
```

### 4.6 Liens relatifs entre pages

| Page source | Vers | Lien relatif |
|---|---|---|
| `index.html` | `FOLDER_A/index.html` | `FOLDER_A/index.html` |
| `index.html` | `global_library.html` | `global_library.html` |
| `FOLDER_A/index.html` | `index.html` | `../index.html` |
| `FOLDER_A/index.html` | `wf_orders_fact.html` | `wf_orders_fact.html` |
| `FOLDER_A/wf_X.html` | `FOLDER_A/index.html` | `index.html` |
| `FOLDER_A/wf_X.html` | `index.html` | `../index.html` |
| `FOLDER_A/wf_X.html` | `global_library.html#OBJ` | `../global_library.html#OBJ` |

### 4.7 Page 1 — `index.html` (Vue Repository)

1. En-tête : nom repository, date export, date analyse, toggle dark/light
2. Warnings globaux (agrégation de tous `wf["warnings"]`)
3. KPI Cards (4) : Folders / Workflows / Score moyen / Objets globaux
4. Barre de répartition stacked LOW/MEDIUM/HIGH/CRITICAL
5. Donut SVG
6. Tableau folders : Nom | Workflows | Complexité dominante | Score max | Dépendances globales | Statut | Lien
   - Statut : `OK` (vert) / `Revue` (orange) / `Bloqué` (rouge)
   - Tri : Bloqué > Revue > OK, puis score max décroissant
7. Lien vers `global_library.html`

### 4.8 Page 2 — `global_library.html`

1. Breadcrumb : Repository › Bibliothèque globale
2. Alerte CRITICAL si objets CRITICAL présents
3. Tableau : Nom | Type | Score | Niveau | Patterns | Workflows impactés | Impact propagé
4. Matrice d'impact croisé (expandable) : objets × folders
5. État vide si `global_objects.json` absent

### 4.9 Page 3 — `FOLDER/index.html` (Vue Sous-projet)

1. Breadcrumb : Repository › FOLDER
2. Card folder : nom, owner, description
3. KPI Cards (4) : Workflows / Score moyen / Objets locaux / Dépendances globales
4. Mini donut SVG
5. Tableau workflows : Workflow | Score direct | Score hérité | Score total | Niveau | Transformations clés | Connexions | Lien
6. Dépendances vers bibliothèque globale (liens ancrés `../global_library.html#NOM`)
7. Connexions du folder (source/target connexions uniques)
8. Warnings du folder

### 4.10 Page 4 — `FOLDER/wf_NOM.html` (Vue Workflow)

1. Breadcrumb : Repository › FOLDER › wf_nom
2. Warning banner (CRITICAL/HIGH)
3. Card en-tête : score décomposé `Direct Xpts + Hérité Ypts = Total Zpts`
4. **[NOUVEAU]** Connexions Source → Cible (noms connexions + tables physiques)
5. Sources et cibles enrichies (schéma, db, type, nb champs)
6. **[NOUVEAU]** Variables `$$` et fichier de paramètres
7. **[NOUVEAU]** Pre SQL / Post SQL (si présents)
8. Data flow SVG + modal zoom/pan (existant, conservé intact)
9. Tableau transformations + colonne Expression/SQL
10. **[NOUVEAU]** Sections SQL détaillées (SQL Oracle + SQL Spark + patterns)
11. **[NOUVEAU]** Objets hérités avec lien vers global_library
12. Décomposition du score
13. Points de vigilance (requires_human_review)
14. **[NOUVEAU]** Description métier (si --with-ai)
15. Navigation bas : ← Folder / ⌂ Repository / 📚 Bibliothèque

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
| `folder.name` ou `folder_name` | Folder fictif "DEFAULT" |
| `complexity.direct_score` | `total_score`, inherited déduit à 0 |
| `complexity.inherited_score` | 0, section héritage absente |
| `inherited_objects` | Section absente |
| `session.source_connection` | Section connexions absente |
| `session.pre_sql` / `post_sql` | Section absente |
| `warnings` | Section absente |
| `data_flow` vide | Section data flow absente |
| `global_objects.json` absent | `global_library.html` avec état vide |

### 4.13 CLI Reporter

```
python agents/html_reporter_v2.py [OPTIONS]

Options :
  --input       DIR    Dossier canonical JSON (défaut: output/canonical_json)
  --output      DIR    Dossier HTML (défaut: output/html_report)
  --project     STR    Nom du repository
  --export-date STR    Date export (YYYY-MM-DD)
  --no-library         Ne pas générer global_library.html
```

---

## 5. Schéma JSON canonique (contrat Phase 1 → Phase 2)

```json
{
  "meta": {
    "schema_version": "2.1",
    "parsed_at": "2026-07-12T10:30:00Z",
    "parser_version": "2.0.0",
    "warnings": ["P-W010: SQL non parseable dans SQ_ORDERS"]
  },

  "workflow_id": "wf_clients_dim",
  "mapping_id": "m_clients_dim",

  "workflow": {
    "id": "wf_clients_dim",
    "server": "INT_SERVER",
    "is_enabled": true,
    "scheduler_type": "ONDEMAND",
    "description": ""
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
      "config": {},
      "ports": [
        {"name": "IN_ADDR", "datatype": "string", "port_type": "INPUT"},
        {"name": "OUT_ADDR", "datatype": "string", "port_type": "OUTPUT"}
      ]
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
    {
      "from": "SQ_CLIENTS",
      "from_type": "Source Qualifier",
      "to": "EXP_CALC",
      "to_type": "Expression",
      "fields": ["CLIENT_ID"]
    }
  ],

  "execution_order": ["SQ_CLIENTS", "LKP_STATUS", "EXP_CALC", "TGT_DIM_CLIENTS"],

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
    "session_variables": [
      {"name": "$$BATCH_DATE", "override": "2026-07-01"}
    ],
    "per_transformation_overrides": []
  },

  "mapping_variables": [
    {"name": "$$BATCH_DATE", "datatype": "string", "default": "SYSDATE-1"},
    {"name": "$$MAX_ROWS",   "datatype": "decimal", "default": "500000"}
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
      "global_modifiers": {"unconnected_lookup": 1},
      "inherited": {"MLT_CLEAN_ADDRESS": 4}
    }
  },

  "workflow_complexity": {
    "total_score": 10,
    "flag": "HIGH",
    "estimated_migration_days": "3-5",
    "auto_conversion": false,
    "score_breakdown": {}
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
    "requires_human_review": ["SQ_CLIENTS: ROWNUM Oracle — remplacer par ROW_NUMBER() OVER (...)"]
  },

  "routing_decision": {
    "target_platform": "pyspark",
    "auto_conversion_feasibility": "MEDIUM",
    "human_intervention_required": true,
    "rationale": "SQL override avec pattern ROWNUM Oracle détecté. Supervision recommandée."
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
| `utils.py` | Tout | `slim_canonical`, `select_rag_sections`, `apply_function_patches` |
| `phase1_reporter.py` | `_flag_badge()`, `_donut_svg()`, `_data_flow_svg()` | Réutilisés dans v2 |
| `phase1_reporter.py` | `DATA_FLOW_MODAL_JS`, `COMMON_CSS` | Copiés dans html_reporter_v2.py |

### 6.2 Ce qu'on refactorise

| Fichier | Fonction | Changement |
|---|---|---|
| `parser_agent.py` | `parse_xml()` ligne 199 | `find("FOLDER")` → `findall("FOLDER")` |
| `parser_agent.py` | `_parse_workflow()` ligne 171 | → 3 fonctions distinctes SESSION |
| `parser_agent.py` | `_parse_transformations()` ligne 119 | Ajouter `mapplet_ref` |
| `parser_agent.py` | `_detect_lookup_flags()` ligne 378 | Corriger bug `lookup_connected_dynamic` (toujours False) |
| `parser_agent.py` | `compute_complexity()` ligne 442 | Ajouter scoring transitif |

### 6.3 Ce qu'on crée (nouveau)

| Fichier | Rôle |
|---|---|
| `agents/xml_splitter.py` | Splitter complet |
| `agents/parser_agent_v2.py` | Parser refactorisé |
| `agents/html_reporter_v2.py` | Reporter Option B |

### 6.4 Code mort identifié

- `MODEL` hardcodé dans `parser_agent.py` → externaliser
- Chemins hardcodés sur `wf_clients_dim` dans `codegen_agent.py`, `fixer_agent.py`, `qa_agent.py` — hors scope sprint

### 6.5 Bug connu

`_detect_lookup_flags()` ligne 387 : `lookup_connected_dynamic` toujours `False` — à corriger dans v2.

### 6.6 W014 — seuil warning score

Le warning P-W014 est réservé pour la profondeur DFS > 10 niveaux (MAPPLETs imbriqués). Un score élevé n'est plus un warning — CRITICAL à partir de 15 pts selon `complexity_matrix.json`.

---

## 7. Dépendances Python

### Existantes (à conserver)

```
xml.etree.ElementTree  # parsing XML léger (conservé dans le parser)
sqlglot                # analyse SQL (rendre obligatoire, pas optionnel)
subprocess             # appel LLM CLI
```

### À ajouter

```
lxml                   # streaming iterparse pour le Splitter uniquement
```

### À ne PAS ajouter

```
pandas     # déjà absent du parser/reporter
Jinja2     # templates via f-strings Python uniquement
networkx   # scoring transitif calculable sans graphe externe
```

---

## 8. Structure des fichiers output

```
output/
  split_xml/
    splitter_warnings.json
    FOLDER_A/
      wf_clients_dim.xml
      wf_orders_fact.xml
    FOLDER_B/
      wf_sales_monthly.xml

  canonical_json/
    parser_warnings.json
    global_objects.json
    FOLDER_A/
      wf_clients_dim.json
      wf_orders_fact.json
    FOLDER_B/
      wf_sales_monthly.json

  html_report/
    index.html
    global_library.html
    FOLDER_A/
      index.html
      wf_clients_dim.html
      wf_orders_fact.html
    FOLDER_B/
      index.html
      wf_sales_monthly.html

  01_canonical_json/    ← Existant (Phase 1 actuelle) — non modifié
  phase1_report/        ← Existant (reporter actuel) — non modifié
```

---

## 9. Exemple de pipeline CLI complet

```bash
# Étape 1 : Splitter — découper le XML global
python agents/xml_splitter.py export_repository.xml \
  --output output/split_xml

# Étape 2 : Parser — construire la GlobalLibrary depuis le XML global
python agents/parser_agent_v2.py build-library \
  --input export_repository.xml \
  --output output/canonical_json

# Étape 3 : Parser — analyser tous les workflows
python agents/parser_agent_v2.py batch \
  --input output/split_xml \
  --output output/canonical_json

# Étape 4 : Reporter — générer le rapport HTML
python agents/html_reporter_v2.py \
  --input output/canonical_json \
  --output output/html_report \
  --project "REP_PROD" \
  --export-date "2026-07-12"

# Optionnel : avec enrichissement IA (Phase E)
python agents/parser_agent_v2.py batch \
  --input output/split_xml \
  --output output/canonical_json \
  --with-ai
```

---

*Fin de spec — SPEC_PIPELINE_V2.md v2.1*
