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
});
