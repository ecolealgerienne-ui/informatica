"""
Agent 5 — QA Agent
Input  : output/03_fixed_code/wf_clients_dim_documented.py  (annotated code to execute)
         tests/expected_output.csv                           (Informatica reference)
Output : output/04_data_diff_report/data_diff_report.json
         output/04_data_diff_report/data_diff_report.html

Steps:
  1. Execute the documented Python batch → actual output CSV
  2. Load actual vs expected, apply tolerance matrix
  3. Build compact statistical summary (pure Python, size-bounded)
  4. Call Claude Code ONLY if anomalies exist — sends summary only, never raw rows
  5. Generate structured JSON report + HTML report
"""

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

INPUT_CODE_PATH = Path("output/03_fixed_code/wf_workflow_documented.py")
EXPECTED_PATH   = Path("tests/expected_output.csv")
OUTPUT_DIR      = Path("output/04_data_diff_report")
ACTUAL_PATH     = OUTPUT_DIR / "actual_output.csv"

MAX_SAMPLE = 3  # max anomaly samples sent to LLM per column

# Column name patterns for tolerance inference (derived from canonical target fields)
_EXCLUDE_SUFFIXES = ("LOAD_DATE", "ETL_DATE", "INSERT_DATE", "UPDATE_DATE", "LOAD_TS", "ETL_TS")
_EXCLUDE_EXACT    = {"DW_LOAD_DATE", "CREATED_AT", "UPDATED_AT"}
_AGE_PATTERNS     = {"AGE", "AGE_ANS", "NB_ANNEES", "ANNEES"}
_NUMERIC_TYPES    = {"number", "decimal", "float", "double", "numeric", "integer", "bigint", "int"}


def _build_tolerance_from_canonical(canonical: dict) -> dict:
    """
    Derive column tolerance matrix from canonical target field definitions.
    Rules:
      - Load-timestamp columns (DW_LOAD_DATE, *_ETL_DATE …) → exclude
      - Age calculation columns (AGE, AGE_ANS …) → numeric ±1 (birthday edge case)
      - Numeric datatypes (number, decimal, float …) → numeric ±0.01
      - Everything else → exact string match
    Falls back to {"*": {"type": "exact"}} if canonical has no targets.
    """
    tolerance: dict = {}
    for tgt in canonical.get("targets", []):
        for field in tgt.get("fields", []):
            name  = field.get("name", "").upper()
            dtype = field.get("datatype", "").lower()
            if not name:
                continue
            if name in _EXCLUDE_EXACT or any(name.endswith(p) for p in _EXCLUDE_SUFFIXES):
                tolerance[name] = {"type": "exclude"}
            elif name in _AGE_PATTERNS:
                tolerance[name] = {"type": "numeric", "tolerance": 1}
            elif any(t in dtype for t in _NUMERIC_TYPES):
                tolerance[name] = {"type": "numeric", "tolerance": 0.01}
            else:
                tolerance[name] = {"type": "exact"}
    return tolerance or {"*": {"type": "exact"}}


def _detect_primary_key(canonical: dict, tolerance: dict) -> str:
    """
    Detect the primary key column to use as compare() index.
    Priority: explicit is_primary_key flag → *_ID/*_SK/*_KEY suffix → first column.
    """
    for tgt in canonical.get("targets", []):
        for field in tgt.get("fields", []):
            if field.get("is_primary_key") or field.get("keytype", "").upper() not in ("NOT A KEY", "", "NONE"):
                name = field.get("name", "")
                if name:
                    return name.upper()
    # Heuristic: first column whose name ends with a key suffix
    for name in tolerance:
        if any(name.endswith(s) for s in ("_ID", "_SK", "_KEY", "_CODE")):
            return name
    return next(iter(tolerance), "ID")


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Fixture generation — synthetic test data from canonical JSON schema
# ---------------------------------------------------------------------------

N_FIXTURE_ROWS = 5

def _extract_file_env_vars(code_path: str) -> list[str]:
    """Find all os.getenv("*_FILE") references in generated script."""
    src = Path(code_path).read_text(encoding="utf-8")
    return list(dict.fromkeys(re.findall(r'os\.getenv\(["\']([A-Z_]+_FILE)["\']', src)))


