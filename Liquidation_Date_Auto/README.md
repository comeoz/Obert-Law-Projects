# Liquidation Date Auto-Lookup

A small web app that takes an Excel file of CBP **entry numbers**, looks up each
one's official **liquidation date** on the CBP liquidation bulletin, and returns
the same file with the dates added in a new column.

It talks directly to the CBP bulletin's JSON search API (the same one the
official site uses), so there is no slow, fragile browser automation.

- **Source site:** <https://trade.cbp.dhs.gov/ace/liquidation/LBNotice/>

## What it does

1. You upload an `.xlsx` / `.xls` file.
2. You pick which column contains the entry numbers.
3. For each entry number it queries CBP and finds the **Liquidated** event date.
4. You download the same spreadsheet back with two new columns:
   - **Liquidation Date** — `YYYY-MM-DD`, blank if the entry is not liquidated.
   - **Lookup Status** — why a date is present or blank, e.g.
     `LIQUIDATED`, `NOT LIQUIDATED (on file: Extension)`,
     `NOT FOUND (no bulletin notice for this entry)`, or an error message.

Entry numbers can be written with or without dashes (`E860-4347391` or
`E8604347391`) — both work. Duplicate entry numbers are only looked up once.

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
