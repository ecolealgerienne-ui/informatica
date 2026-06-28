# Plateforme d'Intelligence de Migration Informatica
## Présentation Commerciale

> Public : DSI · Directeur de Transformation · Directeur Technique
> Durée de présentation : 30–45 minutes

---

## Slide 1 — Migrer Informatica PowerCenter : l'IA réduit votre risque de 60 à 80 %

**Votre parc Informatica est une valeur — pas un problème.**
Nous vous aidons à le migrer vers Python, PySpark ou DBT avec une traçabilité complète,
des coûts maîtrisés et sans dépendance à une seule équipe d'experts.

> *Migration = risque métier, coût humain, délais. Nous attaquons les trois à la fois.*

---

## Slide 2 — Le problème : une migration Informatica coûte cher et prend du temps

**Les chiffres du marché :**

| Complexité workflow | Estimation effort manuel | Risque |
|---|---|---|
| Simple (filtres, jointures) | 5 000 – 10 000 € | Faible |
| Moyen (lookups, routeurs) | 10 000 – 25 000 € | Modéré |
| Complexe (SCD2, agrégats) | 25 000 – 50 000 € | Élevé |
| Critique (logique métier dense) | > 50 000 € | Très élevé |

**Un parc de 500 workflows représente un budget migration de 5 à 15 millions d'euros.**

Pourquoi si cher ?
- La logique métier est enfouie dans des XML propriétaires, parfois sans documentation
- Les experts Informatica se raréfient sur le marché
- Les tests de non-régression sont chronophages et souvent incomplets
- La connaissance des workflows est souvent concentrée sur 2 ou 3 personnes

---

## Slide 3 — Pourquoi maintenant ?

**Trois facteurs ont changé en 18 mois :**

**1. Les LLMs de nouvelle génération lisent et génèrent du code de façon fiable**
Les modèles actuels comprennent la sémantique ETL — pas seulement la syntaxe.

**2. Informatica PowerCenter perd du terrain**
Les équipes cherchent à en sortir : coût de licence, obsolescence, recrutement difficile.

**3. Les clouds imposent PySpark, DBT, Airflow — pas Informatica**
Azure, AWS, GCP ont un calendrier. Les migrations attendent, les budgets grossissent.

> La fenêtre pour migrer de façon contrôlée est ouverte.
> Dans 3 à 5 ans, ce sera urgent — et plus coûteux.

---

## Slide 4 — Notre approche : une plateforme, pas un outil

Nous ne proposons pas un transpileur automatique.
Nous proposons une **plateforme d'intelligence de migration** pilotée par des experts.

**Le principe :**
- L'IA prend en charge l'analyse, la génération et la vérification
- L'expert humain supervise, valide et décide des cas complexes
- Le client garde la main sur chaque livrable

**Ce que ça change :**
- L'effort humain se concentre sur les 20 % de cas complexes
- Les 80 % restants sont traités à une vitesse impossible manuellement
- Chaque décision est tracée et justifiable en audit

> Le modèle n'est pas "l'IA à la place de l'humain" —
> c'est "l'IA démultiplie l'expert".

---

## Slide 5 — L'actif central : le Canonical JSON

**Le problème avec les approches concurrentes :**
Elles transpilent directement XML → code cible. Si la cible change (Python → DBT, par exemple), tout est à refaire.

**Notre différenciation :**

```
XML Informatica
      │
      ▼
 CANONICAL JSON  ←── Source de vérité normalisée de la logique métier
      │
      ├──→ Python / pandas
      ├──→ PySpark / Databricks
      ├──→ DBT (SQL transformé)
      ├──→ Airflow (orchestration)
      └──→ Snowflake / Microsoft Fabric
```

**Le Canonical JSON capture :**
- Les sources et cibles de données
- La logique de transformation (règles, filtres, jointures, hiérarchie)
- Les dépendances entre mappings
- Le score de complexité et les objets Informatica non triviaux (SCD2, Normalizer, Router…)

> Le Canonical JSON reste chez vous après la mission.
> C'est votre documentation vivante du parc — même si vous ne migrez pas tout de suite.

---

## Slide 6 — Les 5 agents : pipeline de bout en bout

