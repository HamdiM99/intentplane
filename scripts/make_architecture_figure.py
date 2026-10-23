"""Architecture figure for the README (docs/img/architecture.png). Needs matplotlib.

    python scripts/make_architecture_figure.py
"""
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

OUT = Path(__file__).resolve().parents[1] / "docs" / "img" / "architecture.png"
SURF, INK, INK2, MUTED, LINE = "#fcfcfb", "#0b0b0b", "#4a4946", "#8a8984", "#d9d7d0"
ORANGE, ORANGE_T, ORANGE_D = "#eb6834", "#fdf1eb", "#b8471c"
BLUE, BLUE_T, BLUE_D = "#2a78d6", "#edf4fd", "#1b5aa6"
GREEN, GREEN_T, GREEN_D = "#1f8a4c", "#ebf6ef", "#14653a"
VIOLET, VIOLET_T = "#6a4fc4", "#f2effb"
SANS, MONO = ["Segoe UI", "DejaVu Sans"], ["Consolas", "DejaVu Sans Mono"]

fig = plt.figure(figsize=(16, 10), dpi=110, facecolor=SURF)
ax = fig.add_axes([0, 0, 1, 1])
ax.set_xlim(0, 1600)
ax.set_ylim(0, 1000)
ax.axis("off")


def box(x, y, w, h, fc, ec, lw=1.4, r=14, z=1):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle=f"round,pad=0,rounding_size={r}", fc=fc, ec=ec, lw=lw,
                                zorder=z))


def t(x, y, s, size=12, color=INK, weight="normal", ha="left", va="center", family=SANS, z=5, **kw):
    ax.text(x, y, s, fontsize=size, color=color, fontweight=weight, ha=ha, va=va, family=family, zorder=z, **kw)


def arrow(p, q, color, lw=2.2, rad=0.0, z=4):
    ax.add_patch(FancyArrowPatch(p, q, arrowstyle="-|>", mutation_scale=18, color=color, lw=lw,
                                 connectionstyle=f"arc3,rad={rad}", zorder=z, shrinkA=0, shrinkB=0))


def card(x, y, w, h, title, lines, accent, fill="white", title_size=13.5, line_size=11, mono=False):
    box(x, y, w, h, fill, accent, lw=1.6, r=12, z=2)
    t(x + 16, y + h - 24, title, size=title_size, weight="bold", z=6)
    for i, line in enumerate(lines):
        t(x + 16, y + h - 52 - i * 21, line, size=line_size, color=INK2, family=MONO if mono else SANS, z=6)


# -------------------------------------------------------------------- title
t(40, 965, "intentplane", size=30, weight="bold")
t(40, 928, "the agent proposes, the gateway decides: an intent control plane for LLM agents that act",
  size=16, color=INK2)

# -------------------------------------------------------------------- zones
box(25, 115, 310, 790, ORANGE_T, ORANGE, lw=1.3, r=22)
t(45, 880, "UNTRUSTED", size=12.5, color=ORANGE_D, weight="bold")
box(355, 115, 900, 790, BLUE_T, BLUE, lw=1.3, r=22)
t(375, 880, "TRUSTED CONTROL PLANE  ·  deterministic, no LLM in the decision", size=12.5, color=BLUE_D,
  weight="bold")
box(1275, 115, 300, 790, GREEN_T, GREEN, lw=1.3, r=22)
t(1295, 880, "HUMANS AND SYSTEMS", size=12.5, color=GREEN_D, weight="bold")

# -------------------------------------------------------------------- untrusted column
card(45, 690, 270, 165, "What the agent reads", ["emails, invoices, PDFs", "web pages, tool outputs",
                                                  "may hide instructions"], ORANGE)
arrow((180, 690), (180, 640), ORANGE)
card(45, 470, 270, 168, "LLM agent", ["any model: GPT, Claude,", "GLM, Mistral, ...",
                                      "assumed compromised"], ORANGE)
arrow((180, 470), (180, 420), ORANGE)
box(45, 160, 270, 258, "white", ORANGE, lw=1.6, r=12, z=2)
t(61, 392, "Proposal", size=13.5, weight="bold", z=6)
t(61, 366, "the only thing it can send", size=11, color=INK2, z=6)
for i, line in enumerate(['{ "action": "invoice.pay",', '  "invoice_ref": "INV-2291",',
                          '  "payee_iban": "LT60...",', '  "amount_cents": 1240000 }']):
    t(61, 330 - i * 22, line, size=10.5, family=MONO, color=INK, z=6)
t(61, 222, "no origin, no scope,", size=11, color=ORANGE_D, weight="bold", z=6)
t(61, 199, "no context: just data", size=11, color=ORANGE_D, weight="bold", z=6)

