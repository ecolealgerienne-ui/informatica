"""
Agent 3.5 — Documenter Agent
Input  : output/03_fixed_code/wf_clients_dim_fixed.py
         output/01_canonical_json/wf_clients_dim.json
Output : output/03_fixed_code/workflow_explanation.md   (métier/technique)
         output/03_fixed_code/wf_clients_dim_documented.py  (code annoté)

Steps:
  1. Call Claude Code → generate business/technical explanation (Markdown)
  2. Call Claude Code → inject inline comments into fixed code using explanation
"""

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


INPUT_CODE_PATH = Path("output/03_fixed_code/wf_clients_dim_fixed.py")
INPUT_JSON_PATH = Path("output/01_canonical_json/wf_clients_dim.json")
OUTPUT_DIR      = Path("output/03_fixed_code")


# ---------------------------------------------------------------------------
# Prompt 1 — Business/Technical explanation
# ---------------------------------------------------------------------------

EXPLANATION_PROMPT = """You are an expert ETL migration consultant who bridges business and technical teams.

## CRITICAL OUTPUT RULE
Respond ONLY with a Markdown document. No preamble, no sign-off. Start directly with the first heading.
Do NOT include any JSON or raw data dumps in your response — the canonical JSON is provided as context only.

## DATEDIFF / AGE PATTERN — USE THIS EXACT FORMULA (do not invent alternatives)
```python
def _calc_age(birth_series: pd.Series, ref_date: datetime) -> pd.Series:
    age = ref_date.year - birth_series.dt.year
    birthday_passed = (
        (birth_series.dt.month < ref_date.month) |
        ((birth_series.dt.month == ref_date.month) &
         (birth_series.dt.day <= ref_date.day))
    )
    return (age - (~birthday_passed).astype(int)).astype("Int64")
```
Never use tuple comparison `(month, day) < (month, day)` — it is fragile with NaT values.

## Task
Produce a bilingual (French business / English technical) explanation of this Informatica PowerCenter workflow.
The audience is: (1) business analysts who validate the migration, (2) developers who maintain the Python code.

## Required document structure

# {workflow_id} — Documentation de migration

## Vue d'ensemble métier
<2-3 sentences: what this workflow does in plain business language, what data it produces and why>

## Flux de données
<A step-by-step description of the data journey, written for a business analyst>

## Transformations — Détail métier/technique

For EACH transformation, produce a section like this:

### <transformation_name> — <human label>
| Dimension | Description |
|---|---|
| **Rôle métier** | <what business problem this solves, in French> |
| **Entrées** | <input fields> |
| **Sorties** | <output fields> |
| **Règle technique** | <exact Python/pandas equivalent> |
| **Points d'attention** | <edge cases, tolerance, risks> |

## Variables & paramètres
<Explain $$BATCH_DATE and any session variables in plain language>

## Règles de qualité (Data Diff)
<Explain what the QA agent will check and why each column has its tolerance>

## Risques & points de vigilance migration
<Bullet list of things a reviewer should double-check>

"""


# ---------------------------------------------------------------------------
# Prompt 2 — Annotated code
# ---------------------------------------------------------------------------

ANNOTATION_PROMPT = """You are a senior Python ETL engineer adding inline documentation to a batch script.

## CRITICAL OUTPUT RULE
Your response MUST be a single ```python ... ``` block with the fully annotated script.
NO prose outside the block. Start with ```python, end with ```.

## Task
Add clear, meaningful inline comments to the Python code below using the workflow explanation as your source.
Comments must be:
- In French for business rules (WHY this step exists)
- In English for technical implementation notes (HOW it works)
- Concise — one line max per comment, no multi-paragraph blocks
- Placed ABOVE the relevant line or block, not at end of line (except for very short notes)

## DO NOT change any logic — only add comments. The code must remain functionally identical.

## Workflow explanation (source of truth for comments)
{explanation}

## Python code to annotate
```python
{code}
```
"""


