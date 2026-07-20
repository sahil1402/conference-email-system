"""Phase 2c — consolidate to 12-15 GENERAL, year-persistent intents.

Goal (per instruction): the taxonomy should be general inquiry TYPES that recur
across conference years; specific one-off incidents (an outage, a single-year
special track) must NOT become their own intents — they fold into the general type
they exemplify.

Method: reconstruct the 50 clean inbound clusters deterministically (same filter +
cached embeddings + KMeans seed as mine_recluster_inbound), compute each cluster's
**year spread** (2021-2026) as a persistence signal, then a single LLM consolidation
call merges the 50 named clusters into 12-15 general year-persistent intents,
abstracting incidents and folding/excluding single-year clusters. Finally validate
each induced intent's year spread.

Inputs (gitignored): all_inbound.jsonl, all_inbound_vecs.npy, cluster_names_clean.jsonl.
Output: data/mining/taxonomy_consolidated.json + console.

Usage: cd backend && MODEL_PROVIDER=local OMP_NUM_THREADS=4 HF_HUB_OFFLINE=1 \
         python scripts/data_mining/mine_consolidate.py
"""
import asyncio
import json
import sys
from collections import Counter
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.core.config import settings  # noqa: E402
from app.pipeline.openai_compat import post_chat  # noqa: E402
from scripts.data_mining.mine_recluster_inbound import is_noise, K  # noqa: E402
from scripts.data_mining.mine_llm_taxonomy import _chat, _json_from  # noqa: E402

MINING = REPO_ROOT / "data" / "mining"
INBOUND = MINING / "all_inbound.jsonl"
VEC_PATH = MINING / "all_inbound_vecs.npy"
CLEAN_CLUSTERS = MINING / "inbound_clean_clusters.json"
NAMES = MINING / "cluster_names_clean.jsonl"
OUT = MINING / "taxonomy_consolidated.json"

# A year counts toward "span" if it holds >=5% of the cluster; persistent = the
# type shows up in >=3 distinct years (of the 6 available, 2021-2026).
YEAR_SHARE_MIN = 0.05
PERSIST_MIN_YEARS = 3


def year_profile(year_counts: Counter):
    total = sum(year_counts.values())
    span = [y for y, c in year_counts.items() if c / total >= YEAR_SHARE_MIN]
    max_share = max(year_counts.values()) / total
    return sorted(span), round(max_share, 2), total


def reconstruct_clusters():
    import numpy as np
    from sklearn.cluster import KMeans

    rows = [json.loads(l) for l in open(INBOUND, encoding="utf-8")]
    vecs = np.load(VEC_PATH)
    channel = {t["id"]: (t.get("via") or {}).get("channel")
               for t in map(json.loads, open(REPO_ROOT / "data" / "tickets" / "tickets.jsonl", encoding="utf-8"))}
    keep = [i for i, r in enumerate(rows) if not is_noise(r, channel.get(r["ticket_id"]))]
    clean = [rows[i] for i in keep]
    cvecs = vecs[keep]
    labels = KMeans(n_clusters=K, random_state=42, n_init=4).fit_predict(cvecs)

    # sanity: cluster sizes must match the recluster output that named the clusters
    want = {c["cluster"]: c["size"] for c in json.loads(CLEAN_CLUSTERS.read_text())["clusters"]}
    got = Counter(int(l) for l in labels)
    if want != dict(got):
        print("WARN: cluster sizes differ from recluster output — id alignment risk")
    year_by_cluster = {}
    for cid in range(K):
        idx = [i for i, l in enumerate(labels) if l == cid]
        year_by_cluster[cid] = Counter(clean[i]["year"] for i in idx)
    return year_by_cluster


_SYS = (
    "You are consolidating an over-clustered set of conference help-desk email topics "
    "into a COMPACT, GENERAL taxonomy of recurring inquiry types. Rules:\n"
    "1. Produce BETWEEN 12 AND 15 intents total (hard limit).\n"
    "2. Each intent must be a GENERAL inquiry TYPE that recurs across conference years "
    "— never a specific incident.\n"
    "3. Specific one-off incidents (a single-year system outage, a one-time special "
    "track, a specific deadline event) must NOT be their own intent. Fold them into the "
    "general type they exemplify (e.g. an OpenReview outage -> a general "
    "'review-system access problem' intent).\n"
    "4. Use each cluster's year_span: a cluster confined to 1-2 years is likely an "
    "incident — fold it into a general intent, or if it has no general home, put its id "
    "in excluded_cluster_ids.\n"
    "5. Merge near-duplicates; group intents under a few families.\n"
    "Output ONLY JSON: {\"taxonomy\":[{\"family\":\"..\",\"intent\":\"<snake_case>\","
    "\"definition\":\"<one sentence>\",\"cluster_ids\":[<int>..]}],"
    "\"excluded_cluster_ids\":[<int>..]}. Every input cluster id must appear exactly "
    "once, in one intent's cluster_ids OR in excluded_cluster_ids."
)


async def main():
    names = {o["cluster"]: o for o in map(json.loads, open(NAMES, encoding="utf-8"))}
    years = reconstruct_clusters()

    lines = []
    for cid, o in sorted(names.items(), key=lambda kv: -kv[1]["size"]):
        span, max_share, total = year_profile(years[cid])
        persist = "yes" if len(span) >= PERSIST_MIN_YEARS else "NO(incident?)"
        yr = ",".join(f"{y}:{years[cid][y]}" for y in sorted(years[cid]))
        lines.append(f"id={cid} size={o['size']} label={o['label']} :: {o['definition']} "
                     f"| years[{yr}] span={len(span)} max_share={max_share} persistent={persist}")
    user = "Clusters (with year spread across 2021-2026):\n" + "\n".join(lines)

    async with httpx.AsyncClient(timeout=180) as client:
        raw = await _chat(client, _SYS, user, max_tokens=6000)
    merged = _json_from(raw or "") or {}
    taxonomy = merged.get("taxonomy", [])
    excluded = merged.get("excluded_cluster_ids", [])

    # validate: aggregate year spread + volume per induced intent
    for t in taxonomy:
        agg = Counter()
        for cid in t.get("cluster_ids", []):
            agg += years.get(cid, Counter())
        span, max_share, vol = year_profile(agg) if agg else ([], 0, 0)
        t["volume"] = vol
        t["year_span"] = sorted(span)
        t["persistent"] = len(span) >= PERSIST_MIN_YEARS
        t["max_year_share"] = max_share
    taxonomy.sort(key=lambda t: -t["volume"])
    OUT.write_text(json.dumps({"taxonomy": taxonomy, "excluded_cluster_ids": excluded}, indent=2))

    excl_vol = sum(sum(years.get(c, Counter()).values()) for c in excluded)
    print(f"consolidated to {len(taxonomy)} intents; excluded {len(excluded)} clusters "
          f"(~{excl_vol} tickets)\n")
    fam = None
    for t in taxonomy:
        if t.get("family") != fam:
            fam = t.get("family"); print(f"\n[{fam}]")
        flag = "" if t["persistent"] else "  <-- NOT year-persistent!"
        print(f"  {t['volume']:>5}  {t['intent']:<40} yrs={t['year_span']} "
              f"maxshr={t['max_year_share']}{flag}")
        print(f"         {t.get('definition','')}")
    if excluded:
        print(f"\nexcluded as incident/one-off (cluster ids): {excluded}")
    print(f"\n-> {OUT}")


if __name__ == "__main__":
    asyncio.run(main())
