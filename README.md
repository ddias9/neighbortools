# NeighborTools: classroom demo

A small web app where rural homeowners can find equipment that nearby neighbors
are willing to lend, request it, and get a confirmed pickup time without calling
around.

> **Classroom demo.** All equipment, owners, and participants are fictional
> sample data. Anything you click here is simulated activity. It is **not**
> evidence that the research question has been validated.

## Start it

You need Python 3.9 or newer. There is nothing else to install.

```bash
python app.py
```

Then open **http://localhost:8000** in your browser. On a Mac, type `python3`
instead of `python`. To stop the server, press `Ctrl+C` in the terminal.

Everything is saved in `neighbortools.db`, which is created next to `app.py` the
first time the app runs. Data survives page refreshes and server restarts. To
start over, use **Reset demo** on the Results page.

## Use a second device (phone or your partner's laptop)

Start the server in LAN mode:

```bash
python app.py --lan
```

The terminal prints a line like `On other devices: http://192.168.1.23:8000`.
Open that address on any device connected to the **same Wi-Fi network**. In LAN
mode, the Results page also shows participant links using that address, so you
can send them straight to the other device.

- **Windows** may ask whether Python can use the network. Choose **Allow** for
  private networks.
- **Campus and public Wi-Fi** often block connections between devices. If the
  other device can't connect, put both devices on a phone hotspot or a home
  network.
- LAN mode has no passwords, so anyone on that network who has the address can
  use the app. Only use it on a network you trust, and stop the server when you
  finish.

## Pages and links

| Page | Link |
|---|---|
| Home and demo menu | `/` |
| Borrower view, Participants 1 to 5 | `/borrow?p=k7m2q`, `r4t8w`, `h3n6z`, `b9x5c`, `f2v7j` |
| Borrower view, guest (not counted) | `/borrow?p=guest` |
| Owner view (Dale, Ruth, Marcus) | `/owner?o=dale`, `ruth`, `marcus` |
| Results and reset | `/results` |

The green menu at the top of every page links to all of these.

## Demo walkthrough with your partner (about 5 minutes)

**Roles:** you play Dale, an owner, on the laptop running the server. Your
partner plays Claire, Participant 1, on a phone or a second browser window.

1. **Setup.** Run `python app.py --lan`. Open **Results** and click
   **Reset demo**.
2. **First visit.** Your partner opens the Participant 1 link (copy it from the
   Results table). On Results, Participant 1 now shows **Window open**: the
   first visit was recorded automatically.
3. **Request.** Your partner clicks **Request this item** on the *Gas pressure
   washer*. They fill in a name, contact, job, and times for Saturday 9 AM to
   Sunday 5 PM. To show validation, first set the return time before the
   pickup time and submit. The request then appears as **Pending**.
4. **Accept.** You open **Owner: Dale**. Under *Needs a decision*, click
   **Accept…**, change pickup to 10 AM, add a note ("It's in the shed by the
   driveway"), and click **Accept and reserve**.
5. **Borrower sees it.** Within about 5 seconds, your partner's screen changes
   to **Accepted**, showing the confirmed times and your note. No calls or
   texts were needed.
6. **No double-booking.** Open **P2** or **Guest** and request the pressure
   washer for Saturday afternoon. The app blocks it and shows the reserved
   time.
7. **Pickup and return.** As Dale, click **Mark picked up**, then
   **Mark returned**. Your partner sees each status change.
8. **Results.** The page shows *1 of 5 participants counted*, with a progress
   bar and the 3-of-5 threshold marked. Point out the simulated-activity label.
9. **Persistence (optional).** Stop the server with `Ctrl+C`, start it again,
   and refresh. Everything is still there.
10. **Finish.** Click **Reset demo**.

## How the results are counted

- When a participant link is opened for the first time, the visit time is saved.
  This starts that participant's 48-hour window.
- A participant counts **once** if they submit a complete request within 48
  hours of that first visit. Every field must be valid, or the server rejects
  the request and stores nothing. The owner's decision afterwards doesn't
  matter.
- The threshold is met when at least 3 of the 5 participants count.
- Guest requests are never counted.
- The app cannot tell whether a request is *genuine*. That judgment belongs to
  the researcher.

## Files

| File | What it does |
|---|---|
| `app.py` | Web server: JSON API, validation, overlap checks, results counting, and SQLite storage |
| `sample_data.py` | Fictional owners, participants, and equipment. Availability is set as days from today, so the demo never expires |
| `static/index.html`, `static/style.css`, `static/app.js` | The browser side, covering every view |
| `test_app.py` | Automated end-to-end checks |

## Run the automated checks

```bash
python -m unittest -v test_app
```

This starts a separate copy of the server with a throwaway database, so your
demo data is not touched. It checks the sample data, visit tracking, form and
date validation, accept and decline, overlap blocking, pickup and return,
adding and editing equipment, persistence after a restart, the 48-hour rule,
guest requests, and reset.

## Limitations and what's left out

- **Left out on purpose:** payments, ratings, recommendations, maps, delivery,
  chat, email and SMS notifications, and accounts or logins.
- **No real security.** Links are not secret. Anyone with an owner link can act
  as that owner. That is fine for a classroom demo but not for real
  participants.
- **Times use the server computer's clock.** Pickup and return times are
  treated as local time, which assumes everyone is in the same time zone.
- **Borrowers can't edit or cancel a request** once it's sent. Owners can
  decline a request or cancel a reservation. Equipment can be hidden from the
  board but not deleted.
- **Updates aren't instant.** Pages refresh every 5 seconds.
- **Testing:** the app was checked on one Windows computer using two browser
  tabs and a separate command-line client. It was **not** tested on a real
  second device over Wi-Fi, or in Safari or on an iPhone.
