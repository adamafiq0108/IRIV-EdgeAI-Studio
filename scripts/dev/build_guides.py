"""Render the operator guides under core/static/guides/*.md to HTML, beside them.

    python scripts/dev/build_guides.py

The HTML fragment is what the product shows the operator (in a dialog on the
Models page, styled by the app's own .guide-body rules); the Markdown is the
source we edit. Rendered here, at development time, and committed with the
source, so the box carries no Markdown parser for one file. It used to be a
PDF download (2026-09-14: a popup, the operator asked); the Chrome step is
gone with it.

The Markdown subset is what the guides use: `#` headings, paragraphs, `-`
lists, `- [ ]` checklists, fenced code, pipe tables, inline code and bold.
"""
import html
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
GUIDES = os.path.join(ROOT, "core", "static", "guides")

def inline(text):
    text = html.escape(text, quote=False)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    return text


def render(md):
    out, i, lines = [], 0, md.splitlines()
    while i < len(lines):
        line = lines[i]
        if line.startswith("```"):
            j = i + 1
            block = []
            while j < len(lines) and not lines[j].startswith("```"):
                block.append(lines[j]); j += 1
            out.append("<pre><code>" + html.escape("\n".join(block)) + "</code></pre>")
            i = j + 1
            continue
        if line.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                rows.append(lines[i]); i += 1
            cells = [[c.strip() for c in r.strip().strip("|").split("|")] for r in rows]
            cells = [c for c in cells if not all(re.fullmatch(r":?-+:?", x or "-") for x in c)]
            out.append("<table><tr>" + "".join(f"<th>{inline(c)}</th>" for c in cells[0]) + "</tr>"
                       + "".join("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>"
                                 for r in cells[1:]) + "</table>")
            continue
        if re.match(r"^\d+\. ", line) or line.startswith("- "):
            ordered = bool(re.match(r"^\d+\. ", line))
            items = []
            # An item runs on while the next line is indented (a wrapped item
            # in the source) -- two spaces or more, the way the .md wraps.
            while i < len(lines) and (re.match(r"^\d+\. ", lines[i]) or lines[i].startswith("- ")
                                      or (lines[i].startswith("  ") and lines[i].strip() and items)):
                if lines[i].startswith("  ") and not re.match(r"^(\d+\. |- )", lines[i]):
                    items[-1] += " " + lines[i].strip()
                else:
                    items.append(re.sub(r"^(\d+\. |- )", "", lines[i]))
                i += 1
            tag = "ol" if ordered else "ul"
            checklist = all(it.startswith("[ ] ") for it in items)
            lis = []
            for it in items:
                if it.startswith("[ ] "):
                    lis.append(f'<li class="check">{inline(it[4:])}</li>')
                else:
                    lis.append(f"<li>{inline(it)}</li>")
            cls = ' class="checks"' if checklist else ""
            out.append(f"<{tag}{cls}>" + "".join(lis) + f"</{tag}>")
            continue
        m = re.match(r"^(#{1,3}) (.*)", line)
        if m:
            out.append(f"<h{len(m.group(1))}>{inline(m.group(2))}</h{len(m.group(1))}>")
            i += 1
            continue
        if not line.strip():
            i += 1
            continue
        para = [line]
        i += 1
        while i < len(lines) and lines[i].strip() and not re.match(r"^(#|```|\||- |\d+\. )", lines[i]):
            para.append(lines[i]); i += 1
        out.append(f"<p>{inline(' '.join(para))}</p>")
    return "\n".join(out)


def build(md_path):
    html_path = md_path[:-3] + ".html"
    md = open(md_path, encoding="utf-8").read()
    # A FRAGMENT: no doctype, no styles. The page that shows it owns the look.
    with open(html_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(render(md) + "\n")
    print("wrote", os.path.relpath(html_path, ROOT), os.path.getsize(html_path), "bytes")


if __name__ == "__main__":
    targets = sys.argv[1:] or [os.path.join(GUIDES, n) for n in sorted(os.listdir(GUIDES))
                               if n.endswith(".md")]
    for t in targets:
        build(t)
