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
    let selectedFiles = [];
    const counter = document.getElementById("deal-image-limit");
    const addMore = document.getElementById("deal-add-image");

    const syncInputFiles = () => {
      const transfer = new DataTransfer();
      selectedFiles.forEach((file) => transfer.items.add(file));
      input.files = transfer.files;
    };

    const renderPreviews = () => {
      preview.replaceChildren();
      selectedFiles.forEach((file, index) => {
        const figure = document.createElement("figure");
        const image = document.createElement("img");
        const caption = document.createElement("figcaption");
        const remove = document.createElement("button");
        image.alt = `Selected deal image ${index + 1}`;
        caption.textContent = file.name;
        remove.type = "button";
        remove.className = "deal-image-remove";
        remove.innerHTML = "<i class=\"bi bi-x-lg\"></i> Remove";
        remove.setAttribute("aria-label", `Remove ${file.name}`);
        remove.addEventListener("click", () => {
          selectedFiles.splice(index, 1);
          syncInputFiles();
          renderPreviews();
        });
        figure.append(image, caption, remove);
        preview.append(figure);
        const reader = new FileReader();
        reader.addEventListener("load", () => { image.src = reader.result; });
        reader.readAsDataURL(file);
      });
      preview.hidden = selectedFiles.length === 0;
      preview.setAttribute("aria-hidden", selectedFiles.length === 0 ? "true" : "false");
      if (counter) {
        counter.textContent = `${selectedFiles.length} of 5 images selected`;
        counter.classList.remove("is-error");
      }
      if (addMore) {
        addMore.disabled = selectedFiles.length >= 5;
        addMore.setAttribute("aria-disabled", addMore.disabled ? "true" : "false");
        addMore.innerHTML = addMore.disabled ? '<i class="bi bi-check2"></i> 5 images complete' : '<i class="bi bi-plus-lg"></i> Add more images';
      }
    };

    input.addEventListener("change", () => {
      const incoming = Array.from(input.files || []).filter((file) => file.type.startsWith("image/"));
      const available = Math.max(0, 5 - selectedFiles.length);
      selectedFiles = selectedFiles.concat(incoming.slice(0, available));
      syncInputFiles();
      renderPreviews();
      if (counter && incoming.length > available) {
        counter.textContent = "Maximum 5 images. Remove an image before adding another.";
        counter.classList.add("is-error");
      }
    });
    addMore?.addEventListener("click", () => {
      if (selectedFiles.length < 5) input.click();
    });
  });
});
