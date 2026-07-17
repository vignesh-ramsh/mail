"""api/*.py — whitelisted functions, not table-named (docs/arc.MD §3.11). Loaded via relay.register_api(...). Always callable directly via arc.relay.call(...); additionally reachable over HTTP at /api/method/<plugin>.<function_name> when gateway is installed.

Delete this file, or rename it and uncomment what you need.
"""

# import arc
#
# @arc.relay.whitelist(methods=["GET"], roles=["Guest"])
# async def ping() -> dict:
#     return {"ok": True}
