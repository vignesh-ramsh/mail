"""hooks/<Table Name>.py — one file per table, named exactly after its schema (docs/arc.MD §3.11). Loaded via relay.register_hooks(...).

Delete this file, or rename it to a real table and uncomment what you need. Nothing below runs until you do.
"""

# import arc
#
# @arc.relay.validate
# async def check_something(ctx) -> None:
#     if ctx.doc.some_field is None:
#         arc.relay.throw("some_field is required", code="missing_field")
#
# @arc.relay.after_save
# async def on_saved(ctx) -> None:
#     if ctx.doc._is_new:
#         arc.relay.log(f"created {ctx.new['id']}")
