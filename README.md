# mail

Multi-account, multi-provider email delivery for ARC — Jinja2-templated
or direct content, `to`/`cc`/`bcc`, and attachments (by reference, never
inline bytes). One call site: `arc.mail.send(...)`.

```
mail/
├── plugin.toml
├── pyproject.toml
├── README.md              # this file
└── mail/
    ├── __init__.py          # register() + MailProvider (arc.mail)
    ├── providers.py         # Provider protocol + registry, Message/Attachment
    ├── _delivery.py         # the queued worker function (deliver_email)
    ├── cli.py                # `arc mail send-test`
    └── schemas/
        ├── MailAccount.json
        └── MailTemplate.json
```

## How it fits together

- `plugin.toml`: `requires = ["pgdb", "relay"]`, `optional_requires = ["lineup"]`.
- **Accounts and templates are ordinary declared schemas** (`MailAccount`,
  `MailTemplate`) — both are already full CRUD in the admin plugin's
  Data Browser. There is no bespoke account/template management API or
  CLI in this plugin; managing them means opening `/admin-desk` → Data
  Browser → plugin `mail` → table `mailaccount` / `mailtemplate`.
- **Delivery is queued** through `arc.relay.enqueue()` — durable via
  `lineup` when it's installed (a real `arc lineup worker` process has
  to be running to actually send anything), or an honest in-process
  fallback when it isn't. `arc.mail.send()` awaits the result either
  way, so a call to it only returns once the message has actually been
  handed off (durable path) or actually sent (fallback path) — never a
  silently-detached background task.
- **Credentials are never stored by this plugin.** `MailAccount.secret_ref`
  only names a key; the real value lives in `arc.settings`' encrypted
  secret store (the same one every other credential in this project
  uses), set via `arc settings set <key> <value> --secret` or the
  admin plugin's Settings & Secrets screen.

## 1. Configuring an account

Create a row in `MailAccount` (via the Data Browser, or `arc.relay.save`
from a script/migration):

