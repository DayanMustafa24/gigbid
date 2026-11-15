"""Phase 6 API integration tests: real app, real bid graph, temp SQLite, offline fakes (see tests/conftest.py).
Covers auth, tenant isolation, the run lifecycle with the human gate (exactly-once decision), replayable SSE, retry guards,
the degraded-marketplace path, the uniform error shape and edge headers."""
from __future__ import annotations

from tests.conftest import INLINE_JOB, PASSWORD, FakeMarket, market_job


async def signed_in(h, email="owner@example.com", password=PASSWORD):
    cl = h.client()
    r = await cl.post("/auth/register", json={"email": email, "password": password})
    assert r.status_code == 201, r.text
    return cl


async def start_run(h, cl, **kw):
    body = {"kind": "bid", "persona": "ai_ml", "job": INLINE_JOB, **kw}
    r = await cl.post("/runs", json=body)
    assert r.status_code in (200, 202), r.text
    await h.svc.drain()
    return r.json()["id"]


# ---- auth ----------------------------------------------------------------------------------------------------------------------------
async def test_unauthenticated_requests_get_uniform_401(api_harness):
    h = await api_harness()
    r = await h.client().get("/runs")
    assert r.status_code == 401 and r.json()["error"]["code"] == "unauthenticated"


async def test_register_login_logout_and_cookie_flags(api_harness):
    h = await api_harness()
    cl = h.client()
    r = await cl.post("/auth/register", json={"email": "A@Example.com", "password": PASSWORD})
    assert r.status_code == 201 and r.json()["email"] == "a@example.com"
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie
    assert "token" not in r.json() and (await cl.get("/auth/me")).json()["email"] == "a@example.com"
    assert (await cl.post("/auth/logout")).status_code == 204
    cl.cookies.clear()
    assert (await cl.get("/auth/me")).status_code == 401
    bad = await cl.post("/auth/login", json={"email": "a@example.com", "password": "wrong-password"})
    assert bad.status_code == 401 and bad.json()["error"]["code"] == "invalid_credentials"
    ok = await cl.post("/auth/login", json={"email": "a@example.com", "password": PASSWORD})
    assert ok.status_code == 200


async def test_registration_closed_after_first_account(api_harness):
    h = await api_harness()
    await signed_in(h)
    r = await h.client().post("/auth/register", json={"email": "b@example.com", "password": PASSWORD})
    assert r.status_code == 403 and r.json()["error"]["code"] == "registration_closed"


async def test_weak_password_rejected_with_uniform_shape(api_harness):
    h = await api_harness()
    r = await h.client().post("/auth/register", json={"email": "a@example.com", "password": "short"})
    assert r.status_code == 422 and "error" in r.json()


# ---- run lifecycle -------------------------------------------------------------------------------------------------------------------
async def test_run_pauses_at_gate_then_approval_completes_exactly_once(api_harness):
    h = await api_harness()
    cl = await signed_in(h)
    run_id = await start_run(h, cl)
    run = (await cl.get(f"/runs/{run_id}")).json()
    assert run["status"] == "awaiting_approval" and "approve" in run["pending"]["actions"]
    assert run["pending"]["draft"]["cover_letter"]

    dup = await cl.post("/runs", json={"kind": "bid", "persona": "ai_ml", "job": INLINE_JOB})
    assert dup.status_code == 200 and dup.json()["id"] == run_id  # same job, active run: returned, not duplicated

    first = await cl.post(f"/runs/{run_id}/decision", json={"action": "approve"})
    assert first.status_code == 202
    await h.svc.drain()
    assert (await cl.get(f"/runs/{run_id}")).json()["status"] == "completed"
    again = await cl.post(f"/runs/{run_id}/decision", json={"action": "approve"})
    assert again.status_code == 409 and again.json()["error"]["code"] == "not_awaiting_approval"


async def test_reject_closes_run_and_unlisted_action_is_refused(api_harness):
    h = await api_harness()
    cl = await signed_in(h)
    run_id = await start_run(h, cl)
    bad = await cl.post(f"/runs/{run_id}/decision", json={"action": "dismiss"})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "action_not_allowed"
    assert (await cl.post(f"/runs/{run_id}/decision", json={"action": "reject", "reason": "no"})).status_code == 202
    await h.svc.drain()
    assert (await cl.get(f"/runs/{run_id}")).json()["status"] == "closed"


async def test_edit_requires_text(api_harness):
    h = await api_harness()
    cl = await signed_in(h)
    run_id = await start_run(h, cl)
    assert (await cl.post(f"/runs/{run_id}/decision", json={"action": "edit"})).status_code == 422


