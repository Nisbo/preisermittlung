const CARD_VERSION = "0.3.7";
const CARD_TYPE = "preisermittlung-card";

const DEFAULT_CONFIG = {
  title: "Preisermittlung",
  entity_prefix: "sensor.preisermittlung_",
  service_url: "",
  category_id: "",
  show_category_filter: true,
  show_category_dropdown: true,
  show_category_multi_filter: true,
  show_search: true,
  show_target_price_filter: true,
  target_price_filter_active: false,
  group_by_category: false,
  extra_matches_display: "wrap",
  extra_matches_expanded: true,
  target_price_highlight_enabled: true,
  target_price_missed_display: "normal",
  target_price_extra_matches_enabled: false,
  compact: false,
  image_size: 48,
  sort_by: "name",
  sort_dir: "asc",
  columns: ["image", "name", "provider", "shop", "price", "last_changed"],
};

const COLUMN_DEFINITIONS = [
  { key: "image", label: "Bild" },
  { key: "name", label: "Artikel" },
  { key: "provider", label: "Anbieter" },
  { key: "shop", label: "Shop" },
  { key: "shop_detail", label: "Shopdetails" },
  { key: "price", label: "Preis" },
  { key: "target_price", label: "Wunschpreis" },
  { key: "unit_price", label: "Grundpreis" },
  { key: "package_size", label: "Packung" },
  { key: "category", label: "Kategorie" },
  { key: "status", label: "Status" },
  { key: "last_checked", label: "Geprüft" },
  { key: "last_changed", label: "Geändert" },
];

const SORT_OPTIONS = [
  { key: "name", label: "Artikel" },
  { key: "provider", label: "Anbieter" },
  { key: "shop", label: "Shop" },
  { key: "shop_detail", label: "Shopdetails" },
  { key: "price", label: "Preis" },
  { key: "target_price", label: "Wunschpreis" },
  { key: "category", label: "Kategorie" },
  { key: "last_checked", label: "Geprüft" },
  { key: "last_changed", label: "Geändert" },
  { key: "status", label: "Status" },
];

function normalizeConfig(config) {
  const merged = { ...DEFAULT_CONFIG, ...(config || {}) };
  merged.columns = Array.isArray(merged.columns) && merged.columns.length
    ? merged.columns.filter((column) => COLUMN_DEFINITIONS.some((item) => item.key === column))
    : [...DEFAULT_CONFIG.columns];
  merged.image_size = Math.max(24, Math.min(96, Number(merged.image_size) || DEFAULT_CONFIG.image_size));
  merged.sort_by = SORT_OPTIONS.some((item) => item.key === merged.sort_by) ? merged.sort_by : DEFAULT_CONFIG.sort_by;
  merged.sort_dir = merged.sort_dir === "desc" ? "desc" : "asc";
  merged.service_url = String(merged.service_url || "").trim().replace(/\/+$/, "");
  if (!["wrap", "slider", "off"].includes(merged.extra_matches_display)) merged.extra_matches_display = DEFAULT_CONFIG.extra_matches_display;
  if (!["hide", "normal", "muted"].includes(merged.target_price_missed_display)) merged.target_price_missed_display = DEFAULT_CONFIG.target_price_missed_display;
  merged.extra_matches_expanded = merged.extra_matches_expanded !== false;
  merged.target_price_highlight_enabled = merged.target_price_highlight_enabled !== false;
  merged.target_price_extra_matches_enabled = merged.target_price_extra_matches_enabled === true;
  merged.show_target_price_filter = merged.show_target_price_filter !== false;
  merged.target_price_filter_active = merged.target_price_filter_active === true;
  merged.show_search = merged.show_search !== false;
  merged.show_category_filter = merged.show_category_filter !== false;
  merged.show_category_dropdown = merged.show_category_dropdown !== false;
  merged.show_category_multi_filter = merged.show_category_multi_filter !== false;
  merged.category_id = String(merged.category_id || "").trim();
  return merged;
}

