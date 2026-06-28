"""
Phase 1 Reporter — génère un rapport HTML interactif depuis les Canonical JSON.

Structure de sortie :
  output/phase1_report/
    index.html              ← dashboard projet
    wf_<name>.html          ← fiche détaillée par workflow

Usage (depuis poc-ia-migration/) :
    python agents/phase1_reporter.py
    python agents/phase1_reporter.py --project "Projet ACME" --input output/01_canonical_json
"""

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

FLAG_COLORS = {
    "LOW":      {"bg": "#d1fae5", "border": "#10b981", "text": "#065f46", "badge": "#10b981"},
    "MEDIUM":   {"bg": "#fef3c7", "border": "#f59e0b", "text": "#78350f", "badge": "#f59e0b"},
    "HIGH":     {"bg": "#fee2e2", "border": "#ef4444", "text": "#7f1d1d", "badge": "#ef4444"},
    "CRITICAL": {"bg": "#f5d0fe", "border": "#a21caf", "text": "#4a044e", "badge": "#a21caf"},
}

PLATFORM_LABELS = {
    "python":     "Python / pandas",
    "pyspark":    "PySpark",
    "databricks": "Databricks",
}

FEASIBILITY_LABELS = {
    "HIGH":   ("Conversion automatique", "#10b981"),
    "MEDIUM": ("Supervision recommandée", "#f59e0b"),
    "LOW":    ("Intervention experte", "#ef4444"),
}

# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_canonical_jsons(input_dir: Path) -> list[dict]:
    workflows = []
    for p in sorted(input_dir.glob("*.json")):
        data = json.loads(p.read_text(encoding="utf-8"))
        data["_filename"] = p.stem
        workflows.append(data)
    return workflows


def summarize_project(workflows: list[dict]) -> dict:
    flag_counts = {"LOW": 0, "MEDIUM": 0, "HIGH": 0, "CRITICAL": 0}
    platform_counts = {}
    total_score = 0
    total_transfo = 0

    for wf in workflows:
        c = wf.get("workflow_complexity", {})
        flag = c.get("flag", "MEDIUM")
        flag_counts[flag] = flag_counts.get(flag, 0) + 1
        total_score += c.get("total_score", 0)

        platform = wf.get("routing_decision", {}).get("target_platform", "python")
        platform_counts[platform] = platform_counts.get(platform, 0) + 1

        total_transfo += len(wf.get("transformations", []))

    avg_score = round(total_score / len(workflows), 1) if workflows else 0

    return {
        "count": len(workflows),
        "flag_counts": flag_counts,
        "platform_counts": platform_counts,
        "avg_score": avg_score,
        "total_transformations": total_transfo,
    }


# ---------------------------------------------------------------------------
# Common CSS / JS
# ---------------------------------------------------------------------------

