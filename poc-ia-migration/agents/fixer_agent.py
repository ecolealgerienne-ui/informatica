"""
Agent 3 — Fixer Agent
Input  : output/02_generated_code/wf_clients_dim.py
         output/01_canonical_json/wf_clients_dim.json
Output : output/03_fixed_code/wf_clients_dim_fixed.py
         output/03_fixed_code/fix_report.json

Steps:
  1. Static checks (no LLM): syntax, mandatory functions, forbidden patterns
  2. If static checks pass → call Claude Code for semantic review + correction
  3. Up to 3 correction cycles
  4. Write fixed code + structured fix report
"""

import ast
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from agents.utils import (apply_function_patches, _patches_applied_count,
                          select_rag_sections, slim_canonical)


INPUT_CODE_PATH  = Path("output/02_generated_code/wf_clients_dim.py")
INPUT_JSON_PATH  = Path("output/01_canonical_json/wf_clients_dim.json")
OUTPUT_DIR       = Path("output/03_fixed_code")
RAG_MAP_PATH     = Path("rag_base/transformation_map.json")
RAG_TMPL_PATH    = Path("rag_base/python_templates.md")

MANDATORY_FUNCTIONS = {"extract", "transform", "load", "main"}
FORBIDDEN_PATTERNS  = ["df.apply(", "iterrows(", ".apply(lambda"]
MAX_CYCLES = 3


# ---------------------------------------------------------------------------
# Static checks (deterministic — no LLM)
# ---------------------------------------------------------------------------

def check_syntax(code: str) -> tuple[bool, str]:
    try:
        ast.parse(code)
        return True, ""
    except SyntaxError as e:
        return False, f"SyntaxError at line {e.lineno}: {e.msg}"


def check_mandatory_functions(code: str) -> tuple[bool, list[str]]:
    tree    = ast.parse(code)
    defined = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    missing = MANDATORY_FUNCTIONS - defined
    return len(missing) == 0, sorted(missing)


def check_forbidden_patterns(code: str) -> list[str]:
    hits = []
    for i, line in enumerate(code.splitlines(), 1):
        for pattern in FORBIDDEN_PATTERNS:
            if pattern in line:
                hits.append(f"line {i}: '{pattern}' found → use vectorised pandas instead")
    return hits


def check_calc_age_pattern(code: str) -> tuple[bool, str]:
    """Detect the fragile tuple-comparison pattern in _calc_age."""
    if "_calc_age" not in code:
        return True, ""
    if "zip(birth_series.dt.month" in code and ">= pd.Series(" in code:
        return False, (
            "_calc_age uses fragile tuple comparison (>= pd.Series of tuples). "
            "Replace with explicit month/day boolean comparisons."
        )
    return True, ""


def run_static_checks(code: str) -> dict:
    syntax_ok, syntax_err          = check_syntax(code)
    funcs_ok, missing_funcs        = check_mandatory_functions(code) if syntax_ok else (False, [])
    forbidden_hits                  = check_forbidden_patterns(code) if syntax_ok else []
    age_ok, age_err                = check_calc_age_pattern(code) if syntax_ok else (False, "")

    issues = []
    if not syntax_ok:
        issues.append({"type": "SYNTAX_ERROR", "detail": syntax_err})
    if not funcs_ok:
        issues.append({"type": "MISSING_FUNCTIONS", "detail": f"Missing: {missing_funcs}"})
    for hit in forbidden_hits:
        issues.append({"type": "FORBIDDEN_PATTERN", "detail": hit})
    if not age_ok:
        issues.append({"type": "EXPRESSION_TRANSLATION", "field": "AGE", "detail": age_err})

    return {
        "syntax_valid":      syntax_ok,
        "functions_ok":      funcs_ok,
        "forbidden_hits":    forbidden_hits,
        "age_pattern_ok":    age_ok,
        "issues":            issues,
        "has_issues":        bool(issues),
    }


