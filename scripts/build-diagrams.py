#!/usr/bin/env python3
"""Build the README infographics in docs/assets from the text in this file.

    python scripts/build-diagrams.py           write the SVG files
    python scripts/build-diagrams.py --check   exit 1 if a file is missing or out of date

Writes how-it-works.svg and how-it-works-dark.svg (the four lanes and the lifecycle, animated
with CSS, a static frame under prefers-reduced-motion) and contract-to-expectation.svg plus its
dark version. Every label names a real command, file or setting; change them here, not in the
SVG files. Standard library only.
"""
from __future__ import annotations

import sys
from pathlib import Path
from xml.sax.saxutils import escape

ASSETS = Path(__file__).resolve().parent.parent / "docs" / "assets"

THEMES = {
    "light": dict(canvas="#ffffff", card="#f6f8fa", line="#d0d7de", text="#1f2328", muted="#59636e",
                  lit="#0969da", block="#cf222e", warn="#9a6700", sign="#0969da", auto="#1a7f37",
                  plain="#6e7781", chip="#eaeef2"),
    "dark": dict(canvas="#0d1117", card="#161b22", line="#3d444d", text="#e6edf3", muted="#9198a1",
                 lit="#4493f8", block="#f85149", warn="#d29922", sign="#4493f8", auto="#3fb950",
                 plain="#8b949e", chip="#21262d"),
}
SANS = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif"
MONO = "ui-monospace, SFMono-Regular, 'SF Mono', Menlo, Consolas, monospace"

# Each step: (kind, main text, detail). A main text in backticks is set in the code font.
# Kinds: block (a failure stops the change), warn (advisory), sign (a person approves),
# auto (applied by the pipeline), plain (a step the producer takes).
LANES = [
    ("Create", "Data Producer", [
        ("plain", "`dpi new-product`", "starts from the template"),
        ("plain", "ODCS contract", "schema, team, quality rules"),
        ("plain", "Access file", "who may read each object"),
        ("plain", "ODPS product file", "output ports and status"),
        ("plain", "Pull request to main", "on data-contracts"),
    ]),
    ("Review and approve", "Contract CI, then Data Stewards", [
        ("block", "`datacontract lint`", "valid ODCS 3.1.0"),
        ("block", "`dpi check-mandatory`", "named Steward and owner"),
        ("block", "`dpi check-products`", "ports, versions, domains"),
        ("warn", "`datacontract test`", "rules on current data"),
        ("block", "`dpi check-expectations`", "every pipeline rule runs"),
        ("sign", "Data Stewards approve", "required reviewer, merge"),
    ]),
    ("Build, rules embedded", "Product bundle deploy", [
        ("auto", "`fetch-rules.sh`", "merged contract into bundle"),
        ("auto", "Deploy to dev", "Databricks Asset Bundle"),
        ("sign", "Approve, deploy to prod", "Azure DevOps Environment"),
        ("auto", "`@rules.apply(...)`", "rules become expectations"),
    ]),
    ("Publish and sync", "Publisher identity, no secret", [
        ("block", "Schema drift check", "object matches contract"),
        ("auto", "Comments and tags", "governed tags, dp.contract_*"),
        ("auto", "Grants", "exactly the access file"),
        ("auto", "Ontos contracts", "versions, status, quality"),
        ("auto", "Ontos data products", "walked through G1 to G4"),
        ("auto", "Certified badge", "back in Unity Catalog"),
    ]),
]
SEVERITY = [("warn", "warning", "keep the row, count it"), ("block", "error", "drop the row"),
            ("block", "critical", "fail the update")]
LIFECYCLE = ["draft G1", "proposed G2", "approved G3", "active G4", "deprecated", "retired"]
LEGEND = [("block", "Blocking"), ("warn", "Advisory"), ("sign", "A person approves"),
          ("auto", "Applied by the pipeline")]

RULES = [
    ("required: true", "on column order_id", "`order_id` IS NOT NULL"),
    ("nullValues, mustBe: 0", "on column customer_id", "`customer_id` IS NOT NULL"),
    ("invalidValues, validValues: [F, O, P]", "on column order_status",
     "`order_status` IS NULL OR `order_status` IN ('F', 'O', 'P')"),
    ("expectation: total_price > 0", "custom property on any rule", "total_price > 0"),
]

W = 1000
LOOP = 12  # seconds for the token to pass all four lanes