COMMON_CSS = """
:root {
  --gray-50: #f9fafb;
  --gray-100: #f3f4f6;
  --gray-200: #e5e7eb;
  --gray-300: #d1d5db;
  --gray-500: #6b7280;
  --gray-700: #374151;
  --gray-900: #111827;
  --blue-50: #eff6ff;
  --blue-600: #2563eb;
  --blue-700: #1d4ed8;
  --font: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: var(--font); background: var(--gray-50); color: var(--gray-900); font-size: 14px; line-height: 1.6; }
h1 { font-size: 1.75rem; font-weight: 700; }
h2 { font-size: 1.25rem; font-weight: 600; margin-bottom: 1rem; }
h3 { font-size: 1rem; font-weight: 600; margin-bottom: 0.5rem; }
.container { max-width: 1200px; margin: 0 auto; padding: 2rem 1.5rem; }
.header { background: #1e293b; color: white; padding: 1.5rem 0; margin-bottom: 2rem; }
.header .container { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 1rem; }
.header-meta { font-size: 0.8rem; opacity: 0.7; margin-top: 0.25rem; }
.badge {
  display: inline-block; padding: 0.2rem 0.6rem; border-radius: 9999px;
  font-size: 0.72rem; font-weight: 700; letter-spacing: 0.05em; text-transform: uppercase;
}
.card { background: white; border-radius: 0.75rem; border: 1px solid var(--gray-200); padding: 1.5rem; margin-bottom: 1.5rem; }
.card-title { font-size: 0.75rem; font-weight: 600; color: var(--gray-500); text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 0.25rem; }
.card-value { font-size: 2rem; font-weight: 700; line-height: 1.2; }
.card-sub { font-size: 0.8rem; color: var(--gray-500); margin-top: 0.25rem; }
.grid-4 { display: grid; grid-template-columns: repeat(4, 1fr); gap: 1rem; margin-bottom: 1.5rem; }
.grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 1.5rem; margin-bottom: 1.5rem; }
@media (max-width: 900px) { .grid-4 { grid-template-columns: repeat(2, 1fr); } }
@media (max-width: 600px) { .grid-4 { grid-template-columns: 1fr 1fr; } .grid-2 { grid-template-columns: 1fr; } }
table { width: 100%; border-collapse: collapse; }
th { background: var(--gray-50); padding: 0.6rem 0.75rem; text-align: left; font-size: 0.72rem; font-weight: 600; color: var(--gray-500); text-transform: uppercase; letter-spacing: 0.05em; border-bottom: 1px solid var(--gray-200); }
td { padding: 0.75rem; border-bottom: 1px solid var(--gray-100); vertical-align: middle; }
tr:last-child td { border-bottom: none; }
tr:hover td { background: var(--gray-50); }
a { color: var(--blue-600); text-decoration: none; font-weight: 500; }
a:hover { color: var(--blue-700); text-decoration: underline; }
.btn-back { display: inline-flex; align-items: center; gap: 0.4rem; background: white; border: 1px solid var(--gray-200); border-radius: 0.5rem; padding: 0.5rem 1rem; font-size: 0.85rem; color: var(--gray-700); font-weight: 500; margin-bottom: 1.5rem; }
.btn-back:hover { background: var(--gray-50); text-decoration: none; }
.section-label { font-size: 0.7rem; font-weight: 700; color: var(--gray-500); text-transform: uppercase; letter-spacing: 0.08em; margin-bottom: 0.75rem; }
.pill { display: inline-flex; align-items: center; gap: 0.3rem; padding: 0.15rem 0.5rem; border-radius: 0.35rem; font-size: 0.75rem; font-weight: 500; }
.info-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 1rem; }
@media (max-width: 700px) { .info-grid { grid-template-columns: 1fr 1fr; } }
.divider { border: none; border-top: 1px solid var(--gray-200); margin: 1.5rem 0; }
.tag { display: inline-block; background: var(--gray-100); color: var(--gray-700); border-radius: 0.25rem; padding: 0.1rem 0.4rem; font-size: 0.72rem; font-family: monospace; margin: 0.1rem; }
.progress-bar { height: 8px; border-radius: 4px; background: var(--gray-200); overflow: hidden; }
.progress-fill { height: 100%; border-radius: 4px; transition: width 0.5s; }
.alert { padding: 0.75rem 1rem; border-radius: 0.5rem; border-left: 4px solid; margin-bottom: 1rem; font-size: 0.85rem; }
.alert-warning { background: #fffbeb; border-color: #f59e0b; color: #78350f; }
.alert-info { background: var(--blue-50); border-color: var(--blue-600); color: #1e40af; }
.footer { text-align: center; font-size: 0.75rem; color: var(--gray-500); padding: 2rem 0; border-top: 1px solid var(--gray-200); margin-top: 2rem; }

/* Data flow modal */
.df-container { position: relative; overflow-x: auto; padding: 0.5rem 0; cursor: pointer; }
.df-expand-btn {
  position: absolute; top: 0.5rem; right: 0.5rem; z-index: 2;
  background: white; border: 1px solid var(--gray-200); border-radius: 0.4rem;
  padding: 0.3rem 0.7rem; font-size: 0.75rem; font-weight: 600; color: var(--gray-700);
  cursor: pointer; display: flex; align-items: center; gap: 0.3rem;
  box-shadow: 0 1px 3px rgba(0,0,0,0.1);
}
.df-expand-btn:hover { background: var(--gray-50); }
.df-modal-overlay {
  display: none; position: fixed; inset: 0; z-index: 1000;
  background: rgba(0,0,0,0.75); align-items: center; justify-content: center;
}
.df-modal-overlay.open { display: flex; }
.df-modal-inner {
  position: relative; background: white; border-radius: 0.75rem;
  width: 95vw; height: 90vh; overflow: hidden;
  display: flex; flex-direction: column;
}
.df-modal-header {
  display: flex; align-items: center; justify-content: space-between;
  padding: 0.75rem 1rem; border-bottom: 1px solid var(--gray-200);
  flex-shrink: 0;
}
.df-modal-title { font-weight: 600; font-size: 0.9rem; color: var(--gray-700); }
.df-modal-controls { display: flex; align-items: center; gap: 0.5rem; }
.df-btn {
  background: var(--gray-100); border: 1px solid var(--gray-200); border-radius: 0.35rem;
  padding: 0.3rem 0.65rem; font-size: 0.8rem; font-weight: 600; color: var(--gray-700);
  cursor: pointer; line-height: 1;
}
.df-btn:hover { background: var(--gray-200); }
.df-close-btn {
  background: none; border: none; cursor: pointer; font-size: 1.2rem;
  color: var(--gray-500); padding: 0.2rem 0.4rem; border-radius: 0.25rem;
}
.df-close-btn:hover { color: var(--gray-900); background: var(--gray-100); }
.df-modal-canvas {
  flex: 1; overflow: hidden; position: relative; cursor: grab;
}
.df-modal-canvas.grabbing { cursor: grabbing; }
.df-modal-svg-wrap {
  position: absolute; top: 0; left: 0;
  transform-origin: 0 0;
  will-change: transform;
}
.df-hint { font-size: 0.7rem; color: var(--gray-500); padding: 0.25rem 1rem; border-top: 1px solid var(--gray-100); text-align: center; flex-shrink: 0; }
"""

# ---------------------------------------------------------------------------
# Dashboard (index.html)
# ---------------------------------------------------------------------------

def _donut_svg(flag_counts: dict, size: int = 140) -> str:
    colors = {
        "LOW": "#10b981", "MEDIUM": "#f59e0b",
        "HIGH": "#ef4444", "CRITICAL": "#a21caf"
    }
    total = sum(flag_counts.values()) or 1
    cx = cy = size / 2
    r = size / 2 - 16
    stroke_w = 22

    parts = []
    offset = 0
    circ = 2 * math.pi * r
    for flag in ["LOW", "MEDIUM", "HIGH", "CRITICAL"]:
        count = flag_counts.get(flag, 0)
        if count == 0:
            continue
        frac = count / total
        dash = frac * circ
        parts.append(
            f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" '
            f'stroke="{colors[flag]}" stroke-width="{stroke_w}" '
            f'stroke-dasharray="{dash:.2f} {circ:.2f}" '
            f'stroke-dashoffset="-{offset * circ:.2f}" '
            f'transform="rotate(-90 {cx} {cy})" />'
        )
        offset += frac

    dominant = max(flag_counts, key=lambda k: flag_counts[k]) if flag_counts else "?"
    return f"""
<svg width="{size}" height="{size}" viewBox="0 0 {size} {size}">
  {chr(10).join(parts)}
  <text x="{cx}" y="{cy - 6}" text-anchor="middle" font-size="18" font-weight="700" fill="#111827">{total}</text>
  <text x="{cx}" y="{cy + 14}" text-anchor="middle" font-size="9" fill="#6b7280" text-transform="uppercase">workflows</text>
</svg>"""


