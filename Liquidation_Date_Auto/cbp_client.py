"""
cbp_client.py
-------------
Talks to the CBP ACE "Official Notice of Extension, Suspension and Liquidation"
bulletin (a.k.a. the Liquidation Bulletin Notice / LBNotice).

The public web page at
    https://trade.cbp.dhs.gov/ace/liquidation/LBNotice/
is a single-page app that POSTs to a JSON endpoint (`.../search`). This module
reproduces that call directly with `requests`, so we can look up an entry number
and read back its liquidation date without driving a browser.

Auth flow the site uses (and that we replicate):
  1. GET the page once -> receive session cookies (JSESSIONID, XSRF-TOKEN, ...)
     and a CSRF token embedded in a <meta name="_csrf"> tag.
  2. POST to `search` with that token in the `X-XSRF-TOKEN` header and the
     cookies attached (requests.Session handles cookies automatically).
"""

import re
import time
import requests

BASE_URL = "https://trade.cbp.dhs.gov/ace/liquidation/LBNotice/"
SEARCH_URL = BASE_URL + "search"

# CBP bulletin event codes (from the site's own "Event" filter dropdown):
#   L = Liquidated, R = Re-liquidated, E = Extended, S = Suspended
# We record the most recent date seen for each of these as its own column, so an
# entry that has, say, an Extension *and* a Liquidation *and* a Re-liquidation
# keeps all three dates.
_EVENT_DATE_KEYS = {
    "L": "liquidation_date",
    "R": "reliquidation_date",
    "E": "extension_date",
    "S": "suspension_date",
}

# Codes that mean the entry has actually (re)liquidated.
_LIQUIDATION_CODES = {"L", "R"}


class CBPClientError(Exception):
    """Raised when we cannot establish a working session with the CBP site."""


