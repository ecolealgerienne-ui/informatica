# Guide : Structure du Repository Informatica PowerCenter
## Périmètre d'analyse pour une migration exhaustive

> Document de référence — issu de recherches sur la documentation officielle Informatica (docs.informatica.com v10.5)  
> et des pratiques des outils du marché (Lakebridge, AWS SCT, Informatica Migration Factory)  
> Date : Juin 2026

---

## 1. Architecture du repository PowerCenter

Un repository PowerCenter **n'est pas un dossier de fichiers**. C'est une **base de données relationnelle** (Oracle, SQL Server, DB2) gérée par le Repository Service.

```
Repository PowerCenter
├── Base de données (OPB_* tables — stockage brut)
│   ├── REP_* views — vues de lecture sécurisées
│   └── Metadata : objets, versions, dépendances, historique d'exécution
│
├── Exports XML (via pmrep ou Designer)
│   └── Format : powrmart.dtd — seul format d'échange standard
│
└── Filesystem Integration Service
    ├── Fichiers de paramètres (.par / .prm / .txt)
    └── Logs de session ($PMSessionLogDir)
```

**Conséquence directe** : les XMLs de workflows que nous parsons ne représentent qu'une partie de ce qui existe dans un projet PowerCenter réel.

---

## 2. Taxonomie complète des objets exportables

