"""Result figures, from the raw per-run results in integrations/agentdojo/llm_runs and the replay result
tables. Needs matplotlib.

    python scripts/make_figures.py      # -> docs/figures/fig_*.pdf and .png
"""
import json
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

ROOT = Path(__file__).resolve().parents[1]
HERE = ROOT / "docs" / "figures"
RUNS = ROOT / "integrations" / "agentdojo" / "llm_runs"
INK, INK2, MUTED, GRID = "#0b0b0b", "#52514e", "#8a8984", "#e4e2dc"
BLUE, ORANGE, AQUA, YELLOW = "#2a78d6", "#eb6834", "#1baf7a", "#eda100"   # categorical slots 1-4, in order
plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 8.5, "axes.edgecolor": MUTED,
                     "axes.labelcolor": INK2, "xtick.color": INK2, "ytick.color": INK2,
                     "axes.spines.top": False, "axes.spines.right": False, "pdf.fonttype": 42})

OFFSETS = {  # label placement (points), to keep direct labels apart
    "undefended": (6, -3), "spotlighting": (-62, 2), "repeat_user_prompt": (6, 2), "tool_filter": (6, -7),
    "pi_detector": (6, -3), "ip-known-nohuman": (-20, 7), "ip-known-attentive": (6, -10)}
LABELS = {"undefended": "No defense", "spotlighting": "Spotlighting", "repeat_user_prompt": "Repeat prompt",
          "tool_filter": "Tool filter", "pi_detector": "PI detector",
          "ip-known-nohuman": "intentplane, no human", "ip-known-attentive": "intentplane, attentive human"}


def load(model: str, suite: str, config: str):
    d = RUNS / model / ("" if suite == "banking" else suite) / "important_instructions" / config
    rs = [(p.name, json.loads(p.read_text())) for p in d.glob("*.json")]
    rs = [(n, r) for n, r in rs if r["error"] is None]
    clean = [r for n, r in rs if n.endswith("__none.json")]
    att = [r for n, r in rs if not n.endswith("__none.json")]
    if not clean or not att:
        return None
    resisted = sum(1 for r in att if not r["attack_success"] and r["stats"]["attack_attempts"] == 0)
    blocked = sum(1 for r in att if not r["attack_success"] and r["stats"]["attack_blocked"] > 0)
    ok = sum(r["attack_success"] for r in att)
    return {"utility": 100 * sum(r["utility"] for r in clean) / len(clean), "asr": 100 * ok / len(att),
            "n": len(att), "resisted": resisted, "blocked": blocked, "succeeded": ok,
            "other": len(att) - resisted - blocked - ok}


# ------------------------------------------------------------------ fig 1: security / utility trade-off
def tradeoff():
    fig, axes = plt.subplots(1, 2, figsize=(6.8, 2.7), sharey=True)
    for ax, suite in zip(axes, ("banking", "workspace")):
        for cfg, label in LABELS.items():
            r = load("gpt-4o-mini-2024-07-18", suite, cfg)
            if r is None:
                continue
            ours = cfg.startswith("ip-")
            ax.scatter(r["utility"], r["asr"], s=40 if ours else 30, color=ORANGE if ours else BLUE,
                       edgecolor="white", linewidth=0.8, zorder=3, marker="D" if ours else "o")
            dx, dy = OFFSETS[cfg]
            if suite == "workspace":   # three points within 8 utility points of each other near zero
                dx, dy = {"tool_filter": (-52, -12), "ip-known-nohuman": (-40, 12),
                          "ip-known-attentive": (8, -14), "spotlighting": (-62, 2)}.get(cfg, (dx, dy))
            ax.annotate(label.replace("intentplane, ", "intentplane\n") if ours else label,
                        (r["utility"], r["asr"]), xytext=(dx, dy), textcoords="offset points",
                        fontsize=6.8, color=INK2, linespacing=1.0)
        ax.set_title(f"AgentDojo {suite}, gpt-4o-mini", fontsize=8.5, color=INK, loc="left")
        ax.set_xlabel("Utility without attack (%)")
        ax.set_xlim(0, 105)
        ax.grid(color=GRID, linewidth=0.6)
        ax.set_axisbelow(True)
    axes[0].set_ylabel("Attacks that succeed (%)")
    axes[0].set_ylim(-3, 55)
    fig.tight_layout()
    fig.savefig(HERE / "fig_tradeoff.pdf")
    fig.savefig(HERE / "fig_tradeoff.png", dpi=150)


