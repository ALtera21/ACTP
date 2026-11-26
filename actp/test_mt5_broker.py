"""
Mt5Broker mapping tests. These use a fake `MetaTrader5` module, so they
need no terminal and run on any OS. They prove the translation logic, not
the real MT5 behaviour (that is what the probe and smoke tests are for).
"""

from decimal import Decimal
from types import SimpleNamespace as NS

import pytest

from actp.broker import (
    BrokerRejected,
    BrokerUnavailable,
    OrderRequest,
    OrderStatus,
)
from actp.mt5_broker import DEFAULT_MAGIC, Mt5Broker

SYMBOL = "XAUUSDc"


class FakeMt5:
    # Real MT5 constant values.
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    ORDER_TYPE_BUY_LIMIT = 2
    ORDER_TYPE_SELL_LIMIT = 3
    ORDER_TYPE_BUY_STOP = 4
    ORDER_STATE_CANCELED = 2
    ORDER_STATE_FILLED = 4
    ORDER_STATE_REJECTED = 5
    ORDER_STATE_EXPIRED = 6
    ORDER_STATE_PARTIAL = 3

    def __init__(self):
        self.pendings = []
        self.positions = []
        self.history = []
        self.tick = NS(bid=4084.5, ask=4084.9)
        self.fail = set()
        self.sent = []
        self.send_result = NS(retcode=10008, order=555, comment="ok")

    def _maybe(self, name, value):
        return None if name in self.fail else value

    def initialize(self, *a):
        return "initialize" not in self.fail

    def shutdown(self):
        pass

    def last_error(self):
        return (-1, "fake error")

    def symbol_select(self, symbol, enable):
        return True

    def symbol_info(self, symbol):
        return self._maybe("symbol_info", NS(digits=2, filling_mode=1))

    def symbol_info_tick(self, symbol):
        return self._maybe("tick", self.tick)

    def orders_get(self, ticket=None):
        items = [o for o in self.pendings if ticket in (None, o.ticket)]
        return self._maybe("orders_get", tuple(items))

    def positions_get(self, ticket=None):
        items = [p for p in self.positions if ticket in (None, p.ticket)]
        return self._maybe("positions_get", tuple(items))

    def order_send(self, request):
        self.sent.append(request)
        if "order_send" in self.fail:
            return None
        return self.send_result

    def history_orders_get(self, date_from, date_to):
        return self._maybe("history", tuple(self.history))


def pending(ticket, comment, type_=2, magic=DEFAULT_MAGIC, price=4084.77):
    return NS(
        ticket=ticket, comment=comment, type=type_, magic=magic,
        symbol=SYMBOL, price_open=price, volume_current=0.01,
        sl=4075.95, tp=4087.04,
    )


def position(ticket, comment, type_=0, magic=DEFAULT_MAGIC):
    return NS(
        ticket=ticket, identifier=ticket, comment=comment, type=type_,
        magic=magic, symbol=SYMBOL, price_open=4084.77, volume=0.01,
        sl=4075.95, tp=4087.04,
    )


def hist(ticket, comment, state, type_=2, magic=DEFAULT_MAGIC, position_id=0):
    return NS(
        ticket=ticket, comment=comment, type=type_, magic=magic,
        symbol=SYMBOL, price_open=4084.77, volume_initial=0.01,
        sl=4075.95, tp=4087.04, state=state, position_id=position_id,
    )


def make():
    fake = FakeMt5()
    return Mt5Broker(mt5=fake), fake


def test_pending_order_is_mapped():
    broker, fake = make()
    fake.pendings = [pending(101, "170-abcd1234:0")]
    (order,) = broker.list_orders()
    assert order.ticket == "101"
    assert order.client_id == "170-abcd1234:0"
    assert order.side == "BUY"
    assert order.status == OrderStatus.PENDING
    assert order.entry_price == Decimal("4084.77")
    assert order.volume == Decimal("0.01")
    assert order.sl == Decimal("4075.95")


def test_sell_limit_side():
    broker, fake = make()
    fake.pendings = [pending(1, "m:0", type_=3)]
    assert broker.list_orders()[0].side == "SELL"


def test_other_magic_is_ignored():
    broker, fake = make()
    fake.pendings = [pending(1, "manual trade", magic=0)]
    fake.positions = [position(2, "", magic=0)]
    fake.history = [hist(3, "x", FakeMt5.ORDER_STATE_CANCELED, magic=7)]
    assert broker.list_orders() == []


