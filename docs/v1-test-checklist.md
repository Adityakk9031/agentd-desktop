# agentd 1.0.0 — test checklist

Work through these on your own computer with the installed app
(`desktop/dist/agentd Setup 1.0.0.exe`). Tick each one, and note anything odd
with the time it happened (the audit log keeps every approval).

## 1. Install and first start
- [ ] Run the installer. Windows SmartScreen may warn ("unrecognised app"),
      because the app is not code-signed yet: More info → Run anyway.
- [ ] The app opens without Python installed (the runtime is bundled).
- [ ] Choose AI → pick a provider (OpenAI, Google, Claude or Grok) → paste your
      API key → the model list appears → pick a model.
- [ ] Close and reopen the app: the model is still chosen and you are not asked
      for the key again. The key is in Windows Credential Manager
      (Control Panel → Credential Manager → Windows Credentials →
      `agentd-keys`), not in a text file.

## 2. Chat and files
- [ ] "Create a file on my Desktop called test.txt with today's date" → an
      approval card appears → Allow once → the file exists.
- [ ] Follow-up: "add the time to that file" → it edits the same file without
      asking which one (the task journal carries the step).
- [ ] "What is taking up space in my Downloads folder?" → an answer with
      sizes, no approval needed (only looking).
- [ ] Stop button stops a long task.

## 3. Access levels
- [ ] **Ask before acting**: every change asks.
- [ ] **Approve routine actions**: writing an ordinary file runs without a card
      and the activity shows "Approved by the reviewer". Ask for something
      risky ("turn off Windows Defender") → it asks you or refuses.
- [ ] **Full access**: no cards, except a payment click, which always asks.

## 4. Desktop apps
- [ ] "Open Notepad and write a shopping list" → an orange frame appears
      around Notepad while the agent types, and goes away after.
- [ ] "Open Paint and draw a circle" → the agent uses screenshots with the
      position grid (Paint's canvas has no buttons to read).
- [ ] The agent cannot control the agentd window itself or a password manager.

## 5. Browser
- [ ] "Search for the price of a Raspberry Pi 5 and give me three shops" →
      the browser window opens; you see it working.
- [ ] Shopping: "add a USB cable under ₹300 to my Amazon cart" → it stops at
      checkout and asks before any pay / place-order click. **Answer Deny.**
- [ ] Website passwords: save a login for a site, then ask the agent to sign
      in there → it signs in without ever showing the password.
- [ ] Open a look-alike address such as `https://amaz0n.com` → the agent
      reports a warning that it imitates amazon.com.

## 6. Memory and skills
- [ ] "Remember that I am vegetarian" → in a new chat, "suggest a dinner" →
      it suggests vegetarian food.
- [ ] After a multi-step answer, click **Save as skill** → approve the card →
      the skill shows up and is used next time for a similar task.
- [ ] "Forget that I am vegetarian" → it is gone from memory.

## 7. Agents (team) and automations
- [ ] Agents: "Compare three laptops under ₹60,000 and write a summary file" →
      several agents work, then one combined answer.
- [ ] Automations: create one that runs every hour ("summarise today's top
      tech news"), click Run now → a result appears. Try "Allow routine
      changes" on one that writes a file.

## 8. Connections
- [ ] Tools page lists the built-in tools.
- [ ] Add one MCP server you use (Tools → Add) → its tools appear; a tool that
      changes something asks first.
- [ ] Telegram / Discord / Slack (optional): put the bot token in
      `%APPDATA%\agentd\.env` (for example `TELEGRAM_BOT_TOKEN=...`; see plan.md
      section 10), restart the app, message the bot → the agent answers, and
      approvals come as buttons in the chat.

## 9. Updates and uninstall
- [ ] The update check says you are on the latest version (no release is
      published yet, so nothing is offered).
- [ ] Uninstall from Windows Settings → Apps: the app is removed cleanly.

Report problems with: what you asked, which model, the access level, and
what you saw. The installed app keeps its files in `%APPDATA%\agentd`:
`data\` holds the audit log (every approval), `memory.md`, `conversations.jsonl`
(past tasks, secrets removed) and your own skills.