# ------------------------------------------------------------------ fig 2: who stopped the attack
def attribution():
    rows = [("gpt-4o-mini", "gpt-4o-mini-2024-07-18", "undefended"),
            ("gpt-4o-mini", "gpt-4o-mini-2024-07-18", "ip-known-attentive"),
            ("GLM-4.6", "zai_glm-4.6", "undefended"),
            ("GLM-4.6", "zai_glm-4.6", "ip-known-attentive"),
            ("Claude Haiku 5.5", "claude-haiku-5-5", "undefended"),
            ("Claude Haiku 5.5", "claude-haiku-5-5", "ip-known-attentive")]
    parts = [("resisted", "model resisted", BLUE), ("succeeded", "attack succeeded", ORANGE),
             ("blocked", "gateway blocked", AQUA), ("other", "attempted, failed otherwise", YELLOW)]
    data = [(f"{m}\n{'intentplane' if c.startswith('ip-') else 'no defense'}", load(d, "banking", c))
            for m, d, c in rows]
    data = [(lab, r) for lab, r in data if r]
    fig, ax = plt.subplots(figsize=(6.8, 2.6))
    for i, (lab, r) in enumerate(reversed(data)):
        left = 0.0
        for key, _, color in parts:
            w = 100 * r[key] / r["n"]
            if w:
                ax.barh(i, w, left=left, height=0.62, color=color, edgecolor="white", linewidth=0.8)
                if w >= 6:
                    ax.text(left + w / 2, i, f"{r[key]}", ha="center", va="center", fontsize=7, color="white")
            left += w
    ax.set_yticks(range(len(data)), [lab for lab, _ in reversed(data)], fontsize=7.5)
    ax.set_xlim(0, 100)
    ax.set_xlabel("Attacked scenarios, banking (%; counts out of 144 in the bars)")
    ax.legend(handles=[plt.Rectangle((0, 0), 1, 1, color=c) for _, _, c in parts],
              labels=[lab for _, lab, _ in parts], ncol=4, fontsize=7, frameon=False,
              loc="lower center", bbox_to_anchor=(0.45, 1.0))
    ax.spines["left"].set_visible(False)
    ax.tick_params(axis="y", length=0)
    fig.tight_layout()
    fig.savefig(HERE / "fig_attribution.pdf")