def _columns_used_per_env_var(code_path: str) -> dict[str, list[str]]:
    """
    Parse the generated script to find which columns each *_FILE variable uses.
    Strategy: find variable assigned from pd.read_csv(ENV_VAR), then collect
    all df["COL"] and df[["COL1","COL2"]] and subset=[...] references on that var.
    Returns {ENV_VAR: [col1, col2, ...]}
    """
    src = Path(code_path).read_text(encoding="utf-8")
    result: dict[str, list[str]] = {}

    # Step 1: map env var → local variable name
    # e.g.  dim = pd.read_csv(DIM_ACCOUNTS_FILE)
    #        df  = pd.read_csv(SOURCE_FILE, ...)
    var_to_local: dict[str, str] = {}
    for m in re.finditer(
        r'(\w+)\s*=\s*pd\.read_csv\(\s*([A-Z_]+_FILE)\b',
        src,
    ):
        local_var, env_var = m.group(1), m.group(2)
        var_to_local[env_var] = local_var

    # Step 2: for each local var, collect column references
    for env_var, local in var_to_local.items():
        cols: list[str] = []
        # df["COL"] or df[['COL']]
        cols += re.findall(rf'{re.escape(local)}\[\s*["\']([A-Z_][A-Z0-9_]*)["\']', src)
        # subset=["COL1", "COL2"]
        cols += re.findall(r'subset\s*=\s*\[([^\]]+)\]', src)
        # flatten subset matches (they're comma-separated quoted strings)
        flat: list[str] = []
        for c in cols:
            flat += re.findall(r'["\']([A-Z_][A-Z0-9_]*)["\']', c)
        cols = list(dict.fromkeys(flat or cols))
        if cols:
            result[env_var] = cols

    return result


def _synthetic_value(col: str, idx: int) -> str:
    col_up = col.upper()
    if any(k in col_up for k in ("DATE", "TIME", "MODIFIED", "CREATION", "START", "END")):
        return f"2024-0{(idx % 9) + 1}-01"
    if any(k in col_up for k in ("_ID", "_SK", "_CODE", "NEXTVAL", "CURRVAL")):
        return str(idx + 1)
    if any(k in col_up for k in ("LIMIT", "AMOUNT", "RATE", "SCORE", "AMOUNT")):
        return str(round(1000.0 + idx * 100, 2))
    if "STATUS" in col_up or "IS_CURRENT" in col_up or "FLAG" in col_up:
        return "Y" if idx % 2 == 0 else "N"
    return f"TEST_{col[:10]}_{idx}"


def _generate_fixture_csv(columns: list[str], tmp_dir: Path, name: str) -> str:
    """Generate a minimal synthetic CSV with given columns (deduplicated)."""
    unique_cols = list(dict.fromkeys(columns))  # preserve order, remove duplicates
    rows = [{col: _synthetic_value(col, i) for col in unique_cols} for i in range(N_FIXTURE_ROWS)]
    df   = pd.DataFrame(rows, columns=unique_cols)
    assert df.columns.is_unique, f"Duplicate columns in fixture {name}: {df.columns[df.columns.duplicated()].tolist()}"
    path = tmp_dir / f"{name}.csv"
    df.to_csv(path, index=False)
    return str(path)


def _fields_from_canonical(canonical: dict) -> tuple[list[str], dict[str, list[str]]]:
    """
    Extract field names from canonical JSON (real structure from parser_agent).
    Returns:
      - source_cols     : fields of the first (main) SOURCE table
      - cols_by_table   : {TABLE_NAME_UPPER: [field names]} for ALL source tables
                          Built directly from XML SOURCE definitions — exact DB columns,
                          not transformation ports (avoids column name collisions on merge)
    """
    source_cols: list[str] = []
    cols_by_table: dict[str, list[str]] = {}

    # ALL source tables (main + lookup sources like REF_STATUT, DIM_*)
    for src in canonical.get("sources", []):
        name = src.get("name", "").upper()
        cols = [f["name"] for f in src.get("fields", []) if f.get("name")]
        if cols:
            cols_by_table[name] = cols
            if not source_cols:
                source_cols = cols  # first source = main source

    # TARGET table fields as additional reference (covers DIM_ tables not in sources)
    for tgt in canonical.get("targets", []):
        name = tgt.get("name", "").upper()
        cols = [f["name"] for f in tgt.get("fields", []) if f.get("name")]
        if name and cols and name not in cols_by_table:
            cols_by_table[name] = cols

    # Lookup transformation ref_table → only as fallback if not already in sources/targets
    for t in canonical.get("transformations", []):
        if t.get("type") == "Lookup Procedure":
            ref = t.get("ref_table", "").upper()
            if ref and ref not in cols_by_table:
                output_ports = [
                    p["name"] for p in t.get("ports", [])
                    if p.get("name") and p.get("port_type", "").upper() != "INPUT"
                ]
                # Also extract physical join-key columns from the lookup condition
                # e.g. "IN_CATEGORY = CATEGORY_RAW" → CATEGORY_RAW is a physical column
                # in the ref table that must exist in the fixture
                condition = t.get("condition", "")
                rhs_cols = re.findall(r'=\s*([A-Z_][A-Z0-9_]+)', condition.upper())
                all_ref_cols = list(dict.fromkeys(output_ports + rhs_cols))
                if all_ref_cols:
                    cols_by_table[ref] = all_ref_cols

    # Source Qualifier transformations → index by physical table name
    # SQ_SALES_ONLINE → table_name=SALES_ONLINE, so SALES_ONLINE_FILE fixture gets SQ ports
    # Also used as fallback for source_cols when no explicit <SOURCE> element exists
    for t in canonical.get("transformations", []):
        if t.get("type") == "Source Qualifier":
            sq_name = t.get("name", "").upper()
            # Strip common SQ_ prefix to get the physical table name
            table_name = sq_name[3:] if sq_name.startswith("SQ_") else sq_name
            ports = [p["name"] for p in t.get("ports", []) if p.get("name")]
            if table_name and ports and table_name not in cols_by_table:
                cols_by_table[table_name] = ports
            if not source_cols and ports:
                source_cols = ports

    return source_cols, cols_by_table


