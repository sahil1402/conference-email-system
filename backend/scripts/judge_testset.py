"""LLM-as-judge for the 20-email test set: FRESH drafts vs the chair's actual reply.

Two stages in one run:
  1. Re-generate drafts for all 20 test emails through the CURRENT pipeline
     (EmailPipeline.process_email → distill + fusion retrieval + drafter with the
     placeholder contract). Runs against a throwaway SQLite DB seeded with the 93
     real policies, so the dev DB is never touched and no stale drafts are reused.
  2. Score each fresh draft against the chair's actual reply (chair_reply) with the
     configured OpenAI-compatible model as judge (1-5 per dimension + rationale).

Usage:  cd backend && export PATH=/u/jpang1/miniconda3/envs/autoexp/bin:$PATH
        python scripts/judge_testset.py [--model NAME] [--limit N]

Outputs (data/eval_real/, gitignored):
  testset_20_drafts_current.jsonl  fresh drafts
  testset_20_judge.jsonl           per-ticket scores + rationale
  testset_20_judge_report.md       aggregate report
"""

import argparse
import asyncio
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_BACKEND))
sys.path.insert(0, str(_BACKEND / "scripts"))

# --- production-parity config + a throwaway DB, set BEFORE importing app modules
_fd, _tmpdb = tempfile.mkstemp(suffix=".db", prefix="judge_testset_")
os.close(_fd)
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_tmpdb}"
os.environ["MODEL_PROVIDER"] = "local"
os.environ["QUERY_STRATEGY"] = "distill"
os.environ["RETRIEVAL_BACKEND"] = "fusion"
os.environ.setdefault("HF_HUB_OFFLINE", "1")

import httpx  # noqa: E402

from app.core.config import settings  # noqa: E402
from distill_style_guide import read_key  # noqa: E402

settings.LOCAL_MODEL_API_KEY = read_key()
# Reasoning models spend completion budget on hidden reasoning before visible
# text; the production 500 default would come back empty.
settings.DRAFTER_MAX_TOKENS = 2500

from app.db.database import async_session_factory  # noqa: E402  (bound to temp DB)
from app.db.models import Base, PolicyDocument  # noqa: E402
from app.pipeline.orchestrator import EmailPipeline  # noqa: E402
from app.repositories.policy_repository import PolicyRepository  # noqa: E402

EVAL_DIR = _ROOT / "data" / "eval_real"
TESTSET = EVAL_DIR / "testset_20.jsonl"
KB_PATH = _ROOT / "data" / "knowledge_base" / "policies.json"
DRAFTS_OUT = EVAL_DIR / "testset_20_drafts_current.jsonl"
JUDGE_OUT = EVAL_DIR / "testset_20_judge.jsonl"
REPORT = EVAL_DIR / "testset_20_judge_report.md"

DIMS = ["factual_correctness", "completeness", "helpfulness", "placeholder_quality", "tone", "overall"]

_JUDGE_SYSTEM = """You are a strict expert evaluator for an AAAI conference \
support-email assistant. You are given: a requester's INQUIRY, the CHAIR'S ACTUAL \
REPLY (ground truth, written by the human program chair), an AI-GENERATED DRAFT \
reply, and the draft's NOTES FOR CHAIR.

IMPORTANT — how to read the draft. The draft is NOT a final, send-ready email; it \
is a DRAFT prepared for a program chair to review and COMPLETE before sending. The \
assistant deliberately inserts inline [CHAIR: ...] placeholders wherever the reply \
needs information the policy knowledge base cannot provide or a decision only a \
chair can make (e.g. the status of a specific submission, whether to grant an \
exception, the outcome of an operational step). In those cases a placeholder is \
the EXPECTED, CORRECT behavior — NOT a mistake or an omission. Evaluate the draft \
as "a draft a chair will finish," assuming the chair fills each placeholder with \
the obvious required information. Many drafts are human-review drafts that are not \
complete or helpful until their placeholders are filled — do NOT penalize them for \
that alone.

A placeholder is GOOD when its inline hint AND the matching NOTES FOR CHAIR line \
are specific and ACTIONABLE — telling the chair exactly what to confirm, decide, \
or provide. A placeholder is BAD when it is vague/empty (no useful guidance), or \
defers something the chair actually answered directly from policy (the draft could \
and should have answered it), or is missing where the draft instead GUESSED/asserted \
something only a chair should decide (under-deferral).

Score each dimension as an integer 1-5 (5 = best):
- factual_correctness: policy/facts asserted are correct and consistent with the \
chair's reply; no hallucinated, invented, or contradictory claims. A correct, \
well-placed [CHAIR] deferral is NOT a factual error.
- completeness: does the draft address every part of the inquiry, EITHER by \
answering from policy OR by deferring with an appropriate placeholder? An \
appropriate deferral counts as ADDRESSED (full credit) — do NOT dock completeness \
merely because a placeholder is still unfilled. Dock it only for parts left neither \
answered nor deferred, or for content wrongly deferred that the chair answered.
- helpfulness: ASSUMING the chair fills the placeholders with the expected \
information, would the finished reply resolve the requester's need about as well as \
the chair's actual reply? Do not penalize helpfulness merely because the raw \
(unfilled) draft is incomplete; do penalize if it would not help even once filled, \
or if the placeholders leave the chair doing work the draft should have done.
- placeholder_quality: quality of the draft's DEFERRAL judgment — does it defer \
exactly the right things (chair-only / non-KB info) with specific, actionable hints \
(+ notes), neither over-deferring (vague or unnecessary placeholders) nor \
under-deferring (guessing what it should have placeheld)? If no placeholders were \
needed and none were used, score 5. If placeholders were needed but hints are vague, \
or needed deferrals are missing, score low.
- tone: professional, appropriate, matches a program chair's voice.
- overall: holistic quality of this draft AS A CHAIR-FACING DRAFT — a concise draft \
that answers what it can from policy and defers the rest with clear, actionable \
placeholders is excellent, even if not send-ready as-is.

Return ONLY a JSON object with exactly these keys and no prose outside it:
{"factual_correctness":int,"completeness":int,"helpfulness":int,\
"placeholder_quality":int,"tone":int,"overall":int,"rationale":"one or two sentences"}"""


