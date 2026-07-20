"""Build the real-ticket evaluation set: sample + LLM-assisted labels.

Implements step 1 of the test roadmap in docs/PIPELINE_AUDIT.md. Two resumable
stages over data/tickets/marc_threads.jsonl and the real policy KB
(data/knowledge_base/policies.json):

  sample  ~200 answered threads, stratified by keyword-classifier intent with
          author-facing intents over-sampled (the corpus skews ~68%
          reviewer-ops where policy coverage is low). Per ticket, retrieval
          candidates = union of BM25 top-10 and dense top-10 over the 93 real
          chunks (intent-neutral query, so candidate generation does not
          inherit classifier bias).
          -> data/eval_real/sample.jsonl
  label   One LLM call per ticket, anchored on the chair's REAL reply (the
          reply defines the information need). Labels: policy_answerable /
          relevant_chunk_ids (multi-gold, from candidates or the full title
          catalog) / intent (11-way + other). Resumable; skips labeled ids.
          -> data/eval_real/labels.jsonl

Question/reply text is PII-scrubbed (same scrubbers as the style distillation)
before it reaches the external API. All outputs are gitignored (real PII).

Usage:
    python scripts/label_real_tickets.py sample
    python scripts/label_real_tickets.py label [--model <id>] [--workers 4]
"""

import argparse
import asyncio
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.pipeline.classifier import keyword_classify  # noqa: E402
from distill_style_guide import chat, scrub, strip_quoted_tail  # noqa: E402
from scripts._kb_retriever import build_retriever_from_kb  # noqa: E402

THREADS_PATH = REPO_ROOT / "data" / "tickets" / "marc_threads.jsonl"
KB_PATH = REPO_ROOT / "data" / "knowledge_base" / "policies.json"
OUT_DIR = REPO_ROOT / "data" / "eval_real"
SAMPLE_PATH = OUT_DIR / "sample.jsonl"
LABELS_PATH = OUT_DIR / "labels.jsonl"

DEFAULT_MODEL = "gpt-5.5"
EXCLUDED_TAGS = {"closed_by_merge", "system_email_notification_failure"}
MIN_QUESTION_CHARS = 40
MIN_REPLY_CHARS = 150

# Target counts per keyword-classifier intent (14-intent taxonomy; old labels
# remapped via the A6 table). Author-facing intents are over-sampled;
# reviewer_assignment (the largest real stratum) is capped and also serves as
# the fill pool if other strata come up short.
QUOTAS = {
    "cms_support": 30,
    "submission_requirements": 25,
    "submission_format_policy": 25,
    "review_submission_help": 20,
    "anonymity_violation": 15,
    "submission_upload_help": 15,
    "author_list_change": 10,
    "committee_invitation": 3,
    "reviewer_workload_role": 2,
    "paper_bidding": 2,
    "reviewer_assignment": 55,
}

INTENT_DEFS = """\
- submission_requirements: deadlines, eligibility, required steps, portal access, tracks, next steps for accepted papers
- submission_format_policy: page limits, templates, anonymization, appendices/checklists, supplementary format
- cms_support: CMT/OpenReview account, email-linking, site access, general workflow support; registration/attendance/proceedings questions
- reviewer_assignment: add/remove/replace/validate/locate reviewer or emergency-reviewer assignments
- review_submission_help: submitting reviews/meta-reviews, late/missing reviews, review-system access problems/outages
- paper_bidding: access/reopen/extend/correct the paper-bidding / reviewer-preference process
- author_profile_compliance: missing/invalid Scholar/DBLP profile ids, user-info/conflict/subject-area completion
- author_list_change: add/remove/reorder/correct authors or submission metadata after submission
- submission_upload_help: upload/replace/restore a paper, camera-ready, or supplementary file (incl. restoring a withdrawn submission)
- review_decision_appeal: appeals about review quality, rebuttal handling, scores, or the final decision
- desk_reject_appeal: explain/reconsider/reverse a desk rejection (formatting, page-limit, appendix, checklist grounds)
- anonymity_violation: reports a submission may break double-blind anonymity via identifying information/disclosures
- reviewer_workload_role: adjust review workload, volunteer as reviewer, or seek an elevated role (SPC / area chair)
- committee_invitation: accept/decline reviewer/PC/session-chair invitations, availability, resend/reactivate a link"""

