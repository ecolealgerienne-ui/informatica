#!/usr/bin/env python3
"""
Phase 1 — Analyse & Inventaire

Lance le Parser sur tous les XML du dossier input/, puis génère
le rapport HTML (dashboard + fiches workflow).

Usage depuis poc-ia-migration/ :
    python run_phase1.py                        # parsing complet (appel LLM)
    python run_phase1.py --rescore-only         # recalcul scores sans LLM (matrice modifiée)
    python run_phase1.py --project "Mon Projet"
    python run_phase1.py --workers 4            # parallélisation

Prérequis :
    pip install -r requirements.txt
    claude --version   # Claude Code CLI authentifié (non requis avec --rescore-only)
"""

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from agents.parser_agent import (
    ParserAgent, parse_xml, build_data_flow, compute_complexity,
    RAG_COMPLEXITY_PATH,
)
from agents.phase1_reporter import load_canonical_jsons, generate_index, generate_workflow_page


def rescore_one(json_path: Path, matrix: dict) -> tuple[str, bool, str]:
    """Reapply deterministic scorer to an existing canonical JSON. No LLM call."""
    try:
        canonical = json.loads(json_path.read_text(encoding="utf-8"))

        # Rebuild a minimal structural dict from the canonical JSON
        structural = {
            "transformations": [
                {
                    "name":             t["name"],
                    "type":             t["type"],
                    "has_sql_override": t.get("has_sql_override", False),
                    "select_distinct":  False,
                    "filter_condition": t.get("condition", ""),
                    "computed_fields":  t.get("computed_fields", []),
                    "multiple_match":   t.get("multiple_match", ""),
                    "cache_persistent": t.get("cache_persistent", False),
                }
                for t in canonical.get("transformations", [])
            ],
            "targets":  canonical.get("targets", []),
            "workflow": {
                "session": {
                    "parameter_file": canonical.get("session", {}).get("parameter_file", ""),
                    "variables":      canonical.get("session", {}).get("variables", []),
                }
            },
        }

        # Reuse sql_analyses already stored in canonical JSON
        sql_analyses = {}
        for t in canonical.get("transformations", []):
            if t.get("sql_analysis"):
                sql_analyses[t["name"]] = t["sql_analysis"]

        det = compute_complexity(structural, matrix, sql_analyses)

        # Patch scores in transformations
        per_t = det["per_transformation"]

        def t_flag(score: int) -> str:
            if score <= 2:  return "LOW"
            if score <= 5:  return "MEDIUM"
            if score <= 8:  return "HIGH"
            return "CRITICAL"

        for t in canonical["transformations"]:
            dt = per_t.get(t["name"], {})
            t["complexity_score"] = dt.get("score", 0)
            t["complexity_flag"]  = t_flag(dt.get("score", 0))
            t["score_breakdown"]  = dt.get("breakdown", {})

        # Patch workflow-level complexity and routing
        routing = det["routing"].copy()
        routing["rationale"] = canonical.get("routing_decision", {}).get("rationale", "")
        canonical["workflow_complexity"] = det["complexity"]
        canonical["routing_decision"]    = routing
        flags = det["global_flags"].copy()
        flags["oracle_proprietary_functions"] = canonical.get("flags", {}).get("oracle_proprietary_functions", [])
        flags["requires_human_review"]        = canonical.get("flags", {}).get("requires_human_review", [])
        canonical["flags"] = flags

        json_path.write_text(
            json.dumps(canonical, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        flag  = det["complexity"]["flag"]
        score = det["complexity"]["total_score"]
        return json_path.stem, True, f"[OK]   {json_path.name} → {flag} ({score})"
    except Exception as e:
        return json_path.stem, False, f"[ERR]  {json_path.name} — {e}"


def parse_one(xml_path: Path, output_json_dir: Path) -> tuple[Path, bool, str]:
    out = output_json_dir / f"{xml_path.stem}.json"
    if out.exists() and out.stat().st_mtime >= xml_path.stat().st_mtime:
        return xml_path, True, f"[SKIP] {xml_path.name} — JSON déjà à jour"
    try:
        agent = ParserAgent(str(xml_path))
        agent.run()
        return xml_path, True, f"[OK]   {xml_path.name}"
    except Exception as e:
        return xml_path, False, f"[ERR]  {xml_path.name} — {e}"


def main():
    p = argparse.ArgumentParser(description="Phase 1 — Analyse & Inventaire")
    p.add_argument("--input",        default="input",               help="Dossier contenant les fichiers XML Informatica")
    p.add_argument("--output",       default="output",              help="Dossier de sortie")
    p.add_argument("--project",      default="Migration Informatica", help="Nom du projet")
    p.add_argument("--workers",      type=int, default=1,           help="Workers parallèles (défaut: 1)")
    p.add_argument("--force",        action="store_true",           help="Re-parser même si le JSON existe déjà (appel LLM)")
    p.add_argument("--rescore-only", action="store_true",           help="Recalcule les scores sans LLM (matrice modifiée)")
    args = p.parse_args()

    input_dir  = Path(args.input)
    output_dir = Path(args.output)
    json_dir   = output_dir / "01_canonical_json"
    report_dir = output_dir / "phase1_report"
    json_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n{'='*60}")
    print(f"  Phase 1 — Analyse & Inventaire")
    print(f"  Projet  : {args.project}")
    if args.rescore_only:
        print(f"  Mode    : RESCORE ONLY (0 token LLM)")
    print(f"  Output  : {report_dir.resolve()}")
    print(f"{'='*60}\n")

    t0 = time.time()
    errors = []

    # ------------------------------------------------------------------
    # Mode --rescore-only : réappliquer la matrice sur les JSON existants
    # ------------------------------------------------------------------
    if args.rescore_only:
        json_files = sorted(json_dir.glob("*.json"))
        if not json_files:
            print(f"ERREUR : aucun Canonical JSON dans {json_dir}. Lancez d'abord sans --rescore-only.")
            sys.exit(1)

        print(f"ÉTAPE 1/2 — Rescoring déterministe ({len(json_files)} JSON, 0 appel LLM)\n")
        matrix = json.loads(RAG_COMPLEXITY_PATH.read_text(encoding="utf-8"))
        for jf in json_files:
            _, ok, msg = rescore_one(jf, matrix)
            print(msg)
            if not ok:
                errors.append(msg)

        total    = len(json_files)
        ok_count = total - len(errors)
        print(f"\n  Résultat : {ok_count}/{total} workflows rescorés en {time.time()-t0:.1f}s")

    # ------------------------------------------------------------------
    # Mode normal : parsing complet avec LLM
    # ------------------------------------------------------------------
    else:
        xml_files = sorted(input_dir.glob("*.xml"))
        if not xml_files:
            print(f"ERREUR : aucun fichier .xml trouvé dans {input_dir}")
            sys.exit(1)

        print(f"  Input   : {input_dir.resolve()} ({len(xml_files)} XML)\n")
        print(f"ÉTAPE 1/2 — Parsing des workflows (workers={args.workers})\n")

        if args.force:
            for f in json_dir.glob("*.json"):
                f.unlink()

        if args.workers == 1:
            for xml in xml_files:
                _, ok, msg = parse_one(xml, json_dir)
                print(msg)
                if not ok:
                    errors.append(msg)
        else:
            with ThreadPoolExecutor(max_workers=args.workers) as ex:
                futures = {ex.submit(parse_one, xml, json_dir): xml for xml in xml_files}
                for fut in as_completed(futures):
                    _, ok, msg = fut.result()
                    print(msg)
                    if not ok:
                        errors.append(msg)

        total    = len(xml_files)
        ok_count = total - len(errors)
        print(f"\n  Résultat : {ok_count}/{total} workflows parsés en {time.time()-t0:.1f}s")
        if errors:
            print(f"\n  Workflows en erreur :")
            for e in errors:
                print(f"    {e}")

    # ------------------------------------------------------------------
    # Étape 2 : Rapport HTML (commun aux deux modes)
    # ------------------------------------------------------------------
    print(f"\nÉTAPE 2/2 — Génération du rapport HTML\n")
    workflows = load_canonical_jsons(json_dir)
    if not workflows:
        print("ERREUR : aucun Canonical JSON disponible.")
        sys.exit(1)

    generate_index(workflows, args.project, report_dir)
    for wf in workflows:
        generate_workflow_page(wf, args.project, report_dir)

    print(f"\n{'='*60}")
    print(f"  Rapport Phase 1 prêt")
    print(f"  Ouvrir : {report_dir / 'index.html'}")
    if errors:
        print(f"  ATTENTION : {len(errors)} workflow(s) en erreur")
    print(f"{'='*60}\n")

    (report_dir / "summary.json").write_text(json.dumps({
        "project": args.project,
        "mode": "rescore-only" if args.rescore_only else "full-parse",
        "total": total,
        "ok": ok_count,
        "errors": errors,
        "elapsed_seconds": round(time.time() - t0, 1),
        "report": str((report_dir / "index.html").resolve()),
    }, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
