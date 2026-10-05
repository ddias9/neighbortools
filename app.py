#!/usr/bin/env python3
"""NeighborTools - classroom demo of a neighborhood equipment-sharing board.

Uses only the Python standard library (3.9+):
  * http.server serves the web pages and a small JSON API
  * sqlite3 keeps equipment, requests, and visit tracking in one shared file

Start it with:   python app.py          (this computer only)
                 python app.py --lan    (also other devices on your network)
"""

import argparse
import json
import os
import re
import socket
import sqlite3
import threading
import traceback
import uuid
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from sample_data import OWNERS, PARTICIPANTS, RESEARCH_QUESTION, SAMPLE_TOOLS

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
PAGES = {"/", "/borrow", "/owner", "/results"}  # all served by static/index.html
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
}

WINDOW_HOURS = 48  # a participant counts if they send a request within 48 h of their first visit
THRESHOLD = 3      # research threshold: at least 3 of the 5 participants
GUEST = "guest"    # borrower link that works normally but is never counted in results

OWNER_BY_ID = {o["id"]: o for o in OWNERS}
PARTICIPANT_BY_ID = {p["id"]: p for p in PARTICIPANTS}

SCHEMA = """
CREATE TABLE IF NOT EXISTS tools (
    id             TEXT PRIMARY KEY,
    owner_id       TEXT NOT NULL,
    name           TEXT NOT NULL,
    description    TEXT NOT NULL,
    pickup_area    TEXT NOT NULL,
    available_from TEXT,               -- YYYY-MM-DD; NULL means "use the offsets below"
    available_to   TEXT,
    from_offset    INTEGER,            -- sample items: days from today, so the demo never expires
    to_offset      INTEGER,
    listed         INTEGER NOT NULL DEFAULT 1,
    sort_order     INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS requests (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    tool_id          TEXT NOT NULL,
    participant_id   TEXT NOT NULL,
    borrower_name    TEXT NOT NULL,
    contact          TEXT NOT NULL,
    job              TEXT NOT NULL,
    requested_pickup TEXT NOT NULL,    -- local time, YYYY-MM-DDTHH:MM
    requested_return TEXT NOT NULL,
    confirmed_pickup TEXT,             -- set by the owner when accepting
    confirmed_return TEXT,
    status           TEXT NOT NULL DEFAULT 'pending',
    owner_note       TEXT NOT NULL DEFAULT '',
    created_at       TEXT NOT NULL,    -- UTC, YYYY-MM-DDTHH:MM:SSZ
    decided_at       TEXT,
    picked_up_at     TEXT,
    returned_at      TEXT
);
CREATE TABLE IF NOT EXISTS visits (
    participant_id TEXT PRIMARY KEY,
    first_visit_at TEXT NOT NULL,      -- UTC; starts the 48-hour window
    last_visit_at  TEXT NOT NULL,
    visit_count    INTEGER NOT NULL DEFAULT 1
);
"""

REQUEST_QUERY = (
    "SELECT r.*, t.name AS tool_name, t.owner_id, t.pickup_area"
    " FROM requests r JOIN tools t ON t.id = r.tool_id"
)

# Same tables for the hosted version (Postgres, e.g. Vercel + Neon).
POSTGRES_SCHEMA = """
CREATE TABLE IF NOT EXISTS tools (
    id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, name TEXT NOT NULL, description TEXT NOT NULL,
    pickup_area TEXT NOT NULL, available_from TEXT, available_to TEXT, from_offset INTEGER,
    to_offset INTEGER, listed INTEGER NOT NULL DEFAULT 1, sort_order INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS requests (
    id SERIAL PRIMARY KEY, tool_id TEXT NOT NULL, participant_id TEXT NOT NULL,
    borrower_name TEXT NOT NULL, contact TEXT NOT NULL, job TEXT NOT NULL,
    requested_pickup TEXT NOT NULL, requested_return TEXT NOT NULL,
    confirmed_pickup TEXT, confirmed_return TEXT, status TEXT NOT NULL DEFAULT 'pending',
    owner_note TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, decided_at TEXT,
    picked_up_at TEXT, returned_at TEXT
);
CREATE TABLE IF NOT EXISTS visits (
    participant_id TEXT PRIMARY KEY, first_visit_at TEXT NOT NULL, last_visit_at TEXT NOT NULL,
    visit_count INTEGER NOT NULL DEFAULT 1
)
"""

