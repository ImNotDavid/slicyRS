"""Records, as named collections. Where and how they are kept is the storage layer's business (see storage/)."""
from .storage import db

rate_hit = db.rate_hit
rate_reset = db.rate_reset

printers = db.collection("printers")
jobs = db.collection("jobs")
users = db.collection("users")
models = db.collection("models")
audit = db.collection("audit")
codes = db.collection("login_codes")
settings = db.collection("settings")
sessions = db.collection("sessions")
events = db.collection("job_events")
gateways = db.collection("gateways")