def _flag_badge(flag: str) -> str:
    c = FLAG_COLORS.get(flag, FLAG_COLORS["MEDIUM"])
    return (f'<span class="badge" style="background:{c["bg"]};color:{c["text"]};'
            f'border:1px solid {c["border"]}">{flag}</span>')


def _platform_badge(platform: str) -> str:
    colors = {"python": "#3b82f6", "pyspark": "#8b5cf6", "databricks": "#f97316"}
    color = colors.get(platform, "#6b7280")
    label = PLATFORM_LABELS.get(platform, platform)
    return f'<span class="pill" style="background:{color}20;color:{color}">{label}</span>'


def _feasibility_badge(feasibility: str) -> str:
    label, color = FEASIBILITY_LABELS.get(feasibility, (feasibility, "#6b7280"))
    return f'<span class="pill" style="background:{color}20;color:{color}">{label}</span>'


def generate_index(workflows: list[dict], project_name: str, output_dir: Path) -> None:
    summary = summarize_project(workflows)
    now = datetime.now(timezone.utc).strftime("%d/%m/%Y à %H:%M UTC")

    # Stat cards (2 KPIs: workflows count + average score)
    stat_cards = []
    kpis = [
        ("Workflows analysés", str(summary["count"]), f"{summary['total_transformations']} transformations"),
        ("Score moyen", str(summary["avg_score"]), "complexité (max observé: 35)"),
    ]
    for title, value, sub in kpis:
        stat_cards.append(f"""
        <div class="card" style="padding:1.25rem">
          <div class="card-title">{title}</div>
          <div class="card-value">{value}</div>
          <div class="card-sub">{sub}</div>
        </div>""")

    # Workflow table rows
    table_rows = []
    for wf in sorted(workflows, key=lambda w: -w.get("workflow_complexity", {}).get("total_score", 0)):
        fname = wf["_filename"]
        c = wf.get("workflow_complexity", {})
        r = wf.get("routing_decision", {})
        flag = c.get("flag", "MEDIUM")
        score = c.get("total_score", 0)
        days = c.get("estimated_migration_days", "—")
        platform = r.get("target_platform", "python")
        feasibility = r.get("auto_conversion_feasibility", "MEDIUM")
        n_transfo = len(wf.get("transformations", []))
        sources = ", ".join(s.get("name", "") for s in wf.get("sources", []))
        wf_id = wf.get("workflow_id", fname)

        table_rows.append(f"""
        <tr>
          <td><a href="{fname}.html" title="{wf_id}">{fname.replace('wf_','').replace('_',' ').title()}</a><br>
              <span style="font-size:0.7rem;color:#6b7280;font-family:monospace">{wf_id}</span></td>
          <td>{_flag_badge(flag)} <strong style="margin-left:0.4rem">{score}</strong></td>
          <td>{_platform_badge(platform)}</td>
          <td>{days} j</td>
          <td>{_feasibility_badge(feasibility)}</td>
          <td style="font-size:0.8rem;color:#6b7280">{n_transfo}</td>
          <td style="font-size:0.75rem;color:#6b7280;max-width:180px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">{sources}</td>
        </tr>""")

    # Distribution legend
    legend_items = []
    for flag, cnt in sorted(summary["flag_counts"].items(), key=lambda x: ["LOW","MEDIUM","HIGH","CRITICAL"].index(x[0])):
        if cnt == 0:
            continue
        c = FLAG_COLORS[flag]
        pct = int(100 * cnt / summary["count"]) if summary["count"] else 0
        legend_items.append(f"""
        <div style="display:flex;align-items:center;gap:0.5rem;margin-bottom:0.5rem">
          <div style="width:12px;height:12px;border-radius:2px;background:{c['badge']};flex-shrink:0"></div>
          <span style="font-size:0.8rem;color:#374151;font-weight:600">{flag}</span>
          <span style="font-size:0.8rem;color:#6b7280">{cnt} workflow{'s' if cnt>1 else ''} ({pct}%)</span>
        </div>""")

    # Platform breakdown
    platform_items = []
    for plat, cnt in sorted(summary["platform_counts"].items(), key=lambda x: -x[1]):
        label = PLATFORM_LABELS.get(plat, plat)
        pct = int(100 * cnt / summary["count"]) if summary["count"] else 0
        colors_p = {"python": "#3b82f6", "pyspark": "#8b5cf6", "databricks": "#f97316"}
        color = colors_p.get(plat, "#6b7280")
        platform_items.append(f"""
        <div style="margin-bottom:0.75rem">
          <div style="display:flex;justify-content:space-between;margin-bottom:0.25rem">
            <span style="font-size:0.8rem;font-weight:600;color:{color}">{label}</span>
            <span style="font-size:0.8rem;color:#6b7280">{cnt} ({pct}%)</span>
          </div>
          <div class="progress-bar">
            <div class="progress-fill" style="width:{pct}%;background:{color}"></div>
          </div>
        </div>""")

    html = f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{project_name} — Inventaire Phase 1</title>