def _load_testset(limit: int | None) -> list[dict]:
    rows = [json.loads(l) for l in TESTSET.read_text(encoding="utf-8").splitlines() if l.strip()]
    return rows[:limit] if limit else rows


async def _seed_policies() -> None:
    """Create the schema on the temp DB and load the 93 real policies."""
    async with async_session_factory() as db:
        conn = await db.connection()
        await conn.run_sync(Base.metadata.create_all)
        await db.commit()
    policies = json.loads(KB_PATH.read_text(encoding="utf-8"))
    repo = PolicyRepository()
    async with async_session_factory() as db:
        for p in policies:
            await repo.upsert_by_key(db, p, source="aaai_scrape")
    async with async_session_factory() as db:
        n = len(await repo.list_for_index(db))
    print(f"[seed] temp DB seeded: {n} active policies", flush=True)


async def _regenerate(rows: list[dict]) -> list[dict]:
    """Run each test email through the CURRENT pipeline; capture the fresh draft."""
    pipeline = EmailPipeline()
    ts = datetime.now(timezone.utc).isoformat()
    out: list[dict] = []
    for i, row in enumerate(rows, 1):
        email_data = {
            "from": "requester@example.com",
            "to": "confmail@aaai.org",
            "subject": row["subject"],
            "body": row["body"],
            "timestamp": ts,
        }
        try:
            async with async_session_factory() as db:
                res = await pipeline.process_email(email_data, db)
            d = res.draft
            rec = {
                "ticket_id": row["ticket_id"],
                "intent": res.classification.intent,
                "method": res.classification.method,
                "lane": res.routing.lane,
                "draft_text": d.draft_text,
                "placeholders": list(d.placeholders or []),
                "citations": list(d.citations or []),
                "notes_for_chair": d.notes_for_chair,
                "reply_leaks": bool((d.generation_metadata or {}).get("reply_leaks")),
                "status": res.status,
                "chair_reply": row["chair_reply"],
                "subject": row["subject"],
                "body": row["body"],
            }
        except Exception as exc:  # noqa: BLE001
            rec = {"ticket_id": row["ticket_id"], "error": repr(exc),
                   "chair_reply": row["chair_reply"], "subject": row["subject"], "body": row["body"]}
        out.append(rec)
        ph = len(rec.get("placeholders", []))
        print(f"[regen {i:2}/{len(rows)}] ticket {rec['ticket_id']} "
              f"lane={rec.get('lane')} ph={ph} "
              f"{'ERR ' + rec['error'] if rec.get('error') else 'ok'}", flush=True)
    DRAFTS_OUT.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in out) + "\n", encoding="utf-8")
    return out


def _extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1].lstrip("json").strip() if "```" in text[3:] else text
    start, end = text.find("{"), text.rfind("}")
    return json.loads(text[start : end + 1])


async def _judge_one(client: httpx.AsyncClient, draft: dict) -> dict:
    inquiry = f"Subject: {draft['subject']}\n\n{draft['body']}"
    ph = len(draft.get("placeholders", []))
    notes = draft.get("notes_for_chair") or "(none)"
    user = (
        f"### REQUESTER INQUIRY\n{inquiry}\n\n"
        f"### CHAIR'S ACTUAL REPLY (ground truth)\n{draft['chair_reply']}\n\n"
        f"### AI-GENERATED DRAFT (contains {ph} [CHAIR: ...] placeholder(s))\n{draft.get('draft_text','')}\n\n"
        f"### DRAFT'S NOTES FOR CHAIR (per-placeholder guidance)\n{notes}\n\n"
        "Score the AI draft against the chair's actual reply per the rubric. JSON only."
    )
    url = f"{settings.LOCAL_MODEL_BASE_URL}/chat/completions"
    headers = {"Authorization": f"Bearer {settings.LOCAL_MODEL_API_KEY}"}
    payload = {
        "model": settings.LOCAL_MODEL_NAME,
        "messages": [{"role": "system", "content": _JUDGE_SYSTEM},
                     {"role": "user", "content": user}],
        "response_format": {"type": "json_object"},
        "max_tokens": 3000,
    }
    resp = await client.post(url, json=payload, headers=headers)
    if resp.status_code == 400 and "max_completion_tokens" in resp.text and "max_tokens" in payload:
        payload["max_completion_tokens"] = payload.pop("max_tokens")
        resp = await client.post(url, json=payload, headers=headers)
    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"]["content"]
    scores = _extract_json(content)
    return {k: scores.get(k) for k in DIMS} | {"rationale": scores.get("rationale", "")}


