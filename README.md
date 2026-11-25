# agentd

**An open-source AI agent for your own computer.** Bring your own model and key. Give it a task in plain words, and it works on your files, runs commands, uses the web and a real browser, works inside your desktop apps, and connects to your other tools. It asks before it changes anything.

A desktop app for Windows, and a command line and local API wherever Python runs. No account, no login, no subscription to us: you pay only your AI provider, and everything stays on your computer.

> **Version 1.0.0.** Windows is fully supported, including control of desktop apps. On macOS and Linux everything except desktop-app control works from source; there is no installer for them yet.

---

## What it can do

| | |
| --- | --- |
| **Work on your computer** | List, read, search, create and edit files; run commands; open files and apps. |
| **Use any desktop app** | Notepad, Excel, Paint, Settings, any Windows program. It reads the window as a list of buttons and boxes and clicks and types like a person. For apps with nothing to read (canvases, games, remote desktops) it works from screenshots with a position grid, and can zoom in before small clicks. An orange frame shows which window it is using. |
| **Use the web** | Search the web, read pages, and drive a real browser window you can watch: click, fill in forms, pick sizes, add to a basket. It warns you about look-alike sites such as `amaz0n.com`. |
| **Ask before acting** | Three levels, changeable mid-task: **Ask before acting** (a card with Allow once / Allow for this task / Deny), **Approve routine actions** (a second AI check approves everyday actions and asks you about the rest), and **Full access**. |
| **Never pay or type secrets on its own** | Buttons that pay or place an order always ask, at every level. It never types passwords, card numbers or one-time codes; it hands those to you. |
| **Sign in without seeing passwords** | Save a website's login once under **Website passwords**. The agent can sign in with it but never sees it, and only on that website. |
| **Work as a team** | **Agents** mode: a supervisor splits the task, several agents work at the same time, and a lead writes the answer. You watch each one live. |
| **Run on a schedule** | **Automations** run a task every day, on weekdays, or every few minutes or hours, with one agent or a team, each with its own model and effort. |
| **Remember you** | It keeps notes about you, and before each task it looks up the notes and past conversations that match. Ask it to search or forget. Anything that looks like a password or key is never saved. |
| **Learn your tasks** | **Skills** are instruction files (`SKILL.md`) the agent loads when a task fits. After a task with several steps, press **Save as skill** and it writes one from what it really did. Included: online shopping, web research, desktop apps. |
| **Keep its place in long tasks** | Every step is kept in a task journal (files, browser, desktop, services, and what each touched), so follow-ups know what was done. In long tasks old results are cleared to save space, and facts it needs are kept as task notes. |
| **Connect your tools** | Add any [MCP](https://modelcontextprotocol.io) server (a program or a web address) on the **Tools** page. Built in: GitHub, weather, news, Discord and Slack reading. |
| **Answer from your chats** | Message it from **Telegram, Discord or Slack**. Its questions arrive as Approve / Reject buttons. |

## Models

Pick a provider in the chat box, paste your API key, and choose from that provider's current models. The key is checked with the provider, then stored in your system's keychain (Windows Credential Manager or macOS Keychain), not in a text file. Each run can use an **effort** level (low, medium, high) to trade speed and cost against quality.

| Provider | Key |
| --- | --- |
| OpenAI | `OPENAI_API_KEY` |
| Google (Gemini) | `GOOGLE_API_KEY` or `GEMINI_API_KEY` |
| Anthropic (Claude) | `ANTHROPIC_API_KEY` |
| xAI (Grok) | `XAI_API_KEY` |
| Also supported | OpenRouter, Groq, DeepSeek, Mistral, Ollama (local, free) |

If a model fails (out of credit, bad key, provider down), the next one in your list takes over. Desktop-app screenshots need a model that reads images; the big four all do.

## Install

### Windows app (no Python needed)

Download `agentd Setup 1.0.0.exe` from [Releases](https://github.com/Adityakk9031/agentd-desktop/releases) and run it. The app is not code-signed yet, so Windows SmartScreen warns the first time: choose **More info**, then **Run anyway**. By default it installs for your user only, without administrator rights.

Or build the installer yourself (Python 3.11+ and Node.js 20+):

```bash
python desktop/build_runtime.py
```

```bash
cd desktop && npm install && npx electron-builder --win nsis
```

The installer appears in `desktop/dist/`.

### From source (Windows, macOS, Linux)

You need **Python 3.11+**. For the desktop app you also need **Node.js 20+**.

```bash
git clone https://github.com/Adityakk9031/agentd-desktop
```

```bash
cd agentd-desktop
```

```bash
pip install -e ".[models,browser,desktop,channels]"
```

The browser features use Playwright. If you have Edge or Chrome installed, agentd uses it. Otherwise install Playwright's own browser:

```bash
playwright install chromium
```

Desktop app:

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

Enter keys in the app, or put them in a `.env` file in the folder you run agentd from (see [`.env.example`](.env.example)); a key in `.env` wins over the keychain. The installed app keeps its files in `%APPDATA%\agentd`. Useful settings:

| Setting | What it does |
| --- | --- |
| `AGENTD_MODEL_DEFAULT` | Default model, for example `xai:grok-4.6` or `openai:gpt-5.4` |
| `AGENTD_MODEL_DEFAULT_FALLBACKS` | Models to try next, comma-separated |
| `AGENTD_EFFORT` | Default effort: `low`, `medium` or `high` |
| `AGENTD_WORKSPACE` | Where relative paths start (default: your home folder) |
| `PERMISSION_ALLOW` / `PERMISSION_ASK` / `PERMISSION_DENY` | Tool rules, as name patterns like `github_*` |
| `TELEGRAM_BOT_TOKEN`, `DISCORD_BOT_TOKEN`, `SLACK_APP_TOKEN` | Turn on chat channels |
| `AGENTD_KEYCHAIN=0` | Keep API keys in `.env` instead of the keychain |
| `AGENTD_AUTO_RECALL=0` | Do not look up notes and past conversations before each task |
| `AGENTD_DESKTOP_SCREENSHOTS=0` | No screenshots of desktop apps (for models that cannot read images) |
| `AGENTD_CONTROL_FRAME=0` | No orange frame around the window the agent uses |

For per-role models (planner, worker, verifier) use `agentd.yaml`; run `python -m agentd init` for a commented example.

## Safety

The rules below are enforced in code, outside the model, so the AI cannot forget or talk its way past them.

- **Local only.** The server listens on `127.0.0.1`. The desktop app adds a random token so other programs on your computer cannot drive it.
- **Ask before changing anything** (unless you choose Full access): files, commands, opening things, desktop clicks and typing, signing in, and tools that write.
- **Approve routine actions** only lets the second AI check approve actions that stay on your computer (files, commands, opening things, desktop apps), and only when it clearly says yes. Anything else, or any error, still asks you. Sign-ins, connected services, payments and hand-overs always ask.
- **Always ask** for anything that pays, orders or subscribes, for that one click only.
- **Off limits in every mode:**
  - files that hold keys and passwords (`.env`, `.ssh`, browser password stores)
  - system folders
  - destructive commands (formatting drives, deleting your home folder, changing user accounts or security settings)
  - pages on your computer or local network
  - agentd's own window, password managers and Windows security tools
  - typing into password boxes, in the browser or in desktop apps
- **Nothing it reads can give it orders.** Web pages, files, messages and its own recalled notes are treated as information.
- **Unattended automations** may read and look things up. Anything that would change your computer is declined and reported, unless you let that automation make routine changes (checked by the second AI). Full access is never offered to an automation.
- **Audit log** of every approval and what it did, in `data/audit.db`.

These are guard rails, not a sandbox: a command you approve runs with your own rights.

## Your data

Everything stays in the `data/` folder where agentd runs (`%APPDATA%\agentd\data` for the installed app):
- approvals and automations (SQLite)
- the audit log
- the agent's notes about you (`memory.md`) and past tasks (`conversations.jsonl`, secrets removed)
- your own skills
- saved website logins (encrypted; the key is in your system keychain)
- the browser profile
- your MCP connections

API keys are in your system keychain. What you ask goes straight from your computer to the AI provider you chose. Web searches go to DuckDuckGo.

## Updates

The app checks this repository's GitHub releases. When a newer version is out, it shows the release notes with **Update now**, installs it and restarts. Nothing is downloaded until you press the button. Running from a clone? Update with `git pull`.

To publish a release: set the same version in `agentd/__init__.py` and `desktop/package.json`, then push a matching tag (for example `v1.1.0`). The release workflow runs the tests, builds the package and the Windows installer, and creates the GitHub release.

## Development

```bash
pip install -e ".[dev,models,browser,desktop,channels]"
```

```bash
python -m pytest
```

```bash
cd desktop && npm run e2e
```

`npm run e2e` drives the real app with your configured model: chat, an approval card, a follow-up, stopping a task, an agent team, recents, an automation and the Tools page. `npm run smoke` checks the app starts and its screens work, without a model. [`docs/v1-test-checklist.md`](docs/v1-test-checklist.md) lists what to try by hand.

Layout:

```
agentd/
  runtime/    the agent loop, agent teams, skills, recall, task journal
  computer/   file, command, web, browser and desktop-app tools, hard safety rules
  safety/     approval gate and reviewer, permissions, keys, login vault, look-alike sites, audit log
  models/     model choice, providers, fallbacks, effort
  tools/      built-in MCP server and tools, your MCP connections
  channels/   Telegram, Discord, Slack
  services/   automations, task runner, approval queue
  server/     local API and the app's screens (ui/)
  skills/     bundled SKILL.md files
desktop/      Electron app and installer build
docs/         test checklist
tests/
```

See [`plan.md`](plan.md) for what is done and where the project is going.

## Credits

The design borrows ideas from other open-source projects; no code was copied:

- [OpenClaw](https://github.com/openclaw/openclaw) (MIT): the browser snapshot view, skills, MCP connections and the loop guard.
- [Operator-Use](https://github.com/CursorTouch/Operator-Use) and [UFO](https://github.com/microsoft/UFO) (MIT): desktop windows as lists of controls.
- [UI-TARS](https://github.com/bytedance/UI-TARS-desktop) and [Agent-S](https://github.com/simular-ai/Agent-S) (Apache-2.0): acting on screenshot positions.
- [Windows-MCP](https://github.com/CursorTouch/Windows-MCP) (MIT): the control frame.
- [OpenMausBot](https://github.com/milind-soni/OpenMausBot) (Apache-2.0) and [Hermes Agent](https://github.com/NousResearch/hermes-agent) (MIT): the reviewer approvals, recall and learned skills.
- "Following the Agent" (Agentrail Research, 2026): the task journal and look-alike warnings.

Desktop control uses [uiautomation](https://github.com/yinkaisheng/Python-UIAutomation-for-Windows) (Apache-2.0). See [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).

## License

[Apache License 2.0](LICENSE).
