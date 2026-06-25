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

from agents.utils import select_rag_sections, slim_canonical, rag_stats


CANONICAL_JSON_PATH = Path("output/01_canonical_json/wf_clients_dim.json")
OUTPUT_DIR          = Path("output/02_generated_code")
RAG_TEMPLATES_PATH  = Path("rag_base/python_templates.md")
RAG_MAP_PATH        = Path("rag_base/transformation_map.json")


SYSTEM_PROMPT = """You are an expert Python ETL engineer specialising in migrating Informatica PowerCenter workflows to Python/pandas.

## CRITICAL OUTPUT RULE — READ FIRST
Your response MUST be a single ```python ... ``` code block containing the complete batch script.
NO prose, NO explanations, NO tables, NO markdown outside the code block.
The response starts with ```python and ends with ```. Nothing else.

## Your role
Generate a complete, runnable Python batch script from a canonical JSON description of an Informatica mapping.

## Hard constraints — NEVER violate these
1. Use ONLY pandas vectorised operations — NEVER df.apply(), NEVER iterrows()
2. Lookups are ALWAYS implemented as df.merge() — never as loops or dict lookups on rows
3. Age calculation MUST use the fully vectorised pattern from the RAG Base
4. Load function MUST be idempotent (write to .tmp then os.replace())
5. Follow the exact mandatory batch structure from the RAG Base
6. All datetime operations use pd.to_datetime() or .dt accessor — never strptime() in a loop
7. Output ONLY the ```python code block — zero explanations, zero markdown outside the block
8. MANDATORY GUARD: After every extract step, add an early-exit guard:
   if df.empty:
       print("[WARNING] No rows extracted — pipeline exits cleanly")
       return
   This prevents crashes on downstream operations (merges, type casts) when filters return 0 rows.

## RAG Base — Approved transformation map
{rag_map}

## RAG Base — Mandatory Python batch patterns
{rag_templates}
"""

USER_PROMPT = """Generate the complete Python batch script for this Informatica mapping.

## Canonical JSON
{canonical_json}

## Instructions
- Read ALL sources, targets, transformations and connectors from the canonical JSON above
- Implement one extract() function per source table, reading from env var *_FILE CSV paths
- Implement one lookup_<name>() function per Lookup Procedure transformation using df.merge()
- Implement transform() applying ALL expressions defined in Expression transformations
- Implement filter functions for each Filter transformation found in the canonical JSON
- Implement load() writing final target columns to OUTPUT_FILE atomically
- Implement main() orchestrating all steps with audit logging (rows_in / rows_out per step)
- Use BATCH_DATE env var for all date filters; use *_FILE env vars for all CSV file paths
- Output ONLY the columns defined in the target table(s) of the canonical JSON
"""


MODEL = "claude-sonnet-4-6"  # code generation — powerful model required for quality


def call_claude(system: str, user: str) -> str:
    prompt = f"SYSTEM:\n{system}\n\nUSER:\n{user}"
    result = subprocess.run(
        ["claude", "-p", "--model", MODEL, "--output-format", "text"],
        input=prompt,
        capture_output=True,
        text=True,
        timeout=300,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Claude CLI error:\n{result.stderr}")
    return result.stdout.strip()


def clean_code(raw: str) -> str:
    """Extract Python code from Claude response regardless of wrapping format."""
    # Case 1: response contains a ```python ... ``` block → extract it
    if "```python" in raw:
        start = raw.find("```python") + len("```python")
        end   = raw.find("```", start)
        if end != -1:
            return raw[start:end].strip()

    # Case 2: generic ``` block
    if "```" in raw:
        start = raw.find("```") + 3
        # skip language tag if present
        nl = raw.find("\n", start)
        if nl != -1:
            start = nl + 1
        end = raw.find("```", start)
        if end != -1:
            return raw[start:end].strip()

    # Case 3: raw Python — find first line that looks like Python
    lines = raw.splitlines()
    for i, line in enumerate(lines):
        stripped = line.strip()
        if (stripped.startswith('"""') or stripped.startswith("import ")
                or stripped.startswith("# ") or stripped.startswith("from ")):
            return "\n".join(lines[i:]).strip()

    return raw.strip()


class CodeGenAgent:
    def __init__(self, canonical: dict, workflow_name: str = "wf_workflow"):
        self.canonical     = canonical
        self.workflow_name = workflow_name

    def run(self) -> str:
        # Select only RAG sections relevant to this workflow's transformation types
        selected_map, selected_tmpl = select_rag_sections(
            self.canonical, str(RAG_MAP_PATH), str(RAG_TEMPLATES_PATH)
        )
        stats = rag_stats(str(RAG_MAP_PATH), str(RAG_TEMPLATES_PATH),
                          selected_map, selected_tmpl)
        print(f"[CodeGen] RAG selection: {stats['selected_chars']} / {stats['full_chars']} chars "
              f"(−{stats['reduction_pct']}%)")

        # Use medium slim canonical (drop precision/scale/nullable)
        canonical_slim = slim_canonical(self.canonical, level="medium")

        system = SYSTEM_PROMPT.format(
            rag_map=selected_map,
            rag_templates=selected_tmpl,
        )
        user = USER_PROMPT.format(
            canonical_json=json.dumps(canonical_slim, indent=2),
        )

        print("[CodeGen] Calling Claude Code to generate Python batch...")
        raw  = call_claude(system, user)
        code = clean_code(raw)

        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        output_path = OUTPUT_DIR / f"{self.workflow_name}.py"
        output_path.write_text(code, encoding="utf-8")
        print(f"[CodeGen] Code written to {output_path} ({len(code.splitlines())} lines)")
        return code, str(output_path)


if __name__ == "__main__":
    json_path = sys.argv[1] if len(sys.argv) > 1 else str(CANONICAL_JSON_PATH)
    canonical = json.loads(Path(json_path).read_text(encoding="utf-8"))
    wf_name   = Path(json_path).stem
    agent = CodeGenAgent(canonical, workflow_name=wf_name)
    code, out_path = agent.run()
    print(f"\n[CodeGen] Output: {out_path}")
    print(f"[CodeGen] Preview (first 20 lines):")
    print("\n".join(code.splitlines()[:20]))
