// Add/remove rows of an L_ list. Each list's <template> holds one blank row
// whose names and ids carry "__INDEX__"; a new row is a clone of it with a
// fresh index swapped in. Cloning (not innerHTML) keeps every value the
// server escaped exactly as it was.
document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll("[data-list]").forEach((list) => {
    const rows = list.querySelector("[data-rows]");
    const blank = list.querySelector("template");
    const addButton = list.querySelector("[data-add-row]");
    let next = Number(list.dataset.next);

    addButton.addEventListener("click", () => {
      const row = blank.content.firstElementChild.cloneNode(true);
      row.querySelectorAll("[name], [id], [for]").forEach((el) => {
        for (const attr of ["name", "id", "for"]) {
          if (el.hasAttribute(attr)) {
            el.setAttribute(attr, el.getAttribute(attr).replace("__INDEX__", next));
          }
        }
      });
      next += 1;
      rows.append(row);
      // Lets conditional.js show or hide the new row's conditional fields.
      row.querySelectorAll('input[type="checkbox"]').forEach((box) => {
        box.dispatchEvent(new Event("change", { bubbles: true }));
      });
      row.querySelector("input")?.focus();
    });

    rows.addEventListener("click", (event) => {
      const remove = event.target.closest("[data-remove-row]");
      if (!remove) return;
      remove.closest("li").remove();
      addButton.focus();
    });
  });
});