# When hosted, these come from environment variables. Locally neither is set: the app
# uses SQLite and this computer's clock.
DATABASE_URL = os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URL")
APP_TIMEZONE = os.environ.get("APP_TIMEZONE")  # e.g. America/New_York; hosting servers run on UTC

db = None                   # one database connection shared by all request threads,
db_lock = threading.Lock()  # guarded by this lock so each API call is all-or-nothing
lan_urls = []
schema_ready = False


class ApiError(Exception):
    def __init__(self, status, message, errors=None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.errors = errors or []


# ------------------------------------------------------------------ time helpers
# Pickup and return times are plain local times ("2026-10-10T09:00") because all
# neighbors share one time zone; fixed-width strings compare correctly as text.
# Tracking timestamps are UTC so the 48-hour window is exact.

LOCAL_DATETIME = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d{1,3})?)?$")
DATE_ONLY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def utc_now():
    return datetime.now(timezone.utc)


def to_iso(moment):
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def from_iso(text):
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def local_datetime():
    if APP_TIMEZONE:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo(APP_TIMEZONE)).replace(tzinfo=None)
    return datetime.now()


def local_now():
    return local_datetime().strftime("%Y-%m-%dT%H:%M")


def today():
    return local_datetime().date()


def parse_local_datetime(value):
    """'YYYY-MM-DDTHH:MM' for a valid datetime-local value, otherwise None."""
    if not isinstance(value, str) or not LOCAL_DATETIME.match(value.strip()):
        return None
    value = value.strip()[:16]  # drop seconds if a browser sends them
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M")
    except ValueError:
        return None
    return value


def parse_date(value):
    if not isinstance(value, str) or not DATE_ONLY.match(value.strip()):
        return None
    try:
        date.fromisoformat(value.strip())
    except ValueError:
        return None
    return value.strip()


def nice_datetime(value):
    d = datetime.strptime(value, "%Y-%m-%dT%H:%M")
    return f"{d:%a %b} {d.day}, {d.hour % 12 or 12}:{d:%M} {'AM' if d.hour < 12 else 'PM'}"


def nice_date(value):
    d = date.fromisoformat(value)
    return f"{d:%a %b} {d.day}"


# ----------------------------------------------------------------------- storage

def open_database(path):
    """SQLite file locally; Postgres when DATABASE_URL is set (hosted)."""
    global schema_ready
    if DATABASE_URL:
        import postgres
        conn, schema = postgres.Connection(DATABASE_URL), POSTGRES_SCHEMA
    else:
        conn, schema = sqlite3.connect(path, check_same_thread=False), SCHEMA
        conn.row_factory = sqlite3.Row
    if not schema_ready:
        conn.executescript(schema)
        if conn.execute("SELECT COUNT(*) FROM tools").fetchone()[0] == 0:
            seed_sample_tools(conn)
        conn.commit()
        schema_ready = True
    return conn


def seed_sample_tools(conn):
    for order, t in enumerate(SAMPLE_TOOLS):
        conn.execute(
            "INSERT INTO tools (id, owner_id, name, description, pickup_area, from_offset, to_offset,"
            " listed, sort_order) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?) ON CONFLICT (id) DO NOTHING",
            (t["id"], t["ownerId"], t["name"], t["description"], t["pickupArea"],
             t["fromOffset"], t["toOffset"], order))


def availability(tool):
    """(first_day, last_day) as YYYY-MM-DD. Unedited sample items move with today's date."""
    if tool["available_from"]:
        return tool["available_from"], tool["available_to"]
    now = today()
    return ((now + timedelta(days=tool["from_offset"])).isoformat(),
            (now + timedelta(days=tool["to_offset"])).isoformat())


def get_tool(tool_id):
    return db.execute("SELECT * FROM tools WHERE id = ?", (tool_id,)).fetchone()


def get_request(request_id):
    return db.execute(REQUEST_QUERY + " WHERE r.id = ?", (request_id,)).fetchone()


