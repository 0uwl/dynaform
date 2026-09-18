// Load a template from the mounted template directory into the editor as soon
// as it is picked, instead of making the user press "Load into editor". The
// button is the no-JS path and is hidden here so there is only ever one way to
// do it on a scripted page. The fetched text lands in the textarea and goes no
// further -- edits belong to this page only, and the file is never written to.
document.addEventListener("DOMContentLoaded", () => {
  const select = document.getElementById("template_choice");
  const textarea = document.getElementById("template_text");
  const loadButton = document.getElementById("load-template");
  const status = document.getElementById("template-choice-status");
  const loadUrl = select && select.dataset.loadUrl;
  if (!select || !textarea || !loadUrl) return;

  if (loadButton) loadButton.hidden = true;

  // What the editor held the last time a template was loaded, so an unpicked
  // template or the user's own typing can be told apart from untouched text.
  let loadedText = textarea.value;

  let statusTimer;
  function announce(message) {
    if (!status) return;
    status.textContent = message;
    clearTimeout(statusTimer);
    statusTimer = setTimeout(() => {
      status.textContent = "";
    }, 4000);
  }

  select.addEventListener("change", async () => {
    const name = select.value;
    if (!name) return;

    if (textarea.value.trim() && textarea.value !== loadedText) {
      if (!window.confirm("Replace the edited template text in the editor?")) {
        return;
      }
    }

    try {
      const url = new URL(loadUrl, window.location.href);
      url.searchParams.set("name", name);
      const response = await fetch(url, { headers: { Accept: "text/plain" } });
      if (!response.ok) throw new Error(`load failed: ${response.status}`);
      textarea.value = await response.text();
      loadedText = textarea.value;
      announce(`Loaded ${name}.`);
    } catch (err) {
      // Fall back to the server round trip rather than leaving a dead select.
      if (loadButton) loadButton.hidden = false;
      announce("Could not load that template -- use the Load into editor button.");
    }
  });
});
