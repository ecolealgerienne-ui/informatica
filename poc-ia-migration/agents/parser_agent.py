"""
Agent 1 — Parser Agent
Input  : input/wf_clients_dim.xml
Output : output/01_canonical_json/wf_clients_dim.json

Steps:
  1. Parse XML structure deterministically (xml.etree.ElementTree)
  2. Call Claude Code CLI for semantic analysis:
     - Proprietary function detection & Python equivalents
     - Complexity flags per transformation
     - Routing decision (python / pyspark / databricks)
  3. Merge structural data + LLM analysis → canonical JSON
"""

import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

try:
    import sqlglot
    import sqlglot.expressions as exp
    _SQLGLOT_AVAILABLE = True
except ImportError:
    _SQLGLOT_AVAILABLE = False


RAG_MAP_PATH        = Path("rag_base/transformation_map.json")
RAG_COMPLEXITY_PATH = Path("rag_base/complexity_matrix.json")
OUTPUT_DIR   = Path("output/01_canonical_json")


# ---------------------------------------------------------------------------
# XML structural parsing (deterministic — no LLM)
# ---------------------------------------------------------------------------

def _parse_fields(element: ET.Element, tag: str) -> list[dict]:
    fields = []
    for f in element.findall(tag):
        fields.append({
            "name":      f.get("NAME", ""),
            "datatype":  f.get("DATATYPE", ""),
            "port_type": f.get("PORTTYPE", ""),
            "precision": f.get("PRECISION", ""),
            "scale":     f.get("SCALE", "0"),
            "nullable":  f.get("NULLABLE", "NULL"),
            "expression": f.get("EXPRESSION", ""),
        })
    return fields


def _parse_attributes(element: ET.Element) -> dict:
    attrs = {}
    for a in element.findall("TABLEATTRIBUTE"):
        attrs[a.get("NAME", "")] = a.get("VALUE", "")
    return attrs


def _parse_sources(folder: ET.Element, mapping: ET.Element | None = None) -> list[dict]:
    sources = []
    # Form 1: explicit <SOURCE> elements in FOLDER
    for src in folder.findall("SOURCE"):
        sources.append({
            "name":     src.get("NAME") or src.get("BUSINESSNAME"),
            "owner":    src.get("OWNERNAME"),
            "database": src.get("DBDNAME"),
            "db_type":  src.get("DATABASETYPE"),
            "fields":   _parse_fields(src, "SOURCEFIELD"),
        })
    # Form 2: infer sources from Source Qualifier inside MAPPING
    if not sources and mapping is not None:
        seen = set()
        for trf in mapping.findall("TRANSFORMATION"):
            if trf.get("TYPE") != "Source Qualifier":
                continue
            attrs = _parse_attributes(trf)
            table = attrs.get("Source Table", "").strip()
            if table and table not in seen:
                seen.add(table)
                sources.append({
                    "name":     table,
                    "owner":    None,
                    "database": None,
                    "db_type":  "Oracle",
                    "fields":   _parse_fields(trf, "TRANSFORMFIELD"),
                })
    return sources


def _parse_targets(folder: ET.Element, mapping: ET.Element | None = None) -> list[dict]:
    targets = []
    # Form 1: explicit <TARGET> elements in FOLDER
    for tgt in folder.findall("TARGET"):
        targets.append({
            "name":     tgt.get("NAME") or tgt.get("BUSINESSNAME"),
            "owner":    tgt.get("OWNERNAME"),
            "database": tgt.get("DBDNAME"),
            "db_type":  tgt.get("DATABASETYPE"),
            "fields":   _parse_fields(tgt, "TARGETFIELD"),
        })
    # Form 2: <TRANSFORMATION TYPE="Target Definition"> inside MAPPING
    search = mapping if (not targets and mapping is not None) else (folder if not targets else None)
    if search is not None:
        for trf in search.findall("TRANSFORMATION"):
            if trf.get("TYPE") == "Target Definition":
                targets.append({
                    "name":     trf.get("NAME"),
                    "owner":    None,
                    "database": None,
                    "db_type":  "Oracle",
                    "fields":   _parse_fields(trf, "TRANSFORMFIELD"),
                })
    return targets


