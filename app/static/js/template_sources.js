// Fill the editor from either of the two sources that can feed it: a template
// from the mounted directory, or a file the user picked. Both land as text in
// the textarea and go no further -- the edits from there on belong to this page
// and the request that follows it. The directory file is never written to, and
// a picked file is read in the browser rather than uploaded.
//
// One script rather than two because both sources write the same textarea and
// have to agree on what is in it: whichever one filled it last owns it, and
// the other asks before overwriting work the user has since typed.
document.addEventListener("DOMContentLoaded", () => {
  const textarea = document.getElementById("template_text");
  if (!textarea) return;

  const select = document.getElementById("template_choice");
  const fileInput = document.getElementById("template_file");

  // What the editor held when a source last filled it, so text the user has
  // since typed can be told apart from text that is simply sitting there.
  let loadedText = textarea.value;

  const statusTimers = new WeakMap();
  function announce(status, message) {
    if (!status) return;
    status.textContent = message;
    clearTimeout(statusTimers.get(status));
    statusTimers.set(
      status,
      setTimeout(() => {
        status.textContent = "";
      }, 5000)
    );
  }

  // True when it is safe to overwrite the editor: it is empty, holds exactly
  // what a source put there, or the user said go ahead.
  function mayReplaceEditor() {
    if (!textarea.value.trim() || textarea.value === loadedText) return true;
    return window.confirm("Replace the edited template text in the editor?");
  }

  function fill(text) {
    textarea.value = text;
    loadedText = text;
    // The editor is now the one source of truth for what gets parsed, so drop
    // any attached file: the server prefers an upload over the editor text and
    // would otherwise parse the original, discarding edits made here.
    if (fileInput) fileInput.value = "";
    if (select) select.value = "";
  }

  function setUpDirectoryPicker() {
    const loadButton = document.getElementById("load-template");
    const status = document.getElementById("template-choice-status");
    const loadUrl = select && select.dataset.loadUrl;
    if (!select || !loadUrl) return;

    // The button is the no-JS path; picking is enough on a scripted page.
    if (loadButton) loadButton.hidden = true;

    select.addEventListener("change", async () => {
      const name = select.value;
      if (!name) return;
      if (!mayReplaceEditor()) {
        select.value = "";
        return;
      }

      try {
        const url = new URL(loadUrl, window.location.href);
        url.searchParams.set("name", name);
        const response = await fetch(url, { headers: { Accept: "text/plain" } });
        if (!response.ok) throw new Error(`load failed: ${response.status}`);
        const text = await response.text();
        fill(text);
        // fill() clears the select along with the file input; put the picked
        // template back so the list still shows what the editor is holding.
        select.value = name;
        announce(status, `Loaded ${name}.`);
      } catch (err) {
        // Fall back to the server round trip rather than leaving a dead select.
        if (loadButton) loadButton.hidden = false;
        announce(status, "Could not load that template -- use the Load into editor button.");
      }
    });
  }

  function setUpFilePreview() {
    const status = document.getElementById("template-file-status");
    const hint = document.getElementById("template-file-hint");
    if (!fileInput) return;

    // Without scripting the file really is uploaded, and the hint in the page
    // says so; here it is read in the browser instead, so correct the hint.
    if (hint) {
      hint.textContent =
        "The file is read in your browser and shown in the editor below -- it is not uploaded.";
    }

    const maxBytes = Number(fileInput.dataset.maxBytes) || 0;
    const allowed = (fileInput.accept || "").split(",").map((ext) => ext.trim().toLowerCase());

    fileInput.addEventListener("change", async () => {
      const file = fileInput.files && fileInput.files[0];
      if (!file) return;

      const name = file.name.toLowerCase();
      if (allowed.length && !allowed.some((ext) => ext && name.endsWith(ext))) {
        // Left attached on purpose: submitting then gets the server's own
        // "Template files only" error rather than a silently ignored file.
        announce(status, `Only ${allowed.join(", ")} files can be shown in the editor.`);
        return;
      }
      if (maxBytes && file.size > maxBytes) {
        fileInput.value = "";
        announce(status, `That file is too big (limit ${Math.floor(maxBytes / 1024)} KB).`);
        return;
      }
      if (!mayReplaceEditor()) {
        // Declining means keeping what is in the editor, so the file has to go:
        // left attached, it would override that text on submit.
        fileInput.value = "";
        announce(status, "Kept the text in the editor -- the file was not used.");
        return;
      }

      try {
        fill(await file.text());
        announce(status, `Loaded ${file.name} into the editor.`);
      } catch (err) {
        announce(status, "Could not read that file.");
      }
    });
  }

  setUpDirectoryPicker();
  setUpFilePreview();
});
