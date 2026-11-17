"use strict";
// A small Markdown renderer for the agent's answers.
//
// Safety: it never uses innerHTML. Every piece of text becomes a text node,
// and only a fixed set of elements is created, so nothing in an answer can
// run code or change the page. Links work only for http(s) and mailto
// addresses and open in the user's normal browser.
//
// Supported: headings, paragraphs, bold, italic, strikethrough, inline code,
// fenced code blocks (with a Copy button), bullet / numbered / task lists
// (nested by indentation), block quotes, tables, horizontal rules and links.

(function () {
  const SAFE_LINK = /^(https?:\/\/|mailto:)/i;

  function node(tag, className, children) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    for (const child of children || []) element.append(child);
    return element;
  }

  // ------------------------------------------------------------ inline text

  // Order matters: code first (its contents are literal), then links, then emphasis.
  const INLINE = [
    { re: /`([^`\n]+)`/, make: (m) => node("code", "", [m[1]]) },
    { re: /\[([^\]\n]+)\]\(([^)\s]+)(?:\s+"[^"]*")?\)/, make: (m) => link(m[2], inline(m[1])) },
    { re: /(https?:\/\/[^\s<>()]+[^\s<>().,;:!?'"])/, make: (m) => link(m[1], [m[1]]) },
    { re: /\*\*([^*\n](?:[^\n]*?[^*\n])?)\*\*/, make: (m) => node("strong", "", inline(m[1])) },
    { re: /__([^_\n](?:[^\n]*?[^_\n])?)__/, make: (m) => node("strong", "", inline(m[1])) },
    { re: /~~([^~\n]+)~~/, make: (m) => node("del", "", inline(m[1])) },
    { re: /\*([^*\s](?:[^*\n]*[^*\s])?)\*/, make: (m) => node("em", "", inline(m[1])) },
    { re: /(^|[^\w])_([^_\s](?:[^_\n]*[^_\s])?)_(?=[^\w]|$)/, make: (m) => [m[1], node("em", "", inline(m[2]))] },
  ];

  function link(href, children) {
    if (!SAFE_LINK.test(href)) return children;  // anything else stays plain text
    const a = node("a", "", children);
    a.href = href;
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    return a;
  }

  function inline(text) {
    const out = [];
    let rest = text;
    while (rest) {
      let best = null;
      for (const rule of INLINE) {
        const match = rule.re.exec(rest);
        if (match && (!best || match.index < best.match.index)) best = { match, rule };
      }
      if (!best) { out.push(rest); break; }
      if (best.match.index > 0) out.push(rest.slice(0, best.match.index));
      const made = best.rule.make(best.match);
      out.push(...(Array.isArray(made) ? made : [made]));
      rest = rest.slice(best.match.index + best.match[0].length);
    }
    return out.flat();
  }

  // ---------------------------------------------------------------- blocks

  const FENCE = /^\s*(```|~~~)\s*([\w+#.-]*)\s*$/;
  const HEADING = /^(#{1,6})\s+(.*?)\s*#*\s*$/;
  const RULE = /^\s*([-*_])(\s*\1){2,}\s*$/;
  const ITEM = /^(\s*)([-*+]|\d{1,3}[.)])\s+(.*)$/;
  const QUOTE = /^\s*>\s?(.*)$/;
  const TABLE_SEP = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/;

  function cells(line) {
    let text = line.trim();
    if (text.startsWith("|")) text = text.slice(1);
    if (text.endsWith("|")) text = text.slice(0, -1);
    return text.split("|").map((cell) => cell.trim());
  }

  function codeBlock(language, lines) {
    const code = node("code", "", [lines.join("\n")]);
    const copy = node("button", "copy", ["Copy"]);
    copy.type = "button";
    copy.addEventListener("click", async () => {
      try { await navigator.clipboard.writeText(code.textContent); copy.textContent = "Copied"; }
      catch { copy.textContent = "Select and copy"; }
      setTimeout(() => { copy.textContent = "Copy"; }, 1500);
    });
    return node("div", "code-block", [node("div", "code-head", [node("span", "", [language || "text"]), copy]), node("pre", "", [code])]);
  }

  function list(lines, start) {
    // Builds one list (and its nested lists) from consecutive item lines.
    const first = ITEM.exec(lines[start]);
    const indent = first[1].length;
    const ordered = /\d/.test(first[2]);
    const element = node(ordered ? "ol" : "ul", "", []);
    if (ordered && parseInt(first[2], 10) !== 1) element.start = parseInt(first[2], 10);
    let i = start;
    let item = null;
    while (i < lines.length) {
      const line = lines[i];
      const match = ITEM.exec(line);
      if (match && match[1].length === indent && /\d/.test(match[2]) === ordered) {
        let content = match[3];
        const task = /^\[([ xX])\]\s+(.*)$/.exec(content);
        item = node("li", task ? "task" : "", []);
        if (task) {
          const box = node("span", "box" + (task[1] !== " " ? " done" : ""), [task[1] !== " " ? "✓" : ""]);
          item.append(box);
          content = task[2];
        }
        item.append(...inline(content));
        element.append(item);
        i += 1;
      } else if (match && match[1].length > indent && item) {
        const nested = list(lines, i);
        item.append(nested.element);
        i = nested.next;
      } else if (item && line.trim() && /^\s+/.test(line) && !ITEM.test(line)) {
        item.append(" ", ...inline(line.trim()));  // a wrapped continuation line
        i += 1;
      } else {
        break;
      }
    }
    return { element, next: i };
  }

  function blocks(text) {
    const lines = String(text || "").replace(/\r\n?/g, "\n").split("\n");
    const out = [];
    let i = 0;
    while (i < lines.length) {
      const line = lines[i];
      if (!line.trim()) { i += 1; continue; }

      const fence = FENCE.exec(line);
      if (fence) {
        const body = [];
        i += 1;
        while (i < lines.length && !(lines[i].trim().startsWith(fence[1]))) { body.push(lines[i]); i += 1; }
        i += 1;  // closing fence (or end of text)
        out.push(codeBlock(fence[2], body));
        continue;
      }

      const heading = HEADING.exec(line);
      if (heading) {
        const level = Math.min(heading[1].length + 1, 6);  // answers start at h2 size
        out.push(node("h" + level, "", inline(heading[2])));
        i += 1;
        continue;
      }

      if (RULE.test(line)) { out.push(node("hr", "", [])); i += 1; continue; }

      if (line.includes("|") && i + 1 < lines.length && TABLE_SEP.test(lines[i + 1])) {
        const head = cells(line);
        const table = node("table", "", []);
        table.append(node("thead", "", [node("tr", "", head.map((cell) => node("th", "", inline(cell))))]));
        const body = node("tbody", "", []);
        i += 2;
        while (i < lines.length && lines[i].includes("|") && lines[i].trim()) {
          const row = cells(lines[i]);
          body.append(node("tr", "", head.map((_, c) => node("td", "", inline(row[c] || "")))));
          i += 1;
        }
        table.append(body);
        out.push(node("div", "table-wrap", [table]));
        continue;
      }

      if (QUOTE.test(line)) {
        const quoted = [];
        while (i < lines.length && QUOTE.test(lines[i])) { quoted.push(QUOTE.exec(lines[i])[1]); i += 1; }
        out.push(node("blockquote", "", blocks(quoted.join("\n"))));
        continue;
      }

      if (ITEM.test(line)) {
        const made = list(lines, i);
        out.push(made.element);
        i = made.next;
        continue;
      }

      // Paragraph: consecutive plain lines; single line breaks are kept
      const paragraph = [];
      while (i < lines.length && lines[i].trim() && !FENCE.test(lines[i]) && !HEADING.test(lines[i])
             && !ITEM.test(lines[i]) && !QUOTE.test(lines[i]) && !RULE.test(lines[i])
             && !(lines[i].includes("|") && i + 1 < lines.length && TABLE_SEP.test(lines[i + 1]))) {
        if (paragraph.length) paragraph.push(document.createElement("br"));
        paragraph.push(...inline(lines[i].trim()));
        i += 1;
      }
      out.push(node("p", "", paragraph));
    }
    return out;
  }

  window.renderMarkdown = function (text) {
    return node("div", "md", blocks(text));
  };
})();
