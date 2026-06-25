"""
Shared utilities for LLM pipeline optimization.
- slim_canonical        : reduce canonical JSON size per agent level
- select_rag_sections   : inject only RAG sections relevant to detected patterns
- load_file_cached      : in-memory cache for file reads (avoid repeated disk I/O)
- apply_function_patches: AST-based function replacement (Fixer cycles 2-3)
- inject_docstrings     : AST-based docstring injection (Documenter)
"""

import ast
import copy
import json
import re
import textwrap
from pathlib import Path


# ---------------------------------------------------------------------------
# In-memory RAG cache (avoids repeated disk I/O per agent call)
# ---------------------------------------------------------------------------

_file_cache: dict[str, str] = {}


def load_file_cached(path: str) -> str:
    if path not in _file_cache:
        _file_cache[path] = Path(path).read_text(encoding="utf-8")
    return _file_cache[path]


# ---------------------------------------------------------------------------
# slim_canonical — 3 levels of canonical JSON reduction
#
# level='full'    → Parser (all metadata)
# level='medium'  → CodeGen / Fixer cycle 1 (drop low-value technical fields)
# level='minimal' → Fixer cycles 2-3 (name + expression only, minimal context)
# ---------------------------------------------------------------------------

_REMOVE_MEDIUM = {"precision", "scale", "nullable", "is_primary_key", "default_value"}
_REMOVE_MINIMAL = _REMOVE_MEDIUM | {"datatype", "length", "description", "port_type"}


def slim_canonical(canonical: dict, level: str = "medium") -> dict:
    """
    Return a reduced copy of the canonical JSON.

    level='full'    → no change (Parser use)
    level='medium'  → drop precision/scale/nullable/is_primary_key (CodeGen/Fixer cy1)
    level='minimal' → keep only name + expression per port (Fixer cy2-3)
    """
    if level == "full":
        return canonical

    keys_to_remove = _REMOVE_MEDIUM if level == "medium" else _REMOVE_MINIMAL

    slim = copy.deepcopy(canonical)

    def _slim_fields(fields: list[dict]) -> list[dict]:
        return [{k: v for k, v in f.items() if k not in keys_to_remove} for f in fields]

    for src in slim.get("sources", []):
        if "fields" in src:
            src["fields"] = _slim_fields(src["fields"])

    for tgt in slim.get("targets", []):
        if "fields" in tgt:
            tgt["fields"] = _slim_fields(tgt["fields"])

    for t in slim.get("transformations", []):
        if "ports" in t:
            t["ports"] = _slim_fields(t["ports"])

    # On minimal: also drop verbose analysis fields not needed for correction
    if level == "minimal":
        for t in slim.get("transformations", []):
            for drop_key in ("score_breakdown", "has_proprietary_functions",
                             "python_equivalents", "notes"):
                t.pop(drop_key, None)
        slim.pop("workflow_complexity", None)
        slim.pop("global_flags", None)
        slim.pop("routing_decision", None)

    return slim


# ---------------------------------------------------------------------------
# select_rag_sections — inject only relevant RAG sections
#
# Maps Informatica transformation types found in the canonical JSON
# to the corresponding sections in transformation_map.json and
# python_templates.md.
# ---------------------------------------------------------------------------

# Maps canonical transformation type → transformation_map.json keys
_TYPE_TO_MAP_KEYS: dict[str, list[str]] = {
    "Source Qualifier":         ["string_functions", "date_functions",
                                  "null_functions", "oracle_specific",
                                  "null_safety_rules", "sorted_input_rules"],
    "Expression":               ["string_functions", "date_functions",
                                  "null_functions", "conditional_functions",
                                  "numeric_functions"],
    "Filter":                   ["null_safety_rules", "null_functions"],
    "Lookup Procedure":         ["lookup_patterns", "null_safety_rules"],
    "Aggregator":               ["aggregation_patterns", "sorted_input_rules"],
    "Joiner":                   ["join_patterns", "null_safety_rules",
                                  "sorted_input_rules"],
    "Union":                    ["union_patterns"],
    "Rank":                     ["rank_patterns"],
    "Router":                   ["router_patterns"],
    "Sequence Generator":       ["sequence_generator_patterns"],
    "Normalizer":               ["normalizer_patterns"],
    "Update Strategy":          ["scd_patterns"],
    "Sorter":                   ["sorted_input_rules"],
}

