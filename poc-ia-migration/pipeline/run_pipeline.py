"""
Pipeline orchestrator — runs all 5 agents in sequence.
Supports checkpoint resume: skips steps whose output already exists.

Run from poc-ia-migration/:
    python pipeline/run_pipeline.py input/wf_accounts_scd2.xml
    python pipeline/run_pipeline.py input/wf_accounts_scd2.xml --from-step 3
    python pipeline/run_pipeline.py input/wf_accounts_scd2.xml --force
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


def checkpoint_ok(output_path: Path, input_path: Path | None = None) -> bool:
    """Return True if output exists and is newer than input (checkpoint valid)."""
    if not output_path.exists():
        return False
    if input_path and input_path.exists():
        return output_path.stat().st_mtime >= input_path.stat().st_mtime
    return True


def parse_args() -> tuple[str, int, bool]:
    """Parse CLI args. Returns (xml_path, from_step, force)."""
    args = sys.argv[1:]
    force     = "--force" in args
    args      = [a for a in args if a != "--force"]

    from_step = 1
    if "--from-step" in args:
        idx       = args.index("--from-step")
        from_step = int(args[idx + 1])
        args      = args[:idx] + args[idx + 2:]

    xml_path = args[0] if args else "input/wf_clients_dim.xml"
    return xml_path, from_step, force


def run():
    xml_path, from_step, force = parse_args()
    workflow_name = Path(xml_path).stem

    # Checkpoint paths
    json_ckpt  = Path(f"output/01_canonical_json/{workflow_name}.json")
    draft_ckpt = Path(f"output/02_generated_code/{workflow_name}.py")
    fixed_ckpt = Path(f"output/03_fixed_code/{workflow_name}_fixed.py")
    doc_ckpt   = Path(f"output/03_fixed_code/{workflow_name}_documented.py")

    pipeline_start = time.time()
    log.info("=" * 72)
    log.info("  POC IA Migration — Pipeline démarré")
    log.info(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log.info(f"  Workflow  : {workflow_name}  ({xml_path})")
    log.info(f"  From step : {from_step}{' (force)' if force else ''}")
    log.info("=" * 72)

    # -----------------------------------------------------------------------
    # Step 1 — Parser Agent
    # -----------------------------------------------------------------------
    section("STEP 1/5 — Parser Agent (XML → Canonical JSON)")
    t = time.time()

    skip1 = not force and from_step > 1 and checkpoint_ok(json_ckpt, Path(xml_path))
    if skip1:
        log.info(f"  ⏭  SKIPPED — checkpoint found: {json_ckpt}")
        canonical = json.loads(json_ckpt.read_text(encoding="utf-8"))
    else:
        try:
            parser               = ParserAgent(xml_path)
            canonical, json_path = parser.run()
        except Exception as e:
            log.error(f"Parser Agent failed: {e}")
            sys.exit(1)
        platform    = canonical["routing_decision"]["target_platform"]
        feasibility = canonical["routing_decision"]["auto_conversion_feasibility"]
        complexity  = canonical.get("workflow_complexity", {})
        log.info(
            f"✅ Parser Agent — {elapsed(t)} | platform={platform} | "
            f"feasibility={feasibility} | complexity={complexity.get('flag','?')} "
            f"(score={complexity.get('total_score','?')}, ~{complexity.get('estimated_migration_days','?')}j)"
        )

    if skip1:
        complexity  = canonical.get("workflow_complexity", {})
        log.info(
            f"   complexity={complexity.get('flag','?')} "
            f"(score={complexity.get('total_score','?')}, ~{complexity.get('estimated_migration_days','?')}j)"
        )

    # -----------------------------------------------------------------------
    # Step 2 — CodeGen Agent
    # -----------------------------------------------------------------------
    section("STEP 2/5 — CodeGen Agent (JSON → Python draft)")
    t = time.time()

    skip2 = not force and from_step > 2 and checkpoint_ok(draft_ckpt, json_ckpt)
    if skip2:
        log.info(f"  ⏭  SKIPPED — checkpoint found: {draft_ckpt}")
        code_draft = draft_ckpt.read_text(encoding="utf-8")
    else:
        try:
            codegen               = CodeGenAgent(canonical, workflow_name=workflow_name)
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

    skip3 = not force and from_step > 3 and checkpoint_ok(fixed_ckpt, draft_ckpt)
    if skip3:
        log.info(f"  ⏭  SKIPPED — checkpoint found: {fixed_ckpt}")
        fix_result = {"cycles_used": 0, "status": "FIXED", "output_file": str(fixed_ckpt)}
    else:
        try:
            fixer      = FixerAgent(code_draft, canonical, workflow_name=workflow_name)
            fix_result = fixer.run()
        except Exception as e:
            log.error(f"Fixer Agent failed: {e}")
            sys.exit(1)
        log.info(
            f"✅ Fixer Agent — {elapsed(t)} | "
            f"cycles={fix_result['cycles_used']} | status={fix_result['status']}"
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

    skip4 = not force and from_step > 4 and checkpoint_ok(doc_ckpt, fixed_ckpt)
    if skip4:
        log.info(f"  ⏭  SKIPPED — checkpoint found: {doc_ckpt}")
        doc_result = {
            "annotated_code_file": str(doc_ckpt),
            "explanation_lines":   0,
            "annotated_lines":     len(doc_ckpt.read_text(encoding="utf-8").splitlines()),
        }
    else:
        try:
            documenter = DocumenterAgent(fixed_code, canonical, workflow_name=workflow_name)
            doc_result = documenter.run()
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

    expected_file = f"tests/{workflow_name}_expected.csv"
    if not Path(expected_file).exists():
        expected_file = "tests/expected_output.csv"

    try:
        qa        = QAAgent(
            doc_result["annotated_code_file"],
            expected_file,
            canonical=canonical,
            workflow_name=workflow_name,
        )
        qa_result = qa.run()
    except Exception as e:
        log.error(f"QA Agent failed: {e}")
        sys.exit(1)

    verdict = qa_result["overall_verdict"]
    verdict_icon = "✅" if verdict == "PASS" else ("⚠️" if verdict == "CRASH" else "❌")
    log.info(
        f"{verdict_icon} QA Agent — {elapsed(t)} | "
        f"anomalies={qa_result['anomalies_count']} | verdict={verdict}"
    )
    if verdict == "CRASH":
        last_line = (qa_result.get("crash_traceback") or "").strip().splitlines()
        log.warning(f"  Crash: {last_line[-1] if last_line else 'see data_diff_report.json'}")

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
    log.info(f"    📄 {json_ckpt}")
    log.info(f"    🐍 {draft_ckpt}")
    log.info(f"    🔧 {fixed_ckpt}")
    log.info(f"    📝 output/03_fixed_code/workflow_explanation.md")
    log.info(f"    💬 {doc_ckpt}")
    log.info(f"    📊 output/04_data_diff_report/data_diff_report.html")
    log.info("=" * 72)

    if verdict == "CRASH":
        log.warning("⚠️  Script crash — voir output/04_data_diff_report/data_diff_report.html")
    elif verdict != "PASS":
        log.warning("⚠️  QA non validé — voir output/04_data_diff_report/data_diff_report.html")
        sys.exit(1)

    return qa_result


if __name__ == "__main__":
    run()
