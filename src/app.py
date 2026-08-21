"""Huntly - Phase 2: real accounts and a profile form.

Run with: streamlit run app.py
"""

import streamlit as st

from auth import signup, login
from profiles import create_profile, get_profiles_for_user
from scoring_runner import score_all_active_profiles, get_scores_for_profile
from scraper import scrape_for_active_profiles

def _get_conn_params() -> dict:
    """Database connection settings.

    On Streamlit Community Cloud, these come from the app's Secrets
    panel (st.secrets) - configured there, never committed to git.
    Locally, falls back to the same dev defaults used throughout this
    project, so `streamlit run app.py` still works with no extra setup.
    """
    if "db" in st.secrets:
        return dict(
            host=st.secrets["db"]["host"],
            port=st.secrets["db"].get("port", 5432),
            dbname=st.secrets["db"]["dbname"],
            user=st.secrets["db"]["user"],
            password=st.secrets["db"]["password"],
            sslmode=st.secrets["db"].get("sslmode", "prefer"),
        )
    return dict(
        host="localhost", port=5432, dbname="huntly_db",
        user="job_user", password="job_password",
    )


CONN_PARAMS = _get_conn_params()

st.set_page_config(page_title="Huntly", page_icon="🎯")

if "user_id" not in st.session_state:
    st.session_state.user_id = None
    st.session_state.user_name = None
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
                user_id, name, message = login(CONN_PARAMS, email, password)
                if user_id:
                    st.session_state.user_id = user_id
                    st.session_state.user_name = name
                    st.session_state.user_email = email.strip().lower()
                    st.rerun()
                else:
                    st.error(message)

    with tab_signup:
        with st.form("signup_form"):
            name = st.text_input("Your name", key="signup_name")
            email = st.text_input("Email", key="signup_email")
            password = st.text_input("Password", type="password", key="signup_password")
            st.caption("At least 8 characters.")
            submitted = st.form_submit_button("Create account")
            if submitted:
                user_id, message = signup(CONN_PARAMS, name, email, password)
                if user_id:
                    st.success(message + " Please log in.")
                else:
                    st.error(message)


def show_profile_form():
    st.title("🎯 Huntly")
    st.caption(f"Welcome, {st.session_state.user_name}")

    if st.sidebar.button("Log out"):
        st.session_state.user_id = None
        st.session_state.user_name = None
        st.session_state.user_email = None
        st.rerun()

    existing_profiles = get_profiles_for_user(CONN_PARAMS, st.session_state.user_id)

    if existing_profiles:
        st.subheader("Your profile")
        for p in existing_profiles:
            with st.expander(p.profile_name, expanded=False):
                st.write(f"**Job titles:** {', '.join(p.job_titles)}")
                st.write(f"**Locations:** {', '.join(p.locations)}")
                st.write(f"**Skills:** {', '.join(p.skills)}")
                st.write(f"**Years of experience:** {p.years_experience}")
                st.write(f"**Platforms:** {', '.join(p.platforms)}")
                if p.languages:
                    lang_str = ", ".join(f"{lang} ({level})" for lang, level in p.languages.items())
                    st.write(f"**Languages:** {lang_str}")
        st.divider()

        st.subheader("Your matches")
        col_scrape, col_score = st.columns(2)
        with col_scrape:
            if st.button("🔍 Find new jobs"):
                with st.spinner("Searching LinkedIn and Indeed for your target roles - this can take a minute or two..."):
                    summary = scrape_for_active_profiles(CONN_PARAMS)
                st.success(
                    f"Searched {summary['searches_run']} title/location combinations, "
                    f"found {summary['jobs_found']} postings, {summary['jobs_new']} new."
                )
                if summary.get("errors"):
                    st.warning(f"{len(summary['errors'])} search(es) failed - see logs for details.")
        with col_score:
            if st.button("🔄 Refresh my scores"):
                with st.spinner("Scoring your profile against available jobs..."):
                    count = score_all_active_profiles(CONN_PARAMS)
                st.success(f"Done - {count} scores updated.")

        primary_profile = existing_profiles[0]
        results = get_scores_for_profile(CONN_PARAMS, primary_profile.profile_id)

        if not results:
            st.info("No matches yet - click 'Refresh my matches' above to score your profile against available jobs.")
        else:
            priority_filter = st.multiselect(
                "Filter by priority", ["High", "Medium", "Low"], default=["High", "Medium", "Low"]
            )
            filtered = [r for r in results if r["priority_level"] in priority_filter]

            st.caption(f"Showing {len(filtered)} of {len(results)} matches")
            for r in filtered:
                with st.container(border=True):
                    col1, col2 = st.columns([4, 1])
                    with col1:
                        st.markdown(f"**{r['title']}** at {r['company']}")
                        st.caption(r.get("location") or "")
                        if r["matched_skills"]:
                            st.caption(f"✅ Matched: {', '.join(r['matched_skills'])}")
                        if r["language_penalty_applied"]:
                            st.caption("⚠️ Language requirement not met")
                    with col2:
                        st.metric("Score", r["match_score"])
                        st.caption(r["priority_level"])
                    st.link_button("View posting", r["job_url"])

        st.divider()

    st.subheader("Create a new search profile" if existing_profiles else "Set up your profile")

    with st.form("profile_form"):
        profile_name = st.text_input("Profile name", placeholder="e.g. Data Analyst search")
        job_titles = st.text_input("Job titles (comma-separated)", placeholder="Data Analyst, BI Analyst")
        locations = st.text_input("Locations (comma-separated)", placeholder="Berlin, Germany")
        skills = st.text_input("Your skills (comma-separated)", placeholder="SQL, Python, Power BI, ETL")
        years_experience = st.number_input("Years of experience", min_value=0, max_value=50, value=0)

        platform_choices = st.multiselect(
            "Job search platforms",
            options=["LinkedIn", "Indeed", "Glassdoor"],
            default=["LinkedIn", "Indeed"],
        )

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
            elif not platform_choices:
                st.error("Select at least one job search platform.")
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
                    platforms=[p.lower() for p in platform_choices],
                )
                st.success("Profile saved!")
                st.rerun()


if st.session_state.user_id is None:
    show_auth_screen()
else:
    show_profile_form()
