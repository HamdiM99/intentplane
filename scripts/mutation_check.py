"""Policy mutation testing: neutralise each Cedar `forbid` of the bank pack in turn; the test suite MUST
then fail. A surviving mutant means a security rule is not actually covered by tests.

    python scripts/mutation_check.py
"""
import re
import subprocess
import sys
from pathlib import Path

PACK = Path(__file__).resolve().parents[1] / "intentplane" / "packs" / "bank.py"


def main() -> int:
    original = PACK.read_text(encoding="utf-8")
    ids = re.findall(r'@id\("(forbid-[^"]+)"\)', original)
    survivors = []
    try:
        for pid in ids:
            pattern = re.compile(r'(@id\("' + re.escape(pid) + r'"\)\s*forbid[^;]*?)when \{[^}]*\};', re.DOTALL)
            PACK.write_text(pattern.sub(r"\1when { false };", original, count=1), encoding="utf-8")
            r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider"],
                               capture_output=True, text=True, check=False, cwd=PACK.parents[2])
            killed = r.returncode != 0
            print(f"{'KILLED  ' if killed else 'SURVIVED'}  {pid}")
            if not killed:
                survivors.append(pid)
    finally:
        PACK.write_text(original, encoding="utf-8")
    print(f"\n{len(ids) - len(survivors)}/{len(ids)} mutants killed")
    return 1 if survivors or not ids else 0


if __name__ == "__main__":
    sys.exit(main())
