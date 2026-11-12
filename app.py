"""Entry point for the Streamlit app. Run inside the dev container:

    streamlit run app.py

Lists the pages shown in the sidebar. Each page is its own script in app_pages/ (not "pages/", which Streamlit would also
auto-discover and list as extra sidebar entries).
"""

import streamlit as st

import auth

# Runs before any page: on the hosted site, nothing loads until the password is entered.
auth.require_password()

st.navigation(
    [
        st.Page("app_pages/0_Lender_analysis.py", title="Lender analysis", default=True),
        st.Page("app_pages/1_MSA_analysis.py", title="MSA analysis"),
        st.Page("app_pages/2_Data_explorer.py", title="Data explorer"),
    ]
).run()
