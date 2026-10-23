"""Technical brief for LinkedIn (PDF carousel, 4:5 portrait pages): docs/intentplane-brief.pdf. Needs matplotlib.

    python scripts/make_brief.py [--png DIR]     # --png also writes one PNG per page, to check the layout
"""
import sys
import textwrap
from pathlib import Path

import matplotlib.image as mpimg
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "intentplane-brief.pdf"
REPO = "github.com/HamdiM99/intentplane"
W, H = 1080, 1350

SURF, INK, INK2, MUTED, LINE = "#fcfcfb", "#0b0b0b", "#4a4946", "#8a8984", "#d9d7d0"
ORANGE, ORANGE_T, ORANGE_D = "#eb6834", "#fdf1eb", "#b8471c"
BLUE, BLUE_T, BLUE_D = "#2a78d6", "#edf4fd", "#1b5aa6"
GREEN, GREEN_T = "#1f8a4c", "#ebf6ef"
RED, RED_T = "#d93b3a", "#fde4e3"
GRAY = "#b5b3ad"
VIOLET = "#6a4fc4"
SANS, MONO = ["Segoe UI", "DejaVu Sans"], ["Consolas", "DejaVu Sans Mono"]
PAGES = 12


def new_page(n, kicker, title, color=BLUE_D):
    fig = plt.figure(figsize=(W / 100, H / 100), dpi=100, facecolor=SURF)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, W)
    ax.set_ylim(0, H)
    ax.axis("off")
    if kicker:
        t(ax, 70, 1265, kicker.upper(), 15, color, "bold")
    if title:
        para(ax, 70, 1218, title, 30, INK, "bold", lh=44)
    ax.plot([70, W - 70], [62, 62], color=LINE, lw=1)
    t(ax, 70, 38, f"intentplane  ·  {REPO}", 12.5, MUTED)
    t(ax, W - 70, 38, f"{n} / {PAGES}", 12.5, MUTED, ha="right")
    return fig, ax


def t(ax, x, y, s, size=17, color=INK, weight="normal", ha="left", va="center", family=SANS, **kw):
    ax.text(x, y, s, fontsize=size, color=color, fontweight=weight, ha=ha, va=va, family=family, **kw)


def para(ax, x, y, text, size=18, color=INK2, weight="normal", width=None, lh=None, family=SANS, right=W - 70):
    """Wrapped paragraph from the top-left; returns the y below it. Width in characters is derived from the
    font size and the space up to `right` unless given."""
    lh = lh or size * 1.75
    width = width or int((right - x) / (size * (0.60 if family is MONO else 0.66)))
    for line in textwrap.wrap(text, width):
        t(ax, x, y, line, size, color, weight, va="top", family=family)
        y -= lh
    return y


def box(ax, x, y, w, h, fc, ec, lw=1.5, r=18):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle=f"round,pad=0,rounding_size={r}", fc=fc, ec=ec, lw=lw))


def arrow(ax, p, q, color, lw=2.5):
    ax.add_patch(FancyArrowPatch(p, q, arrowstyle="-|>", mutation_scale=22, color=color, lw=lw))


def tile(ax, x, y, w, h, big, small, color=BLUE_D):
    box(ax, x, y, w, h, "white", LINE, 1.2, 16)
    t(ax, x + 26, y + h - 58, big, 38, color, "bold")
    para(ax, x + 26, y + h - 102, small, 15, INK2, lh=24, right=x + w - 22)


