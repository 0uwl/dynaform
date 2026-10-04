// Show/hide fields whose wrapper has data-parent="<base>" based on the
// checked state of the checkbox for "B_<base>". A top-level checkbox is named
// "B_<base>"; one in a list row "L_x-<n>-B_<base>", and it only reaches the
// fields of its own row. Listening on the document (not on each checkbox)
// covers rows that list_rows.js adds later.
document.addEventListener("DOMContentLoaded", () => {
  const CHECKBOX = /(?:^|-)B_(.+)$/;

  function sync(checkbox) {
    const base = checkbox.name.match(CHECKBOX)[1];
    const row = checkbox.closest("li");
    document.querySelectorAll(`[data-parent="${base}"]`).forEach((el) => {
      if (el.closest("li") === row) el.hidden = !checkbox.checked;
    });
  }

  // Only a checkbox can have a name that matches CHECKBOX.
  document.querySelectorAll('input[type="checkbox"]').forEach((el) => {
    if (CHECKBOX.test(el.name)) sync(el);
  });
  document.addEventListener("change", (event) => {
    if (CHECKBOX.test(event.target.name)) sync(event.target);
  });
});
