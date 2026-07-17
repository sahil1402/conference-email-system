"""Chunk the real AAAI policy markdown documents into the knowledge-base schema.

Reads the six AAAI-27 policy docs (markdown with YAML frontmatter) and emits
data/knowledge_base/policies_aaai27.json in the exact schema of the existing
toy KB (id/category/title/content/source/tags), so BOTH retrieval backends work
unchanged: BM25 indexes title+content+tags from the JSON, and FAISS embeds
"title content" once the chunks are seeded into policy_documents.

Chunking rules (docs/PIPELINE_AUDIT.md step 2 · deployment TODO 3):
- Primary cut at `##` sections. Sections holding `###` subsections split at the
  subsection level (each is an individually meaningful unit, e.g. one ethics
  principle); the text between the `##` header and its first `###` becomes an
  "(intro)" chunk when substantive.
- Text before the first `##` (the document preamble) becomes a "— Overview"
  chunk when non-empty — it often carries real facts (dates, venue, format).
- Any leaf still over MAX_WORDS is packed into "(part n/m)" chunks at paragraph
  boundaries: the dense embedder (256-wordpiece window) truncates beyond
  roughly 200-220 words, so oversized chunks would silently lose tail text.
- Titles are contextual paths ("<Doc> — <Section> — <Subsection>") because the
  title is part of both retrievers' match text — it disambiguates same-topic
  sections across documents (e.g. page limits in the CFP vs the
  cross-reference guide).
- Ids continue the `policy_NNN` format (starting at 101, clear of the toy KB's
  001-045) so the drafter's citation regex keeps matching.

Usage:
    python scripts/chunk_policies.py [--policies-dir DIR] [--output FILE]
"""

import argparse
import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_POLICIES_DIR = REPO_ROOT.parent / "policies"
DEFAULT_OUTPUT = REPO_ROOT / "data" / "knowledge_base" / "policies_aaai27.json"

FIRST_ID = 101
# Leaf chunks above this word count get paragraph-packed into parts (dense
# embedding truncates at ~256 wordpieces ≈ 200-220 words incl. the title).
MAX_WORDS = 220
PART_TARGET_WORDS = 170
# An intro shorter than this (between a ## header and its first ###) is
# boilerplate lead-in, not worth its own chunk.
MIN_INTRO_WORDS = 25

_STOP = {"the", "a", "an", "of", "and", "or", "to", "in", "for", "on", "by", "vs"}


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Return ({title, tags}, body) from a markdown doc with YAML frontmatter."""
    m = re.match(r"\A---\n(.*?)\n---\n", text, flags=re.DOTALL)
    if not m:
        return {"title": "", "tags": []}, text
    meta: dict = {"title": "", "tags": []}
    for line in m.group(1).splitlines():
        if line.startswith("title:"):
            meta["title"] = line.split(":", 1)[1].strip().strip('"')
        elif line.strip().startswith("- "):
            meta["tags"].append(line.strip()[2:].strip())
    return meta, text[m.end():]


def heading_tags(heading: str) -> list[str]:
    """Lowercased content words of a heading — extra match vocabulary."""
    words = re.findall(r"[a-z][a-z-]+", heading.lower())
    return [w for w in words if w not in _STOP][:6]


def pack_paragraphs(text: str) -> list[str]:
    """Split text at paragraph boundaries into parts of ~PART_TARGET_WORDS."""
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    parts: list[list[str]] = [[]]
    count = 0
    for p in paras:
        words = len(p.split())
        if count and count + words > PART_TARGET_WORDS:
            parts.append([])
            count = 0
        parts[-1].append(p)
        count += words
    return ["\n\n".join(part) for part in parts if part]


def split_level(text: str, level: int) -> tuple[str, list[tuple[str, str]]]:
    """Split at `level` headings -> (text before first heading, [(heading, body)])."""
    marker = "#" * level + " "
    pattern = re.compile(rf"\n(?={re.escape(marker)})")
    first = text.find(f"\n{marker}")
    if text.startswith(marker):
        first = 0
    if first == -1:
        return text, []
    head = text[:first]
    sections = []
    for block in pattern.split(text[first:].lstrip("\n")):
        if not block.strip():
            continue
        lines = block.splitlines()
        heading = lines[0].lstrip("# ").strip()
        sections.append((heading, "\n".join(lines[1:]).strip()))
    return head.strip(), sections


class Chunker:
    def __init__(self) -> None:
        self.chunks: list[dict] = []
        self._next_id = FIRST_ID

    def emit(self, title: str, content: str, category: str, source: str,
             tags: list[str]) -> None:
        content = content.strip()
        if not content:
            return
        pieces = pack_paragraphs(content) if len(content.split()) > MAX_WORDS else [content]
        total = len(pieces)
        for i, piece in enumerate(pieces, 1):
            suffix = f" (part {i}/{total})" if total > 1 else ""
            self.chunks.append(
                {
                    "id": f"policy_{self._next_id:03d}",
                    "category": category,
                    "title": f"{title}{suffix}",
                    "content": piece,
                    "source": source,
                    "tags": tags,
                }
            )
            self._next_id += 1

    def chunk_document(self, path: Path) -> int:
        meta, body = parse_frontmatter(path.read_text(encoding="utf-8"))
        doc_title = meta["title"] or path.stem.replace("_", " ").title()
        category = path.stem
        source = f"AAAI-27 — {path.name}"
        base_tags = list(meta["tags"])
        before = len(self.chunks)

        preamble, sections = split_level(body, 2)
        if len(preamble.split()) >= MIN_INTRO_WORDS:
            self.emit(f"{doc_title} — Overview", preamble, category, source, base_tags)

        for heading, section_body in sections:
            sec_title = f"{doc_title} — {heading}"
            sec_tags = base_tags + heading_tags(heading)
            intro, subsections = split_level(section_body, 3)
            if not subsections:
                self.emit(sec_title, section_body, category, source, sec_tags)
                continue
            if len(intro.split()) >= MIN_INTRO_WORDS:
                self.emit(f"{sec_title} (intro)", intro, category, source, sec_tags)
            for sub_heading, sub_body in subsections:
                # #### blocks (rare, small) stay inside their ### chunk; the
                # part-packing above still bounds the final size.
                self.emit(
                    f"{sec_title} — {sub_heading}",
                    sub_body,
                    category,
                    source,
                    sec_tags + heading_tags(sub_heading),
                )
        return len(self.chunks) - before


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--policies-dir", type=Path, default=DEFAULT_POLICIES_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    chunker = Chunker()
    for path in sorted(args.policies_dir.glob("*.md")):
        n = chunker.chunk_document(path)
        print(f"{path.name}: {n} chunks")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(chunker.chunks, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    sizes = sorted(len(c["content"].split()) for c in chunker.chunks)
    over = sum(1 for s in sizes if s > MAX_WORDS)
    print(
        f"\ntotal: {len(chunker.chunks)} chunks -> {args.output}\n"
        f"words/chunk: min {sizes[0]} · median {sizes[len(sizes)//2]} · "
        f"max {sizes[-1]} · over {MAX_WORDS}: {over}"
    )


if __name__ == "__main__":
    main()
