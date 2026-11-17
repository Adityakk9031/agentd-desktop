# Plan — from "AI Ops Assistant" to an open-source agent runtime

Status: proposal, nothing built yet. Written 2026-11-15.
Working name: **TBD** (placeholder in this file: `agentd`).

---

## 1. What we are building

An open-source **AI agent for your computer, with a desktop app**. Think of a
Grok / ChatGPT-style assistant, except that it is yours: you bring the model and
the key, it connects to anything, and it can actually carry out tasks on your
machine and in your tools instead of only answering.

Two parts, one product:

- **The desktop app** — a polished app for Windows, macOS and Linux: chat,
  watch the agent work, approve actions, and manage models, keys and
  connections from settings screens (no config files needed).
- **The runtime underneath** — the solid infrastructure. It runs locally inside
  the app by default, and the same runtime can run headless on a server, in
  Docker or in the cloud, with the desktop app connecting to it remotely.

- **Any LLM.** Paste a key, pick a model per role (planner, worker, verifier,
  embeddings). OpenAI, Anthropic, Gemini, Groq, Mistral, OpenRouter, or a local
  model through Ollama / vLLM.
- **Any tool.** Tools are MCP servers. Add one by adding a few lines of config;
  no code changes in the runtime.
- **Memory stays on your disk.** The vector database is local storage on the
  user's own machine (decided 2026-11-16): an embedded store (Chroma) in the
  app's data folder, no account and no cloud. Pinecone / Qdrant / pgvector
  remain optional for server installs. For full privacy the embeddings can also
  be computed locally (Ollama or a small bundled model), so remembered content
  never leaves the computer.
- **Full control, when you allow it.** Agents can work with files, run
  commands, drive a browser and operate apps on the computer, as well as use
  connected services.
- **Any door.** Desktop app first; also HTTP API, CLI, Telegram, Discord, Slack.
  Same agent behind all.
- **Safe by default.** Permission modes, human approval that really pauses the
  run, a full audit trail, keys stored in the OS keychain.
- **No Jev.** The Typesafe/Jev dependency is removed completely.

Same stack as today for the runtime: Python, FastAPI, LangChain, LangGraph, MCP,
vector DB. The desktop app is a new layer on top.

One-line pitch: *"The open-source AI agent for your computer — bring your own
model, connect anything, and let it do the work."*

## 2. Where this fits (research summary)

| Project | What it is | Why it leaves room for us |
|---|---|---|
| Dify | Visual LLM app platform | Modified Apache licence (source-available); UI-first, not code-first |
| n8n | General automation with AI nodes | Fair-code licence, commercial limits; agents are an add-on |
| Flowise / Langflow | Visual flow builders | Drag-and-drop focus; Flowise's maintenance status is disputed across sources — verify |
| LangGraph (library) | Code-first agent framework, MIT | A library, not a server: no install-and-run product, no channels, no tool admin |
| OpenHands | Autonomous coding agent, MIT | Coding only, not general orchestration |
| Letta | Stateful agents with long-term memory | Memory-centred; smaller tool/channel story |
| MCP gateways (IBM ContextForge, Docker, Microsoft, Obot…) | Tool routing and governance | Tools only; no agent loop, memory or channels |
| **OpenClaw** | Self-hosted personal agent, MIT, any model, WhatsApp/Telegram; 250k+ GitHub stars | **The closest thing to our idea, and already huge.** Weak point is security — see section 10 |
| **Goose** (Block) | Local agent with desktop app + CLI, Apache-2.0, MCP-native, any model | Strong and well-backed; developer-focused, less about remote access and everyday accounts |
| Open Interpreter | Lets a model run code and control the computer, Apache-2.0 | Powerful but runs with full OS permissions and little guard-rail |
| Agent Zero | Agent in its own Docker sandbox, MIT | Isolated workbench, not control of your real machine |

**The gap:** a permissively licensed (Apache-2.0), code-first runtime that turns
LangGraph + MCP into something a person can install in five minutes. Nobody in
the list is all four of: truly open, code-first, MCP-native, runnable out of the
box with chat channels and approvals.

Honest caveat: this is a crowded space. "Biggest" is earned by adoption, not by
scope. The realistic path is to be the *easiest* way to run an MCP-connected
agent on your own machine, then grow outward.

## 3. Design principles

1. **Five-minute install.** `pip install` or `docker compose up`, one config file.
2. **Config over code.** Models, tools, memory, channels and policy all live in
   `agentd.yaml` plus environment variables for secrets.
3. **Everything is a plugin.** Model providers, vector stores, channels and
   verifiers load through Python entry points.
4. **Works offline-first.** Default install needs no cloud account except an
   LLM key (or none, with Ollama): SQLite + Chroma on local disk.
5. **Secure defaults.** API key required, write-tools need approval, secrets
   never logged.
6. **Lean on the stack.** Use LangChain / LangGraph features instead of
   re-implementing them.

## 4. Target architecture

```
   Desktop app    CLI    HTTP API (FastAPI + SSE)    Telegram / Discord / Slack
            \              |                         /
             +----------- Gateway (auth, sessions, rate limits) -----------+
                                   |
                         Agent runtime (LangGraph)
          memory -> plan -> permission check -> execute -> verify -> policy
                 ^                  |                           |
                 |            interrupt() for approval     commit / retry
                 |                  |
   +-------------+------+   +-------+--------+   +-----------------------+
   | Memory             |   | Tool layer     |   | Model layer           |
   | VectorStore iface  |   | MCP client,    |   | init_chat_model,      |
   | Chroma / Pinecone  |   | many servers,  |   | per-role models,      |
   | Qdrant / pgvector  |   | stdio + HTTP   |   | any provider / local  |
   +--------------------+   +----------------+   +-----------------------+
                                   |
        State: LangGraph checkpointer (SQLite default, Postgres option)
        Audit: every model call, tool call and approval recorded
```