class CBPClient:
    """A thin, session-aware client for the CBP liquidation bulletin search."""

    def __init__(self, request_delay=0.4, timeout=40, max_retries=3):
        """
        request_delay : seconds to pause after each *network* lookup (be polite;
                        the API returns HTTP 429 if hit too fast).
        timeout       : per-request timeout in seconds.
        max_retries   : attempts per lookup for transient errors (429 / 403 / 5xx).
        """
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126.0 Safari/537.36"
            ),
            "Accept": "application/json, text/plain, */*",
        })
        self.request_delay = request_delay
        self.timeout = timeout
        self.max_retries = max_retries

        self.csrf_token = None
        self.csrf_header = None
        self._cache = {}  # normalized entry number -> result dict

        self._init_session()

    # ------------------------------------------------------------------ #
    # Session / CSRF handling
    # ------------------------------------------------------------------ #
    def _init_session(self):
        """Fetch the landing page to obtain session cookies + CSRF token."""
        try:
            resp = self.session.get(BASE_URL, timeout=self.timeout)
            resp.raise_for_status()
        except requests.RequestException as exc:
            raise CBPClientError(
                f"Could not reach the CBP liquidation site: {exc}"
            ) from exc

        html = resp.text
        token = re.search(r'name="_csrf"\s+content="([^"]+)"', html)
        header = re.search(r'name="_csrf_header"\s+content="([^"]+)"', html)
        if not token or not header:
            raise CBPClientError(
                "Connected to the CBP site but could not find the CSRF token. "
                "The website layout may have changed."
            )
        self.csrf_token = token.group(1)
        self.csrf_header = header.group(1)  # normally 'X-XSRF-TOKEN'

    # ------------------------------------------------------------------ #
    # Entry-number normalization
    # ------------------------------------------------------------------ #
    @staticmethod
    def normalize_entry(value):
        """
        Normalize an entry number the same way the CBP site does:
        uppercase, strip dashes/spaces. Also defends against Excel turning a
        numeric entry number into a float (e.g. '30107329306.0').
        Returns '' for blank / NaN input.
        """
        if value is None:
            return ""
        s = str(value).strip()
        if s == "" or s.lower() == "nan":
            return ""
        s = s.upper()
        # Excel-numeric artifact: trailing '.0'
        s = re.sub(r"\.0+$", "", s)
        # Keep only letters and digits (drops dashes, spaces, etc.)
        s = re.sub(r"[^A-Z0-9]", "", s)
        return s

    # ------------------------------------------------------------------ #
    # Core lookup
    # ------------------------------------------------------------------ #
    def lookup(self, entry_number):
        """
        Look up a single entry number.

        Returns a dict with one key per column the CBP bulletin website shows,
        plus a human-readable status. Every value is a plain string ('' when not
        applicable):

          {
            "entry_number"      : normalized entry number,
            "status"            : 'LIQUIDATED' / 'NOT LIQUIDATED (...)' /
                                  'NOT FOUND (...)' / 'EMPTY (...)' / 'ERROR: ...',
            "event_type"        : the operative event, e.g. 'Liquidated',
            "liquidation_date"  : date of the 'Liquidated' event    (MM/DD/YYYY),
            "reliquidation_date": date of the 'Re-liquidated' event,
            "extension_date"    : date of the 'Extended' event,
            "suspension_date"   : date of the 'Suspended' event,
            "posted_date"       : bulletin posted date of the operative event,
            "voided_date"       : voided date of the operative event,
            "basis"             : basis of the operative event,
            "action"            : action of the operative event,
            "port_of_entry"     : port of entry,
            "entry_date"        : entry date,
            "entry_type"        : entry type code,
            "team"              : CBP team,
            "filer"             : filer code,
          }
        """
        entry = self.normalize_entry(entry_number)
        if not entry:
            return self._blank_result("", "EMPTY (no entry number in this row)")

        if entry in self._cache:
            return self._cache[entry]

        result = self._do_lookup(entry)
        self._cache[entry] = result
        if self.request_delay:
            time.sleep(self.request_delay)
        return result

    def _do_lookup(self, entry):
        payload = {
            "dtPageVars": {"draw": 1, "start": 0, "length": 25},
            "searchFields": {"entryNumber": entry},
        }

        last_error = None
        for attempt in range(self.max_retries):
            headers = {
                self.csrf_header: self.csrf_token,
                "Content-Type": "application/json; charset=UTF-8",
            }
            try:
                resp = self.session.post(
                    SEARCH_URL, json=payload, headers=headers, timeout=self.timeout
                )
            except requests.RequestException as exc:
                last_error = f"network error ({exc})"
                time.sleep(1.5 * (attempt + 1))
                continue

            if resp.status_code == 403:
                # CSRF token / session likely expired -> refresh and retry.
                try:
                    self._init_session()
                except CBPClientError as exc:
                    last_error = str(exc)
                    break
                continue

            if resp.status_code == 429:
                # Rate limited -> exponential backoff.
                last_error = "rate limited (HTTP 429)"
                time.sleep(2 ** (attempt + 1))
                continue

            if resp.status_code >= 500:
                last_error = f"CBP server error (HTTP {resp.status_code})"
                time.sleep(1.5 * (attempt + 1))
                continue

            # Any other 4xx: not worth retrying.
            if resp.status_code >= 400:
                return self._error_result(
                    entry, f"CBP rejected the request (HTTP {resp.status_code})"
                )

            # 200 OK
            try:
                data = resp.json()
            except ValueError:
                last_error = "CBP returned an unreadable response"
                continue
            return self._parse_response(entry, data)

        return self._error_result(entry, last_error or "unknown error")

    # ------------------------------------------------------------------ #
    # Response parsing
    # ------------------------------------------------------------------ #
    def _parse_response(self, entry, data):
        if data.get("status") == "ERROR":
            msgs = data.get("errorMessages") or []
            text = "; ".join(m.get("message", "") for m in msgs) or "unknown error"
            return self._error_result(entry, text)

        records = (data.get("data") or {}).get("data") or []
        if not records:
            return self._blank_result(
                entry, "NOT FOUND (no bulletin notice for this entry)"
            )

        result = self._blank_result(entry, "")

        # One date column per event type: keep the most recent date for each.
        for code, key in _EVENT_DATE_KEYS.items():
            matches = [r for r in records if str(r.get("eventCode", "")).upper() == code]
            if matches:
                latest = max(matches, key=lambda r: r.get("eventDate") or "")
                result[key] = _format_date(latest.get("eventDate"))

        # Entry-level fields are identical across an entry's events; read them
        # from the most recent record.
        newest = max(records, key=lambda r: r.get("eventDate") or "")
        result["port_of_entry"] = _clean(newest.get("portOfEntry"))
        result["entry_date"] = _format_date(newest.get("entryDate"))
        result["entry_type"] = _clean(newest.get("entryType"))
        result["team"] = _clean(newest.get("teamNumber"))
        result["filer"] = _clean(newest.get("filer"))

        # The "operative" event drives the descriptive columns (posted/voided
        # date, basis, action). Prefer the most recent (Re-)liquidation; if the
        # entry hasn't liquidated, fall back to the most recent event of any kind.
        liquidations = [
            r for r in records
            if str(r.get("eventCode", "")).upper() in _LIQUIDATION_CODES
        ]
        if liquidations:
            primary = max(liquidations, key=lambda r: r.get("eventDate") or "")
            result["status"] = "LIQUIDATED"
        else:
            primary = newest
            events = ", ".join(
                sorted({str(r.get("event", "")).strip() for r in records if r.get("event")})
            )
            result["status"] = f"NOT LIQUIDATED (on file: {events or 'unknown event'})"

        result["event_type"] = _clean(primary.get("event"))
        result["posted_date"] = _format_date(primary.get("postedDate"))
        result["voided_date"] = _format_date(primary.get("voidedDate"))
        result["basis"] = _clean(primary.get("basis"))
        result["action"] = _clean(primary.get("action"))
        return result

    @staticmethod
    def _blank_result(entry, status):
        """An all-columns result dict with everything blank but entry + status."""
        return {
            "entry_number": entry,
            "status": status,
            "event_type": "",
            "liquidation_date": "",
            "reliquidation_date": "",
            "extension_date": "",
            "suspension_date": "",
            "posted_date": "",
            "voided_date": "",
            "basis": "",
            "action": "",
            "port_of_entry": "",
            "entry_date": "",
            "entry_type": "",
            "team": "",
            "filer": "",
        }

    def _error_result(self, entry, message):
        return self._blank_result(entry, f"ERROR: {message}")


# ---------------------------------------------------------------------- #
# Helpers
# ---------------------------------------------------------------------- #
def _format_date(iso_value):
    """
    CBP returns dates like '2026-07-10T04:00:00.000+00:00'. We only want the
    calendar date, in US format -> '07/10/2026'.
    """
    if not iso_value:
        return ""
    day = str(iso_value).split("T", 1)[0]
    try:
        year, month, dom = day.split("-")
    except ValueError:
        return day  # unexpected shape: pass it through rather than lose it
    return f"{month}/{dom}/{year}"


def _clean(value):
    """Turn a possibly-None API value into a trimmed string ('' for None/NaN)."""
    if value is None:
        return ""
    s = str(value).strip()
    return "" if s.lower() == "nan" else s


# ---------------------------------------------------------------------- #
# Manual smoke test:  python cbp_client.py E8604347391
# ---------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys

    test_entries = sys.argv[1:] or ["E8604347391"]
    client = CBPClient()
    for e in test_entries:
        print(f"{e!r:>16} -> {client.lookup(e)}")