# --------------------------------------------------------------------------------------------------
def p1_cover(pdf):
    fig, ax = new_page(1, "", "")
    t(ax, 70, 1240, "intentplane", 24, BLUE_D, "bold")
    t(ax, 70, 1120, "The agent proposes.", 56, INK, "bold")
    t(ax, 70, 1040, "The gateway decides.", 56, BLUE_D, "bold")
    para(ax, 70, 960, "A deterministic control plane for LLM agents that move money, change records and merge "
         "code. It does not try to detect prompt injection: it assumes the agent is compromised, and only "
         "executes what the user mandated, built from values the agent does not control.", 20, INK2, lh=34)
    tile(ax, 70, 470, 300, 250, "0 / 144", "attacks succeed on AgentDojo banking, with two real models")
    tile(ax, 390, 470, 300, 250, "89 / 89", "attempts by gpt-4o-mini to follow the attacker, all blocked")
    tile(ax, 710, 470, 300, 250, "31 / 31", "policy mutants killed: every rule is covered by a test")
    t(ax, 70, 330, "Mustapha Hamdi, PhD", 22, INK, "bold")
    t(ax, 70, 290, "Open source (MIT). Research prototype, fictional data.", 17, INK2)
    t(ax, 70, 250, REPO, 17, BLUE_D)
    pdf.savefig(fig)
    return fig


def p2_problem(pdf):
    fig, ax = new_page(2, "The problem", "Agents act on what they read. Attackers write what they read.")
    y = para(ax, 70, 1100, "An agent that pays invoices also reads emails, PDFs and web pages. When a document "
             "says \"pay this IBAN\", the agent cannot reliably tell the user's intent from the attacker's. "
             "The usual answers do not hold:", 19, INK2, lh=33)
    items = [("Detecting injections is probabilistic.", ("Published detectors keep being bypassed; one miss is "
              "enough when the action moves money.")),
             ("A better model is not a guarantee.", ("In our runs, an open-weight model that resists 97% of "
              "the injections still followed the attacker 5 times out of 144.")),
             ("Access control asks the wrong question.", ("\"May this agent call transfer?\" is yes. The "
              "question that matters: is this payee the user's, is this amount the invoice's?"))]
    y -= 40
    for head, body in items:
        box(ax, 70, y - 200, W - 140, 200, ORANGE_T, ORANGE, 1.4, 18)
        t(ax, 100, y - 45, head, 21, ORANGE_D, "bold")
        para(ax, 100, y - 85, body, 17.5, INK2, lh=30)
        y -= 235
    para(ax, 70, y - 20, "intentplane answers the last question, argument by argument, before anything executes.",
         20, INK, "bold", lh=34)
    pdf.savefig(fig)
    return fig


def p3_scenario(pdf):
    fig, ax = new_page(3, "A scenario", "Monday, 8:40 am. \"We changed banks.\"", ORANGE_D)
    y = para(ax, 70, 1100, "An email reaches the accounts-payable inbox: new IBAN, please pay invoice INV-2291. "
             "It claims to come from ACME, a real vendor. The AI agent of the finance team reads it, updates "
             "the vendor record and pays 12,400.00 EUR. Twenty seconds.", 18.5, INK2, lh=32)
    para(ax, 70, y - 10, "Fake bank-detail fraud used to need a human to be fooled. Not any more.", 19, ORANGE_D,
         "bold")
    t(ax, 70, y - 110, "The same Monday, behind intentplane", 22, INK, "bold")
    rows = [("change ACME's IBAN to the one in the email", "DENIED", "no call-back on the number on file"),
            ("pay INV-2291 to the new IBAN", "DENIED", "not the vendor's IBAN in the ERP"),
            ("pay INV-2291, amount inflated", "DENIED", "amount differs from the ERP invoice"),
            ("pay an invoice that does not exist", "DENIED", "no such invoice in the ERP"),
            ("pay INV-2291 a second time", "DENIED", "invoice already paid"),
            ("pay ACME at its IBAN on file", "EXECUTED", "after the accountant confirms (> 5,000)")]
    yy = y - 175
    for action, status, why in rows:
        ok = status == "EXECUTED"
        box(ax, 70, yy - 92, W - 140, 92, GREEN_T if ok else RED_T, GREEN if ok else RED, 1.2, 14)
        t(ax, 95, yy - 30, action, 17.5, INK, "bold")
        t(ax, 95, yy - 64, why, 15.5, INK2)
        t(ax, W - 95, yy - 46, status, 18, GREEN if ok else RED, "bold", ha="right")
        yy -= 106
    para(ax, 70, yy - 10, "Changing an IBAN is possible, the proper way: call-back on the number already on "
         "file, a second person approves, then 7 days during which any payment to it needs four eyes.",
         16.5, INK2, lh=28)
    pdf.savefig(fig)
    return fig


