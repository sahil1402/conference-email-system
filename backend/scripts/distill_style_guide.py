"""Distill a reply style/instruction guide from the chair reply corpus.

Three resumable stages over data/tickets/marc_threads.jsonl (see
docs/PIPELINE_AUDIT.md step 3 and the Phase 7B build-order item 4):

  prep   Build PII-scrubbed (question -> reply) pairs. Near-duplicate replies
         (macro/template usage) are collapsed to one representative that
         carries its frequency, so canned patterns inform the guide without
         swamping it. Pairs are dealt into month-stratified batches.
         -> data/style_guide/pairs.jsonl, batches.json
  map    One extraction call per batch: voice / format / recurring norms with
         quoted evidence, with event-specific content flagged separately.
         -> data/style_guide/batches/batch_NN.md   (skips existing files)
  reduce Merge every batch profile into the final layered guide.
         -> data/style_guide/style_guide_v1.md + manifest.json

The guide is deliberately style-and-behavior only: policy content belongs to
retrieval, and event-specific text (e.g. a specific outage) is excluded so the
artifact does not go stale. Drafts must not be signed as any real person — the
signature convention uses a placeholder name.

Usage:
    python scripts/distill_style_guide.py all            # prep + map + reduce
    python scripts/distill_style_guide.py prep|map|reduce
    python scripts/distill_style_guide.py map --model <id>
"""

import argparse
import json
import re
import sys
import time
from collections import defaultdict
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parents[2]
SECRETS_PATH = REPO_ROOT / "docs" / "secrets.txt"
THREADS_PATH = REPO_ROOT / "data" / "tickets" / "marc_threads.jsonl"
OUT_DIR = REPO_ROOT / "data" / "style_guide"
BATCH_DIR = OUT_DIR / "batches"

API_URL = "https://api.openai.com/v1/chat/completions"
DEFAULT_MODEL = "gpt-5.5"

BATCH_SIZE = 50
MAX_PAIRS = 700
# Template clusters used at least this often are always included (as one
# representative each); rarer replies enter the singleton sampling pool.
TEMPLATE_MIN_COUNT = 3
QUESTION_CHARS = 700
REPLY_CHARS = 1800

# ---------------------------------------------------------------------------
# Scrubbing / normalization
# ---------------------------------------------------------------------------
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+")
_PHONE_RE = re.compile(r"\+?\d[\d\s().-]{7,}\d")
_GREETING_RE = re.compile(r"^(dear|hi|hello)\b[^,\n]{0,60}([,\n])", re.IGNORECASE)
# Common markers where quoted email history starts inside a comment body.
_QUOTE_MARKERS = ("\nOn ", "\n> ", "\n-----Original Message-----", "\nFrom: ")
# Sign-off markers — the INQUIRY is cut here so requester signature blocks
# (name, affiliation, phone) never reach the external API.
_SIGNOFF_RE = re.compile(
    r"\n\s*(best regards|best wishes|kind regards|warm regards|regards|best|"
    r"thanks|thank you|sincerely|cheers)\s*[,!.]?\s*\n",
    re.IGNORECASE,
)


def scrub(text: str) -> str:
    """Best-effort PII removal: addresses, phone-like numbers, greeting names."""
    text = _EMAIL_RE.sub("<email>", text)
    text = _PHONE_RE.sub("<phone>", text)
    return _GREETING_RE.sub(lambda m: f"{m.group(1)} <name>{m.group(2)}", text)


def strip_quoted_tail(text: str, cut_signoff: bool = True) -> str:
    """Cut an inquiry body at quoted history (and, optionally, the sign-off).

    ``cut_signoff=True`` (style distillation): signature blocks — name,
    affiliation, phone — never reach the external API. Eval/test paths pass
    ``False``: the system is tested on the exact email, sign-off included,
    and greeting population needs the requester's name.
    """
    cut = len(text)
    for marker in _QUOTE_MARKERS:
        idx = text.find(marker)
        if idx != -1:
            cut = min(cut, idx)
    if cut_signoff:
        m = _SIGNOFF_RE.search(text)
        if m:
            cut = min(cut, m.start())
    return text[:cut]


def template_key(text: str) -> str:
    """Normalization used to cluster near-duplicate (macro) replies."""
    t = text.lower()
    t = _EMAIL_RE.sub("<email>", t)
    t = re.sub(r"https?://\S+", "<url>", t)
    t = re.sub(r"\b(dear|hi|hello)\b[^,\n]{0,60}[,\n]", "<greeting>", t)
    t = re.sub(r"\d+", "<n>", t)
    return re.sub(r"\s+", " ", t).strip()