```
  XML Informatica
        │
        ▼
  ┌───────────┐
  │  PARSER   │  Analyse déterministe + scoring de complexité
  └─────┬─────┘  (sans IA pour le parsing structurel)
        │
        ▼
  CANONICAL JSON  ← actif central, livré en Phase 1
        │
        ├──────────────────────┐
        ▼                      ▼
  ┌───────────┐         ┌────────────┐
  │  CODEGEN  │         │ DOCUMENTER │  Phase 1 : fiche fonctionnelle
  └─────┬─────┘         │  (Phase 1) │  depuis JSON seul
        │               └────────────┘
        ▼
  ┌───────────┐
  │   FIXER   │  Vérification statique + correction IA si nécessaire
  └─────┬─────┘  (max 3 cycles, escalade si échec)
        │
        ▼
  ┌───────────┐
  │    QA     │  Exécution réelle + data diff + rapport HTML
  └───────────┘
```

Chaque agent écrit ses sorties avant de passer la main.
**En cas d'escalade, un expert intervient — le pipeline ne force pas une réponse.**

---

## Slide 7 — Phase 1 : Analyse & Inventaire (sans engagement migration)

**Ce que vous obtenez, sans engager le développement :**

Pour chaque workflow de votre parc :

| Livrable | Contenu |
|---|---|
| Canonical JSON | Représentation normalisée complète de la logique |
| Fiche fonctionnelle | Description métier lisible (FR/EN), diagramme data flow |
| Score de complexité | LOW / MEDIUM / HIGH / CRITICAL + estimation jours |
| Rapport d'inventaire | Vue consolidée du parc : répartition, dépendances, risques |

**Ce que ça vous apporte :**
- Vous comprenez votre parc avant de budgéter la migration
- Vous identifiez les workflows à fort risque et ceux triviaux
- Vous avez une base pour négocier avec vos intégrateurs
- Vous pouvez décider de migrer en priorité, ou pas, par lot

> La Phase 1 se vend seule. Elle a une valeur indépendamment de la suite.

---

## Slide 8 — Phase 2 & 3 : Migration + Recette

**Phase 2 — Migration**

À partir du Canonical JSON (déjà produit en Phase 1) :
- Génération du code Python ou PySpark selon la complexité
- Correction automatique + supervision experte pour les cas escaladés
- Documentation technique inline (docstrings, commentaires métier)
- Rapport de corrections pour chaque workflow

**Phase 3 — Recette**

- Exécution du code généré contre vos données de test
- Data diff vs sortie Informatica de référence
- Rapport HTML interactif : taux de conformité, anomalies, samples
- Livraison avec seuil de validation défini contractuellement

**Ce qui reste à la charge du client :**
- Fourniture des données de test et des golden datasets
- Validation fonctionnelle finale par les équipes métier
- Déploiement sur l'environnement cible

---

## Slide 9 — Résultats du POC : 9 workflows réels analysés

**Campagne de test sur 9 workflows Informatica réels :**

| Agent | Résultat |
|---|---|
| Parser (analyse + scoring) | **100 % de succès** — 9/9 workflows analysés |
| CodeGen (génération code) | **100 % de succès** |
| Fixer (correction) | **Moyenne 1,0 cycle** — aucune boucle de correction |
| Escalade (cas bloquants) | **0 %** sur cette campagne |
| QA PASS (validation data) | **75 %** — `wf_clients_dim` validé end-to-end |

**Ce que les 25 % restants révèlent (honnêtement) :**
- `wf_accounts_scd2` : bug identifié sur la gestion de la clé SCD2 (KeyError connu, en cours)
- `wf_transactions_hist` (CRITICAL, score 21) : non testé sur cette campagne — ces cas nécessitent supervision experte

