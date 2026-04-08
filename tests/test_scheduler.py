import asyncio
import time


async def test_scheduler_fires():
    from src.scheduler.scheduler import Scheduler

    fired = []

    async def on_tick():
        fired.append(time.monotonic())

    scheduler = Scheduler(interval_seconds=0.1, cooldown_seconds=0, callback=on_tick)
    task = asyncio.create_task(scheduler.start())
    await asyncio.sleep(0.35)
    scheduler.stop()
    await task
    assert len(fired) >= 2


async def test_scheduler_cooldown():
    from src.scheduler.scheduler import Scheduler

    fired = []

    async def on_tick():
        fired.append(time.monotonic())

    scheduler = Scheduler(interval_seconds=0.05, cooldown_seconds=0.2, callback=on_tick)
    task = asyncio.create_task(scheduler.start())
    await asyncio.sleep(0.5)
    scheduler.stop()
    await task
    assert len(fired) <= 4


async def test_scheduler_stop():
    from src.scheduler.scheduler import Scheduler

    async def noop():
        pass

    scheduler = Scheduler(interval_seconds=10, cooldown_seconds=0, callback=noop)
    task = asyncio.create_task(scheduler.start())
    await asyncio.sleep(0.05)  # let start() begin running
    scheduler.stop()
    await task
    assert scheduler._running is False
