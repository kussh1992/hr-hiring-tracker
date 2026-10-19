-- HR & Hiring Tracker tables (separate from other apps in the same Supabase project).
-- The app initializes these tables automatically. Do not run this file unless desired.
CREATE TABLE IF NOT EXISTS hr_settings(kind TEXT NOT NULL,value TEXT NOT NULL,active INTEGER DEFAULT 1,ord INTEGER DEFAULT 0,PRIMARY KEY(kind,value));
CREATE TABLE IF NOT EXISTS hr_jobs(id BIGSERIAL PRIMARY KEY,code TEXT UNIQUE,title TEXT,departments TEXT,job_type TEXT,status TEXT,priority TEXT,assignee TEXT,location TEXT,openings INTEGER,description TEXT,created_date TEXT,target_date TEXT,closed_date TEXT,created_by TEXT,updated_at TEXT);
CREATE TABLE IF NOT EXISTS hr_candidates(id BIGSERIAL PRIMARY KEY,code TEXT UNIQUE,name TEXT,email TEXT,phone TEXT,company TEXT,current_title TEXT,experience DOUBLE PRECISION,expected_salary TEXT,current_salary TEXT,notice TEXT,source TEXT,location TEXT,recruiter TEXT,resume_name TEXT,resume_data BYTEA,notes TEXT,created_date TEXT,updated_at TEXT);
CREATE TABLE IF NOT EXISTS hr_applications(id BIGSERIAL PRIMARY KEY,candidate_id BIGINT REFERENCES hr_candidates(id),job_id BIGINT REFERENCES hr_jobs(id),stage TEXT,applied_date TEXT,updated_at TEXT,rating INTEGER DEFAULT 0,interview_date TEXT,rejection_reason TEXT,notes TEXT,UNIQUE(candidate_id,job_id));
CREATE TABLE IF NOT EXISTS hr_activity(id BIGSERIAL PRIMARY KEY,event_at TEXT,changed_by TEXT,entity TEXT,entity_id BIGINT,job_id BIGINT,candidate_id BIGINT,action TEXT,field TEXT,old_value TEXT,new_value TEXT,note TEXT);
CREATE INDEX IF NOT EXISTS idx_hr_activity_date ON hr_activity(event_at);
