"""Huntly - Phase 2: real accounts and a profile form.

Run with: streamlit run app.py
"""

import streamlit as st

from auth import signup, login
from profiles import create_profile, get_profiles_for_user

CONN_PARAMS = dict(
    host="localhost", port=5432, dbname="huntly_db",
    user="job_user", password="job_password",
)

st.set_page_config(page_title="Huntly", page_icon="🎯")

if "user_id" not in st.session_state:
    st.session_state.user_id = None
    st.session_state.user_email = None


def show_auth_screen():
    st.title("🎯 Huntly")
    st.caption("Automated job matching, personalized to you.")

    tab_login, tab_signup = st.tabs(["Log in", "Sign up"])

    with tab_login:
        with st.form("login_form"):
            email = st.text_input("Email")
            password = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Log in")
            if submitted:
                user_id, message = login(CONN_PARAMS, email, password)
                if user_id:
                    st.session_state.user_id = user_id
                    st.session_state.user_email = email.strip().lower()
                    st.rerun()
                else:
                    st.error(message)

    with tab_signup:
        with st.form("signup_form"):
            email = st.text_input("Email", key="signup_email")
            password = st.text_input("Password", type="password", key="signup_password")
            st.caption("At least 8 characters.")
            submitted = st.form_submit_button("Create account")
            if submitted:
                user_id, message = signup(CONN_PARAMS, email, password)
                if user_id:
                    st.success(message + " Please log in.")
                else:
                    st.error(message)


def show_profile_form():
    st.title("🎯 Huntly")
    st.caption(f"Logged in as {st.session_state.user_email}")

    if st.sidebar.button("Log out"):
        st.session_state.user_id = None
        st.session_state.user_email = None
        st.rerun()

    existing_profiles = get_profiles_for_user(CONN_PARAMS, st.session_state.user_id)

    if existing_profiles:
        st.subheader("Your profile")
        for p in existing_profiles:
            with st.expander(p.profile_name, expanded=True):
                st.write(f"**Job titles:** {', '.join(p.job_titles)}")
                st.write(f"**Locations:** {', '.join(p.locations)}")
                st.write(f"**Skills:** {', '.join(p.skills)}")
                st.write(f"**Years of experience:** {p.years_experience}")
                if p.languages:
                    lang_str = ", ".join(f"{lang} ({level})" for lang, level in p.languages.items())
                    st.write(f"**Languages:** {lang_str}")
        st.divider()

    st.subheader("Create a new search profile" if existing_profiles else "Set up your profile")

    with st.form("profile_form"):
        profile_name = st.text_input("Profile name", placeholder="e.g. Data Analyst search")
        job_titles = st.text_input("Job titles (comma-separated)", placeholder="Data Analyst, BI Analyst")
        locations = st.text_input("Locations (comma-separated)", placeholder="Berlin, Germany")
        skills = st.text_input("Your skills (comma-separated)", placeholder="SQL, Python, Power BI, ETL")
        years_experience = st.number_input("Years of experience", min_value=0, max_value=50, value=0)

        st.write("Languages")
        col1, col2 = st.columns(2)
        with col1:
            german_level = st.selectbox("German", ["None", "A1", "A2", "B1", "B2", "C1", "C2", "Native"])
        with col2:
            english_level = st.selectbox("English", ["None", "A1", "A2", "B1", "B2", "C1", "C2", "Native"], index=7)

        submitted = st.form_submit_button("Save profile")

        if submitted:
            if not profile_name or not job_titles or not skills:
                st.error("Profile name, job titles, and skills are required.")
            else:
                languages = {}
                if german_level != "None":
                    languages["German"] = german_level
                if english_level != "None":
                    languages["English"] = english_level

                create_profile(
                    CONN_PARAMS,
                    st.session_state.user_id,
                    profile_name=profile_name,
                    job_titles=[t.strip() for t in job_titles.split(",") if t.strip()],
                    locations=[l.strip() for l in locations.split(",") if l.strip()],
                    skills=[s.strip() for s in skills.split(",") if s.strip()],
                    years_experience=int(years_experience),
                    languages=languages,
                )
                st.success("Profile saved!")
                st.rerun()


if st.session_state.user_id is None:
    show_auth_screen()
else:
    show_profile_form()