# ---------------------------------------------------------------------------
# Claude Code CLI helpers
# ---------------------------------------------------------------------------

MODEL = "claude-haiku-4-5-20251001"  # structured reformulation — lightweight model sufficient


def call_claude(prompt: str, timeout: int = 300, max_tokens: int = 3000) -> str:
    result = subprocess.run(
        ["claude", "-p", "--model", MODEL, "--output-format", "text",
         "--max-tokens", str(max_tokens)],
        input=prompt,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Claude CLI error:\n{result.stderr}")
    return result.stdout.strip()


def extract_code(raw: str) -> str:
    if "```python" in raw:
        start = raw.find("```python") + len("```python")
        end   = raw.find("```", start)
        if end != -1:
            return raw[start:end].strip()
    if "```" in raw:
        start = raw.find("```") + 3
        nl    = raw.find("\n", start)
        if nl != -1:
            start = nl + 1
        end = raw.find("```", start)
        if end != -1:
            return raw[start:end].strip()
    lines = raw.splitlines()
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith('"""') or s.startswith("import ") or s.startswith("# "):
            return "\n".join(lines[i:]).strip()
    return raw.strip()


# ---------------------------------------------------------------------------
# DocumenterAgent class
# ---------------------------------------------------------------------------

class DocumenterAgent:
    def __init__(self, code: str, canonical: dict, workflow_name: str = "wf_workflow"):
        self.code          = code
        self.canonical     = canonical
        self.workflow_name = workflow_name

    def generate_explanation(self) -> str:
        prompt = EXPLANATION_PROMPT.format(
            workflow_id=self.canonical.get("workflow_id", self.workflow_name.upper()),
        )
        print("[Documenter] Generating business/technical explanation (no canonical — code only)...")
        return call_claude(prompt, max_tokens=3000)

    def annotate_code(self, explanation: str) -> str:
        prompt = ANNOTATION_PROMPT.format(
            explanation=explanation,
            code=self.code,
        )
        print("[Documenter] Annotating Python code with inline comments...")
        raw = call_claude(prompt)
        return extract_code(raw)

    def run(self) -> dict:
        explanation   = self.generate_explanation()
        annotated     = self.annotate_code(explanation)

        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

        expl_path = OUTPUT_DIR / "workflow_explanation.md"
        expl_path.write_text(explanation, encoding="utf-8")
        print(f"[Documenter] Explanation → {expl_path} ({len(explanation.splitlines())} lines)")

        code_path = OUTPUT_DIR / f"{self.workflow_name}_documented.py"
        code_path.write_text(annotated, encoding="utf-8")
        print(f"[Documenter] Annotated code → {code_path} ({len(annotated.splitlines())} lines)")

        return {
            "explanation_file":   str(expl_path),
            "annotated_code_file": str(code_path),
            "explanation_lines":  len(explanation.splitlines()),
            "annotated_lines":    len(annotated.splitlines()),
            "timestamp":          datetime.now(timezone.utc).isoformat(),
        }


# ---------------------------------------------------------------------------
# Standalone run
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    code_path = sys.argv[1] if len(sys.argv) > 1 else str(INPUT_CODE_PATH)
    json_path = sys.argv[2] if len(sys.argv) > 2 else str(INPUT_JSON_PATH)

    code      = Path(code_path).read_text(encoding="utf-8")
    canonical = json.loads(Path(json_path).read_text(encoding="utf-8"))

    agent  = DocumenterAgent(code, canonical)
    result = agent.run()

    print(f"\n[Documenter] Explanation : {result['explanation_lines']} lines")
    print(f"[Documenter] Annotated   : {result['annotated_lines']} lines")
    print(f"\n--- Explanation preview (first 30 lines) ---")
    preview = Path(result["explanation_file"]).read_text(encoding="utf-8")
    print("\n".join(preview.splitlines()[:30]))
