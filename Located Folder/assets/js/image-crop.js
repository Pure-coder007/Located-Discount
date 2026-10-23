// Lets vendors crop each chosen deal photo, then shrinks it to a JPEG of at
// most 1600px so uploads stay small on mobile data.
(() => {
  const MAX_EDGE = 1600;
  const QUALITY = 0.85;
  const SHAPES = [
    { label: "Card 3:2", ratio: 1.5 },
    { label: "Square", ratio: 1 },
    { label: "Full photo", ratio: null },
  ];

  const loadImage = (file) => new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file);
    const image = new Image();
    image.onload = () => resolve({ image, url });
    image.onerror = () => { URL.revokeObjectURL(url); reject(new Error("unreadable")); };
    image.src = url;
  });

  const encode = (image, sx, sy, sw, sh, name) => new Promise((resolve) => {
    const scale = Math.min(1, MAX_EDGE / Math.max(sw, sh));
    const canvas = document.createElement("canvas");
    canvas.width = Math.max(1, Math.round(sw * scale));
    canvas.height = Math.max(1, Math.round(sh * scale));
    const context = canvas.getContext("2d");
    context.fillStyle = "#fff";
    context.fillRect(0, 0, canvas.width, canvas.height);
    context.drawImage(image, sx, sy, sw, sh, 0, 0, canvas.width, canvas.height);
    canvas.toBlob((blob) => {
      if (!blob) { resolve(null); return; }
      const base = name.replace(/\.[^.]+$/, "") || "deal-photo";
      resolve(new File([blob], `${base}.jpg`, { type: "image/jpeg", lastModified: Date.now() }));
    }, "image/jpeg", QUALITY);
  });

  const cropOne = (file, position, total) => new Promise(async (resolve) => {
    let loaded;
    try { loaded = await loadImage(file); } catch { resolve(null); return; }
    const { image, url } = loaded;
    const width = image.naturalWidth;
    const height = image.naturalHeight;

    const dialog = document.createElement("div");
    dialog.className = "image-crop-backdrop";
    dialog.innerHTML = `
      <div class="image-crop-dialog" role="dialog" aria-modal="true" aria-labelledby="image-crop-title">
        <div class="image-crop-head"><h3 id="image-crop-title">Crop photo ${position} of ${total}</h3><p>Drag to position. Pinch or use the slider to zoom.</p></div>
        <div class="image-crop-shapes" role="group" aria-label="Photo shape"></div>
        <div class="image-crop-stage"><div class="image-crop-frame"><img alt="Photo being cropped" draggable="false"></div></div>
        <label class="image-crop-zoom"><i class="bi bi-zoom-out"></i><input type="range" min="1" max="4" step="0.01" value="1" aria-label="Zoom"><i class="bi bi-zoom-in"></i></label>
        <div class="image-crop-actions"><button type="button" data-crop-skip>Remove photo</button><button type="button" data-crop-use><i class="bi bi-check2"></i> Use photo</button></div>
      </div>`;
    document.body.append(dialog);
    document.body.classList.add("image-crop-open");
    const stage = dialog.querySelector(".image-crop-stage");
    const frame = dialog.querySelector(".image-crop-frame");
    const preview = frame.querySelector("img");
    const zoom = dialog.querySelector('input[type="range"]');
    const shapes = dialog.querySelector(".image-crop-shapes");
    preview.src = url;

    let ratio = SHAPES[0].ratio;
    let frameW = 0; let frameH = 0; let base = 1; let scale = 1; let x = 0; let y = 0;

    const clamp = () => {
      x = Math.min(0, Math.max(frameW - width * scale, x));
      y = Math.min(0, Math.max(frameH - height * scale, y));
    };
    const paint = () => {
      clamp();
      preview.style.width = `${width * scale}px`;
      preview.style.height = `${height * scale}px`;
      preview.style.transform = `translate(${x}px, ${y}px)`;
    };
    const layout = () => {
      const maxW = stage.clientWidth;
      const maxH = Math.max(180, Math.min(window.innerHeight * 0.5, 460));
      const shape = ratio || width / height;
      frameW = Math.min(maxW, maxH * shape);
      frameH = frameW / shape;
      frame.style.width = `${frameW}px`;
      frame.style.height = `${frameH}px`;
      base = Math.max(frameW / width, frameH / height);
      scale = base * Number(zoom.value);
      x = (frameW - width * scale) / 2;
      y = (frameH - height * scale) / 2;
      paint();
    };
    const zoomTo = (next, cx = frameW / 2, cy = frameH / 2) => {
      const target = Math.min(base * 4, Math.max(base, next));
      x = cx - (cx - x) * (target / scale);
      y = cy - (cy - y) * (target / scale);
      scale = target;
      zoom.value = String(scale / base);
      paint();
    };

    SHAPES.forEach((shape, index) => {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = shape.label;
      button.setAttribute("aria-pressed", index === 0 ? "true" : "false");
      button.addEventListener("click", () => {
        ratio = shape.ratio;
        shapes.querySelectorAll("button").forEach((item) => item.setAttribute("aria-pressed", item === button ? "true" : "false"));
        zoom.value = "1";
        zoom.disabled = !ratio;
        layout();
      });
      shapes.append(button);
    });

    const pointers = new Map();
    let pinch = null;
    frame.addEventListener("pointerdown", (event) => {
      frame.setPointerCapture(event.pointerId);
      pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
      if (pointers.size === 2) {
        const [a, b] = [...pointers.values()];
        pinch = { distance: Math.hypot(a.x - b.x, a.y - b.y), scale };
      }
    });
    frame.addEventListener("pointermove", (event) => {
      const last = pointers.get(event.pointerId);
      if (!last || !ratio) return;
      const now = { x: event.clientX, y: event.clientY };
      pointers.set(event.pointerId, now);
      if (pointers.size === 2 && pinch) {
        const [a, b] = [...pointers.values()];
        const box = frame.getBoundingClientRect();
        zoomTo(pinch.scale * (Math.hypot(a.x - b.x, a.y - b.y) / pinch.distance), (a.x + b.x) / 2 - box.left, (a.y + b.y) / 2 - box.top);
      } else if (pointers.size === 1) {
        x += now.x - last.x;
        y += now.y - last.y;
        paint();
      }
    });
    const release = (event) => { pointers.delete(event.pointerId); if (pointers.size < 2) pinch = null; };
    frame.addEventListener("pointerup", release);
    frame.addEventListener("pointercancel", release);
    zoom.addEventListener("input", () => zoomTo(base * Number(zoom.value)));
    window.addEventListener("resize", layout);

    const finish = async (use) => {
      window.removeEventListener("resize", layout);
      let result = null;
      if (use) {
        result = ratio
          ? await encode(image, -x / scale, -y / scale, frameW / scale, frameH / scale, file.name)
          : await encode(image, 0, 0, width, height, file.name);
      }
      URL.revokeObjectURL(url);
      dialog.remove();
      document.body.classList.remove("image-crop-open");
      resolve(result);
    };
    dialog.querySelector("[data-crop-use]").addEventListener("click", () => finish(true));
    dialog.querySelector("[data-crop-skip]").addEventListener("click", () => finish(false));
    requestAnimationFrame(layout);
    dialog.querySelector("[data-crop-use]").focus();
  });

  window.ldCropImages = async (files) => {
    const cropped = [];
    for (const [index, file] of files.entries()) {
      const result = await cropOne(file, index + 1, files.length);
      if (result) cropped.push(result);
    }
    return cropped;
  };
})();