def test_position_is_filled_and_not_duplicated_from_history():
    broker, fake = make()
    fake.positions = [position(101, "m:0")]
    fake.history = [hist(101, "m:0", FakeMt5.ORDER_STATE_FILLED, position_id=101)]
    (order,) = broker.list_orders()
    assert order.status == OrderStatus.FILLED
    assert order.client_id == "m:0"


def test_position_comment_rewritten_by_broker_falls_back_to_history():
    broker, fake = make()
    fake.positions = [position(101, "[sl 4070.00]")]
    fake.history = [hist(101, "m:3", FakeMt5.ORDER_STATE_FILLED, position_id=101)]
    (order,) = broker.list_orders()
    assert order.client_id == "m:3"
    assert order.status == OrderStatus.FILLED


def test_history_cancelled_and_closed_are_reported():
    broker, fake = make()
    fake.history = [
        hist(1, "m:0", FakeMt5.ORDER_STATE_CANCELED),
        hist(2, "m:1", FakeMt5.ORDER_STATE_FILLED, position_id=2),  # closed since
        hist(3, "m:2", FakeMt5.ORDER_STATE_EXPIRED),
    ]
    by_id = {o.client_id: o.status for o in broker.list_orders()}
    assert by_id == {
        "m:0": OrderStatus.CANCELLED,
        "m:1": OrderStatus.CLOSED,
        "m:2": OrderStatus.CANCELLED,
    }


def test_closing_deals_in_history_are_not_layers():
    broker, fake = make()
    fake.history = [
        hist(9, "[tp 4087.04]", FakeMt5.ORDER_STATE_FILLED, type_=1, position_id=2)
    ]
    assert broker.list_orders() == []


@pytest.mark.parametrize("name", ["orders_get", "positions_get", "history"])
def test_read_failure_is_unavailable(name):
    broker, fake = make()
    fake.fail.add(name)
    with pytest.raises(BrokerUnavailable):
        broker.list_orders()


def test_unreadable_comment_on_our_order_halts_safely():
    broker, fake = make()
    fake.pendings = [pending(1, "garbage")]
    with pytest.raises(BrokerUnavailable):
        broker.list_orders()


def test_unexpected_order_type_with_our_magic_halts_safely():
    broker, fake = make()
    fake.pendings = [pending(1, "m:0", type_=FakeMt5.ORDER_TYPE_BUY_STOP)]
    with pytest.raises(BrokerUnavailable):
        broker.list_orders()


def test_duplicate_client_id_among_active_orders_halts_safely():
    broker, fake = make()
    fake.pendings = [pending(1, "m:0"), pending(2, "m:0")]
    with pytest.raises(BrokerUnavailable):
        broker.list_orders()


def test_get_tick():
    broker, fake = make()
    tick = broker.get_tick(SYMBOL)
    assert tick.bid == Decimal("4084.50")
    assert tick.ask == Decimal("4084.90")


def test_get_tick_missing_is_unavailable():
    broker, fake = make()
    fake.fail.add("tick")
    with pytest.raises(BrokerUnavailable):
        broker.get_tick(SYMBOL)


def test_zero_tick_is_unavailable():
    broker, fake = make()
    fake.tick = NS(bid=0.0, ask=0.0)
    with pytest.raises(BrokerUnavailable):
        broker.get_tick(SYMBOL)


def test_connect_failure_is_unavailable():
    broker, fake = make()
    fake.fail.add("initialize")
    with pytest.raises(BrokerUnavailable):
        broker.connect()


# ---- close_position ---------------------------------------------------


def test_close_buy_sells_at_bid_against_the_position():
    broker, fake = make()
    fake.positions = [position(101, "m:0", type_=0)]
    broker.close_position("101")
    (sent,) = fake.sent
    assert sent["action"] == 1
    assert sent["type"] == FakeMt5.ORDER_TYPE_SELL
    assert sent["position"] == 101
    assert sent["price"] == 4084.5  # bid
    assert sent["volume"] == 0.01
    assert sent["magic"] == DEFAULT_MAGIC
    assert sent["type_filling"] == 0  # FOK, the only mode the symbol offers


def test_close_sell_buys_at_ask():
    broker, fake = make()
    fake.positions = [position(101, "m:0", type_=1)]
    broker.close_position("101")
    assert fake.sent[0]["type"] == FakeMt5.ORDER_TYPE_BUY
    assert fake.sent[0]["price"] == 4084.9  # ask