# Maps canonical transformation type → python_templates.md section headers
_TYPE_TO_TMPL_SECTIONS: dict[str, list[str]] = {
    "Source Qualifier":         ["Mandatory Batch Structure", "Null-Safe Comparisons",
                                  "Sorted Input"],
    "Expression":               ["Pattern: String Cleaning", "Pattern: Age Calculation",
                                  "Pattern: DECODE"],
    "Filter":                   ["Pattern: Null-Safe Comparisons"],
    "Lookup Procedure":         ["Pattern: Lookup as Merge",
                                  "Pattern: Unconnected Lookup",
                                  "Pattern: Case-Insensitive Lookup"],
    "Aggregator":               ["Pattern: Sorted Input", "Mandatory Batch Structure"],
    "Joiner":                   ["Pattern: Sorted Input", "Mandatory Batch Structure"],
    "Union":                    ["Mandatory Batch Structure"],
    "Rank":                     ["Mandatory Batch Structure"],
    "Router":                   ["Pattern: Router"],
    "Sequence Generator":       ["Pattern: Sequence Generator"],
    "Normalizer":               ["Pattern: Normalizer / Unpivot"],
    "Update Strategy":          ["Pattern: SCD Type 2"],
    "Sorter":                   ["Pattern: Sorted Input"],
}

# Always inject these regardless of workflow
_ALWAYS_MAP_KEYS = ["null_safety_rules"]
_ALWAYS_TMPL_SECTIONS = ["Mandatory Batch Structure", "Idempotent Load",
                          "Pattern: Audit Logging", "Forbidden Patterns"]


def _extract_section(text: str, header: str) -> str:
    """Extract one ## section from a markdown document."""
    pattern = rf"(## {re.escape(header)}.*?)(?=\n## |\Z)"
    m = re.search(pattern, text, re.DOTALL)
    return m.group(1).strip() if m else ""


def select_rag_sections(canonical: dict, map_path: str, tmpl_path: str) -> tuple[str, str]:
    """
    Return (selected_map_json, selected_templates_md) containing only the
    sections relevant to transformation types present in the canonical JSON.

    canonical  : parsed canonical JSON dict
    map_path   : path to transformation_map.json
    tmpl_path  : path to python_templates.md
    Returns    : (map_json_str, templates_md_str) — ready to inject into prompts
    """
    # Collect transformation types from canonical
    types_present = {
        t.get("type", "") for t in canonical.get("transformations", [])
    }

    # Determine which map keys and template sections to include
    map_keys: set[str] = set(_ALWAYS_MAP_KEYS)
    tmpl_sections: list[str] = list(_ALWAYS_TMPL_SECTIONS)

    for t_type in types_present:
        map_keys.update(_TYPE_TO_MAP_KEYS.get(t_type, []))
        for section in _TYPE_TO_TMPL_SECTIONS.get(t_type, []):
            if section not in tmpl_sections:
                tmpl_sections.append(section)

    # Check for unconnected lookup specifically (lookup_subtype field)
    for t in canonical.get("transformations", []):
        if t.get("lookup_subtype") == "UNCONNECTED":
            map_keys.add("lookup_patterns")
            if "Pattern: Unconnected Lookup" not in tmpl_sections:
                tmpl_sections.append("Pattern: Unconnected Lookup")

    # Build filtered map JSON
    full_map = json.loads(load_file_cached(map_path))
    selected_map = {k: full_map[k] for k in map_keys if k in full_map}
    selected_map_str = json.dumps(selected_map, indent=2, ensure_ascii=False)

    # Build filtered templates markdown
    full_tmpl = load_file_cached(tmpl_path)
    selected_sections = []
    for section in tmpl_sections:
        extracted = _extract_section(full_tmpl, section)
        if extracted:
            selected_sections.append(extracted)
    selected_tmpl_str = "\n\n".join(selected_sections)

    return selected_map_str, selected_tmpl_str


# ---------------------------------------------------------------------------
# Diagnostics helper (used in run_pipeline.py to log token savings)
# ---------------------------------------------------------------------------