def _build_fixture_env(
    code_path: str,
    canonical: dict,
    tmp_dir: Path,
    workflow_name: str,
) -> dict[str, str]:
    """
    Build env var dict for executing the batch.
    For each *_FILE env var found in the script:
      1. Check tests/{workflow_name}_{var_lower}.csv  — use if exists (golden)
      2. Otherwise generate synthetic CSV from canonical JSON port names
    """
    file_vars    = _extract_file_env_vars(code_path)
    env_map      = {"OUTPUT_FILE": str(ACTUAL_PATH), "BATCH_DATE": "2023-01-01"}

    # Priority 1: columns inferred by static analysis of the generated script itself
    script_cols  = _columns_used_per_env_var(code_path)

    # Priority 2: canonical JSON structural info
    source_cols, lkp_cols = _fields_from_canonical(canonical)

    # Priority 3: all ports from all transformations (superset fallback)
    all_cols = list(dict.fromkeys(
        p["name"]
        for t in canonical.get("transformations", [])
        for p in t.get("ports", [])
        if p.get("name")
    )) or ["ID", "VALUE", "STATUS", "DATE_MODIFIED"]

    for var in file_vars:
        if var == "OUTPUT_FILE":
            continue
        # Check for a pre-built golden fixture first
        golden = Path(f"tests/{workflow_name}_{var.lower()}.csv")
        if not golden.exists():
            golden = Path(f"tests/{var.lower()}.csv")
        if golden.exists():
            env_map[var] = str(golden)
            print(f"[QA]   {var} → {golden} (golden)")
        else:
            # Column selection strategy (priority order):
            # 1. Columns inferred by static analysis of the generated script
            # 2. Columns from the SOURCE table in canonical JSON matching this env var
            #    e.g. REF_STATUT_FILE → canonical sources["REF_STATUT"].fields
            #    This prevents column name collisions on merge (e.g. LIBELLE in both tables)
            # 3. Superset of all ports (last resort)
            table_name = var.replace("_FILE", "").upper()  # e.g. REF_STATUT_FILE → REF_STATUT
            if var in script_cols:
                cols = script_cols[var]
                src  = "script-analysis"
            elif table_name in lkp_cols:
                cols = lkp_cols[table_name]
                src  = f"source-schema({table_name})"
            elif var == "SOURCE_FILE":
                cols = source_cols or all_cols
                src  = "source-schema(main)" if source_cols else "superset-all"
            else:
                cols = all_cols
                src  = "superset-all"

            unique_cols = list(dict.fromkeys(cols))
            path = _generate_fixture_csv(unique_cols, tmp_dir, f"fixture_{var.lower()}")
            env_map[var] = path
            print(f"[QA]   {var} → {path} ({src}, {len(unique_cols)} cols)")

    return env_map


# ---------------------------------------------------------------------------
# Step 1 — Execute the Python batch
# ---------------------------------------------------------------------------