| field | meaning |
|---|---|
| `name` | unique key you pass as `arc.mail.send(..., account="billing")`. Omit `account` on `send()` to use whichever row has `is_default = true`. |
| `provider` | `"smtp"` today — see [Providers](#providers) below. |
| `from_email` / `from_name` | the message's `From:` header. |
| `config` | provider-specific settings (JSON). For `smtp`: `host`, `port`, `username`, `starttls` (bool, default `true`), `use_tls` (bool, default `false`). |
| `secret_ref` | the name of a settings key holding the credential (the SMTP password) — leave empty for an unauthenticated relay. |
| `is_default` | at most one account should have this `true`. |
| `enabled` | a disabled account is refused at send time, not silently skipped. |

Example, via the CLI/`arc` shell (or just fill the same fields in the
Data Browser's "New row" form):

```python
await arc.relay.save(
    "mailaccount",
    {
        "name": "transactional",
        "provider": "smtp",
        "from_email": "noreply@yourdomain.com",
        "from_name": "Your App",
        "config": {
            "host": "smtp.yourprovider.com",
            "port": 587,
            "username": "apikey",
            "starttls": True,
        },
        "secret_ref": "mail.transactional.password",
        "is_default": True,
        "enabled": True,
    },
)
```

Then set the actual credential — **never put it in `config`**:

```bash
arc settings set mail.transactional.password "the-real-smtp-password" --secret
```

(or Settings & Secrets in the admin UI, using the same key name as `secret_ref`.)

## 2. Registering a template

Create a row in `MailTemplate`:

| field | meaning |
|---|---|
| `name` | unique key you pass as `arc.mail.send(..., template="welcome")`. |
| `subject` | a Jinja2 template string. |
| `text_body` | a Jinja2 template string — required. |
| `html_body` | a Jinja2 template string — optional; omit for a plain-text-only email. |
| `sample_context` | optional JSON — pure documentation (never enforced or auto-filled), so whoever edits this row later knows what variables the template expects. There's no live preview, so this is the only record of that. |

Example:

```python
await arc.relay.save(
    "mailtemplate",
    {
        "name": "welcome",
        "subject": "Welcome, {{ name }}!",
        "text_body": "Hi {{ name }},\n\nYour account {{ email }} is ready. Roles: {{ roles | join(', ') }}.",
        "html_body": "<p>Hi <b>{{ name }}</b>,</p><p>Your account {{ email }} is ready.</p>",
        "sample_context": {
            "name": "Ada",
            "email": "ada@example.com",
            "roles": ["admin", "billing"],
        },
    },
)
```

Full Jinja2 syntax is available — conditionals, loops, filters
(`{% if %}`, `{% for %}`, `| join`, `| upper`, ...). `subject`/`text_body`/
`html_body` are rendered independently, each with the same `context` dict.

Templates are re-fetched and re-rendered on every send (not cached at
boot) — editing a template takes effect on the very next `send()` call
that references it, no restart needed.

## 3. Sending mail — `arc.mail.send()`

```python
async def send(
    self,
    to: list[str],
    *,
    account: str | None = None,
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
    attachments: list[dict[str, str]] | None = None,
    # exactly one of these two groups:
    subject: str | None = None,
    text_body: str | None = None,
    html_body: str | None = None,
    template: str | None = None,
    context: dict[str, Any] | None = None,
) -> None
```

`template` and direct content (`subject`/`text_body`/`html_body`) are
**mutually exclusive** — passing neither or both raises `ValueError`
immediately, before anything is queued.

### Template mode

```python
await arc.mail.send(
    ["ada@example.com"],
    template="welcome",
    context={"name": "Ada", "email": "ada@example.com", "roles": ["admin", "billing"]},
)
```

### Direct mode (no template needed)

```python
await arc.mail.send(
    ["ada@example.com"],
    subject="One-off notice",
    text_body="This didn't need a saved template.",
)
```

`context` only applies to `template` mode. If you want variables in a
one-off direct email, interpolate them yourself (an f-string) before
calling `send()` — direct mode never runs Jinja2.

### cc / bcc / a specific account

```python
await arc.mail.send(
    ["ada@example.com"],
    cc=["manager@example.com"],
    bcc=["audit@example.com"],
    account="transactional",  # omit to use the is_default=true account
    template="welcome",
    context={"name": "Ada", "email": "ada@example.com", "roles": ["admin"]},
)
```

### Attachments — by reference, not by value

```python
await arc.mail.send(
    ["ada@example.com"],
    subject="Your invoice",
    text_body="Attached.",
    attachments=[
        {
            "filename": "invoice.pdf",
            "source": "/var/data/invoices/inv-042.pdf",
            "mimetype": "application/pdf",
        },
        {"filename": "receipt.pdf", "source": "https://files.example.com/receipts/042.pdf"},
    ],
)
```

Each attachment is `{"filename": ..., "source": ..., "mimetype": ...}`
— `mimetype` is optional (guessed from the filename if omitted).
`source` is either:
- a **local filesystem path** — checked to exist right here, at
  `send()`-call time (fails fast with `FileNotFoundError` before
  anything is queued), then actually read later by whichever process
  ends up delivering the message.
- an **`http(s)://` URL** — not checked eagerly (no network call in
  the request path); fetched later, at send time.

**Only this small reference dict crosses the queue — never the file's
actual bytes.** The worker process resolves real content exactly once,
right before sending. This means a **local-path attachment requires the
worker to be able to read that same path** — same host, or a shared
volume. If your worker runs somewhere else, use a URL instead.

## 4. Actually delivering queued mail

If `lineup` is installed, `send()` hands the job to a durable Redis
queue named `"mail"` — **nothing is sent until a worker consumes it**:

```bash
arc lineup worker --queues=mail
```

Run that as a long-lived process (systemd unit, supervisor, etc.) in
any real deployment. Without a worker running, messages sit queued
indefinitely — they are not lost, but they are not sent either.

If `lineup` is *not* installed, `send()` sends inline, synchronously,
in whatever process called it — no separate worker needed, but no
durability either (a crash between enqueue and delivery loses the
message, same as `arc.relay.enqueue()`'s own documented fallback
behavior in general).

## 5. CLI — `arc mail send-test`

For verifying an account/template setup without writing a script.
Multi-value options are comma-separated (`--to`, `--cc`, `--bcc`,
`--attach`), matching this project's other CLI commands.

```bash
# templated
arc mail send-test --to ada@example.com --template welcome \
  --context '{"name": "Ada", "email": "ada@example.com", "roles": ["admin"]}'

# direct content, explicit account, cc/bcc, an attachment
arc mail send-test --to ada@example.com --cc manager@example.com --bcc audit@example.com \
  --account transactional --subject "Direct test" --text "Hello" \
  --attach /tmp/note.txt,https://example.com/file.pdf
```

Remember: if `lineup` is installed, `send-test` only queues the
message — run `arc lineup worker --queues=mail` (in another terminal,
or briefly in the foreground) to actually see it delivered.

## Providers

Only `smtp` ships today. The `Provider` protocol + `PROVIDERS` registry
in `mail/providers.py` is the extensibility point — adding a second
provider is:

1. Add a class implementing `async def send(self, message, *, from_email, from_name, config, credential) -> None`.
2. Register it: `PROVIDERS["sendgrid"] = SendGridProvider()`.
3. Add `"sendgrid"` to `MailAccount.provider`'s SELECT options
   (`mail/schemas/MailAccount.json`) and run `arc pgdb plan` / `migrate`.

No changes needed anywhere else — `_delivery.py` looks the provider up
by `account["provider"]` and calls it generically.

## Known limitations (deliberate, not oversights)

- No delivery log / admin UI page yet (mirrors `relay`'s `_job_log`
  pattern — straightforward to add later, not built because it wasn't
  asked for).
- No bounce/complaint handling, no retry beyond whatever `lineup`'s
  queue already gives for free.
- A local-path attachment assumes the worker can read that exact path
  — see [Attachments](#attachments--by-reference-not-by-value) above.
- Account/template management has no dedicated API or CLI — it's the
  Data Browser, on purpose (see [How it fits together](#how-it-fits-together)).