def find_clash(tool_id, start, end, ignore_request_id=None):
    """The first accepted or picked-up reservation of this tool that overlaps [start, end)."""
    return db.execute(
        "SELECT * FROM requests WHERE tool_id = ? AND id != ?"
        " AND status IN ('accepted', 'picked_up')"
        " AND confirmed_pickup < ? AND ? < confirmed_return"
        " ORDER BY confirmed_pickup LIMIT 1",
        (tool_id, ignore_request_id or 0, end, start)).fetchone()


def tool_json(tool):
    first, last = availability(tool)
    reservations = db.execute(
        "SELECT confirmed_pickup, confirmed_return, status FROM requests WHERE tool_id = ?"
        " AND (status = 'picked_up' OR (status = 'accepted' AND confirmed_return >= ?))"
        " ORDER BY confirmed_pickup", (tool["id"], local_now())).fetchall()
    return {
        "id": tool["id"],
        "ownerId": tool["owner_id"],
        "ownerName": OWNER_BY_ID[tool["owner_id"]]["firstName"],
        "name": tool["name"],
        "description": tool["description"],
        "pickupArea": tool["pickup_area"],
        "availableFrom": first,
        "availableTo": last,
        "relativeDates": tool["available_from"] is None,
        "listed": bool(tool["listed"]),
        "reservations": [{"start": r["confirmed_pickup"], "end": r["confirmed_return"],
                          "status": r["status"]} for r in reservations],
    }


def participant_label(pid):
    p = PARTICIPANT_BY_ID.get(pid)
    return f"{p['label']} ({p['name']})" if p else "Guest (not tracked)"


def request_json(r):
    return {
        "id": r["id"],
        "toolId": r["tool_id"],
        "toolName": r["tool_name"],
        "ownerId": r["owner_id"],
        "ownerName": OWNER_BY_ID[r["owner_id"]]["firstName"],
        "pickupArea": r["pickup_area"],
        "participantId": r["participant_id"],
        "participantLabel": participant_label(r["participant_id"]),
        "borrowerName": r["borrower_name"],
        "contact": r["contact"],
        "job": r["job"],
        "requestedPickup": r["requested_pickup"],
        "requestedReturn": r["requested_return"],
        "confirmedPickup": r["confirmed_pickup"],
        "confirmedReturn": r["confirmed_return"],
        "status": r["status"],
        "ownerNote": r["owner_note"],
        "createdAt": r["created_at"],
        "decidedAt": r["decided_at"],
        "pickedUpAt": r["picked_up_at"],
        "returnedAt": r["returned_at"],
    }


# -------------------------------------------------------------------- validation

def text_field(body, key, label, max_len, errors, required=True):
    """Trimmed text from the request body; appends a message to errors if invalid."""
    value = body.get(key)
    value = value.strip() if isinstance(value, str) else ""
    if required and not value:
        errors.append(f"{label} is required.")
        return ""
    if len(value) > max_len:
        errors.append(f"{label} must be {max_len} characters or fewer.")
        return ""
    return value


def check_times(tool, pickup_raw, return_raw, errors, ignore_request_id=None, allow_past=False):
    """Validate a pickup/return pair against the tool's availability and reservations."""
    pickup = parse_local_datetime(pickup_raw)
    ret = parse_local_datetime(return_raw)
    if not pickup:
        errors.append("Enter a valid pickup date and time.")
    if not ret:
        errors.append("Enter a valid return date and time.")
    if not pickup or not ret:
        return pickup, ret

    problems = []
    if ret <= pickup:
        problems.append("Return time must be after pickup time.")
    if not allow_past and pickup < local_now():
        problems.append("Pickup time can't be in the past.")
    first, last = availability(tool)
    if pickup[:10] < first or ret[:10] > last:
        problems.append(f"Pickup and return must fall within the listed availability "
                        f"({nice_date(first)} to {nice_date(last)}).")
    if not problems:
        clash = find_clash(tool["id"], pickup, ret, ignore_request_id)
        if clash:
            problems.append(
                f"Those times overlap an accepted reservation ({nice_datetime(clash['confirmed_pickup'])} "
                f"to {nice_datetime(clash['confirmed_return'])}). Choose times that don't overlap.")
    errors.extend(problems)
    return pickup, ret


def participant_info(pid):
    if pid == GUEST:
        return {"id": GUEST, "label": "Guest", "isGuest": True}
    if pid not in PARTICIPANT_BY_ID:
        raise ApiError(404, "Unknown participant link.")
    return {**PARTICIPANT_BY_ID[pid], "isGuest": False}