# ------------------------------------------------------------------ fig 0: architecture
def architecture():
    fig = plt.figure(figsize=(6.8, 3.0))
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, 680)
    ax.set_ylim(0, 300)
    ax.axis("off")

    def box(x, y, w, h, title, lines=(), ec=MUTED, fc="white", bold=True, size=7.6):
        ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0,rounding_size=6", fc=fc, ec=ec, lw=1))
        ax.text(x + w / 2, y + h - 11, title, ha="center", va="center", fontsize=size,
                fontweight="bold" if bold else "normal", color=INK)
        for k, line in enumerate(lines):
            ax.text(x + w / 2, y + h - 24 - 10 * k, line, ha="center", va="center", fontsize=6.6, color=INK2)

    def arrow(p, q, color=INK2, rad=0.0, style="-|>"):
        ax.add_patch(FancyArrowPatch(p, q, arrowstyle=style, mutation_scale=9, color=color, lw=1,
                                     connectionstyle=f"arc3,rad={rad}", shrinkA=0, shrinkB=0))

    # zones
    ax.add_patch(FancyBboxPatch((6, 8), 150, 284, boxstyle="round,pad=0,rounding_size=8",
                                fc="#fdf1eb", ec=ORANGE, lw=0.8))
    ax.text(14, 282, "UNTRUSTED", fontsize=7, color=ORANGE, fontweight="bold", va="center")
    ax.add_patch(FancyBboxPatch((166, 8), 400, 284, boxstyle="round,pad=0,rounding_size=8",
                                fc="#edf4fd", ec=BLUE, lw=0.8))
    ax.text(174, 282, "TRUSTED: deterministic gateway, no LLM in the decision", fontsize=7, color=BLUE,
            fontweight="bold", va="center")
    ax.add_patch(FancyBboxPatch((576, 8), 98, 284, boxstyle="round,pad=0,rounding_size=8",
                                fc="#ebf6ef", ec=AQUA, lw=0.8))
    ax.text(584, 282, "SYSTEMS", fontsize=7, color="#14653a", fontweight="bold", va="center")

    box(18, 214, 126, 52, "Content", ("emails, invoices, PDFs", "may carry injections"))
    box(18, 134, 126, 52, "LLM agent", ("any model,", "assumed compromised"))
    box(18, 46, 126, 60, "Proposal", ("action + raw values", "no origin, scope or", "context field"))
    arrow((81, 214), (81, 186))
    arrow((81, 134), (81, 106))

    box(178, 214, 120, 52, "Signed mandate", ("principal, tenant, scopes,", "budget, claims"), ec=BLUE)
    box(310, 214, 120, 52, "Registries", ("per owner: payees,", "vendors, contacts"), ec=BLUE)
    box(442, 214, 112, 52, "Context providers", ("sanctions, VoP,", "invoice, IBAN age"), ec=BLUE)

    stages = ["authn", "contract", "resolve", "scope", "budget", "Cedar\nlevels 0-2", "approve", "execute"]
    x0, w, g = 178, 43, 4
    for k, s in enumerate(stages):
        x = x0 + k * (w + g)
        hl = k in (2, 5)
        ax.add_patch(FancyBboxPatch((x, 128), w, 54, boxstyle="round,pad=0,rounding_size=4",
                                    fc="#f2effb" if hl else "white", ec="#6a4fc4" if hl else BLUE, lw=1))
        ax.text(x + w / 2, 155, s, ha="center", va="center", fontsize=6.2, color=INK,
                fontweight="bold" if hl else "normal", linespacing=1.1)
        if k < len(stages) - 1:
            arrow((x + w, 155), (x + w + g, 155), color=BLUE)
    for (sx, tx) in ((238, 199), (370, 293), (498, 434)):   # mandate -> authn, registries -> resolve,
        arrow((sx, 214), (tx, 184), color=BLUE)              # context -> policy
    arrow((144, 76), (178, 150), color=ORANGE, rad=-0.2)
    ax.text(366, 26, "critical values are looked up for the mandate's owner, never trusted from the agent",
            ha="center", fontsize=6.4, color=INK2, style="italic")
    ax.text(366, 116, "any failure or exception = DENY; check, execute and budget update in one critical section",
            ha="center", fontsize=6.4, color="#a12726")

    box(178, 46, 376, 46, "Hash-chained receipts",
        ("raw proposal, resolved values and their origin, decision, approvers",), ec=BLUE)
    box(588, 196, 76, 70, "Approvers", ("user: step-up", "other: four eyes"), ec=AQUA)
    box(588, 100, 76, 70, "Acted-upon", ("bank, ERP,", "mail, drive"), ec=AQUA)
    arrow((588, 224), (520, 184), color=AQUA, rad=0.15)
    arrow((554, 150), (588, 140), color=AQUA)
    arrow((520, 128), (480, 92), color=BLUE)
    fig.savefig(HERE / "fig_arch.pdf")
    fig.savefig(HERE / "fig_arch.png", dpi=150)


# ------------------------------------------------------------------ fig 3: the human in the loop (replay)
def replay_rows(suite: str) -> dict:
    """Rows of results_replay*.md, mandate prompt+known: key -> (utility %, attack success %, prompts/run)."""
    import re
    name = "results_replay.md" if suite == "banking" else f"results_replay_{suite}.md"
    out = {}
    for line in (RUNS.parent / name).read_text(encoding="utf-8").splitlines():
        m = re.match(r"\| (undefended|intentplane, mandate=prompt\+known, ([\w-]+) human) \| (\d+)/(\d+) .*?"
                     r"\| \*\*(\d+)/(\d+) .*?\| .*? \| ([\d.]+) \|", line)
        if m:
            out[m.group(2) or "undefended"] = (100 * int(m.group(3)) / int(m.group(4)), int(m.group(5)),
                                               float(m.group(7)))
    # attack success over the EXECUTABLE pairs: the undefended replay executes every attack that has a
    # ground truth (workspace injection tasks 6-13 have none in AgentDojo v1.2)
    executable = out["undefended"][1] if suite == "workspace" else 144
    return {k: (u, 100 * n / executable, pr) for k, (u, n, pr) in out.items()}


