import asyncio
import pytest


async def test_scheduler_fires_on_interval():
    from src.scheduler.scheduler import Scheduler

    fired = []

    async def callback(events):
        fired.append(events[0][0])

    scheduler = Scheduler(interval_seconds=0.1, callback=callback)
    task = asyncio.create_task(scheduler.start())
    await asyncio.sleep(0.35)
    scheduler.stop()
    await task
    assert len(fired) >= 2
    assert fired[0] == "scheduled"


async def test_scheduler_trigger_wakes_from_sleep():
    from src.scheduler.scheduler import Scheduler

    calls = []

    async def callback(events):
        calls.append(list(events))

    scheduler = Scheduler(interval_seconds=10, callback=callback)
    task = asyncio.create_task(scheduler.start())
    await asyncio.sleep(0.05)

    await scheduler.trigger("conditional", context="fill_event_1")
    await asyncio.sleep(0.1)

    scheduler.stop()
    await task
    assert [("scheduled", None)] in calls
    assert any(("conditional", "fill_event_1") in batch for batch in calls)


async def test_scheduler_trigger_merges_multiple_events():
    """多个 trigger 事件在一次 sleep 内入队，应汇成单个 batch 一次性交付。"""
    from src.scheduler.scheduler import Scheduler

    calls = []

    async def callback(events):
        calls.append(list(events))

    scheduler = Scheduler(interval_seconds=10, callback=callback)
    task = asyncio.create_task(scheduler.start())
    await asyncio.sleep(0.05)

    await scheduler.trigger("conditional", context="event1")
    await scheduler.trigger("conditional", context="event2")
    await asyncio.sleep(0.3)

    scheduler.stop()
    await task
    non_bootstrap = [b for b in calls if b != [("scheduled", None)]]
    assert len(non_bootstrap) == 1
    assert non_bootstrap[0] == [
        ("conditional", "event1"),
        ("conditional", "event2"),
    ]


async def test_scheduler_stop():
    from src.scheduler.scheduler import Scheduler

    async def noop(events):
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

    async def callback(events):
        fired.extend(t for t, _ in events)

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

    calls = []

    async def callback(events):
        calls.append(list(events))

    scheduler = Scheduler(interval_seconds=10, callback=callback)
    task = asyncio.create_task(scheduler.start())
    await asyncio.sleep(0.05)

    await scheduler.trigger("alert", context="price_drop")
    await asyncio.sleep(0.15)

    scheduler.stop()
    await task
    non_bootstrap = [b for b in calls if b != [("scheduled", None)]]
    assert len(non_bootstrap) == 1
    assert ("alert", "price_drop") in non_bootstrap[0]


async def test_scheduler_priority_then_fifo():
    """Iter 7 (T2-2): 跨优先级按 priority (conditional > alert > scheduled)，
    同优先级内按 sequence FIFO。

    pre-next-observation §T2-2：取代旧 test_scheduler_fifo_order；P0-6
    cross-tick fix — close fill conditional 不应被 stale alerts 在 FIFO
    淹没。batch-drain 后整批一次交付，priority 排序体现在 batch list 内的顺序。
    """
    from src.scheduler.scheduler import Scheduler

    calls = []

    async def callback(events):
        calls.append(list(events))

    scheduler = Scheduler(interval_seconds=10, callback=callback)
    task = asyncio.create_task(scheduler.start())
    await asyncio.sleep(0.05)

    # 入队: conditional fill_1 (p=0,seq=1) / alert (p=1,seq=2) / conditional fill_2 (p=0,seq=3)
    await scheduler.trigger("conditional", context="fill_1")
    await scheduler.trigger("alert", context="price_drop")
    await scheduler.trigger("conditional", context="fill_2")
    await asyncio.sleep(0.3)

    scheduler.stop()
    await task

    non_scheduled = [b for b in calls if b != [("scheduled", None)]]
    assert len(non_scheduled) == 1
    # 新语义：两个 conditional 先（同优先级 seq FIFO），alert 最后
    assert non_scheduled[0] == [
        ("conditional", "fill_1"),
        ("conditional", "fill_2"),
        ("alert", "price_drop"),
    ], f"实际: {non_scheduled[0]}"


