# Otter CLI Command Reference

Follow [the installation guide](installation.md) when the `otter` executable is unavailable.
Run `otter --help` for the installed command list.

## Authorization

Set `OTTER_URL` to override the Otter server URL or omit it to use `https://otter.narumi.dev/`.
Run `otter auth login` to start device authorization, open Otter, and wait for the user to approve the displayed code.
If a saved token already exists for that server, login revokes it before requesting a replacement and preserves it if revocation fails.
Add `--no-open` when the browser must be opened manually, and use `--client-name <name>` to identify the requesting agent or machine.
The CLI stores the issued token by server URL in `~/.config/otter/credentials.json` with mode `0600`.
Set `OTTER_CONFIG_PATH` only when a different credential file is required.
Set `OTTER_TOKEN` from a secret manager for ephemeral agent or CI use without persistence.
Run `otter auth status` to verify authorization.
Run `otter auth logout` to revoke the current stored token and remove it locally.
Never ask the user for their Otter password or print an access token.
Remote HTTP is rejected unless `OTTER_ALLOW_INSECURE_HTTP=1` is explicitly set.

## Read Commands

```bash
otter me
otter trips list
otter trips get --trip <trip-id>
otter participants list --trip <trip-id>
otter expenses list --trip <trip-id>
otter balances get --trip <trip-id>
otter settlements list --trip <trip-id>
```

`trips get` returns the complete trip payload.
`balances get` returns current balances and suggested settlements.
`settlements list` returns suggested settlements and recorded payments.

## Trip Commands

```bash
otter trips create --name <name> [--currency TWD]
otter trips update --trip <trip-id> [--name <name>] [--archived true|false]
otter trips delete --trip <trip-id> --yes
```

Supported currencies for new trips are `TWD`, `JPY`, `USD`, and `EUR`.
Changing a trip's base currency and exchange rates is not exposed because it can invalidate existing rate configuration; use the browser settings flow.
Only run `trips delete` after explicit approval because it deletes the whole group and its records.

## Participant Commands

```bash
otter participants add --trip <trip-id> --name <name>
otter participants rename --trip <trip-id> --participant <participant-id> --name <name>
otter participants delete --trip <trip-id> --participant <participant-id> --yes
```

Otter rejects deletion when the participant is used by an expense or payment record.

## Expense Commands

```bash
otter expenses add \
  --trip <trip-id> \
  --description <text> \
  --amount <major-unit-amount> \
  --currency <code> \
  --paid-by <participant-id> \
  --split-with <participant-id,participant-id> \
  [--date YYYY-MM-DD] \
  [--category <name>] \
  [--tags <tag,tag>]

otter expenses update \
  --trip <trip-id> \
  --expense <expense-id> \
  [--description <text>] \
  [--amount <major-unit-amount>] \
  [--currency <code>] \
  [--paid-by <participant-id>] \
  [--split-with <participant-id,participant-id>] \
  [--date YYYY-MM-DD] \
  [--category <name>] \
  [--tags <tag,tag>]

otter expenses delete --trip <trip-id> --expense <expense-id> --yes
```

The available categories are `餐飲`, `交通`, `住宿`, `門票`, `購物`, and `其他`.
Omit optional add fields to use server defaults.
Pass `--tags ""` on update to clear all tags.
The CLI creates equal splits and does not expose custom amounts, percentages, or shares.

## Settlement Commands

```bash
otter settlements record \
  --trip <trip-id> \
  --from <participant-id> \
  --to <participant-id> \
  --amount <major-unit-amount> \
  [--currency <code>] \
  [--date YYYY-MM-DD] \
  [--note <text>]

otter settlements delete --trip <trip-id> --payment <payment-id> --yes
```

Omitting settlement currency uses the trip base currency.
Recording a settlement is a write, not a transfer of real funds.

## Output and Errors

Successful data commands write JSON to stdout.
Errors write an object shaped as `{"error":{"code":"...","message":"...","status":400}}` to stderr and exit non-zero.
The `status` field appears only for HTTP response errors.
Treat `CONFIG_ERROR`, `INSECURE_HTTP`, `AUTH_ERROR`, `CONNECTION_ERROR`, and HTTP `401` or `403` as blockers rather than retryable failures.
