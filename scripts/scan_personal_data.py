"""Scan tracked files for secrets and personal data before publishing.

Generic patterns only: this script lives in the repository, so it must not
itself contain any specific identifier it is looking for. It catches the
categories that matter — private network addresses, credentials, tokens,
keys and addresses — without embedding anyone's actual values.

Exit code 1 when matches are found, so it can gate a commit.
"""

from __future__ import annotations

import pathlib
import re
import subprocess
import sys

PATTERNS: dict[str, re.Pattern[str]] = {
    "private IPv4": re.compile(
        r"\b(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
        r"|192\.168\.\d{1,3}\.\d{1,3}"
        r"|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})\b"
    ),
    "aws access key": re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    "jwt / bearer token": re.compile(
        r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"
    ),
    "long hex secret": re.compile(r"\b[0-9a-f]{48,}\b"),
    "mac address": re.compile(r"\b(?:[0-9A-Fa-f]{2}[:\-]){5}[0-9A-Fa-f]{2}\b"),
    "literal password": re.compile(r"""\w*[Pp]assword\s*=\s*["'][^"']{8,}["']"""),
    "presigned url": re.compile(r"X-Amz-Signature=[0-9a-f]{16,}"),
    "email address": re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    "ssh private key": re.compile(r"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----"),
}

# Tooling that legitimately contains detection logic, plus dependency pins.
SKIP = ("scripts/scan_personal_data.py", "requirements")


def tracked_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files"], capture_output=True, text=True, check=True
    )
    return [
        line
        for line in out.stdout.splitlines()
        if not any(s in line for s in SKIP) and pathlib.Path(line).is_file()
    ]


def main() -> int:
    total = 0
    for path in tracked_files():
        try:
            text = pathlib.Path(path).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for label, pattern in PATTERNS.items():
            for lineno, line in enumerate(text.splitlines(), 1):
                if pattern.search(line):
                    print(f"{label:20} {path}:{lineno}")
                    total += 1

    print(f"\ntotal matches: {total}")
    if total:
        print("Remove or replace these before publishing.")
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())
