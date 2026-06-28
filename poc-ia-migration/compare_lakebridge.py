"""
Compare Lakebridge output vs notre pipeline sur les 9 XMLs.

Usage:
  # Étape 1 — Analyzer
  databricks labs lakebridge analyze \
    --source-directory ./input/ \
    --source-technology informatica \
    --report-file ./output/lakebridge_analysis.json

  # Étape 2 — Transpile
  databricks labs lakebridge transpile \
    --transpiler-config-path ./lakebridge_config.json \
    --input-source ./input/ \
    --source-dialect informatica \
    --output-folder ./output/lakebridge_pyspark/

  # Étape 3 — Comparaison
  python compare_lakebridge.py
"""

import json
from pathlib import Path

ANALYSIS_REPORT  = Path("output/lakebridge_analysis.json")
LAKEBRIDGE_DIR   = Path("output/lakebridge_pyspark")
OUR_PIPELINE_DIR = Path("output/03_fixed_code")

# Scores de complexité issus de notre campagne de tests (Parser Agent)
OUR_SCORES = {
    "wf_smoke_test":        {"score": 3,  "complexity": "SIMPLE",   "our_result": "PASS"},
    "wf_clients_dim":       {"score": 6,  "complexity": "SIMPLE",   "our_result": "PASS"},
    "wf_products_dim":      {"score": 7,  "complexity": "SIMPLE",   "our_result": "PASS"},
    "wf_orders_fact":       {"score": 11, "complexity": "MEDIUM",   "our_result": "FAIL*"},
    "wf_sales_monthly":     {"score": 12, "complexity": "MEDIUM",   "our_result": "PASS"},
    "wf_unconnected_lkp":   {"score": 13, "complexity": "MEDIUM",   "our_result": "PASS"},
    "wf_xml_normalizer":    {"score": 16, "complexity": "COMPLEX",  "our_result": "PASS"},
    "wf_accounts_scd2":     {"score": 19, "complexity": "COMPLEX",  "our_result": "CRASH"},
    "wf_transactions_hist": {"score": 21, "complexity": "CRITICAL", "our_result": "UNTESTED"},
}

# Modèle LLM recommandé selon le score (architecture hybride Lakebridge + LLM)
def recommended_model(score: int) -> str:
    if score < 8:
        return "Haiku"
    if score < 16:
        return "Sonnet"
    return "Opus"


def check_lakebridge_output(wf_name: str) -> dict:
    """Vérifie ce que Lakebridge a généré pour ce workflow."""
    result = {
        "transpiled":    False,
        "lines":         0,
        "has_pyspark":   False,
        "has_scd2":      False,
        "has_join":      False,
        "has_aggregate": False,
        "file":          None,
    }

    if not LAKEBRIDGE_DIR.exists():
        return result

    # Cherche le fichier généré (plusieurs nommages possibles)
    candidates = (
        list(LAKEBRIDGE_DIR.glob(f"*{wf_name}*")) +
        list(LAKEBRIDGE_DIR.glob(f"*{wf_name.replace('wf_', '')}*"))
    )

    if not candidates:
        return result

    code = candidates[0].read_text(encoding="utf-8")
    result["transpiled"]    = True
    result["lines"]         = len(code.splitlines())
    result["has_pyspark"]   = "spark" in code.lower() or "pyspark" in code.lower()
    result["has_scd2"]      = any(k in code.lower() for k in ["eff_start", "is_current", "scd"])
    result["has_join"]      = "join(" in code.lower()
    result["has_aggregate"] = any(k in code.lower() for k in ["groupby", "agg(", "sum(", "count("])
    result["file"]          = str(candidates[0])
    return result


def check_our_output(wf_name: str) -> dict:
    """Vérifie ce que notre pipeline a généré pour ce workflow."""
    fixed = OUR_PIPELINE_DIR / f"{wf_name}_fixed.py"
    if not fixed.exists():
        return {"generated": False, "lines": 0, "file": None}
    code = fixed.read_text(encoding="utf-8")
    return {
        "generated": True,
        "lines":     len(code.splitlines()),
        "file":      str(fixed),
    }


