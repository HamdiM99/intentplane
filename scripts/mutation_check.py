"""Policy mutation testing over every Cedar file: each mutant weakens one rule, and the test suite MUST then
fail. A surviving mutant means a security rule is not actually covered by tests.

  * every `forbid` is neutralised (`when { false }`);
  * every approval guard `context.approval_level >= N` is removed (the permit no longer needs a human).

    python scripts/mutation_check.py
"""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = sorted([*ROOT.glob("intentplane/packs/*.cedar"), *ROOT.glob("integrations/*/*.cedar")])


def mutants(text: str):
    for m in re.finditer(r'@id\("(forbid-[^"]+)"\)\s*forbid[^;]*?(when|unless) \{[^}]*\};', text, re.DOTALL):
        yield m.group(1), text[:m.start(2)] + "when { false };" + text[m.end():]
    for m in re.finditer(r'@id\("([^"]+)"\)\s*permit[^;]*?;', text, re.DOTALL):
        block = m.group(0)
        for g in re.finditer(r"(&&\s*)?context\.approval_level >= \d", block):
            weakened = block[:g.start()] + ("" if g.group(1) else "true") + block[g.end():]
            yield f"{m.group(1)} (no approval)", text[:m.start()] + weakened + text[m.end():]


def main() -> int:
    total, survivors = 0, []
    for path in FILES:
        original = path.read_text(encoding="utf-8")
        try:
            for name, mutated in mutants(original):
                total += 1
                path.write_text(mutated, encoding="utf-8")
                r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider"],
                                   capture_output=True, text=True, check=False, cwd=ROOT)
                killed = r.returncode != 0
                print(f"{'KILLED  ' if killed else 'SURVIVED'}  {path.name}: {name}")
                if not killed:
                    survivors.append(f"{path.name}: {name}")
        finally:
            path.write_text(original, encoding="utf-8")
    print(f"\n{total - len(survivors)}/{total} mutants killed")
    return 1 if survivors or not total else 0


if __name__ == "__main__":
    sys.exit(main())
