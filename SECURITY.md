# Security & Secret Hygiene

This project is **built in public**. There are **no secrets in the repo, ever**.
Everything sensitive lives in environment variables loaded from a local, gitignored
`.env`. See `.env.example` for the full list with fake values.

## How secrets are kept out

- **`.env` is gitignored**; only `.env.example` (fake values) is committed.
- **Pydantic `SecretStr`** wraps every credential (`src/gamer/config.py`) so it
  never renders in logs, `repr()`, or tracebacks. Never log a whole `Settings`.
- **gitleaks** runs three ways:
  - **pre-commit hook** — blocks a commit that contains a secret.
    On first install, scan all history: `pre-commit run gitleaks --all-files`.
  - **CI job** — `.github/workflows/ci.yml` runs gitleaks on **full history**
    (`fetch-depth: 0`) and fails the build on any finding.
  - Custom rules for Steam keys and Telegram tokens live in `.gitleaks.toml`.
- **GitHub push protection + secret scanning** — enable in repo settings:
  *Settings → Code security and analysis → Secret scanning → Push protection*.

## First-time setup

```bash
uv sync --dev
uv run pre-commit install                 # installs the git hook
uv run pre-commit run gitleaks --all-files # baseline full-history scan
cp .env.example .env                       # then fill in real values
```

## Secrets used (all free-tier, all rotatable)

| Secret | Where to get it | Rotation |
|---|---|---|
| `GAMER_STEAM__API_KEY` | https://steamcommunity.com/dev/apikey | Revoke + regenerate on that page; update `.env`; restart app. |
| `GAMER_TELEGRAM__BOT_TOKEN` | @BotFather → `/newbot` | @BotFather → `/revoke`; issues a new token; update `.env`. |
| `GAMER_TWITCH__CLIENT_ID` / `_SECRET` | https://dev.twitch.tv/console/apps | "New Secret" in the app console; update `.env`. |
| `GAMER_DB__PASSWORD` | Local only (compose) | Change in `.env`, recreate the Postgres volume or `ALTER ROLE`. |

**If a secret is ever committed:** rotate it immediately (assume it is burned —
git history and CI logs are public), then purge from history and force-push.
Rotation always comes first; scrubbing history second.

## Reporting a vulnerability

Open a private security advisory on GitHub (*Security → Advisories → Report a
vulnerability*) rather than a public issue.
