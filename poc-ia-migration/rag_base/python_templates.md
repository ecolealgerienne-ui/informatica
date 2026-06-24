# Python ETL Batch Templates & Standards

## Mandatory Batch Structure

Every generated batch script MUST follow this exact structure:

```python
"""
Batch   : <workflow_id> / <mapping_id>
Source  : <owner>.<source_table> (<db_type>)
Target  : <owner>.<target_table> (<db_type>)
Migrated from PowerCenter on: <date>
Complexity: <LOW|MEDIUM|HIGH|CRITICAL> | Platform: Python standalone
"""
import os
import pandas as pd
import numpy as np
from datetime import datetime
from typing import Tuple

BATCH_DATE = os.getenv("BATCH_DATE", "2026-01-01")
SOURCE_FILE = os.getenv("SOURCE_FILE", "tests/golden_dataset.csv")
REF_STATUT_FILE = os.getenv("REF_STATUT_FILE", "tests/ref_statut.csv")
OUTPUT_FILE = os.getenv("OUTPUT_FILE", "output/result.csv")


def extract(source_file: str, batch_date: str) -> pd.DataFrame:
    """Read source data and apply date filter."""
    ...

def lookup_<name>(df: pd.DataFrame, ref_file: str) -> pd.DataFrame:
    """Enrich df with reference table using merge (never a loop)."""
    ...

def transform(df: pd.DataFrame, batch_date: str) -> pd.DataFrame:
    """Apply all expression transformations using vectorised pandas."""
    ...

def filter_<name>(df: pd.DataFrame) -> pd.DataFrame:
    """Apply filter condition."""
    ...

def load(df: pd.DataFrame, output_file: str) -> int:
    """Write output atomically. Returns row count written."""
    ...

def main() -> Tuple[int, int]:
    """Orchestrates extract → lookup → transform → filter → load."""
    ...

if __name__ == "__main__":
    rows_in, rows_out = main()
    print(f"[DONE] {rows_in} rows in → {rows_out} rows out")
```

---

## Pattern: Lookup as Merge (Connected Lookup)

```python
def lookup_statut(df: pd.DataFrame, ref_file: str) -> pd.DataFrame:
    ref = pd.read_csv(ref_file, dtype=str)
    ref = ref.drop_duplicates(subset=["CODE"], keep="first")
    return df.merge(ref, left_on="STATUT_CODE", right_on="CODE", how="left")
```

**Rules:**
- Always `drop_duplicates` on the reference key before merging (implements "Return First Row" policy)
- Always `how='left'` for connected lookups
- Never iterate rows with a loop or `.apply()` for lookups

---

## Pattern: Unconnected Lookup (`:LKP.` syntax)

Informatica syntax: `:LKP.LKP_REF_PAYS(COUNTRY_CODE)` — called as a function inside an Expression.

```python
def _build_unconnected_lkp(ref_file: str, key_col: str) -> dict:
    """Pre-load reference into dict for O(1) vectorised map."""
    ref = pd.read_csv(ref_file, dtype=str)
    ref = ref.drop_duplicates(subset=[key_col], keep="first")
    return ref.set_index(key_col).to_dict(orient="index")


def apply_unconnected_lkp(df: pd.DataFrame, src_col: str, lkp_dict: dict,
                           output_col: str, lkp_field: str, default=None) -> pd.DataFrame:
    """
    Vectorised equivalent of :LKP.LKP_NAME(src_col).lkp_field
    Uses .map() on pre-loaded dict — never apply() row-level.
    """
    df[output_col] = df[src_col].map(
        lambda k: lkp_dict.get(k, {}).get(lkp_field, default)
    )
    # Use vectorised map with a Series instead of lambda when possible:
    mapping = {k: v.get(lkp_field, default) for k, v in lkp_dict.items()}
    df[output_col] = df[src_col].map(mapping).fillna(default if default is not None else np.nan)
    return df
```

**CRITICAL — do NOT generate this:**
```python
# FORBIDDEN: row-level apply for unconnected lookup
df["PAYS_LABEL"] = df["COUNTRY_CODE"].apply(lambda x: get_pays_label(x))
```