Source officielle : [`pmrep objectexport`](https://docs.informatica.com/data-integration/powercenter/10-4-1/command-reference/pmrep-command-reference/objectexport.html)

| Type d'objet | Description | Couvert par notre parser |
|---|---|---|
| **Source** | Définition table/vue/fichier source | ✅ Partiellement (si dans même XML) |
| **Target** | Définition table/vue/fichier cible | ✅ Partiellement (si dans même XML) |
| **Mapping** | Logique ETL complète (transformations + connecteurs) | ✅ Oui |
| **Transformation** *(reusable)* | Transformation stockée hors mapping, réutilisée dans N mappings | ❌ Non |
| **Mapplet** | Groupe de transformations réutilisables (sous-mapping) | ❌ Non |
| **Session** | Configuration d'exécution d'un mapping | ✅ Partiellement |
| **Workflow** | Orchestration des sessions et tâches | ✅ Oui |
| **Worklet** *(reusable)* | Groupe de tâches réutilisables sans scheduling | ⚠️ Seulement si non-reusable (embedded) |
| **UDF** (User-Defined Function) | Fonctions custom réutilisables dans les expressions | ❌ Non |
| **Shortcut** | Pointeur vers objets partagés d'un autre dossier | ❌ Non (référence visible, contenu non) |
| **Connection Object** | Métadonnées de connexion (sans credentials) | ⚠️ Exportable avec `-m` flag |

### Distinction Worklet vs Workflow

| | Workflow | Worklet |
|---|---|---|
| Scheduling | ✅ Oui | ❌ Non |
| Exécution autonome | ✅ Oui | ❌ Doit être embedded dans un workflow |
| Réutilisable dans N workflows | Rarement | ✅ Oui, par définition |
| Dans nos XMLs actuels | ✅ Oui | ⚠️ Seulement si non-reusable |

---

## 3. Ce qui est dans un XML — et ce qui n'y est pas

### Contenu d'un export XML (`powrmart.dtd`)

```xml
<POWERMART>
  <REPOSITORY NAME="REP_PROD">
    <FOLDER NAME="MIGRATION_POC">
      <SOURCE .../>           <!-- définitions sources -->
      <TARGET .../>           <!-- définitions cibles -->
      <MAPPING NAME="...">
        <TRANSFORMATION ...>  <!-- logique ETL -->
          <TRANSFORMFIELD .../> <!-- ports et types -->
          <TABLEATTRIBUTE .../>  <!-- SQL, conditions, config -->
        </TRANSFORMATION>
        <CONNECTOR .../>      <!-- data flow entre transformations -->
      </MAPPING>
      <WORKFLOW ...>
        <SESSION .../>        <!-- config exécution -->
        <WORKLET .../>        <!-- tâches embedded -->
      </WORKFLOW>
    </FOLDER>
  </REPOSITORY>
</POWERMART>
```

**Ce que le XML contient :**
- Logique des mappings (transformations, ports, expressions, conditions)
- Définitions sources/targets (structure, types de données)
- Config sessions (paramètres, noms de connexions — **sans credentials**)
- Ordre d'exécution des tâches dans les workflows
- Références aux objets réutilisables (mais pas leur contenu si exportés séparément)

### Ce qui n'est PAS dans le XML

| Élément manquant | Où c'est stocké | Impact sur l'analyse |
|---|---|---|
| **Credentials** des connexions | Tables `OPB_*` chiffrées | Faible — à re-configurer de toute façon |
| **Schedules** (planifications) | `REP_WORKFLOWS` en base | Moyen — orchestration cible |
| **Historique d'exécution** | `REP_WFLOW_RUN` en base | **Élevé** — détecte le code mort |
| **Logs de session** | Fichiers binaires `$PMSessionLogDir` | **Élevé** — fréquence, durée, erreurs réelles |
| **Statistiques de performance** | `REP_SESS_LOG` en base | Moyen — sizing plateforme cible |
| **Historique des versions** | Base repository | Faible |
| **Valeurs des paramètres runtime** | Fichiers `.par` sur le filesystem | **Élevé** — config DEV/UAT/PROD |

---

## 4. Fichiers de paramètres (`.par`)

**Source officielle** : [Parameter File Structure](https://docs.informatica.com/data-integration/powercenter/10-5/advanced-workflow-guide/parameter-files/parameter-file-structure.html)

### Format

```ini
[Service:IntSvs_PROD]
$$ENV=PROD

[DOSSIER_ETL.wf_clients_dim]
$$BATCH_DATE=2024-01-01
$$SRC_CONNECTION=ORACLE_PROD_CRM

[Session:s_clients_dim]
$$TARGET_SCHEMA=DWH_PROD
$$REJECT_LIMIT=100
```

### Caractéristiques

- Extensions valides : `.par`, `.prm`, `.txt`
- Stockage : **filesystem** du serveur Integration Service, NON en base de données
- Path configurable via variable `$PMParamDir`
- Priorité d'application : CLI `pmcmd` > propriétés workflow > propriétés session
- Variables mapping préfixées `$$`, variables workflow sans préfixe
- Un seul fichier peut couvrir N workflows et N sessions

### Ce que ça contient de précieux pour la migration

- Valeurs réelles des connexions par environnement (DEV/UAT/PROD)
- Paramètres de batch (`$$BATCH_DATE`, `$$CUTOFF_DATE`)
- Seuils de rejet et de contrôle
- Chemins de fichiers plats
- Tout ce qui varie entre les environnements

---

## 5. Vues REP_* utiles pour l'analyse (accès base de données)

Si accès direct à la base repository disponible (lecture seule sur vues REP_*) :

| Vue | Contenu | Utilité migration |
|---|---|---|
| `REP_WORKFLOWS` | Définitions et planifications des workflows | Inventaire exhaustif + scheduling |
| `REP_WFLOW_RUN` | Historique d'exécution (date, durée, statut) | **Détection code mort** |
| `REP_SESS_LOG` | Logs de session (lignes lues/rejetées/écrites) | Volumétrie réelle |
| `REP_TBL_MAPPING` | Relations table ↔ mapping | Lineage source-to-target |
| `REP_SRC_MAPPING` | Relations source ↔ mapping | Inventaire sources réelles |
| `REP_SESSIONS` | Configuration des sessions réutilisables | Config d'exécution |

> ⚠️ Ne jamais faire d'INSERT/UPDATE sur les tables `OPB_*` — risque de corruption du repository.
> Lecture seule uniquement sur les vues `REP_*`.

---

## 6. Ce que font les outils du marché

| Outil | Inputs réels | Ce qu'il analyse | Ce qu'il ne voit pas |
|---|---|---|---|
| **Databricks Lakebridge** | XMLs PowerCenter (mappings, sessions, workflows) | Logique ETL, expressions SQL, transformations | Mapplets séparés, UDFs, historique d'exécution |
| **AWS SCT** | XMLs PowerCenter | SQL embarqué, noms d'objets DB | Logique de transformation complexe |
| **Informatica Migration Factory** | Accès direct base repository | Tout — objets, dépendances, historique | Rien (accès complet) |
| **WhereScape** | Pattern matching sur exports | Structure des dépendances | Logique métier dense |
| **Notre parser (Phase 1)** | XMLs workflows fournis par le client | Mappings, transformations, scoring, data flow | Voir tableau §2 |

**Conclusion** : aucun outil basé sur les XMLs seuls ne voit tout. L'accès base est nécessaire pour l'historique d'exécution. C'est une limite partagée par Lakebridge et AWS SCT.

---

## 7. Checklist de collecte pour une Phase 1 exhaustive

### Niveau 1 — Minimum viable (ce que nous faisons aujourd'hui)

```bash
# Export des workflows via pmrep
pmrep objectexport -o workflow -f <FOLDER> -b -r -u workflows_export.xml
```

- [x] XMLs de workflows (avec `-b` pour les dépendants non-réutilisables, `-r` pour les réutilisables)

### Niveau 2 — Recommandé (angle mort majeur corrigé)

```bash
# Mapplets (sous-mappings réutilisables)
pmrep objectexport -o mapplet -f <FOLDER> -u mapplets_export.xml

# Transformations réutilisables
pmrep objectexport -o transformation -f <FOLDER> -u transformations_export.xml

# Worklets réutilisables
pmrep objectexport -o worklet -f <FOLDER> -u worklets_export.xml
```

- [ ] XMLs mapplets
- [ ] XMLs transformations réutilisables
- [ ] XMLs worklets réutilisables
- [ ] Fichiers `.par` de chaque environnement (DEV, UAT, PROD)
- [ ] `pmrep listobjects -f <FOLDER>` → inventaire complet avant export

### Niveau 3 — Analyse avancée (accès base ou logs)

- [ ] Export `REP_WFLOW_RUN` sur 6–12 mois → détection du code mort
- [ ] Export `REP_SESS_LOG` → volumétrie réelle par workflow
- [ ] Liste des connexions (noms, types de bases) → inventaire infrastructure
- [ ] Scripts shell `pmcmd` → dépendances d'orchestration inter-workflows

---

## 8. Impact sur notre Canonical JSON

### Ce qu'on devrait ajouter au modèle canonique

```json
{
  "workflow_id": "wf_clients_dim",
  "mapplets_used": ["mpl_clean_address", "mpl_validate_email"],
  "reusable_transformations": ["rtr_error_handler"],
  "udfs_used": ["fn_format_date", "fn_clean_name"],
  "parameter_file": {
    "path": "$PMParamDir/prod.par",
    "variables": {
      "$$BATCH_DATE": "runtime",
      "$$SRC_CONNECTION": "ORACLE_PROD"
    }
  },
  "execution_stats": {
    "last_run": "2024-12-15",
    "avg_duration_min": 42,
    "runs_last_6m": 180,
    "error_rate_pct": 0.5
  }
}
```

### Priorité d'implémentation

| Ajout | Effort | Valeur complexité | Valeur migration | Priorité |
|---|---|---|---|---|
| Parse mapplets XML | Moyen | **Critique** | Haute | 🔴 P1 |
| Parse transformations réutilisables | Moyen | **Critique** | Haute | 🔴 P1 |
| Parse worklets réutilisables | Moyen | Haute | Moyenne | 🔴 P1 |
| Listobjects → inventaire complet | Faible | Faible | Moyenne | 🟡 P2 |
| Connexion via `REP_WFLOW_RUN` | Élevé | Faible | Haute | 🟡 P2 |
| Parse fichiers `.par` | Faible | **Nulle** | Faible | 🟢 P3 |
| Parse logs de session | Élevé | Nulle | Moyenne | 🟢 P3 |

> **Note sur les fichiers `.par`** : ils ne contribuent pas à la complexité de migration.
> Ce sont des variables de configuration runtime (`$$BATCH_DATE`, `$$CONNECTION`) — l'équivalent
> Python est `os.getenv()` ou un fichier de config. Déplacer les valeurs vers un `.env` ou
> un gestionnaire de secrets est trivial. Pas de scoring, pas d'analyse sémantique nécessaire.

---

## 9. Architecture : scoring transitif par les objets globaux

### Principe

Les objets globaux (mapplets, transformations réutilisables, worklets) sont des **fonctions partagées** appelées par N workflows. Leur complexité doit **remonter vers chaque workflow qui les utilise**.

Sans cette résolution, un workflow peut être classé LOW alors qu'il délègue sa logique critique à un mapplet CRITICAL — et planter silencieusement en Phase 2.

### Problème concret

```
mpl_clean_address (Mapplet — objet global)
├── Expression (3 champs calculés)       → score 0
├── Java Transformation                  → score 8, flag CRITICAL automatique
└── Lookup non connecté                  → score 3

wf_clients_dim (Workflow)
└── Mapplet : mpl_clean_address
└── Filter simple
└── Expression (2 champs)

Scoring SANS résolution : LOW (score 2)   ← FAUX, sous-estime massivement le risque
Scoring AVEC résolution : CRITICAL (13)   ← JUSTE, héritage de la Java Transformation
```

### Architecture en deux temps

```
┌─────────────────────────────────────────────────────────┐
│  ÉTAPE 0 — Bibliothèque d'objets globaux                │
│                                                         │
│  input/mapplets/          → parse + score               │
│  input/transformations/   → parse + score               │
│  input/worklets/          → parse + score               │
│            │                                            │
│            ▼                                            │
│     global_library.json                                 │
│     {                                                   │
│       "mpl_clean_address": {                            │
│         "score": 8,                                     │
│         "flag": "CRITICAL",                             │
│         "transformations": [...],                       │
│         "auto_critical": true                           │
│       },                                                │
│       "mpl_validate_email": { "score": 1, ... }         │
│     }                                                   │
└─────────────────────────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────┐
│  ÉTAPE 1 — Analyse des workflows (enrichie)             │
│                                                         │
│  Pour chaque workflow XML :                             │
│    1. Parser les transformations directes               │
│    2. Détecter les références à des objets globaux      │
│    3. Résoudre via global_library.json                  │
│    4. Injecter les transformations du mapplet           │
│    5. Additionner les scores (direct + hérité)          │
│            │                                            │
│            ▼                                            │
│     Canonical JSON enrichi                              │
│     {                                                   │
│       "workflow_complexity": {                          │
│         "direct_score": 2,                              │
│         "inherited_score": 11,                          │
│         "total_score": 13,                              │
│         "flag": "CRITICAL"                              │
│       },                                                │
│       "global_objects_used": [                          │
│         {                                               │
│           "name": "mpl_clean_address",                  │
│           "type": "Mapplet",                            │
│           "inherited_score": 11,                        │
│           "flag": "CRITICAL"                            │
│         }                                               │
│       ]                                                 │
│     }                                                   │
└─────────────────────────────────────────────────────────┘
```

### Règles de scoring transitif

| Cas | Comportement |
|---|---|
| Workflow référence un mapplet LOW | Score du mapplet ajouté au total |
| Workflow référence un mapplet CRITICAL | Workflow devient CRITICAL automatiquement |
| Mapplet contient une Java Transformation | Héritage du flag CRITICAL automatique — indépendamment des scores |
| Plusieurs mapplets référencés | Scores cumulés — le plus restrictif détermine le flag final |
| Mapplet non trouvé dans la bibliothèque | Warning dans le rapport — score estimé MEDIUM par défaut |

### Impact sur la décomposition du score (rapport Phase 1)

Le score breakdown dans le rapport HTML doit distinguer :

```
Score de complexité : 13 — CRITICAL

  Transformations directes
  ├── Filter simple              0
  ├── Expression (2 champs)      0
  └── Sous-total direct          2

  Objets globaux utilisés
  ├── mpl_clean_address (Mapplet)
  │   ├── Expression (3 champs)  0
  │   ├── Java Transformation    8  ← CRITICAL automatique
  │   └── Lookup non connecté    3
  └── Sous-total hérité         11

  Modificateurs globaux          0
  ──────────────────────────────
  TOTAL                         13  CRITICAL
```

### Structure d'input recommandée

```
input/
  workflows/           ← XMLs de workflows (requis)
    wf_clients_dim.xml
    wf_orders_fact.xml
  mapplets/            ← XMLs de mapplets (optionnel — enrichit le scoring)
    all_mapplets.xml   ← export pmrep objectexport -o mapplet -f FOLDER
  transformations/     ← XMLs de transformations réutilisables (optionnel)
    all_reusable.xml   ← export pmrep objectexport -o transformation -f FOLDER
  worklets/            ← XMLs de worklets réutilisables (optionnel)
    all_worklets.xml   ← export pmrep objectexport -o worklet -f FOLDER
```

Règle de dégradation gracieuse : si `mapplets/` est absent ou vide, le parser fonctionne exactement comme aujourd'hui — les références à des mapplets sont signalées comme non-résolues dans le rapport sans bloquer l'analyse.

### Commandes pmrep pour le client

```bash
# À lancer sur le serveur Informatica (fournir ce script au client)
pmrep connect -r REP_PROD -d DOMAIN -u admin -x ****

pmrep objectexport -o mapplet        -f MON_FOLDER -u input/mapplets/all_mapplets.xml
pmrep objectexport -o transformation -f MON_FOLDER -u input/transformations/all_reusable.xml
pmrep objectexport -o worklet        -f MON_FOLDER -u input/worklets/all_worklets.xml

pmrep disconnect
```

---

## 11. Ce qui reste hors scope — et pourquoi

**Credentials** : intentionnellement absents des exports XML pour des raisons de sécurité. À re-configurer dans la plateforme cible. Non pertinent pour la migration de la logique.

**Historique des versions** : PowerCenter versionne ses objets en base. L'export XML ne donne que la version courante. Pour la migration, c'est la version courante qui compte.

**Logs binaires** : les fichiers de log `$PMSessionLogDir` sont binaires et volumineux. L'information utile (volumétrie, durée) est accessible via les vues `REP_SESS_LOG` si accès base disponible.

---

## Références

| Source | URL | Type |
|---|---|---|
| Informatica Repository Guide 10.5 | https://docs.informatica.com/data-integration/powercenter/10-5/repository-guide/ | Documentation officielle |
| pmrep objectexport | https://docs.informatica.com/data-integration/powercenter/10-4-1/command-reference/pmrep-command-reference/objectexport.html | Documentation officielle |
| Parameter File Structure | https://docs.informatica.com/data-integration/powercenter/10-5/advanced-workflow-guide/parameter-files/parameter-file-structure.html | Documentation officielle |
| REP_WFLOW_RUN view | https://docs.informatica.com/data-integration/powercenter/10-4-0/repository-guide/mx-views-reference/workflow--worklet--and-task-views/ | Documentation officielle |
| Lakebridge Informatica Migration | https://medium.com/@senthilkumarr.ma/lakebridge-modernizing-datastage-informatica-etl-to-databricks-6e03d97da6a0 | Technique |
| AWS SCT — Converting Informatica ETL | https://docs.aws.amazon.com/SchemaConversionTool/latest/userguide/CHAP-converting-informatica.html | Documentation officielle |
| Informatica Migration Factory | https://apptad.com/blogs/comprehensive-migration-guide-from-informatica-powercenter-to-informatica-intelligent-data-management-cloud-idmc/ | Pratique |
