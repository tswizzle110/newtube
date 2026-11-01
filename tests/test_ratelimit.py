import datetime as dt

import pytest

from newtube.net.ratelimit import DailyCapReached, Lane, RateLimiter


class FakeTime:
    def __init__(self):
        self.now = 100.0
        self.sleeps = []
        self.date = dt.date(2026, 1, 1)

    def clock(self):
        return self.now

    async def sleep(self, s):
        self.sleeps.append(s)
        self.now += s


def make(ft, **kw):
    args = dict(min_interval=1.0, jitter=0.5, daily_cap=3, clock=ft.clock, sleep=ft.sleep,
                rand=lambda: 1.0, today=lambda: ft.date)
    args.update(kw)
    return RateLimiter(**args)


async def test_requests_are_spaced_with_jitter():
    ft = FakeTime()
    rl = make(ft)
    await rl.acquire()
    await rl.acquire()
    # second call had to wait min_interval + jitter (rand()==1.0 -> full 0.5)
    assert ft.sleeps == [pytest.approx(1.5)]


async def test_background_daily_cap_and_reset():
    ft = FakeTime()
    rl = make(ft)
    for _ in range(3):
        await rl.acquire(Lane.BACKGROUND)
    assert rl.remaining_today() == 0
    with pytest.raises(DailyCapReached):
        await rl.acquire(Lane.BACKGROUND)
    # interactive requests are never capped
    await rl.acquire(Lane.INTERACTIVE)
    # next day the budget resets
    ft.date += dt.timedelta(days=1)
    await rl.acquire(Lane.BACKGROUND)
    assert rl.used_today() == 1
