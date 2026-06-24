"""
Agent 4 — QA Agent
Input  : output/03_fixed_code/wf_clients_dim_documented.py  (annotated code to execute)
         tests/expected_output.csv                           (Informatica reference)
         output/03_fixed_code/workflow_explanation.md        (for LLM context)
Output : output/04_data_diff_report/data_diff_report.json
         output/04_data_diff_report/data_diff_report.html

Steps:
  1. Execute the documented Python batch → actual output CSV
  2. Load actual vs expected, apply tolerance matrix
  3. Call Claude Code → interpret anomalies, produce narrative
  4. Generate structured JSON report + HTML report
"""

import importlib.util
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

INPUT_CODE_PATH  = Path("output/03_fixed_code/wf_clients_dim_documented.py")
INPUT_EXPL_PATH  = Path("output/03_fixed_code/workflow_explanation.md")
EXPECTED_PATH    = Path("tests/expected_output.csv")
OUTPUT_DIR       = Path("output/04_data_diff_report")
ACTUAL_PATH      = OUTPUT_DIR / "actual_output.csv"

# Tolerance matrix from spec section 6
TOLERANCE = {
    "CLIENT_ID":      {"type": "exact"},
    "NOM_CLEAN":      {"type": "exact"},
    "PRENOM_CLEAN":   {"type": "exact"},
    "AGE":            {"type": "numeric", "tolerance": 1},
    "STATUT_CODE":    {"type": "exact"},
    "STATUT_LIBELLE": {"type": "exact"},
    "EMAIL_LOWER":    {"type": "exact"},
    "SEGMENT_CODE":   {"type": "exact"},
    "PAYS_CODE":      {"type": "exact"},
    "DATE_CREATION":  {"type": "exact"},
    "BATCH_DATE":     {"type": "exact"},
    "DW_LOAD_DATE":   {"type": "exclude"},
}


# ---------------------------------------------------------------------------
# Step 1 — Execute the Python batch
# ---------------------------------------------------------------------------

def execute_batch(code_path: str) -> pd.DataFrame:
    """Run the documented batch script and return the actual output."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env["SOURCE_FILE"]      = "tests/golden_dataset.csv"
    env["REF_STATUT_FILE"]  = "tests/ref_statut.csv"
    env["OUTPUT_FILE"]      = str(ACTUAL_PATH)
    env["BATCH_DATE"]       = "2026-01-01"

    print(f"[QA] Executing batch: {code_path}")
    result = subprocess.run(
        [sys.executable, code_path],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Batch execution failed:\n{result.stderr}")

    for line in result.stdout.splitlines():
        print(f"[QA]   {line}")

    return pd.read_csv(ACTUAL_PATH, dtype=str)


# ---------------------------------------------------------------------------
# Step 2 — Data Diff with tolerance matrix
# ---------------------------------------------------------------------------

def normalise(df: pd.DataFrame) -> pd.DataFrame:
    """Normalise string columns for comparison."""
    for col in df.select_dtypes(include=["object", "str"]).columns:
        df[col] = df[col].str.strip()
    return df


def compare(actual: pd.DataFrame, expected: pd.DataFrame) -> dict:
    """Apply tolerance matrix and return structured diff results."""
    actual   = normalise(actual.copy())
    expected = normalise(expected.copy())

    # Align on CLIENT_ID
    actual   = actual.set_index("CLIENT_ID").sort_index()
    expected = expected.set_index("CLIENT_ID").sort_index()

    anomalies   = []
    col_results = {}

    missing_in_actual   = set(expected.index) - set(actual.index)
    extra_in_actual     = set(actual.index) - set(expected.index)
    common_ids          = sorted(set(actual.index) & set(expected.index))

    if missing_in_actual:
        anomalies.append({
            "type":    "MISSING_ROWS",
            "detail":  f"CLIENT_IDs in expected but not in actual: {sorted(missing_in_actual)}",
            "count":   len(missing_in_actual),
        })
    if extra_in_actual:
        anomalies.append({
            "type":    "EXTRA_ROWS",
            "detail":  f"CLIENT_IDs in actual but not in expected: {sorted(extra_in_actual)}",
            "count":   len(extra_in_actual),
        })

    for col, rule in TOLERANCE.items():
        if rule["type"] == "exclude":
            col_results[col] = {"status": "EXCLUDED", "anomalies": 0}
            continue
        if col == "CLIENT_ID":
            col_results[col] = {"status": "OK", "anomalies": 0}
            continue
        if col not in actual.columns or col not in expected.columns:
            col_results[col] = {"status": "MISSING_COLUMN", "anomalies": 1}
            anomalies.append({"type": "MISSING_COLUMN", "column": col})
            continue

        col_anomalies = []
        for cid in common_ids:
            val_actual   = actual.loc[cid, col]
            val_expected = expected.loc[cid, col]

            if rule["type"] == "exact":
                if str(val_actual).strip() != str(val_expected).strip():
                    col_anomalies.append({
                        "client_id": cid,
                        "actual":    val_actual,
                        "expected":  val_expected,
                    })
            elif rule["type"] == "numeric":
                try:
                    diff = abs(float(val_actual) - float(val_expected))
                    if diff > rule["tolerance"]:
                        col_anomalies.append({
                            "client_id": cid,
                            "actual":    val_actual,
                            "expected":  val_expected,
                            "diff":      diff,
                        })
                except (ValueError, TypeError):
                    col_anomalies.append({
                        "client_id": cid,
                        "actual":    val_actual,
                        "expected":  val_expected,
                        "error":     "non-numeric",
                    })

        status = "OK" if not col_anomalies else "ANOMALY"
        col_results[col] = {"status": status, "anomalies": len(col_anomalies), "details": col_anomalies}
        if col_anomalies:
            anomalies.append({
                "type":    "VALUE_MISMATCH",
                "column":  col,
                "count":   len(col_anomalies),
                "samples": col_anomalies[:3],
            })

    return {
        "rows_expected":   len(expected),
        "rows_actual":     len(actual),
        "rows_common":     len(common_ids),
        "anomalies_count": len(anomalies),
        "anomalies":       anomalies,
        "columns":         col_results,
    }


# ---------------------------------------------------------------------------
# Step 3 — Claude Code narrative interpretation
# ---------------------------------------------------------------------------

NARRATIVE_PROMPT = """You are a data quality expert writing a QA report for an ETL migration.

