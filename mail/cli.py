"""mail.cli — `arc mail ...` commands.

Mounted onto the core `arc` CLI via the `arc.plugins.cli` entry point,
same mechanism as psqldb.cli/redix.cli/lineup.cli. `send-test` does a
real `arc.boot()` + opens psqldb (and `lineup`, when installed, so a
durable enqueue actually has a started broker to publish through) —
same shape as authn.cli's `_run()` helper, since this does a real send,
not a read-only connectivity probe. Comma-separated string options for
multi-value flags (`--to`, `--cc`, ...), matching authn.cli's own
`--role` convention rather than typer's repeatable-flag `list[str]`.

Account/template management has no CLI here on purpose — both are
ordinary declared schemas, already fully manageable through the
generic Data Browser (docs/arc.MD §3.14); a second, parallel CLI-based
CRUD surface would just be duplication.
"""

from __future__ import annotations

import asyncio
import json
import warnings

import arc
import typer
from rich.console import Console

app = typer.Typer(help="Commands for the mail provider.")
console = Console()
err_console = Console(stderr=True, style="bold red")


def _run(coro) -> None:
    async def _main():
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", arc.ArcAdvisory)
            arc.boot()
        await arc.psqldb.open()
        has_lineup = hasattr(arc, "lineup")
        if has_lineup:
            await arc.lineup.open()
        try:
            await coro
        finally:
            if has_lineup:
                await arc.lineup.close()
            await arc.psqldb.close()

    asyncio.run(_main())


def _split(value: str) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


@app.command(name="send-test")
def send_test(
    to: str = typer.Option(..., "--to", help="Comma-separated recipient emails."),
    account: str = typer.Option(None, "--account", help="MailAccount name. Default: the account with is_default=true."),
    cc: str = typer.Option("", "--cc", help="Comma-separated Cc recipients."),
    bcc: str = typer.Option("", "--bcc", help="Comma-separated Bcc recipients."),
    template: str = typer.Option(None, "--template", help="MailTemplate name."),
    context: str = typer.Option(None, "--context", help='JSON object of template variables, e.g. \'{"name": "Ada"}\'.'),
    subject: str = typer.Option(None, "--subject", help="Direct subject (instead of --template)."),
    text: str = typer.Option(None, "--text", help="Direct plain-text body (instead of --template)."),
    html: str = typer.Option(None, "--html", help="Direct HTML body (optional, direct mode only)."),
    attach: str = typer.Option("", "--attach", help="Comma-separated attachments: local paths or http(s):// URLs."),
) -> None:
    """Send a real email through a configured MailAccount — for verifying
    an account/template setup from the command line, without writing a
    throwaway script."""
    attachments = [{"filename": a.rsplit("/", 1)[-1], "source": a, "mimetype": None} for a in _split(attach)]
    parsed_context = json.loads(context) if context else {}

    async def _send() -> None:
        try:
            # Pass whatever the user actually gave straight through, unfiltered
            # — arc.mail.send() itself is the one source of truth for the
            # exactly-one-of-template-or-direct-content rule. Pre-filtering
            # here (e.g. dropping --subject whenever --template was also
            # given) would silently swallow that exact mistake instead of
            # surfacing it.
            await arc.mail.send(
                _split(to),
                account=account,
                cc=_split(cc),
                bcc=_split(bcc),
                attachments=attachments or None,
                template=template,
                context=parsed_context,
                subject=subject,
                text_body=text,
                html_body=html,
            )
        except Exception as exc:
            err_console.print(f"send failed: {exc.__class__.__name__}: {exc}")
            raise typer.Exit(code=1)
        console.print(f"[bold green]Queued/sent to {to}.[/bold green]")

    _run(_send())
