# agentd

**An open-source AI agent for your own computer.** Bring your own model and key. Give it a task in plain words, and it works on your files, runs commands, uses the web and a real browser, and connects to your other tools. It asks before it changes anything.

Runs on Windows and macOS (desktop app) or anywhere Python runs (command line and local API). No account, no subscription to us: you pay only your AI provider, and everything stays on your computer.

> Early version (0.1). Expect rough edges.

---

## What it can do

| | |
| --- | --- |
| **Work on your computer** | List, read, search, create and edit files; run commands; open files and apps. |
| **Use the web** | Search the web, read pages, and drive a real browser window you can watch: click, fill in forms, pick sizes, add to a basket. |
| **Ask before acting** | Writing a file, running a command or signing in pauses on a card with **Allow once / Allow for this task / Deny**. Switch to **Full access** when you trust the task, even mid-task. |
| **Never pay or type secrets on its own** | Buttons that pay or place an order always ask, in every mode. It never types passwords, card numbers or one-time codes; it hands those to you. |
| **Sign in without seeing passwords** | Save a website's login once under **Website passwords**. The agent can sign in with it but never sees it, and only on that website. Optional. |
| **Work as a team** | **Agents** mode: a supervisor splits the task, several agents work at the same time, and a lead writes the answer. You watch each one live. |
| **Run on a schedule** | **Automations** run a task every day, on weekdays, or every few minutes or hours, with one agent or a team. |
| **Connect your tools** | Add any [MCP](https://modelcontextprotocol.io) server (a program or a web address) on the **Tools** page. Built in: GitHub, weather, news, Discord and Slack reading. |
| **Follow instructions you write** | **Skills** are instruction files (`SKILL.md`) the agent loads when a task fits. Included: online shopping and web research. |
| **Answer from your chats** | Message it from **Telegram, Discord or Slack**. Its questions arrive as Approve / Reject buttons. |
| **Remember you** | It keeps short notes about you and your preferences. Anything that looks like a password or key is never saved. |

## Models

Pick a provider in the chat box, paste your API key, and choose from that provider's current models. Each run can use an **effort** level (low, medium, high) to trade speed and cost against quality.

| Provider | Key |
| --- | --- |
| OpenAI | `OPENAI_API_KEY` |
| Google (Gemini) | `GOOGLE_API_KEY` or `GEMINI_API_KEY` |
| Anthropic (Claude) | `ANTHROPIC_API_KEY` |
| xAI (Grok) | `XAI_API_KEY` |
| Also supported | OpenRouter, Groq, DeepSeek, Mistral, Ollama (local, free) |

If a model fails (out of credit, bad key, provider down), the next one in your list takes over.

## Install

You need **Python 3.11+**. For the desktop app you also need **Node.js 20+**.

```bash
git clone https://github.com/Adityakk9031/agentd-desktop
```

```bash
cd agentd-desktop
```

```bash
pip install -e ".[models,browser,channels]"
```

The browser features use Playwright. If you have Edge or Chrome installed, agentd uses it. Otherwise install Playwright's own browser:

```bash
playwright install chromium
```

### Desktop app

```bash
cd desktop
```

```bash
npm install
```

```bash
npm start
```

The app starts the agent in the background on your computer, on a random local port with a random access token, and stops it when you close the window.

### Command line

```bash
python -m agentd doctor --ping
```

```bash
python -m agentd chat "Make a notes.txt on my Desktop with today's date"
```

```bash
python -m agentd chat --team "Compare the top 3 Python web frameworks on GitHub"
```

Add `--yes` to let it act without asking. Paying and handing steps to you still always need a person.

Other commands: `init` (write a starter `agentd.yaml`), `serve` (run the local API and the screens at `http://127.0.0.1:8000/app/`), `mcp` (run the built-in tools as an MCP server), `version`.

## Settings

Put keys in a `.env` file in the folder you run agentd from (see [`.env.example`](.env.example)), or enter them in the app. Useful settings:

| Setting | What it does |
| --- | --- |
| `AGENTD_MODEL_DEFAULT` | Default model, for example `xai:grok-4.6` or `openai:gpt-5.4` |
| `AGENTD_MODEL_DEFAULT_FALLBACKS` | Models to try next, comma-separated |
| `AGENTD_EFFORT` | Default effort: `low`, `medium` or `high` |
| `AGENTD_WORKSPACE` | Where relative paths start (default: your home folder) |
| `PERMISSION_ALLOW` / `PERMISSION_ASK` / `PERMISSION_DENY` | Tool rules, as name patterns like `github_*` |
| `TELEGRAM_BOT_TOKEN`, `DISCORD_BOT_TOKEN`, `SLACK_APP_TOKEN` | Turn on chat channels |

For per-role models (planner, worker, verifier) use `agentd.yaml`; run `python -m agentd init` for a commented example.

## Safety

The rules below are enforced in code, outside the model, so the AI cannot forget or talk its way past them.

- **Local only.** The server listens on `127.0.0.1`. The desktop app adds a random token so other programs on your computer cannot drive it.
- **Ask before changing anything** (unless you choose Full access): files, commands, opening things, signing in, and tools that write.
- **Always ask** for anything that pays, orders or subscribes, for that one click only.
- **Off limits in every mode:**
  - files that hold keys and passwords (`.env`, `.ssh`, browser password stores)
  - system folders
  - destructive commands (formatting drives, deleting your home folder, changing user accounts or security settings)
  - pages on your computer or local network
- **Nothing it reads can give it orders.** Web pages, files and messages are treated as information.
- **Unattended automations** may read and look things up, but anything that would change your computer is declined and reported.
- **Audit log** of what it did, in `data/audit.db`.

These are guard rails, not a sandbox: a command you approve runs with your own rights.

## Your data

Everything stays in the `data/` folder where agentd runs:
- approvals and automations (SQLite)
- the audit log
- the agent's notes about you
- saved website logins (encrypted)
- the browser profile
- your MCP connections

What you ask goes straight from your computer to the AI provider you chose. Web searches go to DuckDuckGo.

## Updates

The app checks this repository's GitHub releases. When a newer version is out, it shows the release notes with **Update now**, installs it and restarts. Nothing is downloaded until you press the button. Running from a clone? Update with `git pull`.

To publish a release: set `__version__` in `agentd/__init__.py`, then push a matching tag (for example `v0.2.0`). The release workflow runs the tests, builds the package and creates the GitHub release.

## Development

```bash
pip install -e ".[dev,models,browser,channels]"
```

```bash
python -m pytest
```

```bash
cd desktop && npm run e2e
```

`npm run e2e` drives the real app with your configured model: chat, an approval card, stopping a task, an agent team, an automation and the Tools page.

Layout:

```
agentd/
  runtime/    the agent loop, agent teams, skills
  computer/   file, command, web and browser tools, hard safety rules
  safety/     approval gate, permissions, login vault, audit log
  models/     model choice, providers, fallbacks, effort
  tools/      built-in MCP server and tools, your MCP connections
  channels/   Telegram, Discord, Slack
  services/   automations, task runner, approval queue
  server/     local API and the app's screens (ui/)
  skills/     bundled SKILL.md files
desktop/      Electron app
tests/
```

See [`plan.md`](plan.md) for where the project is going.

## Credits

Parts of the design follow [OpenClaw](https://github.com/openclaw/openclaw) (MIT): the browser snapshot view, skills, MCP connections and the loop guard. See [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).

## License

[Apache License 2.0](LICENSE).
