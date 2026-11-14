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
presets = db.collection("presets")
channels = db.collection("notification_channels")   # shared channels (Discord webhooks) set by admins
notices = db.collection("notices")   # short messages admins post on the Home page (2.18)
folders = db.collection("part_folders")   # My Parts folders (2.17)
spools = db.collection("spools")   # filament inventory (2.10)
groups = db.collection("groups")   # groups (3.0)
roles = db.collection("roles")     # custom roles (3.0)
topups = db.collection("quota_topups")   # extra allowance granted by group operators (3.2)