def _parse_transformations(mapping: ET.Element) -> list[dict]:
    transformations = []
    for t in mapping.findall("TRANSFORMATION"):
        attrs  = _parse_attributes(t)
        ports  = _parse_fields(t, "TRANSFORMFIELD")
        t_type = t.get("TYPE", "")

        entry = {
            "name":    t.get("NAME"),
            "type":    t_type,
            "reusable": t.get("REUSABLE", "NO"),
            "ports":   ports,
            "attributes": attrs,
        }

        if t_type == "Source Qualifier":
            entry["sql_override"]       = attrs.get("Sql Query", "")
            entry["has_sql_override"]   = bool(entry["sql_override"])
            entry["select_distinct"]    = attrs.get("Select Distinct", "NO") == "YES"

        if t_type == "Lookup Procedure":
            entry["ref_table"]          = attrs.get("Lookup table name", "")
            entry["condition"]          = attrs.get("Lookup Condition", "")
            entry["cache_persistent"]   = attrs.get("Lookup Cache Persistent", "NO") == "YES"
            entry["multiple_match"]     = attrs.get("Lookup Policy On Multiple Match", "Return First Row")

        if t_type == "Filter":
            entry["filter_condition"]   = attrs.get("Filter Condition", "")

        if t_type == "Expression":
            entry["computed_fields"] = [
                {"name": p["name"], "expression": p["expression"]}
                for p in ports
                if p["expression"] and p["port_type"] == "OUTPUT"
            ]

        transformations.append(entry)
    return transformations


def _parse_connectors(mapping: ET.Element) -> list[dict]:
    connectors = []
    for c in mapping.findall("CONNECTOR"):
        connectors.append({
            "from_instance": c.get("FROMINSTANCE"),
            "from_field":    c.get("FROMFIELD"),
            "to_instance":   c.get("TOINSTANCE"),
            "to_field":      c.get("TOFIELD"),
        })
    return connectors


def _parse_workflow(folder: ET.Element) -> dict:
    wf = folder.find("WORKFLOW")
    if wf is None:
        return {}
    task = wf.find("TASK")
    session = {}
    if task is not None:
        for attr in task.findall("ATTRIBUTE"):
            session[attr.get("NAME", "")] = attr.get("VALUE", "")
        variables = [
            {"name": vp.get("NAME"), "default": vp.get("VALUE")}
            for vp in task.findall("VALUEPAIR")
        ]
        session["variables"] = variables
        param_file = session.get("Parameter Filename", "")
        session["parameter_file"] = Path(param_file).name if param_file else ""
    return {
        "workflow_name": wf.get("NAME"),
        "server":        wf.get("SERVERNAME"),
        "is_enabled":    wf.get("ISENABLED") == "YES",
        "session":       session,
    }


def parse_xml(xml_path: str) -> dict:
    tree   = ET.parse(xml_path)
    root   = tree.getroot()
    repo   = root.find("REPOSITORY")
    folder = repo.find("FOLDER")
    mapping = folder.find("MAPPING")

    return {
        "mapping_id":       mapping.get("NAME") if mapping is not None else "",
        "folder":           folder.get("NAME"),
        "repository":       repo.get("NAME"),
        "sources":          _parse_sources(folder, mapping),
        "targets":          _parse_targets(folder, mapping),
        "transformations":  _parse_transformations(mapping) if mapping else [],
        "connectors":       _parse_connectors(mapping) if mapping else [],
        "workflow":         _parse_workflow(folder),
    }


# ---------------------------------------------------------------------------
# Build data_flow graph from connectors
# ---------------------------------------------------------------------------