# ---------------------------------------------------------------------------
# Claude Code CLI call — semantic review + correction
# ---------------------------------------------------------------------------

FIXER_PROMPT_CYCLE1 = """You are a senior Python ETL code reviewer and fixer.

## CRITICAL OUTPUT RULE
Your response MUST be a single ```python ... ``` code block with the fully corrected script.
NO prose, NO explanations outside the block. Start with ```python, end with ```.

## RAG Base — Approved patterns (these are the ONLY correct implementations)
{rag_templates}

## Transformation map
{rag_map}

## Issues detected by static analysis
{issues}

## Canonical JSON (source of truth for what the code must do)
{canonical_json}

## Code to review and correct
```python
{code}
```

## Review checklist — fix ALL of these if wrong
1. `_calc_age()`: MUST use explicit month/day boolean comparisons (NOT tuple >= pd.Series)
2. Lookup: MUST be df.merge(), NOT a loop or dict lookup
3. String ops: MUST use .str.strip()/.str.upper()/.str.lower() chains (NOT apply/lambda)
4. Filter: MUST be a boolean mask df[condition] (NOT loop)
5. Load: MUST write to .tmp then os.replace() (atomic swap)
6. if df.empty: return — MUST be present after every extract step
7. STATUT_LIBELLE must come from the lookup merge result (column LIBELLE renamed)

Return the complete corrected script inside a ```python block.
"""

FIXER_PROMPT_CYCLES = """You are a senior Python ETL code fixer handling a targeted correction cycle.

## CRITICAL OUTPUT RULE
Return ONLY the Python functions that need correction — complete and correctly named.
Do NOT return functions that are already correct. Do NOT return the full script.
Wrap your response in a single ```python ... ``` block. Nothing else.

## Issues to fix
{issues}

## Minimal context (transformation names and expressions)
{canonical_json}

## Current script (read-only — extract only the functions you need to fix)
```python
{code}
```

Fix ONLY the reported issues. Return only the corrected function(s).
"""


MODEL_CYCLE1 = "claude-sonnet-4-6"       # cycle 1: complex semantic analysis
MODEL_CYCLES  = "claude-haiku-4-5-20251001"  # cycles 2-3: minor corrections, Haiku sufficient