# -------------------------------------------------------------------- trusted inputs
card(375, 690, 280, 165, "Signed mandate", ["issued from the user's request", "tenant, scopes, budget",
                                            "bound account, currency, payee"], BLUE)
card(670, 690, 280, 165, "Systems of record", ["per tenant: beneficiaries,", "vendor master, ERP invoices,",
                                               "EHR encounters, CI runs"], BLUE)
card(965, 690, 270, 165, "Context providers", ["sanctions, Verification of", "Payee, consent, CI status,",
                                               "review state"], BLUE)
for x in (515, 810, 1100):
    arrow((x, 690), (x, 646), BLUE)

# -------------------------------------------------------------------- gateway
box(375, 395, 860, 250, "white", BLUE, lw=2.4, r=18, z=2)
t(395, 620, "Gateway", size=16, weight="bold", z=6)
t(495, 621, "resolves everything the agent cannot be trusted with, then decides", size=11.5, color=INK2, z=6)
stages = [("authn", "signed\ntoken"), ("contract", "exact\nparams"), ("provenance", "values from\ntrusted sources"),
          ("delegation", "principal's\nscopes"), ("budget", "cumulative\nper mandate"), ("policy", "Cedar,\ndefault deny"),
          ("approval", "hash-bound,\nsingle use"), ("execute", "gateway is\nthe only actor"),
          ("receipt", "hash-chained")]
cw, gap, x0 = 86, 7.5, 392
for i, (name, desc) in enumerate(stages):
    x = x0 + i * (cw + gap)
    hl = name in ("provenance", "policy")
    box(x, 470, cw, 118, VIOLET_T if hl else BLUE_T, VIOLET if hl else BLUE, lw=1.6 if hl else 1.1, r=10, z=3)
    t(x + cw / 2, 562, name, size=9.8, weight="bold", ha="center", color=VIOLET if hl else BLUE_D, z=6)
    t(x + cw / 2, 512, desc, size=9.3, ha="center", color=INK2, linespacing=1.25, z=6)
    if i < len(stages) - 1:
        arrow((x + cw, 529), (x + cw + gap, 529), BLUE, lw=1.3, z=5)
box(392, 410, 826, 42, "#fde4e3", "#d93b3a", lw=1.2, r=10, z=3)
t(805, 431, "any failure or exception  =  DENY     ·     forbid overrides permit     ·     "
  "nothing executes without the gateway", size=10.8, ha="center", color="#a12726", weight="bold", z=6)
arrow((315, 300), (392, 529), ORANGE, lw=2.8, rad=-0.28, z=7)

# -------------------------------------------------------------------- packs
t(375, 368, "Domain packs", size=13.5, weight="bold")
t(545, 368, "contracts, trusted sources, a Cedar policy, an executor: ~50 to 150 lines each", size=11, color=INK2)
packs = [("Bank", ["transfer to the user's own", "beneficiaries or named payee", "step-up > 1,000, hard cap"]),
         ("Accounts payable", ["IBAN = vendor master,", "amount = ERP invoice,", "paid once, call-back,", "7-day cooling-off"]),
         ("Health", ["patient = open encounter", "research needs consent", "break-the-glass: four eyes"]),
         ("Code", ["commit attested by CI", "red CI is never merged", "protected branch: review"])]
pw, pg = 205, 13.3
for i, (name, lines) in enumerate(packs):
    card(375 + i * (pw + pg), 140, pw, 205, name, lines, VIOLET, fill=VIOLET_T, line_size=10.5)

# -------------------------------------------------------------------- right column
card(1295, 690, 260, 165, "Approvers", ["the user (step-up)", "a second person (four eyes)",
                                        "sign the exact operation"], GREEN)
arrow((1295, 735), (1085, 588), GREEN, rad=0.18)
card(1295, 430, 260, 205, "Systems acted upon", ["core banking, ERP,", "EHR, Git forge", "",
                                                 "credentials live only", "behind the gateway"], GREEN)
arrow((1183, 520), (1295, 520), GREEN)
card(1295, 160, 260, 225, "Receipts", ["raw proposal, resolved", "values and their origin,", "decision, approvers",
                                       "", "tamper-evident chain"], GREEN)
arrow((1218, 480), (1295, 330), GREEN, rad=-0.2)

# -------------------------------------------------------------------- results strip
box(25, 22, 1550, 82, "white", LINE, lw=1.2, r=14)
t(48, 75, "AgentDojo banking benchmark (ETH Zurich), 144 prompt-injection scenarios", size=12.5, weight="bold")
t(48, 42, "worst case 143 → 0 attacks succeed   ·   gpt-4o-mini 69 → 0 (it still attempts the attack in 89 scenarios: all blocked)   ·   GLM-4.6 5 → 0", size=12, color=INK2)

OUT.parent.mkdir(parents=True, exist_ok=True)
fig.savefig(OUT, facecolor=SURF)
print(f"wrote {OUT}")
