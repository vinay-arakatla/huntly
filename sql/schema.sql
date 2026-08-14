-- Huntly Phase 1 schema
-- No users table yet (accounts are Phase 2) - candidate_profiles exist
-- standalone for this proof-of-concept stage.

-- Phase 2: real accounts
CREATE TABLE IF NOT EXISTS users (
    user_id SERIAL PRIMARY KEY,
    email VARCHAR(255) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS candidate_profiles (
    profile_id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    profile_name VARCHAR(200) NOT NULL,
    job_titles TEXT[] NOT NULL,
    locations TEXT[] NOT NULL,
    skills TEXT[] NOT NULL,
    years_experience INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_profiles_user_id ON candidate_profiles(user_id);

CREATE TABLE IF NOT EXISTS candidate_languages (
    profile_id INTEGER NOT NULL REFERENCES candidate_profiles(profile_id) ON DELETE CASCADE,
    language VARCHAR(50) NOT NULL,
    proficiency VARCHAR(20) NOT NULL,  -- Native, C2, C1, B2, B1, A2, A1
    PRIMARY KEY (profile_id, language)
);

CREATE TABLE IF NOT EXISTS search_queries (
    query_id SERIAL PRIMARY KEY,
    job_title VARCHAR(200) NOT NULL,
    location VARCHAR(200) NOT NULL,
    last_scraped_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE(job_title, location)
);

CREATE TABLE IF NOT EXISTS raw_job_postings (
    raw_job_id SERIAL PRIMARY KEY,
    query_id INTEGER REFERENCES search_queries(query_id),
    source_platform VARCHAR(50),
    title VARCHAR(500),
    company VARCHAR(300),
    location VARCHAR(300),
    description TEXT,
    job_url VARCHAR(1000) NOT NULL,
    scraped_date TIMESTAMP NOT NULL DEFAULT NOW(),
    job_fetch_date DATE NOT NULL DEFAULT CURRENT_DATE,  -- for day-based filtering; set from the DAG's actual run date, not just insertion time
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_raw_job_url_unique ON raw_job_postings(job_url);
CREATE INDEX IF NOT EXISTS idx_raw_job_fetch_date ON raw_job_postings(job_fetch_date);

CREATE TABLE IF NOT EXISTS cleaned_job_postings (
    job_id SERIAL PRIMARY KEY,
    raw_job_id INTEGER REFERENCES raw_job_postings(raw_job_id),
    title_clean VARCHAR(500) NOT NULL,
    company_clean VARCHAR(300) NOT NULL,
    location_clean VARCHAR(300),
    description_clean TEXT,
    job_url VARCHAR(1000) NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    job_fetch_date DATE NOT NULL DEFAULT CURRENT_DATE,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_cleaned_job_url_unique ON cleaned_job_postings(job_url);
CREATE INDEX IF NOT EXISTS idx_cleaned_job_fetch_date ON cleaned_job_postings(job_fetch_date);

CREATE TABLE IF NOT EXISTS job_skills (
    job_id INTEGER NOT NULL REFERENCES cleaned_job_postings(job_id) ON DELETE CASCADE,
    skill_name VARCHAR(100) NOT NULL,
    PRIMARY KEY (job_id, skill_name)
);

CREATE TABLE IF NOT EXISTS job_language_requirements (
    job_id INTEGER NOT NULL REFERENCES cleaned_job_postings(job_id) ON DELETE CASCADE,
    language VARCHAR(50) NOT NULL,
    required_level VARCHAR(20) NOT NULL,
    PRIMARY KEY (job_id, language)
    -- a job with no rows here has no detected language requirement at all
);

CREATE TABLE IF NOT EXISTS user_job_scores (
    profile_id INTEGER NOT NULL REFERENCES candidate_profiles(profile_id) ON DELETE CASCADE,
    job_id INTEGER NOT NULL REFERENCES cleaned_job_postings(job_id) ON DELETE CASCADE,
    match_score INTEGER NOT NULL CHECK (match_score BETWEEN 0 AND 100),
    priority_level VARCHAR(20) NOT NULL CHECK (priority_level IN ('High', 'Medium', 'Low')),
    matched_skills TEXT[] DEFAULT '{}',
    missing_skills TEXT[] DEFAULT '{}',
    language_penalty_applied BOOLEAN NOT NULL DEFAULT FALSE,
    job_fetch_date DATE NOT NULL DEFAULT CURRENT_DATE,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    PRIMARY KEY (profile_id, job_id)
);

CREATE INDEX IF NOT EXISTS idx_user_job_scores_priority ON user_job_scores(priority_level);
CREATE INDEX IF NOT EXISTS idx_user_job_scores_fetch_date ON user_job_scores(job_fetch_date);