**Correct vectorised approach:**
```python
# Pre-load once, map entire column
ref_pays = pd.read_csv(ref_pays_file, dtype=str).drop_duplicates(subset=["COUNTRY_CODE"], keep="first")
pays_label_map   = ref_pays.set_index("COUNTRY_CODE")["PAYS_LABEL"].to_dict()
fiscal_rate_map  = ref_pays.set_index("COUNTRY_CODE")["FISCAL_RATE"].astype(float).to_dict()
zone_geo_map     = ref_pays.set_index("COUNTRY_CODE")["ZONE_GEO"].to_dict()

df["PAYS_LABEL"]  = df["COUNTRY_CODE"].map(pays_label_map)        # NaN if not found
df["FISCAL_RATE"] = df["COUNTRY_CODE"].map(fiscal_rate_map).fillna(0.20)
df["ZONE_GEO"]    = df["COUNTRY_CODE"].map(zone_geo_map)
```

---

## Pattern: String Cleaning (LTRIM/RTRIM/UPPER)

```python
df["NOM_CLEAN"] = df["NOM"].str.strip().str.upper()
df["PRENOM_CLEAN"] = df["PRENOM"].str.strip().str.upper()
df["EMAIL_LOWER"] = df["EMAIL"].str.strip().str.lower()
```

**Rules:**
- Use `.str.strip()` for LTRIM+RTRIM combined
- Chain `.str` methods — no `.apply(lambda)`

---

## Pattern: Age Calculation (DATEDIFF SYSDATE col 'YY')

```python
def _calc_age(birth_series: pd.Series, ref_date: datetime) -> pd.Series:
    age = ref_date.year - birth_series.dt.year
    birthday_passed = (
        (birth_series.dt.month < ref_date.month) |
        ((birth_series.dt.month == ref_date.month) &
         (birth_series.dt.day <= ref_date.day))
    )
    return (age - (~birthday_passed).astype(int)).astype("Int64")
```

**Rules:**
- Fully vectorised — no `.apply()`
- Explicit month/day comparisons — do NOT use tuple comparison with pd.Series (fragile with NaT)
- Returns nullable integer (`Int64`) to handle NaT birth dates gracefully

---

## Pattern: Normalizer / Unpivot (GCID)

Informatica Normalizer with `OCCURS=N` and `GCID` → `pd.melt()`.

```python
def normalizer_unpivot(df: pd.DataFrame,
                        id_cols: list,
                        value_prefix_pairs: list,
                        n_occurrences: int) -> pd.DataFrame:
    """
    Translates Informatica Normalizer (OCCURS=N, GCID) to pd.melt().

    value_prefix_pairs: list of (col_prefix, output_col_name)
        e.g. [("BUDGET_M", "BUDGET_MENSUEL"), ("REALISE_M", "REALISE_MENSUEL")]
    n_occurrences: number of OCCURS (N in Informatica config) = 12 for months

    GCID becomes the positional index (1-based) after melt.
    """
    frames = []
    for i in range(1, n_occurrences + 1):
        suffix = str(i).zfill(2)
        row = df[id_cols].copy()
        row["GCID"] = i
        for prefix, out_name in value_prefix_pairs:
            src_col = f"{prefix}{suffix}"
            row[out_name] = df[src_col].values
        frames.append(row)
    return pd.concat(frames, ignore_index=True)
```

**Example usage (12 months budget):**
```python
budget_long = normalizer_unpivot(
    df=df_source,
    id_cols=["BUDGET_ID", "DEPT_CODE", "CATEGORIE", "ANNEE", "STATUT_BUDGET"],
    value_prefix_pairs=[
        ("BUDGET_M",  "BUDGET_MENSUEL"),
        ("REALISE_M", "REALISE_MENSUEL"),
    ],
    n_occurrences=12,
)
# GCID column = mois number (1..12) — equivalent to Informatica GCID
```

---

## Pattern: DECODE (multi-value mapping)

```python
def _decode_map(series: pd.Series, mapping: dict, default=None) -> pd.Series:
    """Vectorised DECODE: col.map(dict).fillna(default)."""
    return series.map(mapping).fillna(default if default is not None else np.nan)
```

