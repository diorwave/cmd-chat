import asyncio

from cmd_chat.server.factory import cleanup_stale_sessions


def test_cleanup_task_stops_when_cancelled(app, monkeypatch):
    async def check_cancellation():
        sleep_started = asyncio.Event()
        sleep_calls = 0

        async def controlled_sleep(_seconds):
            nonlocal sleep_calls
            sleep_calls += 1
            if sleep_calls == 1:
                sleep_started.set()
                await asyncio.Future()
            raise RuntimeError("cleanup loop continued after cancellation")

        monkeypatch.setattr(asyncio, "sleep", controlled_sleep)
        task = asyncio.create_task(cleanup_stale_sessions(app))
        await sleep_started.wait()

        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

        assert task.cancelled()

    asyncio.run(check_cancellation())