# ---------------------------------------------------------------------------
# Stage: prep
# ---------------------------------------------------------------------------
def build_pairs() -> None:
    """Extract, cluster, scrub, sample, and batch the (question, reply) pairs."""
    raw: list[dict] = []
    for line in open(THREADS_PATH, encoding="utf-8"):
        t = json.loads(line)
        if "closed_by_merge" in (t.get("tags") or []):
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
        if not reply.strip():
            continue
        raw.append(
            {
                "ticket_id": t["ticket_id"],
                "month": (t.get("created_at") or "")[:7],
                "subject": scrub(t.get("subject") or ""),
                "question": scrub(strip_quoted_tail(question))[:QUESTION_CHARS],
                "reply": scrub(reply)[:REPLY_CHARS],
            }
        )

    # Cluster near-duplicates; keep one representative per cluster + frequency.
    clusters: dict[str, list[dict]] = defaultdict(list)
    for r in raw:
        clusters[template_key(r["reply"])].append(r)

    templates: list[dict] = []
    singles: list[dict] = []
    for members in clusters.values():
        rep = members[0] | {"frequency": len(members)}
        (templates if len(members) >= TEMPLATE_MIN_COUNT else singles).append(rep)

    # Deterministic order (no RNG): spread singleton picks evenly over each
    # month's list so the sample covers the whole cycle, not one busy week.
    templates.sort(key=lambda r: (-r["frequency"], r["ticket_id"]))
    by_month: dict[str, list[dict]] = defaultdict(list)
    for r in sorted(singles, key=lambda r: r["ticket_id"]):
        by_month[r["month"]].append(r)

    budget = MAX_PAIRS - len(templates)
    total_singles = sum(len(v) for v in by_month.values())
    sampled: list[dict] = []
    for _, members in sorted(by_month.items()):
        quota = max(1, round(budget * len(members) / max(total_singles, 1)))
        step = max(1, len(members) // quota)
        sampled.extend(members[::step][:quota])

    pairs = templates + sampled
    # Deal round-robin so every batch mixes months, templates, and one-offs.
    batches: list[list[int]] = [[] for _ in range((len(pairs) + BATCH_SIZE - 1) // BATCH_SIZE)]
    for i, _ in enumerate(pairs):
        batches[i % len(batches)].append(i)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUT_DIR / "pairs.jsonl", "w", encoding="utf-8") as fh:
        for p in pairs:
            fh.write(json.dumps(p, ensure_ascii=False) + "\n")
    (OUT_DIR / "batches.json").write_text(json.dumps(batches))
    print(
        f"prep: {len(raw)} replies -> {len(clusters)} clusters "
        f"({len(templates)} templates >= x{TEMPLATE_MIN_COUNT}, {len(sampled)} sampled one-offs) "
        f"-> {len(pairs)} pairs in {len(batches)} batches"
    )


# ---------------------------------------------------------------------------
# API client
# ---------------------------------------------------------------------------
def read_key() -> str:
    for line in SECRETS_PATH.read_text().splitlines():
        line = line.strip()
        if line.startswith("sk-"):
            return line
    sys.exit(f"No API key found in {SECRETS_PATH}")


def chat(model: str, system: str, user: str, max_out: int) -> str:
    """One chat-completions call with 429/5xx retries. Raises after 5 attempts."""
    key = read_key()
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_completion_tokens": max_out,
    }
    for attempt in range(5):
        resp = httpx.post(
            API_URL,
            json=payload,
            headers={"Authorization": f"Bearer {key}"},
            timeout=300,
        )
        if resp.status_code == 400 and "max_completion_tokens" in resp.text:
            payload["max_tokens"] = payload.pop("max_completion_tokens")
            continue
        if resp.status_code in (429, 500, 502, 503):
            wait = int(resp.headers.get("Retry-After", 15 * (attempt + 1)))
            print(f"  HTTP {resp.status_code}; retrying in {wait}s", flush=True)
            time.sleep(wait)
            continue
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]
    raise RuntimeError("API call failed after retries")


# ---------------------------------------------------------------------------
# Stage: map
# ---------------------------------------------------------------------------
_MAP_SYSTEM = """\
You are analyzing how a conference helpdesk (workflow chairs of a large AI \
conference) answers inquiry emails, to extract their reply style.

You will receive numbered (INQUIRY -> REPLY) pairs. Replies marked \
"[used N times]" are canned templates the team reuses N times; weight them \
accordingly as evidence of standard practice.

Produce a compact profile in markdown with EXACTLY these sections:
## Voice & format
Greeting/sign-off conventions, length, paragraphing, pronouns (we vs I), \
tone (warmth, firmness, apology usage), formatting habits. Quote short \
evidence fragments.
## Behavioral norms
Recurring durable rules of HOW they handle situations (redirecting to proper \
channels, refusing side-channel requests, firmness on deadlines, reassurance, \
gratitude to volunteers, escalation phrasing). One bullet each, with a short \
quoted example. Do NOT restate conference policy content — only behavior.
## Event-specific content (to exclude)
Anything tied to a one-off incident, date, or person (outages, specific \
deadlines, named individuals) that must NOT enter a durable style guide.

Be concrete and evidence-based. Under 600 words."""