**Example:**
```python
# DECODE(TXN_TYPE, 'WIRE','Virement international', 'SEPA','Virement SEPA', 'Autre')
TXN_TYPE_MAP = {
    "WIRE": "Virement international",
    "SEPA": "Virement SEPA",
    "CARD": "Paiement carte",
    "CASH": "Operation especes",
}
df["TXN_TYPE_LABEL"] = _decode_map(df["TXN_TYPE"], TXN_TYPE_MAP, default="Autre")
```

---

## Pattern: Router (split flux en N dataframes)

Informatica Router → filter the dataframe N times with boolean masks.

```python
def apply_router(df: pd.DataFrame, groups: dict) -> dict:
    """
    Translate Informatica Router into N filtered DataFrames.
    groups = {"GRP_INSERT": mask_insert, "GRP_SCD2": mask_scd2, ...}
    Last group is implicitly the default (all remaining rows).
    """
    result = {}
    covered = pd.Series(False, index=df.index)
    group_items = list(groups.items())
    for name, mask in group_items[:-1]:
        result[name] = df[mask].copy()
        covered = covered | mask
    # Default group: rows not covered by any explicit condition
    default_name = group_items[-1][0]
    result[default_name] = df[~covered].copy()
    return result
```

**Example:**
```python
groups = {
    "GRP_ALERT":  df["ALERT_FLAG"] == "Y",
    "GRP_NORMAL": pd.Series(True, index=df.index),  # default
}
split = apply_router(df, groups)
df_alert  = split["GRP_ALERT"]
df_normal = split["GRP_NORMAL"]
```

---

## Pattern: Sequence Generator (surrogate key)

```python
def generate_surrogate_keys(df: pd.DataFrame, start_val: int, step: int = 1) -> pd.Series:
    """
    Replaces Informatica Sequence Generator.
    WARNING: only safe for single-process batch — not thread-safe.
    For Spark/parallel: use monotonically_increasing_id() or UUID.
    """
    return pd.RangeIndex(start=start_val, stop=start_val + len(df) * step, step=step)
```

---

## Pattern: SCD Type 2 (historisation)

```python
def apply_scd2(df_source: pd.DataFrame, df_dim_current: pd.DataFrame,
               natural_key: str, scd_cols: list, sk_col: str,
               eff_start_col: str, eff_end_col: str, is_current_col: str,
               batch_date: str, sk_start: int) -> tuple:
    """
    Returns (df_insert, df_close):
    - df_insert: new rows to INSERT (new records + changed records new version)
    - df_close:  existing SK to UPDATE (set EFF_END_DATE = batch_date - 1)
    """
    EOT = pd.Timestamp("9999-12-31")
    batch_dt = pd.Timestamp(batch_date)
    close_dt = batch_dt - pd.Timedelta(days=1)

    # Merge source with current dimension
    merged = df_source.merge(
        df_dim_current[[natural_key, sk_col, eff_start_col] + scd_cols],
        on=natural_key, how="left", suffixes=("", "_curr")
    )

    # Detect new records (no match in dim)
    mask_new = merged[sk_col].isna()

    # Detect changed records (at least one SCD col differs)
    mask_changed = ~mask_new
    for col in scd_cols:
        mask_changed = mask_changed & (merged[col] != merged[f"{col}_curr"])

    df_new     = merged[mask_new].copy()
    df_changed = merged[mask_changed].copy()

    df_insert = pd.concat([df_new, df_changed], ignore_index=True)
    df_insert[sk_col]         = range(sk_start, sk_start + len(df_insert))
    df_insert[eff_start_col]  = batch_dt
    df_insert[eff_end_col]    = EOT
    df_insert[is_current_col] = "Y"

    df_close = df_changed[[sk_col]].rename(columns={sk_col: sk_col})
    df_close[eff_end_col] = close_dt

    return df_insert, df_close
```

---

## Pattern: Null-Safe Comparisons

Informatica and pandas handle NULL differently. Always apply these rules:

