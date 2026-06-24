"""
Pipeline orchestrator — runs all 5 agents in sequence.
Stops and escalates if any agent returns a FAILED or ESCALATE status.

Run from poc-ia-migration/:
    python pipeline/run_pipeline.py
"""

import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

# Add project root to path so agents can be imported
sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.parser_agent     import ParserAgent
from agents.codegen_agent    import CodeGenAgent
from agents.fixer_agent      import FixerAgent
from agents.documenter_agent import DocumenterAgent
from agents.qa_agent         import QAAgent

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s — %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("pipeline")

SEPARATOR = "─" * 72


def section(title: str):
    log.info(SEPARATOR)
    log.info(f"  {title}")
    log.info(SEPARATOR)


def elapsed(start: float) -> str:
    return f"{time.time() - start:.1f}s"


def run():
    xml_path      = sys.argv[1] if len(sys.argv) > 1 else "input/wf_clients_dim.xml"
    workflow_name = Path(xml_path).stem

    pipeline_start = time.time()
    log.info("=" * 72)
    log.info("  POC IA Migration — Pipeline démarré")
    log.info(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log.info(f"  Workflow : {workflow_name}  ({xml_path})")
    log.info("=" * 72)

    # -----------------------------------------------------------------------
    # Step 1 — Parser Agent
    # -----------------------------------------------------------------------
    section("STEP 1/5 — Parser Agent (XML → Canonical JSON)")
    t = time.time()
    try:
        parser              = ParserAgent(xml_path)
        canonical, json_path = parser.run()
    except Exception as e:
        log.error(f"Parser Agent failed: {e}")
        sys.exit(1)

    platform    = canonical["routing_decision"]["target_platform"]
    feasibility = canonical["routing_decision"]["auto_conversion_feasibility"]
    complexity  = canonical.get("workflow_complexity", {})
    wf_flag     = complexity.get("flag", "?")
    wf_score    = complexity.get("total_score", "?")
    wf_days     = complexity.get("estimated_migration_days", "?")
    log.info(
        f"✅ Parser Agent — {elapsed(t)} | platform={platform} | "
        f"feasibility={feasibility} | complexity={wf_flag} (score={wf_score}, ~{wf_days}j)"
    )

    # -----------------------------------------------------------------------
    # Step 2 — CodeGen Agent
    # -----------------------------------------------------------------------
    section("STEP 2/5 — CodeGen Agent (JSON → Python draft)")
    t = time.time()
    try:
        codegen              = CodeGenAgent(canonical, workflow_name=workflow_name)
        code_draft, code_path = codegen.run()
    except Exception as e:
        log.error(f"CodeGen Agent failed: {e}")
        sys.exit(1)

    log.info(f"✅ CodeGen Agent — {elapsed(t)} | {len(code_draft.splitlines())} lines generated")

    # -----------------------------------------------------------------------
    # Step 3 — Fixer Agent
    # -----------------------------------------------------------------------
    section("STEP 3/5 — Fixer Agent (static checks + semantic correction)")
    t = time.time()
    try:
        fixer      = FixerAgent(code_draft, canonical, workflow_name=workflow_name)
        fix_result = fixer.run()
    except Exception as e:
        log.error(f"Fixer Agent failed: {e}")
        sys.exit(1)

    log.info(
        f"✅ Fixer Agent — {elapsed(t)} | "
        f"cycles={fix_result['cycles_used']} | "
        f"status={fix_result['status']}"
    )
    if fix_result["status"] == "ESCALATE":
        log.error("🚨 Human intervention required — see output/03_fixed_code/fix_report.json")
        sys.exit(1)

    fixed_code = Path(fix_result["output_file"]).read_text(encoding="utf-8")

    # -----------------------------------------------------------------------
    # Step 4 — Documenter Agent
    # -----------------------------------------------------------------------
    section("STEP 4/5 — Documenter Agent (explanation + annotated code)")
    t = time.time()
    try:
        documenter  = DocumenterAgent(fixed_code, canonical, workflow_name=workflow_name)
        doc_result  = documenter.run()
    except Exception as e:
        log.error(f"Documenter Agent failed: {e}")
        sys.exit(1)

    log.info(
        f"✅ Documenter Agent — {elapsed(t)} | "
        f"explanation={doc_result['explanation_lines']} lines | "
        f"annotated={doc_result['annotated_lines']} lines"
    )

    # -----------------------------------------------------------------------
    # Step 5 — QA Agent
    # -----------------------------------------------------------------------
    section("STEP 5/5 — QA Agent (data diff + HTML report)")
    t = time.time()
    try:
        qa         = QAAgent(doc_result["annotated_code_file"], "tests/expected_output.csv")
        qa_result  = qa.run()
    except Exception as e:
        log.error(f"QA Agent failed: {e}")
        sys.exit(1)

    verdict = qa_result["overall_verdict"]
    log.info(
        f"✅ QA Agent — {elapsed(t)} | "
        f"anomalies={qa_result['anomalies_count']} | "
        f"verdict={verdict}"
    )

    # -----------------------------------------------------------------------
    # Final summary
    # -----------------------------------------------------------------------
    total = elapsed(pipeline_start)
    log.info("=" * 72)
    log.info("  PIPELINE TERMINÉ")
    log.info("=" * 72)
    log.info(f"  Durée totale       : {total}")
    log.info(f"  Verdict QA         : {verdict}")
    log.info(f"  Anomalies          : {qa_result['anomalies_count']}")
    log.info(f"  Fixer cycles       : {fix_result['cycles_used']}")
    log.info("")
    log.info("  Outputs produits :")
    log.info(f"    📄 output/01_canonical_json/{workflow_name}.json")
    log.info(f"    🐍 output/02_generated_code/{workflow_name}.py")
    log.info(f"    🔧 output/03_fixed_code/{workflow_name}_fixed.py")
    log.info(f"    📝 output/03_fixed_code/workflow_explanation.md")
    log.info(f"    💬 output/03_fixed_code/{workflow_name}_documented.py")
    log.info(f"    📊 output/04_data_diff_report/data_diff_report.html")
    log.info("=" * 72)

    if verdict != "PASS":
        log.warning("⚠️  QA non validé — voir output/04_data_diff_report/data_diff_report.html")
        sys.exit(1)

    return qa_result


if __name__ == "__main__":
    run()