def marker(kind: str, x: float, y: float, c: dict) -> str:
    """A shape per kind, so the meaning never rests on colour alone."""
    col = c[kind]
    if kind == "block":   # square
        return f'<rect x="{x - 5}" y="{y - 5}" width="10" height="10" rx="1.5" fill="{col}"/>'
    if kind == "warn":    # triangle
        return f'<path d="M{x} {y - 6} L{x + 6} {y + 5} L{x - 6} {y + 5} Z" fill="{col}"/>'
    if kind == "sign":    # person
        return (f'<circle cx="{x}" cy="{y - 2.5}" r="3" fill="{col}"/>'
                f'<path d="M{x - 5.5} {y + 6} a5.5 5 0 0 1 11 0 Z" fill="{col}"/>')
    if kind == "auto":    # tick
        return (f'<path d="M{x - 5} {y} L{x - 1.5} {y + 4} L{x + 5.5} {y - 4.5}" fill="none" '
                f'stroke="{col}" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"/>')
    return f'<circle cx="{x}" cy="{y}" r="3.5" fill="{col}"/>'  # plain: dot


def text(x: float, y: float, s: str, size: float, fill: str, weight: str = "normal",
         anchor: str = "start", code: bool = False) -> str:
    """A line of text; parts in backticks use the code font, or all of it with code=True."""
    parts = ["", s] if code else s.split("`")
    spans = []
    for i, part in enumerate(parts):
        if not part:
            continue
        font = MONO if i % 2 else SANS
        scale = 0.94 if i % 2 else 1
        spans.append(f'<tspan font-family="{font}" font-size="{size * scale:g}">{escape(part)}</tspan>')
    return (f'<text x="{x:g}" y="{y:g}" fill="{fill}" font-size="{size:g}" font-weight="{weight}" '
            f'text-anchor="{anchor}" font-family="{SANS}" xml:space="preserve">{"".join(spans)}</text>')


def svg(width: int, height: int, title: str, desc: str, body: list[str], style: str = "") -> str:
    return "\n".join([
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" '
        f'height="{height}" role="img" aria-labelledby="t d">',
        f"<title id=\"t\">{escape(title)}</title>",
        f"<desc id=\"d\">{escape(desc)}</desc>",
        f"<style>{style}</style>" if style else "",
        *body,
        "</svg>",
        "",
    ])