def build_data_flow(connectors: list[dict]) -> list[dict]:
    flow: dict[tuple, list] = {}
    for c in connectors:
        key = (c["from_instance"], c["to_instance"])
        flow.setdefault(key, []).append(c["from_field"])
    return [
        {"from": k[0], "to": k[1], "fields": sorted(set(v))}
        for k, v in flow.items()
    ]


# ---------------------------------------------------------------------------
# SQL analysis — deterministic, via sqlglot (no LLM)
# ---------------------------------------------------------------------------

def analyse_sql(sql: str, dialect: str = "oracle") -> dict:
    """
    Parse a SQL override with sqlglot and extract complexity flags deterministically.
    Returns a structured dict that enriches the LLM prompt and the canonical JSON.
    Falls back gracefully if sqlglot is not installed.
    """
    if not sql or not _SQLGLOT_AVAILABLE:
        return {"available": False, "sql_length": len(sql) if sql else 0}

    try:
        ast = sqlglot.parse_one(sql, read=dialect, error_level=sqlglot.ErrorLevel.WARN)
    except Exception as e:
        return {"available": True, "parse_error": str(e), "sql_length": len(sql)}

    # Detect constructions that map directly to complexity_matrix modifiers
    has_window      = bool(ast.find(exp.Window))
    has_subquery    = bool(ast.find(exp.Subquery))
    has_union       = bool(ast.find(exp.Union))
    has_distinct    = bool(ast.find(exp.Distinct))

    # Oracle-specific functions detected by name
    all_funcs = [f.name.upper() for f in ast.find_all(exp.Anonymous)]
    all_funcs += [f.sql_name().upper() for f in ast.find_all(exp.Func)
                  if hasattr(f, "sql_name")]
    func_set = set(all_funcs)

    has_rownum    = "ROWNUM" in sql.upper()  # ROWNUM is a pseudo-column, not a function
    has_to_date   = "TO_DATE" in func_set or "TO_DATE" in sql.upper()
    has_to_char   = "TO_CHAR" in func_set or "TO_CHAR" in sql.upper()
    has_decode    = "DECODE" in func_set or "DECODE" in sql.upper()
    has_nvl       = "NVL" in func_set or "NVL" in sql.upper()
    has_trunc     = "TRUNC" in func_set or "TRUNC" in sql.upper()

    # Tables referenced
    tables = [t.name for t in ast.find_all(exp.Table) if t.name]

    # Spark SQL transpilation (best-effort)
    spark_sql = None
    try:
        spark_sql = sqlglot.transpile(sql, read=dialect, write="spark")[0]
    except Exception:
        pass

    # Complexity modifiers that map to complexity_matrix.json
    complexity_hints = {
        "has_analytical_functions": has_window,
        "has_subquery":             has_subquery,
        "has_union":                has_union,
        "select_distinct":          has_distinct,
        "has_oracle_rownum":        has_rownum,
        "has_oracle_to_date":       has_to_date,
    }

    return {
        "available":          True,
        "dialect":            dialect,
        "sql_length":         len(sql),
        "tables_referenced":  tables,
        "oracle_functions":   sorted(f for f in [
            "TO_DATE" if has_to_date else None,
            "TO_CHAR" if has_to_char else None,
            "DECODE"  if has_decode  else None,
            "NVL"     if has_nvl     else None,
            "TRUNC"   if has_trunc   else None,
            "ROWNUM"  if has_rownum  else None,
        ] if f),
        "complexity_hints":   complexity_hints,
        "spark_sql":          spark_sql,
    }


# ---------------------------------------------------------------------------
# Claude Code CLI call — semantic analysis
# ---------------------------------------------------------------------------

