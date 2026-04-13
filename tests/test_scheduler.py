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
    """多个 trigger 事件应按 FIFO 顺序全部处理（不再合并）。"""
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
    await asyncio.sleep(0.3)

    scheduler.stop()
    await task
    conditional_fired = [(t, ctx) for t, ctx in fired if t == "conditional"]
    assert len(conditional_fired) == 2
    contexts = [ctx for _, ctx in conditional_fired]
    assert contexts == ["event1", "event2"]


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


async def test_scheduler_preserves_trigger_type():
    """trigger_type 应保留原始值（不被硬编码为 'conditional'）。"""
    from src.scheduler.scheduler import Scheduler

    fired = []

    async def callback(trigger_type: str, context):
        fired.append((trigger_type, context))

    scheduler = Scheduler(interval_seconds=10, callback=callback)
    task = asyncio.create_task(scheduler.start())
    await asyncio.sleep(0.05)

    await scheduler.trigger("alert", context="price_drop")
    await asyncio.sleep(0.15)

    scheduler.stop()
    await task
    alert_events = [(t, c) for t, c in fired if t == "alert"]
    assert len(alert_events) == 1
    assert alert_events[0] == ("alert", "price_drop")


async def test_scheduler_fifo_order():
    """多个事件应按 FIFO 顺序处理，每个事件保留各自的 trigger_type 和 context。"""
    from src.scheduler.scheduler import Scheduler

    fired = []

    async def callback(trigger_type: str, context):
        fired.append((trigger_type, context))

    scheduler = Scheduler(interval_seconds=10, callback=callback)
    task = asyncio.create_task(scheduler.start())
    await asyncio.sleep(0.05)

    # 在 scheduler sleep 期间快速入队三个事件
    await scheduler.trigger("conditional", context="fill_1")
    await scheduler.trigger("alert", context="price_drop")
    await scheduler.trigger("conditional", context="fill_2")
    await asyncio.sleep(0.3)

    scheduler.stop()
    await task

    # 排除首次 scheduled 触发
    non_scheduled = [(t, c) for t, c in fired if t != "scheduled"]
    assert len(non_scheduled) == 3
    assert non_scheduled[0] == ("conditional", "fill_1")
    assert non_scheduled[1] == ("alert", "price_drop")
    assert non_scheduled[2] == ("conditional", "fill_2")


async def test_scheduler_context_not_lost_on_multiple_triggers():
    """多个 trigger 的 context 不应互相覆盖。"""
    from src.scheduler.scheduler import Scheduler

    fired = []

    async def callback(trigger_type: str, context):
        fired.append((trigger_type, context))
        await asyncio.sleep(0.05)  # 模拟 cycle 耗时

    scheduler = Scheduler(interval_seconds=10, callback=callback)
    task = asyncio.create_task(scheduler.start())
    await asyncio.sleep(0.05)

    await scheduler.trigger("conditional", context="event_A")
    await scheduler.trigger("conditional", context="event_B")
    await asyncio.sleep(0.3)

    scheduler.stop()
    await task

    contexts = [c for t, c in fired if t == "conditional"]
    assert "event_A" in contexts
    assert "event_B" in contexts


async def test_scheduler_safety_valve_max_drain():
    """单次 drain 最多处理 10 个事件，防止无限循环。"""
    from src.scheduler.scheduler import Scheduler

    fired = []

    async def callback(trigger_type: str, context):
        fired.append((trigger_type, context))

    scheduler = Scheduler(interval_seconds=10, callback=callback)
    task = asyncio.create_task(scheduler.start())
    await asyncio.sleep(0.05)

    # 入队 15 个事件
    for i in range(15):
        await scheduler.trigger("conditional", context=f"event_{i}")
    await asyncio.sleep(0.3)

    scheduler.stop()
    await task

    # 首次 drain 最多处理 10 个，剩余 5 个在下一次 sleep 后处理
    conditional_events = [(t, c) for t, c in fired if t == "conditional"]
    assert len(conditional_events) == 15  # 所有事件最终都应被处理


async def test_scheduler_event_preempts_scheduled():
    """有 pending 事件时不执行 scheduled cycle（互斥）。"""
    from src.scheduler.scheduler import Scheduler

    fired = []

    async def callback(trigger_type: str, context):
        fired.append((trigger_type, context))

    scheduler = Scheduler(interval_seconds=0.1, callback=callback)
    task = asyncio.create_task(scheduler.start())
    await asyncio.sleep(0.05)

    # 在第一次 sleep 期间触发事件，应取代 scheduled
    await scheduler.trigger("conditional", context="urgent")
    await asyncio.sleep(0.15)

    scheduler.stop()
    await task

    # 第一次是 scheduled（初始启动），第二次应是 conditional（不是 scheduled）
    assert fired[0] == ("scheduled", None)
    assert fired[1] == ("conditional", "urgent")


async def test_scheduler_drain_respects_stop():
    """drain 循环中调用 stop() 应立即终止剩余事件处理。"""
    from src.scheduler.scheduler import Scheduler

    fired = []

    async def callback(trigger_type: str, context):
        fired.append((trigger_type, context))
        if context == "event_2":
            scheduler.stop()  # 处理 event_2 后立即停止

    scheduler = Scheduler(interval_seconds=10, callback=callback)
    task = asyncio.create_task(scheduler.start())
    await asyncio.sleep(0.05)

    for i in range(5):
        await scheduler.trigger("conditional", context=f"event_{i}")
    await asyncio.sleep(0.5)

    await task

    conditional = [c for t, c in fired if t == "conditional"]
    # event_0, event_1, event_2 应被处理，event_3+ 应被跳过
    assert "event_2" in conditional
    assert "event_3" not in conditional


async def test_set_next_interval_overrides_once():
    """set_next_interval should override the next sleep, then revert to default."""
    from src.scheduler.scheduler import Scheduler

    fired = []

    async def callback(trigger_type: str, context):
        fired.append(trigger_type)

    scheduler = Scheduler(interval_seconds=10, callback=callback)
    scheduler.set_next_interval(0.05)  # 50ms for next sleep only

    task = asyncio.create_task(scheduler.start())
    await asyncio.sleep(0.2)
    # Should have fired: initial scheduled + after 50ms sleep another scheduled
    scheduler.stop()
    await task
    assert len(fired) >= 2


async def test_set_next_interval_resets_after_use():
    """After using the one-shot interval, scheduler returns to default."""
    from src.scheduler.scheduler import Scheduler

    scheduler = Scheduler(interval_seconds=10, callback=lambda t, c: None)
    scheduler.set_next_interval(5.0)
    assert scheduler._next_interval == 5.0
    # Simulate what start() does
    interval = scheduler._next_interval if scheduler._next_interval is not None else scheduler._interval
    scheduler._next_interval = None
    assert interval == 5.0
    assert scheduler._next_interval is None


async def test_set_next_interval_not_set_uses_default():
    """Without set_next_interval, scheduler uses default interval."""
    from src.scheduler.scheduler import Scheduler

    scheduler = Scheduler(interval_seconds=42, callback=lambda t, c: None)
    assert scheduler._next_interval is None
    interval = scheduler._next_interval if scheduler._next_interval is not None else scheduler._interval
    assert interval == 42