def execute_batch(code_path: str, canonical: dict | None = None, workflow_name: str = "wf_workflow") -> pd.DataFrame:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    tmp_dir = OUTPUT_DIR / "_fixtures"
    # Always recreate fixture dir to avoid stale CSVs from previous runs
    import shutil
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    tmp_dir.mkdir(parents=True)

    env = os.environ.copy()

    if canonical:
        fixtures = _build_fixture_env(code_path, canonical, tmp_dir, workflow_name)
        env.update(fixtures)
    else:
        # No canonical JSON — minimal env, script must provide its own defaults
        env["OUTPUT_FILE"] = str(ACTUAL_PATH)
        env["BATCH_DATE"]  = "2023-01-01"

    print(f"[QA] Executing batch: {code_path}")
    result = subprocess.run(
        [sys.executable, code_path],
        capture_output=True, text=True, env=env, timeout=60,
    )
    for line in result.stdout.splitlines():
        print(f"[QA]   {line}")
    if result.returncode != 0:
        # Return sentinel to signal crash — pipeline keeps running, QA reports CRASH
        return None, result.stderr
    return pd.read_csv(ACTUAL_PATH, dtype=str), None


# ---------------------------------------------------------------------------
# Step 2 — Data Diff with tolerance matrix (full detail, stored in report)
# ---------------------------------------------------------------------------

def normalise(df: pd.DataFrame) -> pd.DataFrame:
    for col in df.select_dtypes(include=["object", "str"]).columns:
        df[col] = df[col].str.strip()
    return df


def compare(actual: pd.DataFrame, expected: pd.DataFrame, pk_col: str, tolerance: dict) -> dict:
    actual   = normalise(actual.copy()).set_index(pk_col).sort_index()
    expected = normalise(expected.copy()).set_index(pk_col).sort_index()

    anomalies   = []
    col_results = {}
    total_rows  = len(expected)

    missing_ids = sorted(set(expected.index) - set(actual.index))
    extra_ids   = sorted(set(actual.index) - set(expected.index))
    common_ids  = sorted(set(actual.index) & set(expected.index))

    if missing_ids:
        anomalies.append({"type": "MISSING_ROWS", "count": len(missing_ids),
                          "detail": f"{pk_col}s absents: {missing_ids[:10]}"})
    if extra_ids:
        anomalies.append({"type": "EXTRA_ROWS", "count": len(extra_ids),
                          "detail": f"{pk_col}s en surplus: {extra_ids[:10]}"})

    for col, rule in tolerance.items():
        if rule["type"] == "exclude":
            col_results[col] = {"status": "EXCLUDED", "anomalies": 0}
            continue
        if col == pk_col:
            col_results[col] = {"status": "OK", "anomalies": 0}
            continue
        if col not in actual.columns or col not in expected.columns:
            col_results[col] = {"status": "MISSING_COLUMN", "anomalies": 1}
            anomalies.append({"type": "MISSING_COLUMN", "column": col, "count": 1})
            continue

        col_anomalies = []
        for cid in common_ids:
            va = actual.loc[cid, col]
            ve = expected.loc[cid, col]

            if rule["type"] == "exact":
                if str(va).strip() != str(ve).strip():
                    col_anomalies.append({"pk_value": cid, "actual": va, "expected": ve})

            elif rule["type"] == "numeric":
                try:
                    diff = abs(float(va) - float(ve))
                    if diff > rule["tolerance"]:
                        col_anomalies.append({"pk_value": cid, "actual": va,
                                              "expected": ve, "diff": diff})
                except (ValueError, TypeError):
                    col_anomalies.append({"pk_value": cid, "actual": va,
                                          "expected": ve, "error": "non-numeric"})

        status = "OK" if not col_anomalies else "ANOMALY"
        col_results[col] = {
            "status":    status,
            "anomalies": len(col_anomalies),
            "details":   col_anomalies,  # full detail kept in report, never sent to LLM
        }
        if col_anomalies:
            anomalies.append({
                "type":    "VALUE_MISMATCH",
                "column":  col,
                "count":   len(col_anomalies),
                "samples": [{"pk_value": d["pk_value"], "actual": d["actual"], "expected": d["expected"]} for d in col_anomalies[:3]],
            })

    return {
        "rows_expected":   total_rows,
        "rows_actual":     len(actual),
        "rows_common":     len(common_ids),
        "anomalies_count": len(anomalies),
        "anomalies":       anomalies,
        "columns":         col_results,
    }


SAMPLES_PER_BUCKET = 3  # lignes par bucket dans le sampling stratifié


# ---------------------------------------------------------------------------
# Step 3b — Stratified sampling for HTML diagnostic (never sent to LLM)
# ---------------------------------------------------------------------------

