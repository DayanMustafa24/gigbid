import httpx

from gigsim_local.app import create_app
from gigsim_local.config import SimConfig
from gigsim_local.sample_data import sample_world

H = {"Authorization": "Bearer t"}
LETTER = ("I built and deployed a PyTorch document classifier behind a FastAPI service for a legal startup, cutting review "
          "time by 40 percent. For your classification project I would start with a baseline, add evaluation on your labelled "
          "documents, and ship a tested API within a month. I have delivered similar work with measurable accuracy gains and "
          "clear weekly updates, and I can share relevant past work and answer any questions about the approach you prefer.")


def make(**kw):
    jobs, clients = sample_world()
    cfg = SimConfig(token="t", initial_released=99, **kw)
    app = create_app(cfg, jobs, clients)
    return app, httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://sim", headers=H)


def body(job="sample-001", **kw):
    return {"job_id": job, "cover_letter": LETTER, "bid_amount": 900, "screening_answers": ["Used PyTorch for classifiers."], **kw}


async def draft(c, key="d1", **kw):
    r = await c.post("/v1/proposals", json=body(**kw), headers={"Idempotency-Key": key})
    assert r.status_code == 201, r.text
    return r.json()["proposal_id"]


async def test_auth_and_health():
    _, c = make()
    assert (await c.get("/healthz")).status_code == 200
    assert (await c.get("/v1/jobs", headers={"Authorization": "Bearer nope"})).status_code == 401


async def test_search_job_detail_hides_internals():
    _, c = make()
    r = (await c.get("/v1/jobs", params={"q": "pytorch"})).json()
    assert r["total"] == 4
    j = (await c.get("/v1/jobs/sample-001")).json()
    assert "persona" not in j and "gold" not in j and j["description"] and j["connects_cost"] == 8
    assert (await c.get("/v1/jobs/nope")).status_code == 404


async def test_stream_release_on_tick():
    jobs, clients = sample_world()
    app = create_app(SimConfig(token="t", initial_released=2, release_per_tick=2), jobs, clients)
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://sim", headers=H)
    assert (await c.get("/v1/jobs")).json()["total"] == 2
    await c.post("/admin/tick?n=2")
    assert (await c.get("/v1/jobs")).json()["total"] == 6


