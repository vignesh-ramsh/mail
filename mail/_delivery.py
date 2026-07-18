"""mail._delivery — the ad hoc background function `arc.mail.send()`
hands to `arc.relay.enqueue()` (docs/arc.MD §3.15's ad hoc dispatch
path: no `@arc.relay.task` decorator, no `tasks/` directory convention
— those are for work known ahead of time, and this needs a live
account row plus real network I/O). Deliberately outside api/hooks/
tasks/: nothing scans this module, nothing calls `deliver_email`
directly except `enqueue()` itself (durably via a `lineup` worker, or
in-process as the fallback when `lineup` isn't installed) — and per
`lineup.check_resolvable()`'s requirement, it must stay a plain,
module-level, re-importable function: no lambda, no closure, no method.

Only file METADATA (`filename`/`source`/`mimetype`) crosses the queue
for an attachment — never raw bytes, so a large attachment never
bloats the Redis payload `enqueue()` carries. This is the one place
that resolves real content, exactly once, right before sending: a
local path is read off disk, a URL is fetched over HTTP. The ACCOUNT
row is re-fetched here (by name only), so a credential rotated between
enqueue and delivery is picked up automatically. Template CONTENT, by
contrast, is rendered at send()-call time (mail/__init__._render) and
crosses the queue as finished subject/body strings — a template edit
takes effect from the next send() call onward, not retroactively for
jobs already sitting in the queue.
"""

from __future__ import annotations

import asyncio
import mimetypes
from pathlib import Path
from urllib.parse import urlparse

import httpx

import arc

from .providers import PROVIDERS, Attachment, Message


class MailDeliveryError(RuntimeError):
    pass


async def _resolve_attachment(meta: dict) -> Attachment:
    source = meta["source"]
    filename = meta["filename"]
    mimetype = meta.get("mimetype") or mimetypes.guess_type(filename)[0]

    if urlparse(source).scheme in ("http", "https"):
        async with httpx.AsyncClient() as client:
            resp = await client.get(source, timeout=30.0)
            resp.raise_for_status()
            content = resp.content
    else:
        # A worker process handles jobs off a shared queue concurrently —
        # a synchronous read of a possibly-large local file must not block
        # the event loop out from under everything else it's running.
        content = await asyncio.to_thread(Path(source).read_bytes)

    return Attachment(filename=filename, content=content, mimetype=mimetype)


async def deliver_email(
    account_name: str,
    to: list[str],
    cc: list[str],
    bcc: list[str],
    subject: str,
    text_body: str,
    html_body: str | None,
    attachments: list[dict],
) -> None:
    rows = await arc.relay.list("mailaccount", filters={"name": {"eq": account_name}}, limit=1)
    if not rows:
        raise MailDeliveryError(f"mail account '{account_name}' no longer exists")
    account = rows[0]
    if not account["enabled"]:
        raise MailDeliveryError(f"mail account '{account_name}' is disabled")

    provider = PROVIDERS.get(account["provider"])
    if provider is None:
        raise MailDeliveryError(f"mail account '{account_name}' has unknown provider '{account['provider']}'")

    credential = None
    if account.get("secret_ref"):
        credential = arc.settings.get(account["secret_ref"], reveal=True)

    resolved_attachments = [await _resolve_attachment(meta) for meta in attachments]

    message = Message(
        to=to,
        cc=cc,
        bcc=bcc,
        subject=subject,
        text_body=text_body,
        html_body=html_body,
        attachments=resolved_attachments,
    )
    await provider.send(
        message,
        from_email=account["from_email"],
        from_name=account.get("from_name"),
        config=account.get("config") or {},
        credential=credential,
    )
