"""Entry point for the Streamlit app. Run inside the dev container:

    streamlit run app.py

Lists the pages shown in the sidebar. Each page is its own script in pages/.
"""

import streamlit as st

st.navigation(
    [
        st.Page("pages/1_Redlining_analysis.py", title="Redlining peer analysis", default=True),
        st.Page("pages/2_Data_explorer.py", title="Data explorer"),
    ]
).run()
