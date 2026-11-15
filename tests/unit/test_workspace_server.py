"""Workspace store (mock calendar + mail drafts) and its MCP wiring. Fixed clock: Mon 2026-11-16 08:00 (+05:00)."""
from datetime import UTC, datetime

import pytest

from mcp_servers.workspace.store import RecurringBusy, WorkspaceConfig, WorkspaceError, WorkspaceStore

MON = "2026-11-16"
CFG = WorkspaceConfig(
    utc_offset_hours=5,
    recurring_busy=[
        RecurringBusy(title="Lunch", days=[0, 1, 2, 3, 4], start="13:00", end="14:00"),
        RecurringBusy(title="Planning", days=[0], start="09:00", end="10:00"),
    ],
)


def store(tmp_path, **kw):
    return WorkspaceStore(CFG, tmp_path / "state.json", clock=lambda: datetime(2026, 11, 16, 3, 0, tzinfo=UTC), **kw)


def starts(slots):
    return [s.start[11:16] for s in slots]


def test_free_slots_respect_hours_notice_and_busy(tmp_path):
    s = store(tmp_path).free_slots(MON, duration_min=60)
    got = starts(s)
    assert got[0] == "10:00"                      # 09:00 is planning, 08:00 clock + 2h notice
    assert "12:30" not in got and "13:00" not in got and "13:30" not in got   # lunch 13-14 blocks 60-min slots
    assert got[-1] == "16:00"                     # last 60-min slot ends at 17:00
    assert all(x.start.endswith("+05:00") for x in s)
    assert store(tmp_path).free_slots("2026-11-21") == []     # Saturday


def test_range_and_duration_validation(tmp_path):
    st = store(tmp_path)
    for args in (("2026-11-16", "2026-12-30"), ("2026-11-17", "2026-11-16"), ("16-11-2026", None)):
        with pytest.raises(WorkspaceError):
            st.free_slots(*args)
    with pytest.raises(WorkspaceError):
        st.free_slots(MON, duration_min=5)


def test_hold_blocks_slot_with_buffer_and_release_frees_it(tmp_path):
    st = store(tmp_path)
    h = st.hold_slot(f"{MON}T10:00:00+05:00", 30, "Interview", "hold-key-1", proposal_id="prop-1")
    assert h.status == "tentative" and h.end.startswith(f"{MON}T10:30")
    assert starts(st.free_slots(MON))[0] == "11:00"           # 15-min buffer blocks 10:30
    with pytest.raises(WorkspaceError) as e:
        st.hold_slot(f"{MON}T10:30:00+05:00", 30, "Other", "hold-key-2")
    assert e.value.code == "SlotUnavailable"
    st.release_hold(h.hold_id)
    assert starts(st.free_slots(MON))[0] == "10:00" and st.list_holds() == []
    with pytest.raises(WorkspaceError):
        st.release_hold("hold-nope")


def test_hold_is_idempotent_and_rejects_key_reuse(tmp_path):
    st = store(tmp_path)
    a = st.hold_slot(f"{MON}T11:00:00+05:00", 30, "Interview", "hold-key-1")
    assert st.hold_slot(f"{MON}T11:00:00+05:00", 30, "Interview", "hold-key-1").hold_id == a.hold_id
    assert len(st.list_holds()) == 1
    with pytest.raises(WorkspaceError) as e:
        st.hold_slot(f"{MON}T15:00:00+05:00", 30, "Interview", "hold-key-1")
    assert e.value.code == "IdempotencyKeyReuse"


@pytest.mark.parametrize("start", [f"{MON}T08:30:00+05:00", f"{MON}T18:00:00+05:00", f"{MON}T13:00:00+05:00",
                                   "2026-11-21T11:00:00+05:00", "not-a-date"])
def test_unavailable_or_invalid_hold_times(tmp_path, start):
    with pytest.raises(WorkspaceError):
        store(tmp_path).hold_slot(start, 30, "x", "hold-key-1")


def test_state_persists_across_instances(tmp_path):
    store(tmp_path).hold_slot(f"{MON}T11:00:00+05:00", 30, "Interview", "hold-key-1")
    assert len(store(tmp_path).list_holds()) == 1


def test_email_drafts_never_send(tmp_path):
    st = store(tmp_path)
    d = st.draft_email("client@example.com", "Re: your project", "Hello", "mail-key-1", proposal_id="prop-1")
    again = st.draft_email("client@example.com", "Re: your project", "Hello", "mail-key-1", proposal_id="prop-1")
    assert d.status == "draft" and again.draft_id == d.draft_id
    assert [x.draft_id for x in st.list_drafts("prop-1")] == [d.draft_id] and st.list_drafts("prop-2") == []
    with pytest.raises(WorkspaceError):
        st.draft_email("not an email", "s", "b", "mail-key-2")
    assert not any(n.startswith("send") for n in dir(st))


async def test_mcp_wiring(tmp_path):
    memory = pytest.importorskip("mcp.shared.memory")
    from mcp_servers.workspace.server import create_server

    server = create_server(store(tmp_path), audit_path=tmp_path / "audit.jsonl")
    async with memory.create_connected_server_and_client_session(server._mcp_server) as c:
        tools = {t.name: t for t in (await c.list_tools()).tools}
        assert set(tools) == {"get_free_slots", "list_holds", "hold_slot", "release_hold", "draft_email", "list_email_drafts"}
        assert not any("send" in n for n in tools)
        assert tools["get_free_slots"].annotations.readOnlyHint and not tools["hold_slot"].annotations.readOnlyHint
        slots = await c.call_tool("get_free_slots", {"date_from": MON, "duration_min": 30})
        first = slots.structuredContent["result"][0]["start"]
        hold = await c.call_tool("hold_slot", {"start": first, "idempotency_key": "hold-key-1", "proposal_id": "prop-1"})
        assert not hold.isError and hold.structuredContent["status"] == "tentative"
        clash = await c.call_tool("hold_slot", {"start": first, "idempotency_key": "hold-key-2"})
        assert clash.isError and "SlotUnavailable" in clash.content[0].text
        await c.call_tool("draft_email", {"to": "c@example.com", "subject": "Hi", "body": "SECRET BODY TEXT here",
                                          "idempotency_key": "mail-key-1"})
    raw = server._audit.path.read_text(encoding="utf-8")
    assert "SECRET BODY TEXT" not in raw and server._audit.verify()
