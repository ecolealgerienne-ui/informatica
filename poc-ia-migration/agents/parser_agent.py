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


def _parse_sources(folder: ET.Element) -> list[dict]:
    sources = []
    for src in folder.findall("SOURCE"):
        sources.append({
            "name":     src.get("NAME"),
            "owner":    src.get("OWNERNAME"),
            "database": src.get("DBDNAME"),
            "db_type":  src.get("DATABASETYPE"),
            "fields":   _parse_fields(src, "SOURCEFIELD"),
        })
    return sources


def _parse_targets(folder: ET.Element) -> list[dict]:
    targets = []
    for tgt in folder.findall("TARGET"):
        targets.append({
            "name":     tgt.get("NAME"),
            "owner":    tgt.get("OWNERNAME"),
            "database": tgt.get("DBDNAME"),
            "db_type":  tgt.get("DATABASETYPE"),
            "fields":   _parse_fields(tgt, "TARGETFIELD"),
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
        "sources":          _parse_sources(folder),
        "targets":          _parse_targets(folder),
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
Analyse the Informatica mapping data below and return a JSON object with exactly this structure (no markdown, no explanation — raw JSON only):

{{
  "routing_decision": {{
    "target_platform": "python|pyspark|databricks",
    "rationale": "<one sentence>",
    "auto_conversion_feasibility": "HIGH|MEDIUM|LOW",
    "human_intervention_required": true|false
  }},
  "transformations_analysis": [
    {{
      "name": "<transformation name>",
      "complexity_flag": "LOW|MEDIUM|HIGH|CRITICAL",
      "complexity_score": <integer computed from the scoring matrix>,
      "score_breakdown": {{"<modifier_key>": <score>, ...}},
      "has_proprietary_functions": ["list", "of", "functions"],
      "python_equivalents": {{"FUNC": "pandas equivalent"}},
      "lookup_subtype": "CONNECTED_STATIC|CONNECTED_DYNAMIC|UNCONNECTED|null",
      "notes": "<optional short note>"
    }}
  ],
  "workflow_complexity": {{
    "total_score": <sum of all transformation scores + global modifiers>,
    "flag": "LOW|MEDIUM|HIGH|CRITICAL",
    "estimated_migration_days": "<range from thresholds>",
    "auto_conversion": true|false,
    "score_breakdown": {{
      "transformations": {{"<name>": <score>}},
      "global_modifiers": {{"<modifier>": <score>}}
    }}
  }},
  "global_flags": {{
    "has_java_transformation": false,
    "has_dynamic_lookup": false,
    "has_custom_function": false,
    "has_parameter_file": true|false,
    "has_sql_override": true|false,
    "oracle_proprietary_functions": ["list"],
    "requires_human_review": []
  }}
}}

## RAG Base — Transformation Map (approved Python equivalents)
{rag_map}

## RAG Base — Complexity Scoring Matrix (USE THIS to compute all scores)
{complexity_matrix}

## SQL Pre-Analysis (deterministic — computed by sqlglot, trust these flags)
{sql_analysis}

## Mapping Data to Analyse
{mapping_data}
"""


MODEL = "claude-haiku-4-5-20251001"  # classification task — lightweight model sufficient


def call_claude(prompt: str) -> str:
    result = subprocess.run(
        ["claude", "-p", "--model", MODEL, "--output-format", "text"],
        input=prompt,
        capture_output=True,
        text=True,
        timeout=180,
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


def analyse_with_claude(structural: dict) -> dict:
    rag_map    = json.loads(RAG_MAP_PATH.read_text(encoding="utf-8"))
    complexity = json.loads(RAG_COMPLEXITY_PATH.read_text(encoding="utf-8"))

    # Run sqlglot analysis on every Source Qualifier with a SQL override
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

    mapping_summary = {
        "mapping_id":      structural["mapping_id"],
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
        complexity_matrix=json.dumps(complexity, indent=2),
        sql_analysis=json.dumps(sql_analyses, indent=2) if sql_analyses else "{}",
        mapping_data=json.dumps(mapping_summary, indent=2),
    )

    print("[Parser] Calling Claude Code for semantic analysis + complexity scoring...")
    raw = call_claude(prompt)
    return extract_json(raw), sql_analyses


# ---------------------------------------------------------------------------
# Merge structural + LLM analysis → canonical JSON
# ---------------------------------------------------------------------------

def merge_results(structural: dict, llm: dict, sql_analyses: dict = None) -> dict:
    sql_analyses = sql_analyses or {}
    t_analysis = {t["name"]: t for t in llm.get("transformations_analysis", [])}

    transformations = []
    for t in structural["transformations"]:
        analysis = t_analysis.get(t["name"], {})
        merged = {
            "name":                    t["name"],
            "type":                    t["type"],
            "complexity_flag":         analysis.get("complexity_flag", "MEDIUM"),
            "complexity_score":        analysis.get("complexity_score", 0),
            "score_breakdown":         analysis.get("score_breakdown", {}),
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

    return {
        "workflow_id":        wf.get("workflow_name", ""),
        "mapping_id":         structural["mapping_id"],
        "parsed_at":          datetime.now(timezone.utc).isoformat(),
        "routing_decision":   llm.get("routing_decision", {}),
        "workflow_complexity": llm.get("workflow_complexity", {}),
        "session": {
            "parameter_file": wf.get("session", {}).get("parameter_file", ""),
            "variables":      session_vars,
        },
        "sources":            structural["sources"],
        "targets":            structural["targets"],
        "transformations":    transformations,
        "data_flow":          build_data_flow(structural["connectors"]),
        "flags":              llm.get("global_flags", {}),
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

        llm_analysis, sql_analyses = analyse_with_claude(structural)

        canonical = merge_results(structural, llm_analysis, sql_analyses)

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