_LABEL_SYSTEM = f"""\
You label conference-helpdesk tickets to build an evaluation set for a
policy-retrieval system. For each ticket you get: the requester's INQUIRY, the
REPLY the human workflow chair actually sent (authoritative — it defines what
information was needed), CANDIDATE policy chunks, and a CATALOG of every chunk
title in the knowledge base.

Return ONLY a JSON object:
{{
  "policy_answerable": true/false,   // can the substance of the needed answer be
                                     // found in the policy knowledge base? (false for
                                     // operational actions, account fixes, one-off
                                     // incidents, pure acknowledgements)
  "relevant_chunk_ids": ["policy_1xx", ...],  // chunks containing the information the
                                     // chair's reply conveys (or that directly answer
                                     // the inquiry). Usually 0-3. Prefer CANDIDATES;
                                     // add ids from the CATALOG only when a clearly
                                     // relevant section is missing from candidates.
                                     // Empty list if policy_answerable is false.
  "intent": "<one of the intents below, or 'other'>",
  "intent_other": "<short label if intent='other', else null>",
  "rationale": "<one sentence>"
}}

Intents:
{INTENT_DEFS}

Be strict about policy_answerable: reassurances, roster changes, and requests
whose answer required chair action or system investigation are NOT
policy-answerable even if the reply mentions a policy in passing."""


def load_threads() -> list[dict]:
    rows = []
    for line in open(THREADS_PATH, encoding="utf-8"):
        t = json.loads(line)
        if EXCLUDED_TAGS & set(t.get("tags") or []):
            continue
        comments = t.get("comments") or []
        question = next(
            ((c.get("body") or "") for c in comments if not c.get("is_marc")), ""
        )
        reply = next(
            (
                (c.get("body") or "")
                for c in comments
                if c.get("is_marc") and c.get("public")
            ),
            "",
        )
        # Keep the sign-off/signature: eval inquiries must be the exact email
        # (only quoted reply history is trimmed).
        question = strip_quoted_tail(question, cut_signoff=False).strip()
        if len(question) < MIN_QUESTION_CHARS or len(reply) < MIN_REPLY_CHARS:
            continue
        rows.append(
            {
                "ticket_id": t["ticket_id"],
                "month": (t.get("created_at") or "")[:7],
                "subject": t.get("subject") or "",
                "question": question,
                "reply": reply.strip(),
            }
        )
    return rows


