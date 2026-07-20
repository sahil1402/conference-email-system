"""Inbound-only refinement — filter non-inbound tickets, then re-cluster.

Takes the Phase-0b corpus (all_inbound.jsonl) + its cached embeddings and:
  1. drops conference-generated bulk outbound (`via.channel == 'api'` — e.g. SPC
     recommendation / committee-inactivity reminders), and
  2. drops content-empty artifacts (auto-notifications, pure forwarded wrappers,
     near-empty bodies) that arrived as email but carry no genuine ask,
then re-clusters the survivors (KMeans, reusing the cached embedding vectors — no
re-embed). Emits clean corpus + clusters for the LLM naming step (Phase 2).

Outputs (gitignored): data/mining/inbound_clean.jsonl,
data/mining/inbound_clean_clusters.json.

Usage:  cd backend && OMP_NUM_THREADS=4 python scripts/mine_recluster_inbound.py
"""
import json
import re
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "backend"))

MINING = REPO_ROOT / "data" / "mining"
INBOUND = MINING / "all_inbound.jsonl"
VEC_PATH = MINING / "all_inbound_vecs.npy"
TICKETS = REPO_ROOT / "data" / "tickets" / "tickets.jsonl"
OUT_ROWS = MINING / "inbound_clean.jsonl"
OUT_CLUSTERS = MINING / "inbound_clean_clusters.json"

K = 50
N_REPS = 12
MIN_ASK_CHARS = 40

# content-empty / automated markers (case-insensitive)
_AUTO = re.compile(
    r"femailservice|fnotifications|held for moderation|message held|quarantine|"
    r"mailer-daemon|undeliverable|delivery (has )?failed|automatic reply|out of office|"
    r"auto-?reply|read receipt", re.I)
_FWD_WRAPPER = re.compile(r"^\s*(begin forwarded message|-+ ?forwarded message)", re.I)


def is_noise(row: dict, channel: str | None) -> str | None:
    """Return a drop-reason if this row is not a genuine inbound ask, else None."""
    if channel == "api":
        return "api_outbound"
    body = row["question"]
    if len(body.strip()) < MIN_ASK_CHARS:
        return "too_short"
    if _AUTO.search(body):
        return "auto_notification"
    if _FWD_WRAPPER.match(body) and len(body) < 250:
        return "forward_wrapper"
    return None


def main() -> None:
    import numpy as np
    from sklearn.cluster import KMeans
    from sklearn.feature_extraction.text import TfidfVectorizer

    channel = {}
    for t in map(json.loads, open(TICKETS, encoding="utf-8")):
        channel[t["id"]] = (t.get("via") or {}).get("channel")

    rows = [json.loads(l) for l in open(INBOUND, encoding="utf-8")]
    vecs = np.load(VEC_PATH)
    assert vecs.shape[0] == len(rows), "embedding/rows mismatch"

    keep_idx, drops = [], Counter()
    for i, r in enumerate(rows):
        reason = is_noise(r, channel.get(r["ticket_id"]))
        if reason:
            drops[reason] += 1
        else:
            keep_idx.append(i)

    print(f"kept {len(keep_idx)} / {len(rows)}   drops: {dict(drops)}")
    clean = [rows[i] for i in keep_idx]
    cvecs = vecs[keep_idx]
    with open(OUT_ROWS, "w", encoding="utf-8") as f:
        for r in clean:
            f.write(json.dumps(r) + "\n")

    km = KMeans(n_clusters=K, random_state=42, n_init=4).fit(cvecs)
    labels, centers = km.labels_, km.cluster_centers_

    tfidf = TfidfVectorizer(stop_words="english", token_pattern=r"[a-zA-Z][a-zA-Z]{2,}",
                            min_df=5, max_df=0.4)
    X = tfidf.fit_transform([r["question"] for r in clean])
    terms = tfidf.get_feature_names_out()

    clusters = []
    for cid in range(K):
        idx = np.where(labels == cid)[0]
        if len(idx) == 0:
            continue
        sims = cvecs[idx] @ centers[cid]
        rep = idx[np.argsort(-sims)[:N_REPS]]
        mean = np.asarray(X[idx].mean(axis=0)).ravel()
        clusters.append({
            "cluster": int(cid),
            "size": int(len(idx)),
            "top_terms": [terms[i] for i in mean.argsort()[::-1][:8]],
            "rep_indices": [int(i) for i in rep],   # indices into inbound_clean.jsonl
            "top_tags": Counter(t for i in idx for t in clean[i]["tags"]).most_common(5),
            "cycle": dict(Counter(clean[i]["cycle"] for i in idx)),
        })
    clusters.sort(key=lambda c: -c["size"])
    OUT_CLUSTERS.write_text(json.dumps({"k": K, "n": len(clean), "clusters": clusters}, indent=2))
    print(f"wrote {len(clusters)} clusters -> {OUT_CLUSTERS}")
    print("sizes (top 10):", [c["size"] for c in clusters[:10]])


if __name__ == "__main__":
    main()
