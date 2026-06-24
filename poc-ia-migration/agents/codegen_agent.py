"""
Agent 2 — CodeGen Agent
Input  : output/01_canonical_json/wf_clients_dim.json
Output : output/02_generated_code/wf_clients_dim.py

Steps:
  1. Load canonical JSON + RAG Base (python_templates.md + transformation_map.json)
  2. Call Claude Code CLI with a constrained prompt
  3. Write generated Python batch script
"""

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path


CANONICAL_JSON_PATH = Path("output/01_canonical_json/wf_clients_dim.json")
OUTPUT_DIR          = Path("output/02_generated_code")
RAG_TEMPLATES_PATH  = Path("rag_base/python_templates.md")
RAG_MAP_PATH        = Path("rag_base/transformation_map.json")


SYSTEM_PROMPT = """You are an expert Python ETL engineer specialising in migrating Informatica PowerCenter workflows to Python/pandas.

## Your role
Generate a complete, runnable Python batch script from a canonical JSON description of an Informatica mapping.

## Hard constraints — NEVER violate these
1. Use ONLY pandas vectorised operations — NEVER df.apply(), NEVER iterrows()
2. Lookups are ALWAYS implemented as df.merge() — never as loops or dict lookups on rows
3. Age calculation MUST use the fully vectorised pattern from the RAG Base
4. Load function MUST be idempotent (write to .tmp then os.replace())
5. Follow the exact mandatory batch structure from the RAG Base
6. All datetime operations use pd.to_datetime() or .dt accessor — never strptime() in a loop
7. Output raw Python code only — no markdown fences, no explanations

## RAG Base — Approved transformation map
{rag_map}

## RAG Base — Mandatory Python batch patterns
{rag_templates}
"""

USER_PROMPT = """Generate the complete Python batch script for this Informatica mapping.

## Canonical JSON
{canonical_json}

## Instructions
- Implement extract() reading from SOURCE_FILE CSV (simulating Oracle source), applying the DATE_MAJ >= BATCH_DATE filter
- Implement lookup_statut() using df.merge() against REF_STATUT_FILE CSV
- Implement transform() applying ALL expressions from EXP_TRANSFORM:
    * NOM_CLEAN  = LTRIM(RTRIM(UPPER(NOM)))       → str.strip().str.upper()
    * PRENOM_CLEAN = LTRIM(RTRIM(UPPER(PRENOM)))  → str.strip().str.upper()
    * AGE = DATEDIFF(SYSDATE, DATE_NAISSANCE, YY) → fully vectorised age formula from RAG Base
    * EMAIL_LOWER = LOWER(LTRIM(RTRIM(EMAIL)))    → str.strip().str.lower()
    * BATCH_DATE_OUT = TO_DATE($$BATCH_DATE)      → pd.to_datetime(BATCH_DATE)
    * DW_LOAD_DATE = SYSDATE                      → datetime.now()
    * STATUT_LIBELLE passthrough from lookup
- Implement filter_active() keeping only rows where STATUT_CODE != 'I'
- Implement load() writing final columns to OUTPUT_FILE atomically
- Implement main() orchestrating all steps with audit logging
- Use env vars: BATCH_DATE, SOURCE_FILE, REF_STATUT_FILE, OUTPUT_FILE
- Final columns in output: CLIENT_ID, NOM_CLEAN, PRENOM_CLEAN, AGE, STATUT_CODE,
  STATUT_LIBELLE, EMAIL_LOWER, SEGMENT_CODE, PAYS_CODE, DATE_CREATION, BATCH_DATE, DW_LOAD_DATE
"""


def call_claude(system: str, user: str) -> str:
    prompt = f"SYSTEM:\n{system}\n\nUSER:\n{user}"
    result = subprocess.run(
        ["claude", "-p", prompt, "--output-format", "text"],
        capture_output=True,
        text=True,
        timeout=180,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Claude CLI error:\n{result.stderr}")
    return result.stdout.strip()


def clean_code(raw: str) -> str:
    """Strip markdown fences if Claude added them despite instructions."""
    lines = raw.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines)


class CodeGenAgent:
    def __init__(self, canonical: dict):
        self.canonical = canonical

    def run(self) -> str:
        rag_map       = json.loads(RAG_MAP_PATH.read_text(encoding="utf-8"))
        rag_templates = RAG_TEMPLATES_PATH.read_text(encoding="utf-8")

        system = SYSTEM_PROMPT.format(
            rag_map=json.dumps(rag_map, indent=2),
            rag_templates=rag_templates,
        )
        user = USER_PROMPT.format(
            canonical_json=json.dumps(self.canonical, indent=2),
        )

        print("[CodeGen] Calling Claude Code to generate Python batch...")
        raw  = call_claude(system, user)
        code = clean_code(raw)

        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        output_path = OUTPUT_DIR / "wf_clients_dim.py"
        output_path.write_text(code, encoding="utf-8")
        print(f"[CodeGen] Code written to {output_path} ({len(code.splitlines())} lines)")
        return code


if __name__ == "__main__":
    json_path = sys.argv[1] if len(sys.argv) > 1 else str(CANONICAL_JSON_PATH)
    canonical = json.loads(Path(json_path).read_text(encoding="utf-8"))
    agent = CodeGenAgent(canonical)
    code  = agent.run()
    print(f"\n[CodeGen] Preview (first 20 lines):")
    print("\n".join(code.splitlines()[:20]))
