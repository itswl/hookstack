#!/usr/bin/env python3
"""docs/index.md and docs/zh/index.md are the READMEs, re-addressed for the site.

Four front pages were kept by hand until 2026-10-06: the two READMEs and the
two pages the docs site serves from /docs. Every sentence on the front page
was written four times, and a checker existed whose only job was to notice
when the four disagreed about a number. Two sources is the honest count — one
per language — and the site's pages are a transformation of them:

  - the H1 and the CI badges go (the site has a masthead and no CI column);
  - a front-matter block and the site's own language switch replace the
    README's;
  - links into `docs/` lose the prefix (the page already lives there), and
    links to the service directories become plain names (the site has no
    directories to link into);
  - the tail — Read more and Developing — is the site's own, with absolute
    links back to the repository, because the site cannot link to files it
    does not serve.

The owner's name in those absolute links is read from the README's badges, so
it is written in one place.

    python3 scripts/gen_front_pages.py          # write both pages
    python3 scripts/gen_front_pages.py --check  # fail if either is stale
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

EN_DESCRIPTION = (
    "Put agents on your production signals without handing them the keys — a pipe that signs, routes and "
    "prices every signal, an investigator that runs read-only and proves it at startup, and an audited result "
    "that reaches a person. Self-hosted; the investigator also runs entirely on its own."
)
ZH_DESCRIPTION = (
    "让 agent 值班，但不把钥匙交出去 —— 管道给每条信号签名、路由、计价；调查员只读运行并在启动时证明只读；"
    "结果带审计到人手里。自托管；调查员也可以完全单独使用。"
)

EN_TAIL = """## Read more

*   [Full narrative overview]({repo}/blob/main/OVERVIEW.md)
*   [hookprobe reference]({repo}/blob/main/hookprobe/README.md)
*   [Running all of hookstack]({repo}/blob/main/STACK.md)
*   [Containment](containment.md) — the security boundaries

MIT licensed.
"""
ZH_TAIL = """## 继续读

*   [完整的叙述性总览（OVERVIEW.md）]({repo}/blob/main/OVERVIEW.md)（英文）
*   [hookprobe 参考文档]({repo}/blob/main/hookprobe/README.md)（英文）
*   [把三件套一起跑起来（STACK.md）]({repo}/blob/main/STACK.md)（英文）
*   [安全边界（containment）](../containment.md)（英文）

MIT 协议。
"""

# (source, target, what the README's `docs/` prefix becomes on the site, the site's
# language line, the tail's heading in the README, the front matter description,
# the site's tail)
PAGES = (
    ("README.md", "docs/index.md", "", "**English** · [中文](zh/)", "## Read more", EN_DESCRIPTION, EN_TAIL),
    ("README.zh-CN.md", "docs/zh/index.md", "../", "[English](../) · **中文**", "## 延伸阅读", ZH_DESCRIPTION, ZH_TAIL),
)
_SERVICE_LINK = re.compile(r"\[(`hook(?:relay|judge|probe)`)\]\(hook(?:relay|judge|probe)\)")
_OWNER = re.compile(r"https://github\.com/([^/\s]+)/hookstack/")
# The README's own language switch, in either language's form.
_SWITCH = re.compile(r"^(\*\*English\*\*|\[English\]).*中文")


def owner() -> str:
    """The repository owner, read from the English README's badges: one place."""
    found = _OWNER.search((ROOT / "README.md").read_text(encoding="utf-8"))
    if found is None:
        raise SystemExit("gen_front_pages: README.md's badges do not name the repository owner")
    return found.group(1)


def render(readme: str, prefix: str, language_line: str, tail_heading: str, description: str, tail: str) -> str:
    who = owner()
    repo = f"https://github.com/{who}/hookstack"
    body = readme.split(tail_heading, 1)[0]
    lines = [
        line
        for line in body.splitlines()
        if not (line.startswith("# ") or line.startswith("[![") or _SWITCH.match(line))
    ]
    text = "\n".join(lines).strip("\n") + "\n"
    text = text.replace("](docs/", f"]({prefix}")
    text = _SERVICE_LINK.sub(r"\1", text)
    front = f"---\ntitle: hookstack\ndescription: {description}\n---\n\n{language_line}\n\n"
    return front + text + "\n" + tail.format(repo=repo, owner=who)


def main() -> int:
    check = "--check" in sys.argv
    stale: list[str] = []
    for source, target, prefix, language_line, tail_heading, description, tail in PAGES:
        readme = (ROOT / source).read_text(encoding="utf-8")
        wanted = render(readme, prefix, language_line, tail_heading, description, tail)
        path = ROOT / target
        if check:
            if not path.is_file() or path.read_text(encoding="utf-8") != wanted:
                stale.append(target)
        else:
            path.write_text(wanted, encoding="utf-8")
            print(f"  wrote {target}")
    if stale:
        print("front pages are stale:", file=sys.stderr)
        for target in stale:
            print(f"  {target}", file=sys.stderr)
        print(
            "\nRun `python3 scripts/gen_front_pages.py` and commit the result. The site's front\n"
            "pages are the READMEs re-addressed; edit README.md or README.zh-CN.md, never these.",
            file=sys.stderr,
        )
        return 1
    if check:
        print(f"front pages: {len(PAGES)} site pages are the READMEs, re-addressed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
