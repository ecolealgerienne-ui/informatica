# Documentation Métier — Workflows Informatica PowerCenter
## Migration vers Python / Databricks — POC IA

**Document à destination de l'équipe fonctionnelle**
**Date** : Juin 2026 | **Périmètre** : 9 workflows de la campagne POC

---

## Introduction

Ce document décrit, en langage fonctionnel, ce que fait chacun des workflows Informatica PowerCenter
inclus dans le périmètre du POC de migration. Pour chaque workflow, vous trouverez :

- **Ce que le workflow fait** (en une phrase)
- **D'où viennent les données** (sources)
- **Ce qui est calculé ou transformé**
- **Où vont les données** (cible)
- **Les règles métier clés** appliquées

> Les termes techniques Informatica (Source Qualifier, Expression, Lookup, etc.) sont traduits
> en langage fonctionnel. Aucune connaissance technique n'est requise pour lire ce document.

---

## Synthèse — Vue d'ensemble des 9 workflows

| # | Workflow | Domaine métier | Ce que ça fait |
|---|---|---|---|
| 1 | `wf_clients_dim` | Clients | Référentiel clients enrichi et nettoyé |
| 2 | `wf_products_dim` | Produits | Référentiel produits avec calcul de marge |
| 3 | `wf_accounts_scd2` | Comptes | Historique des changements sur les comptes (SCD2) |
| 4 | `wf_orders_fact` | Commandes | Table de faits commandes agrégée par mois |
| 5 | `wf_sales_monthly` | Ventes | Classement mensuel des ventes par famille/région |
| 6 | `wf_transactions_hist` | Transactions financières | Historique transactionnel avec scoring risque |
| 7 | `wf_unconnected_lkp` | RH / Employés | Référentiel employés avec enrichissement pays et grade |
| 8 | `wf_xml_normalizer` | Budget | Pivot budget annuel → lignes mensuelles |
| 9 | `wf_smoke_test` | Test technique | Vérification du bon fonctionnement de la chaîne |

---

## 1. `wf_clients_dim` — Dimension Clients

### En une phrase
Extrait les clients modifiés depuis la dernière date de traitement, enrichit chaque client
avec son libellé de statut, nettoie les données et charge la table de référence clients du Data Warehouse.

### Sources de données

| Source | Système | Description |
|---|---|---|
| `CLIENTS` | Oracle PROD | Table principale clients (nom, prénom, date naissance, email, segment, pays, statut) |
| `REF_STATUT` | Oracle PROD | Table de référence des statuts (code → libellé court et long) |

### Ce qui est calculé / transformé