def call_claude(prompt: str, cycle: int = 1) -> str:
    model = MODEL_CYCLE1 if cycle == 1 else MODEL_CYCLES
    result = subprocess.run(
        ["claude", "-p", "--model", model, "--output-format", "text"],
        input=prompt,
        capture_output=True,
        text=True,
        timeout=300,
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
        if s.startswith('"""') or s.startswith("import ") or s.startswith("from "):
            return "\n".join(lines[i:]).strip()
    return raw.strip()


def semantic_fix(code: str, static_report: dict, canonical: dict, cycle: int = 1) -> str:
    issues_text = (
        json.dumps(static_report["issues"], indent=2)
        if static_report["issues"]
        else "No static issues detected. Perform full semantic review anyway."
    )

    if cycle == 1:
        # Cycle 1: selective RAG + medium slim canonical → full script output
        selected_map, selected_tmpl = select_rag_sections(
            canonical, str(RAG_MAP_PATH), str(RAG_TMPL_PATH)
        )
        canonical_slim = slim_canonical(canonical, level="medium")
        prompt = FIXER_PROMPT_CYCLE1.format(
            rag_templates=selected_tmpl,
            rag_map=selected_map,
            issues=issues_text,
            canonical_json=json.dumps(canonical_slim, indent=2),
            code=code,
        )
        raw = call_claude(prompt, cycle=cycle)
        return extract_code(raw), "full"
    else:
        # Cycles 2-3: no RAG, minimal canonical → corrected functions only
        slim = slim_canonical(canonical, level="minimal")
        prompt = FIXER_PROMPT_CYCLES.format(
            issues=issues_text,
            canonical_json=json.dumps(slim, indent=2),
            code=code,
        )
        raw = call_claude(prompt, cycle=cycle)
        patched_code = extract_code(raw)
        return patched_code, "patch"


# ---------------------------------------------------------------------------
# FixerAgent class
# ---------------------------------------------------------------------------

class FixerAgent:
    def __init__(self, code: str, canonical: dict, workflow_name: str = "wf_workflow"):
        self.code          = code
        self.canonical     = canonical
        self.workflow_name = workflow_name

    def run(self) -> dict:
        corrections  = []
        cycles_used  = 0
        current_code = self.code

        for cycle in range(1, MAX_CYCLES + 1):
            cycles_used = cycle
            print(f"[Fixer] Cycle {cycle}/{MAX_CYCLES} — running static checks...")
            static = run_static_checks(current_code)

            print(f"[Fixer]   syntax={static['syntax_valid']} "
                  f"functions={static['functions_ok']} "
                  f"forbidden={len(static['forbidden_hits'])} "
                  f"age_pattern={static['age_pattern_ok']}")

            model_used = MODEL_CYCLE1 if cycle == 1 else MODEL_CYCLES
            print(f"[Fixer]   Calling Claude Code (model={model_used}, "
                  f"mode={'full-script' if cycle == 1 else 'functions-only+AST'})...")
            llm_output, output_mode = semantic_fix(current_code, static, self.canonical, cycle=cycle)

            if output_mode == "patch":
                n = _patches_applied_count(current_code, llm_output)
                print(f"[Fixer]   AST patch applied — {n} function(s) replaced")
                merged = apply_function_patches(current_code, llm_output)
                fixed_code = merged if merged != current_code else llm_output
            else:
                fixed_code = llm_output

            fixed_static = run_static_checks(fixed_code)

            if static["issues"]:
                corrections.append({
                    "cycle":        cycle,
                    "output_mode":  output_mode,
                    "issues_found": static["issues"],
                    "resolved":     not fixed_static["has_issues"],
                })

            current_code = fixed_code

            if not fixed_static["has_issues"]:
                print(f"[Fixer] All checks passed after cycle {cycle}")
                break
        else:
            print(f"[Fixer] Max cycles ({MAX_CYCLES}) reached — escalating")

        final_static = run_static_checks(current_code)
        status = "FIXED" if not final_static["has_issues"] else "ESCALATE"

        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        output_path = OUTPUT_DIR / f"{self.workflow_name}_fixed.py"
        output_path.write_text(current_code, encoding="utf-8")

        report = {
            "input_file":    str(INPUT_CODE_PATH),
            "output_file":   str(output_path),
            "timestamp":     datetime.now(timezone.utc).isoformat(),
            "cycles_used":   cycles_used,
            "status":        status,
            "syntax_valid":  final_static["syntax_valid"],
            "rag_compliant": not bool(final_static["forbidden_hits"]),
            "age_pattern_ok": final_static["age_pattern_ok"],
            "corrections":   corrections,
            "human_escalations": final_static["issues"] if status == "ESCALATE" else [],
        }

        report_path = OUTPUT_DIR / "fix_report.json"
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

        print(f"[Fixer] Status: {status} | cycles: {cycles_used}")
        print(f"[Fixer] Fixed code → {output_path}")
        print(f"[Fixer] Report    → {report_path}")
        return report


# ---------------------------------------------------------------------------
# Standalone run
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    code_path = sys.argv[1] if len(sys.argv) > 1 else str(INPUT_CODE_PATH)
    json_path = sys.argv[2] if len(sys.argv) > 2 else str(INPUT_JSON_PATH)

    code      = Path(code_path).read_text(encoding="utf-8")
    canonical = json.loads(Path(json_path).read_text(encoding="utf-8"))

    agent  = FixerAgent(code, canonical)
    result = agent.run()
    print(f"\n[Fixer] Final status : {result['status']}")
    print(f"[Fixer] Cycles used  : {result['cycles_used']}")
    print(f"[Fixer] Corrections  : {len(result['corrections'])}")
