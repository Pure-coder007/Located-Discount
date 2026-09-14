document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll("[data-image-preview]").forEach((input) => {
    const preview = document.getElementById(input.dataset.imagePreview);
    const image = preview?.querySelector("img");
    if (!preview || !image) return;

    input.addEventListener("change", () => {
      const file = input.files?.[0];
      if (image.dataset.objectUrl) {
        URL.revokeObjectURL(image.dataset.objectUrl);
        delete image.dataset.objectUrl;
      }
      if (!file || !file.type.startsWith("image/")) {
        image.removeAttribute("src");
        preview.hidden = true;
        return;
      }
      const objectUrl = URL.createObjectURL(file);
      image.dataset.objectUrl = objectUrl;
      image.src = objectUrl;
      preview.hidden = false;
    });
  });

  document.querySelectorAll("[data-image-previews]").forEach((input) => {
    const preview = document.getElementById(input.dataset.imagePreviews);
    if (!preview) return;

    const clearPreview = () => {
      preview.querySelectorAll("img[data-object-url]").forEach((image) => {
        URL.revokeObjectURL(image.dataset.objectUrl);
      });
      preview.replaceChildren();
      preview.hidden = true;
    };

    input.addEventListener("change", () => {
      clearPreview();
      const files = Array.from(input.files || []).filter((file) => file.type.startsWith("image/")).slice(0, 5);
      if (!files.length) return;

      files.forEach((file, index) => {
        const figure = document.createElement("figure");
        const image = document.createElement("img");
        const caption = document.createElement("figcaption");
        const objectUrl = URL.createObjectURL(file);
        image.src = objectUrl;
        image.dataset.objectUrl = objectUrl;
        image.alt = `Selected deal image ${index + 1}`;
        caption.textContent = file.name;
        figure.append(image, caption);
        preview.append(figure);
      });
      preview.hidden = false;
    });
  });
});