def test_close_refuses_missing_pending_and_foreign():
    broker, fake = make()
    with pytest.raises(BrokerRejected):
        broker.close_position("101")
    fake.pendings = [pending(102, "m:0")]
    with pytest.raises(BrokerRejected):
        broker.close_position("102")  # pending, not filled
    fake.positions = [position(103, "x", magic=0)]
    with pytest.raises(BrokerRejected):
        broker.close_position("103")
    assert fake.sent == []


def test_close_outcome_mapping():
    broker, fake = make()
    fake.positions = [position(101, "m:0")]
    fake.send_result = NS(retcode=10004, order=0, comment="requote")
    with pytest.raises(BrokerRejected):
        broker.close_position("101")
    fake.send_result = NS(retcode=10012, order=0, comment="timeout")
    with pytest.raises(BrokerUnavailable):
        broker.close_position("101")


# ---- place_pending ----------------------------------------------------


def request(client_id="m:0", side="BUY"):
    return OrderRequest(
        client_id=client_id, symbol=SYMBOL, side=side,
        entry_price=Decimal("4084.77"), volume=Decimal("0.01"),
        sl=Decimal("4075.95"), tp=Decimal("4087.04"),
    )


def test_place_pending_sends_the_right_request():
    broker, fake = make()
    order = broker.place_pending(request())
    (sent,) = fake.sent
    assert sent["action"] == 5
    assert sent["type"] == FakeMt5.ORDER_TYPE_BUY_LIMIT
    assert sent["symbol"] == SYMBOL
    assert sent["volume"] == 0.01
    assert sent["price"] == 4084.77
    assert sent["sl"] == 4075.95 and sent["tp"] == 4087.04
    assert sent["magic"] == DEFAULT_MAGIC
    assert sent["comment"] == "m:0"
    assert order.ticket == "555"
    assert order.status == OrderStatus.PENDING


def test_sell_uses_sell_limit():
    broker, fake = make()
    broker.place_pending(request(side="SELL"))
    assert fake.sent[0]["type"] == FakeMt5.ORDER_TYPE_SELL_LIMIT


def test_place_pending_returns_existing_pending_without_sending():
    broker, fake = make()
    fake.pendings = [pending(101, "m:0")]
    order = broker.place_pending(request("m:0"))
    assert order.ticket == "101"
    assert fake.sent == []


def test_place_pending_returns_existing_cancelled_without_sending():
    broker, fake = make()
    fake.history = [hist(7, "m:0", FakeMt5.ORDER_STATE_CANCELED)]
    order = broker.place_pending(request("m:0"))
    assert order.status == OrderStatus.CANCELLED
    assert fake.sent == []


def test_place_pending_rejects_bad_client_id_and_extra_decimals():
    broker, fake = make()
    with pytest.raises(BrokerRejected):
        broker.place_pending(request("no-colon"))
    bad = OrderRequest("m:1", SYMBOL, "BUY", Decimal("4084.771"),
                       Decimal("0.01"), Decimal("1"), Decimal("2"))
    with pytest.raises(BrokerRejected):
        broker.place_pending(bad)
    assert fake.sent == []


@pytest.mark.parametrize("code", [10019, 10015, 10018, 10027, 10029])
def test_definite_refusals_are_rejected(code):
    broker, fake = make()
    fake.send_result = NS(retcode=code, order=0, comment="no")
    with pytest.raises(BrokerRejected):
        broker.place_pending(request())


@pytest.mark.parametrize("code", [10012, 10031, 10011, 10010, 99999])
def test_unknown_outcomes_are_unavailable(code):
    broker, fake = make()
    fake.send_result = NS(retcode=code, order=0, comment="?")
    with pytest.raises(BrokerUnavailable):
        broker.place_pending(request())


def test_order_send_returning_none_is_unavailable():
    broker, fake = make()
    fake.fail.add("order_send")
    with pytest.raises(BrokerUnavailable):
        broker.place_pending(request())


# ---- cancel_pending ---------------------------------------------------


def test_cancel_sends_remove():
    broker, fake = make()
    fake.pendings = [pending(101, "m:0")]
    broker.cancel_pending("101")
    assert fake.sent == [{"action": 8, "order": 101}]


