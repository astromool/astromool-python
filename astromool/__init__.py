"""AstroMool API client for Python 3.8+. Standard library only.

    from astromool import AstroMool
    am = AstroMool("am_test_...")
    r = am.planets({"date": "1995-08-14", "time": "10:35", "tz_id": "Asia/Kolkata", "lat": 25.5941, "lon": 85.1376})
    print(r["data"]["ascendant"]["sign"], r["meta"]["ayanamsa"])

Every method returns the API's envelope: {"data": ..., "meta": ...}. Errors raise AstroMoolError.
Reference: https://developers.astromool.com/docs
"""
import hashlib
import hmac
import json
import random
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

__version__ = "1.1.0"
__all__ = ["AstroMool", "AstroMoolError", "verify_webhook", "__version__"]
_RETRY_STATUS = {500, 502, 503, 504}


class AstroMoolError(Exception):
    """status: HTTP status (0 = no answer). code: stable code, see https://developers.astromool.com/errors
    (SDK-side codes: network_error, timeout, report_failed, webhook_signature_invalid)."""

    def __init__(self, status, body=None, request_id=None):
        b = body if isinstance(body, dict) else {}
        self.status = status
        self.code = b.get("code") or ("http_%d" % status if status else "network_error")
        self.title = b.get("title")
        self.detail = b.get("detail")
        self.request_id = b.get("request_id") or request_id
        self.field = b.get("field")
        self.errors = b.get("errors")
        self.retry_after = b.get("retry_after")
        self.body = body
        super().__init__("%s (%s): %s" % (self.code, status, self.detail or self.title or ""))


def _clean(d):
    return {k: v for k, v in d.items() if v is not None}


class _Group:
    def __init__(self, client):
        self._c = client


class _Calculators(_Group):
    def list(self):
        return self._c.request("GET", "/v1/calculators")

    def run(self, slug, person, partner=None, params=None, detail=None):
        return self._c.request("POST", "/v1/calculators/" + urllib.parse.quote(slug, safe=""),
                               _clean({"person": person, "partner": partner, "params": params, "detail": detail}))


