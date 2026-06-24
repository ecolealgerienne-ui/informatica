"""
etl_utils.py — Bibliothèque commune ETL Migration Informatica → Python
=======================================================================
Fonctions réutilisables pour tous les workflows générés par le pipeline
de migration automatique. Remplace les transformations Informatica standards.

Usage : from etl_utils import _decode_map, _safe_lookup, apply_router, ...
"""

import os
import pandas as pd
import numpy as np
from datetime import datetime, date
from typing import Any, Dict, List, Optional, Tuple, Union


# ---------------------------------------------------------------------------
# DECODE — équivalent vectorisé de DECODE() Informatica
# ---------------------------------------------------------------------------

def _decode_map(
    series: pd.Series,
    mapping: Dict[Any, Any],
    default: Any = None,
) -> pd.Series:
    """
    Vectorised DECODE(col, val1, res1, val2, res2, ..., default).

    Informatica:  DECODE(STATUS, 'A', 'Actif', 'I', 'Inactif', 'Inconnu')
    Python:       _decode_map(df['STATUS'], {'A': 'Actif', 'I': 'Inactif'}, 'Inconnu')
    """
    return series.map(mapping).fillna(default if default is not None else np.nan)


def _decode_select(
    conditions: List[pd.Series],
    values: List[Any],
    default: Any = None,
) -> pd.Series:
    """
    Vectorised nested IIF using np.select.

    Informatica:  IIF(score >= 80, 'CRITIQUE', IIF(score >= 50, 'ELEVE', 'FAIBLE'))
    Python:       _decode_select([score >= 80, score >= 50], ['CRITIQUE', 'ELEVE'], 'FAIBLE')
    """
    return pd.Series(np.select(conditions, values, default=default))


# ---------------------------------------------------------------------------
# SAFE LOOKUP — left join avec null-safety et case normalization
# ---------------------------------------------------------------------------

def _safe_lookup(
    df_main: pd.DataFrame,
    df_ref: pd.DataFrame,
    join_keys: Union[str, List[str]],
    output_cols: Optional[List[str]] = None,
    default: Any = None,
    case_insensitive: bool = False,
    left_suffix: str = "",
    right_suffix: str = "_ref",
) -> pd.DataFrame:
    """
    Null-safe connected lookup (Informatica Lookup Procedure — connected).

    Implements:
    - 'Return First Row' policy (drop_duplicates on ref key)
    - Null-safe join key normalization
    - Optional case-insensitive matching

    Args:
        df_main:          Main DataFrame
        df_ref:           Reference DataFrame
        join_keys:        Column(s) to join on (same name in both frames)
        output_cols:      Columns from df_ref to keep (None = all)
        default:          Fill value for unmatched rows (None → NaN)
        case_insensitive: If True, normalize keys to lowercase before join
        left_suffix:      Suffix for conflicting columns from df_main
        right_suffix:     Suffix for conflicting columns from df_ref

    Returns:
        df_main enriched with reference columns
    """
    if isinstance(join_keys, str):
        join_keys = [join_keys]

    ref = df_ref.copy()
    if output_cols:
        ref = ref[join_keys + [c for c in output_cols if c not in join_keys]]

    # Return First Row policy
    ref = ref.drop_duplicates(subset=join_keys, keep="first")

    # Null-safe + optional case normalization of join keys
    tmp_keys = [f"__jk_{k}__" for k in join_keys]
    for k, tk in zip(join_keys, tmp_keys):
        if case_insensitive:
            df_main[tk] = df_main[k].astype(str).str.strip().str.lower().replace("nan", "__NULL__")
            ref[tk]     = ref[k].astype(str).str.strip().str.lower().replace("nan", "__NULL__")
        else:
            df_main[tk] = df_main[k].astype(str).str.strip().fillna("__NULL__")
            ref[tk]     = ref[k].astype(str).str.strip().fillna("__NULL__")

    merged = df_main.merge(
        ref.drop(columns=join_keys),
        on=tmp_keys,
        how="left",
        suffixes=(left_suffix, right_suffix),
    )
    merged = merged.drop(columns=tmp_keys)

    # Apply default to unmatched ref columns
    if default is not None and output_cols:
        for col in output_cols:
            if col in merged.columns:
                merged[col] = merged[col].fillna(default)

    return merged