<style>
{COMMON_CSS}
.hero-title {{ font-size:1.5rem;font-weight:700;line-height:1.3 }}
.grid-2-kpi {{ display:grid;grid-template-columns:repeat(2,1fr);gap:1rem;margin-bottom:1.5rem }}
@media (max-width:600px) {{ .grid-2-kpi {{ grid-template-columns:1fr }} }}
</style>
</head>
<body>

<div class="header">
  <div class="container">
    <div>
      <div class="hero-title">{project_name}</div>
      <div style="color:#94a3b8;font-size:0.85rem;margin-top:0.25rem">Rapport Phase 1 — Analyse &amp; Inventaire Informatica PowerCenter</div>
    </div>
    <div style="text-align:right">
      <div style="font-size:0.75rem;color:#94a3b8">Généré le {now}</div>
      <div style="font-size:0.75rem;color:#94a3b8;margin-top:0.2rem">Plateforme d'Intelligence de Migration</div>
    </div>
  </div>
</div>

<div class="container">

  <div class="alert alert-info">
    Ce rapport présente l'inventaire complet de votre parc Informatica PowerCenter.
    Chaque workflow a été analysé automatiquement et scoré selon une grille formelle.
    Cliquez sur un workflow pour accéder à sa fiche détaillée.
  </div>

  <!-- KPI cards -->
  <div class="grid-2-kpi">
    {''.join(stat_cards)}
  </div>

  <!-- Charts row -->
  <div class="grid-2">
    <div class="card">
      <h2>Répartition par complexité</h2>
      <div style="display:flex;align-items:center;gap:2rem;flex-wrap:wrap">
        {_donut_svg(summary["flag_counts"])}
        <div>{''.join(legend_items)}</div>
      </div>
    </div>
    <div class="card">
      <h2>Cible technologique recommandée</h2>
      {''.join(platform_items)}
      <div class="alert alert-warning" style="margin-top:1rem;margin-bottom:0">
        Les workflows CRITICAL nécessitent une supervision experte.
        La plateforme cible sera confirmée lors de la Phase 2.
      </div>
    </div>
  </div>

  <!-- Workflow table -->
  <div class="card" style="padding:0;overflow:hidden">
    <div style="padding:1.25rem 1.5rem 0.75rem">
      <h2 style="margin-bottom:0">Inventaire des workflows</h2>
      <p style="font-size:0.8rem;color:#6b7280;margin-top:0.25rem">Classés par score de complexité décroissant. Cliquez sur un nom pour ouvrir la fiche détaillée.</p>
    </div>
    <div style="overflow-x:auto">
    <table>
      <thead>
        <tr>
          <th>Workflow</th>
          <th>Complexité / Score</th>
          <th>Cible recommandée</th>
          <th>Estimation</th>
          <th>Faisabilité IA</th>
          <th>Transfo.</th>
          <th>Source principale</th>
        </tr>
      </thead>
      <tbody>
        {''.join(table_rows)}
      </tbody>
    </table>
    </div>
  </div>

</div>

<div class="footer">
  <div>Rapport Phase 1 — {project_name} — {now}</div>
  <div style="margin-top:0.25rem">Plateforme d'Intelligence de Migration Informatica PowerCenter</div>
</div>

