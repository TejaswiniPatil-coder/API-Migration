"""
Reusable Streamlit UI components — Phase 10.
"""

import streamlit as st
from typing import Optional


def status_badge(label: str, passed: bool) -> None:
    """Display a coloured pass/fail badge."""
    if passed:
        st.success(f"✅ {label}: PASS")
    else:
        st.error(f"❌ {label}: FAIL")


def code_diff(original: str, migrated: str, diff_text: Optional[str] = None) -> None:
    """Display original and migrated code side-by-side, with optional diff."""
    col1, col2 = st.columns(2)
    with col1:
        st.subheader("Original")
        st.code(original, language="python")
    with col2:
        st.subheader("Migrated")
        st.code(migrated, language="python")

    if diff_text:
        with st.expander("📄 Unified Diff", expanded=True):
            st.code(diff_text, language="diff")


# ── Example source snippets ──────────────────────────────

EXAMPLE_SUCCESS = '''\
from benchmark_http import HTTPClient

client = HTTPClient()
client.old_connect('localhost', '8080')
response = client.send_request('http://api.test', 'GET', 'payload')
client.close_connection()
'''

EXAMPLE_MULTI = '''\
from benchmark_db import Database

db = Database()
db.open_connection('localhost', 5432, 'mydb')
db.execute_query('SELECT * FROM users')
results = db.fetchall()
'''

EXAMPLE_AUTH = '''\
from benchmark_auth import Auth

auth = Auth()
auth.login('admin', 'secret')
token = auth.get_token()
'''

EXAMPLE_CACHE = '''\
from benchmark_cache import Cache

cache = Cache()
cache.set_value('session', 'abc123', 60)
val = cache.get_value('session')
'''

EXAMPLE_FAIL = '''\
from benchmark_http import HTTPClient

client = HTTPClient()
client.old_connect('host', 'port', 'extra_arg')
'''

EXAMPLE_REPAIR = '''\
from benchmark_auth import Auth

auth = Auth()
auth.get_token('unexpected_arg')
'''

EXAMPLES = {
    "HTTP Client (rename + type conv + reorder)": EXAMPLE_SUCCESS,
    "Database (3 migrations)": EXAMPLE_MULTI,
    "Auth (arg merge + method→property)": EXAMPLE_AUTH,
    "Cache (rename + arg rename)": EXAMPLE_CACHE,
    "⚠️ Failed — wrong arg count": EXAMPLE_FAIL,
    "🔧 Repair — property with args": EXAMPLE_REPAIR,
}