# ---------------------------------------------------------------------------
# UNCONNECTED LOOKUP — :LKP.NAME(arg) pattern
# ---------------------------------------------------------------------------

def build_unconnected_lkp(
    ref_file: str,
    key_col: str,
    value_cols: Optional[List[str]] = None,
    case_insensitive: bool = False,
) -> Dict[str, Dict[str, Any]]:
    """
    Pre-loads a reference CSV into a nested dict for unconnected lookup.

    Informatica:  :LKP.LKP_REF_PAYS(COUNTRY_CODE)
    Usage:
        lkp_pays = build_unconnected_lkp('ref_pays.csv', 'COUNTRY_CODE')
        df['PAYS_LABEL']  = df['COUNTRY_CODE'].map({k: v['PAYS_LABEL']  for k, v in lkp_pays.items()})
        df['FISCAL_RATE'] = df['COUNTRY_CODE'].map({k: v['FISCAL_RATE'] for k, v in lkp_pays.items()})
    """
    ref = pd.read_csv(ref_file, dtype=str)
    if value_cols:
        ref = ref[[key_col] + value_cols]
    ref = ref.drop_duplicates(subset=[key_col], keep="first")

    if case_insensitive:
        ref[key_col] = ref[key_col].str.strip().str.lower()

    return ref.set_index(key_col).to_dict(orient="index")


def apply_unconnected_lkp(
    series: pd.Series,
    lkp_dict: Dict[str, Dict[str, Any]],
    field: str,
    default: Any = None,
    case_insensitive: bool = False,
) -> pd.Series:
    """
    Apply one field from an unconnected lookup dict to a Series.
    Vectorised — never uses apply(lambda).

    Informatica:  :LKP.LKP_REF_PAYS(COUNTRY_CODE).FISCAL_RATE
    Python:       apply_unconnected_lkp(df['COUNTRY_CODE'], lkp_pays, 'FISCAL_RATE', default=0.20)
    """
    mapping = {k: v.get(field, default) for k, v in lkp_dict.items()}
    src = series.str.strip().str.lower() if case_insensitive else series
    return src.map(mapping).fillna(default if default is not None else np.nan)


# ---------------------------------------------------------------------------
# ROUTER — split DataFrame en N groupes
# ---------------------------------------------------------------------------

def apply_router(
    df: pd.DataFrame,
    groups: Dict[str, pd.Series],
) -> Dict[str, pd.DataFrame]:
    """
    Translates Informatica Router into N filtered DataFrames.

    The last group in the dict is the DEFAULT group (catches all remaining rows).
    Earlier groups are evaluated in order; a row matched by group N is NOT
    included in subsequent groups (mutually exclusive, like Informatica Router).

    Args:
        df:     Input DataFrame
        groups: OrderedDict of {group_name: boolean_mask}
                Last entry = default group

    Returns:
        Dict of {group_name: filtered_DataFrame}

    Example:
        groups = {
            "GRP_INSERT": df["SCD_ACTION"] == "INSERT",
            "GRP_SCD2":   df["SCD_ACTION"] == "SCD2",
            "GRP_DEFAULT": pd.Series(True, index=df.index),  # last = default
        }
        split = apply_router(df, groups)
        df_insert  = split["GRP_INSERT"]
        df_scd2    = split["GRP_SCD2"]
        df_nochange = split["GRP_DEFAULT"]
    """
    result: Dict[str, pd.DataFrame] = {}
    covered = pd.Series(False, index=df.index)

    items = list(groups.items())
    for name, mask in items[:-1]:
        # Only rows not yet covered by a previous group
        effective_mask = mask & ~covered
        result[name] = df[effective_mask].copy()
        covered = covered | effective_mask

    # Default group: all remaining rows
    default_name = items[-1][0]
    result[default_name] = df[~covered].copy()

    return result


# ---------------------------------------------------------------------------
# SEQUENCE GENERATOR — clé surrogate
# ---------------------------------------------------------------------------

