# Python ETL Batch Templates & Standards

## Mandatory Batch Structure

Every generated batch script MUST follow this exact structure:

```python
"""
Batch   : <workflow_id> / <mapping_id>
Source  : <owner>.<source_table> (<db_type>)
Target  : <owner>.<target_table> (<db_type>)
Migrated from PowerCenter on: <date>
Complexity: <LOW|MEDIUM|HIGH> | Platform: Python standalone
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

## Pattern: Lookup as Merge

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

## Pattern: Idempotent Load

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
| `df.apply(lambda x: lookup_dict[x])` | `.merge()` or `.map()` on pre-loaded ref |
| `datetime.strptime()` in a loop | `pd.to_datetime()` on the whole column |
