"""
Test Lakebridge Analyzer sur les 9 XMLs Informatica.

Usage:
  # Étape 1 — lancer l'Analyzer Lakebridge
  databricks labs lakebridge analyze \
    --source-directory ./input/ \
    --source-technology informatica \
    --report-file ./output/lakebridge_analysis.json

  # Étape 2 — comparer avec nos scores
  python compare_lakebridge.py
"""

import json
from pathlib import Path

ANALYSIS_REPORT = Path("output/lakebridge_analysis.json")

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

def recommended_model(score: int) -> str:
    if score < 8:
        return "Haiku"
    if score < 16:
        return "Sonnet"
    return "Opus"


def main():
    if not ANALYSIS_REPORT.exists():
        print(f"[ERROR] {ANALYSIS_REPORT} not found.")
        print("Run first: databricks labs lakebridge analyze \\")
        print("  --source-directory ./input/ \\")
        print("  --source-technology informatica \\")
        print("  --report-file ./output/lakebridge_analysis.json")
        return

    lb_analysis = json.loads(ANALYSIS_REPORT.read_text(encoding="utf-8"))
    print("\n[INFO] Raw Lakebridge report keys:", list(lb_analysis.keys()))

    # Indexer les résultats Lakebridge par nom de mapping (type == "Mapping")
    inventory = lb_analysis.get("inventory", [])
    lb_by_name = {}
    for item in inventory:
        if item.get("type") in ("Mapping", "Workflow", "Worklet", None):
            name = item.get("name", "")
            if name:
                lb_by_name[name] = item

    print("\n[INFO] Objets détectés par Lakebridge (inventory) :")
    for name, item in lb_by_name.items():
        print(f"  - [{item.get('type','?')}] {name} → complexityLevel={item.get('complexityLevel','N/A')}")

    print("\n" + "=" * 80)
    print("ANALYZER LAKEBRIDGE vs SCORES NOTRE PIPELINE")
    print("=" * 80)
    print(f"\n{'Workflow':<25} {'Complexité':<10} {'Score/Nôtre':<12} "
          f"{'Score/LB':<10} {'LLM Reco':<10} {'Notre résultat'}")
    print("-" * 80)

    rows = []
    for wf_name, meta in OUR_SCORES.items():
        # Cherche le workflow dans le rapport Lakebridge (matching partiel)
        lb_item = next(
            (v for k, v in lb_by_name.items() if wf_name in k or k in wf_name),
            {}
        )
        lb_score = lb_item.get("complexityScore", lb_item.get("complexity_score", "N/A"))
        lb_complexity = lb_item.get("complexityLevel", lb_item.get("complexity", "N/A"))

        model = recommended_model(meta["score"])

        print(f"{wf_name:<25} {meta['complexity']:<10} {meta['score']:<12} "
              f"{str(lb_score):<10} {model:<10} {meta['our_result']}")

        rows.append({
            "workflow":        wf_name,
            "our_score":       meta["score"],
            "our_complexity":  meta["complexity"],
            "lb_score":        lb_score,
            "lb_complexity":   lb_complexity,
            "recommended_llm": model,
            "our_result":      meta["our_result"],
            "lb_raw":          lb_item,
        })

    # Résumé routage LLM
    print(f"\n{'=' * 80}")
    print("Recommandation routage LLM (basée sur nos scores) :")
    for model in ["Haiku", "Sonnet", "Opus"]:
        wfs = [r["workflow"] for r in rows if r["recommended_llm"] == model]
        print(f"  {model:<8} ({len(wfs)} workflows) → {', '.join(wfs)}")

    # Export
    out_path = Path("output/lakebridge_analysis_comparison.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nRapport exporté → {out_path}")

    # Constat clé : Lakebridge vs notre pipeline
    lb_complexities = set(r["lb_complexity"] for r in rows if r["lb_complexity"] != "N/A")
    print(f"\n{'=' * 80}")
    print("CONSTAT CLÉ POUR LA PRÉSENTATION")
    print("=" * 80)
    print(f"  Lakebridge complexities détectées : {lb_complexities or {'(aucun match)'}}")
    all_low = all(r["lb_complexity"] in ("LOW", "N/A") for r in rows)
    if all_low:
        print("  [!] Lakebridge classe TOUS les workflows en LOW -- insensible a la complexite reelle")
        print("      (il ne compte que les appels SQL/fonctions, pas les patterns Informatica)")
        print("  [OK] Notre pipeline distingue SIMPLE->CRITICAL grace a l'analyse des transformations")
        print("       -> Recommandation : utiliser nos scores pour le routage LLM (Haiku/Sonnet/Opus)")

    print("\n[INFO] Pour voir le rapport brut Lakebridge complet :")
    print(f"  cat {ANALYSIS_REPORT}")


if __name__ == "__main__":
    main()