def read_tool_fields(body):
    errors = []
    fields = {
        "name": text_field(body, "name", "Name", 80, errors),
        "description": text_field(body, "description", "Description", 500, errors),
        "pickup_area": text_field(body, "pickupArea", "Pickup area", 120, errors),
    }
    first, last = parse_date(body.get("availableFrom")), parse_date(body.get("availableTo"))
    if not first:
        errors.append("Enter a valid 'available from' date.")
    if not last:
        errors.append("Enter a valid 'available until' date.")
    if first and last and last < first:
        errors.append("'Available until' must be on or after 'available from'.")
    if errors:
        raise ApiError(400, "Please fix the following:", errors)
    fields.update(available_from=first, available_to=last, listed=1 if body.get("listed", True) else 0)
    return fields


# --------------------------------------------------------------------- API calls
# Each handler gets (query, body, *url_groups) and returns a JSON-able dict.

def api_config(query, body):
    return {"owners": OWNERS, "participants": PARTICIPANTS, "researchQuestion": RESEARCH_QUESTION,
            "windowHours": WINDOW_HOURS, "threshold": THRESHOLD, "lanUrls": lan_urls}


def api_board(query, body):
    pid = query.get("p", [""])[0]
    participant = participant_info(pid)
    tools = db.execute("SELECT * FROM tools WHERE listed = 1 ORDER BY sort_order").fetchall()
    mine = db.execute(REQUEST_QUERY + " WHERE r.participant_id = ? ORDER BY r.id DESC", (pid,)).fetchall()
    return {"participant": participant, "today": today().isoformat(),
            "tools": [tool_json(t) for t in tools], "requests": [request_json(r) for r in mine]}


def api_record_visit(query, body):
    pid = body.get("participantId")
    participant_info(pid)
    if pid == GUEST:
        return {"tracked": False}
    now = to_iso(utc_now())
    db.execute(
        "INSERT INTO visits (participant_id, first_visit_at, last_visit_at) VALUES (?, ?, ?)"
        " ON CONFLICT(participant_id) DO UPDATE SET last_visit_at = excluded.last_visit_at,"
        " visit_count = visits.visit_count + 1", (pid, now, now))
    visit = db.execute("SELECT * FROM visits WHERE participant_id = ?", (pid,)).fetchone()
    return {"tracked": True, "firstVisitAt": visit["first_visit_at"], "visitCount": visit["visit_count"]}


def api_create_request(query, body):
    pid = body.get("participantId")
    participant_info(pid)
    tool = get_tool(body.get("toolId"))
    if not tool or not tool["listed"]:
        raise ApiError(404, "That item is not on the board.")

    errors = []
    name = text_field(body, "borrowerName", "Your name", 80, errors)
    contact = text_field(body, "contact", "Phone or email", 120, errors)
    if contact and "@" not in contact and len(re.sub(r"\D", "", contact)) < 7:
        errors.append("Phone or email must be an email address or a phone number with at least 7 digits.")
    job = text_field(body, "job", "Maintenance job", 500, errors)
    pickup, ret = check_times(tool, body.get("pickupAt"), body.get("returnAt"), errors)
    if errors:
        raise ApiError(400, "Please fix the following:", errors)

    now = to_iso(utc_now())
    if pid != GUEST:  # make sure the 48-hour clock exists even if the page skipped the visit call
        db.execute("INSERT INTO visits (participant_id, first_visit_at, last_visit_at)"
                   " VALUES (?, ?, ?) ON CONFLICT (participant_id) DO NOTHING", (pid, now, now))
    cur = db.execute(
        "INSERT INTO requests (tool_id, participant_id, borrower_name, contact, job,"
        " requested_pickup, requested_return, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) RETURNING id",
        (tool["id"], pid, name, contact, job, pickup, ret, now))
    return {"request": request_json(get_request(cur.fetchone()["id"]))}