function parseCategoryIds(value) {
  if (Array.isArray(value)) {
    return value.map((item) => String(item || "").trim()).filter(Boolean);
  }
  return String(value || "")
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

function nullableNumber(value) {
  if (value === null || value === undefined || value === "") return null;
  const number = Number(String(value).replace(",", "."));
  return Number.isFinite(number) ? number : null;
}

function isFalseLike(value) {
  if (value === false) return true;
  return ["false", "0", "off", "no", "nein"].includes(String(value ?? "").trim().toLowerCase());
}

function isNoOfferAttributes(attr, stateValue) {
  const error = String(attr.error || "").trim().toLowerCase();
  const status = String(attr.status || "").trim().toLowerCase();
  const priceText = String(attr.price_text || "").trim();
  const priceCents = nullableNumber(attr.price_cents);
  const price = nullableNumber(attr.price);
  return (
    isFalseLike(attr.available)
    || error === "kein angebot"
    || status === "kein angebot"
    || (
      status === "error"
      && priceCents === null
      && price === null
      && (priceText === "-" || ["unknown", "unavailable", ""].includes(String(stateValue || "").trim().toLowerCase()))
    )
  );
}

function entityToProduct(entityId, stateObj) {
  const attr = stateObj.attributes || {};
  const providerName = attr.provider_name || attr.provider || "";
  const shop = attr.shop || "";
  const shopDetail = attr.shop_detail || "";
  const noOffer = isNoOfferAttributes(attr, stateObj.state);
  const rawTargetPrice = nullableNumber(attr.target_price);
  const rawTargetPriceCents = nullableNumber(attr.target_price_cents);
  const targetPriceCents = rawTargetPriceCents && rawTargetPriceCents > 0 ? rawTargetPriceCents : null;
  const targetPrice = targetPriceCents !== null
    ? (rawTargetPrice && rawTargetPrice > 0 ? rawTargetPrice : targetPriceCents / 100)
    : null;
  const priceCents = noOffer ? null : nullableNumber(attr.price_cents);
  const belowTarget = typeof attr.below_target_price === "boolean"
    ? attr.below_target_price && targetPriceCents !== null && !noOffer
    : (targetPriceCents !== null && priceCents !== null ? priceCents <= targetPriceCents : false);
  const status = noOffer ? "Kein Angebot" : (attr.status || (stateObj.state === "unavailable" ? "unavailable" : "ok"));
  return {
    entityId,
    state: stateObj.state,
    name: attr.name || attr.friendly_name || entityId,
    providerName,
    shopDetail,
    shop,
    sourceType: attr.source_type || "",
    categoryId: attr.category_id || "",
    category: attr.category || attr.category_id || "Allgemein",
    categoryShowInGrouped: attr.category_show_in_grouped !== false,
    categorySearchable: attr.category_searchable !== false,
    categoryGroupExpanded: attr.category_group_expanded !== false,
    price: priceCents !== null ? priceCents / 100 : null,
    priceCents,
    targetPrice,
    targetPriceCents,
    targetPriceText: targetPriceCents !== null ? (attr.target_price_text || "") : "",
    belowTargetPrice: belowTarget,
    unitPrice: noOffer ? "" : (attr.unit_price || ""),
    packageSize: noOffer ? "" : (attr.package_size || ""),
    imageUrl: noOffer ? "" : (attr.image_url || ""),
    pdfPage: noOffer ? "" : (attr.pdf_page || ""),
    pdfFile: noOffer ? "" : (attr.pdf_file || ""),
    articleNumber: attr.article_number || "",
    searchTerm: attr.search_term || "",
    matches: noOffer ? [] : (Array.isArray(attr.extra_matches) ? attr.extra_matches : (Array.isArray(attr.matches) ? attr.matches : [])),
    matchCount: noOffer ? 0 : Number(attr.match_count || 0),
    status,
    noOffer,
    lastChecked: attr.last_checked || "",
    lastChanged: attr.last_changed || "",
    url: noOffer ? "" : (attr.url || ""),
    available: noOffer ? false : attr.available,
  };
}

function formatDate(value) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return new Intl.DateTimeFormat("de-DE", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function formatPrice(product) {
  if (product.noOffer) return "Kein Angebot";
  if (product.priceCents === null) return "-";
  if (!Number.isFinite(product.price)) return product.state || "-";
  return new Intl.NumberFormat("de-DE", {
    style: "currency",
    currency: "EUR",
  }).format(product.price);
}

function formatCents(cents) {
  if (!Number.isFinite(Number(cents))) return "-";
  return new Intl.NumberFormat("de-DE", {
    style: "currency",
    currency: "EUR",
  }).format(Number(cents) / 100);
}

function matchPriceCents(match) {
  if (Number.isFinite(Number(match.price_cents))) return Number(match.price_cents);
  if (Number.isFinite(Number(match.price))) return Math.round(Number(match.price) * 100);
  return null;
}

function statusClass(value) {
  return String(value || "unknown").toLowerCase().replace(/[^a-z0-9_-]+/g, "-").replace(/^-+|-+$/g, "") || "unknown";
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function compareProducts(sortBy, sortDir) {
  const direction = sortDir === "desc" ? -1 : 1;
  return (left, right) => {
    let leftValue;
    let rightValue;
    if (sortBy === "price") {
      leftValue = Number.isFinite(left.price) ? left.price : Number.POSITIVE_INFINITY;
      rightValue = Number.isFinite(right.price) ? right.price : Number.POSITIVE_INFINITY;
    } else if (sortBy === "target_price") {
      leftValue = left.targetPriceCents !== null ? Number(left.targetPriceCents) : Number.POSITIVE_INFINITY;
      rightValue = right.targetPriceCents !== null ? Number(right.targetPriceCents) : Number.POSITIVE_INFINITY;
    } else if (sortBy === "provider") {
      leftValue = left.providerName;
      rightValue = right.providerName;
    } else if (sortBy === "shop") {
      leftValue = left.shop;
      rightValue = right.shop;
    } else if (sortBy === "shop_detail") {
      leftValue = left.shopDetail;
      rightValue = right.shopDetail;
    } else if (sortBy === "category") {
      leftValue = left.category;
      rightValue = right.category;
    } else if (sortBy === "last_checked") {
      leftValue = Date.parse(left.lastChecked) || 0;
      rightValue = Date.parse(right.lastChecked) || 0;
    } else if (sortBy === "last_changed") {
      leftValue = Date.parse(left.lastChanged) || 0;
      rightValue = Date.parse(right.lastChanged) || 0;
    } else if (sortBy === "status") {
      leftValue = left.status;
      rightValue = right.status;
    } else {
      leftValue = left.name;
      rightValue = right.name;
    }

    if (typeof leftValue === "number" && typeof rightValue === "number") {
      return (leftValue - rightValue) * direction;
    }
    return String(leftValue).localeCompare(String(rightValue), "de", { sensitivity: "base" }) * direction;
  };
}

class PreisermittlungCard extends HTMLElement {
  static getConfigForm() {
    const columnOptions = COLUMN_DEFINITIONS.map((column) => ({ label: column.label, value: column.key }));
    const sortOptions = SORT_OPTIONS.map((option) => ({ label: option.label, value: option.key }));
    return {
      schema: [
        { name: "title", selector: { text: {} } },
        { name: "service_url", selector: { text: {} } },
        { name: "category_id", selector: { text: {} } },
        {
          type: "grid",
          name: "",
          flatten: true,
          schema: [
            { name: "sort_by", selector: { select: { mode: "dropdown", options: sortOptions } } },
            { name: "sort_dir", selector: { select: { mode: "dropdown", options: [{ label: "Aufsteigend", value: "asc" }, { label: "Absteigend", value: "desc" }] } } },
            { name: "image_size", selector: { number: { min: 24, max: 96, step: 4, mode: "box" } } },
          ],
        },
        {
          type: "grid",
          name: "",
          flatten: true,
          schema: [
            { name: "show_category_filter", selector: { boolean: {} } },
            { name: "show_category_dropdown", selector: { boolean: {} } },
            { name: "show_category_multi_filter", selector: { boolean: {} } },
            { name: "show_search", selector: { boolean: {} } },
            { name: "show_target_price_filter", selector: { boolean: {} } },
            { name: "target_price_filter_active", selector: { boolean: {} } },
            { name: "group_by_category", selector: { boolean: {} } },
            { name: "compact", selector: { boolean: {} } },
          ],
        },
        { name: "extra_matches_display", selector: { select: { mode: "dropdown", options: [{ label: "Umbruch", value: "wrap" }, { label: "Slider", value: "slider" }, { label: "Aus", value: "off" }] } } },
        { name: "extra_matches_expanded", selector: { boolean: {} } },
        {
          type: "grid",
          name: "",
          flatten: true,
          schema: [
            { name: "target_price_highlight_enabled", selector: { boolean: {} } },
            { name: "target_price_extra_matches_enabled", selector: { boolean: {} } },
          ],
        },
        { name: "target_price_missed_display", selector: { select: { mode: "dropdown", options: [{ label: "nicht anzeigen", value: "hide" }, { label: "normal anzeigen", value: "normal" }, { label: "ausgegraut anzeigen", value: "muted" }] } } },
        { name: "columns", selector: { select: { multiple: true, custom_value: false, mode: "dropdown", options: columnOptions } } },
      ],
      computeLabel: (schema) => {
        const labels = {
          title: "Titel",
          service_url: "Preisermittlung URL",
          category_id: "Kategorie-ID",
          sort_by: "Sortierung",
          sort_dir: "Richtung",
          image_size: "Bildgröße",
          show_category_filter: "Kategorie-Filter anzeigen",
          show_category_dropdown: "Kategorie-Dropdown anzeigen",
          show_category_multi_filter: "Mehrfachauswahl anzeigen",
          show_search: "Suche anzeigen",
          show_target_price_filter: "Wunschpreis-Filter anzeigen",
          target_price_filter_active: "Wunschpreis-Filter beim Laden aktiv",
          group_by_category: "Nach Kategorien gruppieren",
          compact: "Kompakte Zeilen",
          extra_matches_display: "Zusatztreffer anzeigen",
          extra_matches_expanded: "Zusatztreffer standardmäßig ausgeklappt",
          target_price_highlight_enabled: "Erreichte Wunschpreise markieren",
          target_price_extra_matches_enabled: "Wunschpreis bei Zusatztreffern",
          target_price_missed_display: "Nicht erreichten Wunschpreis",
          columns: "Spalten",
        };
        return labels[schema.name];
      },
      computeHelper: (schema) => {
        if (schema.name === "title") return `Preisermittlung Card ${CARD_VERSION}`;
        if (schema.name === "service_url") return "Basis-URL der Preisermittlung-App, z.B. http://192.168.178.10:5050";
        if (schema.name === "category_id") return "Leer lassen für alle Kategorien. Eine ID oder mehrere IDs mit Komma eintragen, z.B. katzenfutter, trockenfutter.";
        if (schema.name === "target_price_extra_matches_enabled") return "Bei Prospekt-Zusatztreffern kann die Zuordnung unsicher sein, wenn ein Suchwort mehrere Angebote findet. Dort wird kompakt WP angezeigt.";
        if (schema.name === "target_price_filter_active") return "Wenn aktiv, zeigt die Card beim ersten Laden nur Artikel, deren Wunschpreis erreicht wurde.";
        if (schema.name === "target_price_missed_display") return "Erreichte Wunschpreise bleiben grün sichtbar.";
        if (schema.name === "columns") return "Spalten beziehen sich exakt auf die MQTT-Attribute aus der README.";
        return undefined;
      },
    };
  }

  static getStubConfig() {
    return { ...DEFAULT_CONFIG };
  }

  setConfig(config) {
    this._config = normalizeConfig(config);
    this._selectedCategories = parseCategoryIds(this._config.category_id);
    this._categoryDialogOpen = false;
    this._searchText = "";
    this._targetFilterActive = this._config.target_price_filter_active === true;
    this._sortBy = this._config.sort_by;
    this._sortDir = this._config.sort_dir;
    this._imageGallery = null;
    this._lastRenderSignature = "";
    this._pendingRenderSignature = "";
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
  }

  set hass(hass) {
    this._hass = hass;
    const signature = this._renderSignature(hass);
    if (signature === this._lastRenderSignature) return;
    if (this._isFormInteractionActive()) {
      this._pendingRenderSignature = signature;
      return;
    }
    this._lastRenderSignature = signature;
    this._pendingRenderSignature = "";
    this._render();
  }

  getCardSize() {
    return 5;
  }

  _products() {
    const prefix = this._config.entity_prefix || DEFAULT_CONFIG.entity_prefix;
    return Object.entries(this._hass.states)
      .filter(([entityId]) => entityId.startsWith(prefix))
      .map(([entityId, stateObj]) => entityToProduct(entityId, stateObj))
      .filter((product) => !this._selectedCategories.length || this._selectedCategories.includes(product.categoryId))
      .filter((product) => !this._targetFilterActive || product.belowTargetPrice)
      .sort(compareProducts(this._sortBy || this._config.sort_by, this._sortDir || this._config.sort_dir));
  }

  _productSearchText(product) {
    return [
      product.name,
      product.providerName,
      product.shop,
      product.shopDetail,
      product.sourceType,
      product.category,
      product.articleNumber,
      product.searchTerm,
      product.unitPrice,
      product.packageSize,
      ...product.matches.flatMap((match) => [match.title, match.price_text, match.pdf_file_name]),
    ].join(" ").toLowerCase();
  }

  _renderSignature(hass) {
    const prefix = this._config?.entity_prefix || DEFAULT_CONFIG.entity_prefix;
    const relevantKeys = [
      "name", "friendly_name", "article_number", "search_term", "provider", "provider_name", "shop", "shop_detail",
      "market", "source_type", "category_id", "category", "price", "price_cents", "price_text", "unit_price", "package_size",
      "category_show_in_grouped", "category_searchable", "category_group_expanded",
      "unit_price_text", "package_size_text", "target_price", "target_price_cents", "target_price_text", "below_target_price",
      "available", "image_url", "status", "error", "url",
      "last_checked", "last_changed", "match_count", "matches", "extra_matches", "pdf_page", "pdf_file",
    ];
    return JSON.stringify(
      Object.entries(hass.states)
        .filter(([entityId]) => entityId.startsWith(prefix))
        .sort(([left], [right]) => left.localeCompare(right))
        .map(([entityId, stateObj]) => {
          const attr = stateObj.attributes || {};
          const picked = {};
          relevantKeys.forEach((key) => {
            if (attr[key] !== undefined) picked[key] = attr[key];
          });
          return [entityId, stateObj.state, picked];
        })
    );
  }

  _isFormInteractionActive() {
    const active = this.shadowRoot?.activeElement;
    return !!active && ["SELECT", "INPUT", "TEXTAREA", "HA-SELECT", "HA-TEXTFIELD", "HA-SWITCH", "HA-CHECKBOX"].includes(active.tagName);
  }

  _categories() {
    const prefix = this._config.entity_prefix || DEFAULT_CONFIG.entity_prefix;
    const categories = new Map();
    Object.entries(this._hass.states)
      .filter(([entityId]) => entityId.startsWith(prefix))
      .forEach(([, stateObj]) => {
        const attr = stateObj.attributes || {};
        const id = attr.category_id || "";
        const name = attr.category || id || "Allgemein";
        if (!categories.has(id)) {
          categories.set(id, {
            id,
            name,
            showInGrouped: attr.category_show_in_grouped !== false,
            searchable: attr.category_searchable !== false,
            groupExpanded: attr.category_group_expanded !== false,
          });
        }
      });
    return [...categories.values()]
      .sort((left, right) => left.name.localeCompare(right.name, "de", { sensitivity: "base" }));
  }

  _render() {
    if (!this.shadowRoot || !this._hass || !this._config) return;
    const categories = this._categories();
    const products = this._products();
    const selectedCategoryNames = this._selectedCategories.map((id) => this._categoryName(categories, id)).filter(Boolean);
    const groups = this._config.group_by_category
      ? this._groupProducts(products)
      : [{ id: "_all", name: "", products }];

    this.shadowRoot.innerHTML = `
      <style>${this._styles()}</style>
      <ha-card>
        <div class="card-header">
          <div>
            <div class="title">${escapeHtml(this._config.title || "Preisermittlung")}</div>
            <div class="subtitle" data-subtitle>${products.length} Artikel${selectedCategoryNames.length ? " in " + escapeHtml(selectedCategoryNames.join(", ")) : ""}</div>
          </div>
          <div class="header-controls">
            ${this._config.show_search ? this._searchInput() : ""}
            ${this._config.show_target_price_filter ? this._targetFilterButton() : ""}
            ${this._config.show_category_filter && (this._config.show_category_dropdown || this._config.show_category_multi_filter) ? this._categorySelect(categories) : ""}
          </div>
        </div>
        <div class="content ${this._config.compact ? "compact" : ""}">
          ${products.length ? groups.map((group) => this._table(group)).join("") : this._emptyState()}
        </div>
        ${this._imageGallery ? this._imageOverlay() : ""}
        ${this._categoryDialogOpen ? this._categoryDialog(categories) : ""}
      </ha-card>
    `;

    const select = this.shadowRoot.querySelector("[data-category-filter]");
    if (select) {
      select.value = this._selectedCategories.length === 1 ? this._selectedCategories[0] : "";
      const updateCategory = (event) => {
        const value = event.target.value ?? "";
        this._selectedCategories = value ? [value] : [];
        this._categoryDialogOpen = false;
        this._render();
      };
      select.addEventListener("change", updateCategory);
    }

    this.shadowRoot.querySelectorAll("[data-category-dialog-open]").forEach((button) => {
      button.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        this._categoryDialogOpen = true;
        this._render();
      });
    });

    this.shadowRoot.querySelectorAll("[data-category-dialog-close]").forEach((button) => {
      button.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        this._categoryDialogOpen = false;
        this._render();
      });
    });

    this.shadowRoot.querySelectorAll("[data-category-dialog-panel]").forEach((panel) => {
      panel.addEventListener("click", (event) => {
        event.stopPropagation();
      });
    });

    this.shadowRoot.querySelectorAll("[data-category-apply]").forEach((button) => {
      button.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        this._selectedCategories = [...this.shadowRoot.querySelectorAll("[data-category-choice]:checked")].map((item) => item.value);
        this._categoryDialogOpen = false;
        this._render();
      });
    });

    this.shadowRoot.querySelectorAll("[data-category-single]").forEach((button) => {
      button.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        const value = button.dataset.categorySingle || "";
        this._selectedCategories = value ? [value] : [];
        this._categoryDialogOpen = false;
        this._render();
      });
    });

    this.shadowRoot.querySelectorAll("[data-category-reset]").forEach((button) => {
      button.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        this._selectedCategories = [];
        this._categoryDialogOpen = false;
        this._render();
      });
    });

    this.shadowRoot.querySelectorAll("[data-target-filter]").forEach((button) => {
      button.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        this._targetFilterActive = !this._targetFilterActive;
        this._render();
      });
    });

    const search = this.shadowRoot.querySelector("[data-search]");
    if (search) {
      search.value = this._searchText || "";
      search.addEventListener("input", (event) => {
        this._searchText = event.target.value ?? event.detail?.value ?? "";
        this._applySearchFilter();
      });
    }

    this.shadowRoot.querySelectorAll("[data-entity-id]").forEach((row) => {
      row.addEventListener("click", (event) => {
        if (event.target.closest("a")) return;
        this.dispatchEvent(new CustomEvent("hass-more-info", {
          detail: { entityId: row.dataset.entityId },
          bubbles: true,
          composed: true,
        }));
      });
    });

    this.shadowRoot.querySelectorAll("[data-scroll-strip]").forEach((button) => {
      button.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        const strip = this.shadowRoot.querySelector(button.dataset.scrollStrip || "");
        if (!strip) return;
        const direction = button.dataset.scrollDirection === "prev" ? -1 : 1;
        strip.scrollBy({ left: direction * Math.max(220, strip.clientWidth * 0.8), behavior: "smooth" });
      });
    });

    this.shadowRoot.querySelectorAll("[data-sort-column]").forEach((button) => {
      button.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        const column = button.dataset.sortColumn;
        if (!this._isSortableColumn(column)) return;
        if (this._sortBy === column) {
          this._sortDir = this._sortDir === "asc" ? "desc" : "asc";
        } else {
          this._sortBy = column;
          this._sortDir = column === "price" || column.startsWith("last_") ? "desc" : "asc";
        }
        this._render();
      });
    });

    this.shadowRoot.querySelectorAll("[data-gallery]").forEach((button) => {
      button.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        const items = JSON.parse(button.dataset.gallery || "[]");
        if (!items.length) return;
        this._imageGallery = { items, index: 0 };
        this._render();
      });
    });

    this.shadowRoot.querySelectorAll("[data-gallery-close]").forEach((button) => {
      button.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        this._imageGallery = null;
        this._render();
      });
    });

    this.shadowRoot.querySelectorAll("[data-gallery-dialog]").forEach((dialog) => {
      dialog.addEventListener("click", (event) => {
        event.stopPropagation();
      });
    });

    this.shadowRoot.querySelectorAll("[data-gallery-direction]").forEach((button) => {
      button.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        this._moveGallery(button.dataset.galleryDirection === "prev" ? -1 : 1);
      });
    });

    if (this._searchText) this._applySearchFilter();
  }

  _groupProducts(products) {
    const groups = new Map();
    products.forEach((product) => {
      const key = product.categoryId || "_default";
      if (!groups.has(key)) {
        groups.set(key, {
          id: key,
          name: product.category || "Allgemein",
          showInGrouped: product.categoryShowInGrouped,
          searchable: product.categorySearchable,
          expanded: product.categoryGroupExpanded,
          products: [],
        });
      }
      groups.get(key).products.push(product);
    });
    return [...groups.values()].sort((left, right) => left.name.localeCompare(right.name, "de", { sensitivity: "base" }));
  }

  _categoryName(categories, id) {
    return categories.find((category) => category.id === id)?.name || id;
  }

  _categorySelect(categories) {
    const multiActive = this._selectedCategories.length > 1;
    const selectValue = this._selectedCategories.length === 1 ? this._selectedCategories[0] : "";
    const showMulti = this._config.show_category_multi_filter !== false;
    const showDropdown = this._config.show_category_dropdown !== false;
    return `
      <div class="category-filter-wrap ${!showMulti ? "no-multi" : ""} ${!showDropdown ? "no-dropdown" : ""}">
        ${showMulti ? `<button class="category-dialog-button ${multiActive ? "is-active" : ""}" type="button" data-category-dialog-open title="Mehrere Kategorien auswählen" aria-label="Mehrere Kategorien auswählen">
          <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M8 6h13"/><path d="M8 12h13"/><path d="M8 18h13"/><path d="M3 6h.01"/><path d="M3 12h.01"/><path d="M3 18h.01"/></svg>
        </button>` : ""}
        ${showDropdown ? `<label class="category-filter">
          <span>Kategorie</span>
          <select data-category-filter>
            <option value="" ${selectValue ? "" : "selected"}>Alle Kategorien</option>
            ${categories.map((category) => `
              <option value="${escapeHtml(category.id)}" ${selectValue === category.id ? "selected" : ""}>${escapeHtml(category.name)}</option>
            `).join("")}
          </select>
        </label>` : ""}
      </div>
    `;
  }

  _categoryDialog(categories) {
    return `
      <div class="category-overlay" data-category-dialog-close>
        <div class="category-dialog" role="dialog" aria-modal="true" aria-label="Kategorien auswählen" data-category-dialog-panel>
          <div class="category-dialog-head">
            <div>
              <strong>Kategorien auswählen</strong>
              <span>Mehrere Kategorien markieren und übernehmen.</span>
            </div>
            <button type="button" data-category-dialog-close aria-label="Schließen">×</button>
          </div>
          <div class="category-choice-list">
            ${categories.map((category) => `
              <div class="category-choice">
                <button class="category-single-button" type="button" data-category-single="${escapeHtml(category.id)}" title="Nur diese Kategorie auswählen" aria-label="Nur ${escapeHtml(category.name)} auswählen">↵</button>
                <button class="category-name-button" type="button" data-category-single="${escapeHtml(category.id)}">${escapeHtml(category.name)}</button>
                <input data-category-choice type="checkbox" value="${escapeHtml(category.id)}" ${this._selectedCategories.includes(category.id) ? "checked" : ""}>
              </div>
            `).join("")}
          </div>
          <div class="category-dialog-actions">
            <button type="button" data-category-apply>Übernehmen</button>
            <button type="button" data-category-reset>Alle</button>
            <button type="button" data-category-dialog-close>Abbrechen</button>
          </div>
        </div>
      </div>
    `;
  }

  _searchInput() {
    return `
      <label class="search-filter">
        <span>Suche</span>
        <input data-search type="search" placeholder="Artikel, Anbieter, Shop ..." value="${escapeHtml(this._searchText || "")}">
      </label>
    `;
  }

  _targetFilterButton() {
    return `
      <button class="target-filter-button ${this._targetFilterActive ? "is-active" : ""}" type="button" data-target-filter title="Nur erreichte Wunschpreise anzeigen" aria-label="Nur erreichte Wunschpreise anzeigen">
        <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <circle cx="12" cy="12" r="8"></circle>
          <circle cx="12" cy="12" r="3"></circle>
          <path d="M12 2v3"></path>
          <path d="M12 19v3"></path>
          <path d="M2 12h3"></path>
          <path d="M19 12h3"></path>
        </svg>
      </button>
    `;
  }

  _applySearchFilter() {
    const query = String(this._searchText || "").trim().toLowerCase();
    let visibleCount = 0;
    this.shadowRoot.querySelectorAll("[data-product-row]").forEach((row) => {
      const categorySearchable = row.dataset.categorySearchable !== "false";
      const visible = !query || this._selectedCategories.length || (categorySearchable && String(row.dataset.searchText || "").includes(query));
      row.hidden = !visible;
      if (visible) visibleCount += 1;
      const extraRow = row.nextElementSibling?.classList?.contains("extra-matches-row") ? row.nextElementSibling : null;
      if (extraRow) extraRow.hidden = !visible;
    });
    this.shadowRoot.querySelectorAll(".group").forEach((group) => {
      const hasVisibleRows = [...group.querySelectorAll("[data-product-row]")].some((row) => !row.hidden);
      const hideByCategory = !query && !this._selectedCategories.length && group.dataset.categoryShow === "false";
      group.hidden = !hasVisibleRows || hideByCategory;
    });
    const subtitle = this.shadowRoot.querySelector("[data-subtitle]");
    if (subtitle) {
      const categories = this._categories();
      const selectedCategoryNames = this._selectedCategories.map((id) => this._categoryName(categories, id)).filter(Boolean);
      subtitle.textContent = `${visibleCount} Artikel${selectedCategoryNames.length ? " in " + selectedCategoryNames.join(", ") : ""}`;
    }
  }

  _table(group) {
    const columns = this._config.columns;
    const initiallyHidden = this._config.group_by_category && !this._selectedCategories.length && !this._searchText && group.showInGrouped === false;
    const detailsOpen = group.expanded || this._searchText || this._selectedCategories.length;
    return `
      <section class="group" data-category-show="${group.showInGrouped === false ? "false" : "true"}" ${initiallyHidden ? "hidden" : ""}>
        ${group.name ? `<details class="group-details" ${detailsOpen ? "open" : ""}><summary class="group-title">${escapeHtml(group.name)}</summary>` : ""}
        <div class="table-wrap">
          <table>
            <thead>
              <tr>${columns.map((column) => `<th class="col-${column}">${this._headerCell(column)}</th>`).join("")}</tr>
            </thead>
            <tbody>
              ${group.products.map((product) => this._row(product, columns)).join("")}
            </tbody>
          </table>
        </div>
        ${group.name ? "</details>" : ""}
      </section>
    `;
  }

  _row(product, columns) {
    const rowClass = [
      product.status === "error" || product.noOffer ? "has-error" : "",
      this._config.target_price_highlight_enabled && product.belowTargetPrice ? "is-target-price" : "",
    ].filter(Boolean).join(" ");
    return `
      <tr data-entity-id="${escapeHtml(product.entityId)}" data-product-row data-category-searchable="${product.categorySearchable ? "true" : "false"}" data-search-text="${escapeHtml(this._productSearchText(product))}" class="${rowClass}">
        ${columns.map((column) => `<td class="col-${column}">${this._cell(product, column)}</td>`).join("")}
      </tr>
      ${this._extraMatchesRow(product, columns)}
    `;
  }

  _cell(product, column) {
    if (column === "image") return this._image(product);
    if (column === "name") return `<div class="name">${escapeHtml(product.name)}</div>${product.url && !product.noOffer ? `<a class="link" href="${escapeHtml(this._productUrl(product))}" target="_blank" rel="noreferrer">${escapeHtml(this._linkLabel(product))}</a>` : ""}`;
    if (column === "provider") return escapeHtml(product.providerName || "-");
    if (column === "shop") return escapeHtml(product.shop || "-");
    if (column === "shop_detail") return escapeHtml(product.shopDetail || "-");
    if (column === "price") {
      const badge = this._targetPriceBadge(product);
      return `<strong>${escapeHtml(formatPrice(product))}</strong>${badge ? `<br>${badge}` : ""}`;
    }
    if (column === "target_price") return this._targetPriceBadge(product, { standalone: true }) || "-";
    if (column === "unit_price") return escapeHtml(product.noOffer ? "-" : (product.unitPrice || "-"));
    if (column === "package_size") return escapeHtml(product.noOffer ? "-" : (product.packageSize || "-"));
    if (column === "category") return escapeHtml(product.category || "-");
    if (column === "status") return `<span class="status status-${escapeHtml(statusClass(product.status))}">${escapeHtml(product.status || "-")}</span>`;
    if (column === "last_checked") return escapeHtml(formatDate(product.lastChecked));
    if (column === "last_changed") return escapeHtml(formatDate(product.lastChanged));
    return "";
  }

  _extraMatchesRow(product, columns) {
    if (this._config.extra_matches_display === "off" || !product.matches.length) return "";
    const matches = product.matches.filter((match) => !this._isSameMatch(product, match));
    if (!matches.length) return "";
    const stripId = `matches-${product.entityId.replace(/[^a-zA-Z0-9_-]+/g, "-")}`;
    const displayMode = this._config.extra_matches_display === "slider" ? "slider" : "wrap";
    const strip = `<div id="${escapeHtml(stripId)}" class="extra-matches extra-matches-${displayMode}">${matches.map((match) => this._extraMatch(product, match)).join("")}</div>`;
    const matchesContent = displayMode === "slider"
      ? `<div class="extra-match-slider">
          <button class="match-scroll" type="button" data-scroll-strip="#${escapeHtml(stripId)}" data-scroll-direction="prev" aria-label="Zusatztreffer nach links">‹</button>
          ${strip}
          <button class="match-scroll" type="button" data-scroll-strip="#${escapeHtml(stripId)}" data-scroll-direction="next" aria-label="Zusatztreffer nach rechts">›</button>
        </div>`
      : strip;
    return `
      <tr class="extra-matches-row">
        <td colspan="${columns.length}">
          <details ${this._config.extra_matches_expanded ? "open" : ""}>
            <summary>${matches.length} zusätzliche Prospekt-Treffer${product.searchTerm ? ` für ${escapeHtml(product.searchTerm)}` : ""}</summary>
            ${matchesContent}
          </details>
        </td>
      </tr>
    `;
  }

  _isSameMatch(product, match) {
    return (
      (match.provider_article_number && match.provider_article_number === product.articleNumber)
      || (
        match.image_url === product.imageUrl
        && Number(match.price_cents || 0) === Math.round((product.price || 0) * 100)
      )
    );
  }

  _matchBelowTarget(product, match) {
    if (product.targetPriceCents === null) return false;
    const cents = matchPriceCents(match);
    return cents !== null && cents <= Number(product.targetPriceCents);
  }

  _targetPriceBadge(product, options = {}) {
    if (product.targetPriceCents === null || product.noOffer) return "";
    const reached = options.reached ?? product.belowTargetPrice;
    const mode = this._config.target_price_missed_display;
    if (!reached && mode === "hide") return "";
    const label = "WP";
    const stateClass = reached ? "is-hit" : (mode === "muted" ? "is-muted" : "is-normal");
    const text = product.targetPriceText || formatCents(product.targetPriceCents);
    const standalone = options.standalone ? " is-standalone" : "";
    return `<span class="target-price-badge ${stateClass}${standalone}">${escapeHtml(label)} ${escapeHtml(text)}</span>`;
  }

  _extraMatch(product, match) {
    const price = match.price_text || (Number.isFinite(Number(match.price)) ? new Intl.NumberFormat("de-DE", { style: "currency", currency: "EUR" }).format(Number(match.price)) : "-");
    const pdfUrl = this._pdfPageUrl(match.url, match.pdf_page);
    const image = match.image_url
      ? `<img class="match-image" src="${escapeHtml(this._resolveUrl(match.image_url))}" alt="" loading="lazy">`
      : `<div class="match-image"></div>`;
    const targetBadge = this._config.target_price_extra_matches_enabled ? this._targetPriceBadge(product, {
      compact: true,
      reached: this._matchBelowTarget(product, match),
    }) : "";
    return `
      <article class="extra-match">
        ${image}
        <div>
          <strong>${escapeHtml(price)}</strong>
          ${targetBadge}
          <small>Seite ${escapeHtml(match.pdf_page || "-")}${match.pdf_file_name ? " · " + escapeHtml(match.pdf_file_name) : ""}</small>
          <div class="match-title">${escapeHtml(match.title || "")}</div>
          ${pdfUrl ? `<a class="link" href="${escapeHtml(pdfUrl)}" target="_blank" rel="noreferrer">PDF öffnen</a>` : ""}
        </div>
      </article>
    `;
  }

  _pdfPageUrl(url, page) {
    if (!url) return "";
    const pageNumber = Number.parseInt(page, 10);
    const resolvedUrl = this._resolveUrl(url);
    return pageNumber > 0 ? `${resolvedUrl}#page=${pageNumber}` : resolvedUrl;
  }

  _resolveUrl(url) {
    const value = String(url || "").trim();
    if (!value) return "";
    if (/^(https?:)?\/\//i.test(value) || value.startsWith("data:") || value.startsWith("blob:")) return value;
    const base = this._config.service_url || "";
    if (value.startsWith("/") && base) return `${base}${value}`;
    return value;
  }

  _image(product) {
    const size = this._displayImageSize();
    if (!product.imageUrl) return `<div class="image-placeholder" style="width:${size}px;height:${size}px"></div>`;
    const gallery = this._galleryItems(product);
    return `
      <button class="image-button" type="button" data-gallery="${escapeHtml(JSON.stringify(gallery))}" aria-label="Bild von ${escapeHtml(product.name)} öffnen">
        <img class="product-image" src="${escapeHtml(this._resolveUrl(product.imageUrl))}" alt="" loading="lazy" style="width:${size}px;height:${size}px">
      </button>
    `;
  }

  _columnLabel(column) {
    return COLUMN_DEFINITIONS.find((item) => item.key === column)?.label || column;
  }

  _headerCell(column) {
    const label = this._columnLabel(column);
    if (!this._isSortableColumn(column)) return escapeHtml(label);
    const active = this._sortBy === column;
    const arrow = active ? (this._sortDir === "asc" ? "▲" : "▼") : "↕";
    return `<button class="sort-button ${active ? "is-active" : ""}" type="button" data-sort-column="${escapeHtml(column)}">${escapeHtml(label)} <span>${arrow}</span></button>`;
  }

  _isSortableColumn(column) {
    return SORT_OPTIONS.some((item) => item.key === column);
  }

  _displayImageSize() {
    return this._config.compact ? Math.min(38, this._config.image_size) : this._config.image_size;
  }

  _linkLabel(product) {
    return product.sourceType === "prospect" || product.providerName.toLowerCase().includes("pdf") ? "PDF öffnen" : "Shop öffnen";
  }

  _productUrl(product) {
    return this._linkLabel(product) === "PDF öffnen" ? this._pdfPageUrl(product.url, product.pdfPage) : this._resolveUrl(product.url);
  }

  _galleryItems(product) {
    const items = [];
    if (product.imageUrl) {
      items.push({
        image: this._resolveUrl(product.imageUrl),
        title: product.name,
        subtitle: product.price ? formatPrice(product) : "",
        url: this._productUrl(product),
      });
    }
    product.matches
      .filter((match) => match.image_url && !this._isSameMatch(product, match))
      .forEach((match) => {
        items.push({
          image: this._resolveUrl(match.image_url),
          title: match.title || product.name,
          subtitle: match.price_text || "",
          url: this._pdfPageUrl(match.url, match.pdf_page),
        });
      });
    return items;
  }

  _imageOverlay() {
    const items = this._imageGallery?.items || [];
    const index = Math.max(0, Math.min(this._imageGallery?.index || 0, items.length - 1));
    const item = items[index] || {};
    return `
      <div class="image-overlay" data-gallery-close>
        <div class="image-dialog" data-gallery-dialog role="dialog" aria-modal="true" aria-label="Artikelbild">
          <button class="dialog-close" type="button" data-gallery-close aria-label="Schließen">×</button>
          ${items.length > 1 ? `<button class="dialog-nav dialog-prev" type="button" data-gallery-direction="prev" aria-label="Vorheriges Bild">‹</button>` : ""}
          <div class="dialog-figure">
            <img class="dialog-image" src="${escapeHtml(item.image || "")}" alt="">
          </div>
          ${items.length > 1 ? `<button class="dialog-nav dialog-next" type="button" data-gallery-direction="next" aria-label="Nächstes Bild">›</button>` : ""}
          <div class="dialog-caption">
            <strong>${escapeHtml(item.title || "")}</strong>
            <span>${escapeHtml(item.subtitle || "")}${items.length > 1 ? ` · ${index + 1}/${items.length}` : ""}</span>
            ${item.url ? `<a class="link" href="${escapeHtml(item.url)}" target="_blank" rel="noreferrer">Öffnen</a>` : ""}
          </div>
        </div>
      </div>
    `;
  }

  _moveGallery(direction) {
    if (!this._imageGallery?.items?.length) return;
    const length = this._imageGallery.items.length;
    this._imageGallery.index = (this._imageGallery.index + direction + length) % length;
    this._render();
  }

  _emptyState() {
    return `<div class="empty">Keine Preisermittlung-Sensoren gefunden.</div>`;
  }

  _styles() {
    return `
      :host {
        --pm-muted: var(--secondary-text-color);
        --pm-border: var(--divider-color);
        --pm-radius: 10px;
      }
      ha-card { overflow: hidden; }
      .card-header {
        display: flex;
        justify-content: space-between;
        align-items: flex-start;
        gap: 16px;
        padding: 16px;
        border-bottom: 1px solid var(--pm-border);
      }
      .title {
        font-size: 1.25rem;
        font-weight: 650;
        line-height: 1.25;
      }
      .subtitle {
        color: var(--pm-muted);
        margin-top: 4px;
      }
      .category-filter {
        min-width: 190px;
        width: 190px;
        display: grid;
        gap: 2px;
      }
      .category-filter span {
        color: var(--secondary-text-color);
        font-size: 12px;
        line-height: 14px;
      }
      .category-filter-wrap {
        display: grid;
        grid-template-columns: 42px minmax(0, 1fr);
        align-items: end;
        gap: 8px;
      }
      .category-filter-wrap.no-multi {
        grid-template-columns: minmax(0, 1fr);
      }
      .category-filter-wrap.no-dropdown {
        grid-template-columns: 42px;
      }
      .category-dialog-button,
      .target-filter-button {
        appearance: none;
        width: 42px;
        height: 42px;
        border: 1px solid var(--pm-border);
        border-radius: 8px;
        background: var(--card-background-color);
        color: var(--primary-text-color);
        cursor: pointer;
      }
      .category-dialog-button svg,
      .target-filter-button svg {
        width: 18px;
        height: 18px;
        stroke: currentColor;
      }
      .category-dialog-button.is-active,
      .target-filter-button.is-active {
        border-color: var(--primary-color);
        color: var(--primary-color);
        background: rgba(var(--rgb-primary-color, 3, 169, 244), .12);
        box-shadow: inset 0 2px 5px rgba(0, 0, 0, .16);
        transform: translateY(1px);
      }
      .header-controls {
        display: flex;
        align-items: end;
        justify-content: flex-end;
        flex-wrap: wrap;
        gap: 10px;
      }
      .search-filter {
        min-width: 220px;
        width: 260px;
        display: grid;
        gap: 2px;
      }
      .search-filter span {
        color: var(--secondary-text-color);
        font-size: 12px;
        line-height: 14px;
      }
      select, input {
        box-sizing: border-box;
      }
      select, input {
        background: var(--card-background-color);
        color: var(--primary-text-color);
        border: 1px solid var(--pm-border);
        border-radius: 8px;
        padding: 0 10px;
        font: inherit;
      }
      select[data-category-filter] {
        width: 100%;
        height: 42px;
        min-height: 42px;
        font-size: 16px;
        line-height: 20px;
        outline: none;
      }
      select[data-category-filter]:focus {
        border-color: var(--primary-color);
        box-shadow: 0 0 0 1px var(--primary-color);
      }
      input[data-search] {
        width: 100%;
        height: 42px;
        min-height: 42px;
        font-size: 16px;
        line-height: 20px;
        outline: none;
      }
      input[data-search]:focus {
        border-color: var(--primary-color);
        box-shadow: 0 0 0 1px var(--primary-color);
      }
      .content { padding: 0 0 8px; }
      .group-details {
        display: block;
      }
      .group-details summary {
        cursor: pointer;
        list-style: none;
      }
      .group-details summary::-webkit-details-marker {
        display: none;
      }
      .group-details summary::before {
        content: "▸";
        display: inline-block;
        margin-right: 8px;
        color: var(--primary-color);
      }
      .group-details[open] summary::before {
        content: "▾";
      }
      .group-title {
        padding: 14px 16px 6px;
        color: var(--pm-muted);
        font-weight: 650;
      }
      .table-wrap {
        overflow-x: auto;
      }
      table {
        width: 100%;
        border-collapse: collapse;
      }
      th, td {
        padding: 10px 12px;
        border-bottom: 1px solid var(--pm-border);
        text-align: left;
        vertical-align: middle;
      }
      th {
        color: var(--pm-muted);
        font-size: .78rem;
        text-transform: uppercase;
        letter-spacing: .04em;
        font-weight: 650;
        white-space: nowrap;
      }
      .sort-button {
        appearance: none;
        display: inline-flex;
        align-items: center;
        gap: 6px;
        border: 0;
        padding: 0;
        background: transparent;
        color: inherit;
        font: inherit;
        font-weight: inherit;
        letter-spacing: inherit;
        text-transform: inherit;
        cursor: pointer;
      }
      .sort-button span {
        color: var(--primary-color);
        font-size: .7rem;
      }
      .sort-button:not(.is-active) span {
        color: var(--pm-muted);
        opacity: .55;
      }
      tr[data-entity-id] {
        cursor: pointer;
      }
      tr[data-entity-id]:hover {
        background: rgba(var(--rgb-primary-text-color, 0, 0, 0), .04);
      }
      .compact th, .compact td {
        padding-top: 5px;
        padding-bottom: 5px;
      }
      .compact .name {
        line-height: 1.12;
      }
      .compact small,
      .compact .link {
        font-size: .76rem;
        line-height: 1.12;
      }
      .col-image {
        width: calc(${this._displayImageSize()}px + 16px);
      }
      .image-button {
        appearance: none;
        display: block;
        border: 0;
        padding: 0;
        border-radius: 7px;
        background: transparent;
        cursor: pointer;
      }
      .product-image,
      .image-placeholder {
        display: block;
        border-radius: 7px;
        object-fit: contain;
        background: rgba(var(--rgb-primary-text-color, 0, 0, 0), .04);
      }
      .name {
        font-weight: 650;
        line-height: 1.25;
      }
      small, .link {
        display: block;
        margin-top: 2px;
        color: var(--pm-muted);
        font-size: .82rem;
        line-height: 1.25;
      }
      .link {
        color: var(--primary-color);
        text-decoration: none;
      }
      .status {
        display: inline-flex;
        align-items: center;
        border-radius: 999px;
        padding: 2px 8px;
        background: rgba(var(--rgb-primary-text-color, 0, 0, 0), .06);
        font-size: .82rem;
        font-weight: 650;
      }
      .status-ok { color: var(--success-color, #168039); }
      .status-error, .status-kein-angebot, .has-error .col-price { color: var(--error-color, #db4437); }
      .is-target-price td {
        background: color-mix(in srgb, var(--success-color, #168039) 8%, transparent);
      }
      .is-target-price td:first-child {
        box-shadow: inset 4px 0 0 var(--success-color, #168039);
      }
      .target-price-badge {
        display: inline-flex;
        width: fit-content;
        margin-top: 4px;
        padding: 2px 7px;
        border: 1px solid color-mix(in srgb, var(--primary-color) 42%, var(--pm-border));
        border-radius: 999px;
        color: var(--primary-color);
        background: color-mix(in srgb, var(--primary-color) 8%, transparent);
        font-size: .76rem;
        font-weight: 700;
        line-height: 1.2;
        white-space: nowrap;
      }
      .target-price-badge.is-hit {
        border-color: color-mix(in srgb, var(--success-color, #168039) 50%, var(--pm-border));
        color: var(--success-color, #168039);
        background: color-mix(in srgb, var(--success-color, #168039) 10%, transparent);
      }
      .target-price-badge.is-muted {
        border-color: var(--pm-border);
        color: var(--pm-muted);
        background: rgba(var(--rgb-primary-text-color, 0, 0, 0), .035);
      }
      .target-price-badge.is-standalone {
        margin-top: 0;
      }
      .extra-matches-row td {
        padding-top: 0;
        background: rgba(var(--rgb-primary-text-color, 0, 0, 0), .025);
      }
      .extra-matches-row summary {
        cursor: pointer;
        color: var(--pm-muted);
        font-weight: 650;
        padding: 8px 0;
      }
      .extra-matches {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
        gap: 10px;
        padding-bottom: 10px;
      }
      .extra-match-slider {
        display: grid;
        grid-template-columns: auto minmax(0, 1fr) auto;
        align-items: center;
        gap: 8px;
        padding-bottom: 10px;
      }
      .extra-matches-slider {
        display: flex;
        grid-template-columns: none;
        overflow-x: auto;
        scroll-snap-type: x proximity;
        padding-bottom: 4px;
      }
      .extra-matches-slider .extra-match {
        min-width: 220px;
        max-width: 280px;
        scroll-snap-align: start;
      }
      .match-scroll {
        width: 34px;
        height: 52px;
        border: 1px solid var(--pm-border);
        border-radius: 8px;
        background: var(--card-background-color);
        color: var(--primary-text-color);
        font-size: 1.5rem;
        cursor: pointer;
      }
      .extra-match {
        display: grid;
        grid-template-columns: 48px minmax(0, 1fr);
        gap: 10px;
        align-items: start;
        border: 1px solid var(--pm-border);
        border-radius: 8px;
        padding: 8px;
        background: var(--card-background-color);
      }
      .extra-match .target-price-badge {
        margin: 3px 0 0;
        padding: 1px 6px;
        font-size: .72rem;
      }
      .match-image {
        width: 48px;
        height: 48px;
        display: block;
        object-fit: contain;
        border-radius: 6px;
        background: rgba(var(--rgb-primary-text-color, 0, 0, 0), .04);
      }
      .match-title {
        margin-top: 3px;
        color: var(--primary-text-color);
        font-size: .86rem;
        line-height: 1.25;
        display: -webkit-box;
        -webkit-line-clamp: 2;
        -webkit-box-orient: vertical;
        overflow: hidden;
      }
      .empty {
        padding: 24px 16px;
        color: var(--pm-muted);
      }
      .image-overlay {
        position: fixed;
        inset: 0;
        z-index: 10;
        display: grid;
        place-items: center;
        padding: 24px;
        background: rgba(0, 0, 0, .72);
      }
      .image-dialog {
        position: relative;
        display: grid;
        grid-template-rows: minmax(0, 1fr);
        width: min(900px, 92vw);
        height: min(760px, 88vh);
        border-radius: 12px;
        background: var(--card-background-color);
        color: var(--primary-text-color);
        box-shadow: 0 18px 60px rgba(0, 0, 0, .45);
        overflow: hidden;
      }
      .dialog-figure {
        display: grid;
        place-items: center;
        min-width: 0;
        min-height: 0;
        padding: 16px;
        background: rgba(var(--rgb-primary-text-color, 0, 0, 0), .04);
      }
      .dialog-image {
        display: block;
        max-width: 100%;
        max-height: 100%;
        width: 100%;
        height: 100%;
        object-fit: contain;
      }
      .dialog-caption {
        position: absolute;
        left: 0;
        right: 0;
        bottom: 0;
        padding: 12px 16px;
        border-top: 1px solid rgba(255, 255, 255, .16);
        color: #fff;
        background: linear-gradient(to top, rgba(0, 0, 0, .34), rgba(0, 0, 0, .12));
        text-shadow: 0 1px 3px rgba(0, 0, 0, .65);
        transition: background .16s ease, box-shadow .16s ease;
      }
      .dialog-caption:hover,
      .dialog-caption:focus-within {
        background: linear-gradient(to top, rgba(0, 0, 0, .78), rgba(0, 0, 0, .42));
        box-shadow: 0 -14px 34px rgba(0, 0, 0, .26);
      }
      .dialog-caption strong,
      .dialog-caption span {
        display: block;
      }
      .dialog-caption span {
        margin-top: 2px;
        color: rgba(255, 255, 255, .78);
        font-size: .9rem;
      }
      .dialog-caption .link {
        color: #8fd7ff;
      }
      .dialog-close,
      .dialog-nav {
        position: absolute;
        border: 1px solid var(--pm-border);
        background: var(--card-background-color);
        color: var(--primary-text-color);
        cursor: pointer;
        box-shadow: 0 4px 16px rgba(0, 0, 0, .18);
      }
      .dialog-close {
        top: 10px;
        right: 10px;
        width: 38px;
        height: 38px;
        border-radius: 8px;
        font-size: 1.4rem;
      }
      .dialog-nav {
        top: 50%;
        width: 44px;
        height: 64px;
        border-radius: 10px;
        transform: translateY(-50%);
        font-size: 2rem;
      }
      .dialog-prev { left: 10px; }
      .dialog-next { right: 10px; }
      .category-overlay {
        position: fixed;
        inset: 0;
        z-index: 11;
        display: grid;
        place-items: center;
        padding: 24px;
        background: rgba(0, 0, 0, .52);
      }
      .category-dialog {
        width: min(440px, 92vw);
        max-height: 82vh;
        display: grid;
        grid-template-rows: auto minmax(0, 1fr) auto;
        border-radius: 12px;
        background: var(--card-background-color);
        color: var(--primary-text-color);
        box-shadow: 0 18px 60px rgba(0, 0, 0, .42);
        overflow: hidden;
      }
      .category-dialog-head {
        display: flex;
        align-items: flex-start;
        justify-content: space-between;
        gap: 12px;
        padding: 16px;
        border-bottom: 1px solid var(--pm-border);
      }
      .category-dialog-head strong,
      .category-dialog-head span {
        display: block;
      }
      .category-dialog-head span {
        margin-top: 2px;
        color: var(--pm-muted);
        font-size: .9rem;
      }
      .category-dialog-head button,
      .category-dialog-actions button {
        min-height: 36px;
        border: 1px solid var(--pm-border);
        border-radius: 8px;
        background: var(--card-background-color);
        color: var(--primary-text-color);
        cursor: pointer;
      }
      .category-dialog-head button {
        width: 38px;
        font-size: 1.4rem;
      }
      .category-choice-list {
        display: grid;
        overflow: auto;
        border-bottom: 1px solid var(--pm-border);
      }
      .category-choice {
        display: grid;
        grid-template-columns: 34px minmax(0, 1fr) auto;
        align-items: center;
        gap: 8px;
        min-height: 42px;
        padding: 8px 12px;
        border-bottom: 1px solid var(--pm-border);
      }
      .category-choice:last-child {
        border-bottom: 0;
      }
      .category-choice:hover {
        background: rgba(var(--rgb-primary-text-color, 0, 0, 0), .04);
      }
      .category-choice input {
        width: auto;
      }
      .category-single-button,
      .category-name-button {
        border: 0;
        background: transparent;
        color: var(--primary-text-color);
        cursor: pointer;
      }
      .category-single-button {
        width: 30px;
        height: 30px;
        border: 1px solid var(--pm-border);
        border-radius: 7px;
        color: var(--primary-color);
        font-size: 1rem;
      }
      .category-name-button {
        overflow: hidden;
        padding: 0;
        text-align: left;
        text-overflow: ellipsis;
        white-space: nowrap;
        font: inherit;
      }
      .category-name-button:hover {
        color: var(--primary-color);
      }
      .category-dialog-actions {
        display: flex;
        gap: 8px;
        justify-content: flex-end;
        padding: 12px 16px;
      }
      .category-dialog-actions button:first-child {
        border-color: var(--primary-color);
        background: var(--primary-color);
        color: var(--text-primary-color, #fff);
      }
      @media (max-width: 640px) {
        .card-header {
          display: grid;
        }
        .header-controls {
          justify-content: stretch;
        }
        .search-filter,
        .category-filter {
          min-width: 0;
          width: 100%;
        }
        .category-filter-wrap {
          grid-template-columns: 42px minmax(0, 1fr);
          width: 100%;
        }
        th, td {
          padding-left: 8px;
          padding-right: 8px;
        }
      }
    `;
  }
}

customElements.define(CARD_TYPE, PreisermittlungCard);

window.customCards = window.customCards || [];
window.customCards.push({
  type: CARD_TYPE,
  name: "Preisermittlung Card",
  description: "Tabelle für Preisermittlung-Sensoren mit Kategorieauswahl, Suche, Bildern, Spalten und Sortierung.",
  preview: true,
});

console.info(`%c${CARD_TYPE}%c ${CARD_VERSION}`, "color:#d4001a;font-weight:700", "color:inherit");
