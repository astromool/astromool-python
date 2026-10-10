# astromool (Python)

Official client for the [AstroMool API](https://developers.astromool.com/docs): Vedic charts computed from the
Swiss Ephemeris, the 90 astromool.com calculators, and the full PDF reports. Python 3.8+, standard library only.

```python
import os
from astromool import AstroMool, AstroMoolError

am = AstroMool(os.environ["ASTROMOOL_KEY"])     # am_test_... while you build, am_live_... in production

birth = {"date": "1995-08-14", "time": "10:35", "tz_id": "Asia/Kolkata", "lat": 25.5941, "lon": 85.1376}
chart = am.planets(birth)                         # {"data": ..., "meta": ...}
print(chart["data"]["ascendant"]["sign"], chart["meta"]["ayanamsa"])

m = am.calculators.run("manglik-dosha-calculator-by-date-of-birth", birth)
print(m["data"]["summary"])                       # "Manglik - intensity 18/100"
```

Every method returns the API's envelope. `meta` states the ayanamsa, the UTC offset used and the engine version;
read `meta["warnings"]` - it flags things like a birth time that did not exist because of a DST change.

## Methods

| Method | API |
|---|---|
| `planets(birth, ayanamsa=, node=)` | `POST /v1/vedic/planets` |
| `calculators.list()` / `calculators.run(slug, person, partner=, params=, detail=)` | `/v1/calculators` |
| `reports.create(slug, person, partner=, params=, lang=, webhook_url=, idempotency_key=)` | `POST /v1/reports/{slug}` |
| `reports.get(id)` / `reports.wait_until_ready(id, timeout=300)` / `reports.download(url)` | |
| `geo.search(q, limit)` / `geo.timezone(tz_id, date, time)` | `/v1/geo/*` |
| `meta.ayanamsas(date)`, `usage()`, `health()` | |

## Reports

```python
r = am.reports.create("kundli-matching-by-date-of-birth", groom, partner=bride, lang="both")
ready = am.reports.wait_until_ready(r["data"]["report_id"])
open("report.pdf", "wb").write(am.reports.download(ready["data"]["download_url"]))
```

`reports.create` sends an `Idempotency-Key` (generated if you do not pass one), so a retry never creates a second report.

## Errors and retries

Failures raise `AstroMoolError` with `status`, `code` (stable - see the [errors page](https://developers.astromool.com/errors)),
`detail`, `request_id` and `field`. The client retries `rate_limited` (waiting `Retry-After`), 5xx answers and
network errors (`max_retries=2`). `quota_exceeded` is never retried.

## Webhooks

```python
from astromool import verify_webhook

@app.post("/astromool-hook")                       # Flask
def hook():
    event = verify_webhook(request.get_data(), request.headers.get("X-AstroMool-Signature"), os.environ["ASTROMOOL_WEBHOOK_SECRET"])
    return "", 200
```

Use the raw request bytes. The webhook secret is in the dashboard under Keys.

MIT licence.
