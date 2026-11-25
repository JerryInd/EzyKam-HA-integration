"""Proves the new regression tests really catch the old defect.

Temporarily reinstalls the pre-fix ``_absorb_cookies`` (jar-wide read) and the
pre-fix approval test (trust ``is_authenticated``), then asserts the new tests
FAIL against them. If they still pass, the tests are vacuous.

Run:  python scripts/prove_regressions_fail.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CLIENT = REPO / "custom_components" / "ezykam" / "core" / "client.py"
AUTH = REPO / "custom_components" / "ezykam" / "core" / "auth.py"

OLD_ABSORB = '''    def _absorb_cookies(self, response: aiohttp.ClientResponse) -> None:
        """OLD (buggy): read the whole shared cookie jar."""
        jar = self._session.cookie_jar.filter_cookies(URL(self.base_url))
        sid = jar.get(COOKIE_SESSION_ID)
        csrf = jar.get(COOKIE_CSRF_TOKEN)
        changed = False
        if sid and sid.value and sid.value != self.auth.sid:
            self.auth.sid = sid.value
            changed = True
        if csrf and csrf.value and csrf.value != self.auth.csrf_token:
            self.auth.csrf_token = csrf.value
            changed = True
'''

NEW_ABSORB_START = '    def _absorb_cookies(self, response: aiohttp.ClientResponse) -> None:'
NEW_ABSORB_END = "    # ------------------------------------------------------------------\n    # transport"


def _between(text: str, start: str, end: str) -> tuple[int, int]:
    i = text.index(start)
    j = text.index(end, i)
    return i, j


def _swap(path: Path, start: str, end: str, replacement: str) -> str:
    original = path.read_text(encoding="utf-8")
    i, j = _between(original, start, end)
    path.write_text(original[:i] + replacement + original[j:], encoding="utf-8")
    return original


def _run(tests: list[str]) -> bool:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", *tests],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    return proc.returncode == 0


def main() -> int:
    # --- 1. old jar-wide absorb must make the cookie test FAIL -------------
    old_client = _swap(CLIENT, NEW_ABSORB_START, NEW_ABSORB_END, OLD_ABSORB)
    try:
        if _run(["tests/test_client.py::test_stale_cookie_in_shared_jar_is_not_adopted"]):
            print("FAIL: cookie test still passes against OLD absorb -> vacuous")
            return 1
        print("OK: cookie test fails against OLD jar-wide absorb (as required)")
    finally:
        CLIENT.write_text(old_client, encoding="utf-8")

    # --- 2. old approval test must make the auth test FAIL -----------------
    old_auth = _swap(
        AUTH,
        "            auth = self._client.auth\n            if auth.is_complete",
        "                if not auth.csrf_token:",
        "            if self._saw_body_sid or self._client.is_authenticated:\n"
        "                auth = self._client.auth\n"
        "                if not auth.csrf_token:",
    )
    try:
        if _run(["tests/test_auth.py::test_login_ignores_session_held_before_start"]):
            print("FAIL: auth test still passes against OLD approval -> vacuous")
            return 1
        print("OK: auth test fails against OLD is_authenticated approval (as required)")
    finally:
        AUTH.write_text(old_auth, encoding="utf-8")

    # --- 3. everything must be restored and green --------------------------
    if not _run(["-q"]):
        print("FAIL: suite red after restore")
        return 1
    print("OK: suite green with fixes restored")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