def p4_idea(pdf):
    fig, ax = new_page(4, "The idea", "The agent can only propose. The gateway resolves the rest.")
    box(ax, 70, 690, 430, 400, "white", ORANGE, 2, 18)
    t(ax, 95, 1055, "What the agent sends", 20, ORANGE_D, "bold")
    for i, line in enumerate(['Proposal("invoice.pay", {', '  "invoice_ref": "INV-2291",', '  "payee_iban": "LT60...",',
                              '  "amount_cents": 1240000,', '  "currency": "EUR" })']):
        t(ax, 95, 1000 - i * 36, line, 16.5, INK, family=MONO)
    para(ax, 95, 790, "No field for origin, scope or context. An unexpected key is refused.", 17, ORANGE_D,
         "bold", lh=28, right=480)
    arrow(ax, (505, 890), (575, 890), BLUE, 3)
    items = [("Signed mandate", "who the user is, their scopes, the bound account and currency, the budget"),
             ("Systems of record", "per user or company: beneficiaries, vendor master, ERP invoices, EHR, CI"),
             ("Context providers", "sanctions, Verification of Payee, consent, CI status, review state")]
    y = 1090
    for head, body in items:
        box(ax, 580, y - 124, 430, 124, BLUE_T, BLUE, 1.4, 14)
        t(ax, 600, y - 32, head, 19, BLUE_D, "bold")
        para(ax, 600, y - 58, body, 15.5, INK2, lh=25, right=990)
        y -= 138
    y = para(ax, 70, 630, "Each critical argument is checked against a source the agent does not control. "
             "The IBAN must be the current IBAN of the invoice's vendor, read from the ERP. The amount must "
             "be the invoice's. A reference like {\"ref\": \"INV-2291\"} is resolved by the gateway, never "
             "trusted.", 18.5, INK2, lh=32)
    para(ax, 70, y - 30, "Then a Cedar policy decides, default deny, and the gateway is the only component "
         "that can act. Whatever the model writes, it cannot widen its own mandate.", 18.5, INK, "bold",
         lh=32)
    pdf.savefig(fig)
    return fig


def p5_architecture(pdf):
    fig, ax = new_page(5, "Architecture", "One gateway, one trust boundary, domain packs.")
    img = mpimg.imread(ROOT / "docs" / "img" / "architecture.png")
    h_img = (W - 80) * img.shape[0] / img.shape[1]
    ax.imshow(img, extent=(40, W - 40, 1095 - h_img, 1095), zorder=2)
    para(ax, 70, 1095 - h_img - 40, "Orange: everything the agent touches, untrusted. Blue: the control "
             "plane, deterministic, no LLM in the decision. Purple: domain packs (bank, accounts payable, "
             "health, code), each a set of contracts, trusted sources, a Cedar policy and an executor. Green: "
             "the humans who approve and the systems acted upon, whose credentials live only behind the "
             "gateway.", 17.5, INK2, lh=30)
    pdf.savefig(fig)
    return fig


def p6_pipeline(pdf):
    fig, ax = new_page(6, "How a proposal is decided", "Nine stages. Any failure is a refusal.")
    stages = [("authn", "the mandate token is genuine, unexpired, not revoked"),
              ("contract", "known action, exact parameter set, types, validators"),
              ("provenance", "every critical value equals a value from a trusted source, for that user or company"),
              ("delegation", "the user holds the action's scope; the agent never exceeds it"),
              ("budget", "cumulative amount and call count per mandate"),
              ("policy", "Cedar, default deny, forbid over permit; the smallest approval level that permits"),
              ("approval", "step-up by the user or four eyes by someone else, signed over the exact operation"),
              ("execute", "only the gateway acts; check, act and update in one critical section"),
              ("receipt", "hash-chained: what the agent sent, where each value came from, who approved")]
    y = 1095
    for i, (name, desc) in enumerate(stages):
        hl = name in ("provenance", "policy")
        box(ax, 70, y - 84, 200, 72, "#f2effb" if hl else BLUE_T, VIOLET if hl else BLUE, 1.5, 14)
        t(ax, 170, y - 48, name, 18, VIOLET if hl else BLUE_D, "bold", ha="center")
        para(ax, 295, y - 26, desc, 16.5, INK2, lh=27)
        if i < len(stages) - 1:
            ax.plot([170, 170], [y - 84, y - 98], color=BLUE, lw=2)
        y -= 98
    box(ax, 70, y - 70, W - 140, 64, RED_T, RED, 1.3, 14)
    t(ax, W / 2, y - 38, "any exception = DENY    ·    a failed execution consumes no budget", 17, "#a12726",
      "bold", ha="center")
    pdf.savefig(fig)
    return fig