def how_it_works(c: dict) -> str:
    margin, gap = 16, 24
    card_w = (W - 2 * margin - 3 * gap) / 4
    track_y, card_y, card_h = 34, 56, 338
    body = [f'<rect width="{W}" height="100%" rx="12" fill="{c["canvas"]}"/>']

    # The track the token travels along, above the cards.
    xs = [margin + i * (card_w + gap) for i in range(4)]
    centres = [x + card_w / 2 for x in xs]
    body.append(f'<line x1="{centres[0]:g}" y1="{track_y}" x2="{centres[3]:g}" y2="{track_y}" '
                f'stroke="{c["line"]}" stroke-width="2" stroke-dasharray="4 6"/>')
    for cx in centres:
        body.append(f'<circle cx="{cx:g}" cy="{track_y}" r="5" fill="{c["canvas"]}" stroke="{c["line"]}" '
                    f'stroke-width="2"/>')
    body.append(f'<circle class="token" cx="{centres[0]:g}" cy="{track_y}" r="8" fill="{c["lit"]}"/>')

    for i, (x, (title, role, steps)) in enumerate(zip(xs, LANES)):
        body.append(f'<rect x="{x:g}" y="{card_y}" width="{card_w:g}" height="{card_h}" rx="10" '
                    f'fill="{c["card"]}" stroke="{c["line"]}" stroke-width="1.5"/>')
        body.append(f'<rect class="lit lit{i}" x="{x:g}" y="{card_y}" width="{card_w:g}" height="{card_h}" '
                    f'rx="10" fill="none" stroke="{c["lit"]}" stroke-width="3"/>')
        body.append(f'<circle cx="{x + 24:g}" cy="{card_y + 28}" r="13" fill="{c["lit"]}"/>')
        body.append(text(x + 24, card_y + 33, str(i + 1), 14, c["canvas"], "bold", "middle"))
        body.append(text(x + 45, card_y + 33, title, 16, c["text"], "600"))
        body.append(text(x + 14, card_y + 60, role, 12.5, c["muted"]))
        y = card_y + 92
        for kind, main, detail in steps:
            body.append(marker(kind, x + 22, y - 5, c))
            body.append(text(x + 36, y, main, 14, c["text"]))
            body.append(text(x + 36, y + 16, detail, 12, c["muted"]))
            y += 39
        if i == 2:
            body.append(text(x + 14, y + 4, "Rule severity sets the action", 12.5, c["muted"], "600"))
            y += 26
            for kind, sev, action in SEVERITY:
                body.append(marker(kind, x + 22, y - 5, c))
                body.append(text(x + 36, y, f"`{sev}`: {action}", 13, c["text"]))
                y += 24
        if i < 3:  # arrow to the next lane
            ax, ay = x + card_w + 4, card_y + card_h / 2
            body.append(f'<path d="M{ax:g} {ay:g} h{gap - 10} m-6 -6 l6 6 l-6 6" fill="none" '
                        f'stroke="{c["muted"]}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>')

    # The lifecycle strip.
    ly = card_y + card_h + 26
    body.append(text(margin, ly + 4, "Data product lifecycle: `status` in the product file, one gate at a time",
                     13.5, c["text"], "600"))
    chip_w, chip_gap, cy = 140, 24, ly + 22
    for j, label in enumerate(LIFECYCLE):
        cx = margin + j * (chip_w + chip_gap)
        gate = "G" in label
        body.append(f'<rect x="{cx}" y="{cy}" width="{chip_w}" height="30" rx="15" fill="{c["chip"]}" '
                    f'stroke="{c["lit"] if gate else c["line"]}" stroke-width="1.5"/>')
        body.append(text(cx + chip_w / 2, cy + 20, label, 13.5, c["text"], "normal", "middle"))
        if j < len(LIFECYCLE) - 1:
            ax = cx + chip_w + 4
            body.append(f'<path d="M{ax} {cy + 15} h{chip_gap - 8} m-5 -5 l5 5 l-5 5" fill="none" '
                        f'stroke="{c["muted"]}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>')

    # The legend.
    gy = cy + 62
    lx = margin
    for kind, label in LEGEND:
        body.append(marker(kind, lx + 7, gy - 4, c))
        body.append(text(lx + 20, gy, label, 13, c["muted"]))
        lx += 34 + 7.2 * len(label)
    body.append(text(W - margin, gy, "Steward approval in the pull request is the sign-off", 13, c["muted"],
                     "normal", "end"))
    height = gy + 18

    step = [cx - centres[0] for cx in centres]
    style = (
        f".lit{{opacity:0;animation:lit {LOOP}s infinite}}"
        + "".join(f".lit{i}{{animation-delay:{i * LOOP / 4:g}s}}" for i in range(4))
        + "@keyframes lit{0%{opacity:0}3%{opacity:1}22%{opacity:1}25%{opacity:0}100%{opacity:0}}"
        + f".token{{animation:token {LOOP}s infinite ease-in-out}}"
        + "@keyframes token{"
        + f"0%{{transform:translateX(0);opacity:1}}20%{{transform:translateX(0)}}"
        + f"25%{{transform:translateX({step[1]:g}px)}}45%{{transform:translateX({step[1]:g}px)}}"
        + f"50%{{transform:translateX({step[2]:g}px)}}70%{{transform:translateX({step[2]:g}px)}}"
        + f"75%{{transform:translateX({step[3]:g}px)}}95%{{transform:translateX({step[3]:g}px);opacity:1}}"
        + f"100%{{transform:translateX({step[3]:g}px);opacity:0}}}}"
        + "@media (prefers-reduced-motion: reduce){.lit,.token{animation:none}.lit{opacity:0}}"
    )
    desc = ("Four lanes, left to right. 1 Create: a Data Producer runs dpi new-product, writes the ODCS "
            "contract, the access file and the ODPS product file, and opens a pull request. 2 Review and "
            "approve: contract CI runs datacontract lint, dpi check-mandatory, dpi check-products and dpi "
            "check-expectations (blocking) and datacontract test (advisory), then the Data Stewards approve. "
            "3 Build: fetch-rules.sh copies the merged contract into the bundle, which deploys to dev, then to "
            "prod after an approval, and rules.apply turns the rules into Lakeflow expectations. 4 Publish: "
            "the publisher identity checks schema drift, sets comments, tags and grants in Unity Catalog, and "
            "syncs contracts and data products to Ontos. Below, the lifecycle from draft G1 to active G4.")
    return svg(W, height, "How governance as code works, from pull request to Unity Catalog and Ontos",
               desc, body, style)