def build_sample() -> None:
    rows = load_threads()
    by_intent: dict[str, list[dict]] = {}
    for r in rows:
        cls = keyword_classify(r["subject"], r["question"])
        r["kw_intent"] = cls.intent
        r["kw_confidence"] = round(cls.confidence, 3)
        by_intent.setdefault(cls.intent, []).append(r)

    picked: list[dict] = []
    shortfall = 0
    for intent, quota in QUOTAS.items():
        pool = sorted(by_intent.get(intent, []), key=lambda r: r["ticket_id"])
        if len(pool) <= quota:
            picked.extend(pool)
            shortfall += quota - len(pool)
        else:
            step = len(pool) // quota
            picked.extend(pool[::step][:quota])
    if shortfall:  # top up from the reviewer_assignment tail (largest pool)
        pool = sorted(by_intent.get("reviewer_assignment", []), key=lambda r: r["ticket_id"])
        already = {r["ticket_id"] for r in picked}
        extras = [r for r in pool if r["ticket_id"] not in already]
        picked.extend(extras[:: max(1, len(extras) // max(shortfall, 1))][:shortfall])

    # --- retrieval candidates over the REAL corpus (intent-neutral query) ---
    # Login-node etiquette: cap torch's thread pool BEFORE it spins up, and
    # encode all queries in ONE batch — per-row encode calls oversubscribe
    # every core via OMP spin-waits.
    import torch

    torch.set_num_threads(4)
    from sentence_transformers import SentenceTransformer  # heavy; import late
    import numpy as np

    bm25 = build_retriever_from_kb(KB_PATH)
    chunks = json.load(open(KB_PATH, encoding="utf-8"))
    embedder = SentenceTransformer("all-MiniLM-L6-v2", device="cpu")
    doc_vecs = embedder.encode(
        [f"{c['title']} {c['content']}" for c in chunks], normalize_embeddings=True
    )
    queries = [f"{r['subject']} {r['question'][:600]}" for r in picked]
    query_vecs = embedder.encode(queries, normalize_embeddings=True, batch_size=64)
    print(f"encoded {len(queries)} queries", flush=True)

    async def bm25_ids(query: str) -> list[str]:
        res = await bm25.retrieve(query, intent="", top_k=10)
        return [c.policy_id for c in res]

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(SAMPLE_PATH, "w", encoding="utf-8") as fh:
        for r, query, qv in zip(picked, queries, query_vecs):
            ids_b = asyncio.run(bm25_ids(query))
            dense_rank = np.argsort(-(doc_vecs @ qv).ravel())[:10]
            ids_f = [chunks[i]["id"] for i in dense_rank]
            union: list[str] = []
            for pair in zip(ids_b, ids_f):  # interleave, preserve both orders
                for pid in pair:
                    if pid not in union:
                        union.append(pid)
            r["candidates"] = union[:20]
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    from collections import Counter

    dist = Counter(r["kw_intent"] for r in picked)
    print(f"sample: {len(picked)} tickets -> {SAMPLE_PATH}")
    print("by kw_intent:", dict(dist.most_common()))


def build_user_prompt(row: dict, chunk_by_id: dict, catalog: str) -> str:
    cands = "\n\n".join(
        f"[{cid}] {chunk_by_id[cid]['title']}\n{chunk_by_id[cid]['content']}"
        for cid in row["candidates"]
    )
    return (
        f"SUBJECT: {scrub(row['subject'])}\n\n"
        f"INQUIRY:\n{scrub(row['question'])[:1200]}\n\n"
        f"CHAIR'S ACTUAL REPLY:\n{scrub(row['reply'])[:1500]}\n\n"
        f"CANDIDATE CHUNKS:\n{cands}\n\n"
        f"FULL TITLE CATALOG:\n{catalog}"
    )


def run_label(model: str, workers: int) -> None:
    rows = [json.loads(l) for l in open(SAMPLE_PATH, encoding="utf-8")]
    done: set[int] = set()
    if LABELS_PATH.exists():
        done = {
            json.loads(l)["ticket_id"] for l in open(LABELS_PATH, encoding="utf-8")
        }
    todo = [r for r in rows if r["ticket_id"] not in done]
    print(f"label: {len(todo)} to label ({len(done)} already done)")

    chunks = json.load(open(KB_PATH, encoding="utf-8"))
    chunk_by_id = {c["id"]: c for c in chunks}
    catalog = "\n".join(f"[{c['id']}] {c['title']}" for c in chunks)
    known_ids = set(chunk_by_id)

    lock = threading.Lock()

    def one(row: dict) -> None:
        raw = chat(model, _LABEL_SYSTEM, build_user_prompt(row, chunk_by_id, catalog),
                   max_out=1500)
        try:
            data = json.loads(raw[raw.index("{"): raw.rindex("}") + 1])
        except (ValueError, json.JSONDecodeError):
            print(f"  ticket {row['ticket_id']}: unparseable label, skipped", flush=True)
            return
        rec = {
            "ticket_id": row["ticket_id"],
            "policy_answerable": bool(data.get("policy_answerable")),
            "relevant_chunk_ids": [
                i for i in (data.get("relevant_chunk_ids") or []) if i in known_ids
            ],
            "intent": data.get("intent"),
            "intent_other": data.get("intent_other"),
            "rationale": data.get("rationale", ""),
            "kw_intent": row["kw_intent"],
            "kw_confidence": row["kw_confidence"],
        }
        with lock, open(LABELS_PATH, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    start = time.time()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for i, _ in enumerate(pool.map(one, todo), 1):
            if i % 20 == 0:
                print(f"  {i}/{len(todo)} labeled ({time.time()-start:.0f}s)", flush=True)
    print(f"label: done -> {LABELS_PATH}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("stage", choices=["sample", "label"])
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    if args.stage == "sample":
        build_sample()
    else:
        run_label(args.model, args.workers)


if __name__ == "__main__":
    main()
