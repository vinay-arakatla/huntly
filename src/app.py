"""Huntly - a personalized job matching platform.

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
    Locally, falls back to the same development defaults used
    throughout this project, so `streamlit run app.py` still works
    with no additional setup.
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
    st.title("Huntly")
    st.caption("A personalized job matching platform.")

    tab_login, tab_signup = st.tabs(["Log In", "Sign Up"])

    with tab_login:
        with st.form("login_form"):
            email = st.text_input("Email Address")
            password = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Log In")
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
            name = st.text_input("Full Name", key="signup_name")
            email = st.text_input("Email Address", key="signup_email")
            password = st.text_input("Password", type="password", key="signup_password")
            st.caption("Must be at least 8 characters.")
            submitted = st.form_submit_button("Create Account")
            if submitted:
                user_id, message = signup(CONN_PARAMS, name, email, password)
                if user_id:
                    st.success(message + " You may now log in.")
                else:
                    st.error(message)


def show_profile_form():
    st.title("Huntly")
    st.caption(f"Welcome back, {st.session_state.user_name}.")

    if st.sidebar.button("Log Out"):
        st.session_state.user_id = None
        st.session_state.user_name = None
        st.session_state.user_email = None
        st.rerun()

    existing_profiles = get_profiles_for_user(CONN_PARAMS, st.session_state.user_id)

    if existing_profiles:
        st.subheader("Your Profile")
        for p in existing_profiles:
            with st.expander(p.profile_name, expanded=False):
                st.write(f"**Job Titles:** {', '.join(p.job_titles)}")
                st.write(f"**Locations:** {', '.join(p.locations)}")
                st.write(f"**Skills:** {', '.join(p.skills)}")
                st.write(f"**Years of Experience:** {p.years_experience}")
                if p.languages:
                    lang_str = ", ".join(f"{lang} ({level})" for lang, level in p.languages.items())
                    st.write(f"**Languages:** {lang_str}")
        st.divider()

        st.subheader("Your Job Matches")
        col_scrape, col_score = st.columns(2)
        with col_scrape:
            if st.button("Search for New Jobs"):
                with st.spinner("Searching LinkedIn, Indeed, and Glassdoor for your target roles. This may take a few minutes."):
                    summary = scrape_for_active_profiles(CONN_PARAMS)
                st.success(
                    f"Completed {summary['searches_run']} search(es) across your target roles and locations. "
                    f"Found {summary['jobs_found']} postings, {summary['jobs_new']} of which are new."
                )
                if summary.get("errors"):
                    st.warning(f"{len(summary['errors'])} search(es) could not be completed. See application logs for details.")
                if summary.get("row_errors"):
                    st.warning(f"{len(summary['row_errors'])} posting(s) could not be processed and were skipped.")
        with col_score:
            if st.button("Recalculate Match Scores"):
                with st.spinner("Scoring your profile against all available postings..."):
                    count = score_all_active_profiles(CONN_PARAMS)
                st.success(f"Match scores updated successfully ({count} scored).")

        primary_profile = existing_profiles[0]
        results = get_scores_for_profile(CONN_PARAMS, primary_profile.profile_id)

        if not results:
            st.info("No matches yet. Select 'Search for New Jobs' and then 'Recalculate Match Scores' to get started.")
        else:
            filter_col1, filter_col2 = st.columns(2)
            with filter_col1:
                priority_filter = st.multiselect(
                    "Priority", ["High", "Medium", "Low"], default=["High", "Medium", "Low"]
                )
            with filter_col2:
                available_platforms = sorted(set((r.get("source_platform") or "unknown") for r in results))
                platform_filter = st.multiselect(
                    "Platform",
                    options=available_platforms,
                    default=available_platforms,
                    format_func=lambda p: p.title(),
                )

            filtered = [
                r for r in results
                if r["priority_level"] in priority_filter
                and (r.get("source_platform") or "unknown") in platform_filter
            ]

            st.caption(f"Showing {len(filtered)} of {len(results)} matches")
            for r in filtered:
                with st.container(border=True):
                    col1, col2 = st.columns([4, 1])
                    with col1:
                        st.markdown(f"**{r['title']}** — {r['company']}")
                        location_line = r.get("location") or ""
                        platform_label = (r.get("source_platform") or "unknown").title()
                        st.caption(f"{location_line}  •  Source: {platform_label}" if location_line else f"Source: {platform_label}")
                        if r["matched_skills"]:
                            st.caption(f"Matched skills: {', '.join(r['matched_skills'])}")
                        if r["language_penalty_applied"]:
                            st.caption("Note: this posting's stated language requirement is not met by your profile.")
                    with col2:
                        st.metric("Score", r["match_score"])
                        st.caption(r["priority_level"])
                    st.link_button("View Posting", r["job_url"])

        st.divider()

    st.subheader("Create a New Search Profile" if existing_profiles else "Set Up Your Profile")

    st.write("Languages")
    st.caption(
        "Select the languages you speak. Huntly currently checks job postings "
        "for German and English requirements. If a posting doesn't state a "
        "language requirement at all, it has no effect on your score - so if "
        "you don't select German, for example, you will not lose points on "
        "postings that never asked for it."
    )
    known_languages = st.multiselect("Which languages do you know?", ["German", "English"])

    language_levels = {}
    if known_languages:
        level_cols = st.columns(len(known_languages))
        for col, lang in zip(level_cols, known_languages):
            with col:
                default_index = 6 if lang == "English" else 2  # English defaults to Native, German to B1
                language_levels[lang] = st.selectbox(
                    f"{lang} level", ["A1", "A2", "B1", "B2", "C1", "C2", "Native"],
                    index=default_index, key=f"level_{lang}",
                )

    with st.form("profile_form"):
        profile_name = st.text_input("Profile Name", placeholder="e.g. Data Analyst Search")
        job_titles = st.text_input("Target Job Titles (comma-separated)", placeholder="Data Analyst, BI Analyst")
        locations = st.text_input("Target Locations (comma-separated)", placeholder="Berlin, Germany")
        skills = st.text_input("Your Skills (comma-separated)", placeholder="SQL, Python, Power BI, ETL")
        years_experience = st.number_input("Years of Experience", min_value=0, max_value=50, value=0)

        submitted = st.form_submit_button("Save Profile")

        if submitted:
            if not profile_name or not job_titles or not skills:
                st.error("Profile name, job titles, and skills are required fields.")
            else:
                create_profile(
                    CONN_PARAMS,
                    st.session_state.user_id,
                    profile_name=profile_name,
                    job_titles=[t.strip() for t in job_titles.split(",") if t.strip()],
                    locations=[l.strip() for l in locations.split(",") if l.strip()],
                    skills=[s.strip() for s in skills.split(",") if s.strip()],
                    years_experience=int(years_experience),
                    languages=language_levels,
                )
                st.success("Profile saved successfully.")
                st.rerun()


if st.session_state.user_id is None:
    show_auth_screen()
else:
    show_profile_form()