def generate_surrogate_keys(
    n_rows: int,
    start_val: int = 1,
    step: int = 1,
) -> pd.RangeIndex:
    """
    Replaces Informatica Sequence Generator for surrogate key generation.

    WARNING: single-process only. Not safe for parallel/Spark execution.
    For Spark: use monotonically_increasing_id() or uuid.uuid4().

    Args:
        n_rows:    Number of rows needing a key
        start_val: Starting value (mirrors 'Current Value' in Informatica config)
        step:      Increment (mirrors 'Increment By')

    Returns:
        RangeIndex to assign as surrogate key column

    Example:
        df["ACCOUNT_SK"] = generate_surrogate_keys(len(df), start_val=50001)
    """
    return pd.RangeIndex(start=start_val, stop=start_val + n_rows * step, step=step)


# ---------------------------------------------------------------------------
# NORMALIZER — unpivot (OCCURS=N, GCID)
# ---------------------------------------------------------------------------

def normalizer_unpivot(
    df: pd.DataFrame,
    id_cols: List[str],
    value_prefix_pairs: List[Tuple[str, str]],
    n_occurrences: int,
    zero_pad: int = 2,
    gcid_col: str = "GCID",
) -> pd.DataFrame:
    """
    Translates Informatica Normalizer (OCCURS=N) to long format.

    GCID in Informatica = positional index 1..N → becomes a column.

    Args:
        df:                  Source DataFrame (wide format)
        id_cols:             Columns that don't repeat (stable IDs)
        value_prefix_pairs:  [(col_prefix, output_name), ...]
                             e.g. [("BUDGET_M", "BUDGET_MENSUEL"), ("REALISE_M", "REALISE_MENSUEL")]
        n_occurrences:       OCCURS value (N) — e.g. 12 for months
        zero_pad:            Zero-pad width for suffix (2 → M01, M02, ...)
        gcid_col:            Name of the GCID output column

    Returns:
        Long-format DataFrame with one row per occurrence

    Example:
        df_long = normalizer_unpivot(
            df=df_budget,
            id_cols=["BUDGET_ID", "DEPT_CODE", "ANNEE"],
            value_prefix_pairs=[
                ("BUDGET_M",  "BUDGET_MENSUEL"),
                ("REALISE_M", "REALISE_MENSUEL"),
            ],
            n_occurrences=12,
        )
        # Result: 1 input row → 12 output rows, GCID = 1..12
    """
    frames = []
    for i in range(1, n_occurrences + 1):
        suffix = str(i).zfill(zero_pad)
        chunk = df[id_cols].copy()
        chunk[gcid_col] = i
        for prefix, out_name in value_prefix_pairs:
            src_col = f"{prefix}{suffix}"
            chunk[out_name] = df[src_col].values
        frames.append(chunk)
    return pd.concat(frames, ignore_index=True)


# ---------------------------------------------------------------------------
# SCD TYPE 2 — historisation dimension
# ---------------------------------------------------------------------------

