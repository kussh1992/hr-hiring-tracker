import streamlit as st
import pandas as pd, io, re, os
import psycopg2
from psycopg2.extras import DictCursor
from pathlib import Path
from datetime import date, datetime, timedelta

ROOT=Path(__file__).resolve().parent
DB_LABEL="Supabase PostgreSQL (hr_ tables)"
st.set_page_config(page_title="HR & Hiring Tracker",page_icon="👥",layout="wide")

# Basic shared-password gate. Configure APP_PASSWORD in Streamlit Secrets.
# For multiple users, replace with organization SSO before production use.
if "hr_authenticated" not in st.session_state: st.session_state.hr_authenticated=False
try: expected_password=st.secrets.get("APP_PASSWORD", "")
except Exception: expected_password=""
if not expected_password:
    st.error("App is not configured yet. Add APP_PASSWORD and DATABASE_URL in Streamlit Secrets before using this tracker.")
    st.stop()
if not st.session_state.hr_authenticated:
    st.title("HR & Hiring Tracker — Sign in")
    entered=st.text_input("Shared access password",type="password")
    if st.button("Sign in",type="primary"):
        if entered and entered == expected_password:
            st.session_state.hr_authenticated=True; st.rerun()
        else: st.error("Incorrect password.")
    st.stop()
if st.sidebar.button("Sign out"):
    st.session_state.hr_authenticated=False; st.rerun()

DEFAULTS={
"Departments":["Operations","Business Analytics","Technology","Finance","Human Resources","RCM"],
"Job Types":["Full-time","Part-time","Contract","Internship","Temporary"],
"Job Statuses":["Open","On Hold","Closed","Cancelled"],
"Candidate Stages":["Sourced","Applied","Screening","Shortlisted","Interview Scheduled","Interviewed","Offer","Hired","Rejected","Withdrawn","On Hold"],
"Assignees":["Unassigned"],"Candidate Sources":["LinkedIn","Employee Referral","Job Portal","Consultant","Career Website","Other"],
"Priorities":["Low","Medium","High","Critical"],"Locations":["Not Specified"],
"Rejection Reasons":["Skills mismatch","Experience mismatch","Salary mismatch","Candidate withdrew","Position closed","Other"]}

class CursorCompat:
    def __init__(self, cur): self.cur=cur; self.lastrowid=None
    def execute(self, sql, args=()):
        sql=sql.strip()
        ignored=bool(re.match(r"INSERT\s+OR\s+IGNORE", sql, flags=re.I))
        sql=re.sub(r"INSERT\s+OR\s+IGNORE\s+INTO", "INSERT INTO", sql, flags=re.I)
        if ignored:
            sql=re.sub(r";?\s*$", " ON CONFLICT DO NOTHING", sql)
        # PostgreSQL's date casting replaces SQLite date(value) in reporting queries.
        sql=re.sub(r"date\(([^()]+)\)", r"(\1)::date", sql, flags=re.I)
        wants_id=bool(re.match(r"INSERT\s+INTO\s+(?:hr_)?(jobs|candidates|applications)\b", sql, flags=re.I)) and "RETURNING" not in sql.upper()
        if wants_id: sql += " RETURNING id"
        sql=sql.replace("?", "%s")
        self.cur.execute(sql, args or ())
        self.lastrowid=None
        if wants_id:
            row=self.cur.fetchone(); self.lastrowid=row[0] if row else None
        return self
    def fetchone(self): return self.cur.fetchone()
    def fetchall(self): return self.cur.fetchall()
    def __iter__(self): return iter(self.cur)
    def executescript(self, script):
        # Schema script contains simple CREATE statements separated by semicolons.
        for statement in script.split(';'):
            statement=statement.strip()
            if not statement: continue
            statement=re.sub(r"INTEGER\s+PRIMARY\s+KEY", "BIGSERIAL PRIMARY KEY", statement, flags=re.I)
            statement=statement.replace("resume_path TEXT,resume_name TEXT", "resume_name TEXT,resume_data BYTEA")
            statement=re.sub(r"REFERENCES\s+jobs\(", "REFERENCES hr_jobs(", statement, flags=re.I)
            statement=re.sub(r"REFERENCES\s+candidates\(", "REFERENCES hr_candidates(", statement, flags=re.I)
            self.cur.execute(statement)
        return self
    def __enter__(self): return self
    def __exit__(self, typ, val, tb): self.cur.close()

class ConnectionCompat:
    def __init__(self):
        url=st.secrets.get("DATABASE_URL", os.getenv("DATABASE_URL", ""))
        if not url: raise RuntimeError("DATABASE_URL is missing. Add it in Streamlit app Settings → Secrets.")
        self.conn=psycopg2.connect(url, cursor_factory=DictCursor, connect_timeout=15)
        self.conn.autocommit=False
    def execute(self, sql, args=()):
        c=CursorCompat(self.conn.cursor()); return c.execute(sql,args)
    def cursor(self): return CursorCompat(self.conn.cursor())
    def executescript(self, script): return self.cursor().executescript(script)
    def __enter__(self): return self
    def __exit__(self, typ, val, tb):
        if typ is None: self.conn.commit()
        else: self.conn.rollback()
        self.conn.close()