def api_owner(query, body, owner_id):
    owner = OWNER_BY_ID.get(owner_id)
    if not owner:
        raise ApiError(404, "Unknown owner link.")
    tools = db.execute("SELECT * FROM tools WHERE owner_id = ? ORDER BY sort_order", (owner_id,)).fetchall()
    rows = db.execute(REQUEST_QUERY + " WHERE t.owner_id = ? ORDER BY r.id DESC", (owner_id,)).fetchall()
    requests = []
    for r in rows:
        item = request_json(r)
        if r["status"] == "pending":  # warn the owner before they try to accept
            clash = find_clash(r["tool_id"], r["requested_pickup"], r["requested_return"], r["id"])
            item["conflict"] = ({"start": clash["confirmed_pickup"], "end": clash["confirmed_return"]}
                                if clash else None)
        requests.append(item)
    return {"owner": owner, "today": today().isoformat(),
            "tools": [tool_json(t) for t in tools], "requests": requests}


def require_status(r, allowed, action):
    if r["status"] not in allowed:
        raise ApiError(409, f"Can't {action} a request that is {r['status'].replace('_', ' ')}.")


def api_request_action(query, body, request_id, action):
    r = get_request(int(request_id))
    if not r:
        raise ApiError(404, "Request not found.")
    if body.get("ownerId") != r["owner_id"]:
        raise ApiError(403, "Only the item's owner can change this request.")
    now = to_iso(utc_now())
    errors = []

    if action == "accept":
        require_status(r, ("pending",), "accept")
        pickup, ret = check_times(get_tool(r["tool_id"]), body.get("pickupAt"), body.get("returnAt"),
                                  errors, ignore_request_id=r["id"], allow_past=True)
        note = text_field(body, "note", "Note", 300, errors, required=False)
        if errors:
            raise ApiError(400, "Can't accept with these times:", errors)
        db.execute("UPDATE requests SET status = 'accepted', confirmed_pickup = ?, confirmed_return = ?,"
                   " owner_note = ?, decided_at = ? WHERE id = ?", (pickup, ret, note, now, r["id"]))
    elif action == "decline":  # also used to cancel an accepted reservation
        require_status(r, ("pending", "accepted"), "decline")
        note = text_field(body, "note", "Note", 300, errors, required=False)
        if errors:
            raise ApiError(400, "Please fix the following:", errors)
        db.execute("UPDATE requests SET status = 'declined', confirmed_pickup = NULL,"
                   " confirmed_return = NULL, owner_note = ?, decided_at = ? WHERE id = ?",
                   (note, now, r["id"]))
    elif action == "picked-up":
        require_status(r, ("accepted",), "mark as picked up")
        db.execute("UPDATE requests SET status = 'picked_up', picked_up_at = ? WHERE id = ?", (now, r["id"]))
    elif action == "returned":
        require_status(r, ("picked_up",), "mark as returned")
        db.execute("UPDATE requests SET status = 'returned', returned_at = ? WHERE id = ?", (now, r["id"]))
    return {"request": request_json(get_request(r["id"]))}


def api_create_tool(query, body):
    owner_id = body.get("ownerId")
    if owner_id not in OWNER_BY_ID:
        raise ApiError(400, "Unknown owner.")
    f = read_tool_fields(body)
    tool_id = "t-" + uuid.uuid4().hex[:8]
    order = db.execute("SELECT COALESCE(MAX(sort_order), 0) + 1 FROM tools").fetchone()[0]
    db.execute(
        "INSERT INTO tools (id, owner_id, name, description, pickup_area, available_from, available_to,"
        " listed, sort_order) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (tool_id, owner_id, f["name"], f["description"], f["pickup_area"], f["available_from"],
         f["available_to"], f["listed"], order))
    return {"tool": tool_json(get_tool(tool_id))}


def api_update_tool(query, body, tool_id):
    tool = get_tool(tool_id)
    if not tool:
        raise ApiError(404, "Equipment not found.")
    if body.get("ownerId") != tool["owner_id"]:
        raise ApiError(403, "Only the owner can edit this equipment.")
    f = read_tool_fields(body)
    if tool["available_from"] is None and (f["available_from"], f["available_to"]) == availability(tool):
        f["available_from"] = f["available_to"] = None  # dates unchanged: keep them relative to today
    db.execute(
        "UPDATE tools SET name = ?, description = ?, pickup_area = ?, available_from = ?,"
        " available_to = ?, listed = ? WHERE id = ?",
        (f["name"], f["description"], f["pickup_area"], f["available_from"], f["available_to"],
         f["listed"], tool_id))
    return {"tool": tool_json(get_tool(tool_id))}


