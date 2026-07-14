"""
Liquidation Date Auto-Lookup — web app
--------------------------------------
Upload an Excel file that contains CBP entry numbers. For each entry number the
app queries the official CBP liquidation bulletin and adds the liquidation date
to a new column, then lets you download the same file back with the dates filled
in.

Run it with:
    streamlit run app.py
"""

import io
import time

import pandas as pd
import streamlit as st

from cbp_client import CBPClient, CBPClientError

st.set_page_config(page_title="Liquidation Date Lookup", page_icon="⚖️", layout="centered")

st.title("⚖️ Liquidation Date Auto-Lookup")
st.write(
    "Upload an Excel file of CBP **entry numbers**. The app looks up each entry's "
    "official liquidation date on the "
    "[CBP liquidation bulletin](https://trade.cbp.dhs.gov/ace/liquidation/LBNotice/) "
    "and returns the same file with the dates added."
)

STATUS_COLUMN = "Lookup Status"

# Columns appended to the output file, in order. Each is (result-dict key,
# spreadsheet column header). These mirror the columns the CBP bulletin website
# shows, with the single "Liquidation Date" split into one date column per event
# type so extensions / suspensions / re-liquidations are all captured.
OUTPUT_COLUMNS = [
    ("status", STATUS_COLUMN),
    ("event_type", "Event Type"),
    ("liquidation_date", "Liquidation Date"),
    ("reliquidation_date", "Re-liquidation Date"),
    ("extension_date", "Extension Date"),
    ("suspension_date", "Suspension Date"),
    ("posted_date", "Posted Date"),
    ("voided_date", "Voided Date"),
    ("basis", "Basis"),
    ("action", "Action"),
    ("port_of_entry", "Port of Entry"),
    ("entry_date", "Entry Date"),
    ("entry_type", "Entry Type"),
    ("team", "Team"),
    ("filer", "Filer"),
]


def guess_entry_column(columns):
    """Pre-select the column whose name looks most like an entry-number column."""
    for i, col in enumerate(columns):
        name = str(col).strip().lower()
        if "entry" in name or name in {"entry number", "entry_no", "entryno", "entry #"}:
            return i
    return 0


uploaded = st.file_uploader("Upload Excel file (.xlsx or .xls)", type=["xlsx", "xls"])

if uploaded is None:
    st.info("Choose an Excel file to get started.")
    st.stop()

# Read every column as text so entry numbers keep their exact form
# (no scientific notation, no lost leading zeros).
try:
    df = pd.read_excel(uploaded, dtype=str)
except Exception as exc:  # noqa: BLE001 - surface any read error to the user
    st.error(f"Could not read that Excel file: {exc}")
    st.stop()

if df.empty:
    st.warning("That file has no rows.")
    st.stop()

st.subheader("Preview")
st.dataframe(df.head(10), use_container_width=True, hide_index=True)
st.caption(f"{len(df):,} rows loaded.")

st.subheader("Settings")
entry_col = st.selectbox(
    "Which column holds the entry numbers?",
    list(df.columns),
    index=guess_entry_column(df.columns),
)

delay = st.slider(
    "Pause between lookups (seconds)",
    min_value=0.0, max_value=2.0, value=0.4, step=0.1,
    help="A short pause keeps CBP from rate-limiting the requests. Raise it if you "
         "see 'rate limited' errors on large files.",
)

unique_entries = df[entry_col].map(CBPClient.normalize_entry)
n_nonblank = int((unique_entries != "").sum())
n_unique = unique_entries[unique_entries != ""].nunique()
st.caption(
    f"{n_nonblank:,} rows have an entry number "
    f"({n_unique:,} unique — duplicates are only looked up once)."
)

if not st.button("🔎 Look up liquidation dates", type="primary"):
    st.stop()

# --------------------------------------------------------------------- #
# Run the lookups
# --------------------------------------------------------------------- #
try:
    client = CBPClient(request_delay=delay)
except CBPClientError as exc:
    st.error(f"Could not connect to the CBP website: {exc}")
    st.stop()

progress = st.progress(0.0)
status_line = st.empty()

results = []
total = len(df)
start_time = time.time()

for i, raw_value in enumerate(df[entry_col]):
    results.append(client.lookup(raw_value))

    done = i + 1
    progress.progress(done / total)
    elapsed = time.time() - start_time
    rate = done / elapsed if elapsed > 0 else 0
    remaining = (total - done) / rate if rate > 0 else 0
    status_line.text(
        f"Processed {done:,}/{total:,}  •  ~{remaining:0.0f}s remaining"
    )

progress.progress(1.0)

# Append every CBP column (overwriting any same-named column from a prior run).
for key, colname in OUTPUT_COLUMNS:
    df[colname] = [r[key] for r in results]

# --------------------------------------------------------------------- #
# Summary + download
# --------------------------------------------------------------------- #
statuses = [r["status"] for r in results]
n_liquidated = sum(s == "LIQUIDATED" for s in statuses)
n_not_found = sum(s.startswith("NOT FOUND") for s in statuses)
n_not_liq = sum(s.startswith("NOT LIQUIDATED") for s in statuses)
n_errors = sum(s.startswith("ERROR") for s in statuses)
n_empty = sum(s.startswith("EMPTY") for s in statuses)

# How many entries carry each of the "extra" events, so the user can see at a
# glance whether any re-liquidations / extensions / suspensions turned up.
n_reliquidated = sum(bool(r["reliquidation_date"]) for r in results)
n_extended = sum(bool(r["extension_date"]) for r in results)
n_suspended = sum(bool(r["suspension_date"]) for r in results)

status_line.empty()
st.success(f"Done in {time.time() - start_time:0.0f} seconds.")

m1, m2, m3, m4 = st.columns(4)
m1.metric("Liquidated", f"{n_liquidated:,}")
m2.metric("Not liquidated", f"{n_not_liq:,}")
m3.metric("Not found", f"{n_not_found:,}")
m4.metric("Errors", f"{n_errors:,}")

e1, e2, e3 = st.columns(3)
e1.metric("With re-liquidation", f"{n_reliquidated:,}")
e2.metric("With extension", f"{n_extended:,}")
e3.metric("With suspension", f"{n_suspended:,}")

if n_empty:
    st.caption(f"{n_empty:,} rows had no entry number and were skipped.")
if n_errors:
    st.warning(
        f"{n_errors:,} rows returned an error (see the '{STATUS_COLUMN}' column). "
        "You can re-run the file to retry just those."
    )

st.subheader("Results")
st.dataframe(df, use_container_width=True, hide_index=True)

# Write the enriched dataframe back to an .xlsx in memory.
buffer = io.BytesIO()
with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
    df.to_excel(writer, index=False)
buffer.seek(0)

base_name = uploaded.name.rsplit(".", 1)[0]
st.download_button(
    "⬇️ Download Excel with liquidation dates",
    data=buffer.getvalue(),
    file_name=f"{base_name}_with_liquidation_dates.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    type="primary",
)