CLAUDE_PROMPT_TEMPLATE = """
You are an expert Informatica PowerCenter → Python migration analyst.

## Task
The complexity score has ALREADY been computed deterministically. Do NOT recompute it.
Your job is ONLY semantic analysis: identify proprietary functions, Python equivalents,
lookup subtypes, and write the routing rationale.

Return a JSON object with exactly this structure (no markdown, no explanation — raw JSON only):

{{
  "routing_rationale": "<one sentence explaining why this platform was chosen given the transformations>",
  "transformations_analysis": [
    {{
      "name": "<transformation name>",
      "has_proprietary_functions": ["list of Informatica/Oracle proprietary functions used"],
      "python_equivalents": {{"FUNC_NAME": "pandas/numpy equivalent"}},
      "lookup_subtype": "CONNECTED_STATIC|CONNECTED_DYNAMIC|UNCONNECTED|null",
      "notes": "<optional short migration note>"
    }}
  ],
  "oracle_proprietary_functions": ["global list of all Oracle functions found across all transformations"],
  "requires_human_review": ["list transformation names that need expert attention, or empty list"]
}}

## RAG Base — Transformation Map (approved Python equivalents)
{rag_map}

## SQL Pre-Analysis (deterministic — computed by sqlglot, trust these flags)
{sql_analysis}

## Mapping Data to Analyse
{mapping_data}
"""


MODEL = "claude-haiku-4-5-20251001"  # semantic analysis only — lightweight model sufficient


# ---------------------------------------------------------------------------
# Deterministic complexity scorer (no LLM)
# ---------------------------------------------------------------------------

def _detect_expression_flags(t: dict) -> dict:
    fields = t.get("computed_fields", [])
    n = len(fields)
    exprs = " ".join(f.get("expression", "") for f in fields).upper()
    return {
        "computed_fields_1_3":    n <= 3,
        "computed_fields_4_8":    4 <= n <= 8,
        "computed_fields_9_plus": n >= 9,
        "has_datediff":           "DATEDIFF" in exprs,
        "has_iif_nested":         exprs.count("IIF(") >= 2,
        "has_decode_complex":     "DECODE(" in exprs,
        "has_java_expression":    "JAVA(" in exprs or "JavaExpression" in exprs,
        "has_string_aggregation": any(f in exprs for f in ("STRING_AGG", "LISTAGG")),
    }


def _detect_filter_flags(t: dict) -> dict:
    cond = (t.get("filter_condition") or t.get("condition", "")).upper()
    n_and_or = cond.count(" AND ") + cond.count(" OR ")
    has_sub = "SELECT " in cond
    return {
        "simple_condition":    not has_sub and n_and_or == 0,
        "compound_condition":  not has_sub and n_and_or >= 1,
        "subquery_condition":  has_sub,
    }


def _detect_lookup_flags(t: dict) -> dict:
    name = (t.get("name") or "").upper()
    is_unconnected = name.startswith("LKP_UNCON") or ":LKP." in name
    cache = t.get("cache_persistent", False)
    match = (t.get("multiple_match") or "").lower()
    return {
        "lookup_unconnected":      is_unconnected,
        "lookup_connected_static": not is_unconnected,
        "lookup_connected_dynamic": False,
        "multiple_match_error":    "error" in match,
        "multiple_match_last_row": "last" in match,
        "cache_persistent":        cache,
        "cross_db_lookup":         False,
    }


def _detect_sq_flags(t: dict, sql_hints: dict) -> dict:
    hints = sql_hints.get("complexity_hints", {})
    return {
        "has_sql_override":         t.get("has_sql_override", False),
        "has_oracle_rownum":        hints.get("has_oracle_rownum", False),
        "has_oracle_to_date":       hints.get("has_oracle_to_date", False),
        "has_subquery":             hints.get("has_subquery", False),
        "has_union":                hints.get("has_union", False),
        "has_analytical_functions": hints.get("has_analytical_functions", False),
        "select_distinct":          t.get("select_distinct", False),
    }