class _Reports(_Group):
    def create(self, slug, person, partner=None, params=None, lang=None, webhook_url=None, idempotency_key=None):
        """An Idempotency-Key is generated unless given, so a retried call never creates a second report."""
        return self._c.request("POST", "/v1/reports/" + urllib.parse.quote(slug, safe=""),
                               _clean({"person": person, "partner": partner, "params": params, "lang": lang, "webhook_url": webhook_url}),
                               headers={"Idempotency-Key": idempotency_key or str(uuid.uuid4())})

    def get(self, report_id):
        return self._c.request("GET", "/v1/reports/" + urllib.parse.quote(report_id, safe=""))

    def wait_until_ready(self, report_id, timeout=300, interval=2):
        end = time.time() + timeout
        while True:
            r = self.get(report_id)
            st = r["data"]["status"]
            if st == "ready":
                return r
            if st == "expired":
                raise AstroMoolError(410, {"code": "report_gone", "detail": "Report files are kept for 7 days. Create the report again."})
            if st == "failed":
                raise AstroMoolError(422, {"code": "report_failed", "title": "Report failed", "request_id": r["meta"].get("request_id"),
                                           "detail": r["data"].get("error") or "The report could not be generated. It was not charged."})
            if time.time() > end:
                raise AstroMoolError(0, {"code": "timeout", "detail": "Report %s not ready after %d s; it is still building - call reports.get later." % (report_id, timeout)})
            time.sleep(interval)

    def download(self, download_url):
        """The PDF bytes behind a signed download_url (no key is sent)."""
        req = urllib.request.Request(download_url, headers={"User-Agent": "astromool-python/" + __version__})
        try:
            with urllib.request.urlopen(req, timeout=self._c.timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            raise AstroMoolError(e.code, _json(e.read()))


class _Geo(_Group):
    def search(self, q, limit=None):
        return self._c.request("GET", "/v1/geo/search", query=_clean({"q": q, "limit": limit}))

    def timezone(self, tz_id, date, time=None):
        return self._c.request("GET", "/v1/geo/timezone", query=_clean({"tz_id": tz_id, "date": date, "time": time}))


class _Meta(_Group):
    def ayanamsas(self, date=None):
        return self._c.request("GET", "/v1/meta/ayanamsas", query=_clean({"date": date}))


class _Vedic(_Group):
    def chart(self, birth): return self._c.request("POST", "/v1/vedic/chart", birth)
    def houses(self, birth): return self._c.request("POST", "/v1/vedic/houses", birth)
    def ashtakavarga(self, birth): return self._c.request("POST", "/v1/vedic/ashtakavarga", birth)

    def divisional(self, birth, chart=None):
        """All 16 vargas, or one: divisional(birth, "D9")."""
        return self._c.request("POST", "/v1/vedic/divisional" + ("/" + urllib.parse.quote(chart, safe="") if chart else ""), birth)

    def chart_image(self, birth, style="north", chart="D1", format="svg", theme="light", size=480):
        """The chart picture: str (svg) or bytes (png)."""
        raw = self._c._binary("/v1/chart/image", dict(birth, style=style, chart=chart, format=format, theme=theme, size=size))
        return raw.decode("utf-8") if format == "svg" else raw


class _Dasha(_Group):
    def vimshottari(self, birth): return self._c.request("POST", "/v1/dasha/vimshottari", birth)
    def yogini(self, birth): return self._c.request("POST", "/v1/dasha/yogini", birth)


class _Panchang(_Group):
    """place = {"lat", "lon", "tz_id"} or {"place": "Patna"}; date YYYY-MM-DD (default: today there)."""
    def day(self, place, date=None): return self._c.request("POST", "/v1/panchang", dict(place, **_clean({"date": date})))
    def rahu_kaal(self, place, date=None): return self._c.request("POST", "/v1/panchang/rahu-kaal", dict(place, **_clean({"date": date})))
    def choghadiya(self, place, date=None): return self._c.request("POST", "/v1/panchang/choghadiya", dict(place, **_clean({"date": date})))
    def hora(self, place, date=None): return self._c.request("POST", "/v1/panchang/hora", dict(place, **_clean({"date": date})))


class _Match(_Group):
    def ashtakoot(self, person, partner): return self._c.request("POST", "/v1/match/ashtakoot", {"person": person, "partner": partner})


class _Numerology(_Group):
    def core(self, name, date, as_of=None): return self._c.request("POST", "/v1/numerology", _clean({"name": name, "date": date, "as_of": as_of}))


class _Dosha(_Group):
    def check(self, name, birth): return self._c.request("POST", "/v1/dosha/" + urllib.parse.quote(name, safe=""), birth)
    def summary(self, birth): return self._c.request("POST", "/v1/dosha/summary", birth)


class _Remedies(_Group):
    def get(self, name, birth): return self._c.request("POST", "/v1/remedies/" + urllib.parse.quote(name, safe=""), birth)


class _Transit(_Group):
    def positions(self, **moment): return self._c.request("POST", "/v1/transit/positions", moment)
    def natal(self, birth, on_date=None): return self._c.request("POST", "/v1/transit/natal", dict(birth, **_clean({"on_date": on_date})))


class _KP(_Group):
    def chart(self, birth): return self._c.request("POST", "/v1/kp/chart", birth)
    def ruling_planets(self, **moment): return self._c.request("POST", "/v1/kp/ruling-planets", moment)
    def sub_table(self): return self._c.request("GET", "/v1/kp/sub-table")


class _Western(_Group):
    def natal(self, birth): return self._c.request("POST", "/v1/western/natal", birth)
    def synastry(self, person, partner): return self._c.request("POST", "/v1/western/synastry", {"person": person, "partner": partner})
    def progressions(self, birth, on_date=None): return self._c.request("POST", "/v1/western/progressions", dict(birth, **_clean({"on_date": on_date})))
    def composite(self, person, partner): return self._c.request("POST", "/v1/western/composite", {"person": person, "partner": partner})


def _json(raw):
    try:
        return json.loads(raw.decode("utf-8")) if raw else None
    except ValueError:
        return {"detail": raw[:300].decode("utf-8", "replace")}


class AstroMool:
    def __init__(self, api_key, base_url="https://api.astromool.com", timeout=30, max_retries=2):
        """api_key: am_test_... / am_live_... (keep server-side). timeout in seconds per request.
        max_retries: for rate_limited (waits Retry-After), 5xx and network errors."""
        if not api_key or not re.match(r"^am_(live|test|pub)_", api_key):
            raise ValueError("AstroMool: pass your API key (am_test_..., am_live_... or am_pub_...)")
        self.api_key, self.base_url, self.timeout, self.max_retries = api_key, base_url.rstrip("/"), timeout, max_retries
        self.calculators, self.reports, self.geo, self.meta = _Calculators(self), _Reports(self), _Geo(self), _Meta(self)
        self.vedic, self.dasha, self.panchang, self.match = _Vedic(self), _Dasha(self), _Panchang(self), _Match(self)
        self.numerology, self.dosha, self.remedies, self.transit = _Numerology(self), _Dosha(self), _Remedies(self), _Transit(self)
        self.kp, self.western = _KP(self), _Western(self)

    def planets(self, birth, ayanamsa=None, node=None):
        """Ascendant and the nine grahas. birth = {date, time, tz_id or utc_offset, lat, lon}."""
        return self.request("POST", "/v1/vedic/planets", dict(birth, **_clean({"ayanamsa": ayanamsa, "node": node})))

    def usage(self):
        return self.request("GET", "/v1/usage")

    def health(self):
        return self.request("GET", "/v1/health")

    def batch(self, requests):
        """Up to 20 calls in one request: [{"method"?, "path", "body"?}]; each is metered as if called alone."""
        return self.request("POST", "/v1/batch", {"requests": requests})

    def topic(self, path, body):
        """Any topic endpoint, e.g. topic("/v1/muhurat/vivah", {"person": {"place": "Delhi"}, "params": {"year": 2027}})."""
        return self.request("POST", path, body)

    def _binary(self, path, body):
        req = urllib.request.Request(self.base_url + path, data=json.dumps(body).encode("utf-8"), method="POST",
                                     headers={"X-API-Key": self.api_key, "Content-Type": "application/json", "User-Agent": "astromool-python/" + __version__})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            raise AstroMoolError(e.code, _json(e.read()), e.headers.get("X-Request-Id"))

    def request(self, method, path, body=None, headers=None, query=None):
        url = self.base_url + path + ("?" + urllib.parse.urlencode(query) if query else "")
        h = {"X-API-Key": self.api_key, "Accept": "application/json", "User-Agent": "astromool-python/" + __version__}
        h.update(headers or {})
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8"); h["Content-Type"] = "application/json"
        attempt = 0
        while True:
            req = urllib.request.Request(url, data=data, method=method, headers=h)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    return _json(r.read())
            except urllib.error.HTTPError as e:
                err = AstroMoolError(e.code, _json(e.read()), e.headers.get("X-Request-Id"))
                retry = (e.code == 429 and err.code == "rate_limited") or e.code in _RETRY_STATUS
                if not retry or attempt >= self.max_retries:
                    raise err
                ra = e.headers.get("Retry-After") or err.retry_after
                time.sleep(min(60, int(ra or 1)) if e.code == 429 else 0.5 * 2 ** attempt + random.random() * 0.1)
            except (urllib.error.URLError, OSError) as e:
                if attempt >= self.max_retries:
                    raise AstroMoolError(0, {"code": "network_error", "detail": str(e)})
                time.sleep(0.5 * 2 ** attempt)
            attempt += 1


def verify_webhook(raw_body, signature_header, secret, tolerance_seconds=300):
    """Verify a report webhook. raw_body: the exact bytes received. signature_header: X-AstroMool-Signature.
    secret: your webhook secret (dashboard > Keys). Returns the parsed event; raises AstroMoolError if not genuine."""
    def fail(d):
        raise AstroMoolError(400, {"code": "webhook_signature_invalid", "detail": d})
    parts = dict(p.strip().split("=", 1) for p in (signature_header or "").split(",") if "=" in p)
    if "t" not in parts or "v1" not in parts:
        fail("X-AstroMool-Signature header missing or malformed.")
    try:
        ts = int(parts["t"])
    except ValueError:
        fail("Signature timestamp is not a number.")
    if abs(time.time() - ts) > tolerance_seconds:
        fail("Webhook timestamp is outside the tolerance window.")
    raw = raw_body if isinstance(raw_body, bytes) else str(raw_body).encode("utf-8")
    good = hmac.new(secret.encode("utf-8"), parts["t"].encode() + b"." + raw, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(good, parts["v1"]):
        fail("Signature does not match.")
    return json.loads(raw.decode("utf-8"))
