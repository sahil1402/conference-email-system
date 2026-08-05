"""READ-ONLY filter: keep only findings NOT already handled in the prior
2026-07-26 discovery/republish batch (124 comment ids, from the report
Marc reviewed and confirmed, minus the 2 hard-excluded test tickets which
are also listed here for completeness).

This does not touch Zendesk at all -- it only reads a local JSON report
file and prints/writes a filtered version. Safe to run anytime.

Usage:
    python scripts/recovery/filter_new_findings.py \
        --report scripts/recovery/output/internal_notes_20260805_004746.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Reuse the exact same Markdown renderer Marc already reviewed once, so
# today's report looks identical in style/format to the 2026-07-26 one --
# no need to re-explain the layout to him.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from find_internal_notes import render_markdown  # noqa: E402

# Comment ids from the 2026-07-26T20:39:47Z report (124 total, across 123
# tickets), transcribed from the reviewed/confirmed PDF. These are the ones
# already found + already republished (or hard-excluded as test artifacts:
# tickets #22655 and #22380). Anything with a comment_id in this set is OLD,
# not a new mistake.
ALREADY_HANDLED_COMMENT_IDS: frozenset[int] = frozenset({
    53421371530779, 53421609684635,  # #22655 -- excluded test ticket
    53410141251995,  # #22380 -- excluded test ticket
    53499343743899, 53500215844507, 53500245500955, 53504209989275,
    53504232545947, 53504249661467, 53504327600539, 53504427786651,
    53505295463451, 53505537776667, 53505634234395, 53505655963035,
    53505716115739, 53505753831963, 53505801901595, 53505825938971,
    53505988561051, 53518094576027, 53518051287451, 53518083140635,
    53518165965595, 53518191092507, 53518265343131, 53518429967003,
    53518406271259, 53518449329051, 53518427071515, 53518493512347,
    53518555143579, 53518542618267, 53518543834779, 53518662504475,
    53518770997915, 53518777791131, 53519044062235, 53534603287067,
    53534631249563, 53534610187419, 53534914855067, 53534940189083,
    53534976693659, 53535000353051, 53535001679771, 53535004279195,
    53535046113435, 53535193193499, 53535215996699, 53535220088987,
    53535211495451, 53535435048475, 53535472759963, 53535818148379,
    53536695111579, 53536690624539, 53536680058779, 53536786202139,
    53536741795611, 53536847949467, 53536931743515, 53536965268635,
    53537144173339, 53537164550555, 53538677247131, 53538684807451,
    53538702748059, 53540957847451, 53540974918171, 53543464295195,
    53543457252379, 53543622460571, 53543711218075, 53543771037083,
    53544007202203, 53543994291739, 53544034396827, 53544053771547,
    53543497917851, 53551872993819, 53551933409307, 53551943365147,
    53552007306011, 53552098061851, 53552146856987, 53552833759387,
    53552882568987, 53552873856411, 53552916406811, 53552893550235,
    53552975128987, 53552980438555, 53552996280859, 53553088955035,
    53553196216475, 53553150000411, 53553264369307, 53553265084187,
    53553320136219, 53553430354075, 53553479289115, 53553464564123,
    53553666266139, 53553681622555, 53553743996187, 53553752149531,
    53553794234907, 53553846615707, 53553847454619, 53553910506523,
    53553902352283, 53553996081691, 53553970562971, 53554043509531,
    53554022448027, 53554061792155, 53554038161179, 53554096021275,
    53554070592539, 53554071691419, 53554111726363, 53554103738907,
    53535547606427,
})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=None,
                         help="Where to write the filtered JSON (default: alongside input, _new-only suffix)")
    args = parser.parse_args()

    data = json.loads(args.report.read_text(encoding="utf-8"))
    findings = data.get("findings", [])

    new_findings = [
        f for f in findings
        if f.get("comment_id") not in ALREADY_HANDLED_COMMENT_IDS
    ]
    already_seen = [
        f for f in findings
        if f.get("comment_id") in ALREADY_HANDLED_COMMENT_IDS
    ]

    print(f"Total findings in report : {len(findings)}")
    print(f"Already handled (old)    : {len(already_seen)}")
    print(f"Genuinely new            : {len(new_findings)}")
    print()

    if new_findings:
        print("New tickets/comments:")
        for f in new_findings:
            print(f"  #{f.get('ticket_id')} — comment {f.get('comment_id')} — {f.get('created_at')}")

    out_path = args.output or args.report.with_name(args.report.stem + "_new_only.json")
    out_data = dict(data)
    out_data["findings"] = new_findings
    out_data["affected_ticket_ids"] = sorted({f.get("ticket_id") for f in new_findings})
    # Keep stats honest for the new, smaller set (tickets/comments scanned
    # stays as-is -- that reflects the real sweep size -- but matches/
    # tickets_with_matches should reflect only the new findings).
    if "stats" in out_data:
        out_data["stats"] = dict(out_data["stats"])
        out_data["stats"]["matches"] = len(new_findings)
        out_data["stats"]["tickets_with_matches"] = len(out_data["affected_ticket_ids"])
    out_path.write_text(json.dumps(out_data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"\nFiltered JSON written: {out_path}")

    md_path = out_path.with_suffix(".md")
    md_path.write_text(render_markdown(out_data), encoding="utf-8")
    print(f"Filtered Markdown written: {md_path}  <-- send this to Marc")


if __name__ == "__main__":
    main()