# HR & Hiring Tracker deployment

## Included
- `app.py`: Streamlit app adapted to Supabase/PostgreSQL and protected by a shared-password gate.
- `requirements.txt`: Python dependencies.
- `schema.sql`: reference schema; the app initializes the tables on first run.

## GitHub
1. Create a new repository named `hr-hiring-tracker` (public repository is okay for source code).
2. Upload `app.py`, `requirements.txt`, and `README.md`. You may also upload `schema.sql`. Do not upload passwords, `.streamlit/secrets.toml`, CVs, or candidate exports.
3. Deploy the repository in Streamlit Community Cloud, selecting `app.py`.

## Streamlit Secrets
In the deployed app's Settings → Secrets, add the following TOML. Replace placeholders locally in the Streamlit Secrets editor; never paste actual passwords into GitHub. Use your existing Supabase Session Pooler connection string with the newly rotated database password. URL-encode reserved characters in the password.

```toml
DATABASE_URL = "postgresql://postgres.PROJECT_REF:URL_ENCODED_PASSWORD@POOLER_HOST:5432/postgres"
APP_PASSWORD = "CHOOSE_A_LONG_RANDOM_SHARED_PASSWORD"
```

## Database and files
The app creates `hr_settings`, `hr_jobs`, `hr_candidates`, `hr_applications`, and `hr_activity` tables. Candidate CVs are stored as BYTEA in `hr_candidates`, not on Streamlit's temporary filesystem. This uses your Supabase database storage.

## Important security note
The shared password is a basic access gate, not individual identity/role management. Limit who receives it, rotate it if shared, and use organization SSO or a proper identity provider before production use. Keep candidate data private and comply with your organization's HR-data policies. A public GitHub repository does not itself make the database public, but anyone with the app URL can attempt to sign in.

## Local testing
```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
streamlit run app.py
```
Create `.streamlit/secrets.toml` locally (do not commit it) with `DATABASE_URL` and `APP_PASSWORD`.
