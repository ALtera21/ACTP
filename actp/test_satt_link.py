import os

from actp import satt_link
from actp.satt_link import Inbox, decode

PLAN = "📋 PLAN XAUUSDr\n\n🟢 BUY LIMIT (6 layer, 0.72 lot)\nArea: 1 - 2"


def test_decode_handles_mql5_utf16_with_and_without_bom():
    assert decode(PLAN.encode("utf-16")) == PLAN
    assert decode(PLAN.encode("utf-16-le")) == PLAN
    assert decode(("\r\n".join(PLAN.split("\n"))).encode("utf-8-sig")) == PLAN
    assert decode(b"caf\xe9") == "café"
    assert decode(b"") == ""


def test_heartbeat_holds_the_time(tmp_path):
    box = Inbox(tmp_path / "in", now=lambda: 1_700_000_123.9)
    box.heartbeat()
    assert (tmp_path / "in" / satt_link.HEARTBEAT_FILE).read_text() == "1700000123"


def test_a_plan_is_handed_over_once_and_the_file_removed(tmp_path):
    now = [1000.0]
    box = Inbox(tmp_path, now=lambda: now[0])
    f = tmp_path / "a.plan"
    f.write_bytes(PLAN.encode("utf-16"))
    os.utime(f, (now[0], now[0]))
    got = box.poll()
    assert [i.text for i in got] == [PLAN] and not got[0].expired
    assert not f.exists()
    assert box.poll() == []


def test_an_old_plan_is_marked_expired(tmp_path):
    now = [1000.0]
    box = Inbox(tmp_path, now=lambda: now[0], max_age=30)
    f = tmp_path / "old.plan"
    f.write_bytes(PLAN.encode("utf-16"))
    os.utime(f, (now[0] - 31, now[0] - 31))
    assert box.poll()[0].expired


def test_other_files_and_unfinished_files_are_ignored(tmp_path):
    box = Inbox(tmp_path)
    (tmp_path / "x.tmp").write_bytes(b"half written")
    (tmp_path / "notes.txt").write_text("hi")
    (tmp_path / "empty.plan").write_bytes(b"")
    assert box.poll() == []
    assert (tmp_path / "x.tmp").exists() and (tmp_path / "notes.txt").exists()


def test_oldest_name_first(tmp_path):
    box = Inbox(tmp_path)
    for name, text in (("2_b.plan", "PLAN B"), ("1_a.plan", "PLAN A")):
        (tmp_path / name).write_text(text)
    assert [i.text for i in box.poll()] == ["PLAN A", "PLAN B"]


def test_display_path_hides_the_user_name():
    shown = satt_link.display_path(
        r"C:\Users\Someone\AppData\Roaming\MetaQuotes\Terminal\Common\Files\ACTP_inbox")
    assert "Someone" not in shown and "Users" not in shown
    assert shown == r"AppData\***\MetaQuotes\Terminal\Common\Files\ACTP_inbox"
    other = satt_link.display_path(r"D:\Portable\MT5\Common\Files\ACTP_inbox")
    assert other.startswith("...") and "D:" not in other
