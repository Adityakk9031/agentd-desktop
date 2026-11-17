---
name: desktop-apps
description: Use a Windows app by clicking and typing (Notepad, Paint, Excel, Settings, any program without a better tool).
---

1. Prefer files, commands or the browser when they can do the job; use an app only when the task needs its window.
2. Open the app with desktop_open_app, or bring an open one forward with desktop_switch.
3. Call desktop_read to see the window. Act on refs (like d3) with desktop_click, desktop_type and desktop_keys.
4. Use keyboard shortcuts when they are simpler: {Ctrl}s save, {Ctrl}{Shift}s save as, {Ctrl}o open, {Ctrl}c / {Ctrl}v copy and paste, {Alt}{F4} close.
5. Dialogs (Save As, Open, confirmations) appear as new windows or new parts of the window: read again after each step.
6. When the list is not enough (images, charts, unlabeled buttons), take desktop_screenshot.
7. Check the result before you finish (read the window again, or check the file on disk).

Never type into password boxes. If an app asks for a password, a code or an administrator approval, call wait_for_user.
