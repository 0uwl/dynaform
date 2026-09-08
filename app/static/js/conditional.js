// Show/hide fields whose wrapper has data-parent="<base>" based on the
// checked state of the checkbox named "B_<base>". Native platform feature
// (hidden attribute + change event) -- no framework needed.
document.addEventListener("DOMContentLoaded", () => {
  function sync(base, checked) {
    document.querySelectorAll(`[data-parent="${base}"]`).forEach((el) => {
      el.hidden = !checked;
    });
  }

  document.querySelectorAll('input[type="checkbox"][name^="B_"]').forEach((checkbox) => {
    const base = checkbox.name.slice(2);
    sync(base, checkbox.checked);
    checkbox.addEventListener("change", () => sync(base, checkbox.checked));
  });
});