def build_stratified_samples(diff: dict) -> dict:
    """
    Pour chaque colonne en anomalie, produit des échantillons représentatifs
    groupés par pattern (ex: diff=+1, diff=-1, null, mismatch).
    Stocké dans le rapport HTML uniquement — jamais envoyé au LLM.
    """
    samples = {}
    for col, result in diff["columns"].items():
        if result.get("status") != "ANOMALY":
            continue

        details = result.get("details", [])
        buckets: dict[str, list] = {}

        for row in details:
            # Clé de bucket selon le type d'anomalie
            if "error" in row:
                key = f"non-numeric"
            elif "diff" in row:
                d = float(row["diff"])
                actual_f   = float(row["actual"])   if row["actual"]   not in (None, "nan", "") else None
                expected_f = float(row["expected"]) if row["expected"] not in (None, "nan", "") else None
                if actual_f is not None and expected_f is not None:
                    direction = "+1" if actual_f > expected_f else "-1" if actual_f < expected_f else f"diff={d:.1f}"
                    key = f"drift_{direction}"
                else:
                    key = f"drift_{d:.1f}"
            else:
                # exact mismatch — bucket par valeur attendue
                key = f"expected={str(row.get('expected', ''))[:30]}"

            buckets.setdefault(key, [])
            if len(buckets[key]) < SAMPLES_PER_BUCKET:
                buckets[key].append({
                    "client_id": row.get("client_id"),
                    "actual":    row.get("actual"),
                    "expected":  row.get("expected"),
                })

        samples[col] = {
            "total_anomalies": len(details),
            "buckets":         buckets,
        }

    return samples


# ---------------------------------------------------------------------------
# Step 3 — Compact statistical summary (size-bounded, safe to send to LLM)
# ---------------------------------------------------------------------------

def build_summary(diff: dict) -> dict:
    """
    Produce a compact summary of the diff result.
    Size is O(nb_columns * MAX_SAMPLE) — always < 2KB regardless of data volume.
    This is the ONLY payload sent to the LLM.
    """
    total = diff["rows_expected"]
    col_summaries = {}

    for col, result in diff["columns"].items():
        status = result["status"]
        if status in ("EXCLUDED", "OK"):
            col_summaries[col] = {"status": status}
            continue

        n = result["anomalies"]
        rate = f"{n / total * 100:.1f}%" if total > 0 else "N/A"
        entry = {"status": status, "anomaly_count": n, "anomaly_rate": rate}

        details = result.get("details", [])
        if details:
            diffs = [abs(float(d["diff"])) for d in details if "diff" in d]
            if diffs:
                entry["mean_diff"] = round(sum(diffs) / len(diffs), 3)
                entry["max_diff"]  = round(max(diffs), 3)
            entry["samples"] = [
                {"actual": d["actual"], "expected": d["expected"]}
                for d in details[:MAX_SAMPLE]
            ]

        col_summaries[col] = entry

    structural = [a for a in diff["anomalies"] if a["type"] in ("MISSING_ROWS", "EXTRA_ROWS")]

    return {
        "total_rows":        total,
        "rows_actual":       diff["rows_actual"],
        "anomalies_count":   diff["anomalies_count"],
        "anomaly_columns":   [c for c, r in diff["columns"].items() if r.get("status") == "ANOMALY"],
        "structural_issues": structural,
        "columns":           col_summaries,
    }


# ---------------------------------------------------------------------------
# Step 4 — Claude Code interpretation (only on anomalies, compact payload)
# ---------------------------------------------------------------------------

NARRATIVE_PROMPT = """You are a data quality expert writing a QA report for an ETL migration.

## CRITICAL OUTPUT RULE
Respond ONLY with a JSON object. No markdown, no prose outside the JSON.
Structure:
{{
  "overall_verdict": "PASS|FAIL",
  "summary": "<2-3 sentences in French>",
  "column_verdicts": {{
    "<col>": "<one-line verdict in French>"
  }},
  "recommendations": ["<action item>", ...]
}}

## Tolerance matrix
{tolerance}

## Statistical summary of anomalies (aggregated — no raw data)
{summary}

Interpret the anomaly patterns. If AGE has ±1 drift, note it may be a birthday edge case.
Focus on root cause in the ETL logic, not individual rows.
"""


MODEL = "claude-haiku-4-5-20251001"  # compact statistical summary interpretation — lightweight model sufficient