def score_transformation(t: dict, matrix: dict, sql_analyses: dict) -> tuple[int, dict]:
    t_type = t.get("type", "")
    t_scores = matrix.get("transformation_scores", {})
    entry = t_scores.get(t_type)
    if entry is None:
        return 0, {}

    base = entry.get("base_score", 0)
    modifiers = entry.get("modifiers", {})
    breakdown = {}

    # Detect flags per transformation type
    if t_type == "Source Qualifier":
        sql_hints = sql_analyses.get(t.get("name", ""), {})
        flags = _detect_sq_flags(t, sql_hints)
    elif t_type == "Expression":
        flags = _detect_expression_flags(t)
    elif t_type == "Filter":
        flags = _detect_filter_flags(t)
    elif t_type == "Lookup Procedure":
        flags = _detect_lookup_flags(t)
    else:
        flags = {}

    total = base
    for mod_key, mod_def in modifiers.items():
        if flags.get(mod_key):
            s = mod_def.get("score", 0)
            if s > 0:
                breakdown[mod_key] = s
                total += s

    return total, breakdown


def compute_complexity(structural: dict, matrix: dict, sql_analyses: dict) -> dict:
    transformations = structural.get("transformations", [])
    targets = structural.get("targets", [])
    session = structural.get("workflow", {}).get("session", {})
    global_mods = matrix.get("global_modifiers", {})
    thresholds = matrix.get("thresholds", {})
    routing_rules = matrix.get("routing_rules", {})

    t_scores = {}
    t_breakdowns = {}
    for t in transformations:
        score, breakdown = score_transformation(t, matrix, sql_analyses)
        t_scores[t["name"]] = score
        t_breakdowns[t["name"]] = breakdown

    # Global modifiers
    n_transfo = len(transformations)
    has_param = bool(session.get("parameter_file") or session.get("variables"))
    has_multiple_targets = len(targets) > 1
    has_java = any(t.get("type") == "Java Transformation" for t in transformations)
    has_custom = any(t.get("type") == "Custom Transformation" for t in transformations)
    has_sq_override = any(t.get("has_sql_override") for t in transformations)

    global_scores = {}
    if has_param and global_mods.get("has_parameter_file"):
        global_scores["has_parameter_file"] = global_mods["has_parameter_file"]["score"]
    if 5 <= n_transfo <= 10 and global_mods.get("transformation_count_5_10"):
        global_scores["transformation_count_5_10"] = global_mods["transformation_count_5_10"]["score"]
    if n_transfo >= 11 and global_mods.get("transformation_count_11_plus"):
        global_scores["transformation_count_11_plus"] = global_mods["transformation_count_11_plus"]["score"]
    if has_multiple_targets and global_mods.get("has_multiple_targets"):
        global_scores["has_multiple_targets"] = global_mods["has_multiple_targets"]["score"]

    total = sum(t_scores.values()) + sum(global_scores.values())

    # Flag from thresholds
    flag = "CRITICAL"
    for fname, fdef in thresholds.items():
        if fdef["min"] <= total <= fdef["max"]:
            flag = fname
            break

    th = thresholds.get(flag, {})
    days = th.get("migration_days", ">5")
    auto = th.get("auto_conversion", False)

    # Routing platform
    if has_java or has_custom or total >= 15:
        platform = "databricks"
        feasibility = "LOW"
    elif total >= 9:
        platform = "pyspark"
        feasibility = "MEDIUM"
    else:
        platform = "python"
        feasibility = "HIGH"

    return {
        "complexity": {
            "total_score": total,
            "flag": flag,
            "estimated_migration_days": days,
            "auto_conversion": auto,
            "score_breakdown": {
                "transformations": t_scores,
                "global_modifiers": global_scores,
            },
        },
        "per_transformation": {
            name: {"score": t_scores[name], "breakdown": t_breakdowns[name]}
            for name in t_scores
        },
        "routing": {
            "target_platform": platform,
            "auto_conversion_feasibility": feasibility,
            "human_intervention_required": has_java or has_custom or total >= 9,
        },
        "global_flags": {
            "has_java_transformation": has_java,
            "has_dynamic_lookup": False,
            "has_custom_function": has_custom,
            "has_parameter_file": has_param,
            "has_sql_override": has_sq_override,
        },
    }


