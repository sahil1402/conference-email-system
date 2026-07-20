"""Phase 1 — taxonomy mining over the Marc Q->A corpus (CM1).

Reads data/mining/marc_qa.jsonl (Phase 0). For each requester question:
  - embeds it (MiniLM, CPU) and clusters the corpus (KMeans, k by silhouette);
  - labels it with the *keyword* classifier (the 14 VALID_INTENTS, NO LLM) so we
    can cross-tab discovered clusters against today's taxonomy for free;
  - scores its max cosine similarity to the 93-chunk KB (leaf-title embed) as a
    coverage proxy.

Outputs (gitignored) data/mining/taxonomy_report.json + a PII-safe console
summary: per-cluster size / top terms / dominant keyword-intent / catch-all rate /
mean KB similarity, plus overall keyword-intent coverage and chair-tag frequency.
This is the evidence for the A3 taxonomy-refresh analysis; no LLM, no Phase 2.

Usage:
    cd backend
    export PATH=/u/jpang1/miniconda3/envs/autoexp/bin:$PATH
    OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 RAYON_NUM_THREADS=1 \
      TOKENIZERS_PARALLELISM=false HF_HUB_OFFLINE=1 python scripts/mine_taxonomy.py
"""
import json
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.pipeline.classifier import VALID_INTENTS, keyword_classify  # noqa: E402
from app.pipeline.taxonomy import FALLBACK_INTENT  # noqa: E402

MINING = REPO_ROOT / "data" / "mining"
QA_PATH = MINING / "marc_qa.jsonl"
VEC_PATH = MINING / "marc_q_vecs.npy"
KB_PATH = REPO_ROOT / "data" / "knowledge_base" / "policies.json"
REPORT_PATH = MINING / "taxonomy_report.json"

K_GRID = [12, 16, 20, 24, 30, 36]
leaf = lambda title: " — ".join(title.split(" — ")[1:]) or title


