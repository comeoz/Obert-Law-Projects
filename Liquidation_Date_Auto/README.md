# Liquidation Date Auto-Lookup

A small web app that takes an Excel file of CBP **entry numbers**, looks up each
one on the CBP liquidation bulletin, and returns the same file with every column
the bulletin website shows added — liquidation date, plus any re-liquidation,
extension, or suspension, and the entry's port, dates, basis, action, and team.

It talks directly to the CBP bulletin's JSON search API (the same one the
official site uses), so there is no slow, fragile browser automation.

- **Source site:** <https://trade.cbp.dhs.gov/ace/liquidation/LBNotice/>

## What it does

1. You upload an `.xlsx` / `.xls` file.
2. You pick which column contains the entry numbers.
3. For each entry number it queries CBP and reads back every bulletin event.
4. You download the same spreadsheet, one row per input row, with these columns
   appended (mirroring the CBP bulletin's own table):
   - **Lookup Status** — `LIQUIDATED`, `NOT LIQUIDATED (on file: …)`,
     `NOT FOUND (no bulletin notice for this entry)`, `EMPTY`, or an error.
   - **Event Type** — the operative event (e.g. `Liquidated`, `Re-liquidated`).
   - **Liquidation Date**, **Re-liquidation Date**, **Extension Date**,
     **Suspension Date** — one date column per event type (`MM/DD/YYYY`), so an
     entry that was extended, liquidated *and* re-liquidated keeps all its dates.
   - **Posted Date**, **Voided Date**, **Basis**, **Action** — details of the
     operative (most recent liquidation) event.
   - **Port of Entry**, **Entry Date**, **Entry Type**, **Team**, **Filer** —
     entry-level details.

   Blank date/detail columns mean that event type isn't on file for the entry.

Entry numbers can be written with or without dashes (`E860-4347391` or
`E8604347391`) — both work. Duplicate entry numbers are only looked up once.
Re-running an already-processed file just refreshes these columns in place.

## Setup (first time only)

Requires Python 3.10+.

```bash
cd "Liquidation_Date_Auto"
pip install -r requirements.txt
```

## Run

```bash
streamlit run app.py
```

Your browser opens at <http://localhost:8501>. Upload a file, click
**Look up liquidation dates**, then **Download**.

## Files

| File             | Purpose                                                        |
| ---------------- | ------------------------------------------------------------- |
| `app.py`         | The Streamlit web interface (upload → process → download).    |
| `cbp_client.py`  | The CBP API client (session, CSRF handling, lookup + parsing).|
| `requirements.txt` | Python dependencies.                                        |

## Notes & limitations

- The CBP bulletin keeps postings online for **15 months**. Entries liquidated
  before that window will come back as `NOT FOUND`.
- CBP rate-limits rapid requests. The app pauses briefly between lookups; if you
  process a very large file and see `rate limited` in the status column, raise
  the **Pause between lookups** slider and re-run.
- `cbp_client.py` can also be run on its own to spot-check a number:
  ```bash
  python cbp_client.py E8604347391
  ```

## How the CBP lookup works (for future maintenance)

The public page is a single-page app that `POST`s to `.../LBNotice/search`.
Each request needs:

- session cookies from first loading the page, and
- a CSRF token read from the page's `<meta name="_csrf">` tag, sent back in the
  `X-XSRF-TOKEN` header.

The JSON response lists every bulletin event for the entry (Extension,
Suspension, Liquidation, ...). We keep the event whose type is **Liquidated**
(`eventCode` `L`) and read its `eventDate`. If more than one liquidation event
exists, the most recent one is used. If the CSRF token expires mid-run, the
client automatically reloads the page to get a fresh one.
