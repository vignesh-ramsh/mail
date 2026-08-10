"""tasks/*.py — background/scheduled jobs (docs/arc.MD §3.11/§3.15). Loaded via relay.register_tasks(...). Durable + schedulable when the `lineup` plugin is installed; still runs in-process (just not durably) if it isn't — never depend on `lineup` directly, arc.relay.task/enqueue handle that automatically.

Delete this file, or rename it and uncomment what you need.
"""

# import arc
#
# @arc.relay.task(queue="default")
# async def send_something(employee_code: str) -> None:
#     ...
#
# @arc.relay.task(queue="low", cron="0 2 * * *")
# async def nightly_cleanup() -> None:
#     ...