def api_results(query, body):
    now = utc_now()
    window = timedelta(hours=WINDOW_HOURS)
    participants = []
    for p in PARTICIPANTS:
        visit = db.execute("SELECT * FROM visits WHERE participant_id = ?", (p["id"],)).fetchone()
        sent = [from_iso(row["created_at"]) for row in db.execute(
            "SELECT created_at FROM requests WHERE participant_id = ? ORDER BY created_at", (p["id"],))]
        first_visit = from_iso(visit["first_visit_at"]) if visit else None
        first_request = sent[0] if sent else None
        # The server only stores requests that passed validation, so every stored request is complete.
        # A participant counts once, no matter how many requests they send.
        counted = bool(first_visit and any(first_visit <= s <= first_visit + window for s in sent))
        if not first_visit:
            state = "not_visited"
        elif counted:
            state = "counted"
        elif now <= first_visit + window:
            state = "window_open"
        else:
            state = "not_counted"
        participants.append({
            **p,
            "link": f"/borrow?p={p['id']}",
            "state": state,
            "counted": counted,
            "firstVisitAt": visit["first_visit_at"] if visit else None,
            "visitCount": visit["visit_count"] if visit else 0,
            "windowEndsAt": to_iso(first_visit + window) if first_visit else None,
            "requestCount": len(sent),
            "firstRequestAt": to_iso(first_request) if first_request else None,
            "minutesToFirstRequest": (round((first_request - first_visit).total_seconds() / 60)
                                      if first_request and first_visit else None),
        })
    counted = sum(p["counted"] for p in participants)
    still_open = sum(p["state"] in ("not_visited", "window_open") for p in participants)
    rows = db.execute(REQUEST_QUERY + " ORDER BY r.id DESC").fetchall()
    return {
        "simulated": True,
        "threshold": THRESHOLD,
        "windowHours": WINDOW_HOURS,
        "total": len(PARTICIPANTS),
        "counted": counted,
        "thresholdMet": counted >= THRESHOLD,
        "maxPossible": counted + still_open,
        "participants": participants,
        "requests": [request_json(r) for r in rows],
        "guestRequests": sum(1 for r in rows if r["participant_id"] == GUEST),
    }


def api_reset(query, body):
    db.execute("DELETE FROM requests")
    db.execute("DELETE FROM visits")
    db.execute("DELETE FROM tools")
    # Request numbers restart at 1.
    if DATABASE_URL:
        db.execute("ALTER SEQUENCE requests_id_seq RESTART WITH 1")
    else:
        db.execute("DELETE FROM sqlite_sequence WHERE name = 'requests'")
    seed_sample_tools(db)
    return {"ok": True}


ROUTES = [
    ("GET", r"/api/config", api_config),
    ("GET", r"/api/board", api_board),
    ("POST", r"/api/visits", api_record_visit),
    ("POST", r"/api/requests", api_create_request),
    ("POST", r"/api/requests/(\d+)/(accept|decline|picked-up|returned)", api_request_action),
    ("GET", r"/api/owner/([\w-]+)", api_owner),
    ("POST", r"/api/tools", api_create_tool),
    ("PUT", r"/api/tools/([\w-]+)", api_update_tool),
    ("GET", r"/api/results", api_results),
    ("POST", r"/api/reset", api_reset),
]


def run_in_transaction(handler, method, query, body, groups):
    """Run one API call all-or-nothing.

    Hosted (Postgres) requests may run on separate short-lived servers, so each call opens
    its own connection, and changes take a database-wide lock so two people can't book
    overlapping times at the same moment.
    """
    global db
    if DATABASE_URL:
        db = open_database(None)
    try:
        if DATABASE_URL and method != "GET":
            db.execute("SELECT pg_advisory_xact_lock(4242)")
        result = handler(query, body, *groups)
        db.commit()
        return result
    except BaseException:
        db.rollback()
        raise
    finally:
        if DATABASE_URL:
            db.close()


# ------------------------------------------------------------------- web server