async def test_scheduler_priority_conditional_over_alert():
    """Iter 7 (T2-2) §spec 直接验证：6 alert + 1 conditional 同时 enqueue，
    conditional 必须排在 batch list 首位（即使最后入队）。

    P0-6 cross-tick：W1 #6 实测 close fill conditional 排在 ~6 stale alerts
    后被淹没 16 min。优先级队列保证 conditional 不被 alert 数量淹没。
    """
    from src.scheduler.scheduler import Scheduler

    calls = []

    async def callback(events):
        calls.append(list(events))

    scheduler = Scheduler(interval_seconds=10, callback=callback)
    task = asyncio.create_task(scheduler.start())
    await asyncio.sleep(0.05)

    # 6 alert 先入队，1 conditional 后入队
    for i in range(6):
        await scheduler.trigger("alert", context=f"alert_{i}")
    await scheduler.trigger("conditional", context="close_fill")
    await asyncio.sleep(0.5)

    scheduler.stop()
    await task

    non_scheduled = [b for b in calls if b != [("scheduled", None)]]
    assert len(non_scheduled) == 1
    batch = non_scheduled[0]
    assert len(batch) == 7, f"应处理 7 事件，实际 {len(batch)}"
    # 关键：conditional 排在首位（即使最后入队）
    assert batch[0] == ("conditional", "close_fill"), (
        f"conditional 应被优先消费，实际首个: {batch[0]}"
    )
    # 后 6 个全是 alert，按入队顺序
    for i in range(6):
        assert batch[1 + i] == ("alert", f"alert_{i}"), (
            f"alert seq[{i}]: {batch[1 + i]}"
        )


async def test_scheduler_fifo_within_same_priority():
    """Iter 7 (T2-2): 同优先级内 sequence tiebreak 保持 FIFO."""
    from src.scheduler.scheduler import Scheduler

    calls = []

    async def callback(events):
        calls.append(list(events))

    scheduler = Scheduler(interval_seconds=10, callback=callback)
    task = asyncio.create_task(scheduler.start())
    await asyncio.sleep(0.05)

    # 3 conditional 全同优先级，按入队顺序排列
    await scheduler.trigger("conditional", context="fill_1")
    await scheduler.trigger("conditional", context="fill_2")
    await scheduler.trigger("conditional", context="fill_3")
    await asyncio.sleep(0.3)

    scheduler.stop()
    await task

    non_scheduled = [b for b in calls if b != [("scheduled", None)]]
    assert len(non_scheduled) == 1
    assert non_scheduled[0] == [
        ("conditional", "fill_1"),
        ("conditional", "fill_2"),
        ("conditional", "fill_3"),
    ], f"同优先级应保持 FIFO，实际: {non_scheduled[0]}"


async def test_scheduler_context_not_lost_on_multiple_triggers():
    """多个 trigger 的 context 不应互相覆盖；都应出现在 batch list 内。"""
    from src.scheduler.scheduler import Scheduler

    calls = []

    async def callback(events):
        calls.append(list(events))

    scheduler = Scheduler(interval_seconds=10, callback=callback)
    task = asyncio.create_task(scheduler.start())
    await asyncio.sleep(0.05)

    await scheduler.trigger("conditional", context="event_A")
    await scheduler.trigger("conditional", context="event_B")
    await asyncio.sleep(0.3)

    scheduler.stop()
    await task

    contexts = [c for b in calls for t, c in b if t == "conditional"]
    assert "event_A" in contexts
    assert "event_B" in contexts


