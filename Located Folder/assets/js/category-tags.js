document.addEventListener('DOMContentLoaded', () => {
  const category = document.querySelector('select[name="category"]');
  const fieldset = document.querySelector('[data-category-tags]');
  if (!category || !fieldset) return;
  const update = () => {
    let count = 0;
    const name = fieldset.querySelector('[data-category-tags-name]');
    if (name) name.textContent = category.value ? `— ${category.value}` : '';
    fieldset.querySelectorAll('[data-tag-category]').forEach(label => {
      const visible = label.dataset.tagCategory === category.value;
      label.hidden = !visible;
      label.querySelector('input').disabled = !visible;
      if (!visible) label.querySelector('input').checked = false;
      if (visible) count += 1;
    });
    const empty = fieldset.querySelector('[data-tags-empty]');
    empty.hidden = count > 0;
    empty.textContent = category.value
      ? 'No tags have been listed for this category yet.'
      : 'Select a category to see its listed tags.';
  };
  category.addEventListener('change', update);
  update();
});
