"""Policy mutation testing over every Cedar file: each mutant weakens one rule, and the test suite MUST then
fail. A surviving mutant means a security rule is not actually covered by tests.

  * every `forbid` is neutralised (`when { false }`);
  * every approval guard `context.approval_level >= N` is removed (the permit no longer needs a human);
  * every other condition of a `permit` is removed, one conjunct at a time (the permit becomes broader).

The suite must pass with NO skipped test before mutating: otherwise a broken environment, or a test skipped
for a missing dependency, would make every mutant look "killed".

    python scripts/mutation_check.py
"""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = sorted([*ROOT.glob("intentplane/packs/*.cedar"), *ROOT.glob("integrations/*/*.cedar")])
PYTEST = [sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", "-rs"]

# Equivalent mutants: they broaden a permit only where another permit already allows the same case at the same
# or a lower approval level, so no decision can change and no test can kill them. Each is listed with the reason;
# anything else that survives is a missing test.
EQUIVALENT = {
    "bank.cedar: permit-large-transfer-with-step-up (drop 'context.params.amount_cents > 100000')":
        "amounts <= 1,000.00 are already permitted at level 0 by permit-transfer",
    "accounts_payable.cedar: permit-pay-stable-iban-large-with-step-up (drop 'context.params.amount_cents > 500000')":
        "small amounts to a stable IBAN are already permitted at level 0 by permit-pay-stable-iban",
    "accounts_payable.cedar: permit-pay-fresh-iban-four-eyes (drop 'context.iban_age_days < 7')":
        "stable IBANs are already permitted at level 0 or 1, below four eyes",
    "code.cedar: permit-merge-protected-branch-reviewed "
    "(drop '[\"main\", \"release\"].contains(context.params.target_branch)')":
        "unprotected branches are already permitted at level 0 by permit-merge-unprotected-branch",
    "code.cedar: permit-merge-protected-branch-four-eyes "
    "(drop '[\"main\", \"release\"].contains(context.params.target_branch)')":
        "unprotected branches are already permitted at level 0 by permit-merge-unprotected-branch",
    "health.cedar: permit-full-record-with-step-up (drop 'context.params.section == \"full\"')":
        "treatment reads of other sections are already permitted at level 0 by permit-treatment-read",
}
GUARD = re.compile(r"context\.approval_level >= \d")


def run_suite() -> subprocess.CompletedProcess:
    return subprocess.run(PYTEST, capture_output=True, text=True, check=False, cwd=ROOT)


def _drop_conjunct(cond: str, i: int) -> str:
    parts = [p.strip() for p in cond.split("&&")]
    rest = parts[:i] + parts[i + 1:]
    return " && ".join(rest) if rest else "true"


def mutants(text: str):
    for m in re.finditer(r'@id\("(forbid-[^"]+)"\)\s*forbid[^;]*?(when|unless) \{[^}]*\};', text, re.DOTALL):
        yield f"{m.group(1)} (neutralised)", text[:m.start(2)] + "when { false };" + text[m.end():]
    for m in re.finditer(r'@id\("([^"]+)"\)\s*(permit[^;]*?)(when|unless) \{([^}]*)\};', text, re.DOTALL):
        name, kw, cond = m.group(1), m.group(3), m.group(4).strip()
        head = text[:m.start(3)]
        tail = text[m.end():]
        if kw == "unless":
            yield f"{name} (unless removed)", head + "when { true };" + tail
            continue
        for i, part in enumerate(p.strip() for p in cond.split("&&")):
            label = "no approval" if GUARD.fullmatch(part) else f"drop '{part}'"
            yield f"{name} ({label})", head + f"when {{ {_drop_conjunct(cond, i)} }};" + tail


def main() -> int:
    base = run_suite()
    summary = (base.stdout.strip().splitlines() or ["?"])[-1]
    if base.returncode != 0 or "skipped" in summary:
        print(f"baseline is not clean ({summary}): fix the suite, or install every test dependency "
              "(pip install -e '.[dev,agentdojo]'), before mutating")
        return 2
    total, equivalent, survivors = 0, 0, []
    for path in FILES:
        original = path.read_text(encoding="utf-8")
        try:
            for name, mutated in mutants(original):
                total += 1
                path.write_text(mutated, encoding="utf-8")
                killed = run_suite().returncode != 0
                key = f"{path.name}: {name}"
                if killed:
                    print(f"KILLED      {key}")
                elif key in EQUIVALENT:
                    print(f"EQUIVALENT  {key}  ({EQUIVALENT[key]})")
                    equivalent += 1
                else:
                    print(f"SURVIVED    {key}")
                    survivors.append(key)
        finally:
            path.write_text(original, encoding="utf-8")
    print(f"\n{total - equivalent - len(survivors)}/{total - equivalent} non-equivalent mutants killed "
          f"({equivalent} equivalent, listed with their reason in this script)")
    return 1 if survivors or not total else 0


if __name__ == "__main__":
    sys.exit(main())