| Champ produit | Règle métier |
|---|---|
| `NOM_CLEAN` | Nom en majuscules, espaces en début/fin supprimés |
| `PRENOM_CLEAN` | Prénom en majuscules, espaces supprimés |
| `AGE` | Âge calculé à la date de traitement (différence entre aujourd'hui et `DATE_NAISSANCE`) |
| `EMAIL_LOWER` | Email converti en minuscules, espaces supprimés |
| `STATUT_LIBELLE` | Libellé long du statut (ex. "Actif", "Inactif") récupéré depuis `REF_STATUT` via le `STATUT_CODE` |
| `BATCH_DATE` | Date de traitement du batch |
| `DW_LOAD_DATE` | Date et heure de chargement dans le Data Warehouse |

### Filtre appliqué
- **Extraction incrémentale** : seuls les clients dont la `DATE_MAJ` est >= à la date de traitement (`$$BATCH_DATE`) sont extraits (max 1 000 000 lignes).
- **Exclusion des inactifs** : les clients avec `STATUT_CODE = 'I'` (Inactif) sont **exclus** de la cible.

### Cible
**`DIM_CLIENTS`** — Table dimension du Data Warehouse, 12 colonnes, clé primaire `CLIENT_ID`.

### Points d'attention fonctionnels
- Un client inactif (`STATUT_CODE = 'I'`) n'apparaît **jamais** dans `DIM_CLIENTS`.
- Si un client a un code statut inconnu (absent de `REF_STATUT`), `STATUT_LIBELLE` sera vide.
- L'âge est recalculé à chaque run — il évolue donc d'un mois à l'autre.

---

## 2. `wf_products_dim` — Dimension Produits

### En une phrase
Extrait les produits actifs créés depuis la date de traitement, normalise les catégories
via un référentiel, calcule la marge et exclut les produits dont la catégorie est inconnue.

### Sources de données

| Source | Système | Description |
|---|---|---|
| `PRODUCTS` | Oracle PROD | Table produits (code, nom, catégorie, sous-catégorie, prix, coût, fournisseur, statut) |
| `REF_CATEGORY` | Oracle PROD | Référentiel des catégories (code normalisé + libellés FR/EN) |

### Ce qui est calculé / transformé

| Champ produit | Règle métier |
|---|---|
| `PRODUCT_CODE_CLEAN` | Code produit en majuscules, espaces supprimés |
| `PRODUCT_NAME_CLEAN` | Nom avec initiale en majuscule (format titre) |
| `SUBCATEGORY_CLEAN` | Sous-catégorie en majuscules |
| `CATEGORY_CODE` | Code catégorie normalisé (depuis `REF_CATEGORY`) |
| `CATEGORY_LABEL_FR` | Libellé catégorie en français |
| `MARGIN_PCT` | Taux de marge = `(Prix - Coût) / Prix × 100`. Si prix = 0, marge = 0. |
| `MARGIN_BAND` | Tranche de marge : `HIGH` (≥50%), `MEDIUM` (≥20%), `LOW` (<20%) |
| `SUPPLIER_CODE_CLEAN` | Code fournisseur en majuscules |

### Filtres appliqués
- **Exclusion des produits supprimés** : `STATUS_CODE != 'D'` (D = Deleted)
- **Extraction incrémentale** : produits créés depuis `$$BATCH_DATE`
- **Exclusion des catégories inconnues** : produits dont `CATEGORY_CODE` est null ou vaut `'UNKN'` sont **exclus**

### Cible
**`DIM_PRODUCTS`** — 15 colonnes, clé primaire `PRODUCT_ID`.

### Points d'attention fonctionnels
- Un produit dont la catégorie n'est pas référencée dans `REF_CATEGORY` **n'est pas chargé**.
- La marge est stockée en pourcentage (ex. `45.23` pour 45,23%).

---

## 3. `wf_accounts_scd2` — Dimension Comptes avec Historique (SCD Type 2)

### En une phrase
Gère l'historique complet des modifications sur les comptes : chaque fois qu'un attribut
surveillé change, l'ancienne version est archivée avec sa date de fin de validité et
une nouvelle version active est créée.

### Ce qu'est le SCD Type 2 (explication fonctionnelle)
> En Data Warehouse, une "Slowly Changing Dimension de Type 2" (SCD2) signifie qu'on ne
> **remplace pas** les données quand elles changent — on **conserve l'historique complet**.
> Chaque version d'un compte a une date de début de validité (`EFF_START_DATE`),
> une date de fin (`EFF_END_DATE`) et un indicateur "version courante" (`IS_CURRENT = 'Y'`).

### Sources de données

| Source | Système | Description |
|---|---|---|
| `ACCOUNTS` | Oracle PROD / CRM | Table des comptes (informations commerciales, risque, crédit) |
| `DIM_ACCOUNTS` | Data Warehouse | Dimension comptes existante — pour comparer avec la version courante |

### Attributs surveillés pour détecter un changement

Le workflow compare la version source avec la version courante en DWH sur ces champs :
- Nom du compte (`ACCOUNT_NAME`)
- Classe de risque (`RISK_CLASS`)
- Limite de crédit (`CREDIT_LIMIT`)
- Pays (`COUNTRY_CODE`)
- Code gestionnaire (`MANAGER_CODE`)

### Logique de décision (3 cas)

| Situation | Action |
|---|---|
| Compte **nouveau** (absent du DWH) | **INSERT** — nouvelle ligne avec `IS_CURRENT = 'Y'`, `EFF_START_DATE = BATCH_DATE`, `EFF_END_DATE = 31/12/9999` |
| Compte **modifié** (au moins un attribut a changé) | **CLOSE** l'ancienne version (`EFF_END_DATE = BATCH_DATE - 1 jour`) + **INSERT** la nouvelle version |
| Compte **inchangé** | **Rien** — aucune écriture |

### Ce qui est calculé / transformé

| Champ produit | Règle métier |
|---|---|
| `ACCOUNT_NAME_CLEAN` | Nom compte en majuscules |
| `CITY_CLEAN` | Ville avec initiale en majuscule (format titre) |
| `RISK_LABEL` | Libellé classe de risque : A=Risque Faible, B=Risque Moyen, C=Risque Élevé, D=Risque Critique |
| `EFF_START_DATE` | Date de début de validité de cette version |
| `EFF_END_DATE` | Date de fin (31/12/9999 = version courante, sinon date réelle de clôture) |
| `IS_CURRENT` | 'Y' si version active, 'N' sinon |
| `ACCOUNT_SK` | Clé technique (surrogate key) — auto-incrémentée, indépendante de l'ID métier |

### Cible
**`DIM_ACCOUNTS`** — 2 opérations possibles par run : INSERT (nouvelles versions) + UPDATE (clôture anciennes versions).

### Points d'attention fonctionnels
- Pour retrouver **la situation d'un compte à une date donnée**, filtrer sur `EFF_START_DATE <= date <= EFF_END_DATE`.
- La clé primaire de la dimension est `ACCOUNT_SK` (technique), pas `ACCOUNT_ID` (métier).
- Un même compte peut avoir **plusieurs lignes** dans la table — une par version.

---

## 4. `wf_orders_fact` — Faits Commandes (Agrégation Mensuelle)

### En une phrase
Joint les en-têtes de commandes avec leurs lignes de détail, enrichit avec le segment
client, calcule les montants nets et produit une agrégation mensuelle par client, canal
et pays.

### Sources de données

| Source | Système | Description |
|---|---|---|
| `ORDERS` | Oracle PROD | En-têtes commandes (date, canal, pays, remise, statut) |
| `ORDER_LINES` | Oracle PROD | Lignes de commande (quantité, montant, code promo) |
| `DIM_CLIENTS` | Data Warehouse | Pour enrichissement avec segment et pays client |

### Filtres appliqués
- **Commandes depuis BATCH_DATE** uniquement
- **Exclusion des statuts** `CANCELLED` et `DRAFT`
- Les lignes de commande sont automatiquement filtrées via leur `ORDER_ID` parent

### Ce qui est calculé / transformé

| Champ | Règle métier |
|---|---|
| `GROSS_AMOUNT` | Montant brut = Quantité × Prix unitaire |
| `DISCOUNT_AMOUNT` | Remise = Montant brut × % remise / 100 |
| `NET_AMOUNT` | Montant net = Brut − Remise |
| `ORDER_YEAR` / `ORDER_MONTH` | Année et mois extraits de la date de commande |
| `HAS_PROMO` | 1 si un code promo a été utilisé, 0 sinon |
| `CHANNEL_CLEAN` | Canal de vente normalisé en majuscules |

### Agrégation mensuelle (par client + segment + canal + pays + année + mois)

| Mesure | Description |
|---|---|
| `ORDER_COUNT` | Nombre de commandes |
| `TOTAL_GROSS` | Total montant brut |
| `TOTAL_DISCOUNT` | Total remises accordées |
| `TOTAL_NET` | Total montant net encaissé |
| `TOTAL_QTY` | Total quantités commandées |
| `PROMO_ORDERS` | Nombre de commandes avec promo |

### Cible
**`FACT_ORDERS_MONTHLY`** — 1 ligne par combinaison (client, segment, canal, pays, année, mois).

### Points d'attention fonctionnels
- Ce n'est pas une table de commandes individuelles — c'est un **cube mensuel**.
- Pour retrouver une commande précise, il faut remonter aux sources (ORDERS).

---

## 5. `wf_sales_monthly` — Classement Mensuel des Ventes par Famille Produit

### En une phrase
Fusionne les ventes online et offline du mois, calcule les totaux par famille produit
et région géographique, puis conserve uniquement le **Top 10** des familles par région.

### Sources de données

| Source | Système | Description |
|---|---|---|
| `SALES_ONLINE` | Oracle PROD | Ventes réalisées via le canal digital |
| `SALES_OFFLINE` | Oracle PROD | Ventes réalisées en magasins physiques |

Filtre commun : seules les ventes **du mois de la date de traitement** (`$$BATCH_DATE`) sont incluses.

### Fusion des deux canaux
Online et Offline sont **empilés** (UNION) — les deux sources ont la même structure, elles
sont simplement concaténées en une seule liste de ventes.

### Ce qui est calculé / transformé

| Champ | Règle métier |
|---|---|
| `FAMILY_LABEL` | Libellé complet de la famille produit (ex. ELEC → "Electronique", FOOD → "Alimentation") |
| `FAMILY_SEGMENT` | Segment commercial (ex. ELEC → "HIGH_TECH", FOOD → "FMCG") |
| `REGION_LABEL` | Libellé de la région (ex. EU → "Europe", AP → "Asie-Pacifique") |
| `SALE_YEAR` / `SALE_MONTH` | Année et mois de la vente |

### Agrégation (par famille, région, canal, année, mois)

| Mesure | Description |
|---|---|
| `SALE_COUNT` | Nombre de ventes |
| `TOTAL_AMOUNT` | Chiffre d'affaires total |
| `MAX_AMOUNT` | Vente maximum |
| `MIN_AMOUNT` | Vente minimum |
| `AVG_AMOUNT` | Panier moyen |

### Classement final
Après agrégation, seules les **10 meilleures familles produit** par région (classées par
chiffre d'affaires décroissant) sont conservées. Le rang (`REGION_RANK`) est ajouté.

### Cible
**`SALES_MONTHLY_RANKING`** — Tableau de bord mensuel des top familles produit par région.

### Points d'attention fonctionnels
- Ce rapport est **mensuel** — chaque run remplace ou complète le mois traité.
- Le Top 10 est calculé **séparément par région** : la famille #1 en Europe n'est pas
  forcément la même qu'en Asie-Pacifique.

---

## 6. `wf_transactions_hist` — Historique Transactions Financières avec Scoring Risque

### En une phrase
Extrait les transactions financières récentes, les enrichit avec l'historique de scoring
risque sur 90 jours, calcule un score de risque composite, déduplique sur la référence
externe et charge l'historique transactionnel dans le Data Warehouse.

### Sources de données

| Source | Système | Description |
|---|---|---|
| `TRANSACTIONS` | Oracle PROD | Transactions du jour avec statut COMPLETED ou PENDING_REVIEW, montant EUR renseigné |
| `SCORING_HIST` | Oracle PROD | Historique agrégé sur 90 jours par compte (moyenne montant, max, nombre de transactions, nombre de pays différents, score précédent) |
| `REF_BLACKLIST_COUNTRIES` | Oracle PROD | Référentiel des pays sous sanctions ou surveillance |

**Filtre d'extraction** : transactions depuis `$$BATCH_DATE`, limitées à `$$MAX_ROWS` lignes,
triées par date puis par ID.

### Ce qui est calculé / transformé

| Champ | Règle métier |
|---|---|
| `TXN_TYPE_LABEL` | Libellé du type de transaction (WIRE = "Virement international", SEPA = "Virement SEPA", CARD = "Paiement carte", CASH = "Opération espèces", CHQE = "Chèque") |
| Score risque composite | Calculé sur plusieurs critères : montant vs historique, fréquence des transactions, pays d'origine/destination (sanctions), historique du score précédent |
| Indicateurs pays blacklist | Vérification si le pays d'origine **ET** le pays de destination sont sous sanctions |

### Déduplication
Les transactions ayant la **même référence externe** (`REF_EXTERNE`) sont dédupliquées :
seule la transaction la plus récente est conservée pour chaque référence.

### Cible
**Historique transactionnel DWH** — avec score de risque, enrichissement type, indicateurs
pays blacklist, et date de chargement.

### Points d'attention fonctionnels
- Les transactions `CANCELLED` ou sans montant EUR ne sont **pas chargées**.
- Une transaction peut être `PENDING_REVIEW` — elle est chargée mais doit être surveillée.
- Le score de risque est **relatif à l'historique 90 jours** — un gros virement d'un compte
  habituellement calme sera plus risqué qu'un même virement d'un compte actif.
- La déduplication garantit qu'une même opération (même `REF_EXTERNE`) n'apparaît
  qu'**une seule fois** dans l'historique.

---

## 7. `wf_unconnected_lkp` — Référentiel Employés avec Enrichissement Pays et Grade

### En une phrase
Extrait les employés actifs, enrichit chaque employé avec son libellé pays, sa zone
géographique, son taux fiscal, son libellé de grade et son coefficient d'ancienneté,
puis calcule la rémunération ajustée.

### Sources de données

| Source | Système | Description |
|---|---|---|
| `EMPLOYES` | Oracle PROD | Table RH — employés actifs recrutés avant ou à la date de traitement |
| `REF_PAYS` | Oracle PROD | Référentiel pays (libellé, zone géographique, taux fiscal) |
| `REF_GRADE_COEFF` | Oracle PROD | Référentiel des grades (libellé, coefficient ancienneté, taux bonus) |

> **Note technique** : Les tables REF_PAYS et REF_GRADE_COEFF sont appelées comme des
> **fonctions dans les expressions** (lookup non-connecté, syntaxe `:LKP.`) — c'est un
> pattern Informatica avancé. En Python, c'est traduit en jointures conditionnelles.

### Ce qui est calculé / transformé

| Champ | Règle métier |
|---|---|
| `NOM_CLEAN` | Nom en majuscules |
| `PRENOM_CLEAN` | Prénom avec initiale en majuscule |
| `PAYS_LABEL` | Libellé complet du pays (depuis `REF_PAYS`) |
| `ZONE_GEO` | Zone géographique (Europe, Amériques, Asie-Pacifique...) |
| `FISCAL_RATE` | Taux fiscal applicable (défaut 0,20 si pays inconnu) |
| `GRADE_LABEL` | Libellé du grade RH |
| `COEFF_ANCIENNETE` | Coefficient multiplicateur selon le grade (défaut 1,0 si grade inconnu) |
| Rémunération ajustée | Calculée à partir du salaire brut × coefficient ancienneté |

### Filtre appliqué
- Seuls les employés avec `STATUT_EMP = 'ACTIF'` et `DATE_EMBAUCHE <= $$BATCH_DATE` sont extraits.

### Points d'attention fonctionnels
- Si un pays n'est pas dans `REF_PAYS`, le taux fiscal prend la valeur par défaut `0,20` (20%).
- Si un grade n'est pas dans `REF_GRADE_COEFF`, le coefficient prend la valeur `1,0` (neutre).
- Le workflow est conçu pour une exécution **incrémentale** (nouveaux employés uniquement par run).

---

## 8. `wf_xml_normalizer` — Pivot Budget Annuel vers Lignes Mensuelles

### En une phrase
Transforme une table budget dans laquelle chaque ligne représente une année avec 12
colonnes budget et 12 colonnes réalisé, en une table où chaque ligne représente
**un seul mois** avec ses montants budget et réalisé.

### Problème fonctionnel résolu
La table source `BUDGET_ANNUEL` est structurée en **colonnes pivotées** :
```
DEPT | ANNEE | BUDGET_M01 | BUDGET_M02 | ... | BUDGET_M12 | REALISE_M01 | ... | REALISE_M12
```
Le Data Warehouse a besoin d'une structure **normalisée** (1 ligne = 1 mois) :
```
DEPT | ANNEE | MOIS | BUDGET_MENSUEL | REALISE_MENSUEL
```
Le workflow effectue cette transformation de structure (appelée "dépivotage" ou "normalisation").

### Source de données

| Source | Système | Description |
|---|---|---|
| `BUDGET_ANNUEL` | Oracle PROD | Budget annuel par département, avec colonnes BUDGET_M01..M12 et REALISE_M01..M12 |

**Filtre** : uniquement l'année correspondant à `$$BATCH_DATE`.

### Ce qui est calculé / transformé

Après dépivotage (12 lignes générées par ligne source) :

| Champ | Règle métier |
|---|---|
| `MOIS_DATE` | Date du 1er jour du mois correspondant (ex. mois 3 → 01/03/ANNEE) |
| `ECART_MONTANT` | Réalisé − Budget (positif = dépassement, négatif = économie) |
| `TAUX_EXECUTION` | Réalisé / Budget × 100 (si budget > 0, sinon null) |
| `STATUT_BUDGET` | Statut du budget (transmis tel quel depuis la source) |

### Cible
**`BUDGET_MENSUEL`** — 1 ligne par (département, catégorie, année, mois), avec les
montants budget et réalisé, l'écart et le taux d'exécution.

### Points d'attention fonctionnels
- Un budget annuel en source génère **12 lignes** en cible (une par mois).
- Le taux d'exécution est `null` si le budget est à zéro (pas de division par zéro).
- Ce workflow est typiquement lancé **une fois par an** (ou en début d'année pour initialiser).

---

## 9. `wf_smoke_test` — Test Fonctionnel de la Chaîne

### En une phrase
Workflow de validation technique minimal : vérifie que la chaîne ETL complète
(extraction, transformation, chargement) fonctionne de bout en bout sans erreur.

### Rôle fonctionnel
Ce workflow ne porte **pas de logique métier significative**. Son rôle est de valider
que l'environnement est opérationnel avant de lancer les vrais workflows.

Il est typiquement exécuté :
- Après une montée de version du pipeline
- Lors de la mise en production sur un nouvel environnement
- Pour valider la connectivité et les droits d'accès

---

## Glossaire des termes techniques

| Terme technique | Signification fonctionnelle |
|---|---|
| **Source Qualifier** | "Porte d'entrée" des données — extrait et filtre les enregistrements depuis la source |
| **Lookup** | Recherche d'une valeur dans une table de référence (équivalent d'une jointure de décodage) |
| **Expression** | Calcul ou transformation sur les données (formules, concatenation, conditions) |
| **Filter** | Règle d'exclusion — les enregistrements ne répondant pas à la condition sont éliminés |
| **Joiner** | Jointure entre deux flux de données (équivalent d'un SQL JOIN) |
| **Aggregator** | Calcul de totaux, moyennes, comptages — regroupement de données |
| **Union** | Fusion de deux flux de même structure (équivalent SQL UNION ALL) |
| **Router** | Aiguillage conditionnel — envoie chaque enregistrement dans un "tuyau" différent selon sa valeur |
| **Rank** | Classement des enregistrements selon un critère, avec conservation du Top N |
| **Normalizer** | Dépivotage de colonnes en lignes (transformation de structure) |
| **Update Strategy** | Décision de mise à jour : Insérer / Mettre à jour / Supprimer |
| **Sequence Generator** | Générateur de clés techniques auto-incrémentées |
| **SCD Type 2** | Historisation des changements dans une dimension : chaque modification crée une nouvelle version |
| **$$BATCH_DATE** | Date de traitement du batch — paramètre passé au lancement du workflow |
| **$$MAX_ROWS** | Limite de lignes extraites — paramètre de sécurité |
| **DW_LOAD_DATE** | Date/heure de chargement dans le Data Warehouse — traçabilité technique |
| **Surrogate Key (SK)** | Clé technique interne au Data Warehouse, distincte de l'identifiant métier |

---

## Ce que la migration change (et ce qui reste identique)

| Aspect | Informatica PowerCenter | Python / Databricks (après migration) |
|---|---|---|
| **Logique métier** | Identique | Identique — aucune règle ne change |
| **Données source** | Oracle (`SRC_ORACLE_PROD`) | Connexion configurable (Oracle, fichier, S3...) |
| **Données cible** | Oracle Data Warehouse | Databricks Delta Lake / tables relationnelles |
| **Paramètre BATCH_DATE** | Fichier `.prm` ou Control-M | Variable d'environnement (`BATCH_DATE`) |
| **Planification** | Control-M → Informatica | Control-M → Python **ou** Databricks Workflows |
| **Lisibilité du code** | Diagrammes graphiques | Code Python commenté + documentation générée |
| **Auditabilité** | Logs Informatica | JSON canonique + rapport QA HTML |

---

*Document généré dans le cadre du POC Migration Informatica → Python/Databricks*
*Branche git : `claude/blissful-tesla-7mvgbf` — Repo : `ecolealgerienne-ui/informatica`*