def run_map(model: str) -> None:
    pairs = [json.loads(l) for l in open(OUT_DIR / "pairs.jsonl", encoding="utf-8")]
    batches = json.loads((OUT_DIR / "batches.json").read_text())
    BATCH_DIR.mkdir(parents=True, exist_ok=True)

    for bi, idxs in enumerate(batches):
        out_path = BATCH_DIR / f"batch_{bi:02d}.md"
        if out_path.exists():
            print(f"map: batch {bi} exists, skipping")
            continue
        blocks = []
        for n, i in enumerate(idxs, 1):
            p = pairs[i]
            freq = f" [used {p['frequency']} times]" if p["frequency"] > 1 else ""
            blocks.append(
                f"### Pair {n} ({p['month']}){freq}\n"
                f"INQUIRY (subject: {p['subject']}):\n{p['question']}\n\n"
                f"REPLY:\n{p['reply']}"
            )
        result = chat(model, _MAP_SYSTEM, "\n\n".join(blocks), max_out=4000)
        out_path.write_text(result, encoding="utf-8")
        print(f"map: batch {bi} done ({len(idxs)} pairs)", flush=True)


# ---------------------------------------------------------------------------
# Stage: reduce
# ---------------------------------------------------------------------------
_REDUCE_SYSTEM = """\
You are writing the definitive reply style/instruction guide for an AI \
assistant that drafts replies for the workflow chairs of a large AI \
conference. Human chairs review every draft before sending.

You will receive style profiles extracted from many batches of real \
(inquiry -> reply) pairs. Merge them into ONE guide in markdown with EXACTLY \
this structure:

# Reply Style & Instruction Guide
## 1. Voice & format
The concrete conventions: greeting, sign-off, length, paragraphing, pronoun \
use, tone. The sign-off must use the literal placeholder "[Sender name]" — \
never a real person's name.
## 2. Behavioral rules
Numbered durable rules for how to handle situations (channel redirection, \
side-channel refusals, deadline firmness, reassurance when the system is at \
fault, gratitude to volunteers, when to state an action was taken vs \
forwarded). Each rule: one imperative sentence, optionally one short example \
phrase in quotes.
## 3. Hard constraints
- Never state a policy fact, deadline, date, or URL that is not present in \
the retrieved policy context provided with each draft (the guide is \
subordinate to grounding).
- Never reference one-off incidents from past conference cycles.
- Never include any real individual's name or contact details.
Plus any further exclusions the profiles support.

Rules for you: exclude ALL event-specific content the profiles flagged; \
exclude conference policy content (retrieval supplies it); resolve \
contradictions by majority evidence across profiles; keep the whole guide \
600–1000 words; output ONLY the guide markdown."""


def run_reduce(model: str) -> None:
    profiles = sorted(BATCH_DIR.glob("batch_*.md"))
    if not profiles:
        sys.exit("reduce: no batch profiles found — run `map` first")
    joined = "\n\n".join(
        f"<<< PROFILE {p.stem} >>>\n{p.read_text(encoding='utf-8')}" for p in profiles
    )
    guide = chat(model, _REDUCE_SYSTEM, joined, max_out=6000)
    guide_path = OUT_DIR / "style_guide_v1.md"
    guide_path.write_text(guide, encoding="utf-8")

    pairs_n = sum(1 for _ in open(OUT_DIR / "pairs.jsonl", encoding="utf-8"))
    (OUT_DIR / "manifest.json").write_text(
        json.dumps(
            {
                "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "source": "data/tickets/marc_threads.jsonl (PII-scrubbed pairs)",
                "pairs": pairs_n,
                "batch_profiles": len(profiles),
                "model": model,
                "output": "style_guide_v1.md",
                "note": "Guide is style/behavior only; policy content comes from retrieval.",
            },
            indent=2,
        )
    )
    print(f"reduce: wrote {guide_path} ({len(guide.split())} words) + manifest")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("stage", choices=["prep", "map", "reduce", "all"])
    parser.add_argument("--model", default=DEFAULT_MODEL)
    args = parser.parse_args()

    if args.stage in ("prep", "all"):
        build_pairs()
    if args.stage in ("map", "all"):
        run_map(args.model)
    if args.stage in ("reduce", "all"):
        run_reduce(args.model)


if __name__ == "__main__":
    main()