def apply_scd2(
    df_source: pd.DataFrame,
    df_dim_current: pd.DataFrame,
    natural_key: str,
    scd_cols: List[str],
    sk_col: str,
    eff_start_col: str,
    eff_end_col: str,
    is_current_col: str,
    batch_date: str,
    sk_start: int,
    end_of_time: str = "9999-12-31",
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Applies SCD Type 2 logic: detect new/changed rows, produce INSERT + UPDATE sets.

    Args:
        df_source:      Incoming source rows
        df_dim_current: Current active rows in the dimension (IS_CURRENT = 'Y')
        natural_key:    Business key column (e.g. "ACCOUNT_ID")
        scd_cols:       Columns that trigger a new version when changed
        sk_col:         Surrogate key column name (e.g. "ACCOUNT_SK")
        eff_start_col:  Effective start date column
        eff_end_col:    Effective end date column
        is_current_col: Current flag column ('Y'/'N')
        batch_date:     Processing date (YYYY-MM-DD string)
        sk_start:       Starting surrogate key for new versions
        end_of_time:    Open-ended date for active rows

    Returns:
        (df_insert, df_close)
        - df_insert: new rows to INSERT (new + changed records new version)
        - df_close:  {sk_col, eff_end_col} rows to UPDATE (close old version)
    """
    EOT      = pd.Timestamp(end_of_time)
    batch_dt = pd.Timestamp(batch_date)
    close_dt = batch_dt - pd.Timedelta(days=1)

    # Suffix for current-dimension columns during merge
    curr_suf = "_CURR_SCD2_"

    merged = df_source.merge(
        df_dim_current[[natural_key, sk_col] + scd_cols].rename(
            columns={c: f"{c}{curr_suf}" for c in [sk_col] + scd_cols}
        ),
        on=natural_key,
        how="left",
    )

    sk_curr = f"{sk_col}{curr_suf}"

    # New records: no existing surrogate key
    mask_new = merged[sk_curr].isna()

    # Changed records: at least one SCD column differs from current
    mask_changed = ~mask_new
    for col in scd_cols:
        col_curr = f"{col}{curr_suf}"
        mask_changed = mask_changed & (merged[col].astype(str) != merged[col_curr].astype(str))

    df_new     = merged[mask_new].copy()
    df_changed = merged[mask_changed].copy()
    df_insert  = pd.concat([df_new, df_changed], ignore_index=True)

    # Assign new surrogate keys
    df_insert[sk_col]         = list(generate_surrogate_keys(len(df_insert), sk_start))
    df_insert[eff_start_col]  = batch_dt
    df_insert[eff_end_col]    = EOT
    df_insert[is_current_col] = "Y"

    # Drop helper curr columns
    curr_cols = [c for c in df_insert.columns if c.endswith(curr_suf)]
    df_insert = df_insert.drop(columns=curr_cols)

    # Close old versions: only for changed records
    df_close = pd.DataFrame()
    if not df_changed.empty:
        df_close = df_changed[[sk_curr]].rename(columns={sk_curr: sk_col})
        df_close[eff_end_col] = close_dt

    return df_insert, df_close


# ---------------------------------------------------------------------------
# AGE CALCULATION — DATEDIFF(SYSDATE, col, 'YY')
# ---------------------------------------------------------------------------

def _calc_age(birth_series: pd.Series, ref_date: datetime) -> pd.Series:
    """
    Vectorised age calculation. Equivalent to Informatica DATEDIFF(SYSDATE, col, 'YY').
    Uses explicit boolean month/day comparisons — NOT tuple comparison (fragile with NaT).
    """
    age = ref_date.year - birth_series.dt.year
    birthday_passed = (
        (birth_series.dt.month < ref_date.month) |
        ((birth_series.dt.month == ref_date.month) &
         (birth_series.dt.day <= ref_date.day))
    )
    return (age - (~birthday_passed).astype(int)).astype("Int64")


# ---------------------------------------------------------------------------
# NULL-SAFE FILTER — équivalent de IIF(!ISNULL(col) AND col != val)
# ---------------------------------------------------------------------------

def null_safe_ne(series: pd.Series, value: Any) -> pd.Series:
    """
    Null-safe not-equal filter.
    Equivalent to Informatica: NOT ISNULL(col) AND col != value
    Avoids silent row loss when col contains NULL (pandas NaN != 'X' is True,
    but NULL rows behave differently than in SQL).
    """
    return series.notna() & (series != value)


def null_safe_eq(series: pd.Series, value: Any) -> pd.Series:
    """Null-safe equality. Returns False for NULL, not NaN."""
    return series.notna() & (series == value)


# ---------------------------------------------------------------------------
# IDEMPOTENT LOAD
# ---------------------------------------------------------------------------

def load_csv_atomic(df: pd.DataFrame, output_file: str) -> int:
    """
    Atomic CSV write: write to .tmp then os.replace().
    Prevents partial files on crash.
    Returns number of rows written.
    """
    os.makedirs(os.path.dirname(output_file) if os.path.dirname(output_file) else ".", exist_ok=True)
    tmp_file = output_file + ".tmp"
    df.to_csv(tmp_file, index=False)
    os.replace(tmp_file, output_file)
    return len(df)
