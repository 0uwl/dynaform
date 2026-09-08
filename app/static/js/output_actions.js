// Copy the rendered output to the clipboard or save it as a .txt file. Both
// work off the text already in the page, so neither needs a server round trip
// -- the output never crosses the wire a second time, and nothing is stored.
document.addEventListener("DOMContentLoaded", () => {
  const output = document.getElementById("output");
  const actions = document.getElementById("output-actions");
  const status = document.getElementById("output-status");
  if (!output || !actions) return;

  // Buttons ship hidden and are revealed here, so they are never dead controls
  // when scripting is unavailable.
  actions.hidden = false;

  let statusTimer;
  function announce(message) {
    status.textContent = message;
    clearTimeout(statusTimer);
    statusTimer = setTimeout(() => {
      status.textContent = "";
    }, 3000);
  }

  async function copyText(text) {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return;
    }
    // navigator.clipboard is undefined on a plain-HTTP origin, which is how
    // this app is usually reached on a LAN. execCommand is deprecated but is
    // the only fallback that works there.
    const helper = document.createElement("textarea");
    helper.value = text;
    helper.setAttribute("readonly", "");
    helper.style.position = "fixed";
    helper.style.top = "-1000px";
    document.body.appendChild(helper);
    helper.select();
    try {
      if (!document.execCommand("copy")) throw new Error("copy command rejected");
    } finally {
      helper.remove();
    }
  }

  document.getElementById("copy-output").addEventListener("click", async () => {
    try {
      await copyText(output.textContent);
      announce("Copied to clipboard.");
    } catch (err) {
      announce("Could not copy -- select the text and copy it manually.");
    }
  });

  document.getElementById("download-output").addEventListener("click", () => {
    const blob = new Blob([output.textContent], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "dynaform-output.txt";
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
    announce("Downloaded dynaform-output.txt.");
  });
});
