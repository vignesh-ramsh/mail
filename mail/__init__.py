"""mail — ARC business plugin: multi-account, multi-provider email
delivery, with optional Jinja2-templated content, to/cc/bcc, and
metadata-only (never inline-bytes) attachments.

`MailAccount` and `MailTemplate` (mail/schemas/) are ordinary declared
schemas — both are already full CRUD in the generic Data Browser
(docs/arc.MD §3.14), so this plugin has no bespoke account/template
management API at all. `MailAccount.secret_ref` only names a settings
key, never a credential value: the actual value is set through the
already-existing Settings & Secrets screen (`arc.settings`, §3.5) —
mail never stores a credential itself, reusing the one encrypted store
the project already has rather than inventing a second one.

Delivery is queued through `arc.relay.enqueue()` (§3.15/§3.11) —
durable via `lineup` when installed, an honest in-process fallback
otherwise; this plugin needed no fallback logic of its own for that,
`enqueue()` already solved it. See `mail._delivery` for the actual
send-time worker function and `mail.providers` for the pluggable
delivery backends.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import arc
import jinja2
from jinja2.sandbox import SandboxedEnvironment

from ._delivery import deliver_email

CAPABILITY = "mail"

# SandboxedEnvironment, not a plain jinja2.Environment: `row["subject"]`/
# `row["text_body"]`/`row["html_body"]` below come straight out of the
# `mailtemplate` table — an ordinary declared schema with full CRUD in the
# generic Data Browser (this module's own docstring), i.e. editable by
# anyone with Superuser, not trusted source the way a template file on
# disk would be. A plain Environment lets template syntax reach through
# attribute access into `__globals__` (the textbook `{{
# cycler.__init__.__globals__.os.popen(...) }}` SSTI payload) — the
# sandbox blocks exactly that, at the cost of nothing a legitimate
# subject/body template ever needed.
#
# Two environments, not one: autoescape=False for the plain-text
# subject/text_body (escaping `&`/`<`/`>` there would corrupt it — see the
# "text" env below), but html_body genuinely needs autoescape=True — this
# module's `context` values are not staff-authored, e.g. forgot_password
# passes the CALLER-supplied `email` straight through, so an unescaped
# `{{ email }}` in an HTML template is a live HTML-injection path into
# whatever the recipient's mail client renders.
_jinja_env = SandboxedEnvironment(autoescape=False)
_jinja_env_html = SandboxedEnvironment(autoescape=True)


class MailError(RuntimeError):
    pass


class AccountNotFoundError(MailError, LookupError):
    pass


class TemplateNotFoundError(MailError, LookupError):
    pass


class MailProvider:
    async def send(
        self,
        to: list[str],
        *,
        account: str | None = None,
        cc: list[str] | None = None,
        bcc: list[str] | None = None,
        attachments: list[dict[str, str]] | None = None,
        # Exactly one of these two groups — see the ValueError below for why.
        subject: str | None = None,
        text_body: str | None = None,
        html_body: str | None = None,
        template: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        """Fire-and-forget, same posture as `arc.relay.enqueue()` itself
        (durable-fire-and-forget when `lineup` is installed, volatile
        otherwise) — but this awaits the returned Task rather than
        discarding it: in the durable path that only waits for the quick
        Redis handoff, while in the fallback path (no `lineup`) it's the
        difference between the send actually being attempted before this
        call returns versus a detached task that might never run before
        the process exits. A synchronous `check_resolvable()` failure
        inside `enqueue()` itself still surfaces immediately either way.

        `attachments`, if given: a list of `{"filename", "source",
        "mimetype"}` dicts — `source` is a local filesystem path or an
        `http(s)://` URL, resolved to real bytes only once, by the worker,
        right before sending (see `mail._delivery`). Never pass raw bytes
        here — there's nowhere for them to go but through the queue.
        """
        has_direct = subject is not None or text_body is not None or html_body is not None
        if bool(template) == has_direct:
            raise ValueError(
                "arc.mail.send() needs exactly one of: `template` (+ optional "
                "`context`), or direct `subject`/`text_body`/`html_body` — got "
                + ("both" if template else "neither")
                + "."
            )

        account_row = await self._resolve_account(account)

        if template:
            subject, text_body, html_body = await self._render(template, context or {})

        for meta in attachments or []:
            source = meta["source"]
            if not source.startswith(("http://", "https://")) and not Path(source).exists():
                raise FileNotFoundError(f"attachment source not found: {source}")

        att_meta = [
            {"filename": a["filename"], "source": a["source"], "mimetype": a.get("mimetype")}
            for a in (attachments or [])
        ]

        task = arc.relay.enqueue(
            deliver_email,
            account_row["name"],
            to,
            cc or [],
            bcc or [],
            subject,
            text_body,
            html_body,
            att_meta,
            queue="mail",
        )
        await task

    async def _resolve_account(self, name: str | None) -> dict:
        filters = {"name": {"eq": name}} if name else {"is_default": {"eq": True}}
        rows = await arc.relay.list(
            "mailaccount", fields=arc.relay.all_columns("mailaccount"), filters=filters, limit=1
        )
        if not rows:
            raise AccountNotFoundError(
                f"no mail account named '{name}'"
                if name
                else "no default mail account is configured"
            )
        return rows[0]

    async def _render(
        self, template_name: str, context: dict[str, Any]
    ) -> tuple[str, str, str | None]:
        rows = await arc.relay.list(
            "mailtemplate",
            fields=["subject", "text_body", "html_body"],
            filters={"name": {"eq": template_name}},
            limit=1,
        )
        if not rows:
            raise TemplateNotFoundError(f"no mail template named '{template_name}'")
        row = rows[0]
        subject = _jinja_env.from_string(row["subject"]).render(**context)
        text_body = _jinja_env.from_string(row["text_body"]).render(**context)
        html_body = (
            _jinja_env_html.from_string(row["html_body"]).render(**context)
            if row.get("html_body")
            else None
        )
        return subject, text_body, html_body


def register(kernel: Any) -> None:
    psqldb = kernel.get("pgdb")
    psqldb.register_model(Path(__file__).parent.parent / "schemas")
    psqldb.register_patches(Path(__file__).parent.parent / "patches")

    relay = kernel.get("relay")
    relay.register_hooks(Path(__file__).parent.parent / "hooks")
    relay.register_api(Path(__file__).parent.parent / "api")
    relay.register_tasks(Path(__file__).parent.parent / "tasks")

    kernel.export(
        CAPABILITY, MailProvider(), requires=["pgdb", "relay"], optional_requires=["lineup"]
    )