def load_lakebridge_analysis() -> dict:
    """Charge le rapport JSON de l'Analyzer Lakebridge."""
    if not ANALYSIS_REPORT.exists():
        print(f"[WARNING] {ANALYSIS_REPORT} not found — run analyzer first")
        return {}
    return json.loads(ANALYSIS_REPORT.read_text(encoding="utf-8"))


def extract_lb_score(wf_name: str, lb_analysis: dict) -> str:
    """Extrait le score de complexité Lakebridge depuis le rapport Analyzer."""
    for item in lb_analysis.get("workflows", []):
        if wf_name in item.get("name", ""):
            return str(item.get("complexity_score", "N/A"))
    return "N/A"


def main():
    print("=" * 80)
    print("COMPARAISON LAKEBRIDGE vs NOTRE PIPELINE — POC Migration Informatica")
    print("=" * 80)

    lb_analysis = load_lakebridge_analysis()

    rows = []
    for wf_name, our_meta in OUR_SCORES.items():
        lb_out  = check_lakebridge_output(wf_name)
        our_out = check_our_output(wf_name)
        lb_score = extract_lb_score(wf_name, lb_analysis)

        rows.append({
            "workflow":         wf_name,
            "our_score":        our_meta["score"],
            "lb_score":         lb_score,
            "complexity":       our_meta["complexity"],
            "recommended_llm":  recommended_model(our_meta["score"]),
            "our_result":       our_meta["our_result"],
            "lb_transpiled":    lb_out["transpiled"],
            "lb_lines":         lb_out["lines"] if lb_out["transpiled"] else None,
            "lb_has_pyspark":   lb_out["has_pyspark"],
            "lb_has_scd2":      lb_out["has_scd2"],
            "lb_has_join":      lb_out["has_join"],
            "lb_has_aggregate": lb_out["has_aggregate"],
            "lb_file":          lb_out["file"],
            "our_lines":        our_out["lines"] if our_out["generated"] else None,
            "our_file":         our_out["file"],
        })

    # --- Tableau console ---
    print(f"\n{'Workflow':<25} {'Complexité':<10} {'Score':<7} {'LB Score':<9} "
          f"{'LLM Reco':<8} {'LB OK':<7} {'LB lignes':<10} "
          f"{'Nôtre lignes':<13} {'Notre résultat'}")
    print("-" * 115)
    for r in rows:
        lb_ok    = "✅" if r["lb_transpiled"] else "❌"
        lb_lines = str(r["lb_lines"]) if r["lb_lines"] is not None else "-"
        our_lines = str(r["our_lines"]) if r["our_lines"] is not None else "-"
        print(
            f"{r['workflow']:<25} {r['complexity']:<10} {r['our_score']:<7} "
            f"{r['lb_score']:<9} {r['recommended_llm']:<8} {lb_ok:<7} "
            f"{lb_lines:<10} {our_lines:<13} {r['our_result']}"
        )

    # --- Résumé ---
    lb_success  = sum(1 for r in rows if r["lb_transpiled"])
    our_success = sum(1 for r in rows if r["our_lines"] is not None)
    print(f"\n{'=' * 80}")
    print(f"Lakebridge BladeBridge : {lb_success}/9 workflows transpilés")
    print(f"Notre pipeline IA      : {our_success}/9 workflows générés")
    print(f"{'=' * 80}")

    # --- Patterns couverts par Lakebridge ---
    if lb_success > 0:
        print("\nPatterns détectés dans la sortie Lakebridge :")
        for r in rows:
            if r["lb_transpiled"]:
                flags = []
                if r["lb_has_pyspark"]:   flags.append("PySpark")
                if r["lb_has_scd2"]:      flags.append("SCD2")
                if r["lb_has_join"]:      flags.append("Join")
                if r["lb_has_aggregate"]: flags.append("Aggregate")
                print(f"  {r['workflow']:<25} → {', '.join(flags) if flags else 'basique'}")

    # --- Recommandation routage modèle ---
    print("\nRecommandation routage LLM (architecture hybride) :")
    for model in ["Haiku", "Sonnet", "Opus"]:
        wfs = [r["workflow"] for r in rows if r["recommended_llm"] == model]
        print(f"  {model:<8} → {', '.join(wfs)}")

    # --- Export JSON ---
    out_path = Path("output/comparison_lakebridge_vs_pipeline.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nRapport JSON exporté → {out_path}")


if __name__ == "__main__":
    main()
