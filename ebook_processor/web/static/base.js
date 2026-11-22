/* What every script of the app shares: building an element, and asking the app.

     App.h(tag, attrs, ...kids)   an element. `class` is the class name, `html` the inner HTML (for text the app
                                  made itself), a key that starts with "on" a listener; null and false are left
                                  out, among the attributes and among the children alike.
     App.api(path, options)       a request to /api, with the header that says it comes from the app's own page.
                                  `options.json` is sent as JSON. An answer that is an error is thrown, with the
                                  app's message, its `status` and its `data`.

   Loaded first by both pages. Each script takes what it needs under its own short names (h or el; api or call). */
"use strict";
window.App = (() => {
  // set as a property of the element, not as an attribute: an attribute would only give the first value
  const AS_PROPERTY = new Set(["checked", "disabled", "value", "hidden", "readOnly"]);
  function h(tag, attrs, ...kids) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v == null || v === false) continue;
      if (k === "class") el.className = v; else if (k === "html") el.innerHTML = v; else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
      else if (AS_PROPERTY.has(k)) el[k] = v; else el.setAttribute(k, v === true ? "" : v);
    }
    for (const kid of kids.flat(Infinity)) if (kid != null && kid !== false) el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
    return el;
  }
  async function api(path, options = {}) {
    const headers = { "X-Ebook-Processor": "1", ...(options.headers || {}) };
    if (options.json !== undefined) { options.body = JSON.stringify(options.json); headers["Content-Type"] = "application/json"; }
    if (options.keepalive && options.body && options.body.length > 60000) options.keepalive = false;   // browsers refuse more on a page that is closing
    const response = await fetch("/api" + path, { ...options, headers });
    const data = await response.json().catch(() => ({ error: "The app gave an unreadable answer." }));
    if (!response.ok) throw Object.assign(new Error(data.error || response.statusText), { status: response.status, data });
    return data;
  }
  return { h, api };
})();
