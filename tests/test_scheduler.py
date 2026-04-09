import asyncio
import pytest


async def test_scheduler_fires_on_interval():
    from src.scheduler.scheduler import Scheduler

    fired = []

    async def callback(trigger_type: str, context):
        fired.append(trigger_type)

    scheduler = Scheduler(interval_seconds=0.1, callback=callback)
    task = asyncio.create_task(scheduler.start())
    await asyncio.sleep(0.35)
    scheduler.stop()
    await task
    assert len(fired) >= 2
    assert fired[0] == "scheduled"


async def test_scheduler_trigger_wakes_from_sleep():
    from src.scheduler.scheduler import Scheduler

    fired = []

    async def callback(trigger_type: str, context):
        fired.append((trigger_type, context))

    scheduler = Scheduler(interval_seconds=10, callback=callback)
    task = asyncio.create_task(scheduler.start())
    await asyncio.sleep(0.05)

    await scheduler.trigger("conditional", context="fill_event_1")
    await asyncio.sleep(0.1)

    scheduler.stop()
    await task
    assert ("scheduled", None) in fired
    assert any(t == "conditional" for t, _ in fired)


async def test_scheduler_trigger_merges_multiple_events():
    from src.scheduler.scheduler import Scheduler

    fired = []

    async def callback(trigger_type: str, context):
        fired.append((trigger_type, context))
        await asyncio.sleep(0.05)

    scheduler = Scheduler(interval_seconds=10, callback=callback)
    task = asyncio.create_task(scheduler.start())
    await asyncio.sleep(0.05)

    await scheduler.trigger("conditional", context="event1")
    await scheduler.trigger("conditional", context="event2")
    await asyncio.sleep(0.2)

    scheduler.stop()
    await task
    conditional_fired = [(t, ctx) for t, ctx in fired if t == "conditional"]
    assert len(conditional_fired) >= 1
    if len(conditional_fired) >= 1:
        contexts = [ctx for _, ctx in conditional_fired]
        assert "event1" in contexts or None in contexts


async def test_scheduler_stop():
    from src.scheduler.scheduler import Scheduler

    async def noop(trigger_type: str, context):
        pass

    scheduler = Scheduler(interval_seconds=10, callback=noop)
    task = asyncio.create_task(scheduler.start())
    await asyncio.sleep(0.05)
    scheduler.stop()
    await task
    assert scheduler._running is False


async def test_scheduler_trigger_before_start():
    from src.scheduler.scheduler import Scheduler

    fired = []

    async def callback(trigger_type: str, context):
        fired.append(trigger_type)

    scheduler = Scheduler(interval_seconds=10, callback=callback)
    await scheduler.trigger("conditional", context="early")
    task = asyncio.create_task(scheduler.start())
    await asyncio.sleep(0.1)

    scheduler.stop()
    await task
    assert "conditional" in fired
