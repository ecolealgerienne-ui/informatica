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
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

INPUT_CODE_PATH = Path("output/03_fixed_code/wf_clients_dim_documented.py")
EXPECTED_PATH   = Path("tests/expected_output.csv")
OUTPUT_DIR      = Path("output/04_data_diff_report")
ACTUAL_PATH     = OUTPUT_DIR / "actual_output.csv"

# Tolerance matrix
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

MAX_SAMPLE = 3  # max anomaly samples sent to LLM per column


# ---------------------------------------------------------------------------
# Step 1 — Execute the Python batch
# ---------------------------------------------------------------------------

def execute_batch(code_path: str) -> pd.DataFrame:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["SOURCE_FILE"]     = "tests/golden_dataset.csv"
    env["REF_STATUT_FILE"] = "tests/ref_statut.csv"
    env["OUTPUT_FILE"]     = str(ACTUAL_PATH)
    env["BATCH_DATE"]      = "2026-01-01"

    print(f"[QA] Executing batch: {code_path}")
    result = subprocess.run(
        [sys.executable, code_path],
        capture_output=True, text=True, env=env, timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Batch execution failed:\n{result.stderr}")
    for line in result.stdout.splitlines():
        print(f"[QA]   {line}")
    return pd.read_csv(ACTUAL_PATH, dtype=str)


# ---------------------------------------------------------------------------
# Step 2 — Data Diff with tolerance matrix (full detail, stored in report)
# ---------------------------------------------------------------------------

def normalise(df: pd.DataFrame) -> pd.DataFrame:
    for col in df.select_dtypes(include=["object", "str"]).columns:
        df[col] = df[col].str.strip()
    return df


def compare(actual: pd.DataFrame, expected: pd.DataFrame) -> dict:
    actual   = normalise(actual.copy()).set_index("CLIENT_ID").sort_index()
    expected = normalise(expected.copy()).set_index("CLIENT_ID").sort_index()

    anomalies   = []
    col_results = {}
    total_rows  = len(expected)

    missing_ids = sorted(set(expected.index) - set(actual.index))
    extra_ids   = sorted(set(actual.index) - set(expected.index))
    common_ids  = sorted(set(actual.index) & set(expected.index))

    if missing_ids:
        anomalies.append({"type": "MISSING_ROWS", "count": len(missing_ids),
                          "detail": f"CLIENT_IDs absents: {missing_ids[:10]}"})
    if extra_ids:
        anomalies.append({"type": "EXTRA_ROWS", "count": len(extra_ids),
                          "detail": f"CLIENT_IDs en surplus: {extra_ids[:10]}"})

    for col, rule in TOLERANCE.items():
        if rule["type"] == "exclude":
            col_results[col] = {"status": "EXCLUDED", "anomalies": 0}
            continue
        if col == "CLIENT_ID":
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
                    col_anomalies.append({"client_id": cid, "actual": va, "expected": ve})

            elif rule["type"] == "numeric":
                try:
                    diff = abs(float(va) - float(ve))
                    if diff > rule["tolerance"]:
                        col_anomalies.append({"client_id": cid, "actual": va,
                                              "expected": ve, "diff": diff})
                except (ValueError, TypeError):
                    col_anomalies.append({"client_id": cid, "actual": va,
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
                "samples": col_anomalies[:3],
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


def call_claude(prompt: str) -> str:
    result = subprocess.run(
        ["claude", "-p", "--output-format", "text"],
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


def interpret_with_claude(summary: dict) -> dict:
    prompt = NARRATIVE_PROMPT.format(
        tolerance=json.dumps(TOLERANCE, indent=2),
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
            html += "<table><tr><th>CLIENT_ID</th><th>Valeur obtenue</th><th>Valeur attendue</th></tr>"
            for r in rows:
                html += f"<tr><td>{r['client_id']}</td><td>{r['actual']}</td><td>{r['expected']}</td></tr>"
            html += "</table>"
    return html


def build_html(report: dict) -> str:
    diff       = report["diff"]
    narrative  = report["narrative"]
    stratified = report.get("stratified_samples", {})
    verdict    = narrative.get("overall_verdict", "FAIL")

    col_rows = ""
    for col, rule in TOLERANCE.items():
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
                anomaly_details += f'<br>&nbsp;&nbsp;CLIENT_ID={s["client_id"]} | attendu={s.get("expected")} | obtenu={s.get("actual")}'
        anomaly_details += "</div>"

    recommendations = "\n".join(f"<li>{r}</li>" for r in narrative.get("recommendations", ["Aucune action requise."]))
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
        anomaly_details=anomaly_details or "<p>Aucune anomalie détectée.</p>",
        stratified_section=_build_stratified_html(stratified),
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

        diff     = compare(actual, expected)
        summary  = build_summary(diff)
        stratified = build_stratified_samples(diff)
        print(f"[QA] Anomalies detected: {diff['anomalies_count']} | Summary payload: ~{len(json.dumps(summary))} bytes")

        if diff["anomalies_count"] == 0:
            print("[QA] No anomalies — skipping LLM call")
            narrative = auto_pass_narrative(summary)
        else:
            narrative = interpret_with_claude(summary)

        report = {
            "workflow_id":        "wf_CLIENTS_DIM",
            "timestamp":          datetime.now(timezone.utc).isoformat(),
            "batch_date":         os.getenv("BATCH_DATE", "2026-01-01"),
            "code_executed":      self.code_path,
            "expected_file":      self.expected_path,
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
    agent  = QAAgent(code_path, expected_path)
    result = agent.run()
    print(f"\n[QA] Overall verdict  : {result['overall_verdict']}")
    print(f"[QA] Anomalies count  : {result['anomalies_count']}")
