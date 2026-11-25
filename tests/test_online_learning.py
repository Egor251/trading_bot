"""Known-answer pins for the observe-only online learner."""

from __future__ import annotations

from host.bootstrap import ensure_code_base_path

ensure_code_base_path()

from engine.online_learning import LiveBar, OnlineConfig, OnlineLearner  # noqa: E402


class _Optimizer:
    def __init__(self, acc: float, net: float, prob: float) -> None:
        self.acc, self.net, self.prob = acc, net, prob
        self.calls = 0

    def run(self, X, y, R, tr, va, te, seed):
        self.calls += 1
        assert 0 not in te or len(te) > 0
        assert len(X) == len(y) == len(R)
        return {"acc": self.acc, "net": self.net, "prob": self.prob}


def _stream(n: int, with_book: bool = False):
    for i in range(n):
        ts = float(i)
        for s in ("BTCUSDT", "ETHUSDT", "SOLUSDT"):
            spread = 2.0 if with_book else None
            imb = 0.1 if with_book else None
            yield LiveBar(ts, s, 100.0 * (1.0 + 0.001 * i), spread, imb)


def _filled(n: int, cfg: OnlineConfig, *, with_book: bool = False):
    learner = OnlineLearner(cfg)
    for bar in _stream(n, with_book):
        learner.observe(bar)
    return learner


def test_no_decision_before_the_observation_floor():
    cfg = OnlineConfig(lags=2, window_bars=30, update_every=5, min_observations=20)
    learner = _filled(12, cfg)
    assert learner.step(_Optimizer(0.9, 10.0, 0.9)) is None
    assert learner.decisions == []
    assert learner.snapshot()["observe_only"] is True


def test_negative_net_never_marks_tradable():
    cfg = OnlineConfig(lags=2, window_bars=30, update_every=5, min_observations=20)
    learner = _filled(40, cfg)
    d = learner.step(_Optimizer(0.9, -1.0, 0.9))
    assert d is not None
    assert d["action"] == "BUY"
    assert d["tradable"] is False
    assert d["observe_only"] is True


def test_positive_net_and_accuracy_allow_logging_only():
    cfg = OnlineConfig(lags=2, window_bars=30, update_every=5, min_observations=20)
    learner = _filled(40, cfg)
    d = learner.step(_Optimizer(0.8, 5.0, 0.4))
    assert d is not None
    assert d["action"] == "SELL"
    assert d["tradable"] is True
    assert d["observe_only"] is True


def test_replay_is_deterministic_and_book_absence_is_flagged():
    cfg = OnlineConfig(lags=2, window_bars=30, update_every=5, min_observations=20)
    a = _filled(40, cfg)
    b = _filled(40, cfg)
    da = a.step(_Optimizer(0.6, 1.0, 0.51))
    db = b.step(_Optimizer(0.6, 1.0, 0.51))
    assert da == db
    assert a.features == b.features
    # no book snapshots last two columns are zero/flag, never invented
    assert a.features[-1][-1] == 0.0
    assert a.features[-1][-2] == 0.0


def test_out_of_order_bar_is_ignored():
    cfg = OnlineConfig(lags=2, min_observations=20)
    learner = OnlineLearner(cfg)
    for bar in _stream(20):
        learner.observe(bar)
    before = learner.bars_seen
    learner.observe(LiveBar(0.0, "BTCUSDT", 123.0))
    assert learner.bars_seen == before
