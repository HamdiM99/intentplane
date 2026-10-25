"""When does resolution work? The same worst-case replay with INCOMPLETE systems of record: each known
counterparty (banking beneficiaries, workspace correspondents) is missing with probability f.

Resolution can only refuse more when records are missing, never accept an attacker's value; what it costs is
false refusals (legitimate operations refused or sent to the human). This measures that cost, which is where
provenance tracking (PACT-style), which needs no record, has the advantage.

    python -m integrations.agentdojo.run_registry [--write]   # -> results_registry.md
"""
from __future__ import annotations

import sys
import warnings
from pathlib import Path

from agentdojo.task_suite.load_suites import get_suite

from integrations.agentdojo.run_replay import VERSION, CompromisedAgent, helpers

DROPS = (0.0, 0.1, 0.25, 0.5, 0.75, 1.0)
SEEDS = (0, 1, 2)


def replay(suite, human, drop, seed):
    """(utility without attack, confirmations per legitimate run, attacks that succeed, executable attacks)"""
    expected_ops, attack_targets, guarded = helpers(suite.name)
    utility = asked = asr = n_att = 0
    for ut in suite.user_tasks.values():
        pre = ut.init_environment(suite.load_and_inject_default_environment({}))
        exp = expected_ops(ut, pre)
        for it in [None, *suite.injection_tasks.values()]:
            if it is not None and not it.ground_truth(pre):
                continue
            kw, cfg = guarded(ut, exp, "prompt+known", human, attack_targets(it, pre) if it else set())
            cfg.seed, cfg.registry_drop = seed, drop
            u, attacked = suite.run_task_with_pipeline(CompromisedAgent(ut, it), ut, it, {}, **kw)
            if it is None:
                utility += u
                asked += cfg.stats["asked_human"]
            else:
                asr += attacked
                n_att += 1
    return utility, asked / len(suite.user_tasks), asr, n_att


def main() -> str:
    warnings.filterwarnings("ignore")
    lines = ["# Incomplete systems of record (worst-case replay, mandate prompt+known)", "",
             (f"Each known counterparty is missing with probability f ({len(SEEDS)} seeds, mean). f = 1 is the"
              " strict mandate (only values written in the request)."), ""]
    for name in ("banking", "workspace"):
        suite = get_suite(VERSION, name)
        n = len(suite.user_tasks)
        lines += [f"## {name}", "", ("| f (records missing) | Utility, no human | Utility, attentive human "
                                    "| Confirmations per legitimate run (attentive) | Attacks that succeed (attentive) |"),
                  "|---|---|---|---|---|"]
        for f in DROPS:
            none = [replay(suite, "none", f, s) for s in SEEDS]
            att = [replay(suite, "attentive", f, s) for s in SEEDS]

            def mean(rs, i):
                return sum(r[i] for r in rs) / len(rs)
            lines.append(f"| {f} | {mean(none, 0):.1f}/{n} ({100 * mean(none, 0) / n:.0f}%) "
                         f"| {mean(att, 0):.1f}/{n} ({100 * mean(att, 0) / n:.0f}%) | {mean(att, 1):.2f} "
                         f"| {mean(att, 2):.1f}/{att[0][3]} |")
            print(lines[-1], flush=True)
        lines.append("")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    text = main()
    print(text)
    if "--write" in sys.argv:
        Path(__file__).with_name("results_registry.md").write_text(text, encoding="utf-8")
