// Keeps an unsent deal form on the vendor's device. Phones often unload the
// page while the photo gallery is open; the draft brings it back on return.
// Text fields live in localStorage, chosen photos in IndexedDB. The server
// clears the draft (data-clear-deal-draft) only after a successful submit.
(() => {
  const PREFIX = "ld-deal-draft:";
  const MAX_AGE_MS = 3 * 86_400_000;
  const SKIPPED = new Set(["csrf_token", "images", "accept_policies", "expiry_timezone_offset"]);

  const storage = {
    get(key) { try { return JSON.parse(localStorage.getItem(key) || "null"); } catch { return null; } },
    set(key, value) { try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* storage full or blocked */ } },
    remove(key) { try { localStorage.removeItem(key); } catch { /* blocked */ } },
  };

  const openPhotos = () => new Promise((resolve) => {
    try {
      const request = indexedDB.open("ld-deal-drafts", 1);
      request.onupgradeneeded = () => request.result.createObjectStore("photos");
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => resolve(null);
    } catch { resolve(null); }
  });
  const photos = async (mode, key, value) => {
    const db = await openPhotos();
    if (!db) return null;
    return new Promise((resolve) => {
      try {
        const store = db.transaction("photos", mode === "get" ? "readonly" : "readwrite").objectStore("photos");
        const request = mode === "get" ? store.get(key) : mode === "put" ? store.put(value, key) : store.delete(key);
        request.onsuccess = () => resolve(request.result || null);
        request.onerror = () => resolve(null);
      } catch { resolve(null); }
    });
  };

  const clearDraft = (key) => { storage.remove(key); photos("delete", key); };

  document.addEventListener("DOMContentLoaded", () => {
    const done = document.querySelector("[data-clear-deal-draft]")?.dataset.clearDealDraft;
    if (done) clearDraft(PREFIX + done);

    const form = document.querySelector("form[data-deal-draft]");
    if (!form) return;
    const key = PREFIX + form.dataset.dealDraft;
    const rerender = form.dataset.draftRerender === "1";
    const imageInput = form.querySelector('input[type="file"][name="images"]');
    const fields = () => Array.from(form.elements).filter((el) => el.name && !SKIPPED.has(el.name) && el.type !== "file" && el.type !== "submit" && el.type !== "button");

    const saveFields = () => {
      const values = fields().map((el) => (el.type === "checkbox" || el.type === "radio")
        ? { name: el.name, value: el.value, checked: el.checked }
        : { name: el.name, value: el.value });
      storage.set(key, { savedAt: Date.now(), values });
    };
    const savePhotos = () => {
      if (!imageInput) return;
      const files = Array.from(imageInput.files || []);
      if (files.length) photos("put", key, files); else photos("delete", key);
    };
    let timer;
    const saveSoon = () => { clearTimeout(timer); timer = setTimeout(saveFields, 400); };
    const saveNow = () => { clearTimeout(timer); saveFields(); };

    const restoreFields = (values) => {
      const all = fields();
      const apply = (el) => {
        const saved = values.find((item) => item.name === el.name && ("checked" in item ? item.value === el.value : true));
        if (!saved) return false;
        if ("checked" in saved) el.checked = saved.checked; else el.value = saved.value;
        return true;
      };
      // Selects first: the category change rebuilds which tags can be ticked.
      all.filter((el) => el.tagName === "SELECT").forEach((el) => { if (apply(el)) el.dispatchEvent(new Event("change", { bubbles: true })); });
      all.filter((el) => el.tagName !== "SELECT").forEach((el) => { if (apply(el)) el.dispatchEvent(new Event("input", { bubbles: true })); });
    };
    const restorePhotos = async () => {
      if (!imageInput || (imageInput.files && imageInput.files.length) || typeof DataTransfer === "undefined") return false;
      const files = await photos("get", key);
      if (!Array.isArray(files) || !files.length) return false;
      const transfer = new DataTransfer();
      files.slice(0, 5).forEach((file) => transfer.items.add(file));
      imageInput.files = transfer.files;
      imageInput.dataset.skipCrop = "1";
      imageInput.dispatchEvent(new Event("change", { bubbles: true }));
      return true;
    };
    const showNotice = () => {
      const notice = document.createElement("div");
      notice.className = "deal-draft-notice sm:col-span-2";
      notice.setAttribute("role", "status");
      notice.innerHTML = '<i class="bi bi-arrow-counterclockwise"></i><span>We restored the deal details you had not submitted yet.</span>';
      const discard = document.createElement("button");
      discard.type = "button";
      discard.textContent = "Start over";
      discard.addEventListener("click", () => { clearDraft(key); window.location.reload(); });
      notice.append(discard);
      form.prepend(notice);
    };

    // Run after the other form scripts have set their initial values.
    setTimeout(async () => {
      const draft = storage.get(key);
      const fresh = draft && Date.now() - draft.savedAt < MAX_AGE_MS;
      if (draft && !fresh) clearDraft(key);
      // After a failed submit the server re-fills the text; only photos are lost.
      if (fresh && !rerender) restoreFields(draft.values || []);
      const restoredPhotos = fresh || rerender ? await restorePhotos() : false;
      if ((fresh && !rerender) || restoredPhotos) showNotice();

      form.addEventListener("input", saveSoon);
      form.addEventListener("change", saveSoon);
      form.addEventListener("deal-images-updated", savePhotos);
      form.addEventListener("submit", saveNow);
      document.addEventListener("visibilitychange", () => { if (document.visibilityState === "hidden") saveNow(); });
      window.addEventListener("pagehide", saveNow);
    }, 0);
  });
})();