async def test_scheduler_drain_cap_20(caplog):
    import logging
    from src.scheduler.scheduler import Scheduler
    batches = []

    async def callback(events):
        batches.append(list(events))

    scheduler = Scheduler(interval_seconds=10, callback=callback)
    task = asyncio.create_task(scheduler.start())
    await asyncio.sleep(0.05)
    for i in range(21):
        await scheduler.trigger("conditional", context=f"e{i}")
    with caplog.at_level(logging.WARNING):
        await asyncio.sleep(0.3)
    scheduler.stop()
    await task

    non_bootstrap = [b for b in batches if b != [("scheduled", None)]]
    assert non_bootstrap, "expected at least one non-bootstrap drain batch"
    assert len(non_bootstrap[0]) == 20
    delivered = [c for b in non_bootstrap for (_, c) in b]
    assert len(delivered) == 21
    assert any("event drain capped" in r.message and "total=21" in r.message
               for r in caplog.records)


async def test_scheduler_drain_cap_boundary_no_warning(caplog):
    import logging
    from src.scheduler.scheduler import Scheduler
    batches = []

    async def callback(events):
        batches.append(list(events))

    scheduler = Scheduler(interval_seconds=10, callback=callback)
    task = asyncio.create_task(scheduler.start())
    await asyncio.sleep(0.05)
    for i in range(20):
        await scheduler.trigger("conditional", context=f"e{i}")
    with caplog.at_level(logging.WARNING):
        await asyncio.sleep(0.2)
    scheduler.stop()
    await task

    non_bootstrap = [b for b in batches if b != [("scheduled", None)]]
    assert len(non_bootstrap) == 1 and len(non_bootstrap[0]) == 20
    assert not any("event drain capped" in r.message for r in caplog.records)


async def test_scheduler_event_preempts_scheduled():
    """有 pending 事件时不执行 scheduled cycle（互斥）。"""
    from src.scheduler.scheduler import Scheduler

    calls = []

    async def callback(events):
        calls.append(list(events))

    scheduler = Scheduler(interval_seconds=0.1, callback=callback)
    task = asyncio.create_task(scheduler.start())
    await asyncio.sleep(0.05)

    # 在第一次 sleep 期间触发事件，应取代 scheduled
    await scheduler.trigger("conditional", context="urgent")
    await asyncio.sleep(0.15)

    scheduler.stop()
    await task

    # 第一次是 scheduled（初始启动 bootstrap），第二次应是 conditional（不是 scheduled）
    assert calls[0] == [("scheduled", None)]
    assert calls[1][0] == ("conditional", "urgent")


async def test_scheduler_drain_is_single_batch():
    """synchronous single-batch drain：一次 sleep 内入队的所有事件汇成
    一个 batch 一次性交付（取代旧 test_scheduler_drain_respects_stop，
    其 stop-mid-drain 跳过后续事件的前提已被同步单批 drain 否定）。"""
    from src.scheduler.scheduler import Scheduler

    calls = []

    async def callback(events):
        calls.append(list(events))

    scheduler = Scheduler(interval_seconds=10, callback=callback)
    task = asyncio.create_task(scheduler.start())
    await asyncio.sleep(0.05)

    for i in range(5):
        await scheduler.trigger("conditional", context=f"event_{i}")
    await asyncio.sleep(0.3)

    scheduler.stop()
    await task

    non_bootstrap = [b for b in calls if b != [("scheduled", None)]]
    assert len(non_bootstrap) == 1
    assert non_bootstrap[0] == [
        ("conditional", "event_0"),
        ("conditional", "event_1"),
        ("conditional", "event_2"),
        ("conditional", "event_3"),
        ("conditional", "event_4"),
    ]


async def test_set_next_interval_overrides_once():
    """set_next_interval should override the next sleep, then revert to default."""
    from src.scheduler.scheduler import Scheduler

    fired = []

    async def callback(events):
        fired.append(events[0][0])

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

    scheduler = Scheduler(interval_seconds=10, callback=lambda events: None)
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

    scheduler = Scheduler(interval_seconds=42, callback=lambda events: None)
    assert scheduler._next_interval is None
    interval = scheduler._next_interval if scheduler._next_interval is not None else scheduler._interval
    assert interval == 42