def humans():
    keys = [("undefended", "no gateway"), ("no", "gateway,\nno human"), ("attentive", "gateway,\nattentive"),
            ("rubber-stamp", "gateway,\napproves all")]
    fig, axes = plt.subplots(1, 2, figsize=(6.8, 2.7), sharey=True)
    for ax, suite in zip(axes, ("banking", "workspace")):
        rows = replay_rows(suite)
        for i, (k, _) in enumerate(keys):
            util, asr, _ = rows[k]
            for dx, v, color in ((-0.19, util, BLUE), (0.19, asr, ORANGE)):
                ax.bar(i + dx, max(v, 0.8), width=0.36, color=color, edgecolor="white", linewidth=0.8)
                ax.text(i + dx, v + 2, f"{v:.0f}", ha="center", va="bottom", fontsize=6.8, color=INK2)
        ax.set_xticks(range(len(keys)), [lab for _, lab in keys], fontsize=7)
        ax.set_ylim(0, 112)
        n = "16 tasks, 144 attacks" if suite == "banking" else "40 tasks, 240 attacks"
        ax.set_title(f"{suite}, worst case ({n})", fontsize=8.2, color=INK, loc="left")
        ax.grid(axis="y", color=GRID, linewidth=0.6)
        ax.set_axisbelow(True)
        ax.tick_params(axis="x", length=0)
    axes[0].set_ylabel("% of tasks / of attacks")
    fig.legend(handles=[plt.Rectangle((0, 0), 1, 1, color=c) for c in (BLUE, ORANGE)],
               labels=["legitimate tasks completed", "attacks that succeed"], ncol=2, fontsize=7,
               frameon=False, loc="upper center", bbox_to_anchor=(0.5, 1.0))
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    fig.savefig(HERE / "fig_humans.pdf")
    fig.savefig(HERE / "fig_humans.png", dpi=150)


# ------------------------------------------------------------------ fig 5: a noisy approver (replay)
def noisy():
    import re
    text = (RUNS.parent / "results_sweep.md").read_text(encoding="utf-8")
    fig, ax = plt.subplots(figsize=(3.4, 2.4))
    for suite, color, marker in (("banking", BLUE, "o"), ("workspace", ORANGE, "D")):
        block = text.split(f"## {suite}: noisy human")[1].split("##")[0]
        pts = [(float(m.group(1)), float(m.group(2)))
               for m in re.finditer(r"^\| ([\d.]+) \| .*?\| [\d.]+/\d+ \(([\d.]+)%\) \|$", block, re.MULTILINE)]
        xs, ys = zip(*pts)
        ax.plot(xs, ys, color=color, lw=2, marker=marker, ms=4.5, mec="white", mew=0.8, label=suite)
        ax.annotate(suite, (xs[-1], ys[-1]), xytext=(-8, 6 if suite == "workspace" else -12),
                    textcoords="offset points", ha="right", fontsize=7, color=INK2)
    ax.plot([0, 1], [0, 100], color=MUTED, lw=0.8, ls=(0, (3, 3)))
    ax.text(0.62, 70, "attack rate = p", fontsize=6.5, color=MUTED, rotation=33)
    ax.set_xlabel("p: probability of approving an\noperation the user did not ask for")
    ax.set_ylabel("Attacks that succeed (%)")
    ax.set_xlim(0, 1.02)
    ax.set_ylim(0, 102)
    ax.grid(color=GRID, linewidth=0.6)
    ax.set_axisbelow(True)
    fig.tight_layout()
    fig.savefig(HERE / "fig_noisy.pdf")
    fig.savefig(HERE / "fig_noisy.png", dpi=150)


if __name__ == "__main__":
    HERE.mkdir(parents=True, exist_ok=True)
    noisy()
    humans()
    architecture()
    tradeoff()
    attribution()
    print("wrote", *sorted(p.name for p in HERE.glob("fig_*.pdf")))
