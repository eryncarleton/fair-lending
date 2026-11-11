"""Print the hash of a password for the hosted site's sign-in screen (see auth.py).

Used by deploy/set_password.sh, which pipes the password in, so it never appears on a command line:

    printf '%s' "$PASSWORD" | python scripts/hash_password.py

Run it on its own and it prompts instead.
"""

import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import auth  # noqa: E402

password = sys.stdin.read() if not sys.stdin.isatty() else getpass.getpass("Password: ")
if not password:
    sys.exit("No password given.")
print(auth.make_hash(password))