**Ce que ça démontre :**
Le pipeline est opérationnel pour les cas LOW et MEDIUM (majorité d'un parc typique).
Les cas CRITICAL sont correctement détectés et orientés vers l'expert.

---

## Slide 10 — Comparaison Lakebridge (Databricks Labs)

**Lakebridge** est l'outil open-source de Databricks Labs pour analyser les parcs ETL.

**Résultat sur nos 9 workflows : Lakebridge classe TOUS en LOW.**

Pourquoi ? Lakebridge compte les appels de fonctions SQL.
Il ne reconnaît pas les objets Informatica natifs :

| Objet Informatica | Impact réel | Lakebridge | Notre scoring |
|---|---|---|---|
| SCD2 (Slowly Changing Dimension) | +6 points | ignoré | détecté |
| Router (branchement conditionnel) | +2 points | ignoré | détecté |
| Normalizer (dépivotage) | +3 points | ignoré | détecté |
| Unconnected Lookup | +2 points | ignoré | détecté |

**Conséquence :** Lakebridge sous-estime le risque → budget migration sous-dimensionné.

> À notre connaissance, aucun outil open-source ne score les objets Informatica propriétaires
> avec ce niveau de granularité. C'est un point différenciant de notre Parser.

---

## Slide 11 — Notre grille de scoring : fondée sur des standards industriels

**Nous n'avons pas inventé notre méthode. Nous l'avons construite en croisant 4 références.**

| Source | Échelle | Ce qu'elle apporte |
|---|---|---|
| **Databricks Lakebridge** | LOW / MEDIUM / HIGH / CRITICAL | L'échelle 4 niveaux, devenue standard de facto |
| **TCS · Capgemini · Accenture** | T1 → T4 | Java/Custom = CRITICAL automatique ; SQL simple = trivial |
| **IEEE** *(220 projets industriels)* | Estimation continue | La *nature* de la transformation prime sur le *nombre* |
| **WhereScape** | Binaire | Le concept "automatisable vs intervention experte" |

**Le consensus de ces 4 sources donne 3 principes :**

1. **Un SQL override simple n'est pas de la complexité** — c'est du SQL réutilisable directement.
2. **10 filtres simples ≠ 3 transformations SCD2** — on mesure ce qui résiste à l'automatisation.
3. **Java et Custom Transformation déclenchent CRITICAL** — aucun mapping automatique possible.

> Notre grille suit les mêmes critères que les grandes usines de migration mondiales.
> Elle est documentée, versionnable, et recalibratable si votre parc évolue.

---

## Slide 12 — Ce qui rend vraiment un workflow difficile à migrer

**Deux catégories. Une frontière claire.**

| ✅ Migrable sans friction | ⚠️ Résiste à l'automatisation |
|---|---|
| SQL override simple (WHERE, TO_DATE) | Java Transformation |
| Filtre, tri, DISTINCT | Custom Transformation |
| Fonctions Oracle triviales (ROWNUM, TO_DATE) | Sous-requêtes SQL imbriquées |
| 10 filtres simples | Fonctions analytiques (ROW_NUMBER, LAG, RANK) |
| Fichier de paramètres | SCD Type 2 (historisation) |
| Expression avec 1–8 champs calculés | Lookup non connecté (syntaxe propriétaire) |
| Aggregator simple (GROUP BY standard) | Normalizer (dépivotage) |

**Répartition observée sur un parc typique (benchmark Accenture / TCS) :**

| Niveau | % du parc | Automatisation estimée |
|---|---|---|
| LOW | 30 – 40 % | 85 – 95 % |
| MEDIUM | 30 – 35 % | 65 – 80 % |
| HIGH | 15 – 25 % | 40 – 60 % |
| CRITICAL | **5 – 15 %** | 10 – 30 % |

> La grande majorité d'un parc Informatica est migrable de façon contrôlée.
> Le scoring Phase 1 vous dit exactement où sont vos 5–15 % de cas critiques — avant de dépenser.

---

## Slide 13 — ROI client : simulation sur 100 workflows

**Hypothèse : parc de 100 workflows, répartition typique**

| Segment | Nb | Effort manuel | Avec notre plateforme |
|---|---|---|---|
| LOW (score 0–3) | 40 | 400 k€ | ~60 k€ (supervision légère) |
| MEDIUM (score 4–8) | 35 | 700 k€ | ~175 k€ (revue experte) |
| HIGH (score 9–14) | 20 | 700 k€ | ~350 k€ (co-développement) |
| CRITICAL (≥ 15) | 5 | 300 k€ | ~200 k€ (expert + IA) |
| **Total** | **100** | **~2,1 M€** | **~785 k€** |

**Réduction d'effort estimée : 60 à 70 %** selon la répartition réelle.

> Ces chiffres sont des estimations basées sur les tarifs moyens du marché.
> Un atelier d'inventaire (Phase 1) permet de les affiner avant tout engagement.

---

## Slide 14 — Modèle commercial : 4 phases progressives

**Approche par phases — chaque phase a une valeur standalone**

| Phase | Livrable principal | Engagement |
|---|---|---|
| **1 — Analyse & Inventaire** | Canonical JSON + fiches fonctionnelles + rapport de parc | Sans engagement migration |
| **2 — Migration** | Code Python/PySpark corrigé + documenté | Par lot ou workflow |
| **3 — Recette** | Rapport QA + data diff + validation | Livré avec Phase 2 |
| **4 — Production** | Déploiement + monitoring + itérations | Sur périmètre défini |

**Tarification indicative Phase 1**

| Parc | Nb workflows | Estimation Phase 1 |
|---|---|---|
| Petit | 50 | Sur devis (à partir de ~20 k€) |
| Moyen | 200 | Sur devis |
| Grand | 500+ | Sur devis — engagement cadre |

> La Phase 1 est le point d'entrée naturel. Elle finance sa propre valeur
> et conditionne le budget des phases suivantes.

---

## Slide 15 — Roadmap : ce qui vient après le POC

**Court terme (3–6 mois)**
- Correction du bug SCD2 (KeyError sur clés composites)
- Logs structurés par agent (coût, latence, cycles)
- Passage en batch multi-workflows (traitement en parallèle)

**Moyen terme (6–12 mois)**
- Support PySpark complet (actuellement placeholder)
- RAG vectoriel sur corpus de migrations validées (exemples métier appris)
- Interface de supervision pour les cas escaladés

**Long terme**
- Multi-sources : Talend, SSIS, AbInitio → même Canonical JSON, même pipeline
- Cibles DBT, Airflow, Snowflake, Microsoft Fabric
- Fine-tuning sur modèle open-source (50–200 exemples validés par vos équipes)

> L'actif Canonical JSON est la fondation.
> Chaque cible et chaque source ajoutée s'appuie sur la même analyse.

---

## Slide 16 — Garanties et limites — ce qu'on vous dit clairement

**Ce que nous garantissons :**
- Analyse complète de votre parc via la Phase 1 (inventaire + scoring)
- Code généré syntaxiquement valide et documenté
- Rapport QA livré avec chaque workflow migré
- Traçabilité complète de chaque décision de migration

**Ce que nous ne promettons pas encore :**
- La Phase QA est actuellement testée sur données synthétiques (30 lignes)
  → validation sur vos volumes réels à planifier ensemble
- Les cas CRITICAL (score ≥ 15) nécessitent toujours une supervision experte
  → l'IA accélère, elle ne remplace pas le jugement humain sur ces cas
- Le support PySpark complet est en cours — disponible sur la roadmap 6 mois
- SCD2 complexe (clés composites) : bug identifié, en cours de résolution

**Notre engagement de transparence :**
Nous signalons chaque cas escaladé et chaque limite détectée.
Vous ne découvrez pas un blocage en recette — vous le savez dès la Phase 1.

---

## Slide 17 — Prochaines étapes

**Étape 1 — Appel de qualification (30 min)**

- Taille de votre parc Informatica (nb de workflows, sources, cibles)
- Contexte de migration : calendrier, budget disponible, cible technologique
- Urgences identifiées (arrêt de licence ? Deadline réglementaire ?)

**Étape 2 — Atelier inventaire (demi-journée)**

- Fourniture de 5 à 10 workflows XML représentatifs
- Exécution de la Phase 1 en direct ou en J+5
- Livraison : rapport de parc, fiches fonctionnelles, budget Phase 2 chiffré

**Étape 3 — Décision go/no-go sur la migration**

Vous avez toutes les informations pour décider — avec votre Canonical JSON comme preuve.

---

> **Contact**
>
> Remplissez ce formulaire ou écrivez-nous directement pour planifier l'appel de qualification.
>
> *Ce document est confidentiel. Reproduction interdite sans accord.*

---

*Document généré le 28/06/2026 — Version 1.0*
