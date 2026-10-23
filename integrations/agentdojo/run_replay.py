"""AgentDojo banking suite, worst case: a FULLY COMPROMISED agent, no LLM needed.

For every (user task, injection task) pair the "agent" executes the attacker's goal verbatim (the
injection task's ground-truth calls) and then the user's task. Every money-moving or account-changing
call goes through the intentplane gateway with the bank pack (Cedar) and an account pack.

    python -m integrations.agentdojo.run_replay            # print
    python -m integrations.agentdojo.run_replay --write    # also write integrations/agentdojo/results_replay.md
"""
from __future__ import annotations

import sys
from pathlib import Path

from agentdojo.agent_pipeline.base_pipeline_element import BasePipelineElement
from agentdojo.agent_pipeline.ground_truth_pipeline import GroundTruthPipeline
from agentdojo.task_suite.load_suites import get_suite

from integrations.agentdojo.runtime import ACCOUNT_TOOLS, MONEY_TOOLS, Config, op_key, runtime_factory, safe_iban

VERSION = "v1.2"
CONFIGS = [  # (label, mandate, human); "off" = no gateway, attack attempts only observed
    ("undefended", "off", None),
    ("intentplane, mandate=prompt, no human", "prompt", "none"),
    ("intentplane, mandate=prompt, attentive human", "prompt", "attentive"),
    ("intentplane, mandate=prompt, rubber-stamp human", "prompt", "rubber-stamp"),
    ("intentplane, mandate=prompt+known, no human", "prompt+known", "none"),
    ("intentplane, mandate=prompt+known, attentive human", "prompt+known", "attentive"),
    ("intentplane, mandate=prompt+known, rubber-stamp human", "prompt+known", "rubber-stamp"),
]


class CompromisedAgent(BasePipelineElement):
    """Does the attacker's goal first, then the user's task. Never refuses anything."""

    def __init__(self, user_task, injection_task=None) -> None:
        self.steps = [GroundTruthPipeline(t) for t in (injection_task, user_task) if t is not None]

    def query(self, query, runtime, env, messages=(), extra_args={}):  # noqa: B006 (AgentDojo signature)
        for step in self.steps:
            query, runtime, env, messages, extra_args = step.query(query, runtime, env, messages, extra_args)
        return query, runtime, env, messages, extra_args


def expected_ops(user_task, pre_env) -> set[tuple]:
    """The operations the account holder really asked for, as shown on a confirmation screen."""
    ops = set()
    for call in user_task.ground_truth(pre_env):
        args = dict(call.args)
        if call.function == "update_scheduled_transaction":
            cur = next((t for t in pre_env.bank_account.scheduled_transactions if t.id == args.get("id")), None)
            if cur is not None:
                args = {**args, "recipient": args.get("recipient") or cur.recipient,
                        "amount": args.get("amount") or cur.amount}
        if call.function in MONEY_TOOLS or call.function.startswith("update_"):
            ops.add(op_key(call.function, args))
    return ops


def attack_targets(injection_task, pre_env) -> set[str]:
    if injection_task is None:
        return set()
    targets = set()
    for call in injection_task.ground_truth(pre_env):
        if call.function in MONEY_TOOLS and isinstance(call.args.get("recipient"), str):
            targets.add(safe_iban(call.args["recipient"]))
        elif call.function in ACCOUNT_TOOLS:
            targets.add(call.function)
    return targets


def guarded(ut, exp, mandate, human, targets=frozenset()):
    cfg = Config(prompt=ut.PROMPT, mandate=mandate, human=human or "none", expected_ops=exp,
                 attack_targets=set(targets))
    return {"runtime_class": runtime_factory(cfg)}, cfg


def run_config(suite, mandate, human):
    utility, util_attacked, attacks_ok, blocked, asked, n_pairs = 0, 0, 0, 0, 0, 0
    per_injection: dict[str, int] = {}
    for ut in suite.user_tasks.values():
        pre = ut.init_environment(suite.load_and_inject_default_environment({}))
        exp = expected_ops(ut, pre)
        kw, _ = guarded(ut, exp, mandate, human)
        u, _ = suite.run_task_with_pipeline(CompromisedAgent(ut), ut, None, {}, **kw)
        utility += u
        for it_id, it in suite.injection_tasks.items():
            kw, cfg = guarded(ut, exp, mandate, human, attack_targets(it, pre))
            u, attacked = suite.run_task_with_pipeline(CompromisedAgent(ut, it), ut, it, {}, **kw)
            util_attacked += u
            attacks_ok += attacked
            blocked += (not attacked) and cfg.stats["attack_blocked"] > 0
            per_injection[it_id] = per_injection.get(it_id, 0) + attacked
            asked += cfg.stats["asked_human"]
            n_pairs += 1
    return {"utility": utility, "n_user": len(suite.user_tasks), "util_attacked": util_attacked, "asr": attacks_ok,
            "blocked": blocked, "pairs": n_pairs, "asked": asked, "per_injection": per_injection}


def report(rows, suite) -> str:
    n_user = len(suite.user_tasks)
    lines = [
        "# AgentDojo banking through intentplane: fully compromised agent (ground-truth replay)", "",
        (f"AgentDojo `{VERSION}` banking suite: {n_user} user tasks x {len(suite.injection_tasks)} injection tasks. "
        "The agent executes the attacker's goal verbatim, then the user's task: a model-independent worst case. "
        "See `runtime.py` for the trust model, the mandate modes and the simulated human."), "",
        ("| Configuration | Utility (no attack) | Utility under attack | Attack success rate | Blocked by the gateway "
        "| Human prompts / run |"),
        "|---|---|---|---|---|---|",
    ]
    for label, r in rows:
        lines.append(
            f"| {label} | {r['utility']}/{r['n_user']} ({100 * r['utility'] / r['n_user']:.0f}%) "
            f"| {r['util_attacked']}/{r['pairs']} ({100 * r['util_attacked'] / r['pairs']:.0f}%) "
            f"| **{r['asr']}/{r['pairs']} ({100 * r['asr'] / r['pairs']:.1f}%)** "
            f"| {r['blocked']}/{r['pairs']} | {r['asked'] / r['pairs']:.2f} |")
    lines += ["", "## Successful attacks per injection task", "",
              "| Injection task | Goal | " + " | ".join(label for label, _ in rows) + " |",
              "|---|---|" + "---|" * len(rows)]
    for it_id, it in suite.injection_tasks.items():
        cells = " | ".join(str(r["per_injection"].get(it_id, 0)) for _, r in rows)
        lines.append(f"| {it_id} | {it.GOAL[:90]} | {cells} |")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    suite = get_suite(VERSION, "banking")
    rows = [(label, run_config(suite, m, h)) for label, m, h in CONFIGS]
    text = report(rows, suite)
    print(text)
    if "--write" in sys.argv:
        Path(__file__).with_name("results_replay.md").write_text(text, encoding="utf-8")