def call_claude(prompt: str) -> str:
    result = subprocess.run(
        ["claude", "-p", "--model", MODEL, "--output-format", "text"],
        input=prompt, capture_output=True, text=True, timeout=180,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Claude CLI error:\n{result.stderr}")
    return result.stdout.strip()


def extract_json(raw: str) -> dict:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = "\n".join(l for l in raw.splitlines() if not l.startswith("```"))
    start, end = raw.find("{"), raw.rfind("}") + 1
    return json.loads(raw[start:end])


def interpret_with_claude(summary: dict, tolerance: dict) -> dict:
    prompt = NARRATIVE_PROMPT.format(
        tolerance=json.dumps(tolerance, indent=2),
        summary=json.dumps(summary, indent=2),
    )
    print("[QA] Calling Claude Code for anomaly interpretation (compact summary only)...")
    raw = call_claude(prompt)
    return extract_json(raw)


def auto_pass_narrative(summary: dict) -> dict:
    return {
        "overall_verdict": "PASS",
        "summary": f"Aucune anomalie détectée sur {summary['total_rows']} lignes. Migration validée.",
        "column_verdicts": {col: "OK" for col in summary["columns"]},
        "recommendations": ["Aucune action requise."],
    }


# ---------------------------------------------------------------------------
# Step 5 — HTML report
# ---------------------------------------------------------------------------

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<title>Data Diff Report — {workflow_id}</title>
<style>
  body  {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
           margin: 40px; color: #1a1a2e; background: #f8f9fa; }}
  h1    {{ color: #16213e; border-bottom: 3px solid #0f3460; padding-bottom: 10px; }}
  h2    {{ color: #0f3460; margin-top: 30px; }}
  .badge {{ display: inline-block; padding: 6px 16px; border-radius: 20px;
             font-weight: bold; font-size: 1.1em; }}
  .pass  {{ background: #d4edda; color: #155724; }}
  .fail  {{ background: #f8d7da; color: #721c24; }}
  .warn  {{ background: #fff3cd; color: #856404; }}
  .excl  {{ background: #e2e3e5; color: #383d41; }}
  table  {{ border-collapse: collapse; width: 100%; margin-top: 15px; }}
  th     {{ background: #16213e; color: white; padding: 10px 14px; text-align: left; }}
  td     {{ padding: 9px 14px; border-bottom: 1px solid #dee2e6; }}
  tr:nth-child(even) {{ background: #f2f2f2; }}
  .meta  {{ color: #6c757d; font-size: 0.9em; margin-bottom: 20px; }}
  .summary-box {{ background: white; border-left: 5px solid #0f3460;
                  padding: 16px 20px; border-radius: 4px; margin: 20px 0; }}
  .anomaly-box {{ background: #fff3cd; border-left: 5px solid #ffc107;
                  padding: 12px 16px; border-radius: 4px; margin: 10px 0;
                  font-family: monospace; font-size: 0.85em; }}
  ul {{ padding-left: 20px; }} li {{ margin: 6px 0; }}
</style>
</head>
<body>
<h1>📊 Data Diff Report — {workflow_id}</h1>
<p class="meta">Généré le {timestamp} | Batch date: {batch_date}</p>
<span class="badge {verdict_class}">{verdict_label}</span>
<div class="summary-box"><strong>Résumé :</strong> {summary}</div>
<h2>Statistiques</h2>
<table>
  <tr><th>Indicateur</th><th>Valeur</th></tr>
  <tr><td>Lignes attendues (Informatica)</td><td>{rows_expected}</td></tr>
  <tr><td>Lignes produites (Python)</td><td>{rows_actual}</td></tr>
  <tr><td>Lignes communes</td><td>{rows_common}</td></tr>
  <tr><td>Anomalies détectées</td><td>{anomalies_count}</td></tr>
</table>
<h2>Résultats par colonne</h2>
<table>
  <tr><th>Colonne</th><th>Tolérance</th><th>Statut</th><th>Anomalies</th><th>Verdict</th></tr>
  {column_rows}
</table>
{anomaly_details}
<h2>Échantillons diagnostiques (sampling stratifié)</h2>
{stratified_section}
<h2>Recommandations</h2>
<ul>{recommendations}</ul>
<h2>Rapport JSON complet</h2>
<pre style="background:#1a1a2e;color:#e0e0e0;padding:20px;border-radius:6px;
            overflow-x:auto;font-size:0.8em;">{json_report}</pre>
</body>
</html>"""


def _build_stratified_html(stratified: dict) -> str:
    if not stratified:
        return "<p>Aucune anomalie — aucun échantillon diagnostique.</p>"

    html = ""
    for col, data in stratified.items():
        html += f"<h3>Colonne <code>{col}</code> — {data['total_anomalies']} anomalie(s)</h3>"
        for bucket_name, rows in data["buckets"].items():
            html += f"<p><strong>Pattern : {bucket_name}</strong></p>"
            html += "<table><tr><th>Clé</th><th>Valeur obtenue</th><th>Valeur attendue</th></tr>"
            for r in rows:
                html += f"<tr><td>{r['pk_value']}</td><td>{r['actual']}</td><td>{r['expected']}</td></tr>"
            html += "</table>"
    return html


def build_html(report: dict) -> str:
    diff       = report["diff"]
    narrative  = report["narrative"]
    stratified = report.get("stratified_samples", {})
    verdict    = narrative.get("overall_verdict", "FAIL")

    col_rows = ""
    tolerance = report.get("tolerance", {})
    for col, rule in tolerance.items():
        result      = diff["columns"].get(col, {})
        status      = result.get("status", "UNKNOWN")
        n_anom      = result.get("anomalies", 0)
        tol_str     = {"exact": "Exact", "numeric": f"±{rule.get('tolerance','')}", "exclude": "Exclu"}.get(rule["type"], "?")
        verdict_col = narrative.get("column_verdicts", {}).get(col, "")

        if status == "OK":
            badge = '<span class="badge pass">✅ OK</span>'
        elif status == "EXCLUDED":
            badge = '<span class="badge excl">— Exclu</span>'
        elif status == "ANOMALY":
            badge = f'<span class="badge fail">❌ {n_anom} anomalie(s)</span>'
        else:
            badge = f'<span class="badge warn">⚠️ {status}</span>'

        col_rows += f"<tr><td><strong>{col}</strong></td><td>{tol_str}</td><td>{badge}</td><td>{n_anom}</td><td>{verdict_col}</td></tr>\n"

    anomaly_details = ""
    for anom in diff["anomalies"]:
        anomaly_details += f'<div class="anomaly-box"><strong>{anom["type"]}</strong>'
        if "column" in anom:
            anomaly_details += f' — colonne <code>{anom["column"]}</code>'
        anomaly_details += f'<br>{anom.get("detail", "")} ({anom.get("count", "")} cas)'
        if "samples" in anom:
            for s in anom["samples"][:2]:
                anomaly_details += f'<br>&nbsp;&nbsp;clé={s.get("pk_value")} | attendu={s.get("expected")} | obtenu={s.get("actual")}'
        anomaly_details += "</div>"

    recommendations = "\n".join(f"<li>{r}</li>" for r in narrative.get("recommendations", ["Aucune action requise."]))
    verdict_class = "pass" if verdict == "PASS" else ("warn" if verdict == "CRASH" else "fail")
    verdict_label = {
        "PASS":  "✅ PASS — Migration validée",
        "CRASH": "⚠️ CRASH — Script planté à l'exécution",
        "FAIL":  "❌ FAIL — Anomalies détectées",
    }.get(verdict, f"❌ {verdict}")

    return HTML_TEMPLATE.format(
        workflow_id=report["workflow_id"],
        timestamp=report["timestamp"],
        batch_date=report["batch_date"],
        verdict_class=verdict_class,
        verdict_label=verdict_label,
        summary=narrative.get("summary", ""),
        rows_expected=diff.get("rows_expected", "N/A"),
        rows_actual=diff.get("rows_actual", 0),
        rows_common=diff.get("rows_common", 0),
        anomalies_count=diff["anomalies_count"],
        column_rows=col_rows,
        anomaly_details=anomaly_details or "<p>Aucune anomalie détectée.</p>",
        stratified_section=_build_stratified_html(stratified),
        recommendations=recommendations,
        json_report=json.dumps(report, indent=2, ensure_ascii=False)[:4000],
    )


# ---------------------------------------------------------------------------
# QAAgent class
# ---------------------------------------------------------------------------

class QAAgent:
    def __init__(
        self,
        code_path: str,
        expected_path: str,
        canonical: dict | None = None,
        workflow_name: str = "wf_workflow",
    ):
        self.code_path     = code_path
        self.expected_path = expected_path
        self.canonical     = canonical
        self.workflow_name = workflow_name

    def run(self) -> dict:
        # Build tolerance matrix and primary key from canonical JSON (generic, workflow-agnostic)
        tolerance = _build_tolerance_from_canonical(self.canonical) if self.canonical else {"*": {"type": "exact"}}
        pk_col    = _detect_primary_key(self.canonical, tolerance) if self.canonical else "ID"
        print(f"[QA] Tolerance matrix: {len(tolerance)} columns | PK: {pk_col}")

        actual, crash_error = execute_batch(self.code_path, self.canonical, self.workflow_name)

        # --- CRASH: script failed to execute ---
        if actual is None:
            print(f"[QA] ⚠️  Batch CRASH — recording in report (pipeline continues)")
            print(f"[QA]   {crash_error.splitlines()[-1] if crash_error else 'unknown error'}")
            crash_diff = {
                "anomalies_count": 1,
                "anomalies": [{"type": "EXECUTION_CRASH", "count": 1, "detail": crash_error or ""}],
                "columns": {},
                "rows_expected": None,
                "rows_actual": 0,
                "rows_common": 0,
            }
            narrative = {
                "overall_verdict": "CRASH",
                "summary": f"Le script a planté à l'exécution : {(crash_error or '').splitlines()[-1]}",
                "column_verdicts": {},
                "recommendations": ["Corriger l'erreur d'exécution du script généré avant validation QA."],
            }
            report = {
                "workflow_id":     self.workflow_name,
                "timestamp":       datetime.now(timezone.utc).isoformat(),
                "batch_date":      os.getenv("BATCH_DATE", "2026-01-01"),
                "code_executed":   self.code_path,
                "expected_file":   self.expected_path,
                "diff":            crash_diff,
                "stratified_samples": {},
                "narrative":       narrative,
                "anomalies_count": 1,
                "overall_verdict": "CRASH",
                "crash_traceback": crash_error or "",
            }
            OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            json_path = OUTPUT_DIR / "data_diff_report.json"
            json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
            html_path = OUTPUT_DIR / "data_diff_report.html"
            html_path.write_text(build_html(report), encoding="utf-8")
            print(f"[QA] JSON report → {json_path}")
            print(f"[QA] HTML report → {html_path}")
            print(f"[QA] Verdict     : CRASH")
            return report

        # --- SUCCESS: script ran, do diff ---
        expected_file = Path(self.expected_path)
        if expected_file.exists():
            expected   = pd.read_csv(expected_file, dtype=str)
            print(f"[QA] Actual: {len(actual)} rows | Expected: {len(expected)} rows")
            diff       = compare(actual, expected, pk_col, tolerance)
            stratified = build_stratified_samples(diff)
        else:
            print(f"[QA] No expected file ({self.expected_path}) — execution-only QA")
            diff = {
                "anomalies_count": 0,
                "anomalies": [],
                "columns": {},
                "rows_expected": None,
                "rows_actual": len(actual),
                "rows_common": 0,
                "note": "No expected output file — execution validated only",
            }
            stratified = {}

        summary  = build_summary(diff)
        print(f"[QA] Anomalies detected: {diff['anomalies_count']} | Summary: ~{len(json.dumps(summary))} bytes")

        if diff["anomalies_count"] == 0:
            print("[QA] No anomalies — skipping LLM call")
            narrative = auto_pass_narrative(summary)
        else:
            narrative = interpret_with_claude(summary, tolerance)

        report = {
            "workflow_id":        self.workflow_name,
            "timestamp":          datetime.now(timezone.utc).isoformat(),
            "batch_date":         os.getenv("BATCH_DATE", "2026-01-01"),
            "code_executed":      self.code_path,
            "expected_file":      self.expected_path,
            "tolerance":          tolerance,
            "primary_key":        pk_col,
            "diff":               diff,
            "stratified_samples": stratified,
            "narrative":          narrative,
            "anomalies_count":    diff["anomalies_count"],
            "overall_verdict":    narrative.get("overall_verdict", "FAIL"),
        }

        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        json_path = OUTPUT_DIR / "data_diff_report.json"
        json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

        html_path = OUTPUT_DIR / "data_diff_report.html"
        html_path.write_text(build_html(report), encoding="utf-8")

        print(f"[QA] JSON report → {json_path}")
        print(f"[QA] HTML report → {html_path}")
        print(f"[QA] Verdict     : {report['overall_verdict']}")
        return report


# ---------------------------------------------------------------------------
# Standalone run
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    code_path     = sys.argv[1] if len(sys.argv) > 1 else str(INPUT_CODE_PATH)
    expected_path = sys.argv[2] if len(sys.argv) > 2 else str(EXPECTED_PATH)

    # Derive workflow_name from code path stem (e.g. wf_accounts_scd2_documented → wf_accounts_scd2)
    wf_name = Path(code_path).stem.replace("_documented", "").replace("_fixed", "")

    # Auto-load canonical JSON if it exists alongside the code outputs
    canonical_path = Path(f"output/01_canonical_json/{wf_name}.json")
    canonical = json.loads(canonical_path.read_text(encoding="utf-8")) if canonical_path.exists() else None
    if canonical:
        print(f"[QA] Loaded canonical JSON: {canonical_path}")
    else:
        print(f"[QA] No canonical JSON found at {canonical_path} — using legacy fixture mode")

    agent  = QAAgent(code_path, expected_path, canonical=canonical, workflow_name=wf_name)
    result = agent.run()
    print(f"\n[QA] Overall verdict  : {result['overall_verdict']}")
    print(f"[QA] Anomalies count  : {result['anomalies_count']}")
