"""python -m intentplane.approvals hash-passcode  ->  a passcode hash for [approvals.approvers]."""
import argparse
import getpass

from .board import hash_passcode


def main() -> None:
    ap = argparse.ArgumentParser(prog="python -m intentplane.approvals")
    ap.add_argument("command", choices=["hash-passcode"])
    ap.parse_args()
    passcode = getpass.getpass("Passcode: ")
    if len(passcode) < 8:
        raise SystemExit("use at least 8 characters")
    if getpass.getpass("Again: ") != passcode:
        raise SystemExit("the two passcodes differ")
    print(hash_passcode(passcode))


if __name__ == "__main__":
    main()