def test_cancel_refuses_missing_and_foreign_orders():
    broker, fake = make()
    with pytest.raises(BrokerRejected):
        broker.cancel_pending("101")
    fake.pendings = [pending(102, "manual", magic=0)]
    with pytest.raises(BrokerRejected):
        broker.cancel_pending("102")
    assert fake.sent == []


def test_cancel_refuses_a_filled_position():
    broker, fake = make()
    fake.positions = [position(101, "m:0")]
    with pytest.raises(BrokerRejected):
        broker.cancel_pending("101")


# ---- modify_sltp ------------------------------------------------------


def test_modify_pending_sends_modify_with_same_price():
    broker, fake = make()
    fake.pendings = [pending(101, "m:0")]
    broker.modify_sltp("101", Decimal("4076.00"), Decimal("4088.00"))
    (sent,) = fake.sent
    assert sent["action"] == 7 and sent["order"] == 101
    assert sent["price"] == 4084.77
    assert sent["sl"] == 4076.0 and sent["tp"] == 4088.0


def test_modify_position_sends_sltp():
    broker, fake = make()
    fake.positions = [position(101, "m:0")]
    broker.modify_sltp("101", Decimal("4076.00"), Decimal("4088.00"))
    (sent,) = fake.sent
    assert sent["action"] == 6 and sent["position"] == 101
    assert sent["symbol"] == SYMBOL


def test_modify_with_no_changes_code_is_success():
    broker, fake = make()
    fake.pendings = [pending(101, "m:0")]
    fake.send_result = NS(retcode=10025, order=0, comment="No changes")
    broker.modify_sltp("101", Decimal("4075.95"), Decimal("4087.04"))


def test_modify_refuses_missing_foreign_and_bad_ticket():
    broker, fake = make()
    with pytest.raises(BrokerRejected):
        broker.modify_sltp("101", Decimal("1"), Decimal("2"))
    fake.positions = [position(5, "x", magic=0)]
    with pytest.raises(BrokerRejected):
        broker.modify_sltp("5", Decimal("1"), Decimal("2"))
    with pytest.raises(BrokerRejected):
        broker.modify_sltp("abc", Decimal("1"), Decimal("2"))
    assert fake.sent == []

def test_place_market_buy_deals_at_ask():
    broker, fake = make()
    order = broker.place_market(request())
    (sent,) = fake.sent
    assert sent["action"] == 1
    assert sent["type"] == FakeMt5.ORDER_TYPE_BUY
    assert sent["price"] == 4084.9  # ask
    assert sent["comment"] == "m:0"
    assert sent["sl"] == 4075.95 and sent["tp"] == 4087.04
    assert order.status == OrderStatus.FILLED


def test_place_market_sell_deals_at_bid():
    broker, fake = make()
    broker.place_market(request(side="SELL"))
    assert fake.sent[0]["type"] == FakeMt5.ORDER_TYPE_SELL
    assert fake.sent[0]["price"] == 4084.5  # bid


def test_place_market_returns_existing_without_sending():
    broker, fake = make()
    fake.positions = [position(101, "m:0")]
    assert broker.place_market(request("m:0")).ticket == "101"
    assert fake.sent == []


def test_market_opened_layer_closed_by_sl_is_reported_closed():
    broker, fake = make()
    fake.history = [
        hist(1, "m:0", FakeMt5.ORDER_STATE_FILLED,
             type_=FakeMt5.ORDER_TYPE_BUY, position_id=1),
        hist(2, "[sl 4075.95]", FakeMt5.ORDER_STATE_FILLED,
             type_=FakeMt5.ORDER_TYPE_SELL, position_id=1),
    ]
    (order,) = broker.list_orders()
    assert order.client_id == "m:0"
    assert order.status == OrderStatus.CLOSED

# ---- get_candles / tick time ------------------------------------------------

def test_get_candles_maps_rates_oldest_first():
    broker, fake = make()
    fake.TIMEFRAME_M5 = 5
    fake.copy_rates_from_pos = lambda sym, tf, pos, n: (
        [{"time": 1000, "close": 4084.5}, {"time": 1300, "close": 4085.25}]
        if (sym, tf, pos, n) == (SYMBOL, 5, 0, 2) else None
    )
    got = broker.get_candles(SYMBOL, "M5", 2)
    assert [(c.open_time, c.close) for c in got] == [
        (1000, Decimal("4084.50")), (1300, Decimal("4085.25"))]


