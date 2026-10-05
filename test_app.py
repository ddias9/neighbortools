"""End-to-end checks for the NeighborTools demo (standard library only).

Starts the real server on a spare port with a throwaway database, then walks
through the core flow over HTTP, including a server restart. Your real demo
data (neighbortools.db) is not touched.

Run:  python -m unittest -v test_app
"""

import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import date, datetime, timedelta, timezone
from urllib.error import HTTPError
from urllib.request import Request, urlopen

HERE = os.path.dirname(os.path.abspath(__file__))
P1, P2, P3, P4, P5 = "k7m2q", "r4t8w", "h3n6z", "b9x5c", "f2v7j"
WASHER, LADDER, WHEELBARROW, TILLER = "t1", "t2", "t3", "t7"


def at(days, hour, minute=0):
    """Local 'YYYY-MM-DDTHH:MM' a number of days from today."""
    return f"{(date.today() + timedelta(days=days)).isoformat()}T{hour:02d}:{minute:02d}"


def utc_hours_ago(hours):
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class NeighborToolsFlow(unittest.TestCase):
    """Tests run in name order and share one server, like a real demo session."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db_path = os.path.join(cls.tmp.name, "test.db")
        cls.log = open(os.path.join(cls.tmp.name, "server.log"), "w")
        cls.port = free_port()
        cls.start_server()

    @classmethod
    def tearDownClass(cls):
        cls.stop_server()
        cls.log.close()
        cls.tmp.cleanup()

    @classmethod
    def start_server(cls):
        cls.proc = subprocess.Popen(
            [sys.executable, os.path.join(HERE, "app.py"), "--port", str(cls.port), "--db", cls.db_path],
            stdout=cls.log, stderr=cls.log)
        for _ in range(100):
            try:
                cls.call("GET", "/api/config")
                return
            except OSError:
                time.sleep(0.1)
        raise RuntimeError("server did not start")

    @classmethod
    def stop_server(cls):
        cls.proc.terminate()  # a hard stop: anything not committed would be lost
        cls.proc.wait(10)

    @classmethod
    def call(cls, method, path, body=None, expect=200):
        data = json.dumps(body).encode() if body is not None else None
        req = Request(f"http://127.0.0.1:{cls.port}{path}", data=data, method=method,
                      headers={"Content-Type": "application/json"})
        try:
            with urlopen(req, timeout=5) as res:
                status, payload = res.status, json.loads(res.read())
        except HTTPError as err:
            status, payload = err.code, json.loads(err.read())
        if expect is not None and status != expect:
            raise AssertionError(f"{method} {path} returned {status}, expected {expect}: {payload}")
        return payload

    def request(self, participant, tool, pickup, ret, expect=200, **overrides):
        body = {"participantId": participant, "toolId": tool, "borrowerName": "Claire",
                "contact": "555-201-3344", "job": "Pressure-wash the deck", "pickupAt": pickup, "returnAt": ret}
        body.update(overrides)
        return self.call("POST", "/api/requests", body, expect)

    def results(self):
        res = self.call("GET", "/api/results")
        return res, {p["id"]: p for p in res["participants"]}

    # -------------------------------------------------------------------------

    def test_01_sample_data(self):
        cfg = self.call("GET", "/api/config")
        self.assertEqual(len(cfg["owners"]), 3)
        self.assertEqual(len({p["id"] for p in cfg["participants"]}), 5)
        tools = self.call("GET", f"/api/board?p={P1}")["tools"]
        self.assertTrue(8 <= len(tools) <= 10)
        names = " ".join(t["name"].lower() for t in tools)
        for needed in ("pressure washer", "ladder", "hedge trimmer", "wheelbarrow"):
            self.assertIn(needed, names)
        self.assertEqual(len({t["ownerName"] for t in tools}), 3)
        washer = next(t for t in tools if t["id"] == WASHER)
        self.assertEqual(washer["availableFrom"], date.today().isoformat())  # relative to today
        self.assertTrue(washer["relativeDates"])

    def test_02_first_visit_recorded_once(self):
        first = self.call("POST", "/api/visits", {"participantId": P1})
        time.sleep(1.1)
        again = self.call("POST", "/api/visits", {"participantId": P1})
        self.assertEqual(first["firstVisitAt"], again["firstVisitAt"])
        self.assertEqual(again["visitCount"], 2)
        self.call("POST", "/api/visits", {"participantId": "nobody"}, expect=404)
        _, rows = self.results()
        self.assertEqual(rows[P1]["state"], "window_open")
        self.assertEqual(rows[P2]["state"], "not_visited")

    def test_03_validation(self):
        err = self.request(P1, WASHER, at(1, 9), at(2, 17), expect=400, borrowerName=" ", job="")
        self.assertEqual(len(err["errors"]), 2)
        err = self.request(P1, WASHER, at(2, 9), at(1, 9), expect=400)
        self.assertIn("after pickup", " ".join(err["errors"]))
        err = self.request(P1, TILLER, at(1, 9), at(1, 17), expect=400)  # tiller starts in 5 days
        self.assertIn("availability", " ".join(err["errors"]))
        err = self.request(P1, WASHER, at(1, 9), at(40, 17), expect=400)  # washer ends in 21 days
        self.assertIn("availability", " ".join(err["errors"]))
        err = self.request(P1, WASHER, at(-1, 9), at(1, 17), expect=400)
        self.assertIn("past", " ".join(err["errors"]))
        self.request(P1, WASHER, "2026-13-40T10:00", at(1, 17), expect=400)
        self.request(P1, WASHER, at(1, 9), at(2, 17), expect=400, contact="call me")
        self.request("nobody", WASHER, at(1, 9), at(2, 17), expect=404)
        _, rows = self.results()
        self.assertEqual(rows[P1]["requestCount"], 0)  # nothing invalid was stored

    def test_04_request_accept_and_borrower_sees_it(self):
        req = self.request(P1, WASHER, at(1, 9), at(2, 17))["request"]
        self.assertEqual(req["status"], "pending")
        type(self).r1 = req["id"]

        board = self.call("GET", f"/api/board?p={P1}")
        self.assertEqual([r["status"] for r in board["requests"]], ["pending"])
        owner = self.call("GET", "/api/owner/dale")
        self.assertIn(self.r1, [r["id"] for r in owner["requests"]])
        self.assertEqual(self.call("GET", "/api/owner/ruth")["requests"], [])

        res, rows = self.results()
        self.assertEqual(res["counted"], 1)
        self.assertEqual(rows[P1]["state"], "counted")
        self.assertFalse(res["thresholdMet"])

        accept = {"pickupAt": at(1, 10), "returnAt": at(2, 16), "note": "It's behind the barn."}
        self.call("POST", f"/api/requests/{self.r1}/accept", {**accept, "ownerId": "ruth"}, expect=403)
        done = self.call("POST", f"/api/requests/{self.r1}/accept", {**accept, "ownerId": "dale"})["request"]
        self.assertEqual(done["status"], "accepted")

        mine = self.call("GET", f"/api/board?p={P1}")["requests"][0]
        self.assertEqual((mine["status"], mine["confirmedPickup"], mine["confirmedReturn"]),
                         ("accepted", at(1, 10), at(2, 16)))
        self.assertEqual(mine["ownerNote"], "It's behind the barn.")

        other = self.call("GET", f"/api/board?p={P2}")  # another participant sees the reservation...
        washer = next(t for t in other["tools"] if t["id"] == WASHER)
        self.assertEqual(washer["reservations"], [{"start": at(1, 10), "end": at(2, 16), "status": "accepted"}])
        self.assertEqual(other["requests"], [])          # ...but not P1's request details

    def test_05_overlapping_reservations_blocked(self):
        err = self.request(P2, WASHER, at(2, 9), at(3, 9), expect=400)  # overlaps 1d10:00-2d16:00
        self.assertIn("overlap", " ".join(err["errors"]))
        self.request(P2, WASHER, at(2, 16), at(3, 12))  # starts exactly when the other ends: allowed

        # Two pending requests may overlap each other; only one can be accepted.
        a = self.request(P2, WASHER, at(4, 9), at(4, 17))["request"]["id"]
        b = self.request(P3, WASHER, at(4, 12), at(5, 10), borrowerName="Teresa")["request"]["id"]
        self.call("POST", f"/api/requests/{a}/accept", {"ownerId": "dale", "pickupAt": at(4, 9), "returnAt": at(4, 17)})

        pending_b = next(r for r in self.call("GET", "/api/owner/dale")["requests"] if r["id"] == b)
        self.assertEqual(pending_b["conflict"], {"start": at(4, 9), "end": at(4, 17)})
        err = self.call("POST", f"/api/requests/{b}/accept",
                        {"ownerId": "dale", "pickupAt": at(4, 12), "returnAt": at(5, 10)}, expect=400)
        self.assertIn("overlap", " ".join(err["errors"]))
        self.call("POST", f"/api/requests/{b}/accept",
                  {"ownerId": "dale", "pickupAt": at(5, 9), "returnAt": at(5, 17)})  # adjusted times work

        # Cancelling a reservation frees the slot.
        self.call("POST", f"/api/requests/{a}/decline", {"ownerId": "dale", "note": "Sorry"})
        self.request(P2, WASHER, at(4, 10), at(4, 15))

    def test_06_pickup_and_return(self):
        self.call("POST", f"/api/requests/{self.r1}/returned", {"ownerId": "dale"}, expect=409)
        picked = self.call("POST", f"/api/requests/{self.r1}/picked-up", {"ownerId": "dale"})["request"]
        self.assertEqual(picked["status"], "picked_up")
        self.assertIsNotNone(picked["pickedUpAt"])
        returned = self.call("POST", f"/api/requests/{self.r1}/returned", {"ownerId": "dale"})["request"]
        self.assertEqual(returned["status"], "returned")
        self.call("POST", f"/api/requests/{self.r1}/accept",
                  {"ownerId": "dale", "pickupAt": at(1, 10), "returnAt": at(2, 16)}, expect=409)
        washer = next(t for t in self.call("GET", f"/api/board?p={P1}")["tools"] if t["id"] == WASHER)
        self.assertNotIn(at(1, 10), [r["start"] for r in washer["reservations"]])
        mine = self.call("GET", f"/api/board?p={P1}")["requests"][0]
        self.assertEqual(mine["status"], "returned")

    def test_07_add_and_edit_equipment(self):
        new = {"ownerId": "dale", "name": "Chainsaw", "description": "18-inch bar.", "pickupArea": "Dale's shop",
               "availableFrom": at(0, 0)[:10], "availableTo": at(10, 0)[:10], "listed": True}
        tool = self.call("POST", "/api/tools", new)["tool"]
        self.assertIn(tool["id"], [t["id"] for t in self.call("GET", f"/api/board?p={P1}")["tools"]])
        self.call("POST", "/api/tools", {**new, "availableTo": at(-2, 0)[:10]}, expect=400)
        self.call("PUT", f"/api/tools/{tool['id']}", {**new, "ownerId": "ruth"}, expect=403)
        self.call("PUT", f"/api/tools/{tool['id']}", {**new, "listed": False})
        self.assertNotIn(tool["id"], [t["id"] for t in self.call("GET", f"/api/board?p={P1}")["tools"]])
        self.assertIn(tool["id"], [t["id"] for t in self.call("GET", "/api/owner/dale")["tools"]])

        # Editing a sample item's availability fixes its dates, and requests must respect them.
        ladder = {"ownerId": "dale", "name": "24-ft extension ladder", "description": "Aluminum.",
                  "pickupArea": "Miller Creek Rd", "availableFrom": at(1, 0)[:10], "availableTo": at(3, 0)[:10]}
        edited = self.call("PUT", f"/api/tools/{LADDER}", ladder)["tool"]
        self.assertFalse(edited["relativeDates"])
        self.request(P1, LADDER, at(5, 9), at(5, 17), expect=400)

    def test_08_persistence_and_48_hour_window(self):
        before = self.results()[0]["requests"]
        self.stop_server()
        # Simulate two participants who first visited long ago (the app can't wait 48 real hours).
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("INSERT INTO visits VALUES (?, ?, ?, 1)", (P4, utc_hours_ago(50), utc_hours_ago(50)))
            conn.execute("INSERT INTO visits VALUES (?, ?, ?, 1)", (P5, utc_hours_ago(47), utc_hours_ago(47)))
        conn.close()
        self.start_server()

        res, rows = self.results()
        self.assertEqual(res["requests"], before)  # everything survived the restart
        self.assertEqual(rows[P4]["state"], "not_counted")

        self.request(P4, WHEELBARROW, at(1, 9), at(1, 12), borrowerName="Hank")  # 50 h after visit: too late
        self.request(P5, WHEELBARROW, at(2, 9), at(2, 12), borrowerName="Mei")   # 47 h after visit: counts
        res, rows = self.results()
        self.assertEqual(rows[P4]["state"], "not_counted")
        self.assertEqual(rows[P5]["state"], "counted")
        self.assertGreater(rows[P2]["requestCount"], 1)  # several requests...
        self.assertEqual(res["counted"], 4)                # ...but each participant counts once (P1, P2, P3, P5)
        self.assertTrue(res["thresholdMet"])
        self.assertTrue(res["simulated"])

    def test_09_guest_not_counted(self):
        self.request("guest", WHEELBARROW, at(3, 9), at(3, 12), borrowerName="Visitor")
        res, _ = self.results()
        self.assertEqual(res["guestRequests"], 1)
        self.assertEqual(res["counted"], 4)

    def test_10_reset(self):
        self.call("POST", "/api/reset", {})
        res, rows = self.results()
        self.assertEqual((res["counted"], res["requests"]), (0, []))
        self.assertTrue(all(p["state"] == "not_visited" for p in rows.values()))
        tools = self.call("GET", "/api/owner/dale")["tools"]
        self.assertEqual([t["id"] for t in tools], ["t1", "t2", "t3"])  # added chainsaw is gone
        self.assertTrue(all(t["relativeDates"] for t in tools))         # edited ladder restored
        new = self.request(P1, WASHER, at(1, 9), at(2, 17))["request"]
        self.assertEqual(new["id"], 1)  # request numbers restart


if __name__ == "__main__":
    unittest.main(verbosity=2)