def call_claude(prompt: str) -> str:
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


def extract_json(raw: str) -> dict:
    raw = raw.strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        raw = "\n".join(
            line for line in lines
            if not line.startswith("```")
        )
    start = raw.find("{")
    end   = raw.rfind("}") + 1
    if start == -1 or end == 0:
        raise ValueError(f"No JSON object found in Claude response:\n{raw[:500]}")
    return json.loads(raw[start:end])


def analyse_with_claude(structural: dict) -> tuple[dict, dict, dict]:
    rag_map    = json.loads(RAG_MAP_PATH.read_text(encoding="utf-8"))
    complexity = json.loads(RAG_COMPLEXITY_PATH.read_text(encoding="utf-8"))

    # Step 1 — deterministic SQL analysis (sqlglot)
    sql_analyses = {}
    for t in structural["transformations"]:
        if t.get("type") == "Source Qualifier" and t.get("sql_override"):
            result = analyse_sql(t["sql_override"], dialect="oracle")
            sql_analyses[t["name"]] = result
            if result.get("available"):
                hints = result.get("complexity_hints", {})
                print(f"[Parser] sqlglot — {t['name']}: "
                      f"window={hints.get('has_analytical_functions')}, "
                      f"subquery={hints.get('has_subquery')}, "
                      f"union={hints.get('has_union')}, "
                      f"oracle_funcs={result.get('oracle_functions', [])}")

    # Step 2 — deterministic complexity scoring (no LLM)
    det = compute_complexity(structural, complexity, sql_analyses)
    print(f"[Parser] Deterministic score: {det['complexity']['total_score']} "
          f"({det['complexity']['flag']}) → {det['routing']['target_platform']}")

    # Step 3 — LLM for semantic analysis only (proprietary functions, equivalents, rationale)
    mapping_summary = {
        "mapping_id":          structural["mapping_id"],
        "complexity_computed": det["complexity"],
        "routing_computed":    det["routing"],
        "transformations": [
            {
                "name":             t["name"],
                "type":             t["type"],
                "sql_override":     t.get("sql_override", ""),
                "filter_condition": t.get("filter_condition", ""),
                "computed_fields":  t.get("computed_fields", []),
                "ref_table":        t.get("ref_table", ""),
                "condition":        t.get("condition", ""),
            }
            for t in structural["transformations"]
        ],
        "workflow": structural["workflow"],
    }

    prompt = CLAUDE_PROMPT_TEMPLATE.format(
        rag_map=json.dumps(rag_map, indent=2),
        sql_analysis=json.dumps(sql_analyses, indent=2) if sql_analyses else "{}",
        mapping_data=json.dumps(mapping_summary, indent=2),
    )

    print("[Parser] Calling Claude Code for semantic analysis (functions, equivalents, rationale)...")
    raw = call_claude(prompt)
    return extract_json(raw), sql_analyses, det


# ---------------------------------------------------------------------------
# Merge structural + LLM analysis → canonical JSON
# ---------------------------------------------------------------------------