class Handler(BaseHTTPRequestHandler):
    server_version = "NeighborTools/1.0"

    def do_GET(self):
        self.handle_any("GET")

    def do_POST(self):
        self.handle_any("POST")

    def do_PUT(self):
        self.handle_any("PUT")

    def handle_any(self, method):
        url = urlparse(self.path)
        if not url.path.startswith("/api/"):
            if method == "GET":
                return self.serve_file(url.path)
            return self.send_json(405, {"error": "Method not allowed."})
        try:
            for route_method, pattern, handler in ROUTES:
                match = re.fullmatch(pattern, url.path)
                if match and route_method == method:
                    body = self.read_json() if method != "GET" else {}
                    with db_lock:
                        result = run_in_transaction(handler, method, parse_qs(url.query), body, match.groups())
                    return self.send_json(200, result)
            raise ApiError(404, f"Unknown API endpoint: {method} {url.path}")
        except ApiError as err:
            self.send_json(err.status, {"error": err.message, "errors": err.errors})
        except Exception:
            traceback.print_exc()
            self.send_json(500, {"error": "Something went wrong on the server."})

    def read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length > 100_000:
            raise ApiError(413, "Request too large.")
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            raise ApiError(400, "Request body must be JSON.")
        if not isinstance(body, dict):
            raise ApiError(400, "Request body must be a JSON object.")
        return body

    def send_json(self, status, payload):
        self.send_bytes(status, json.dumps(payload).encode("utf-8"), "application/json; charset=utf-8")

    def send_bytes(self, status, data, content_type):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def serve_file(self, path):
        clean = path.rstrip("/") or "/"
        if clean in PAGES:
            file_path = os.path.join(STATIC_DIR, "index.html")
        elif clean.startswith("/static/"):
            file_path = os.path.normpath(os.path.join(STATIC_DIR, clean[len("/static/"):]))
        else:
            file_path = None
        if not file_path or not file_path.startswith(STATIC_DIR + os.sep) or not os.path.isfile(file_path):
            return self.send_bytes(404, b"Not found", "text/plain; charset=utf-8")
        with open(file_path, "rb") as f:
            data = f.read()
        content_type = CONTENT_TYPES.get(os.path.splitext(file_path)[1], "application/octet-stream")
        self.send_bytes(200, data, content_type)

    def log_request(self, code="-", size="-"):
        # Keep the console readable: skip successful page loads and the 5-second polling.
        status = str(getattr(code, "value", code))  # code may be an HTTPStatus
        if self.command != "GET" or status[:1] in ("4", "5"):
            super().log_request(code, size)


def local_ip_addresses():
    """Best-effort list of this computer's network addresses (shown with --lan)."""
    found = []
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(("10.255.255.255", 1))  # UDP "connect" sends nothing; it just picks the main interface
        found.append(probe.getsockname()[0])
        probe.close()
    except OSError:
        pass
    try:
        found += socket.gethostbyname_ex(socket.gethostname())[2]
    except OSError:
        pass
    return [ip for i, ip in enumerate(found) if not ip.startswith("127.") and ip not in found[:i]]


def main():
    global db, lan_urls
    parser = argparse.ArgumentParser(description="NeighborTools classroom demo server")
    # Hosting services such as Render set PORT and expect the app to listen on all interfaces.
    hosted = "PORT" in os.environ
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8000)),
                        help="port to listen on (default 8000, or $PORT when hosted)")
    parser.add_argument("--lan", action="store_true",
                        help="also accept connections from other devices on your network")
    parser.add_argument("--db", default=os.path.join(BASE_DIR, "neighbortools.db"),
                        help="SQLite database file (default: neighbortools.db next to app.py)")
    args = parser.parse_args()

    db = open_database(args.db)
    host = "0.0.0.0" if args.lan or hosted else "127.0.0.1"
    try:
        server = ThreadingHTTPServer((host, args.port), Handler)
    except OSError as err:
        raise SystemExit(f"Could not start on port {args.port} ({err}). Try: python app.py --port 8001")
    if args.lan:
        lan_urls = [f"http://{ip}:{args.port}" for ip in local_ip_addresses()]

    print("NeighborTools classroom demo is running.", flush=True)
    print(f"  On this computer:     http://localhost:{args.port}", flush=True)
    if args.lan:
        for url in lan_urls or ["(could not detect this computer's network address)"]:
            print(f"  On other devices:     {url}", flush=True)
    else:
        print("  Other devices:        restart with  python app.py --lan", flush=True)
    print(f"  Data file:            {args.db}", flush=True)
    print("Press Ctrl+C to stop.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()
        db.close()


if __name__ == "__main__":
    main()
