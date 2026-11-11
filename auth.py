"""Password screen for the hosted app.

The hosted site is open to the internet; this asks for one shared password before any page runs,
so no data reaches a browser without it. Only a PBKDF2 hash of the password is stored, in Google
Secret Manager, and it reaches the app as FAIR_LENDING_PASSWORD_HASH. Set or change it with
deploy/set_password.sh.

Locally there's no password unless FAIR_LENDING_PASSWORD_HASH is set. The hosted image sets
FAIR_LENDING_REQUIRE_PASSWORD=1, so a deployment without a hash shows nothing instead of everything.
"""

import hashlib
import hmac
import os
import time

import streamlit as st

ITERATIONS = 600_000


def make_hash(password: str, iterations: int = ITERATIONS) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${digest.hex()}"


def check(password: str, stored: str) -> bool:
    try:
        algorithm, iterations, salt, digest = stored.split("$")
        if algorithm != "pbkdf2_sha256":
            return False
        attempt = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(iterations))
    except ValueError:
        return False
    return hmac.compare_digest(attempt.hex(), digest)


def require_password() -> None:
    """Show the sign-in screen and stop, unless this browser session has signed in."""
    stored = os.environ.get("FAIR_LENDING_PASSWORD_HASH", "").strip()
    if not stored:
        if os.environ.get("FAIR_LENDING_REQUIRE_PASSWORD") == "1":
            st.error("This site isn't set up yet: no password has been configured.")
            st.stop()
        return  # local use: no password
    if st.session_state.get("signed_in"):
        return

    st.set_page_config(page_title="Sign in · Fair lending", layout="centered")
    st.title("Fair lending analysis")
    with st.form("sign_in"):
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Sign in", type="primary")
    if submitted:
        if check(password, stored):
            st.session_state["signed_in"] = True
            st.rerun()
        time.sleep(2)  # slows down guessing
        st.error("That password isn't right.")
    st.stop()