def merge_results(structural: dict, llm: dict, sql_analyses: dict, det: dict) -> dict:
    sql_analyses = sql_analyses or {}
    t_analysis = {t["name"]: t for t in llm.get("transformations_analysis", [])}
    per_t = det.get("per_transformation", {})

    # Thresholds for per-transformation flag
    def t_flag(score: int) -> str:
        if score <= 2:   return "LOW"
        if score <= 5:   return "MEDIUM"
        if score <= 8:   return "HIGH"
        return "CRITICAL"

    transformations = []
    for t in structural["transformations"]:
        analysis  = t_analysis.get(t["name"], {})
        det_t     = per_t.get(t["name"], {})
        det_score = det_t.get("score", 0)

        merged = {
            "name":                    t["name"],
            "type":                    t["type"],
            # Scores come exclusively from the deterministic scorer
            "complexity_flag":         t_flag(det_score),
            "complexity_score":        det_score,
            "score_breakdown":         det_t.get("breakdown", {}),
            # Semantic fields come from LLM
            "has_proprietary_functions": analysis.get("has_proprietary_functions", []),
            "python_equivalents":      analysis.get("python_equivalents", {}),
            "ports":                   t["ports"],
        }
        if t["type"] == "Source Qualifier":
            merged["has_sql_override"] = t.get("has_sql_override", False)
            merged["sql_override"]     = t.get("sql_override", "")
            merged["sql_dialect"]      = "oracle"
            if t["name"] in sql_analyses:
                merged["sql_analysis"] = sql_analyses[t["name"]]
        if t["type"] == "Lookup Procedure":
            merged["lookup_subtype"]   = analysis.get("lookup_subtype", "CONNECTED_STATIC")
            merged["ref_table"]        = t.get("ref_table", "")
            merged["condition"]        = t.get("condition", "")
            merged["cache_persistent"] = t.get("cache_persistent", False)
        if t["type"] == "Filter":
            merged["condition"]        = t.get("filter_condition", "")
        if t["type"] == "Expression":
            merged["computed_fields"]  = t.get("computed_fields", [])
        if analysis.get("notes"):
            merged["notes"]            = analysis["notes"]
        transformations.append(merged)

    wf = structural["workflow"]
    session_vars = wf.get("session", {}).get("variables", [])

    # routing_decision: deterministic platform + LLM rationale
    routing = det["routing"].copy()
    routing["rationale"] = llm.get("routing_rationale", "")

    # global_flags: deterministic booleans + LLM semantic lists
    flags = det["global_flags"].copy()
    flags["oracle_proprietary_functions"] = llm.get("oracle_proprietary_functions", [])
    flags["requires_human_review"]        = llm.get("requires_human_review", [])

    return {
        "workflow_id":         wf.get("workflow_name", ""),
        "mapping_id":          structural["mapping_id"],
        "parsed_at":           datetime.now(timezone.utc).isoformat(),
        "routing_decision":    routing,
        "workflow_complexity": det["complexity"],
        "session": {
            "parameter_file": wf.get("session", {}).get("parameter_file", ""),
            "variables":      session_vars,
        },
        "sources":             structural["sources"],
        "targets":             structural["targets"],
        "transformations":     transformations,
        "data_flow":           build_data_flow(structural["connectors"]),
        "flags":               flags,
    }


# ---------------------------------------------------------------------------
# ParserAgent class
# ---------------------------------------------------------------------------

class ParserAgent:
    def __init__(self, xml_path: str):
        self.xml_path  = xml_path
        self.workflow_name = Path(xml_path).stem  # e.g. "wf_products_dim"

    def run(self) -> dict:
        print(f"[Parser] Parsing XML: {self.xml_path}")
        structural = parse_xml(self.xml_path)
        print(f"[Parser] Found {len(structural['transformations'])} transformations, "
              f"{len(structural['connectors'])} connectors")

        llm_analysis, sql_analyses, det = analyse_with_claude(structural)

        canonical = merge_results(structural, llm_analysis, sql_analyses, det)

        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        output_path = OUTPUT_DIR / f"{self.workflow_name}.json"
        output_path.write_text(
            json.dumps(canonical, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"[Parser] Canonical JSON written to {output_path}")
        return canonical, str(output_path)


# ---------------------------------------------------------------------------
# Standalone run
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    xml_file = sys.argv[1] if len(sys.argv) > 1 else "input/wf_clients_dim.xml"
    agent = ParserAgent(xml_file)
    result, out_path = agent.run()
    print(f"\n[Parser] routing → {result['routing_decision'].get('target_platform')}")
    print(f"[Parser] feasibility → {result['routing_decision'].get('auto_conversion_feasibility')}")
    print(f"[Parser] transformations analysed: {len(result['transformations'])}")
    print(f"[Parser] output → {out_path}")