</body>
</html>"""

    out = output_dir / "index.html"
    out.write_text(html, encoding="utf-8")
    print(f"[Reporter] Dashboard → {out}")


# ---------------------------------------------------------------------------
# Workflow detail page
# ---------------------------------------------------------------------------

def _score_breakdown_table(breakdown: dict) -> str:
    rows = []
    transfo_scores = breakdown.get("transformations", {})
    global_scores = breakdown.get("global_modifiers", {})

    for name, score in sorted(transfo_scores.items(), key=lambda x: -x[1]):
        rows.append(f"""
        <tr>
          <td><span class="tag">{name}</span></td>
          <td style="color:#6b7280;font-size:0.8rem">Transformation</td>
          <td style="text-align:right;font-weight:700;color:{'#ef4444' if score>=5 else '#f59e0b' if score>=3 else '#374151'}">{score}</td>
        </tr>""")
    for key, score in sorted(global_scores.items(), key=lambda x: -x[1]):
        rows.append(f"""
        <tr>
          <td><span class="tag">{key.replace('_',' ')}</span></td>
          <td style="color:#6b7280;font-size:0.8rem">Modificateur global</td>
          <td style="text-align:right;font-weight:700;color:#6b7280">+{score}</td>
        </tr>""")

    return f"""
    <table>
      <thead><tr><th>Élément</th><th>Type</th><th style="text-align:right">Score</th></tr></thead>
      <tbody>{''.join(rows)}</tbody>
    </table>"""


def _data_flow_svg(data_flow: list[dict], transformations: list[dict], sources: list[dict], targets: list[dict]) -> str:
    # Build node order from connectors
    nodes = {}
    for s in sources:
        nodes[s["name"]] = {"type": "source", "label": s["name"]}
    for t in transformations:
        nodes[t["name"]] = {"type": "transfo", "label": t["name"], "t_type": t.get("type", "")}
    for t in targets:
        nodes[t["name"]] = {"type": "target", "label": t["name"]}

    # Topological order via BFS
    adj = {}
    for edge in data_flow:
        adj.setdefault(edge["from"], []).append(edge["to"])

    visited = []
    queue = [s["name"] for s in sources]
    seen = set(queue)
    while queue:
        node = queue.pop(0)
        visited.append(node)
        for nxt in adj.get(node, []):
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    # Add remaining nodes
    for n in nodes:
        if n not in seen:
            visited.append(n)

    if not visited:
        return ""

    NODE_W, NODE_H, PAD_X, PAD_Y, GAP_X = 160, 36, 12, 8, 30
    total_w = len(visited) * (NODE_W + GAP_X) + 20
    total_h = NODE_H + PAD_Y * 2 + 60

    node_colors = {
        "source": ("#dbeafe", "#2563eb", "#1e40af"),
        "transfo": ("#f3f4f6", "#6b7280", "#374151"),
        "target": ("#d1fae5", "#10b981", "#065f46"),
    }

    # Position nodes
    positions = {}
    for i, name in enumerate(visited):
        positions[name] = (20 + i * (NODE_W + GAP_X), PAD_Y + 20)

    rects = []
    for name, (x, y) in positions.items():
        ntype = nodes.get(name, {}).get("type", "transfo")
        t_type = nodes.get(name, {}).get("t_type", "")
        bg, border, fg = node_colors.get(ntype, node_colors["transfo"])
        label = name[:20] + ("…" if len(name) > 20 else "")
        sub = t_type[:18] if t_type else ""
        rects.append(
            f'<rect x="{x}" y="{y}" width="{NODE_W}" height="{NODE_H}" rx="6" '
            f'fill="{bg}" stroke="{border}" stroke-width="1.5"/>'
            f'<text x="{x+NODE_W//2}" y="{y+14}" text-anchor="middle" font-size="10" font-weight="600" fill="{fg}">{label}</text>'
            f'<text x="{x+NODE_W//2}" y="{y+26}" text-anchor="middle" font-size="8" fill="#6b7280">{sub}</text>'
        )

    arrows = []
    for edge in data_flow:
        f, t = edge.get("from"), edge.get("to")
        if f in positions and t in positions:
            fx, fy = positions[f]
            tx, ty = positions[t]
            x1 = fx + NODE_W
            y1 = fy + NODE_H // 2
            x2 = tx
            y2 = ty + NODE_H // 2
            mx = (x1 + x2) / 2
            arrows.append(
                f'<path d="M{x1},{y1} C{mx},{y1} {mx},{y2} {x2},{y2}" '
                f'fill="none" stroke="#9ca3af" stroke-width="1.5" '
                f'marker-end="url(#arrow)"/>'
            )

    svg = f"""<svg id="df-svg" width="{total_w}" height="{total_h}" viewBox="0 0 {total_w} {total_h}">
  <defs>
    <marker id="arrow" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto">
      <path d="M0,0 L0,6 L8,3 z" fill="#9ca3af"/>
    </marker>
  </defs>
  {''.join(arrows)}
  {''.join(rects)}
