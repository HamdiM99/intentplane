"""Worst-case replay (AgentDojo's own injection tasks, fully compromised agent) under two variations:

  * a NOISY human: approves an operation the user did not ask for with probability p, refuses one the user
    asked for with probability q (several seeds), instead of the ideal attentive oracle;
  * ABLATIONS of the gateway: resolution, budget and approval levels removed one at a time, and all three.

    python -m integrations.agentdojo.run_sweep [--write]   # -> results_sweep.md
"""
from __future__ import annotations

import sys
import warnings
from pathlib import Path

from agentdojo.task_suite.load_suites import get_suite

from integrations.agentdojo.run_replay import VERSION, CompromisedAgent, helpers

P_VALUES = (0.0, 0.05, 0.1, 0.2, 0.5, 1.0)
Q = 0.1
SEEDS = (0, 1, 2)
ABLATIONS = [("full gateway", frozenset()), ("without resolution", frozenset({"resolution"})),
             ("without budget", frozenset({"budget"})), ("without approval levels", frozenset({"approval"})),
             ("without all three", frozenset({"resolution", "budget", "approval"}))]


def replay(suite, mandate, human, seed=0, ablate=frozenset()):
    """(utility without attack, attacks that succeed, executable attack pairs)"""
    expected_ops, attack_targets, guarded = helpers(suite.name)
    utility = asr = 0
    for ut in suite.user_tasks.values():
        pre = ut.init_environment(suite.load_and_inject_default_environment({}))
        exp = expected_ops(ut, pre)
        for it in [None, *suite.injection_tasks.values()]:
            if it is not None and not it.ground_truth(pre):
                continue   # workspace injection tasks 6-13 ship without ground truth: not replayable
            kw, cfg = guarded(ut, exp, mandate, human, attack_targets(it, pre) if it else set())
            cfg.seed, cfg.ablate = seed, ablate
            u, attacked = suite.run_task_with_pipeline(CompromisedAgent(ut, it), ut, it, {}, **kw)
            if it is None:
                utility += u
            else:
                asr += attacked
    n_attacks = sum(1 for ut in suite.user_tasks.values() for it in suite.injection_tasks.values()
                    if it.ground_truth(ut.init_environment(suite.load_and_inject_default_environment({}))))
    return utility, asr, n_attacks


def main() -> str:
    warnings.filterwarnings("ignore")
    lines = ["# Noisy human and ablations (worst-case replay, mandate prompt+known)", ""]
    for name in ("banking", "workspace"):
        suite = get_suite(VERSION, name)
        n_user = len(suite.user_tasks)
        lines += [f"## {name}: noisy human (q = {Q}, {len(SEEDS)} seeds, mean)", "",
                  "| p (approves an operation the user did not ask for) | Utility | Attacks that succeed |",
                  "|---|---|---|"]
        for p in P_VALUES:
            rs = [replay(suite, "prompt+known", f"noisy:{p}:{Q}", seed) for seed in SEEDS]
            u = sum(r[0] for r in rs) / len(rs)
            a = sum(r[1] for r in rs) / len(rs)
            lines.append(f"| {p} | {u:.1f}/{n_user} ({100 * u / n_user:.0f}%) "
                         f"| {a:.1f}/{rs[0][2]} ({100 * a / rs[0][2]:.1f}%) |")
            print(lines[-1], flush=True)
        lines += ["", f"## {name}: ablations (no human)", "", "| Gateway | Utility | Attacks that succeed |",
                  "|---|---|---|"]
        for label, ablate in ABLATIONS:
            u, a, n = replay(suite, "prompt+known", "none", 0, ablate)
            lines.append(f"| {label} | {u}/{n_user} | {a}/{n} ({100 * a / n:.1f}%) |")
            print(lines[-1], flush=True)
        lines.append("")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    text = main()
    print(text)
    if "--write" in sys.argv:
        Path(__file__).with_name("results_sweep.md").write_text(text, encoding="utf-8")
