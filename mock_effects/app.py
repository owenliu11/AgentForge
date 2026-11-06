# mock_effects/app.py
"""
A stand-in for a real downstream API (Slack, GitHub, email, ...).

It stores one report per idempotency key and counts every delivery, so the
kill drills can assert each side effect was applied exactly once.
"""

from collections import Counter

from fastapi import FastAPI, Header, HTTPException

app = FastAPI(title="mock_effects")
reports: dict[str, dict] = {}
deliveries: Counter[str] = Counter()
notifications: list[dict] = []


@app.post("/reports")
def post_report(body: dict, idempotency_key: str = Header(...)):
    deliveries[idempotency_key] += 1
    duplicate = idempotency_key in reports
    reports.setdefault(idempotency_key, body)
    return {"id": idempotency_key, "duplicate": duplicate}


@app.get("/reports/{key}")
def get_report(key: str):
    if key not in reports:
        raise HTTPException(status_code=404)
    return reports[key]


@app.post("/notify")
def post_notify(body: dict):
    notifications.append(body)
    return {"count": len(notifications)}


@app.get("/stats")
def stats():
    return {
        "reports_applied": len(reports),
        "report_deliveries": sum(deliveries.values()),
        "duplicates_suppressed": sum(n - 1 for n in deliveries.values()),
        "notifications": len(notifications),
    }


@app.post("/reset")
def reset():
    reports.clear()
    deliveries.clear()
    notifications.clear()
    return {"ok": True}