async def _judge(drafts: list[dict]) -> list[dict]:
    results: list[dict] = []
    sem = asyncio.Semaphore(4)
    async with httpx.AsyncClient(timeout=180.0) as client:
        async def one(i: int, d: dict) -> dict:
            base = {"ticket_id": d["ticket_id"], "intent": d.get("intent"),
                    "lane": d.get("lane"), "placeholders": len(d.get("placeholders", []))}
            if d.get("error") or not d.get("draft_text"):
                print(f"[judge {i:2}/{len(drafts)}] ticket {d['ticket_id']} SKIP (no draft)", flush=True)
                return base | {k: None for k in DIMS} | {"rationale": "no draft generated"}
            async with sem:
                try:
                    sc = await _judge_one(client, d)
                except Exception as exc:  # noqa: BLE001
                    print(f"[judge {i:2}/{len(drafts)}] ticket {d['ticket_id']} ERR {exc!r}", flush=True)
                    return base | {k: None for k in DIMS} | {"rationale": f"judge error: {exc!r}"}
            print(f"[judge {i:2}/{len(drafts)}] ticket {d['ticket_id']} overall={sc.get('overall')}", flush=True)
            return base | sc
        results = await asyncio.gather(*(one(i, d) for i, d in enumerate(drafts, 1)))
    JUDGE_OUT.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in results) + "\n", encoding="utf-8")
    return results


def _write_report(results: list[dict]) -> None:
    scored = [r for r in results if r.get("overall") is not None]
    def mean(k: str) -> float:
        vals = [r[k] for r in scored if isinstance(r.get(k), (int, float))]
        return round(sum(vals) / len(vals), 2) if vals else float("nan")
    lines = [
        "# LLM-as-judge — 20-email test set: fresh draft vs chair's actual reply",
        "",
        f"- Judge model: `{settings.LOCAL_MODEL_NAME}` (OpenAI-compatible endpoint)",
        f"- Drafts: **freshly generated** via the current pipeline "
        f"(QUERY_STRATEGY=distill, RETRIEVAL_BACKEND=fusion) — not stale.",
        f"- Scored: {len(scored)}/{len(results)} tickets (1-5 per dimension; 5 = best).",
        "",
        "## Aggregate means",
        "",
        "| dimension | mean |",
        "|---|---|",
    ] + [f"| {d} | {mean(d)} |" for d in DIMS] + [
        "",
        f"- Drafts with >=1 [CHAIR] placeholder: "
        f"{sum(1 for r in results if (r.get('placeholders') or 0) > 0)}/{len(results)}",
        "",
        "## Per-ticket",
        "",
        "| ticket | intent | lane | ph | fact | compl | help | pq | tone | overall |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        lines.append(
            f"| {r['ticket_id']} | {r.get('intent','')} | {r.get('lane','')} | "
            f"{r.get('placeholders','')} | {r.get('factual_correctness','-')} | "
            f"{r.get('completeness','-')} | {r.get('helpfulness','-')} | "
            f"{r.get('placeholder_quality','-')} | "
            f"{r.get('tone','-')} | {r.get('overall','-')} |"
        )
    lines += ["", "## Rationales", ""]
    for r in results:
        lines.append(f"- **{r['ticket_id']}** (overall {r.get('overall','-')}): {r.get('rationale','')}")
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n" + "\n".join(lines[:18]), flush=True)
    print(f"\n[done] report → {REPORT}", flush=True)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=None, help="override judge/draft model (else settings.LOCAL_MODEL_NAME)")
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()
    if args.model:
        settings.LOCAL_MODEL_NAME = args.model
    print(f"[config] model={settings.LOCAL_MODEL_NAME} base={settings.LOCAL_MODEL_BASE_URL} "
          f"query={settings.QUERY_STRATEGY} retrieval={settings.RETRIEVAL_BACKEND}", flush=True)
    rows = _load_testset(args.limit)
    await _seed_policies()
    drafts = await _regenerate(rows)
    results = await _judge(drafts)
    _write_report(results)
    try:
        os.unlink(_tmpdb)
    except OSError:
        pass


if __name__ == "__main__":
    asyncio.run(main())