Stack choices, confirmed against current docs:

- **Models:** `init_chat_model("provider:model")` gives one interface for every
  provider; the provider's integration package must be installed. Anything
  OpenAI-compatible (Ollama, vLLM, OpenRouter) works through a custom `base_url`.
  Models can be switched per call through LangGraph config.
- **Tools:** `MultiServerMCPClient` from `langchain-mcp-adapters` manages many
  MCP servers at once, mixing stdio and HTTP transports. Tools are async, so the
  executor must call `ainvoke`.
- **Durable runs and approvals:** a LangGraph checkpointer plus `interrupt()`
  pauses a run for a human and resumes it with `Command(resume=...)`. SQLite for
  single-server, Postgres for multi-instance.
- **Memory:** LangChain's `VectorStore` and embeddings interfaces, so the store
  and the embedding model are both config choices.

### Proposed package layout

```
agentd/
  config/      schema (pydantic), loader, `doctor` checks
  models/      provider registry, per-role model resolution
  tools/       MCP client manager, permission policy, built-in MCP servers
  memory/      vector store factory, scoped recall (per user / workspace)
  runtime/     LangGraph graph, nodes, state, checkpointer setup
  verify/      local checks, LLM judge, policy
  server/      FastAPI app, auth, SSE streaming, approvals API
  channels/    telegram, discord, slack (optional extras)
  audit/       run / step / tool-call log, export
  cli/         init, serve, chat, doctor, tools, models
  computer/    built-in local tools: files, shell, browser, screen control
desktop/       the desktop app (UI + bundled runtime)
examples/  docs/  tests/  docker/
```

### The desktop app

- **Shell:** Electron (decided 2026-11-16), for Windows and macOS. A first
  version exists in `desktop/`: it starts the runtime on a random localhost
  port with a random token and shows the screens the runtime serves at
  `/app` (plain HTML/JS for now, in `agentd/server/ui/`).
- **Runtime inside:** the Python runtime ships bundled as a background process
  the app starts and stops. The user installs one file and never sees Python.
  This bundling is the hardest packaging job in the project.
- **Local or remote:** the app talks to the runtime over the same HTTP API
  everyone else uses, so it can point at `localhost` or at a server you run.
- **Screens:** chat with live step-by-step view; approvals inbox; models and
  keys; connections (add an MCP tool in a few clicks, with sign-in handled by
  the app); memory browser; run history and audit log; permission settings.
- **Keys:** stored in the operating system keychain, never in plain files.

### Full control of the computer

Built-in tools, each one switchable on or off:

| Capability | How |
|---|---|
| Files | Read, write, search inside folders the user grants |
| Commands | Run shell commands and scripts |
| Browser | Drive a real browser (Playwright MCP server) |
| Screen | See the screen and use mouse and keyboard, for models that support computer use |
| Services | Anything with an MCP server: GitHub, Slack, Gmail, databases… |

Because this is real power over someone's machine, control is graded:

- **Ask mode (default):** every action that changes something needs a tap.
- **Trusted mode:** safe actions run on their own; risky ones still ask.
- **Full auto:** runs without asking, inside limits the user sets (allowed
  folders, blocked commands, optional sandbox).

Some actions always ask, whatever the mode: spending money, sending messages
as the user, deleting outside granted folders, changing system settings.

### Remote access to your own computer

The core promise: **tell it the task from anywhere, and it does the work on
your computer.** You are out with your phone; your PC at home does the job.

- **How you reach it:** Telegram / Discord / Slack bot, a phone-friendly web
  page, or the desktop app on another machine.
- **What you get back:** live progress, screenshots of what it is doing,
  result files, and approval cards you can tap from the phone.
- **How the connection works:** the computer connects *outward* to the chat
  service or to a relay, so no ports are opened on the home network. A
  self-hosted relay is an option for people who want no third party involved.
- **Who is allowed:** each phone or account is paired once with a code shown on
  the computer; unpaired senders are ignored. Traffic is encrypted.
- **Safety on top:** remote sessions start in Ask mode, there is a
  "stop everything" button on every door, and every remote action is logged.

Remote access plus full control is the most dangerous combination in the
product: a stolen phone or leaked bot token must not hand over the computer.
Pairing, per-device revocation and the always-ask list are therefore part of
the first release that includes remote access, not an add-on.

### Never stuck on one model

- **Switch any time.** Keys and models are changed in settings; the next step
  of a running task simply uses the new one.
- **Resume where it stopped.** Every step of a task is saved (LangGraph
  checkpointer). If a key runs out of credit or a provider is down mid-task,
  the task pauses, and continues from the same step once another key or model
  is supplied. Nothing is redone.