def test_get_candles_failures_are_unavailable_or_rejected():
    broker, fake = make()
    fake.TIMEFRAME_M1 = 1
    fake.copy_rates_from_pos = lambda *a: None
    with pytest.raises(BrokerUnavailable):
        broker.get_candles(SYMBOL, "M1", 2)
    with pytest.raises(BrokerRejected):
        broker.get_candles(SYMBOL, "M7", 2)


def test_tick_carries_the_server_time_when_mt5_gives_one():
    broker, fake = make()
    assert broker.get_tick(SYMBOL).time is None
    fake.tick = NS(bid=4084.5, ask=4084.9, time=1763400000)
    assert broker.get_tick(SYMBOL).time == 1763400000


def _filled(ticket, kind=0, magic=DEFAULT_MAGIC):
    from types import SimpleNamespace as NS
    return NS(ticket=ticket, magic=magic, symbol=SYMBOL, type=kind,
              volume=0.01, price_open=4080.0, sl=0.0, tp=0.0,
              comment="", time_setup=0, time=0)


def _order(ticket, status):
    return type("O", (), {"ticket": str(ticket), "status": status})()


def test_release_many_sends_every_close_with_one_read_and_one_tick():
    mt5 = FakeMt5()
    mt5.positions = [_filled(1), _filled(2, kind=1), _filled(3)]
    broker = Mt5Broker(mt5=mt5)
    orders = [_order(1, OrderStatus.FILLED), _order(2, OrderStatus.FILLED),
              _order(3, OrderStatus.FILLED)]
    assert broker.release_many(orders) == [None, None, None]
    assert len(mt5.sent) == 3
    assert {r["position"] for r in mt5.sent} == {1, 2, 3}
    prices = {r["position"]: r["price"] for r in mt5.sent}
    assert prices[1] == 4084.5 and prices[2] == 4084.9  # BUY at bid, SELL at ask


def test_release_many_cancels_pending_before_closing_and_skips_gone_ones():
    mt5 = FakeMt5()
    mt5.positions = [_filled(1)]
    mt5.pendings = [_filled(7)]
    broker = Mt5Broker(mt5=mt5)
    orders = [_order(1, OrderStatus.FILLED), _order(7, OrderStatus.PENDING),
              _order(9, OrderStatus.FILLED)]  # 9 is already closed
    assert broker.release_many(orders) == [None, None, None]
    assert [r["action"] for r in mt5.sent] == [8, 1]  # remove, then deal


def test_release_many_reports_the_failed_one_and_still_sends_the_rest():
    mt5 = FakeMt5()
    mt5.positions = [_filled(1), _filled(2)]
    broker = Mt5Broker(mt5=mt5)
    mt5.send_result = NS(retcode=10031, order=0, comment="no connection")
    outcomes = broker.release_many(
        [_order(1, OrderStatus.FILLED), _order(2, OrderStatus.FILLED)])
    assert all(isinstance(o, BrokerUnavailable) for o in outcomes)
    assert len(mt5.sent) == 2


def test_an_order_that_is_filling_right_now_does_not_halt(monkeypatch):
    # Seen live: orders_get briefly returned our order as type 0 (plain BUY)
    # the moment it filled. That must not halt ACTP.
    monkeypatch.setattr("actp.mt5_broker.time.sleep", lambda _s: None)
    broker, fake = make()
    fake.pendings = [pending(1, "m:0", type_=FakeMt5.ORDER_TYPE_BUY),
                     pending(2, "m:1")]
    orders = broker.list_orders()
    assert [o.ticket for o in orders] == ["2"]


def test_a_read_in_the_middle_of_a_fill_is_read_again(monkeypatch):
    monkeypatch.setattr("actp.mt5_broker.time.sleep", lambda _s: None)
    broker, fake = make()
    fake.pendings = [pending(1, "m:0", type_=FakeMt5.ORDER_TYPE_BUY)]
    calls = {"n": 0}
    real = fake.orders_get

    def flaky(ticket=None):
        calls["n"] += 1
        if calls["n"] >= 2:  # the next read is consistent: now a position
            fake.pendings = []
            fake.positions = [position(1, "m:0")]
        return real(ticket)

    fake.orders_get = flaky
    orders = broker.list_orders()
    assert [(o.ticket, o.status.name) for o in orders] == [("1", "FILLED")]
