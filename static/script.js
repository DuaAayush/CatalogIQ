// script.js -> poore page ka kaam. Koi framework nahi, sirf fetch().

const CATEGORIES = ["Groceries", "Beverages", "Personal Care", "Household",
                    "Electronics", "Fashion", "Home & Kitchen", "Other"];
const PAGE_SIZE = 10;
const POLL_EVERY_MS = 1000;      // job progress kitni der baad poochna (1 second se kam nahi)
const SEARCH_WAIT_MS = 400;      // typing rukne ke itni der baad hi search

let currentPage = 1;
let totalProducts = 0;
let shownProducts = [];          // abhi screen par dikh rahe products

function byId(id) { return document.getElementById(id); }

// ---------- chhoti helper cheezein ----------
function fillCategoryDropdowns() {
  for (const name of CATEGORIES) {
    byId("category-filter").add(new Option(name, name));
    byId("edit-category").add(new Option(name, name));
  }
}

// Search box: har key par nahi, jab user ruk jaye tab.
function debounce(work, waitMs) {
  let timer;
  return function () {
    clearTimeout(timer);
    timer = setTimeout(work, waitMs);
  };
}

// ---------- 1. CSV upload ----------
// CSV text ko rows (list of lists) me todta hai. Quotes ("a,b") aur "" ko samajhta hai.
function splitCsvIntoRows(text) {
  if (text.charCodeAt(0) === 0xFEFF) text = text.slice(1);      // Excel ka BOM hatao
  const rows = [];
  let row = [], cell = "", insideQuotes = false;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (insideQuotes) {
      if (ch === '"' && text[i + 1] === '"') { cell += '"'; i++; }
      else if (ch === '"') insideQuotes = false;
      else cell += ch;
    } else if (ch === '"') insideQuotes = true;
    else if (ch === ",") { row.push(cell); cell = ""; }
    else if (ch === "\n" || ch === "\r") {
      if (ch === "\r" && text[i + 1] === "\n") i++;
      row.push(cell); rows.push(row); row = []; cell = "";
    } else cell += ch;
  }
  if (cell !== "" || row.length > 0) { row.push(cell); rows.push(row); }
  return rows;
}

// Rows ko products me badlo: {sku, raw_title, raw_description}
function csvToProducts(text) {
  const rows = splitCsvIntoRows(text);
  if (rows.length === 0) return { products: [], skipped: 0, missingColumns: true };
  const headers = rows[0].map(h => h.trim().toLowerCase());
  const skuAt = headers.indexOf("sku");
  const titleAt = headers.indexOf("raw_title");
  const descriptionAt = headers.indexOf("raw_description");
  if (skuAt < 0 || titleAt < 0) return { products: [], skipped: 0, missingColumns: true };

  const products = [];
  let skipped = 0;
  for (const row of rows.slice(1)) {
    if (row.every(cell => cell.trim() === "")) continue;          // khaali line
    const sku = (row[skuAt] || "").trim();
    const title = row[titleAt] || "";
    if (!sku || !title.trim()) { skipped++; continue; }
    const description = descriptionAt >= 0 ? (row[descriptionAt] || "") : "";
    products.push({ sku: sku, raw_title: title, raw_description: description === "" ? null : description });
  }
  return { products: products, skipped: skipped, missingColumns: false };
}

async function uploadCsv() {
  const file = byId("csv-file").files[0];
  const message = byId("upload-message");
  if (!file) { message.textContent = "Please choose a CSV file first."; return; }

  const parsed = csvToProducts(await file.text());
  if (parsed.missingColumns) { message.textContent = "CSV must have the columns sku and raw_title."; return; }
  if (parsed.products.length === 0) { message.textContent = "No valid rows found in the CSV."; return; }

  message.textContent = "Uploading " + parsed.products.length + " products...";
  try {
    const response = await fetch("/api/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ products: parsed.products }),
    });
    const job = await response.json();
    if (!response.ok) { message.textContent = "Upload failed: " + job.error; return; }
    message.textContent = "Job " + job.id + " started" +
      (parsed.skipped ? " (" + parsed.skipped + " rows skipped: sku or title missing)." : ".");
    byId("progress-box").hidden = false;
    watchJob(job.id);
  } catch (error) {
    message.textContent = "Upload failed: could not reach the server.";
  }
}

// ---------- 2. Live progress (polling) ----------
async function watchJob(jobId) {
  let job = null;
  try {
    const response = await fetch("/api/jobs/" + jobId);
    job = await response.json();
    if (!response.ok) { byId("progress-text").textContent = job.error; return; }
    showProgress(job);
  } catch (error) {
    byId("progress-text").textContent = "Server not reachable, retrying...";
  }
  if (job && job.status === "completed") {
    loadProducts();                                   // job khatam -> catalogue ta-za karo
  } else {
    setTimeout(function () { watchJob(jobId); }, POLL_EVERY_MS);   // 1 second baad phir pucho
  }
}