```python
# WRONG: NULL != 'VALUE' returns NULL in SQL → rows silently dropped
df_filtered = df[df["STATUS"] != "I"]

# CORRECT: explicit null handling before comparison
df_filtered = df[df["STATUS"].fillna("").ne("I")]

# WRONG: joining on nullable keys can drop rows silently
merged = df.merge(ref, on="CODE")

# CORRECT: normalize keys before join (strip + upper + fillna)
df["CODE_KEY"]  = df["CODE"].str.strip().str.upper().fillna("__NULL__")
ref["CODE_KEY"] = ref["CODE"].str.strip().str.upper().fillna("__NULL__")
merged = df.merge(ref, on="CODE_KEY", how="left")
```

**Rule: always `.fillna()` on join keys and filter columns before any comparison.**

---

## Pattern: Case-Insensitive Lookup

When Informatica XML has `Case Sensitive Lookup = NO`:

```python
# Normalize both sides to lowercase before merge
df["_join_key"] = df["COUNTRY_CODE"].str.strip().str.lower()
ref["_join_key"] = ref["COUNTRY_CODE"].str.strip().str.lower()
merged = df.merge(ref, on="_join_key", how="left")
merged = merged.drop(columns=["_join_key"])
```

**Parser detection:** if `TABLEATTRIBUTE NAME="Case Sensitive"` is `NO` in the XML → apply normalization.

---

## Pattern: Sorted Input (Aggregator / Joiner)

When Informatica XML has `Sorted Input = YES` on Aggregator or Joiner:

```python
# WRONG: groupby without sort — correct results but unpredictable order
result = df.groupby(["REGION", "MOIS"]).agg(...)

# CORRECT: explicit sort before groupby when Sorted Input = YES
df = df.sort_values(["REGION", "MOIS"], kind="mergesort")
result = df.groupby(["REGION", "MOIS"], sort=False).agg(...)
# sort=False after explicit sort is a performance optimization
```

**Parser detection:** `TABLEATTRIBUTE NAME="Sorted Input" VALUE="YES"` → add `sort_values()` before aggregation.

---

## Idempotent Load

```python
def load(df: pd.DataFrame, output_file: str) -> int:
    tmp_file = output_file + ".tmp"
    df.to_csv(tmp_file, index=False)
    os.replace(tmp_file, output_file)  # atomic swap
    return len(df)
```

**Rules:**
- Write to `.tmp` first, then `os.replace()` for atomicity
- In DB context: write to staging table, then TRUNCATE + INSERT into target (single transaction)

---

## Pattern: Audit Logging

```python
def main() -> Tuple[int, int]:
    start = datetime.now()
    print(f"[START] {datetime.now().isoformat()} batch_date={BATCH_DATE}")
    df_src = extract(SOURCE_FILE, BATCH_DATE)
    rows_in = len(df_src)
    df = lookup_statut(df_src, REF_STATUT_FILE)
    df = transform(df, BATCH_DATE)
    df = filter_active(df)
    rows_out = load(df, OUTPUT_FILE)
    elapsed = (datetime.now() - start).total_seconds()
    print(f"[END] rows_in={rows_in} rows_out={rows_out} elapsed={elapsed:.1f}s")
    return rows_in, rows_out
```

---

## Forbidden Patterns

| Forbidden | Use instead |
|---|---|
| `df.apply(lambda row: ..., axis=1)` | Vectorised pandas operations |
| `for _, row in df.iterrows()` | `.merge()`, `.str`, `.dt`, `np.where()` |
| `df.apply(lambda x: lookup_dict[x])` | `.map()` on pre-loaded dict or `.merge()` |
| `datetime.strptime()` in a loop | `pd.to_datetime()` on the whole column |
| `:LKP.` translated as `apply(lambda)` | Pre-load dict, use `.map()` on full column |
| Tuple comparison `(month, day) < (month, day)` with pd.Series | Explicit boolean month/day comparisons |
| `groupby()` without `sort_values()` when Sorted Input = YES | `sort_values()` before `groupby(sort=False)` |
| Join on nullable key without `.fillna()` | Normalize keys with `.str.strip().str.upper().fillna()` |