def p7_ap(pdf):
    fig, ax = new_page(7, "Accounts-payable pack", "Protect the payment and its vendor record.")
    y = para(ax, 70, 1100, "Bank-detail fraud attacks the vendor master before the payment. So the pack "
             "governs both actions:", 18.5, INK2, lh=32)
    rules = [("vendor.update_bank_details", [("call-back on the phone number already on file, never the one "
                                             "in the email"), "then a second person approves the exact change"]),
             ("invoice.pay", ["the invoice exists in the ERP and is open: paid once, even under concurrency",
                              "payee = current IBAN of the invoice's vendor; amount = the invoice's",
                              "IBAN changed < 7 days ago: four eyes; > 5,000: the accountant confirms",
                              "Verification of Payee, sanctions, hard cap; records scoped to the company"])]
    yy = y - 20
    for name, lines in rules:
        hgt = 70 + 34 * len(lines)
        box(ax, 70, yy - hgt, W - 140, hgt, "#f2effb", VIOLET, 1.5, 16)
        t(ax, 95, yy - 35, name, 19, VIOLET, "bold", family=MONO)
        for i, line in enumerate(lines):
            t(ax, 110, yy - 75 - i * 34, "- " + line, 15.5, INK2)
        yy -= hgt + 25
    t(ax, 70, yy - 5, "python demo_ap.py", 18, INK, "bold", family=MONO)
    demo = [("1. update IBAN to the one in the email", "DENIED"), ("2. pay INV-2291 to the new IBAN", "DENIED"),
            ("3. pay INV-2291 inflated", "DENIED"), ("4. pay an invoice that does not exist", "DENIED"),
            ("5. update IBAN, call-back logged", "PENDING four eyes"), ("6. same, the CFO approves", "EXECUTED"),
            ("7. pay INV-2292 to the new IBAN today", "PENDING four eyes"), ("8. same payment, 8 days later", "EXECUTED"),
            ("9. pay INV-2292 a second time", "DENIED")]
    box(ax, 70, yy - 60 - 36 * len(demo), W - 140, 36 * len(demo) + 30, "#16181d", "#0d0f12", 1, 14)
    for i, (step, st) in enumerate(demo):
        c = "#56d364" if st == "EXECUTED" else ("#e3b341" if st.startswith("PENDING") else "#ff7b72")
        t(ax, 95, yy - 70 - i * 36, step, 15, "#d8dbe0", family=MONO)
        t(ax, W - 95, yy - 70 - i * 36, st, 15, c, "bold", ha="right", family=MONO)
    pdf.savefig(fig)
    return fig


