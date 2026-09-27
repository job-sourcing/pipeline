"""Prompt assets — prompts are first-class versioned files (D11).

Every prompt lives in jobsearch/prompts/*.md with frontmatter:
    ---
    version: <int>
    source: <provenance>
    ---

The version feeds the LLM cache key (D8) so prompt edits invalidate cache.
Prompts are NEVER inline strings.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


@dataclass(frozen=True)
class PromptAsset:
    name: str
    version: str
    template: str
    source: str = ""


def load_prompt(name: str, prompts_dir: Path | None = None) -> PromptAsset:
    path = (prompts_dir or PROMPTS_DIR) / f"{name}.md"
    text = path.read_text(encoding="utf-8")
    m = _FRONTMATTER_RE.match(text)
    meta: dict[str, str] = {}
    body = text
    if m:
        for line in m.group(1).splitlines():
            if ":" in line:
                k, _, v = line.partition(":")
                meta[k.strip()] = v.strip()
        body = text[m.end():]
    return PromptAsset(
        name=name,
        version=meta.get("version", "0"),
        template=body.strip(),
        source=meta.get("source", ""),
    )