async def test_create_is_idempotent():
    _, c = make()
    a = await draft(c, "k1")
    assert await draft(c, "k1") == a
    r = await c.post("/v1/proposals", json=body(bid_amount=1), headers={"Idempotency-Key": "k1"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "idempotency_key_reuse"
    assert (await c.post("/v1/proposals", json=body())).json()["detail"]["code"] == "idempotency_key_required"
    r = await c.post("/v1/proposals", json=body(), headers={"Idempotency-Key": "k2"})
    assert r.json()["detail"]["code"] == "duplicate_proposal"


async def test_submit_spends_once_and_dry_run_is_free():
    _, c = make()
    pid = await draft(c)
    p = (await c.post(f"/v1/proposals/{pid}/submit", params={"dry_run": "true"})).json()
    assert p["would_spend"] == 8 and p["submittable"] and (await c.get("/v1/connects/balance")).json()["balance"] == 200
    r1 = await c.post(f"/v1/proposals/{pid}/submit", headers={"Idempotency-Key": "s1"})
    r2 = await c.post(f"/v1/proposals/{pid}/submit", headers={"Idempotency-Key": "s1"})
    assert r1.json() == r2.json() and (await c.get("/v1/connects/balance")).json()["balance"] == 192
    r3 = await c.post(f"/v1/proposals/{pid}/submit", headers={"Idempotency-Key": "s2"})
    assert r3.status_code == 409 and r3.json()["detail"]["code"] == "already_submitted"


async def test_insufficient_connects():
    _, c = make(initial_connects=3)
    pid = await draft(c)
    r = await c.post(f"/v1/proposals/{pid}/submit", headers={"Idempotency-Key": "s"})
    assert r.status_code == 402


async def test_policy_update_toggle():
    _, c = make()
    pid = await draft(c)
    assert (await c.get("/v1/platform/policy")).json()["update_active"] is False
    pol = (await c.post("/admin/policy-update", json={"enabled": True})).json()
    assert pol["connects_surcharge"] == 2 and pol["requires_ai_disclosure"]
    prev = (await c.post(f"/v1/proposals/{pid}/submit", params={"dry_run": "true"})).json()
    assert prev["would_spend"] == 10 and not prev["submittable"] and prev["issues"][0]["rule"] == "PU-ai-disclosure"
    r = await c.post(f"/v1/proposals/{pid}/submit", headers={"Idempotency-Key": "s"})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "policy_violation"
    pid2 = await draft(c, "d2", job="sample-006", ai_disclosure=True)
    r = await c.post(f"/v1/proposals/{pid2}/submit", headers={"Idempotency-Key": "s3"})
    assert r.status_code == 200 and r.json()["connects_spent"] == 10


async def test_rate_limit_with_fake_clock():
    t = [0.0]
    _, c = make(rate_limit_per_min=3, clock=lambda: t[0])
    for _ in range(3):
        assert (await c.get("/v1/connects/balance")).status_code == 200
    r = await c.get("/v1/connects/balance")
    assert r.status_code == 429 and int(r.headers["Retry-After"]) >= 1
    t[0] += 61
    assert (await c.get("/v1/connects/balance")).status_code == 200


async def test_faults_before_and_after_commit():
    _, c = make()
    pid = await draft(c)
    await c.post("/admin/faults", json={"fault_after_commit_rate": 1.0})
    r = await c.post(f"/v1/proposals/{pid}/submit", headers={"Idempotency-Key": "s"})
    assert r.status_code >= 500                                  # response lost ...
    r = await c.post(f"/v1/proposals/{pid}/submit", headers={"Idempotency-Key": "s"})
    assert r.status_code == 200 and r.json()["balance_after"] == 192   # ... retry replays, spent once
    await c.post("/admin/faults", json={"fault_rate": 1.0})
    assert (await c.get("/v1/connects/balance")).status_code >= 500


async def _run(seed=1, letter=LETTER):
    jobs, clients = sample_world()
    app = create_app(SimConfig(token="t", seed=seed, initial_released=99), jobs, clients)
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://sim", headers=H)
    pid = (await c.post("/v1/proposals", json={**body(), "cover_letter": letter}, headers={"Idempotency-Key": "a"})).json()["proposal_id"]
    await c.post(f"/v1/proposals/{pid}/submit", headers={"Idempotency-Key": "b"})
    await c.post("/admin/tick?n=3")
    return c, pid


async def test_client_model_is_deterministic_and_prefers_specific_letters():
    c1, _ = await _run()
    c2, _ = await _run()
    o1, o2 = (await c1.get("/admin/outcomes")).json(), (await c2.get("/admin/outcomes")).json()
    assert o1["items"] == o2["items"]
    generic = "Dear Sir, I am writing to apply for this job. I am very interested. Thank you for your time."
    cg, _ = await _run(letter=generic)
    assert (await cg.get("/admin/outcomes")).json()["items"][0]["score"] < o1["items"][0]["score"]


async def test_scam_client_lures_and_conversation_flow():
    _, c = make()
    pid = await draft(c, job="sample-004", bid_amount=100)
    await c.post(f"/v1/proposals/{pid}/submit", headers={"Idempotency-Key": "s"})
    await c.post("/admin/tick")
    msgs = (await c.get("/v1/messages", params={"proposal_id": pid})).json()["items"]
    assert msgs and "telegram" in msgs[0]["text"].lower()
    # honest client: message flow ends in a valid status for some seed
    seen = set()
    for seed in range(12):
        cc, p = await _run(seed=seed)
        st = (await cc.get(f"/v1/proposals/{p}")).json()["status"]
        if st == "replied":
            await cc.post("/v1/messages", json={"proposal_id": p, "text": "Happy to share details. " * 8}, headers={"Idempotency-Key": "m"})
            await cc.post("/admin/tick")
            st = (await cc.get(f"/v1/proposals/{p}")).json()["status"]
        seen.add(st)
    assert seen <= {"ignored", "replied", "interview", "closed", "hired"} and len(seen) > 1