def p8_worst_case(pdf):
    fig, ax = new_page(8, "Results 1: worst case", "Even if the agent obeys the attacker.")
    para(ax, 70, 1100, "AgentDojo banking benchmark (ETH Zurich), 144 injection scenarios. Replayed without an "
         "LLM: the agent executes the attacker's goal verbatim, then the user's task. Model-independent.",
         18, INK2, lh=31)
    rows = [("No protection", 99.3, 100.0), ("intentplane, no human", 0.0, 56.25),
            ("intentplane, attentive human", 0.0, 93.75), ("intentplane, human approves everything", 77.1, 93.75)]
    cax = fig.add_axes([0.07, 0.29, 0.86, 0.45], facecolor=SURF)
    n, step = len(rows), 1.45
    for i, (label, atk, util) in enumerate(rows):
        yc = (n - 1 - i) * step
        cax.barh(yc + 0.2, atk, 0.36, color=ORANGE, edgecolor=SURF, lw=2)
        cax.barh(yc - 0.2, util, 0.36, color=BLUE, edgecolor=SURF, lw=2)
        cax.text(max(atk, 0) + 1.5, yc + 0.2, f"{atk:.0f}%", va="center", fontsize=15, fontweight="bold",
                 family=SANS, color=INK)
        cax.text(util + 1.5, yc - 0.2, f"{util:.0f}%", va="center", fontsize=15, family=SANS, color=INK)
        cax.text(0, yc + 0.45, label, fontsize=16, fontweight="bold", family=SANS, color=INK, va="bottom")
    cax.set_xlim(0, 112)
    cax.set_ylim(-0.6, (n - 1) * step + 0.95)
    cax.axis("off")
    box(ax, 70, 300, 24, 24, ORANGE, ORANGE, 0, 4)
    t(ax, 104, 312, "attacks that succeed", 15.5, INK)
    box(ax, 380, 300, 24, 24, BLUE, BLUE, 0, 4)
    t(ax, 414, 312, "legitimate tasks completed (16 tasks)", 15.5, INK)
    para(ax, 70, 260, "With an attentive human confirming new payees and step-ups, no attack succeeds and 15 "
         "of 16 tasks complete. A human who approves everything lets 77% through: security moves to the person "
         "who signs. Even then, the mandate's budget stops \"send as much as possible\".", 16.5, INK2, lh=28)
    pdf.savefig(fig)
    return fig


def p9_real_models(pdf):
    fig, ax = new_page(9, "Results 2: real models", "Better models are fooled less often. None is safe every time.")
    para(ax, 70, 1100, "Same benchmark, real agents (AgentDojo's important_instructions attack, one run each). "
         "Each bar: the scenarios, out of 144, in which the model tried to carry out the attacker's action.",
         18, INK2, lh=31)
    rows = [("gpt-4o-mini, no protection", 69, 23, 0), ("gpt-4o-mini, intentplane", 0, 0, 89),
            ("GLM-4.6 (open weights), no protection", 5, 0, 0), ("GLM-4.6, intentplane", 0, 0, 3)]
    cax = fig.add_axes([0.07, 0.345, 0.86, 0.38], facecolor=SURF)
    n = len(rows)
    for i, (label, ok, failed, blocked) in enumerate(rows):
        yc = n - 1 - i
        left = 0
        for v, c in ((ok, ORANGE), (failed, GRAY), (blocked, BLUE)):
            if v:
                cax.barh(yc, v, 0.5, left=left, color=c, edgecolor=SURF, lw=2)
                left += v
        total = ok + failed + blocked
        cax.text(left + 2, yc, f"{total} tried, {ok} succeeded", va="center", fontsize=15, family=SANS,
                 color=INK, fontweight="bold" if ok == 0 else "normal")
        cax.text(0, yc + 0.33, label, fontsize=16, fontweight="bold", family=SANS, color=INK, va="bottom")
    cax.set_xlim(0, 144)
    cax.set_ylim(-0.5, n - 0.2)
    cax.axis("off")
    for x, c, lbl in ((70, ORANGE, "succeeded"), (300, GRAY, "failed on its own"), (580, BLUE, "blocked by the gateway")):
        box(ax, x, 425, 24, 24, c, c, 0, 4)
        t(ax, x + 34, 437, lbl, 15.5, INK)
    para(ax, 70, 380, "The gateway does not make the model more obedient: gpt-4o-mini tries about as often with "
         "it (89 scenarios) as without (92). After a refusal it often tries again (44 scenarios with several "
         "attempts, against 20): none gets through. Legitimate tasks without attack: gpt-4o-mini 9 -> 8 of 16, "
         "GLM-4.6 15 -> 13 of 16 (one run each).", 16.5, INK2, lh=28)
    pdf.savefig(fig)
    return fig


