"""Generate the Conference Email System engineering progress report (PDF).

A self-contained ReportLab document covering Phases 0-4. There was no prior
generator in the repo (the existing "Design Document" PDF was produced
externally), so this script is the reusable living generator going forward.

Run:
    python scripts/generate_progress_pdf.py

Output:
    ./Conference Email System Progress Report.pdf  (repo root)

Note: this is an internal engineering progress report, not the shared design
document. Per project convention we avoid naming any specific hosted foundation
model; the drafter is described generically as a "configurable AI provider".
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    HRFlowable,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

# --- palette (mirrors the frontend design system: indigo accent) -----------
INDIGO = colors.HexColor("#6366f1")
INK = colors.HexColor("#1e1b3a")
SLATE = colors.HexColor("#475569")
INDIGO_BG = colors.HexColor("#eef2ff")
GREEN = colors.HexColor("#10b981")
GREEN_BG = colors.HexColor("#ecfdf5")
AMBER = colors.HexColor("#f59e0b")
AMBER_BG = colors.HexColor("#fffbeb")
BORDER = colors.HexColor("#e2e8f0")
ROW_ALT = colors.HexColor("#f8fafc")

_OUTPUT = Path(__file__).resolve().parents[1] / "Conference Email System Progress Report.pdf"


# ---------------------------------------------------------------------------
# Styles
# ---------------------------------------------------------------------------
def _styles() -> dict:
    base = getSampleStyleSheet()
    s = {}
    s["title"] = ParagraphStyle(
        "DocTitle", parent=base["Title"], fontName="Helvetica-Bold",
        fontSize=22, textColor=INK, spaceAfter=4, leading=26,
    )
    s["subtitle"] = ParagraphStyle(
        "DocSubtitle", parent=base["Normal"], fontName="Helvetica",
        fontSize=11, textColor=SLATE, spaceAfter=2,
    )
    s["phase"] = ParagraphStyle(
        "PhaseHeading", parent=base["Heading1"], fontName="Helvetica-Bold",
        fontSize=16, textColor=INDIGO, spaceBefore=16, spaceAfter=6, leading=20,
    )
    s["sub"] = ParagraphStyle(
        "SubHeading", parent=base["Heading2"], fontName="Helvetica-Bold",
        fontSize=12, textColor=INK, spaceBefore=10, spaceAfter=3, leading=15,
    )
    s["body"] = ParagraphStyle(
        "Body", parent=base["Normal"], fontName="Helvetica", fontSize=9.5,
        textColor=INK, leading=13.5, spaceAfter=4, alignment=TA_LEFT,
    )
    s["label"] = ParagraphStyle(
        "Label", parent=base["Normal"], fontName="Helvetica-Bold", fontSize=8,
        textColor=INDIGO, spaceAfter=1, leading=11,
    )
    s["callout_body"] = ParagraphStyle(
        "CalloutBody", parent=base["Normal"], fontName="Helvetica-Oblique",
        fontSize=9, textColor=INK, leading=12.5,
    )
    s["cell"] = ParagraphStyle(
        "Cell", parent=base["Normal"], fontName="Helvetica", fontSize=8.5,
        textColor=INK, leading=11,
    )
    s["cell_head"] = ParagraphStyle(
        "CellHead", parent=base["Normal"], fontName="Helvetica-Bold",
        fontSize=8.5, textColor=colors.white, leading=11,
    )
    s["small"] = ParagraphStyle(
        "Small", parent=base["Normal"], fontName="Helvetica", fontSize=8,
        textColor=SLATE, leading=11,
    )
    return s


S = _styles()


# ---------------------------------------------------------------------------
# Reusable flowables
# ---------------------------------------------------------------------------
def callout(label: str, text: str, width: float,
            accent=INDIGO, bg=INDIGO_BG) -> Table:
    """A colored callout box with a left accent bar and a bold label."""
    inner = [
        [Paragraph(label.upper(), S["label"])],
        [Paragraph(text, S["callout_body"])],
    ]
    t = Table(inner, colWidths=[width])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), bg),
        ("LEFTPADDING", (0, 0), (-1, -1), 12),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LINEBEFORE", (0, 0), (0, -1), 3, accent),
        ("BOX", (0, 0), (-1, -1), 0.5, BORDER),
    ]))
    return t


def summary_table(rows: list[list[str]], width: float) -> Table:
    """Phase summary table: Sub-phase | What shipped | Config flag | Tests."""
    header = ["Sub-phase", "What shipped", "Config flag affected", "Tests added"]
    data = [[Paragraph(h, S["cell_head"]) for h in header]]
    for r in rows:
        data.append([Paragraph(str(c), S["cell"]) for c in r])
    col_w = [0.75 * inch, width - 3.05 * inch, 1.55 * inch, 0.75 * inch]
    t = Table(data, colWidths=col_w, repeatRows=1)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), INDIGO),
        ("GRID", (0, 0), (-1, -1), 0.5, BORDER),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("ALIGN", (3, 0), (3, -1), "CENTER"),
    ]
    for i in range(1, len(data)):
        if i % 2 == 0:
            style.append(("BACKGROUND", (0, i), (-1, i), ROW_ALT))
    t.setStyle(TableStyle(style))
    return t


def subphase(story: list, width: float, heading: str, built: str,
             decision: str, not_built: str, meeting: str) -> None:
    """Render one sub-phase block in the standard structure."""
    story.append(Paragraph(heading, S["sub"]))
    story.append(Paragraph(f"<b>What was built.</b> {built}", S["body"]))
    story.append(Paragraph(f"<b>Key decision.</b> {decision}", S["body"]))
    story.append(Paragraph(f"<b>Intentionally not built.</b> {not_built}", S["body"]))
    story.append(Spacer(1, 3))
    story.append(callout("In a meeting", meeting, width))
    story.append(Spacer(1, 8))


def phase_heading(story: list, title: str, blurb: str) -> None:
    story.append(Paragraph(title, S["phase"]))
    story.append(HRFlowable(width="100%", thickness=1.2, color=INDIGO,
                            spaceBefore=0, spaceAfter=6))
    story.append(Paragraph(blurb, S["body"]))


# ---------------------------------------------------------------------------
# Document body
# ---------------------------------------------------------------------------
def build_story(width: float) -> list:
    story: list = []

    # --- title block ---
    story.append(Paragraph("Conference Email System", S["title"]))
    story.append(Paragraph("Engineering Progress Report &mdash; Phases 0&ndash;4", S["subtitle"]))
    story.append(Paragraph(
        f"AI-powered two-lane conference email management (FAQ auto-reply + "
        f"human review). Generated {datetime.now().strftime('%B %d, %Y')}.",
        S["small"]))
    story.append(Spacer(1, 6))
    story.append(HRFlowable(width="100%", thickness=1.5, color=INDIGO, spaceAfter=4))

    # =====================================================================
    # Phase 0-2 (concise historical context)
    # =====================================================================
    phase_heading(story, "Phase 0 &mdash; Foundation",
                  "Project skeleton and the swappable-seam architecture that the "
                  "rest of the system plugs into.")
    story.append(Paragraph(
        "Backend scaffold (FastAPI app, typed config via pydantic-settings, async "
        "SQLAlchemy + Alembic, ORM models for emails / audit_logs / policy_documents), "
        "the six independently-replaceable modules (classifier, retriever, router, "
        "drafter, persistence, UI), and a Next.js frontend shell. Health check green; "
        "initial migration clean.", S["body"]))

    phase_heading(story, "Phase 1 &mdash; Pipeline &amp; API",
                  "The end-to-end processing pipeline and its HTTP surface.")
    story.append(Paragraph(
        "Toy dataset (30 labeled emails) + knowledge base (45 policy chunks); a "
        "repository layer for all DB access; the classify &rarr; retrieve &rarr; route "
        "&rarr; draft pipeline plus an orchestrator; and the v1 API (ingest, queue, "
        "detail, approve, reroute, analytics) with a seed script. <b>16 tests passing.</b>",
        S["body"]))

    phase_heading(story, "Phase 2 &mdash; Frontend",
                  "A complete, demo-ready operator interface.")
    story.append(Paragraph(
        "React Query API client + hooks; a dark-mode design system (indigo accent); "
        "dashboard with live stats; an email queue split-pane review interface; the "
        "auto-replies table; an audit-log timeline; analytics charts; and a responsive "
        "app shell. All pages typed and served.", S["body"]))

    # =====================================================================
    # Phase 3
    # =====================================================================
    phase_heading(
        story, "Phase 3 &mdash; Production Hardening &amp; Intelligence",
        "Five sub-phases (executed in the order 3E, 3C, 3D, 3A, 3B) that turned the "
        "MVP into a configurable, learnable, production-leaning system. Every "
        "capability is a config-flag swap; defaults are unchanged.")

    subphase(
        story, width, "3E &mdash; Real Audit Endpoint",
        "Replaced the audit stub with a real, paginated, filterable audit API reading "
        "directly from the audit_logs table: <b>GET /api/v1/audit</b> (filter by "
        "email_id / action / actor, with limit &amp; offset) and "
        "<b>GET /api/v1/audit/{id}</b>.",
        "Added methods to the existing AuditRepository and mapped API fields "
        "(created_at &larr; timestamp, details &larr; extra_metadata) via Pydantic "
        "aliases instead of renaming DB columns &mdash; zero migration, fully "
        "backward compatible.",
        "No write or delete endpoints &mdash; the audit trail is append-only by "
        "design; entries are created only by the pipeline and chair actions.",
        "Every action the system or a chair takes is now permanently logged and "
        "queryable &mdash; full traceability for accountability and demos.")

    subphase(
        story, width, "3C &mdash; PostgreSQL Migration Readiness",
        "Added async (asyncpg) and sync (psycopg2) Postgres drivers, a "
        "SYNC_DATABASE_URL setting, a dialect-aware async Alembic env, and a "
        "zero-drift checkpoint migration. Switching to Postgres is one .env line "
        "plus <i>alembic upgrade head</i>.",
        "Kept SQLite as the default for dev and tests and made batch-ALTER "
        "SQLite-only, so Postgres migrations emit clean direct ALTERs and the URL "
        "passes through untouched.",
        "Did not stand up a live Postgres server or run migrations against it (none "
        "in dev) &mdash; verified via the checkpoint migration and a model-vs-schema diff.",
        "The system runs on lightweight SQLite today but is ready to flip to "
        "production Postgres with a single config line &mdash; no code changes.")

    subphase(
        story, width, "3D &mdash; Local LLM Backend",
        "Made the drafter provider-aware: MODEL_PROVIDER selects a hosted API, a "
        "local OpenAI-compatible endpoint (Ollama-style, MODEL_PROVIDER=local), or a "
        "deterministic fallback. Added a <b>GET /api/v1/health/model</b> status probe.",
        "Every provider path returns a draft and never raises; local errors degrade "
        "gracefully to a fallback with a logged warning, so a down model server can't "
        "break the pipeline.",
        "No response streaming and no automatic provider failover &mdash; the surface "
        "stays simple; the provider is chosen by config, not runtime auto-selection.",
        "We can run entirely on our own GPU with an open model for privacy and cost, "
        "or use a hosted API &mdash; switching is one config line, and the system "
        "stays up even if the model server is down.")

    subphase(
        story, width, "3A &mdash; Trainable Classifier",
        "A sentence-embedding + logistic-regression intent classifier that learns "
        "from labeled emails via <b>POST /api/v1/train/classifier</b> and is enabled "
        "with CLASSIFIER_BACKEND=trainable. Until trained, it auto-falls back to the "
        "keyword baseline.",
        "Kept the exact public classifier interface (a true drop-in), lazy CPU "
        "embedding load, and on-disk model artifacts &mdash; no churn for callers and "
        "no GPU requirement.",
        "No deep fine-tuning or GPU training &mdash; a compact CPU model is sufficient "
        "at this data scale and keeps the loop fast and cheap.",
        "The classifier can now learn from real labeled emails and improve over time, "
        "while safely falling back to rules until it has been trained.")

    subphase(
        story, width, "3B &mdash; RL Bandit Router",
        "An epsilon-greedy bandit (&epsilon; = 0.15) that learns lane decisions per "
        "intent from chair feedback &mdash; approve rewards the chosen lane, reroute "
        "penalizes it. Enabled with ROUTING_STRATEGY=rl; win-rates at "
        "<b>GET /api/v1/analytics/rl-stats</b>.",
        "The learning layer sits on top of hard safety guards (sensitive intents and a "
        "low-confidence floor always escalate), so the bandit can never override safety.",
        "No neural / contextual-feature policy &mdash; a per-intent bandit is "
        "interpretable and data-efficient for the current volume.",
        "The router gets smarter every time a chair approves or reroutes an email, but "
        "it can never auto-reply to sensitive cases &mdash; safety rules always win.")

    story.append(Paragraph("Phase 3 summary", S["sub"]))
    story.append(summary_table([
        ["3E", "Paginated, filterable audit endpoint (/api/v1/audit)", "&mdash;", "5"],
        ["3C", "PostgreSQL migration readiness (drivers, async Alembic, checkpoint)", "SYNC_DATABASE_URL", "0"],
        ["3D", "Local / hosted / fallback drafter + model health probe", "MODEL_PROVIDER", "4"],
        ["3A", "Trainable embedding classifier + train endpoint", "CLASSIFIER_BACKEND", "5"],
        ["3B", "RL bandit router + feedback loop + rl-stats", "ROUTING_STRATEGY", "6"],
    ], width))
    story.append(Spacer(1, 4))
    story.append(Paragraph("<b>Test count at end of Phase 3: 36 passing.</b>", S["body"]))

    # =====================================================================
    # Phase 4
    # =====================================================================
    phase_heading(
        story, "Phase 4 &mdash; Retrieval Quality &amp; Evaluation",
        "Two completed sub-phases that add semantic retrieval and a measurement "
        "harness &mdash; the tools to know whether the system is actually good.")

    subphase(
        story, width, "4A &mdash; FAISS Vector Retrieval",
        "A dense-vector retriever (sentence-transformer embeddings, all-MiniLM-L6-v2; "
        "FAISS IndexFlatIP with L2-normalized cosine) selectable via "
        "RETRIEVAL_BACKEND=faiss. Lazy index build, rebuild_index() for live reindex, "
        "new config FAISS_MODEL_NAME, and <b>GET /api/v1/retrieval/info</b>. BM25 is "
        "unchanged and still the default.",
        "Implemented as a flat module (not a subpackage) to avoid the import-shadowing "
        "bug removed in Phase 1C; loads documents from the DB via the policy repository "
        "using its own short-lived session, so retrieve() stays a true drop-in for BM25.",
        "No approximate-nearest-neighbor index or GPU FAISS &mdash; exact flat search "
        "is instant at 45 chunks; kept CPU-only.",
        "We can now match policies by meaning, not just keywords &mdash; and swap "
        "between keyword and semantic search with one config line, with the proven "
        "keyword method still the safe default.")

    subphase(
        story, width, "4B &mdash; Eval Harness with Ground Truth",
        "A labeled ground-truth set (data/eval/ground_truth.json &mdash; 40 emails, "
        "all 8 intents &times;5, 15 FAQ / 25 human-review, 8 hard) and a "
        "component-level eval script (scripts/run_eval.py) that scores classifier + "
        "retriever + router: per-intent precision / recall / F1, routing accuracy, and "
        "retrieval hit-rate, written as a JSON report plus a console summary. CLI: "
        "--retrieval, --top-k, --output, --ground-truth, --verbose.",
        "Evaluate each component independently (not the full orchestrator) with no DB "
        "writes, so runs are fast, deterministic, and reproducible.",
        "No FAISS eval against a seeded DB yet (BM25 baseline first) and no automated "
        "CI quality gate &mdash; this is a research tool for now.",
        "We can now measure quality objectively and catch regressions &mdash; the "
        "harness already surfaced a concrete, fixable weakness (below).")

    story.append(callout(
        "Baseline eval results (BM25, keyword classifier, top-k 3)",
        "Classification accuracy <b>95.0%</b> (macro F1 <b>0.950</b>) and retrieval "
        "hit-rate <b>97.5%</b> are strong. Routing accuracy is <b>77.5%</b>: "
        "human-review is perfect (25/25) but the FAQ lane is only <b>6/15</b>. The "
        "keyword classifier's confidence frequently falls below the 0.65 FAQ "
        "threshold, so genuine FAQ emails get escalated to humans &mdash; a clear, "
        "actionable signal to tune the threshold and/or train the classifier.",
        width, accent=GREEN, bg=GREEN_BG))
    story.append(Spacer(1, 6))

    story.append(Paragraph("Phase 4 summary", S["sub"]))
    story.append(summary_table([
        ["4A", "FAISS dense retrieval + /retrieval/info (BM25 still default)",
         "RETRIEVAL_BACKEND, FAISS_MODEL_NAME", "6"],
        ["4B", "Eval harness + 40-email ground truth + JSON reports", "&mdash;", "5"],
    ], width))
    story.append(Spacer(1, 4))
    story.append(Paragraph("<b>Test count at end of Phase 4B: 47 passing.</b>", S["body"]))
    story.append(Spacer(1, 6))

    story.append(callout(
        "What comes next",
        "Tune the FAQ confidence threshold (directly addresses the 6/15 finding); run "
        "the eval with the FAISS backend against a seeded database; Dockerize for "
        "deployment; and prepare the live demo.",
        width, accent=AMBER, bg=AMBER_BG))

    return story


def main() -> Path:
    doc = SimpleDocTemplate(
        str(_OUTPUT), pagesize=LETTER,
        leftMargin=0.85 * inch, rightMargin=0.85 * inch,
        topMargin=0.75 * inch, bottomMargin=0.75 * inch,
        title="Conference Email System - Progress Report",
        author="Melady Lab",
    )
    story = build_story(doc.width)
    doc.build(story)
    return _OUTPUT


if __name__ == "__main__":
    out = main()
    print(f"PDF generated -> {out} ({out.stat().st_size:,} bytes)")