def con(): return ConnectionCompat()
def stamp(): return datetime.now().isoformat(timespec="seconds")
def init():
    with con() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS hr_settings(kind TEXT,value TEXT,active INTEGER DEFAULT 1,ord INTEGER DEFAULT 0,PRIMARY KEY(kind,value));
        CREATE TABLE IF NOT EXISTS hr_jobs(id INTEGER PRIMARY KEY,code TEXT UNIQUE,title TEXT,departments TEXT,job_type TEXT,status TEXT,priority TEXT,assignee TEXT,location TEXT,openings INTEGER,description TEXT,created_date TEXT,target_date TEXT,closed_date TEXT,created_by TEXT,updated_at TEXT);
        CREATE TABLE IF NOT EXISTS hr_candidates(id INTEGER PRIMARY KEY,code TEXT UNIQUE,name TEXT,email TEXT,phone TEXT,company TEXT,current_title TEXT,experience REAL,expected_salary TEXT,current_salary TEXT,notice TEXT,source TEXT,location TEXT,recruiter TEXT,resume_path TEXT,resume_name TEXT,notes TEXT,created_date TEXT,updated_at TEXT);
        CREATE TABLE IF NOT EXISTS hr_applications(id INTEGER PRIMARY KEY,candidate_id INTEGER,job_id INTEGER,stage TEXT,applied_date TEXT,updated_at TEXT,rating INTEGER DEFAULT 0,interview_date TEXT,rejection_reason TEXT,notes TEXT,UNIQUE(candidate_id,job_id),FOREIGN KEY(candidate_id) REFERENCES candidates(id),FOREIGN KEY(job_id) REFERENCES jobs(id));
        CREATE TABLE IF NOT EXISTS hr_activity(id INTEGER PRIMARY KEY,event_at TEXT,changed_by TEXT,entity TEXT,entity_id INTEGER,job_id INTEGER,candidate_id INTEGER,action TEXT,field TEXT,old_value TEXT,new_value TEXT,note TEXT);
        CREATE INDEX IF NOT EXISTS idx_hr_activity_date ON hr_activity(event_at);
        """)
        for kind,vals in DEFAULTS.items():
            for i,v in enumerate(vals):
                c.execute("INSERT OR IGNORE INTO hr_settings(kind,value,active,ord) VALUES(?,?,1,?)",(kind,v,i))
def opts(kind, all_values=False):
    with con() as c:
        q="SELECT value FROM hr_settings WHERE kind=?"+("" if all_values else " AND active=1")+" ORDER BY ord,value"
        return [r[0] for r in c.execute(q,(kind,)).fetchall()]
def get_kpi_days():
    with con() as c:
        row=c.execute("SELECT value FROM hr_settings WHERE kind='KPI Settings' AND active=1 ORDER BY ord LIMIT 1").fetchone()
        if row:
            try: return max(1,int(row[0]))
            except (TypeError,ValueError): pass
        c.execute("INSERT OR IGNORE INTO hr_settings(kind,value,active,ord) VALUES('KPI Settings','30',1,0)")
        return 30
def set_kpi_days(days, by):
    old=get_kpi_days()
    with con() as c:
        c.execute("UPDATE hr_settings SET active=0 WHERE kind='KPI Settings'")
        c.execute("INSERT OR IGNORE INTO hr_settings(kind,value,active,ord) VALUES('KPI Settings',?,1,0)",(str(int(days)),))
        c.execute("UPDATE hr_settings SET active=1 WHERE kind='KPI Settings' AND value=?",(str(int(days)),))
        event(c,"settings",0,"KPI_TARGET_UPDATED",by,field="KPI days",old=old,new=int(days),note="Job closure KPI target updated")
def event(c,entity,eid,action,by,job=None,candidate=None,field="",old="",new="",note=""):
    c.execute("INSERT INTO hr_activity(event_at,changed_by,entity,entity_id,job_id,candidate_id,action,field,old_value,new_value,note) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
    (stamp(),by,entity,eid,job,candidate,action,field,"" if old is None else str(old),"" if new is None else str(new),note))
def df(sql,args=()):
    with con() as c:
        cur=c.execute(sql,args); rows=cur.fetchall(); cols=[d.name for d in cur.cur.description] if cur.cur.description else []
        return pd.DataFrame([dict(r) for r in rows],columns=cols)
def safe_filename(s): return re.sub(r"[^A-Za-z0-9_-]","_",s)
def new_code(prefix): return f"{prefix}-{datetime.now():%y%m%d%H%M%S}-{datetime.now().microsecond//1000:03d}"
def attach_cv(upload, code):
    if not upload: return None, ""
    ext=Path(upload.name).suffix.lower()
    if ext not in (".pdf",".doc",".docx",".rtf"): raise ValueError("Upload a PDF, DOC, DOCX, or RTF CV.")
    return upload.getvalue(), Path(upload.name).name
def create_job(title,deps,jtype,status,priority,assignee,location,openings,desc,created,target,by):
    code=new_code("JOB")
    with con() as c:
        cur=c.execute("INSERT INTO hr_jobs(code,title,departments,job_type,status,priority,assignee,location,openings,description,created_date,target_date,created_by,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (code,title,", ".join(deps),jtype,status,priority,assignee,location,openings,desc,str(created),str(target),by,stamp()))
        jid=cur.lastrowid; event(c,"job",jid,"JOB_CREATED",by,job=jid,new=title,note="Job role created")
    return jid
def create_candidate(data,upload,by):
    code=new_code("CAN"); path,name=attach_cv(upload,code)
    with con() as c:
        cur=c.execute("INSERT INTO hr_candidates(code,name,email,phone,company,current_title,experience,expected_salary,current_salary,notice,source,location,recruiter,resume_name,resume_data,notes,created_date,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (code,data["name"],data["email"],data["phone"],data["company"],data["title"],data["experience"],data["expected"],data["current"],data["notice"],data["source"],data["location"],data["recruiter"],name,path,data["notes"],str(data["created"]),stamp()))
        cid=cur.lastrowid; event(c,"candidate",cid,"CANDIDATE_CREATED",by,candidate=cid,new=data["name"])
        if path: event(c,"candidate",cid,"CV_ATTACHED",by,candidate=cid,new=name)
    return cid
def add_option(kind,value):
    value=value.strip()
    if not value:return False,"Enter an option."
    with con() as c:
        if c.execute("SELECT 1 FROM hr_settings WHERE kind=? AND lower(value)=lower(?)",(kind,value)).fetchone(): return False,"Option already exists."
        n=c.execute("SELECT COALESCE(MAX(ord),0)+1 FROM hr_settings WHERE kind=?",(kind,)).fetchone()[0]
        c.execute("INSERT INTO hr_settings(kind,value,active,ord) VALUES(?,?,1,?)",(kind,value,n))
    return True,"Option added."
def display(d):
    if d.empty: st.info("No records found.")
    else: st.dataframe(d,use_container_width=True,hide_index=True)
init()
st.sidebar.title("👥 HR & Hiring Hub")
by=st.sidebar.text_input("Your name (audit attribution)","Admin")
page=st.sidebar.radio("Pages",["Dashboard","Jobs","Candidates","Daily Entry","Reports","Daily Activity & Exports","Control Panel","Audit History"])
departments=opts("Departments"); job_types=opts("Job Types"); job_statuses=opts("Job Statuses")
stages=opts("Candidate Stages"); assignees=opts("Assignees"); sources=opts("Candidate Sources")
priorities=opts("Priorities"); locations=opts("Locations"); reasons=opts("Rejection Reasons")
kpi_days=get_kpi_days()
jobs=df("SELECT * FROM hr_jobs ORDER BY CASE priority WHEN 'Critical' THEN 0 WHEN 'High' THEN 1 ELSE 2 END,created_date")
cands=df("SELECT * FROM hr_candidates ORDER BY id DESC")
apps=df("""SELECT a.*,c.code candidate_code,c.name candidate_name,j.code job_code,j.title job_title,j.departments,j.status job_status
FROM hr_applications a JOIN hr_candidates c ON c.id=a.candidate_id JOIN hr_jobs j ON j.id=a.job_id ORDER BY a.updated_at DESC""")

if page=="Dashboard":
    st.title("HR & Hiring Dashboard")
    openj=jobs[jobs.status=="Open"] if not jobs.empty else jobs
    activeapps=apps[~apps.stage.isin(["Rejected","Withdrawn","Hired"])] if not apps.empty else apps
    overdue=jobs[(jobs.status=="Open")&jobs.target_date.notna()&(jobs.target_date<str(date.today()))] if not jobs.empty else jobs
    a,b,c,d,e=st.columns(5); a.metric("Job roles",len(jobs)); b.metric("Open roles",len(openj)); c.metric("Candidates",len(cands)); d.metric("Active pipeline",len(activeapps)); e.metric("Overdue roles",len(overdue))
    # Job closure KPI: elapsed calendar days from creation to closure must be <= configured target.
    closed_jobs=df("""SELECT j.id,j.code,j.title,j.created_date,j.closed_date,j.updated_at,
        COALESCE((SELECT MIN(a.event_at) FROM hr_activity a WHERE a.job_id=j.id AND a.field='status' AND a.new_value='Closed'),j.closed_date,j.updated_at) AS effective_closed_at
        FROM hr_jobs j WHERE j.status='Closed'""")
    if not closed_jobs.empty:
        closed_jobs["created_dt"]=pd.to_datetime(closed_jobs["created_date"],errors="coerce")
        closed_jobs["closed_dt"]=pd.to_datetime(closed_jobs["effective_closed_at"],errors="coerce")
        closed_jobs["closure_days"]=(closed_jobs["closed_dt"].dt.normalize()-closed_jobs["created_dt"].dt.normalize()).dt.days
        closed_jobs["met_kpi"]=closed_jobs["closure_days"].between(0,kpi_days)
        closed_jobs["month"]=closed_jobs["closed_dt"].dt.to_period("M").astype(str)
        closed_jobs=closed_jobs.dropna(subset=["closed_dt","created_dt"])
    st.subheader(f"Monthly Job Closure KPI (target: close within {kpi_days} days)")
    if not closed_jobs.empty:
        monthly=closed_jobs.groupby("month").agg(**{"Jobs closed":("id","count"),"Met KPI":("met_kpi","sum")}).reset_index().rename(columns={"month":"Month"}).sort_values("Month")
        monthly=monthly.set_index("Month")
        st.line_chart(monthly[["Jobs closed","Met KPI"]],use_container_width=True)
        total_closed=len(closed_jobs); met=int(closed_jobs["met_kpi"].sum())
        m1,m2,m3=st.columns(3); m1.metric("Closed jobs",total_closed); m2.metric("Met KPI",met); m3.metric("KPI achievement",f"{met/total_closed:.1%}" if total_closed else "—")
    else:
        st.info("No closed job roles with usable creation and closure dates yet. Once jobs are closed, the monthly KPI trend will appear here.")
    x,y=st.columns(2)
    with x:
        st.subheader("Open jobs by department")
        if not jobs.empty:
            rows=[]
            for _,r in openj.iterrows():
                for dep in str(r.departments).split(", "): rows.append(dep)
            if rows: st.bar_chart(pd.Series(rows).value_counts())
    with y:
        st.subheader("Candidate pipeline")
        if not apps.empty: st.bar_chart(apps.stage.value_counts())
    st.subheader("Job role summary")
    if not jobs.empty:
        v=jobs.copy(); v["aging_days"]=v.apply(lambda r:(date.today()-date.fromisoformat(r.created_date[:10])).days if r.status=="Open" else 0,axis=1)
        display(v[["code","title","departments","job_type","status","priority","assignee","created_date","target_date","aging_days"]])
    st.subheader("Recent activity")
    display(df("SELECT event_at,changed_by,entity,action,field,old_value,new_value,note FROM hr_activity ORDER BY id DESC LIMIT 15"))

elif page=="Jobs":
    st.title("Jobs / Requisitions")
    with st.expander("➕ Create job role",expanded=jobs.empty):
        with st.form("newjob"):
            a,b,c=st.columns(3); title=a.text_input("Job title *"); deps=b.multiselect("Applicable departments *",departments,default=departments[:1]); jt=c.selectbox("Job type",job_types)
            d,e,f=st.columns(3); status=d.selectbox("Status",job_statuses); priority=e.selectbox("Priority",priorities); assignee=f.selectbox("Assignee",assignees)
            g,h,i=st.columns(3); loc=g.selectbox("Location",locations); openings=h.number_input("Openings",1,500,1); created=i.date_input("Created date",date.today())
            target=st.date_input("Target hiring date",date.today()+timedelta(days=30)); desc=st.text_area("Job description / requirements")
            submit=st.form_submit_button("Create job role",type="primary")
        if submit:
            if not title.strip() or not deps: st.error("Enter a title and select at least one department.")
            else:
                jid=create_job(title.strip(),deps,jt,status,priority,assignee,loc,int(openings),desc,created,target,by); st.success(f"Created job {jid}"); st.rerun()
    if not jobs.empty:
        a,b,c,d=st.columns(4); search=a.text_input("Search role"); depf=b.multiselect("Department filter",departments); statf=c.multiselect("Status filter",job_statuses); typef=d.multiselect("Job type filter",job_types)
        v=jobs.copy()
        if search:v=v[v.title.str.contains(search,case=False,na=False)|v.code.str.contains(search,case=False,na=False)]
        if depf:v=v[v.departments.apply(lambda x:any(dep in x.split(", ") for dep in depf))]
        if statf:v=v[v.status.isin(statf)]
        if typef:v=v[v.job_type.isin(typef)]
        v["aging_days"]=v.apply(lambda r:(date.today()-date.fromisoformat(r.created_date[:10])).days if r.status=="Open" else 0,axis=1)
        v["KPI target (days)"]=kpi_days
        display(v[["code","title","departments","job_type","status","priority","assignee","created_date","target_date","aging_days","KPI target (days)","openings"]])
        jc={f"{r.code} — {r.title}":int(r.id) for _,r in v.iterrows()}
        if jc:
            sel=st.selectbox("View job role",list(jc)); jid=jc[sel]; jr=jobs[jobs.id==jid].iloc[0]
            with st.expander("📂 Expand job details, mapped candidates & audit history", expanded=False):
                st.markdown(f"**Job:** {jr.title}  |  **Code:** {jr.code}")
                st.write(f"**Departments:** {jr.departments} · **Type:** {jr.job_type} · **Status:** {jr.status} · **Priority:** {jr.priority}")
                st.write(f"**Assignee:** {jr.assignee} · **Location:** {jr.location} · **Openings:** {jr.openings}")
                job_age=max(0,(date.today()-date.fromisoformat(str(jr.created_date)[:10])).days) if jr.created_date else 0
                st.write(f"**Created:** {jr.created_date} · **Target date:** {jr.target_date or 'Not set'} · **Created by:** {jr.created_by}")
                st.metric("Job aging (days)",job_age,help=f"Configured closure KPI: within {kpi_days} days")
                st.write("**Description / requirements**")
                st.write(jr.description or "No description provided.")
                st.subheader("Candidates mapped to this job")
                mapped_apps=apps[apps.job_id==jid] if not apps.empty else apps
                display(mapped_apps[["candidate_name","candidate_code","stage","applied_date","updated_at","rating","interview_date"]] if not mapped_apps.empty else mapped_apps)
                st.subheader("Job audit / history")
                display(df("SELECT event_at,changed_by,action,field,old_value,new_value,note FROM hr_activity WHERE job_id=? ORDER BY id DESC",(jid,)))
            with st.expander("✏️ Edit job status / details"):
                with st.form(f"editjob{jid}"):
                    newtitle=st.text_input("Title",jr.title); newdeps=st.multiselect("Departments",departments,default=[x for x in jr.departments.split(", ") if x in departments])
                    newtype=st.selectbox("Job type",job_types,index=job_types.index(jr.job_type) if jr.job_type in job_types else 0)
                    newstatus=st.selectbox("Status",job_statuses,index=job_statuses.index(jr.status) if jr.status in job_statuses else 0)
                    newpriority=st.selectbox("Priority",priorities,index=priorities.index(jr.priority) if jr.priority in priorities else 0)
                    newassignee=st.selectbox("Assignee",assignees,index=assignees.index(jr.assignee) if jr.assignee in assignees else 0)
                    newloc=st.selectbox("Location",locations,index=locations.index(jr.location) if jr.location in locations else 0)
                    newopen=st.number_input("Openings",1,500,int(jr.openings)); newtarget=st.date_input("Target date",date.fromisoformat(jr.target_date) if jr.target_date else date.today()+timedelta(days=30))
                    newdesc=st.text_area("Description",jr.description or ""); note=st.text_input("Change note")
                    save=st.form_submit_button("Save changes")
                if save:
                    if not newtitle.strip() or not newdeps: st.error("Title and departments are required.")
                    else:
                        fields={"title":newtitle.strip(),"departments":", ".join(newdeps),"job_type":newtype,"status":newstatus,"priority":newpriority,"assignee":newassignee,"location":newloc,"openings":int(newopen),"target_date":str(newtarget),"description":newdesc}
                        with con() as cx:
                            old=dict(cx.execute("SELECT * FROM hr_jobs WHERE id=?",(jid,)).fetchone())
                            closed_date = (str(date.today()) if fields["status"]=="Closed" and old.get("status")!="Closed" else (old.get("closed_date") if fields["status"]=="Closed" else None))
                            cx.execute("UPDATE hr_jobs SET title=?,departments=?,job_type=?,status=?,priority=?,assignee=?,location=?,openings=?,target_date=?,description=?,closed_date=?,updated_at=? WHERE id=?",
                            (fields["title"],fields["departments"],fields["job_type"],fields["status"],fields["priority"],fields["assignee"],fields["location"],fields["openings"],fields["target_date"],fields["description"],closed_date,stamp(),jid))
                            for k,nv in fields.items():
                                if str(old.get(k) or "")!=str(nv or ""): event(cx,"job",jid,"FIELD_CHANGED",by,job=jid,field=k,old=old.get(k),new=nv,note=note)
                            event(cx,"job",jid,"JOB_UPDATED",by,job=jid,note=note or "Job details submitted")
                        st.success("Job updated and audit recorded."); st.rerun()

elif page=="Candidates":
    st.title("Candidates")
    with st.expander("➕ Add candidate + CV",expanded=cands.empty):
        with st.form("newcandidate",clear_on_submit=True):
            a,b,c=st.columns(3); name=a.text_input("Full name *"); email=b.text_input("Email"); phone=c.text_input("Phone")
            d,e,f=st.columns(3); company=d.text_input("Current company"); title=e.text_input("Current title"); exp=f.number_input("Experience (years)",0.0,60.0,0.0,0.5)
            g,h,i=st.columns(3); expected=g.text_input("Expected salary / CTC"); current=h.text_input("Current salary / CTC"); notice=i.text_input("Notice period")
            j,k,l=st.columns(3); source=j.selectbox("Source",sources); loc=k.selectbox("Location",locations); recruiter=l.selectbox("Recruiter",assignees)
            created=st.date_input("Added date",date.today()); notes=st.text_area("Notes"); resume=st.file_uploader("Attach CV (PDF/DOC/DOCX/RTF)",type=["pdf","doc","docx","rtf"])
            open_jobs=jobs[jobs.status=="Open"] if not jobs.empty else jobs
            jobmap_create={f"{r.code} — {r.title} ({r.departments})":int(r.id) for _,r in open_jobs.iterrows()}
            map_now=st.checkbox("Map candidate to a job now",value=bool(jobmap_create),disabled=not bool(jobmap_create))
            if jobmap_create and map_now:
                initial_job=st.selectbox("Job role to map to",list(jobmap_create),key="candidate_create_job")
                initial_stage=st.selectbox("Initial application stage",stages,key="candidate_create_stage")
                initial_note=st.text_input("Mapping note",key="candidate_create_mapping_note")
            else:
                initial_job=""; initial_stage=stages[0] if stages else "Applied"; initial_note=""
            submit=st.form_submit_button("Save candidate",type="primary")
        if submit:
            if not name.strip(): st.error("Candidate name is required.")
            else:
                cid=create_candidate(dict(name=name.strip(),email=email,phone=phone,company=company,title=title,experience=exp,expected=expected,current=current,notice=notice,source=source,location=loc,recruiter=recruiter,created=created,notes=notes),resume,by)
                if jobmap_create and map_now and initial_job:
                    mapped_jid=jobmap_create[initial_job]
                    with con() as cx:
                        cur=cx.execute("INSERT INTO hr_applications(candidate_id,job_id,stage,applied_date,updated_at,notes) VALUES(?,?,?,?,?,?)",(cid,mapped_jid,initial_stage,str(date.today()),stamp(),initial_note))
                        event(cx,"application",cur.lastrowid,"CANDIDATE_MAPPED_TO_JOB",by,job=mapped_jid,candidate=cid,field="stage",new=initial_stage,note=initial_note or "Mapped during candidate creation")
                st.success(f"Candidate saved #{cid}" + (" and mapped to the selected job." if jobmap_create and map_now else "")); st.rerun()
    if not cands.empty:
        a,b,c=st.columns(3); search=a.text_input("Search candidate"); sf=b.multiselect("Source",sources); rf=c.multiselect("Recruiter",assignees)
        v=cands.copy()
        if search:v=v[v.name.str.contains(search,case=False,na=False)|v.code.str.contains(search,case=False,na=False)|v.email.str.contains(search,case=False,na=False)]
        if sf:v=v[v.source.isin(sf)]
        if rf:v=v[v.recruiter.isin(rf)]
        display(v[["code","name","email","phone","company","current_title","experience","expected_salary","notice","source","recruiter","created_date"]])
        cc={f"{r.code} — {r['name']}":int(r.id) for _,r in v.iterrows()}
        if cc:
            sel=st.selectbox("Select candidate",list(cc)); cid=cc[sel]; cand=cands[cands.id==cid].iloc[0]
            if cand.resume_data is not None and cand.resume_name:
                st.write("CV: "+str(cand.resume_name)); st.download_button("Download CV",bytes(cand.resume_data),file_name=cand.resume_name)
            st.subheader("Map candidate to open job")
            openj=jobs[jobs.status=="Open"] if not jobs.empty else jobs
            jobmap={f"{r.code} — {r.title} ({r.departments})":int(r.id) for _,r in openj.iterrows()}
            if jobmap:
                with st.form(f"map{cid}"):
                    chosen=st.selectbox("Open job role",list(jobmap)); stage=st.selectbox("Initial stage",stages); note=st.text_input("Mapping note")
                    mapped=st.form_submit_button("Map candidate to job")
                if mapped:
                    jid=jobmap[chosen]
                    with con() as cx:
                        exists=cx.execute("SELECT 1 FROM hr_applications WHERE candidate_id=? AND job_id=?",(cid,jid)).fetchone()
                        if exists: st.warning("Candidate already mapped to this job.")
                        else:
                            cur=cx.execute("INSERT INTO hr_applications(candidate_id,job_id,stage,applied_date,updated_at,notes) VALUES(?,?,?,?,?,?)",(cid,jid,stage,str(date.today()),stamp(),note))
                            event(cx,"application",cur.lastrowid,"CANDIDATE_MAPPED_TO_JOB",by,job=jid,candidate=cid,field="stage",new=stage,note=note)
                            st.success("Candidate mapped; job and candidate audit will reflect it."); st.rerun()
            candidate_apps=apps[apps.candidate_id==cid] if not apps.empty else apps
            st.subheader("Candidate's job applications")
            display(candidate_apps[["job_code","job_title","departments","stage","applied_date","updated_at","rating","interview_date","rejection_reason","notes"]] if not candidate_apps.empty else candidate_apps)
            if not candidate_apps.empty:
                apopts={f"{r.job_code} — {r.job_title} ({r.stage})":int(r.id) for _,r in candidate_apps.iterrows()}
                chosen=st.selectbox("Update application",list(apopts)); aid=apopts[chosen]; ar=candidate_apps[candidate_apps.id==aid].iloc[0]
                with st.form(f"appedit{aid}"):
                    a,b,c=st.columns(3); ns=a.selectbox("Stage",stages,index=stages.index(ar.stage) if ar.stage in stages else 0); rating=b.selectbox("Rating 0–5",[0,1,2,3,4,5],index=int(ar.rating or 0)); interview=c.date_input("Interview date",date.fromisoformat(ar.interview_date) if ar.interview_date else date.today())
                    rejection=st.selectbox("Rejection reason",[""]+reasons,index=([""]+reasons).index(ar.rejection_reason) if ar.rejection_reason in [""]+reasons else 0)
                    appnote=st.text_area("Application notes",ar.notes or ""); update=st.form_submit_button("Save stage / application update")
                if update:
                    with con() as cx:
                        old=dict(cx.execute("SELECT * FROM hr_applications WHERE id=?",(aid,)).fetchone())
                        fields={"stage":ns,"rating":int(rating),"interview_date":str(interview) if ns=="Interview Scheduled" else None,"rejection_reason":rejection,"notes":appnote}
                        cx.execute("UPDATE hr_applications SET stage=?,rating=?,interview_date=?,rejection_reason=?,notes=?,updated_at=? WHERE id=?",(ns,int(rating),fields["interview_date"],rejection,appnote,stamp(),aid))
                        for k,nv in fields.items():
                            if str(old.get(k) or "")!=str(nv or ""): event(cx,"application",aid,"FIELD_CHANGED",by,job=old["job_id"],candidate=cid,field=k,old=old.get(k),new=nv)
                    st.success("Application updated; field-level history recorded."); st.rerun()
            with st.expander("Candidate audit history"):
                display(df("SELECT event_at,changed_by,entity,action,field,old_value,new_value,note FROM hr_activity WHERE candidate_id=? ORDER BY id DESC",(cid,)))

elif page=="Daily Entry":
    st.title("Daily Entry")
    st.caption("Record a recruitment update or follow-up against a job role and/or candidate. Entries are timestamped and included in audit history and reports.")
    job_options={f"{r.code} — {r.title}":int(r.id) for _,r in jobs.iterrows()} if not jobs.empty else {}
    entry_date=st.date_input("Entry date",date.today(),key="daily_entry_date")
    selected_job=st.selectbox("Related job role (optional)",["— None —"]+list(job_options),key="daily_entry_job")
    jid=job_options.get(selected_job)
    # When a job is selected, show only candidates with an application mapped to that job.
    # If no job is selected, allow any candidate to be selected. Candidate mapping is optional.
    if jid is not None:
        mapped_ids=set(apps.loc[apps.job_id==jid,"candidate_id"].astype(int).tolist()) if not apps.empty else set()
        eligible_cands=cands[cands.id.isin(mapped_ids)] if not cands.empty else cands
        if eligible_cands.empty:
            st.info("No candidates are mapped to this job yet. You can leave the candidate blank or map a candidate from the Candidates page.")
    else:
        eligible_cands=cands
    cand_options={f"{r['name']} — {r['code']}":int(r['id']) for _,r in eligible_cands.iterrows()} if not eligible_cands.empty else {}
    selected_candidate=st.selectbox("Related candidate (optional)",["— None —"]+list(cand_options),key=f"daily_entry_candidate_{jid or 'none'}")
    activity_type=st.selectbox("Activity type",["Recruiter follow-up","CV reviewed","Screening completed","Interview update","Candidate feedback","Offer update","Hiring manager follow-up","Job requirement update","Other"],key="daily_entry_type")
    summary=st.text_input("Short summary *",key="daily_entry_summary")
    details=st.text_area("Activity details / next steps",key="daily_entry_details")
    next_followup=st.date_input("Next follow-up date (optional)",value=None,key="daily_entry_followup")
    save_entry=st.button("Save daily entry",type="primary")
    if save_entry:
        if not summary.strip():
            st.error("Please enter a short summary.")
        elif selected_job=="— None —" and selected_candidate=="— None —":
            st.error("Select at least a job role or a candidate so the entry can be linked to a record.")
        else:
            cid=cand_options.get(selected_candidate)
            note_text=f"{activity_type}: {summary.strip()}" + (f" | Details: {details.strip()}" if details.strip() else "") + (f" | Next follow-up: {next_followup}" if next_followup else "")
            with con() as cx:
                event(cx,"daily_entry",jid or cid,"DAILY_ENTRY",by,job=jid,candidate=cid,field="activity",old="",new=summary.strip(),note=f"Entry date: {entry_date} | {note_text}")
            st.success("Daily entry saved to audit history.")
            st.rerun()
    st.subheader("Recent daily entries")
    recent=df("""SELECT a.event_at,a.changed_by,j.code job_code,j.title job_title,c.name candidate_name,c.code candidate_code,a.new_value summary,a.note details
    FROM hr_activity a LEFT JOIN hr_jobs j ON j.id=a.job_id LEFT JOIN hr_candidates c ON c.id=a.candidate_id
    WHERE a.action='DAILY_ENTRY' ORDER BY a.id DESC LIMIT 100""")
    display(recent)
    if not recent.empty:
        st.download_button("Export daily entries (CSV)",recent.to_csv(index=False).encode("utf-8-sig"),file_name=f"daily_entries_{date.today()}.csv",mime="text/csv")

elif page=="Reports":
    st.title("Reports")
    st.caption("Filter recruitment records and activity by date, department, status, job type, owner, candidate stage and source.")
    start_col,end_col=st.columns(2)
    date_from=start_col.date_input("From date",date.today()-timedelta(days=90),key="report_from")
    date_to=end_col.date_input("To date",date.today(),key="report_to")
    f1,f2,f3=st.columns(3)
    dep_filter=f1.multiselect("Department",departments,key="report_dep")
    status_filter=f2.multiselect("Job status",job_statuses,key="report_status")
    type_filter=f3.multiselect("Job type",job_types,key="report_type")
    f4,f5,f6=st.columns(3)
    owner_filter=f4.multiselect("Assignee / recruiter",assignees,key="report_owner")
    stage_filter=f5.multiselect("Candidate stage",stages,key="report_stage")
    source_filter=f6.multiselect("Candidate source",sources,key="report_source")
    tab_jobs,tab_apps,tab_activity=st.tabs(["Job Report","Candidate Pipeline","Activity Report"])
    rjobs=jobs.copy()
    if not rjobs.empty:
        rjobs["created_date_dt"]=pd.to_datetime(rjobs.created_date,errors="coerce").dt.date
        rjobs=rjobs[(rjobs.created_date_dt>=date_from)&(rjobs.created_date_dt<=date_to)]
        if dep_filter:rjobs=rjobs[rjobs.departments.apply(lambda x:any(d in str(x).split(", ") for d in dep_filter))]
        if status_filter:rjobs=rjobs[rjobs.status.isin(status_filter)]
        if type_filter:rjobs=rjobs[rjobs.job_type.isin(type_filter)]
        if owner_filter:rjobs=rjobs[rjobs.assignee.isin(owner_filter)]
        rjobs=rjobs.drop(columns=["created_date_dt"],errors="ignore")
    rapps=apps.copy()
    if not rapps.empty:
        rapps["applied_dt"]=pd.to_datetime(rapps.applied_date,errors="coerce").dt.date
        rapps=rapps[(rapps.applied_dt>=date_from)&(rapps.applied_dt<=date_to)]
        if dep_filter:rapps=rapps[rapps.departments.apply(lambda x:any(d in str(x).split(", ") for d in dep_filter))]
        if status_filter:rapps=rapps[rapps.job_status.isin(status_filter)]
        if type_filter:rapps=rapps[rapps.job_id.isin(rjobs.id)]
        if owner_filter:rapps=rapps[rapps.job_id.isin(rjobs.id)]
        if stage_filter:rapps=rapps[rapps.stage.isin(stage_filter)]
        if source_filter and not cands.empty:rapps=rapps[rapps.candidate_id.isin(cands[cands.source.isin(source_filter)].id)]
        rapps=rapps.drop(columns=["applied_dt"],errors="ignore")
    ractivity=df("""SELECT a.*,j.code job_code,j.title job_title,j.departments,j.job_type,j.status job_status,j.assignee,
        c.code candidate_code,c.name candidate_name,c.source candidate_source,ap.stage candidate_stage
        FROM hr_activity a LEFT JOIN hr_jobs j ON j.id=a.job_id LEFT JOIN hr_candidates c ON c.id=a.candidate_id
        LEFT JOIN hr_applications ap ON ap.candidate_id=a.candidate_id AND ap.job_id=a.job_id
        WHERE date(a.event_at)>=date(?) AND date(a.event_at)<=date(?) ORDER BY a.id DESC""",(str(date_from),str(date_to)))
    if not ractivity.empty:
        if dep_filter:ractivity=ractivity[ractivity.departments.fillna("").apply(lambda x:any(d in x.split(", ") for d in dep_filter))]
        if status_filter:ractivity=ractivity[ractivity.job_status.isin(status_filter)]
        if type_filter:ractivity=ractivity[ractivity.job_type.isin(type_filter)]
        if owner_filter:ractivity=ractivity[ractivity.assignee.isin(owner_filter)]
        if stage_filter:ractivity=ractivity[ractivity.candidate_stage.isin(stage_filter)]
        if source_filter:ractivity=ractivity[ractivity.candidate_source.isin(source_filter)]
    with tab_jobs:
        st.metric("Matching job roles",len(rjobs)); display(rjobs)
        st.download_button("Download job report CSV",rjobs.to_csv(index=False).encode("utf-8-sig"),file_name="job_report.csv",mime="text/csv")
    with tab_apps:
        st.metric("Matching applications",len(rapps)); display(rapps[[c for c in ["candidate_name","candidate_code"] if c in rapps.columns] + [c for c in rapps.columns if c not in ["candidate_name","candidate_code"]]])
        if not rapps.empty: st.bar_chart(rapps.stage.value_counts())
        st.download_button("Download candidate pipeline CSV",rapps.to_csv(index=False).encode("utf-8-sig"),file_name="candidate_pipeline_report.csv",mime="text/csv")
    with tab_activity:
        st.metric("Matching activity records",len(ractivity)); display(ractivity[[c for c in ["candidate_name","candidate_code"] if c in ractivity.columns] + [c for c in ractivity.columns if c not in ["candidate_name","candidate_code"]]])
        st.download_button("Download activity report CSV",ractivity.to_csv(index=False).encode("utf-8-sig"),file_name="filtered_activity_report.csv",mime="text/csv")

elif page=="Daily Activity & Exports":
    st.title("Daily Activity & Exports")
    a,b,c=st.columns(3); start=a.date_input("From",date.today()-timedelta(days=30)); end=b.date_input("To",date.today()); entity=c.multiselect("Entity",["job","candidate","application"])
    activity=df("""SELECT a.*,j.code job_code,j.title job_title,c.code candidate_code,c.name candidate_name FROM hr_activity a
    LEFT JOIN hr_jobs j ON j.id=a.job_id LEFT JOIN hr_candidates c ON c.id=a.candidate_id WHERE date(a.event_at)>=date(?) AND date(a.event_at)<=date(?) ORDER BY a.id DESC""",(str(start),str(end)))
    if entity: activity=activity[activity.entity.isin(entity)]
    search=st.text_input("Search activity")
    if search and not activity.empty: activity=activity[activity.astype(str).apply(lambda col:col.str.contains(search,case=False,na=False)).any(axis=1)]
    st.metric("Changes in selected period",len(activity)); display(activity[[c for c in ["candidate_name","candidate_code"] if c in activity.columns] + [c for c in activity.columns if c not in ["candidate_name","candidate_code"]]])
    st.download_button("Download daily activity CSV",activity.to_csv(index=False).encode("utf-8-sig"),file_name=f"hr_activity_{start}_{end}.csv",mime="text/csv")
    out=io.BytesIO()
    with pd.ExcelWriter(out,engine="openpyxl") as writer:
        activity.to_excel(writer,sheet_name="Daily Activity",index=False); jobs.to_excel(writer,sheet_name="Jobs",index=False)
        cands.drop(columns=["resume_data"],errors="ignore").to_excel(writer,sheet_name="Candidates",index=False); apps.to_excel(writer,sheet_name="Applications",index=False)
    st.download_button("Download full Excel workbook",out.getvalue(),file_name="hr_hiring_export.xlsx",mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

elif page=="Control Panel":
    st.title("Control Panel")
    st.caption("Customize dropdown values. Deactivation keeps existing records intact.")
    for label,kind in {"Departments":"Departments","Job types":"Job Types","Job statuses":"Job Statuses","Candidate stages":"Candidate Stages","Assignees / recruiters":"Assignees","Candidate sources":"Candidate Sources","Priorities":"Priorities","Locations":"Locations","Rejection reasons":"Rejection Reasons"}.items():
        with st.expander(label,expanded=kind=="Candidate Stages"):
            with st.form("add"+kind):
                val=st.text_input("Add option",key="input"+kind); add=st.form_submit_button("Add")
            if add:
                ok,msg=add_option(kind,val)
                if ok: st.success(msg); st.rerun()
                else: st.warning(msg)
            active=opts(kind)
            st.write("Active: "+", ".join(active))
            if active:
                selected=st.selectbox("Deactivate option",active,key="deact"+kind)
                if st.button("Deactivate selected",key="button"+kind):
                    if len(active)==1: st.error("At least one active option must remain.")
                    else:
                        with con() as cx: cx.execute("UPDATE hr_settings SET active=0 WHERE kind=? AND value=?",(kind,selected))
                        st.success("Deactivated; historical records are preserved."); st.rerun()
    st.divider()
    st.subheader("Job closure KPI target")
    st.caption("A job meets KPI when it is closed within the configured number of calendar days from its creation date.")
    with st.form("kpi_target_form"):
        new_kpi_days=st.number_input("Maximum days to close a job role",min_value=1,max_value=3650,value=int(get_kpi_days()),step=1)
        save_kpi=st.form_submit_button("Save KPI target")
    if save_kpi:
        set_kpi_days(int(new_kpi_days),by)
        st.success(f"Job closure KPI updated to {int(new_kpi_days)} days.")
        st.rerun()
    st.divider(); st.write(f"Database: `{DB_LABEL}`")
    st.info("Records and CV attachments are stored in PostgreSQL. Use Supabase backups for recovery; downloaded exports may contain personal data.")

elif page=="Audit History":
    st.title("Audit History")
    audit=df("""SELECT a.*,j.code job_code,j.title job_title,c.code candidate_code,c.name candidate_name
    FROM hr_activity a LEFT JOIN hr_jobs j ON j.id=a.job_id LEFT JOIN hr_candidates c ON c.id=a.candidate_id ORDER BY a.id DESC""")
    a,b,c=st.columns(3); search=a.text_input("Search"); actions=b.multiselect("Actions",sorted(audit.action.dropna().unique()) if not audit.empty else []); users=c.multiselect("Changed by",sorted(audit.changed_by.dropna().unique()) if not audit.empty else [])
    if search and not audit.empty:audit=audit[audit.astype(str).apply(lambda col:col.str.contains(search,case=False,na=False)).any(axis=1)]
    if actions:audit=audit[audit.action.isin(actions)]
    if users:audit=audit[audit.changed_by.isin(users)]
    display(audit[[c for c in ["candidate_name","candidate_code"] if c in audit.columns] + [c for c in audit.columns if c not in ["candidate_name","candidate_code"]]])
    st.download_button("Download full audit CSV",audit.to_csv(index=False).encode("utf-8-sig"),"hr_audit_history.csv","text/csv")