def p10_limits(pdf):
    fig, ax = new_page(10, "What it costs, what is not solved", "Honest limits.", ORANGE_D)
    items = [("The human who signs.", ("A human who approves everything lets 77% of worst-case attacks "
              "through. Approval fatigue is the open problem.")),
             ("Utility without a human.", ("Payments to payees the user never named are refused: 9 of 16 "
              "tasks complete in the worst-case replay.")),
             ("Derived values.", ("An amount computed from an untrusted document is bounded (budget, policy, "
              "ERP invoice), not traced.")),
             ("Trusted sources are trusted.", "A poisoned registry or a compromised gateway are out of scope."),
             ("Prototype.", "In-memory state, one process, simulated approvers, fictional data."),
             ("Known attacks only, so far.", "AgentDojo's attacks are not adaptive. Adaptive attacks are next.")]
    y = 1095
    for head, body in items:
        t(ax, 70, y, head, 23, INK, "bold", va="top")
        y = para(ax, 70, y - 42, body, 19, INK2, lh=33) - 34
    pdf.savefig(fig)
    return fig


def p11_tests(pdf):
    fig, ax = new_page(11, "How it is tested", "Every claim is a test that can fail.")
    tile(ax, 70, 810, 455, 280, "43 tests", "regression tests for every attack found in reviews, incl. "
         "concurrency and cross-company cases")
    tile(ax, 555, 810, 455, 280, "6,000", "random proposals over what an attacker controls, checked by an "
         "oracle independent of the gateway code")
    tile(ax, 70, 505, 455, 280, "31 / 31", "policy mutants killed: each forbid, approval guard and permit "
         "condition weakened in turn (6 equivalent, documented)")
    tile(ax, 555, 505, 455, 280, "640 runs", "raw AgentDojo results in the repo: outcome and attribution of "
         "every run, two models, with and without the gateway")
    para(ax, 70, 450, "Continuous integration runs lint, tests, mutation testing, both demos and the AgentDojo "
         "replay on Python 3.10 to 3.12. The design was reviewed and attacked several times; each finding "
         "became a regression test.", 17.5, INK2, lh=30)
    pdf.savefig(fig)
    return fig


def p12_close(pdf):
    fig, ax = new_page(12, "Try it", "The code, the tests and the scenarios are open.")
    box(ax, 70, 760, W - 140, 330, "#16181d", "#0d0f12", 1, 16)
    for i, line in enumerate(["git clone https://" + REPO, "pip install -e \".[dev,agentdojo]\"",
                              "pytest", "python demo_ap.py", "python -m integrations.agentdojo.run_replay"]):
        t(ax, 100, 1040 - i * 56, "$ " + line, 18, "#d8dbe0", family=MONO)
    y = para(ax, 70, 700, "I would like to hear from teams deploying agents in finance, accounts payable or "
             "banking, ERP integrators, and anyone who wants to break it.", 20, INK, "bold", lh=34)
    t(ax, 70, y - 50, "Mustapha Hamdi, PhD", 22, INK, "bold")
    t(ax, 70, y - 92, REPO, 19, BLUE_D)
    t(ax, 70, y - 130, "MIT licence. Research prototype, fictional data, no compliance claim.", 16, INK2)
    pdf.savefig(fig)
    return fig


def main() -> None:
    png_dir = Path(sys.argv[sys.argv.index("--png") + 1]) if "--png" in sys.argv else None
    OUT.parent.mkdir(parents=True, exist_ok=True)
    builders = [p1_cover, p2_problem, p3_scenario, p4_idea, p5_architecture, p6_pipeline, p7_ap, p8_worst_case,
                p9_real_models, p10_limits, p11_tests, p12_close]
    with PdfPages(OUT) as pdf:
        for i, build in enumerate(builders, 1):
            fig = build(pdf)
            if png_dir:
                png_dir.mkdir(parents=True, exist_ok=True)
                fig.savefig(png_dir / f"page{i:02d}.png", facecolor=SURF, dpi=60)
            plt.close(fig)
    print(f"wrote {OUT} ({len(builders)} pages)")


if __name__ == "__main__":
    main()
