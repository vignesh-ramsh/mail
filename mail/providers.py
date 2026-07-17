"""mail.providers — pluggable delivery backends.

One `Provider` protocol + a registry keyed by `MailAccount.provider`.
Only "smtp" ships today, deliberately — the registry/protocol IS the
"multi-provider support" feature; a second provider is "write the
class, register it, add the SELECT option on MailAccount.provider",
never a redesign.

`Message`/`Attachment` are the provider-agnostic envelope every
provider's `send()` takes — built once by `mail._delivery.deliver_email`
with real attachment bytes already resolved (never crosses a queue
itself, see `_delivery.py`'s own docstring)."""

from __future__ import annotations

from dataclasses import dataclass, field
from email.message import EmailMessage
from typing import Protocol

import aiosmtplib


@dataclass
class Attachment:
    filename: str
    content: bytes
    mimetype: str | None = None


@dataclass
class Message:
    to: list[str]
    subject: str
    text_body: str
    html_body: str | None = None
    cc: list[str] = field(default_factory=list)
    bcc: list[str] = field(default_factory=list)
    attachments: list[Attachment] = field(default_factory=list)


class Provider(Protocol):
    async def send(
        self,
        message: Message,
        *,
        from_email: str,
        from_name: str | None,
        config: dict,
        credential: str | None,
    ) -> None: ...


class SMTPProvider:
    async def send(
        self,
        message: Message,
        *,
        from_email: str,
        from_name: str | None,
        config: dict,
        credential: str | None,
    ) -> None:
        email_msg = EmailMessage()
        email_msg["Subject"] = message.subject
        email_msg["From"] = f"{from_name} <{from_email}>" if from_name else from_email
        email_msg["To"] = ", ".join(message.to)
        if message.cc:
            email_msg["Cc"] = ", ".join(message.cc)
        # Bcc deliberately gets no header at all (that's the whole point of
        # "blind") — those addresses only ever reach the SMTP envelope, via
        # `recipients=` below, never the message itself.
        email_msg.set_content(message.text_body)
        if message.html_body:
            email_msg.add_alternative(message.html_body, subtype="html")
        for att in message.attachments:
            mimetype = att.mimetype or "application/octet-stream"
            maintype, _, subtype = mimetype.partition("/")
            email_msg.add_attachment(
                att.content, maintype=maintype, subtype=subtype or "octet-stream", filename=att.filename
            )

        await aiosmtplib.send(
            email_msg,
            sender=from_email,
            recipients=[*message.to, *message.cc, *message.bcc],
            hostname=config.get("host", "localhost"),
            port=int(config.get("port", 587)),
            username=config.get("username"),
            password=credential,
            start_tls=config.get("starttls", True),
            use_tls=config.get("use_tls", False),
        )


PROVIDERS: dict[str, Provider] = {"smtp": SMTPProvider()}
