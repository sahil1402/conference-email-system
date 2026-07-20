"""Phase 1b — embed + over-cluster all inbound questions (conference-wide).

Reads data/mining/all_inbound.jsonl (Phase 0b). Embeds each question (MiniLM, CPU,
local — no egress), then **over-clusters** with KMeans (k=50 by default) so distinct
sub-topics stay separated; the LLM merge pass (Phase 2) collapses them into a clean
taxonomy. Emits per-cluster metadata + representative example row-indices for the
LLM naming step.

Outputs (gitignored):
  data/mining/all_inbound_vecs.npy       cached embeddings (resumable)
  data/mining/all_clusters.json          {k, clusters:[{cluster,size,top_terms,
                                          rep_indices,top_tags,cycle}]}

No model calls. Usage (background, thread caps):
    cd backend && OMP_NUM_THREADS=4 HF_HUB_OFFLINE=1 python scripts/mine_cluster_all.py
"""
import json
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "backend"))

MINING = REPO_ROOT / "data" / "mining"
INBOUND = MINING / "all_inbound.jsonl"
VEC_PATH = MINING / "all_inbound_vecs.npy"
OUT_PATH = MINING / "all_clusters.json"

K = 50            # deliberate over-clustering; LLM merges in Phase 2
N_REPS = 12       # representative examples per cluster for the LLM namer


def main() -> None:
    import numpy as np
    from sentence_transformers import SentenceTransformer
    from sklearn.cluster import KMeans
    from sklearn.feature_extraction.text import TfidfVectorizer

    rows = [json.loads(l) for l in open(INBOUND, encoding="utf-8")]
    n = len(rows)
    print(f"loaded {n} inbound questions")

    embedder = SentenceTransformer("all-MiniLM-L6-v2", device="cpu")
    if VEC_PATH.exists() and np.load(VEC_PATH).shape[0] == n:
        vecs = np.load(VEC_PATH)
        print("using cached embeddings")
    else:
        texts = [f"{r['subject']}. {r['question'][:500]}" for r in rows]
        vecs = embedder.encode(texts, normalize_embeddings=True, batch_size=64,
                               show_progress_bar=False)
        np.save(VEC_PATH, vecs)
    print(f"embeddings: {vecs.shape}")

    km = KMeans(n_clusters=K, random_state=42, n_init=4).fit(vecs)
    labels = km.labels_
    centers = km.cluster_centers_  # not normalized, but fine for nearest-by-dot

    tfidf = TfidfVectorizer(stop_words="english", token_pattern=r"[a-zA-Z][a-zA-Z]{2,}",
                            min_df=5, max_df=0.4)
    X = tfidf.fit_transform([r["question"] for r in rows])
    terms = tfidf.get_feature_names_out()

    clusters = []
    for cid in range(K):
        idx = np.where(labels == cid)[0]
        if len(idx) == 0:
            continue
        # representative examples: members nearest this cluster's center
        sims = vecs[idx] @ centers[cid]
        rep_local = idx[np.argsort(-sims)[:N_REPS]]
        # top TF-IDF terms for the cluster
        mean = np.asarray(X[idx].mean(axis=0)).ravel()
        top_terms = [terms[i] for i in mean.argsort()[::-1][:8]]
        tag_counter = Counter(t for i in idx for t in rows[i]["tags"])
        cyc = Counter(rows[i]["cycle"] for i in idx)
        clusters.append({
            "cluster": int(cid),
            "size": int(len(idx)),
            "top_terms": top_terms,
            "rep_indices": [int(i) for i in rep_local],
            "top_tags": tag_counter.most_common(5),
            "cycle": dict(cyc),
        })
    clusters.sort(key=lambda c: -c["size"])

    OUT_PATH.write_text(json.dumps({"k": K, "n": n, "clusters": clusters}, indent=2))
    print(f"wrote {len(clusters)} clusters -> {OUT_PATH}")
    print("sizes (top 10):", [c["size"] for c in clusters[:10]])


if __name__ == "__main__":
    main()