def main() -> None:
    import numpy as np
    from sentence_transformers import SentenceTransformer
    from sklearn.cluster import KMeans
    from sklearn.metrics import silhouette_score
    from sklearn.feature_extraction.text import TfidfVectorizer

    rows = [json.loads(l) for l in open(QA_PATH, encoding="utf-8")]
    n = len(rows)
    print(f"loaded {n} Q->A rows")

    embedder = SentenceTransformer("all-MiniLM-L6-v2", device="cpu")

    # --- embed questions (subject + opening ask), cached ---
    texts = [f"{r['subject']}. {r['question'][:500]}" for r in rows]
    if VEC_PATH.exists():
        vecs = np.load(VEC_PATH)
        if vecs.shape[0] != n:
            vecs = None
    else:
        vecs = None
    if vecs is None:
        vecs = embedder.encode(texts, normalize_embeddings=True, show_progress_bar=False,
                               batch_size=64)
        np.save(VEC_PATH, vecs)
    print(f"embeddings: {vecs.shape}")

    # --- keyword-intent (the 11), no LLM ---
    kw = [keyword_classify(r["subject"], r["question"]) for r in rows]
    intents = [c.intent for c in kw]
    # A "catch-all" hit = the FALLBACK_INTENT from a zero-keyword match (conf 0.3).
    catchall = [c.intent == FALLBACK_INTENT and c.reasoning.startswith("No policy")
                for c in kw]

    # --- pick k by silhouette on a sample, then fit full ---
    rng = np.random.RandomState(42)
    samp = rng.choice(n, size=min(1500, n), replace=False)
    best_k, best_s = K_GRID[0], -1.0
    for k in K_GRID:
        km = KMeans(n_clusters=k, random_state=42, n_init=4).fit(vecs[samp])
        s = silhouette_score(vecs[samp], km.labels_)
        print(f"  k={k:>2}  silhouette={s:.3f}")
        if s > best_s:
            best_k, best_s = k, s
    print(f"chosen k={best_k} (silhouette {best_s:.3f})")
    km = KMeans(n_clusters=best_k, random_state=42, n_init=8).fit(vecs)
    labels = km.labels_

    # --- KB coverage: max cosine sim of each question to the 93 leaf chunks ---
    chunks = json.load(open(KB_PATH, encoding="utf-8"))
    doc_vecs = embedder.encode([f"{leaf(c['title'])} {c['content']}" for c in chunks],
                               normalize_embeddings=True, show_progress_bar=False)
    max_sim = (vecs @ doc_vecs.T).max(axis=1)  # cosine (normalized) — higher = covered

    # --- per-cluster TF-IDF top terms ---
    tfidf = TfidfVectorizer(stop_words="english", token_pattern=r"[a-zA-Z][a-zA-Z]{2,}",
                            min_df=5, max_df=0.5)
    X = tfidf.fit_transform([r["question"] for r in rows])
    terms = tfidf.get_feature_names_out()

    def top_terms(idx_mask, m=6):
        sub = X[idx_mask]
        if sub.shape[0] == 0:
            return []
        mean = np.asarray(sub.mean(axis=0)).ravel()
        return [terms[i] for i in mean.argsort()[::-1][:m]]

    clusters = []
    for cid in range(best_k):
        mask = labels == cid
        idx = np.where(mask)[0]
        ci = Counter(intents[i] for i in idx)
        dom_intent, dom_n = ci.most_common(1)[0]
        tag_counter = Counter(t for i in idx for t in rows[i]["tags"])
        cyc = Counter(rows[i]["cycle"] for i in idx)
        clusters.append({
            "cluster": cid,
            "size": int(mask.sum()),
            "top_terms": top_terms(mask),
            "dominant_kw_intent": dom_intent,
            "dominant_pct": round(100 * dom_n / mask.sum(), 1),
            "catchall_pct": round(100 * sum(catchall[i] for i in idx) / mask.sum(), 1),
            "mean_kb_sim": round(float(max_sim[mask].mean()), 3),
            "top_tags": tag_counter.most_common(4),
            "cycle": dict(cyc),
        })
    clusters.sort(key=lambda c: -c["size"])

    # --- overall aggregates ---
    intent_dist = Counter(intents)
    all_tags = Counter(t for r in rows for t in r["tags"])
    overall = {
        "n": n,
        "kw_intent_distribution": dict(intent_dist.most_common()),
        "catchall_rate": round(100 * sum(catchall) / n, 1),
        "fallback_intent_rate": round(100 * intent_dist[FALLBACK_INTENT] / n, 1),
        "mean_kb_sim": round(float(max_sim.mean()), 3),
        "low_kb_cov_rate(<0.35)": round(100 * float((max_sim < 0.35).mean()), 1),
        "top_chair_tags": all_tags.most_common(25),
        "n_rows_tagged": sum(1 for r in rows if r["tags"]),
    }

    REPORT_PATH.write_text(json.dumps(
        {"overall": overall, "k": best_k, "silhouette": round(best_s, 3),
         "clusters": clusters}, indent=2))

    # --- console summary (PII-safe: aggregates + topic terms only) ---
    print("\n================ OVERALL ================")
    print(f"n={n}  chosen k={best_k}")
    print(f"keyword-intent distribution (of the 14): {overall['kw_intent_distribution']}")
    print(f"catch-all (zero-keyword-match -> {FALLBACK_INTENT}): {overall['catchall_rate']}%")
    print(f"{FALLBACK_INTENT} total: {overall['fallback_intent_rate']}%")
    print(f"mean KB max-sim: {overall['mean_kb_sim']}   low-coverage(<0.35): {overall['low_kb_cov_rate(<0.35)']}%")
    print(f"rows with >=1 chair tag: {overall['n_rows_tagged']}/{n}")
    print(f"top chair tags: {overall['top_chair_tags']}")
    print("\n================ CLUSTERS (by size) ================")
    print(f"{'cl':>3} {'size':>5} {'domIntent':>22} {'dom%':>5} {'catch%':>6} {'kbSim':>6}  top-terms")
    for c in clusters:
        print(f"{c['cluster']:>3} {c['size']:>5} {c['dominant_kw_intent']:>22} "
              f"{c['dominant_pct']:>5} {c['catchall_pct']:>6} {c['mean_kb_sim']:>6}  "
              f"{', '.join(c['top_terms'])}")
    print(f"\nreport -> {REPORT_PATH}")


if __name__ == "__main__":
    main()