def rag_stats(full_map_path: str, full_tmpl_path: str,
              selected_map: str, selected_tmpl: str) -> dict:
    """Return token reduction stats (character proxy for tokens)."""
    full_map_len  = len(load_file_cached(full_map_path))
    full_tmpl_len = len(load_file_cached(full_tmpl_path))
    sel_map_len   = len(selected_map)
    sel_tmpl_len  = len(selected_tmpl)
    total_full    = full_map_len + full_tmpl_len
    total_sel     = sel_map_len + sel_tmpl_len
    return {
        "full_chars":     total_full,
        "selected_chars": total_sel,
        "reduction_pct":  round((1 - total_sel / total_full) * 100, 1) if total_full else 0,
    }


# ---------------------------------------------------------------------------
# apply_function_patches — AST-based function replacement (Fixer cycles 2-3)
#
# The Fixer returns ONLY the corrected functions (not the full script).
# We find each function by name in the original AST and replace its body.
# Robust: no line-number matching, no text diffing, no whitespace sensitivity.
# ---------------------------------------------------------------------------

def apply_function_patches(original_code: str, patched_functions_code: str) -> str:
    """
    Replace named functions in original_code with their corrected versions
    from patched_functions_code.

    original_code          : full Python script (current state)
    patched_functions_code : Python snippet containing only the corrected functions
    Returns                : updated full script as string

    Falls back to returning original_code unchanged if either parse fails,
    so the pipeline never crashes due to a malformed patch.
    """
    if not patched_functions_code or not patched_functions_code.strip():
        return original_code

    try:
        original_tree = ast.parse(original_code)
        patch_tree    = ast.parse(patched_functions_code)
    except SyntaxError:
        return original_code

    # Index corrected functions by name
    patches: dict[str, ast.FunctionDef] = {
        node.name: node
        for node in ast.walk(patch_tree)
        if isinstance(node, ast.FunctionDef)
    }

    if not patches:
        return original_code

    patched_count = 0
    for node in ast.walk(original_tree):
        if isinstance(node, ast.FunctionDef) and node.name in patches:
            p = patches[node.name]
            node.body           = p.body
            node.args           = p.args
            node.decorator_list = p.decorator_list
            node.returns        = p.returns
            patched_count += 1

    # ast.unparse produces valid but compact code; re-add module docstring if present
    result = ast.unparse(original_tree)

    # ast.unparse strips blank lines — restore minimal readability with a pass
    # by re-parsing and unparsing (idempotent after first call)
    return result


def _patches_applied_count(original_code: str, patched_functions_code: str) -> int:
    """Return how many functions were successfully patched (for logging)."""
    if not patched_functions_code:
        return 0
    try:
        patch_tree = ast.parse(patched_functions_code)
        patches = {n.name for n in ast.walk(patch_tree) if isinstance(n, ast.FunctionDef)}
        original_tree = ast.parse(original_code)
        original_fns  = {n.name for n in ast.walk(original_tree) if isinstance(n, ast.FunctionDef)}
        return len(patches & original_fns)
    except SyntaxError:
        return 0


# ---------------------------------------------------------------------------
# inject_docstrings — AST-based docstring injection (Documenter)
#
# The Documenter returns a JSON dict {function_name: docstring_text}.
# We insert each docstring as the first statement of its function body.
# Never rewrites the code logic — only adds string constants.
# ---------------------------------------------------------------------------

def inject_docstrings(code: str, docstrings: dict[str, str]) -> str:
    """
    Inject docstrings into named functions (and optionally the module).

    code        : Python source to annotate
    docstrings  : {"__module__": "...", "function_name": "...", ...}
    Returns     : annotated Python source string

    Keys not found in the AST are silently ignored.
    Falls back to returning code unchanged on parse error.
    """
    if not docstrings:
        return code

    try:
        tree = ast.parse(code)
    except SyntaxError:
        return code

    def _make_docstring_node(text: str) -> ast.Expr:
        return ast.Expr(value=ast.Constant(value=textwrap.dedent(text).strip()))

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            if node.name in docstrings:
                # Remove existing docstring if present before inserting new one
                if (node.body and isinstance(node.body[0], ast.Expr)
                        and isinstance(node.body[0].value, ast.Constant)):
                    node.body.pop(0)
                node.body.insert(0, _make_docstring_node(docstrings[node.name]))

        elif isinstance(node, ast.Module):
            if "__module__" in docstrings:
                if (node.body and isinstance(node.body[0], ast.Expr)
                        and isinstance(node.body[0].value, ast.Constant)):
                    node.body.pop(0)
                node.body.insert(0, _make_docstring_node(docstrings["__module__"]))

    return ast.unparse(tree)