async def test_trace_and_sse_replay_end_with_done(api_harness):
    h = await api_harness()
    cl = await signed_in(h)
    run_id = await start_run(h, cl)
    await cl.post(f"/runs/{run_id}/decision", json={"action": "reject"})
    await h.svc.drain()
    events = (await cl.get(f"/runs/{run_id}/trace")).json()
    assert events[0]["node"] == "run_started" and [e["seq"] for e in events] == sorted(e["seq"] for e in events)
    assert {"analyst", "risk_assessor", "fit_matcher"} <= {e["node"] for e in events}
    sse = await cl.get(f"/runs/{run_id}/events")
    assert sse.status_code == 200 and "text/event-stream" in sse.headers["content-type"]
    assert "event: done" in sse.text and sse.text.count("event: node") == len(events)
    last = events[len(events) // 2]["seq"]
    resumed = await cl.get(f"/runs/{run_id}/events", headers={"Last-Event-ID": str(last)})
    ids = [int(line.split(":", 1)[1]) for line in resumed.text.splitlines() if line.startswith("id:")]
    assert ids and min(ids) > last  # reconnect replays only what was missed


async def test_retry_refused_for_a_run_that_is_not_failed(api_harness):
    h = await api_harness()
    cl = await signed_in(h)
    run_id = await start_run(h, cl)
    r = await cl.post(f"/runs/{run_id}/retry")
    assert r.status_code == 409 and r.json()["error"]["code"] == "not_retryable"


async def test_stale_running_run_is_recovered_as_failed(api_harness):
    from datetime import timedelta

    from sqlalchemy import update

    from apps.api.db import Run, utcnow

    h = await api_harness()
    cl = await signed_in(h)
    run_id = await start_run(h, cl)
    async with h.c.sessions() as db:
        await db.execute(update(Run).where(Run.id == run_id).values(status="running", updated_at=utcnow() - timedelta(minutes=30)))
        await db.commit()
    assert await h.svc.recover_stale() == 1
    run = (await cl.get(f"/runs/{run_id}")).json()
    assert run["status"] == "failed" and "retry" in run["error"]


# ---- isolation / validation / degraded mode / edge -----------------------------------------------------------------------------------
async def test_another_users_run_is_404_and_lists_are_scoped(api_harness):
    h = await api_harness(allow_registration=True)
    alice = await signed_in(h, "alice@example.com")
    bob = await signed_in(h, "bob@example.com")
    run_id = await start_run(h, alice)
    for path in (f"/runs/{run_id}", f"/runs/{run_id}/trace", f"/runs/{run_id}/events"):
        assert (await bob.get(path)).status_code == 404
    assert (await bob.post(f"/runs/{run_id}/decision", json={"action": "approve"})).status_code == 404
    assert (await bob.get("/runs")).json() == [] and len((await alice.get("/runs")).json()) == 1


async def test_invalid_run_bodies_are_422(api_harness):
    h = await api_harness()
    cl = await signed_in(h)
    for body in ({"kind": "bid", "persona": "ai_ml"},
                 {"kind": "bid", "persona": "ai_ml", "job_id": "x", "job": INLINE_JOB},
                 {"kind": "conversation", "persona": "ai_ml"},
                 {"kind": "bid", "persona": "ai_ml", "job": {**INLINE_JOB, "budget_min": 900}}):
        r = await cl.post("/runs", json=body)
        assert r.status_code == 422 and "error" in r.json(), body


async def test_marketplace_outage_is_503_not_a_crash(api_harness):
    h = await api_harness(market=None)
    cl = await signed_in(h)
    r = await cl.post("/runs", json={"kind": "bid", "persona": "ai_ml", "job_id": "j1"})
    err = r.json()["error"]
    assert r.status_code == 503 and err["code"] == "marketplace_unavailable" and err["retryable"] is True
    assert (await cl.post("/runs", json={"kind": "bid", "persona": "ai_ml", "job": INLINE_JOB})).status_code == 202  # pasted jobs still work


async def test_marketplace_job_run_and_status_endpoint(api_harness):
    h = await api_harness(market=FakeMarket([market_job()]))
    cl = await signed_in(h)
    run_id = await start_run(h, cl, job=None, job_id="j1")
    assert (await cl.get(f"/runs/{run_id}")).json()["subject_id"] == "j1"
    st = await cl.get("/marketplace/status")
    assert st.status_code == 200 and st.json()["status"] == "up" and st.json()["connects"] == 100


async def test_health_trace_header_and_security_headers(api_harness):
    h = await api_harness()
    cl = h.client()
    r = await cl.get("/health", headers={"x-gigbid-trace-id": "trace-abc12345"})
    assert r.status_code == 200 and r.headers["x-gigbid-trace-id"] == "trace-abc12345"
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["x-frame-options"] == "DENY"
    forged = await cl.get("/health", headers={"x-gigbid-trace-id": "bad id;<script>"})
    assert forged.headers["x-gigbid-trace-id"] != "bad id;<script>"


async def test_forget_needs_the_typed_phrase(api_harness):
    h = await api_harness()
    cl = await signed_in(h)
    assert (await cl.post("/memory/forget", json={"confirm": "yes"})).status_code == 422
    assert (await cl.post("/memory/forget", json={"confirm": "forget everything"})).status_code in (200, 204)


# ---- approval queue / contract surface -----------------------------------------------------------------------------------------------
async def test_approval_queue_lists_only_my_pending_runs(api_harness):
    h = await api_harness(allow_registration=True)
    alice = await signed_in(h, "alice@example.com")
    bob = await signed_in(h, "bob@example.com")
    run_id = await start_run(h, alice)
    queue = (await alice.get("/approvals")).json()
    assert [r["id"] for r in queue] == [run_id] and queue[0]["status"] == "awaiting_approval"
    assert (await bob.get("/approvals")).json() == []
    assert (await alice.post(f"/runs/{run_id}/decision", json={"action": "reject"})).status_code == 202
    await h.svc.drain()
    assert (await alice.get("/approvals")).json() == []


async def test_required_route_groups_are_in_the_contract(api_harness):
    h = await api_harness()
    paths = set(h.app.openapi()["paths"])
    assert {"/auth/login", "/jobs/search", "/jobs/{job_id}", "/proposals", "/proposals/{proposal_id}/messages", "/approvals", "/runs",
            "/runs/{run_id}/events", "/runs/{run_id}/decision"} <= paths
    assert any(p.startswith("/analytics") for p in paths)
