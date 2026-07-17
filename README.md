# forestfire

An async Python framework for running payments-enabled chat and utility bots on
[Signal](https://signal.org/). Subclass a bot, name a method `do_hello`, and
`/hello` becomes a command.

```python
from forest.core import Bot, Message, run_bot

class HelloBot(Bot):
    async def do_hello(self, message: Message) -> str:
        return "hello, world!"

if __name__ == "__main__":
    run_bot(HelloBot)
```

forestfire is the continuation of the MobileCoin-era
[forest](https://github.com/mobilecoinofficial/forest) framework, revived and
modernized in 2026. The pre-fork history is preserved on the tags
`mobilecoin-era-final` and `firedex-main-archive`.

## How it works

- **[signal-cli](https://github.com/AsamK/signal-cli)** runs as a child process
  in `jsonRpc` mode; the framework reads and writes newline-delimited JSON-RPC
  over its stdio. No other Signal backend is supported.
- **Users are uuids.** Phone numbers are never used as identities — not for
  recipients, admins, mentions, or stored state. The only phone number in the
  system is the bot's own signal-cli account id (`BOT_NUMBER`).
  `Message.source` still exists as a deprecated alias of `Message.uuid`.
- **Class hierarchy**: `Signal` (process + I/O) → `Bot` (command dispatch) →
  `ExtrasBot` → `PayBot` (MobileCoin payments via
  [full-service](https://github.com/mobilecoinofficial/full-service) v2) →
  `QuestionBot` (interactive `ask_*` flows: freeform, yes/no, int, float,
  multiple choice, email, address).
- **Persistence** is `forest.pdictng.aPersistDict`: an async dict backed by a
  strongly-consistent KV store (a Deno openKV worker). Values are AES-EAX
  encrypted and keys salted-hashed before leaving the process. One writer
  process per `NAMESPACE`, enforced by a lease (`NAMESPACE_CLAIM`) in the
  backend — multi-writer is unsupported.
- **Signal keystate** (the signal-cli sqlite database) is replicated with
  [litestream](https://litestream.io/); the account file is backed up to the KV
  store. See `litestream_template.yml`.
- An **aiohttp app** on port 8080 serves `/health` and `/ready` (k8s-style
  probes that check the signal-cli child process) plus optional webhooks.

## Quickstart

Requirements: Python ≥3.11 (3.13 recommended), a
[signal-cli](https://github.com/AsamK/signal-cli) binary (needs Java 21+), and
a Signal account for the bot (see the
[signal-cli registration wiki](https://github.com/AsamK/signal-cli/wiki/Registration-with-captcha)).

```bash
pip install -e .          # or: pip install -e . --group dev  (adds test/lint tools)
```

Create a `.env` (never commit it):

```bash
BOT_NUMBER=+15551234567                  # the bot's signal-cli account
ADMIN=00000000-0000-0000-0000-000000000000   # your signal uuid (not your phone number!)
# generate each of these with: cat /dev/urandom | head -c 32 | base58
AESKEY=...    # encrypts persisted values
SALT=...      # salts hashed keys
NAMESPACE=my-bot-prod   # persistence namespace; must be STABLE across deploys
PAUTH=...     # auth token for the KV backend
PURL=https://your-kv-worker.example.com
```

A minimal bot needs only `BOT_NUMBER` and `ADMIN`. The persistence secrets
(`AESKEY`, `SALT`, `NAMESPACE`, `PAUTH`) are validated **lazily** — they're
required the first time the bot encrypts or stores state, not at startup, so a
stateless bot doesn't need them. When `RESTORE=1` (keystate backup is on),
they're validated eagerly at startup instead, so a misconfigured persistent bot
fails on boot rather than on its first write. When persistence *is* used, the
framework **fails closed**: there are no insecure defaults to forget to override.

Run it:

```bash
python hellobot.py
```

Text your bot `/hello`, `/ping`, or `/help`.

## Environment variables

Always required:

| var | meaning |
|---|---|
| `BOT_NUMBER` | the bot's own signal-cli account (E164) |

Required only when the bot persists state (validated on first use):

| var | meaning |
|---|---|
| `AESKEY`, `SALT` | encryption key + hash salt for persisted state (base58) |
| `NAMESPACE` | persistence namespace; stable across deploys, one writer at a time |
| `PAUTH`, `PURL` | auth + URL for the KV persistence backend |

Common:

| var | meaning |
|---|---|
| `ADMIN`, `ADMINS` | uuids allowed to run `@requires_admin` commands |
| `ADMIN_GROUP` | group id whose members all get admin |
| `ENV` | which `{ENV}_secrets` file to load; falls back to `.env` |
| `SIGNAL_PATH` | path to the signal-cli executable if not in `ROOT_DIR`/PATH |
| `LOGLEVEL` | console log level (default DEBUG); logs are JSON |
| `FULL_SERVICE_URL` | full-service instance for payments |

Feature gates (off by default):

| var | enables |
|---|---|
| `ENABLE_WEBHOOKS` | `POST /user/{recipient}`, `POST /admin`, `POST /restart` — unauthenticated, so also network-restrict them |
| `ENABLE_EVAL` | the admin-only `/eval` debugging command (executes Python in-process) |
| `ENABLE_MAGIC` | fuzzy matching of mistyped commands |
| `RESTORE` | litestream keystate restore/replication on startup |
| `NAMESPACE_CLAIM_TTL` | seconds before a dead writer's namespace claim expires (default 60) |

## Development

```bash
pip install -e . --group dev
pytest                     # 82 tests, no network or signal-cli needed
black . && mypy forest mc_util hellobot.py && pylint forest mc_util
```

CI runs exactly those four steps on every push, with unpinned latest tools —
lint churn is absorbed as it lands. Generated `*_pb2.py` files are excluded
from formatting and linting.

Style: black, verbose names over concise ones, one commit per fix.

## License

MIT. See [LICENSE](LICENSE) and [CHANGELOG.md](CHANGELOG.md).