function showProgress(job) {
  const percent = job.total ? Math.round(job.done * 100 / job.total) : 0;
  byId("progress-bar").value = percent;
  byId("progress-text").textContent =
    "Status: " + job.status + " | Done: " + job.done + "/" + job.total +
    " | Failed: " + job.failed + " | Cache hits: " + job.cache_hits;
}

// ---------- 3. Catalogue list ----------
async function loadProducts() {
  const params = new URLSearchParams({ page: currentPage, page_size: PAGE_SIZE });
  const searchText = byId("search-box").value.trim();
  const category = byId("category-filter").value;
  if (searchText) params.set("q", searchText);
  if (category) params.set("category", category);

  try {
    const response = await fetch("/api/products?" + params);
    const data = await response.json();
    if (!response.ok) { byId("list-message").textContent = data.error; return; }
    shownProducts = data.items;
    totalProducts = data.total;
    showProducts();
  } catch (error) {
    byId("list-message").textContent = "Could not load products.";
  }
}

function showProducts() {
  const list = byId("product-list");
  list.innerHTML = "";
  byId("list-message").textContent = totalProducts + " products found";

  shownProducts.forEach(function (product, index) {
    const item = document.createElement("li");

    const info = document.createElement("div");
    info.className = "product-info";
    const title = document.createElement("strong");
    title.textContent = product.clean_title || product.raw_title;
    const meta = document.createElement("div");
    meta.className = "product-meta";
    meta.textContent = product.sku + " | " + (product.category || "no category") + " | ";
    const badge = document.createElement("span");
    badge.className = "badge " + product.status;
    badge.textContent = product.status;
    meta.appendChild(badge);
    info.append(title, meta);
    if (product.status === "failed") {
      const error = document.createElement("div");
      error.className = "error";
      error.textContent = "Error: " + (product.error || "unknown");
      info.appendChild(error);
    }

    const button = document.createElement("button");
    button.type = "button";
    button.textContent = "Review";
    button.setAttribute("aria-label", "Review " + product.sku);
    button.addEventListener("click", function () { openReview(index); });

    item.append(info, button);
    list.appendChild(item);
  });

  const lastPage = Math.max(1, Math.ceil(totalProducts / PAGE_SIZE));
  byId("page-text").textContent = "Page " + currentPage + " of " + lastPage;
  byId("previous-button").disabled = currentPage <= 1;
  byId("next-button").disabled = currentPage >= lastPage;
}

function goToFirstPageAndLoad() {
  currentPage = 1;
  loadProducts();
}

// ---------- 4. Review + approve ----------
let reviewingSku = null;

function openReview(index) {
  const product = shownProducts[index];
  reviewingSku = product.sku;

  byId("raw-sku").textContent = product.sku;
  byId("raw-title").textContent = product.raw_title;
  byId("raw-description").textContent = product.raw_description || "(none)";
  byId("review-status").textContent = product.status;
  byId("review-brand").textContent = product.brand || "(unknown)";
  byId("review-error").hidden = product.status !== "failed";
  byId("review-error").textContent = "Error: " + (product.error || "");

  byId("edit-title").value = product.clean_title || product.raw_title;
  byId("edit-category").value = product.category || "Other";
  byId("edit-tags").value = (product.tags || []).join(", ");
  byId("review-message").textContent = "";

  byId("review-empty").hidden = true;
  byId("review-form").hidden = false;
  byId("review-heading").focus();                      // keyboard user ko seedha review par le jao
  byId("review-heading").scrollIntoView();
}

async function saveAndApprove(event) {
  event.preventDefault();
  const tags = byId("edit-tags").value.split(",").map(t => t.trim()).filter(t => t);
  const changes = {
    clean_title: byId("edit-title").value,
    category: byId("edit-category").value,
    tags: tags,
  };
  try {
    const response = await fetch("/api/products/" + encodeURIComponent(reviewingSku), {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(changes),
    });
    const data = await response.json();
    if (!response.ok) { byId("review-message").textContent = "Error: " + data.error; return; }
    byId("review-message").textContent = "Saved. Status is now: " + data.status;
    byId("review-status").textContent = data.status;
    byId("review-error").hidden = true;
    loadProducts();
  } catch (error) {
    byId("review-message").textContent = "Could not save. Is the server running?";
  }
}

// ---------- sab ko jodo ----------
fillCategoryDropdowns();
byId("upload-button").addEventListener("click", uploadCsv);
byId("search-box").addEventListener("input", debounce(goToFirstPageAndLoad, SEARCH_WAIT_MS));
byId("category-filter").addEventListener("change", goToFirstPageAndLoad);
byId("previous-button").addEventListener("click", function () { currentPage--; loadProducts(); });
byId("next-button").addEventListener("click", function () { currentPage++; loadProducts(); });
byId("review-form").addEventListener("submit", saveAndApprove);
loadProducts();
