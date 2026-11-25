# Third-party notices

## Desktop control

agentd/computer/desktop.py follows the design of Operator-Use
(https://github.com/CursorTouch/Operator-Use, MIT License) and Microsoft UFO
(https://github.com/microsoft/UFO, MIT License): the active window shown as a
list of interactive controls with refs, and the control types worth showing.
No code was copied from them. The work is done by the uiautomation package
(https://github.com/yinkaisheng/Python-UIAutomation-for-Windows, Apache License
2.0), installed as a dependency.

## OpenMausBot and Hermes Agent

agentd/safety/reviewer.py (the "Approve routine actions" level) follows the
"Approve for me" approval level of OpenMausBot
(https://github.com/milind-soni/OpenMausBot, Apache License 2.0) and the smart
approvals of Hermes Agent (https://github.com/NousResearch/hermes-agent, MIT
License). agentd/runtime/recall.py follows OpenMausBot's recall of notes and
earlier conversations before each turn, and save_skill in
agentd/runtime/skills.py follows Hermes Agent's /learn. No code was copied
from either project.

## OpenClaw

Parts of agentd's design follow OpenClaw (https://github.com/openclaw/openclaw),
adapted to Python: the accessibility-snapshot browser view with element refs
and "[new]" marking (agentd/computer/browser.py), the tool repeat guard
(agentd/runtime/agent.py), SKILL.md instruction packs (agentd/runtime/skills.py)
and user-configured MCP servers (agentd/tools/connections.py).

MIT License

Copyright (c) 2026 OpenClaw Foundation

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
