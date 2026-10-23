# Security policy

## Secrets

- All secrets live in `.env` (git-ignored, created with mode 600 by the installer).
- The Telegram client never logs request URLs (they contain the bot token), and the
  daemon lowers `httpx`/`httpcore` logging to WARNING for the same reason.
- CI runs [gitleaks](https://github.com/gitleaks/gitleaks) on every push.

## Untrusted input

News headlines are attacker-controlled text. Before they reach the model they are
NFKC-normalised, stripped of markup and control characters, length-capped, screened
for prompt-injection phrases (matches are dropped and counted) and fenced inside
`<<<UNTRUSTED_ARTICLE>>>` markers. The risk desk that gates every order is
deterministic code and is never delegated to the LLM.

## Who can trade

Only Telegram user ids in `TELEGRAM_OPERATOR_IDS` can send commands or press
YES / NO / LATE. Everyone else is ignored (commands) or refused (buttons), also in groups.

## Reporting

Please report vulnerabilities through a private GitHub security advisory on this
repository rather than a public issue.