- **Automatic fallback.** The user can list models in order ("try Claude, then
  Gemini, then my local model"); the runtime moves down the list on quota or
  outage errors without asking.
- **No subscription to us.** The user pays only their own model provider, or
  nothing with a local model.

### What the user can ask for — capability map

| The user says | How the agent does it | Permission |
|---|---|---|
| "Fix this bug and open a pull request" | GitHub connection + files + commands | Ask before pushing |
| "Post this on my LinkedIn / X / Discord" | Official API where one exists, otherwise the browser signed in as the user | Always ask before posting |
| "Summarise what I missed in Slack" | Slack connection (read) | Runs on its own |
| "Cut this video to 60 seconds and add subtitles" | Installs and runs tools such as ffmpeg and a speech-to-text model | Ask before installing software |
| "Find the best price for these headphones and order them" | Browser: search, compare, fill the basket | Always ask before paying, showing item and total |
| "Track my order / this price / this repo" | Scheduled background task | Runs on its own |
| "Tell me on WhatsApp when it's done" | Notification connections (Telegram, WhatsApp, Discord, email) | Runs on its own once connected |

Built from the same four pieces every time: a model, connections (MCP), computer
control, and permissions. New abilities come from adding connections or
installing tools, not from changing the runtime.

**Scheduled and background tasks** are a required part of the runtime for
tracking and notifications: "every morning", "when the price drops", "when the
build finishes".

### Honest limits

- **It is as capable as the model behind it.** A strong model completes long
  tasks; a small local model will fail more often. The app should say so.
- **Official APIs only for accounts.** Every service is connected through its
  official API with credentials the user supplies (section 10 lists what each
  one allows). Where no official path exists, the app says so; it never tries
  to evade bot detection.
- **WhatsApp** works through the official Cloud API with a business number;
  Telegram is the easy default for notifications.
- **Money and identity always need a tap.** Paying, ordering and posting as the
  user are never fully automatic, in any mode. Card details are entered by the
  user or their password manager, not typed by the agent.
- **"Anything" has a ceiling.** Captchas, two-factor prompts and bank logins
  are handed back to the user.

### Example config

```yaml
models:
  planner:    anthropic:claude-sonnet-5-5
  worker:     openai:gpt-5.4-mini
  verifier:   google_genai:gemini-3.5-flash
  embeddings: openai:text-embedding-3-small

tools:
  github:
    transport: stdio
    command: npx
    args: ["-y", "@modelcontextprotocol/server-github"]
  internal_api:
    transport: http
    url: https://tools.example.com/mcp

permissions:
  default: ask            # allow | ask | deny
  rules:
    - match: "github.search_*"
      action: allow
    - match: "*.delete_*"
      action: deny

memory:
  store: chroma           # chroma | pinecone | qdrant | pgvector
  scope: user

state:
  checkpointer: sqlite    # sqlite | postgres

channels:
  telegram: { enabled: true }
```

## 5. What happens to the current code

| Today | Decision | Becomes |
|---|---|---|
| `jev/client.py`, all Jev calls | **Remove** | — |
| Jev guardrail node | Replace | Deterministic permission rules (allow / ask / deny per tool) |
| Jev verification | Replace | Local checks + optional LLM judge on the *final answer* |
| Jev tool choice | Remove | Planner is given real tool schemas, so exact names are used |
| `jev/policy.py` | Keep, rename | `verify/policy.py` — commit / retry / review |
| `jev/decision_log.py` | Rework | Audit log of runs, steps, tool calls, human decisions |
| `llm/gemini_client.py` | Replace | `init_chat_model` with structured output; Gemini becomes one provider |
| `memory/vector_store.py` | Replace | `VectorStore` factory, similarity threshold, per-user scope |
| `tools/langchain_tools.py` | Replace | Long-lived `MultiServerMCPClient`, no subprocess per request |
| `mcp_server.py` + GitHub/weather/news/Discord/Slack tools | Keep | Shipped as optional built-in MCP servers |
| `agents/executor.py` | Rewrite | Async tool calls, correct result parsing, step outputs feed later steps |
| `agents/planner.py` | Rework | Tool-calling agent loop with schemas, replacing the static JSON plan |
| `services/approval_queue.py` | Replace | LangGraph `interrupt()` + checkpointer; approvals gate actions, not just memory |
| `channels/*` | Keep, fix | Plugins; fix Slack ack, Telegram backoff, token logging |
| `main.py` | Rework | `server/` with API-key auth, no import-time clients |

The bugs found in the earlier review (sync MCP invoke, result parsing, session
wrapper, token in logs, no auth) are all resolved by this restructuring rather
than patched separately.

## 6. Roadmap

Each phase ends in something a stranger could install and use.

**Phase 0 — Clean foundation** *(2026-11-16: Jev removed, tool path fixed,
permissions in code, token leak closed, code restructured into the `agentd/`
package with `pyproject.toml` and an `agentd` CLI — done, uncommitted.)*
- Remove Jev. Restructure into the `agentd/` package with `pyproject.toml`.
- Fix the tool path: async invocation, result parsing, session handling.
- Done when: a tool task runs end to end against a real MCP session in a test.

**Phase 1 — Bring your own model** *(2026-11-16: built — per-role models from
`agentd.yaml` or env, any LangChain provider incl. Ollama / OpenRouter /
OpenAI-compatible URLs, automatic fallback chain, configurable embeddings,
`agentd init` / `doctor [--ping]` / `chat`. Gemini SDK removed. Uncommitted.
Not yet: resuming a half-finished task after restart — that needs the
checkpointer in Phase 3.)*
- Config schema and loader; `init_chat_model` per role; OpenAI-compatible and
  Ollama support; pluggable embeddings.
- CLI: `agentd init`, `agentd doctor`, `agentd chat`.
- Done when: the same task passes on three providers and one local model by
  changing only config.

**Phase 2 — Bring your own tools**
- `MultiServerMCPClient` with stdio + HTTP servers from config; tool schemas
  given to the model; steps can use earlier results.
- Permission rules (allow / ask / deny).
- Done when: adding a public MCP server takes a config edit and no code.

**Phase 3 — Durable and safe**
- Checkpointer (SQLite, Postgres); `interrupt()` approvals that pause real
  actions; API-key auth; audit log; secret redaction in logs.
- Done when: a run survives a server restart while waiting for approval.

**Phase 4 — Memory**
- Vector store factory (Chroma default; Pinecone, Qdrant, pgvector); scoped
  recall with a similarity threshold; opt-in saving.
- Done when: switching stores is a one-line config change.

**Phase 5 — Desktop app (first public release)**
- Tauri + React app with the runtime bundled; chat with live steps, approvals,
  model/key settings, connections screen, run history.
- Installers for Windows, macOS and Linux; streaming API the app is built on.
- Done when: a non-developer installs one file, pastes a key, connects one
  tool and completes a task.

**Phase 6 — Computer control**
- Built-in file, shell and browser tools; screen control for capable models;
  the three permission modes and folder/command limits.
- Done when: "download the invoices from this site and sort them into folders"
  works end to end in Ask mode.

**Phase 7 — Run anywhere**
- Docker image and compose file; PyPI release; desktop app connects to a
  remote runtime; Telegram / Discord / Slack as optional extras; docs site.
- Done when: `docker compose up` on a server gives an agent reachable from the
  desktop app and from a Telegram bot.

**Phase 8 — Growth**
- Connector gallery (one-click MCP tools) and shareable agent presets.
- Multi-agent: supervisor and sub-agent graphs defined in config.
- Observability: OpenTelemetry traces, optional Langfuse / LangSmith export.
- Plugin SDK, example gallery, evaluation harness.

## 7. Open-source plan

- **Licence:** Apache-2.0. A true open licence is a concrete advantage over
  Dify and n8n.
- **Repo hygiene:** README with a 60-second demo, `CONTRIBUTING.md`, code of
  conduct, issue templates, CI (lint, type check, tests on Linux/macOS/Windows).
- **Quality bar:** no test that only passes against fakes for a core path; at
  least one real-MCP-session test per release.
- **Adoption levers:** one-command install, a gallery of ready-made configs
  ("GitHub triage bot", "Slack digest", "local-only assistant"), compatibility
  with the public MCP server ecosystem.

## 8. Risks

- **Crowded field.** Mitigation: stay narrow — best install experience for
  MCP-connected agents — before adding breadth.
- **Fast-moving dependencies.** LangChain, LangGraph and MCP adapters change
  often (even the HTTP transport name differs between docs pages). Pin versions
  and test against upgrades in CI.
- **Scope creep.** A web UI and multi-agent support are attractive but should
  wait until Phases 0–5 are solid.
- **Security exposure.** An agent with control of a computer can delete files,
  leak data or be tricked by instructions hidden in a web page or document.
  Permission modes, always-ask actions and treating tool output as untrusted
  must ship from the first release, not later.
- **Desktop packaging.** Bundling Python inside a signed, auto-updating app on
  three operating systems is slow, fiddly work. Budget real time for it.
- **Two codebases.** The desktop UI (TypeScript) is a second stack to maintain
  next to the Python runtime.
- **Overlap with LangGraph's own server offering.** Differentiate on being
  fully open, MCP-first and batteries-included (channels, approvals).

## 9. Decisions needed from you

1. **Name** of the project and package.
2. **Repo strategy:** reshape this repository in place, or start a fresh one and
   port code across?
3. **Planner style:** keep plan-then-execute, or move to a tool-calling agent
   loop (recommended — it fixes step-to-step data flow)?
4. ~~Default local setup~~ — **decided:** local storage (Chroma + SQLite on the
   user's disk); cloud vector stores optional.
5. **First release scope:** Phases 0–5 (runtime + desktop app) as the first
   public release (recommended), with computer control following in the next?
6. **Desktop shell:** Tauri (recommended) or Electron?
7. **Computer control scope:** files, commands and browser first
   (recommended), with screen/mouse control later — or everything at once?

## 10. Research update (2026-11-15): what can really be connected

### 10.1 The competition is real

**OpenClaw already does most of this idea**: self-hosted, MIT, bring your own
model, talk to it from WhatsApp or Telegram, runs on your machine. Reported
star counts range from about 250k to 390k. It cannot be ignored or out-featured
quickly.

Its documented weakness is security: a high-severity CVE and command-injection
flaws in early 2026, scans finding 40,000–135,000 instances exposed to the
internet with unsafe defaults, and malicious third-party plugins used for
malware and data theft. Reviews range from "C and improving" to "a security
catastrophe". (Figures come from secondary sources — verify before quoting.)

**Our wedge, therefore:**

1. **Safe by design.** Nothing listens on the internet by default, device
   pairing, graded permissions, always-ask actions, signed connectors only.
2. **A real desktop app.** Everything set up in screens, for people who will
   never edit a config file or open a terminal.
3. **Official connections with guided setup.** A wizard per service instead of
   "figure out the API yourself".
4. **Never loses work.** Resume a task after a key runs out or a restart.

If we cannot be clearly better on these four, we should contribute to an
existing project instead. That is the honest test.

### 10.2 The connection model: the user brings their own app credentials

An open-source desktop app cannot ship a shared secret for LinkedIn, Google or
Meta, and getting our own app approved by every platform would take months per
service. So each user creates their **own** developer app on the service and
pastes the credentials into our app.

- The agent or the Connections screen says exactly what is needed.
- A step-by-step wizard walks through the service's developer portal.
- The user pastes the key, or signs in through the service's own login page.
- Credentials go into the OS keychain. Tokens that expire are refreshed, or the
  user is asked to sign in again.
- Because each account uses its own official credentials within the service's
  limits, nothing here risks a ban.

### 10.3 Service by service

**Tier A — paste a token, working in minutes**

| Service | What the user provides | What works | Limits |
|---|---|---|---|
| Telegram | Bot token from BotFather | Send/receive, notifications, approval buttons | Bot can only message people who started it. Free |
| Discord | Bot token; bot invited to the server | Read and post in channels, buttons | Posts as the *bot*. Automating a personal user account is against Discord's rules |
| Slack | Slack app token, or sign in to Slack's official MCP server | Read, search, post | Workspace admin may need to approve |
| GitHub | Personal access token, or sign in to GitHub's official MCP server | Repos, issues, pull requests, code | Token scopes decide what is allowed |
| Notion, Linear, Stripe | Sign in to their official MCP servers | Their full official tool sets | Per-service scopes |
| Email (any provider) | IMAP/SMTP app password | Read and send mail | Simpler than the Gmail API route |
| Your computer | Nothing | Files, commands, browser (Microsoft's official Playwright MCP server) | Our own permission modes |

**Tier B — user creates a developer app once (15–30 minutes, guided)**

| Service | What the user provides | What works | Limits |
|---|---|---|---|
| LinkedIn | Own developer app with "Share on LinkedIn" | **Post, comment and like as yourself** — reported as self-serve, no partner review | Cannot read your feed or list your posts (that permission is closed). Company Pages need a gated permission. Daily cap around 100–150 actions |
| X (Twitter) | Own developer account, pay-per-use | Post and read | No free tier since Feb 2026; roughly $0.015 per post (unverified) paid by the user to X |
| Google: Gmail, Calendar, Drive | Own Google Cloud OAuth client | Read/send mail, events, files | Gmail scopes are "restricted". Personal use in testing mode is reported to skip verification, but sources conflict and test-mode sign-ins expire weekly — **verify on Google's docs** |
| YouTube | Same Google client | Upload, manage videos | Uploads from unaudited API projects are reported to stay private; daily quota |
| Reddit | Own OAuth app | Read, post, comment | Free for non-commercial use, ~100 requests/minute; each subreddit has its own rules |
| WhatsApp | Meta business account + a dedicated number (Cloud API) | Notifications to yourself, replies | The number cannot be one already used in the WhatsApp app. Messages outside a 24-hour window must use approved templates and are billed per message |
| Instagram / Facebook | Meta app + Professional account linked to a Page | Publish posts, reels, carousels | Personal Instagram accounts are not supported by the API. 25–100 posts/day |

**Tier C — no official path for a personal agent**

| Wanted | Why not | What we do instead |
|---|---|---|
| Read LinkedIn feed, messages, connections | No open API | Not supported |
| Message WhatsApp contacts as your personal account | Only the business API exists | Notifications only |
| Act as your personal Discord user | Against Discord's rules | Bot account |
| Order from Amazon and similar shops through an API | No consumer ordering API | Browser: agent searches, compares and fills the basket; the user confirms and pays |
| Bank and payment accounts | Deliberately out of scope | Not supported |

**Shopping, looking ahead.** Standards for agents buying things are appearing —
ACP (OpenAI + Stripe), UCP (Google + Shopify) and AP2 for payment consent — but
they are new, changing fast, and built mainly for the big platforms' own
agents. Watch them; do not depend on them for the first releases.

### 10.4 Technical findings that change the build

- **Connectors = MCP servers.** GitHub, Slack, Notion, Linear and Stripe run
  official hosted MCP servers with sign-in, and there is an official MCP
  registry. Use those first; write our own small MCP server only where none
  exists (LinkedIn, WhatsApp, X, Telegram).
- **Browser control:** Microsoft's Playwright MCP server reads the page
  structure instead of screenshots, so it works with any model, not only
  vision models. Microsoft states it is "not a security boundary" — our
  permission layer must sit in front of it.
- **Desktop packaging is proven but fiddly.** Tauri 2 + a PyInstaller-frozen
  FastAPI process is an established pattern. Known traps: macOS signing of the
  bundled Python needs special handling, a Windows installer bug can leave an
  old backend after reinstall, and losing the update-signing key means existing
  installs can never update.
- **Security lessons taken from OpenClaw's year:** bind to localhost only;
  no third-party plugin runs without the user seeing what it can access;
  treat everything read from web pages, messages and files as untrusted data
  that cannot give the agent orders.

### 10.5 Recommended first connectors

Telegram (remote control and notifications), GitHub, Slack, email, the local
computer, then LinkedIn posting. These cover the demo scenarios with the least
approval friction. WhatsApp, Google and X follow once the wizard pattern works.

## 11. What you need to do next

These are tasks for the project owner, not for the code.

1. **Try the competition (2–3 hours).** Install OpenClaw and Goose and use each
   for a real task. Write down what annoyed you — that list is our roadmap.
2. **Choose the name** and check it is free as a GitHub organisation, a PyPI
   package and a domain.
3. **Decide the repository:** a fresh repo for the product with this code
   ported in as the runtime (recommended), or reshape this one in place.
4. **Confirm the licence:** Apache-2.0.
5. **Get two model keys plus one local model** (for example one paid provider,
   one free-tier provider, and Ollama installed) so model switching and resume
   can be tested for real.
6. **Create the test accounts** for the first connectors: a Telegram bot
   (exists), a GitHub token (exists), a test Slack workspace, a LinkedIn
   developer app with "Share on LinkedIn", and a spare email account.
7. **Decide the first-version scope:** files, commands and browser control
   first (recommended), with screen and mouse control later.
8. **Later, before the first public release — not now:** an Apple Developer
   account and a Windows code-signing certificate so installers are trusted,
   and a safe place to keep the update-signing key.
9. **Give the go-ahead for Phase 0** (remove Jev, fix the tool path, restructure
   into a package). Until then nothing in the code changes and nothing is
   committed.

## 12. Gap analysis (2026-11-16): what OpenClaw and Goose get wrong, and our answer

Evidence quality: the incidents and release problems below are reported by
news outlets, an OECD incident record and the projects' own notes. Cost and
"most users fail" figures come from blogs and are anecdotal. Nothing here
replaces trying both tools ourselves (section 11, step 1).

### 12.1 OpenClaw

| Complaint | Evidence | What we build instead |
|---|---|---|
| **The agent ignored "stop" and deleted an inbox.** A long task filled the model's context, older history was summarised, and the user's safety rule was silently dropped; 200+ emails were deleted while she watched | Widely reported Feb 2026 incident; OECD incident record | Rules live in **code, not in the prompt**. Permissions are checked by the runtime before every tool call and cannot be forgotten. Stop is a hard kill of the run, not a message the model may ignore. Deletes go to trash first |
| **Hijacked by content it read.** A WhatsApp contact card and a friendly email each made the agent run commands; a Gmail hook allowed remote code execution with no click | The Hacker News, security researchers' write-ups | Everything read from messages, mail, pages and files is marked untrusted and can never issue orders. Actions triggered while processing untrusted content always ask |
| **Unsafe out of the box.** 63% of internet-exposed installs reported using insecure defaults | March 2026 scan (secondary source) | Listens on localhost only. Remote access is outbound-only with device pairing. No setting exposes it to the internet by accident |
| **Malicious plugins.** 1,467 malicious skill packages removed from its marketplace in one quarter | Secondary source | Connector gallery is reviewed and signed. Before install, the app shows exactly what a connector can access. Unsigned connectors need an explicit developer mode |
| **Hard to set up.** Self-hosting needs Docker, nginx and SSL; described as "a server, not an app". Windows users report failed updates, permission errors, slow start | Reviews, user reports | One installer per OS, no terminal. Windows is a first-class target, tested in CI |
| **No real desktop app.** A third-party desktop client exists (macOS only), which shows the demand | Community project | The desktop app is our main product, on Windows, macOS and Linux |
| **Burns money.** Agent spends tokens on work nobody asked for; $300–500/month reported unoptimised | Blogs (anecdotal) | Live cost meter, per-task and daily budgets that pause the run, cheap model for routine steps, no background work unless scheduled by the user |
| **Bad releases.** April 2026 updates broke gateways, plugins and the Discord/Telegram/WhatsApp channels; the founder acknowledged it. Session-state corruption caused total outages | News coverage, issue digests | Stable and beta channels, automatic rollback if the runtime fails to start after an update, real end-to-end tests per release. Task state is checkpointed in a database, so a crash resumes instead of corrupting |
| **Forgets.** Context loss after memory compaction; memory indexing stalls | FAQ pages, open issue | Local vector memory plus "pinned facts" that are always included and never summarised away. A memory screen shows what it knows and lets the user edit it |
| **WhatsApp runs on the user's personal number** through an unofficial route; native support is an open request | GitHub issue | Official WhatsApp Cloud API only, on a dedicated number |
| **"Done" when it was not.** Reports of phantom task completion | Blogs (sources conflict) | A verify step checks the result against the request before reporting success, and the reply shows the evidence |

### 12.2 Goose

| Complaint | Evidence | What we build instead |
|---|---|---|
| **Built for developers.** CLI-first; reviews say non-technical users find it demanding | Several reviews | Designed for everyday tasks and non-developers first; coding is one use among many |
| **Steep learning curve.** Recipes, YAML, extensions and subagents take time | Reviews | No YAML for normal use. Settings screens and ready-made presets ("Inbox helper", "GitHub triage") |
| **Desktop app is secondary** and less polished than commercial apps | Reviews | The app is the product, not an afterthought |
| **Unclear failures.** A misconfigured extension fails without a clear reason | Reviewer report | A health check with plain-language errors and a "fix it" button for each connection |
| **Weak with local models.** Simple tasks work; multi-step work falls apart, and the tool does not say why | Reviews | The app rates each model for agent work and warns before a hard task on a weak model; automatic fallback to a stronger one if allowed |
| **No remote or phone control, no managed scheduling** — not found in any review (unconfirmed gap) | Absence in search results; verify by using it | Remote access, phone approvals and scheduled tasks are core features |

### 12.3 What the other challengers show

Hermes Agent (leaner, stronger memory, heavy install), NanoClaw (every session
in a container, but tied to one model vendor), ZeroClaw (single binary,
approvals on by default) and IronClaw (sandboxing) all exist because of
OpenClaw's security and complexity problems. That confirms the demand, and it
means "more secure than OpenClaw" alone is no longer unique. **Our combination
is what is unique: safe by default + a real desktop app for non-developers +
official connections with guided setup + any model with resume.**

### 12.4 The ten promises (our public checklist)

1. Installs from one file on Windows, macOS and Linux. No terminal.
2. Never reachable from the internet unless you pair a device.
3. Rules are enforced by the app, so the AI cannot forget or override them.
4. Stop means stop, instantly, from every screen and every chat.
5. Nothing it reads can give it orders.
6. You always see the cost, and it stops at your budget.
7. Tasks survive crashes, restarts and empty API keys.
8. Memory stays on your disk, and you can see and edit it.
9. Connections use official APIs with your own credentials.
10. Every add-on shows what it can touch before you install it.

Each promise becomes an automated test. If a release breaks one, it does not ship.

## Sources

- [Open Source AI Agent Platform Comparison (2026)](https://jimmysong.io/blog/open-source-ai-agent-workflow-comparison/)
- [Dify vs Flowise vs Langflow 2026](https://futureagi.com/blog/dify-vs-flowise-vs-langflow-2026/)
- [OSS agent frameworks 2026](https://futureagi.com/blog/oss-agent-frameworks-2026/)
- [LangChain — providers and models](https://docs.langchain.com/oss/python/concepts/providers-and-models)
- [LangChain — init_chat_model reference](https://reference.langchain.com/python/langchain/chat_models/base/init_chat_model)
- [LangChain — MCP guide](https://docs.langchain.com/oss/python/langchain/mcp.md)
- [langchain-mcp-adapters](https://github.com/langchain-ai/langchain-mcp-adapters)
- [LangGraph — interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts.md)
- [LangGraph — checkpointers](https://docs.langchain.com/oss/langgraph/checkpointers)
- [Best Open Source MCP Gateways in 2026 (Geekflare)](https://geekflare.com/open-source/mcp-gateway/)
- [Best Open Source MCP Gateways in 2026 (Maxim — vendor, treat with caution)](https://www.getmaxim.ai/articles/best-open-source-mcp-gateways-in-2026)

Research update (most are secondary sources; verify figures on the official docs):

- [OpenClaw — Wikipedia](https://en.wikipedia.org/wiki/OpenClaw)
- [OpenClaw security risks (Immersive Labs)](https://www.immersivelabs.com/resources/c7-blog/openclaw-what-you-need-to-know-before-it-claws-its-way-into-your-organization)
- [OpenClaw review 2026](https://justinmckelvey.com/blog/openclaw-review)
- [Open-source AI agents in 2026: GitHub data on 18 projects](https://coworkosapp.com/research/open-source-ai-agents-2026/)
- [Best open source AI agents (OpenAlternative)](https://openalternative.co/blog/best-open-source-ai-agents)
- [LinkedIn — Getting access to LinkedIn APIs (Microsoft Learn)](https://learn.microsoft.com/fr-fr/linkedin/shared/authentication/getting-access)
- [Can I post to LinkedIn without partner approval?](https://opentweet.io/answers/can-i-post-to-linkedin-without-partner-approval)
- [LinkedIn API guide for developers 2026](https://dev.to/mikipalet/the-complete-guide-to-the-linkedin-api-for-developers-2026-7p8)
- [WhatsApp Business Platform pricing (Meta)](https://developers.facebook.com/documentation/business-messaging/whatsapp/pricing)
- [WhatsApp Cloud API — send messages (Meta)](https://developers.facebook.com/documentation/business-messaging/whatsapp/messages/send-messages)
- [X replaces its entry-level API plan with pay-per-use](https://opentermsarchive.org/en/memos/x-replaces-its-dollar200-entry-level-api-plan-with-a-pay-per-use-model/)
- [X API pricing](https://opentweet.io/x-api-pricing)
- [Google — restricted scope verification](https://developers.google.com/identity/protocols/oauth2/production-readiness/restricted-scope-verification?hl=ja)
- [Google OAuth verification and Gmail API credentials (2026)](https://www.unipile.com/integrating-google-oauth-2-0-user-authentication-into-your-app/)
- [Instagram content publishing (Meta)](https://developers.facebook.com/docs/instagram-platform/content-publishing/)
- [Reddit API limits and posting restrictions](https://postiz.com/blog/reddit-api-limits-rules-and-posting-restrictions-explained)
- [YouTube upload API: quotas (2026)](https://changelog.zernio.com/blog/youtube-upload-api)
- [Which major platforms have shipped MCP servers in 2026?](https://agentndx-web.pages.dev/blog/which-major-platforms-shipped-mcp-servers-2026)
- [Remote MCP servers guide](https://mcp.so/blog/remote-mcp-servers-guide)
- [Playwright MCP server guide 2026](https://mcp.directory/blog/playwright-browser-mcp-guide-2026)
- [State of agentic commerce 2026](https://rajnandan.com/posts/state-of-agentic-commerce-2026/)
- [Tauri — sidecar docs](https://v2.tauri.app/develop/sidecar)
- [Building desktop LLM apps: Tauri, FastAPI, PyInstaller](https://aiechoes.substack.com/p/building-production-ready-desktop)
- [Debugging macOS code signing with a bundled Python](https://timetobuildbob.github.io/blog/four-prs-to-sign-one-app)

Gap analysis (section 12):

- [Users flee as OpenClaw stumbles through its most difficult week](https://www.trendingtopics.eu/users-flee-as-openclaw-stumbles-through-its-most-difficult-week-ever/)
- [OpenClaw acknowledges rough week with 2026.4.29 release issues](https://feedbagel.com/post/openclaw-had-a-rough-week)
- [OpenClaw review 2026: what it does, what it can't](https://blink.new/blog/openclaw-review-2026-honest-assessment)
- [OpenClaw problems: 7 issues](https://vibecoding.app/blog/why-i-stopped-using-openclaw)
- [OpenClaw honest drawbacks](https://claw.aguidetocloud.com/openclaw/overview/honest-drawbacks/)
- [Open-source OpenClaw desktop client that fixes 17 pitfalls](https://dev.to/joey_lee_c96e4ad421791371/we-built-an-open-source-openclaw-desktop-client-that-fixes-17-pitfalls-gjn)
- [OECD AI incident: OpenClaw data loss and security breach](https://oecd.ai/en/incidents/2026-02-28-4e1d)
- [Meta's AI safety chief told her agent to stop; it deleted her inbox anyway](https://letsdatascience.com/blog/metas-ai-safety-chief-told-her-ai-agent-to-stop-it-deleted-her-inbox-anyway)
- [New attacks trick OpenClaw AI agent (The Hacker News)](https://thehackernews.com/2026/06/new-attacks-trick-openclaw-ai-agent.html?m=1)
- [BrokenClaw: 0-click remote code execution via Gmail hook](https://veganmosfet.codeberg.page/posts/2026-02-02-openclaw_mail_rce/)
- [OpenClaw security risks (TechRadar)](https://www.techradar.com/pro/here-are-the-openclaw-security-risks-you-should-know-about)
- [10 best OpenClaw alternatives in 2026 (Composio)](https://composio.dev/blog/openclaw-alternatives)
- [Best OpenClaw alternatives (Pinggy)](https://pinggy.io/blog/best_openclaw_alternatives/)
- [Goose review: Block's open-source AI agent](https://andrew.ooo/posts/goose-review-open-source-ai-agent-block/)
- [Goose by Block review 2026 (Effloow)](https://effloow.com/articles/goose-open-source-ai-agent-review-2026)
- [Goose pros and cons (AI Tools Atlas)](https://aitoolsatlas.ai/tools/block-goose/pros-cons)
- [Goose CLI review after the Linux Foundation handoff](https://dev.to/pickuma/goose-cli-review-blocks-open-source-agent-after-the-linux-foundation-handoff-4ail)


## 13. DuskBot review (2026-11-16)

[DuskBot](https://github.com/DuskBotAI/duskbot) is a desktop app close to our
goal: bots as contacts, each with its own browser or desktop, memory,
routines and approval levels, running on the user's own subscriptions.

**Licence:** PolyForm Noncommercial, with commercial use under a separate
licence. None of its code can be copied into an Apache-2.0 project. We take
ideas only, and write our own code.

**Ideas taken (our own implementations):**
- Login vault: saved logins the agent can sign in with but never see, filled
  only on the site they belong to (`agentd/safety/vault.py`).
- Approval level can change while a task runs.
- Memory scrubs anything that looks like a secret before saving.
- Routines on chosen weekdays; a run missed by more than 12 hours is skipped.

**Worth considering later:** bots as separate contacts with their own
instructions and model; signing in with an existing ChatGPT / Claude / Grok
subscription through their CLIs instead of API keys; a full desktop (not just
a browser) per bot in a VM or container; "rooms" where bots hand work to each
other; cron schedules and app-event triggers; encrypted backups.

**Removed in the same pass:** the old plan -> run -> verify pipeline
(planner, executor, verifier, LangGraph graph), Pinecone memory, the
`/api/plan`, `/api/execute` and `/api/verify` endpoints, the autonomy policy,
prompt templates, `scratch/`, `demo.sh` and the stale `manifest.json`.
Memory is now the agent's own notes file; vector memory returns in Phase 4
as local storage.

## 14. Status (2026-11-17)

### Built and tested
- Desktop app (Electron, Windows): chat, Agents (team) mode, Automations,
  Approvals, Website passwords, Tools, model picker with effort level,
  formatted answers, in-app update popup.
- Agent: works in a loop with files, commands, web search, web pages and a
  real browser; asks before changing anything; loop guard; skills; memory
  with secrets scrubbed; follow-up messages keep context.
- Safety in code: protected files and system folders, blocked destructive
  commands, never types passwords or card details, always asks before
  paying, local addresses blocked, login vault, audit log, localhost +
  random token.
- Models: OpenAI, Google, Claude, Grok (+ OpenRouter, Groq, DeepSeek,
  Mistral, Ollama), fallback chain, effort levels.
- Connections: built-in tools, your own MCP servers, Telegram / Discord /
  Slack with approval buttons.
- Releases: tag-triggered GitHub workflow; in-app updates from releases.
- Tests: 195 automated (183 without a browser installed) and an end-to-end
  check that drives the real app with a real model (`npm run e2e`).

### Not done yet
1. ~~One-file installer~~: **done for Windows (2026-11-17).**
   `python desktop/build_runtime.py` then `npx electron-builder --win nsis`
   gives `agentd Setup 0.1.0.exe` (173 MB; 507 MB installed). Tested: silent
   install, fresh-install self-test with the bundled runtime, uninstall. The
   release workflow builds it on Windows and attaches it to each release.
2. **macOS build.** Code is cross-platform; never built or run on a Mac.
3. **Code signing** (Apple Developer account, Windows certificate), so
   installers are trusted.
4. **First release.** No version has been published, so the update popup has
   nothing to offer yet.
5. Keys in the OS keychain: **vault key done (2026-11-17)**; model keys are still in `.env`.
6. ~~Mouse-and-keyboard control of other desktop apps~~: **done on Windows
   (2026-11-17)** through UI Automation (see desktop.py); macOS and Linux
   still to do. Vision-only control for apps with no accessible controls
   (Agent-S / UI-TARS style grounding models) is a later step.
7. **Resume after a restart.** A task stopped by closing the app is lost;
   needs a LangGraph checkpointer.
8. **Searchable memory** (Phase 4): the agent's notes are one file today.
9. **Browser for agent teams**: parallel agents would share one window, so
   team agents have no browser yet.
10. ~~Effort level for automations~~: **done (2026-11-17).**
11. **Not yet tried for real:** a full shopping task on a live shop, a real
    Telegram / Discord / Slack message driving the agent, connecting a real
    third-party MCP server through the app, a saved login on a real site.
12. **Context file** `context.md` in the old folder is outdated (old folder
    only).

### Worth adding later
Multiple named bots with their own instructions and model; signing in with
an existing ChatGPT / Claude subscription instead of API keys; event triggers
for automations (new email, GitHub issue); a phone-friendly way to reach the
app from outside the computer; encrypted backup of settings and data.

### Reviewed 2026-11-17 (licences all MIT or Apache-2.0; LiteLLM mixed)
Taken now: Windows desktop control (Operator-Use / UFO design, uiautomation).
Already covered by our own code: provider choice + key setup (Operator-Use,
Goose), approval modes (Goose), MCP tools (Goose), browser agent
(browser-use; we do not solve CAPTCHAs), clean agent loop (Open Interpreter).
Later: LiteLLM as an optional provider for 100+ models; Docker sandbox for
commands (OpenHands, cua); vision grounding for apps without controls
(Agent-S, UI-TARS); multi-agent workforce ideas (Eigent).