## CRITICAL OUTPUT RULE
Respond ONLY with a JSON object. No markdown, no prose outside the JSON.
Structure:
{{
  "overall_verdict": "PASS|FAIL",
  "summary": "<2-3 sentence plain-language summary in French>",
  "column_verdicts": {{
    "<col>": "<one-line verdict in French>"
  }},
  "recommendations": ["<action item>", ...]
}}

## Workflow explanation (context)
{explanation}

## Data diff results
{diff_results}

## Tolerance matrix applied
{tolerance}

Interpret the diff results. If anomalies exist, explain their likely cause based on the workflow logic.
If AGE has ±1 anomalies, note this is within tolerance (birthday edge case).
If DW_LOAD_DATE is excluded, confirm this is expected behaviour.
"""


def call_claude(prompt: str) -> str:
    result = subprocess.run(
        ["claude", "-p", "--output-format", "text"],
        input=prompt,
        capture_output=True,
        text=True,
        timeout=180,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Claude CLI error:\n{result.stderr}")
    return result.stdout.strip()


def extract_json(raw: str) -> dict:
    raw = raw.strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        raw = "\n".join(l for l in lines if not l.startswith("```"))
    start = raw.find("{")
    end   = raw.rfind("}") + 1
    return json.loads(raw[start:end])


def interpret_with_claude(diff: dict, explanation: str) -> dict:
    prompt = NARRATIVE_PROMPT.format(
        explanation=explanation[:3000],
        diff_results=json.dumps(diff, indent=2),
        tolerance=json.dumps(TOLERANCE, indent=2),
    )
    print("[QA] Calling Claude Code for narrative interpretation...")
    raw = call_claude(prompt)
    return extract_json(raw)


# ---------------------------------------------------------------------------
# Step 4 — HTML report
# ---------------------------------------------------------------------------

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<title>Data Diff Report — {workflow_id}</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
          margin: 40px; color: #1a1a2e; background: #f8f9fa; }}
  h1   {{ color: #16213e; border-bottom: 3px solid #0f3460; padding-bottom: 10px; }}
  h2   {{ color: #0f3460; margin-top: 30px; }}
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
  ul {{ padding-left: 20px; }}
  li {{ margin: 6px 0; }}
</style>
</head>
<body>

<h1>📊 Data Diff Report — {workflow_id}</h1>
<p class="meta">Généré le {timestamp} | Batch date: {batch_date}</p>

<span class="badge {verdict_class}">{verdict_label}</span>

<div class="summary-box">
  <strong>Résumé :</strong> {summary}
</div>

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

<h2>Recommandations</h2>
<ul>
  {recommendations}
</ul>

<h2>Rapport JSON complet</h2>
<pre style="background:#1a1a2e;color:#e0e0e0;padding:20px;border-radius:6px;
            overflow-x:auto;font-size:0.8em;">{json_report}</pre>

</body>
</html>"""