def contract_to_expectation(c: dict) -> str:
    margin, row_h, top = 16, 58, 88
    left_w, arrow_w = 400, 70
    right_x = margin + left_w + arrow_w
    body = [f'<rect width="{W}" height="100%" rx="12" fill="{c["canvas"]}"/>',
            text(margin, 34, "Quality rules in the contract become Lakeflow expectations", 17, c["text"], "600"),
            text(margin, 58, "`@rules.apply(...)` reads the merged contract, so the pipeline code holds no rules "
                 "and a rule changes only through an approved pull request.", 13, c["muted"])]
    body.append(text(margin + 4, top - 8, "In the contract (ODCS)", 12.5, c["muted"], "600"))
    body.append(text(right_x + 4, top - 8, "Expectation in the pipeline", 12.5, c["muted"], "600"))
    y = top
    for rule, where, expectation in RULES:
        body.append(f'<rect x="{margin}" y="{y}" width="{left_w}" height="{row_h - 10}" rx="8" '
                    f'fill="{c["card"]}" stroke="{c["line"]}" stroke-width="1.5"/>')
        body.append(text(margin + 14, y + 21, f"`{rule}`", 14, c["text"]))
        body.append(text(margin + 14, y + 39, where, 12, c["muted"]))
        ay = y + (row_h - 10) / 2
        body.append(f'<path d="M{margin + left_w + 10} {ay:g} h{arrow_w - 20} m-7 -7 l7 7 l-7 7" fill="none" '
                    f'stroke="{c["auto"]}" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/>')
        body.append(f'<rect x="{right_x}" y="{y}" width="{W - margin - right_x}" height="{row_h - 10}" rx="8" '
                    f'fill="{c["card"]}" stroke="{c["auto"]}" stroke-width="1.5"/>')
        body.append(text(right_x + 14, y + 29, expectation, 14, c["text"], code=True))
        y += row_h
    y += 14
    body.append(text(margin + 4, y, "The rule's `severity` sets what the pipeline does with a failing row",
                     13.5, c["text"], "600"))
    y += 18
    sev_w = (W - 2 * margin - 2 * 16) / 3
    for j, (kind, sev, action) in enumerate([("warn", "warning` or `info", "keep the row, count it")]
                                            + [(k, s, a) for k, s, a in SEVERITY[1:]]):
        sx = margin + j * (sev_w + 16)
        body.append(f'<rect x="{sx:g}" y="{y}" width="{sev_w:g}" height="40" rx="8" fill="{c["card"]}" '
                    f'stroke="{c[kind]}" stroke-width="1.5"/>')
        body.append(marker(kind, sx + 20, y + 20, c))
        body.append(text(sx + 36, y + 25, f"`{sev}`: {action}", 13.5, c["text"]))
    y += 62
    body.append(text(margin + 4, y, "Whole-table rules (unique, row counts, SQL counts) keep running in "
                     "`datacontract test`. The Lakeflow event log records the pass and fail counts.", 12.5, c["muted"]))
    desc = ("Four contract rules and the expectations they become: required true becomes order_id IS NOT NULL; a "
            "nullValues rule becomes IS NOT NULL; an invalidValues rule becomes IS NULL OR IN the valid values; "
            "an expectation custom property is applied as written. Severity warning or info keeps and counts "
            "the row, error drops it, critical fails the update.")
    return svg(W, y + 20, "Contract quality rules become Lakeflow expectations", desc, body)


def build() -> dict[Path, str]:
    out = {}
    for theme, c in THEMES.items():
        suffix = "" if theme == "light" else "-dark"
        out[ASSETS / f"how-it-works{suffix}.svg"] = how_it_works(c)
        out[ASSETS / f"contract-to-expectation{suffix}.svg"] = contract_to_expectation(c)
    return out


def main(argv: list[str]) -> int:
    files = build()
    if "--check" in argv:
        stale = [p for p, s in files.items() if not p.is_file() or p.read_text(encoding="utf-8") != s]
        for p in stale:
            print(f"{p.relative_to(ASSETS.parent.parent)}: out of date, run scripts/build-diagrams.py")
        return 1 if stale else 0
    ASSETS.mkdir(parents=True, exist_ok=True)
    for p, s in files.items():
        p.write_text(s, encoding="utf-8")
        print(p.relative_to(ASSETS.parent.parent))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