</svg>"""
    return svg


def _transfo_table(transformations: list[dict]) -> str:
    rows = []
    for t in transformations:
        flag = t.get("complexity_flag", "MEDIUM")
        score = t.get("complexity_score", 0)
        t_type = t.get("type", "")
        name = t.get("name", "")
        funcs = t.get("has_proprietary_functions", [])
        equiv = t.get("python_equivalents", {})
        notes = t.get("notes", "")

        funcs_html = " ".join(f'<span class="tag">{f}</span>' for f in funcs) if funcs else '<span style="color:#9ca3af;font-size:0.75rem">—</span>'
        equiv_html = ""
        if equiv:
            equiv_html = " ".join(f'<span class="tag" style="background:#eff6ff;color:#1d4ed8">{k}→{v}</span>' for k, v in list(equiv.items())[:4])

        c = FLAG_COLORS.get(flag, FLAG_COLORS["MEDIUM"])
        badge = f'<span class="badge" style="background:{c["bg"]};color:{c["text"]};border:1px solid {c["border"]}">{flag}</span>'

        rows.append(f"""
        <tr>
          <td><span class="tag" style="font-weight:600">{name}</span></td>
          <td style="color:#6b7280;font-size:0.8rem">{t_type}</td>
          <td>{badge} <span style="font-size:0.8rem;color:#374151;margin-left:0.25rem">{score}</span></td>
          <td>{funcs_html}</td>
          <td style="font-size:0.75rem">{equiv_html}</td>
          <td style="font-size:0.75rem;color:#6b7280">{notes}</td>
        </tr>""")

    return f"""
    <table>
      <thead><tr>
        <th>Nom</th><th>Type Informatica</th><th>Complexité</th>
        <th>Fonctions propriétaires</th><th>Équivalents Python</th><th>Notes</th>
      </tr></thead>
      <tbody>{''.join(rows)}</tbody>
    </table>"""


# JS for modal zoom/pan — embedded once per workflow page
DATA_FLOW_MODAL_JS = """
<script>
(function() {
  var overlay = document.getElementById('df-modal-overlay');
  if (!overlay) return;
  var canvas = document.getElementById('df-modal-canvas');
  var wrap   = document.getElementById('df-svg-wrap');
  var srcSvg = document.getElementById('df-svg');
  if (!srcSvg || !wrap) return;

  // Clone SVG into modal
  var clone = srcSvg.cloneNode(true);
  clone.removeAttribute('id');
  clone.style.display = 'block';
  clone.style.maxWidth = 'none';
  wrap.appendChild(clone);

  var scale = 1, tx = 0, ty = 0;
  var dragging = false, startX, startY, startTx, startTy;

  function applyTransform() {
    wrap.style.transform = 'translate(' + tx + 'px,' + ty + 'px) scale(' + scale + ')';
  }

  function fitToCanvas() {
    var cw = canvas.clientWidth, ch = canvas.clientHeight;
    var sw = clone.getAttribute('width') || clone.viewBox.baseVal.width || 800;
    var sh = clone.getAttribute('height') || clone.viewBox.baseVal.height || 200;
    scale = Math.min(cw / sw, ch / sh, 1) * 0.9;
    tx = (cw - sw * scale) / 2;
    ty = (ch - sh * scale) / 2;
    applyTransform();
  }

  function openModal() {
    overlay.classList.add('open');
    requestAnimationFrame(fitToCanvas);
  }
  function closeModal() { overlay.classList.remove('open'); }

  document.getElementById('df-open-btn').addEventListener('click', openModal);
  document.getElementById('df-close-btn').addEventListener('click', closeModal);
  document.getElementById('df-zoom-in').addEventListener('click', function() {
    scale = Math.min(scale * 1.25, 8); applyTransform();
  });
  document.getElementById('df-zoom-out').addEventListener('click', function() {
    scale = Math.max(scale / 1.25, 0.1); applyTransform();
  });
  document.getElementById('df-zoom-reset').addEventListener('click', fitToCanvas);

  overlay.addEventListener('click', function(e) {
    if (e.target === overlay) closeModal();
  });
  document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') closeModal();
  });

  // Mouse wheel zoom
  canvas.addEventListener('wheel', function(e) {
    e.preventDefault();
    var rect = canvas.getBoundingClientRect();
    var mx = e.clientX - rect.left, my = e.clientY - rect.top;
    var factor = e.deltaY < 0 ? 1.12 : 1 / 1.12;
    var newScale = Math.min(Math.max(scale * factor, 0.1), 8);
    tx = mx - (mx - tx) * (newScale / scale);
    ty = my - (my - ty) * (newScale / scale);
    scale = newScale;
    applyTransform();
  }, { passive: false });

  // Drag to pan
  canvas.addEventListener('mousedown', function(e) {
    dragging = true; startX = e.clientX; startY = e.clientY;
    startTx = tx; startTy = ty;
    canvas.classList.add('grabbing');
  });
  document.addEventListener('mousemove', function(e) {
    if (!dragging) return;
    tx = startTx + (e.clientX - startX);
    ty = startTy + (e.clientY - startY);
    applyTransform();
  });
  document.addEventListener('mouseup', function() {
    dragging = false; canvas.classList.remove('grabbing');
  });

  // Touch support
  var lastDist = null;
  canvas.addEventListener('touchstart', function(e) {
    if (e.touches.length === 1) {
      dragging = true; startX = e.touches[0].clientX; startY = e.touches[0].clientY;
      startTx = tx; startTy = ty;
    }
  });
  canvas.addEventListener('touchmove', function(e) {
    e.preventDefault();
    if (e.touches.length === 1 && dragging) {
      tx = startTx + (e.touches[0].clientX - startX);
      ty = startTy + (e.touches[0].clientY - startY);
      applyTransform();
    } else if (e.touches.length === 2) {
      var dx = e.touches[0].clientX - e.touches[1].clientX;
      var dy = e.touches[0].clientY - e.touches[1].clientY;
      var dist = Math.sqrt(dx*dx + dy*dy);
      if (lastDist) {
        var factor = dist / lastDist;
        scale = Math.min(Math.max(scale * factor, 0.1), 8);
        applyTransform();
      }
      lastDist = dist;
    }
  }, { passive: false });
  canvas.addEventListener('touchend', function() { dragging = false; lastDist = null; });
})();
</script>
"""


def generate_workflow_page(wf: dict, project_name: str, output_dir: Path) -> None:
    fname = wf["_filename"]
    wf_id = wf.get("workflow_id", fname)
    mapping_id = wf.get("mapping_id", "")
    parsed_at = wf.get("parsed_at", "")[:10]

    c = wf.get("workflow_complexity", {})
    r = wf.get("routing_decision", {})
    flag = c.get("flag", "MEDIUM")
    score = c.get("total_score", 0)
    days = c.get("estimated_migration_days", "—")
    platform = r.get("target_platform", "python")
    feasibility = r.get("auto_conversion_feasibility", "MEDIUM")
    rationale = r.get("rationale", "")
    human_required = r.get("human_intervention_required", False)

    sources = wf.get("sources", [])
    targets = wf.get("targets", [])
    transformations = wf.get("transformations", [])
    data_flow = wf.get("data_flow", [])
    session = wf.get("session", {})
    flags = wf.get("flags", {})
    breakdown = c.get("score_breakdown", {})

    flag_color = FLAG_COLORS.get(flag, FLAG_COLORS["MEDIUM"])

    # Sources list
    sources_html = ""
    for s in sources:
        fields_count = len(s.get("fields", []))
        sources_html += f"""
        <div style="border:1px solid #e5e7eb;border-radius:0.5rem;padding:0.75rem;margin-bottom:0.5rem">
          <div style="font-weight:600;font-size:0.85rem">{s.get('name','')}</div>
          <div style="font-size:0.75rem;color:#6b7280">{s.get('db_type','')} · {s.get('database','') or '—'} · {fields_count} champs</div>
        </div>"""

    # Targets list
    targets_html = ""
    for t in targets:
        fields_count = len(t.get("fields", []))
        targets_html += f"""
        <div style="border:1px solid #e5e7eb;border-radius:0.5rem;padding:0.75rem;margin-bottom:0.5rem">
          <div style="font-weight:600;font-size:0.85rem">{t.get('name','')}</div>
          <div style="font-size:0.75rem;color:#6b7280">{t.get('db_type','')} · {t.get('database','') or '—'} · {fields_count} champs</div>
        </div>"""

    # Session variables
    vars_html = ""
    if session.get("variables"):
        for v in session["variables"]:
            vars_html += f'<span class="tag">{v.get("name","")}={v.get("default","")}</span> '
    param_file = session.get("parameter_file", "")

    # Global flags
    flag_items = []
    flag_map = {
        "has_java_transformation": ("Java Transformation", "#ef4444"),
        "has_dynamic_lookup": ("Dynamic Lookup", "#f59e0b"),
        "has_custom_function": ("Custom Function", "#ef4444"),
        "has_parameter_file": ("Parameter File", "#6b7280"),
        "has_sql_override": ("SQL Override", "#6b7280"),
    }
    for key, (label, color) in flag_map.items():
        if flags.get(key):
            flag_items.append(f'<span class="pill" style="background:{color}20;color:{color};margin:0.2rem">{label}</span>')
    flags_html = "".join(flag_items) if flag_items else '<span style="color:#9ca3af;font-size:0.8rem">Aucun flag critique</span>'

    # Oracle proprietary functions
    oracle_funcs = flags.get("oracle_proprietary_functions", [])
    oracle_html = " ".join(f'<span class="tag">{f}</span>' for f in oracle_funcs) if oracle_funcs else '<span style="color:#9ca3af;font-size:0.8rem">—</span>'

    # Requires human review
    human_review = flags.get("requires_human_review", [])
    human_html = " ".join(f'<span class="tag" style="background:#fee2e2;color:#b91c1c">{r}</span>' for r in human_review) if human_review else ""

    # Warning banner
    warning_html = ""
    if human_required or flag in ("CRITICAL", "HIGH"):
        if flag == "CRITICAL":
            warning_html = """<div class="alert alert-warning">
              <strong>Supervision experte requise.</strong> Ce workflow dépasse le seuil d'automatisation.
              Un expert supervisera la génération de code lors de la Phase 2.
            </div>"""
        else:
            warning_html = """<div class="alert alert-info">
              <strong>Supervision recommandée.</strong> La génération sera automatique mais une relecture experte est conseillée.
            </div>"""

    data_flow_svg = _data_flow_svg(data_flow, transformations, sources, targets)
    has_df = bool(data_flow and data_flow_svg)
    score_table = _score_breakdown_table(breakdown)
    transfo_table = _transfo_table(transformations)

    display_name = fname.replace("wf_", "").replace("_", " ").title()

    # Data flow section with expandable modal
    if has_df:
        data_flow_section = f"""
  <!-- Data flow -->
  <div class="card">
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:1rem">
      <h2 style="margin-bottom:0">Data flow</h2>
      <button id="df-open-btn" class="df-expand-btn">&#x26F6; Agrandir</button>
    </div>
    <div class="df-container" onclick="document.getElementById('df-open-btn').click()">
      {data_flow_svg}
    </div>
    <div style="margin-top:0.75rem;display:flex;gap:1.5rem;flex-wrap:wrap">
      <div style="display:flex;align-items:center;gap:0.4rem"><div style="width:12px;height:12px;background:#dbeafe;border:1.5px solid #2563eb;border-radius:2px"></div><span style="font-size:0.75rem;color:#6b7280">Source</span></div>
      <div style="display:flex;align-items:center;gap:0.4rem"><div style="width:12px;height:12px;background:#f3f4f6;border:1.5px solid #6b7280;border-radius:2px"></div><span style="font-size:0.75rem;color:#6b7280">Transformation</span></div>
      <div style="display:flex;align-items:center;gap:0.4rem"><div style="width:12px;height:12px;background:#d1fae5;border:1.5px solid #10b981;border-radius:2px"></div><span style="font-size:0.75rem;color:#6b7280">Cible</span></div>
    </div>
  </div>

  <!-- Data flow modal -->
  <div id="df-modal-overlay" class="df-modal-overlay">
    <div class="df-modal-inner">
      <div class="df-modal-header">
        <span class="df-modal-title">Data flow — {display_name}</span>
        <div class="df-modal-controls">
          <button id="df-zoom-out" class="df-btn">−</button>
          <button id="df-zoom-reset" class="df-btn">⊡ Ajuster</button>
          <button id="df-zoom-in" class="df-btn">+</button>
          <button id="df-close-btn" class="df-close-btn" title="Fermer (Échap)">✕</button>
        </div>
      </div>
      <div id="df-modal-canvas" class="df-modal-canvas">
        <div id="df-svg-wrap" class="df-modal-svg-wrap"></div>
      </div>
      <div class="df-hint">Molette pour zoomer · Glisser pour déplacer · Échap ou clic extérieur pour fermer</div>
    </div>
  </div>
  {DATA_FLOW_MODAL_JS}"""
    else:
        data_flow_section = ""

    html = f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{wf_id} — Phase 1</title>
<style>
{COMMON_CSS}
</style>
</head>
<body>

<div class="header">
  <div class="container">
    <div>
      <div style="font-size:0.8rem;color:#94a3b8;margin-bottom:0.3rem">{project_name} · Fiche workflow</div>
      <div style="font-size:1.4rem;font-weight:700">{display_name}</div>
      <div style="font-size:0.8rem;color:#94a3b8;margin-top:0.2rem;font-family:monospace">{wf_id}</div>
    </div>
    <div style="text-align:right">
      <div style="font-size:0.75rem;color:#94a3b8">Analysé le {parsed_at}</div>
      <div style="margin-top:0.5rem">{_flag_badge(flag)}</div>
    </div>
  </div>
</div>

<div class="container">

  <a href="index.html" class="btn-back">← Retour au dashboard</a>

  {warning_html}

  <!-- Score banner -->
  <div class="card" style="background:{flag_color['bg']};border-color:{flag_color['border']};margin-bottom:1.5rem">
    <div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:1rem">
      <div>
        <div style="font-size:2.5rem;font-weight:800;color:{flag_color['text']};line-height:1">{score}</div>
        <div style="font-size:0.8rem;color:{flag_color['text']};opacity:0.8">Score de complexité total</div>
      </div>
      <div style="text-align:center">
        <div style="font-size:1.4rem;font-weight:700;color:{flag_color['text']}">{days} j</div>
        <div style="font-size:0.75rem;color:{flag_color['text']};opacity:0.8">Estimation migration</div>
      </div>
      <div style="text-align:center">
        <div style="margin-bottom:0.3rem">{_platform_badge(platform)}</div>
        <div style="font-size:0.75rem;color:#6b7280">Cible recommandée</div>
      </div>
      <div style="text-align:center">
        <div style="margin-bottom:0.3rem">{_feasibility_badge(feasibility)}</div>
        <div style="font-size:0.75rem;color:#6b7280">Faisabilité IA</div>
      </div>
    </div>
  </div>

  <!-- Rationale -->
  <div class="card">
    <h2>Analyse de routage</h2>
    <p style="font-size:0.9rem;color:#374151;margin-bottom:1rem">{rationale}</p>
    <div style="display:flex;flex-wrap:wrap;gap:0.5rem;align-items:center">
      <span style="font-size:0.75rem;color:#6b7280;font-weight:600">MAPPING :</span>
      <span class="tag">{mapping_id}</span>
      <span style="font-size:0.75rem;color:#6b7280;font-weight:600;margin-left:0.5rem">FLAGS :</span>
      {flags_html}
    </div>
    {f'<div style="margin-top:0.75rem"><span style="font-size:0.75rem;color:#6b7280;font-weight:600">FONCTIONS ORACLE :</span> {oracle_html}</div>' if oracle_funcs else ''}
    {f'<div style="margin-top:0.75rem"><span style="font-size:0.75rem;color:#b91c1c;font-weight:600">REVUE HUMAINE :</span> {human_html}</div>' if human_review else ''}
  </div>

  <!-- Sources & Targets -->
  <div class="grid-2">
    <div class="card">
      <h2>Sources ({len(sources)})</h2>
      {sources_html or '<span style="color:#9ca3af">Aucune source détectée</span>'}
    </div>
    <div class="card">
      <h2>Cibles ({len(targets)})</h2>
      {targets_html or '<span style="color:#9ca3af">Aucune cible détectée</span>'}
    </div>
  </div>

  {data_flow_section}

  <!-- Transformations table -->
  <div class="card" style="padding:0;overflow:hidden">
    <div style="padding:1.25rem 1.5rem 0.75rem">
      <h2 style="margin-bottom:0">Transformations Informatica ({len(transformations)})</h2>
    </div>
    <div style="overflow-x:auto">{transfo_table}</div>
  </div>

  <!-- Score breakdown + Session -->
  <div class="grid-2">
    <div class="card">
      <h2>Décomposition du score</h2>
      <div style="overflow-x:auto">{score_table}</div>
      <hr class="divider">
      <div style="display:flex;justify-content:space-between;font-weight:700">
        <span>Total</span>
        <span style="color:{flag_color['badge']}">{score}</span>
      </div>
    </div>
    <div class="card">
      <h2>Session &amp; paramètres</h2>
      {f'<div style="margin-bottom:0.75rem"><div class="section-label">Fichier de paramètres</div><span class="tag">{param_file}</span></div>' if param_file else ''}
      {f'<div><div class="section-label">Variables de session</div>{vars_html}</div>' if session.get("variables") else ''}
      {'' if param_file or session.get("variables") else '<span style="color:#9ca3af;font-size:0.8rem">Pas de paramètres détectés</span>'}
      <hr class="divider">
      <div>
        <div class="section-label">Étapes Phase 2 recommandées</div>
        <ul style="font-size:0.8rem;color:#374151;padding-left:1.2rem;margin-top:0.5rem">
          <li>Génération du code {PLATFORM_LABELS.get(platform,'Python')}</li>
          {'<li>Correction sémantique LLM (supervision experte)</li>' if flag in ('HIGH','CRITICAL') else '<li>Vérification statique automatique</li>'}
          <li>Documentation métier bilingue</li>
          <li>Recette data diff vs golden dataset</li>
        </ul>
      </div>
    </div>
  </div>

</div>

<div class="footer">
  <div>{wf_id} · {project_name} · Rapport Phase 1</div>
</div>

</body>
</html>"""

    out = output_dir / f"{fname}.html"
    out.write_text(html, encoding="utf-8")
    print(f"[Reporter] Workflow → {out}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Phase 1 HTML Reporter")
    parser.add_argument("--input", default="output/01_canonical_json", help="Dossier contenant les Canonical JSON")
    parser.add_argument("--output", default="output/phase1_report", help="Dossier de sortie HTML")
    parser.add_argument("--project", default="Projet Migration Informatica", help="Nom du projet")
    args = parser.parse_args()

    input_dir = Path(args.input)
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"[Reporter] Chargement des Canonical JSON depuis {input_dir}")
    workflows = load_canonical_jsons(input_dir)
    if not workflows:
        print("[Reporter] ERREUR : aucun JSON trouvé dans", input_dir)
        return

    print(f"[Reporter] {len(workflows)} workflow(s) trouvé(s)")
    generate_index(workflows, args.project, output_dir)

    for wf in workflows:
        generate_workflow_page(wf, args.project, output_dir)

    print(f"\n[Reporter] Rapport Phase 1 généré dans {output_dir}/")
    print(f"[Reporter] Ouvrez : {output_dir}/index.html")


if __name__ == "__main__":
    main()