def build_html(report: dict) -> str:
    diff      = report["diff"]
    narrative = report["narrative"]
    verdict   = narrative.get("overall_verdict", "FAIL")

    col_rows = ""
    for col, rule in TOLERANCE.items():
        result  = diff["columns"].get(col, {})
        status  = result.get("status", "UNKNOWN")
        n_anom  = result.get("anomalies", 0)
        tol_str = {"exact": "Exact", "numeric": f"±{rule.get('tolerance','')}", "exclude": "Exclu"}.get(rule["type"], "?")
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
        if "samples" in anom and anom["samples"]:
            for s in anom["samples"][:2]:
                anomaly_details += f'<br>&nbsp;&nbsp;CLIENT_ID={s["client_id"]} | attendu={s.get("expected")} | obtenu={s.get("actual")}'
        anomaly_details += "</div>"

    recommendations = "\n".join(
        f"<li>{r}</li>" for r in narrative.get("recommendations", ["Aucune action requise."])
    )

    verdict_class = "pass" if verdict == "PASS" else "fail"
    verdict_label = "✅ PASS — Migration validée" if verdict == "PASS" else "❌ FAIL — Anomalies détectées"

    return HTML_TEMPLATE.format(
        workflow_id=report["workflow_id"],
        timestamp=report["timestamp"],
        batch_date=report["batch_date"],
        verdict_class=verdict_class,
        verdict_label=verdict_label,
        summary=narrative.get("summary", ""),
        rows_expected=diff["rows_expected"],
        rows_actual=diff["rows_actual"],
        rows_common=diff["rows_common"],
        anomalies_count=diff["anomalies_count"],
        column_rows=col_rows,
        anomaly_details=anomaly_details if anomaly_details else "<p>Aucune anomalie détectée.</p>",
        recommendations=recommendations,
        json_report=json.dumps(report, indent=2, ensure_ascii=False)[:4000],
    )


# ---------------------------------------------------------------------------
# QAAgent class
# ---------------------------------------------------------------------------

class QAAgent:
    def __init__(self, code_path: str, expected_path: str):
        self.code_path     = code_path
        self.expected_path = expected_path

    def run(self) -> dict:
        actual   = execute_batch(self.code_path)
        expected = pd.read_csv(self.expected_path, dtype=str)
        print(f"[QA] Actual: {len(actual)} rows | Expected: {len(expected)} rows")

        diff = compare(actual, expected)
        print(f"[QA] Anomalies detected: {diff['anomalies_count']}")

        explanation = ""
        if INPUT_EXPL_PATH.exists():
            explanation = INPUT_EXPL_PATH.read_text(encoding="utf-8")

        narrative = interpret_with_claude(diff, explanation)

        report = {
            "workflow_id":     "wf_CLIENTS_DIM",
            "timestamp":       datetime.now(timezone.utc).isoformat(),
            "batch_date":      os.getenv("BATCH_DATE", "2026-01-01"),
            "code_executed":   self.code_path,
            "expected_file":   self.expected_path,
            "diff":            diff,
            "narrative":       narrative,
            "anomalies_count": diff["anomalies_count"],
            "overall_verdict": narrative.get("overall_verdict", "FAIL"),
        }

        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

        json_path = OUTPUT_DIR / "data_diff_report.json"
        json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

        html      = build_html(report)
        html_path = OUTPUT_DIR / "data_diff_report.html"
        html_path.write_text(html, encoding="utf-8")

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

    agent  = QAAgent(code_path, expected_path)
    result = agent.run()
    print(f"\n[QA] Overall verdict  : {result['overall_verdict']}")
    print(f"[QA] Anomalies count  : {result['anomalies_count']}")
