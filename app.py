from __future__ import annotations

import json
import io
import re
import resource
import socket
import sys
import tempfile
import threading
import time
import urllib.parse
import uuid
import zipfile
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Any, Dict, List, Optional

from flask import Flask, Response, jsonify, redirect, request, send_from_directory, url_for

from config_io import CONFIG_PATH, parse_simple_yaml, write_simple_yaml
from providers import (
    configure_user_agent,
    default_user_agent,
    market_for_product,
    market_for_selection,
    find_markets,
    market_provider,
    normalize_product_url,
    product_provider,
    provider_article_number_from_url,
    provider_choices,
    provider_kind,
    provider_label,
    provider_uses_markets,
    read_product,
    resolve_market,
    virtual_markets,
    browser_cache_infos,
    clear_browser_cache,
)
from readers import generic_reader, manual_pdf_reader
from readers.rewe_reader import markets_from_config


STATE_PATH = Path(__file__).with_name("state.json")
GENERATED_PATH = Path(__file__).with_name("generated")
BACKUP_IMPORT_PATH = Path(__file__).with_name("tmp").joinpath("backup_imports")
APP_NAME = "Preisermittlung"
APP_VERSION = "0.1.3-dev"
DEFAULT_CATEGORY_ID = "allgemein"
DEFAULT_CATEGORY_NAME = "Allgemein"
app = Flask(__name__)

state_lock = threading.Lock()
refresh_thread: Optional[threading.Thread] = None
scheduler_thread: Optional[threading.Thread] = None
scheduler_lock = threading.Lock()
mqtt_thread: Optional[threading.Thread] = None
mqtt_lock = threading.Lock()
progress: Dict[str, Any] = {
    "running": False,
    "current_product_id": None,
    "current_product_name": None,
    "done": 0,
    "total": 0,
    "started_at": None,
    "finished_at": None,
    "error": None,
}


STYLE = """
:root {
  color-scheme: light;
  --bg: #f7f8f5;
  --fg: #172018;
  --muted: #647066;
  --line: #d9dfd6;
  --accent: #cc071e;
  --ok: #1b7f3a;
  --warn: #8a5a00;
  --panel: #ffffff;
  --table-head: #fbfcfa;
  --accent-button: #cc071e;
}
[data-theme="dark"] {
  color-scheme: dark;
  --bg: #141713;
  --fg: #eef3eb;
  --muted: #aab5aa;
  --line: #333a33;
  --accent: #f04455;
  --ok: #65c981;
  --warn: #e2b45c;
  --panel: #1d221c;
  --table-head: #20271f;
  --accent-button: #9f1020;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--fg);
  font: 15px/1.45 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}
main { width: min(1180px, calc(100vw - 32px)); margin: 26px auto; }
header {
  display: flex;
  align-items: end;
  justify-content: space-between;
  gap: 20px;
  margin-bottom: 16px;
}
h1 { margin: 0; font-size: 28px; line-height: 1.15; }
h2 { margin: 0 0 10px; font-size: 17px; }
.meta { color: var(--muted); margin-top: 5px; }
.header-meta {
  display: grid;
  gap: 2px;
}
.header-meta-row {
  display: grid;
  grid-template-columns: max-content auto auto;
  gap: 8px;
  align-items: center;
}
.header-meta-row > span:first-child {
  min-width: 360px;
  white-space: nowrap;
}
.header-meta-row strong {
  color: var(--fg);
  font-weight: 650;
}
.actions, .row-actions { display: flex; gap: 8px; flex-wrap: wrap; }
.action-grid {
  display: grid;
  grid-template-columns: repeat(2, 36px);
  gap: 8px;
}
.action-grid form { margin: 0; }
button, a.button, input, select {
  min-height: 36px;
  border-radius: 6px;
  border: 1px solid var(--line);
  font: inherit;
}
button, a.button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0 12px;
  background: var(--panel);
  color: var(--fg);
  text-decoration: none;
  cursor: pointer;
}
button[disabled] {
  opacity: .65;
  cursor: progress;
}
button.inline-form-button { width: 100%; }
button.primary, a.button.primary { background: var(--accent-button); color: white; border-color: var(--accent-button); }
button.danger { color: #b00020; border-color: #efc2c8; }
button.ghost { border-style: dashed; }
button.icon-only, a.icon-only { width: 36px; padding: 0; }
button.icon-small, a.icon-small {
  width: 30px;
  min-height: 30px;
  padding: 0;
}
button svg, a.button svg { width: 17px; height: 17px; stroke: currentColor; }
a.icon-small svg { width: 15px; height: 15px; }
.red-icon { color: var(--accent); }
input, select { width: 100%; padding: 0 10px; background: white; color: var(--fg); }
[data-theme="dark"] input, [data-theme="dark"] select { background: #121611; }
.summary {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 10px;
  margin-bottom: 14px;
}
.metric, .panel {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 8px;
}
.metric { padding: 12px; }
.metric-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.metric span { display: block; color: var(--muted); font-size: 12px; margin-bottom: 4px; }
.metric strong { font-size: 16px; }
.panel { padding: 14px; margin-bottom: 14px; }
.progress-line {
  height: 10px;
  border-radius: 999px;
  overflow: hidden;
  background: #e7ebe4;
  margin-top: 10px;
}
.progress-line div {
  height: 100%;
  width: var(--pct);
  background: var(--accent);
  transition: width .2s ease;
}
table {
  width: 100%;
  border-collapse: collapse;
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 8px;
  overflow: hidden;
}
th, td {
  padding: 11px 12px;
  border-bottom: 1px solid var(--line);
  text-align: left;
  vertical-align: top;
}
th { font-size: 12px; color: var(--muted); background: var(--table-head); font-weight: 650; }
tr:last-child td { border-bottom: 0; }
.price { color: var(--accent); font-weight: 750; white-space: nowrap; }
.target-price-badge {
  display: inline-flex;
  margin-top: 4px;
  padding: 2px 7px;
  border-radius: 999px;
  border: 1px solid color-mix(in srgb, var(--accent) 40%, var(--line));
  color: var(--accent);
  background: color-mix(in srgb, var(--accent) 8%, transparent);
  font-size: .8rem;
  font-weight: 700;
}
.target-price-badge.is-hit {
  border-color: color-mix(in srgb, var(--ok) 45%, var(--line));
  color: var(--ok);
  background: color-mix(in srgb, var(--ok) 10%, transparent);
}
.target-price-badge.is-muted {
  border-color: var(--line);
  color: var(--muted);
  background: color-mix(in srgb, var(--muted) 6%, transparent);
}
.pdf-match-mini .target-price-badge {
  margin-top: 3px;
  padding: 1px 6px;
  font-size: .74rem;
}
tr.is-target-price > td {
  background: color-mix(in srgb, var(--ok) 8%, var(--panel));
}
tr.is-target-price > td:first-child {
  border-left: 4px solid var(--ok);
}
.ok { color: var(--ok); font-weight: 700; }
.warn { color: var(--warn); font-weight: 700; }
.small { color: var(--muted); font-size: 13px; }
.category-chip {
  display: inline-flex;
  align-items: center;
  min-height: 22px;
  padding: 0 7px;
  border: 1px solid var(--line);
  border-radius: 999px;
  color: var(--muted);
  text-decoration: none;
  font-size: 12px;
}
.category-chip:hover { color: var(--fg); border-color: var(--muted); }
.category-chip.has-color {
  border-color: color-mix(in srgb, var(--category-color) 55%, var(--line));
  background: color-mix(in srgb, var(--category-color) 14%, transparent);
  color: var(--fg);
}
.category-swatch {
  display: inline-block;
  width: 12px;
  height: 12px;
  border: 1px solid var(--line);
  border-radius: 999px;
  background: var(--category-color, transparent);
  margin-right: 6px;
  vertical-align: -1px;
}
.color-row {
  display: grid;
  grid-template-columns: 52px 1fr;
  gap: 8px;
  align-items: center;
}
.color-row input[type="color"] {
  min-height: 36px;
  padding: 3px;
}
.id-reveal {
  display: inline-block;
  position: relative;
  color: var(--muted);
}
.id-label {
  cursor: pointer;
  text-decoration: underline;
  text-decoration-style: dotted;
  border: 0;
  padding: 0;
  background: transparent;
  color: inherit;
  font: inherit;
}
.id-tooltip {
  position: absolute;
  left: 0;
  top: calc(100% + 6px);
  z-index: 10;
  display: none;
  max-width: min(360px, 78vw);
  padding: 6px 8px;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: var(--fg);
  color: var(--panel);
  box-shadow: 0 8px 28px rgba(23, 32, 24, .2);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.id-reveal:hover .id-tooltip,
.id-reveal.is-open .id-tooltip { display: block; }
.category-section { margin-top: 16px; }
.category-section h2 { margin: 0 0 8px; }
.product-cell {
  display: grid;
  grid-template-columns: 48px minmax(0, 1fr);
  gap: 10px;
  align-items: center;
  min-width: 260px;
}
.product-thumb {
  width: 48px;
  height: 48px;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: #f3f5f1;
  object-fit: contain;
  display: block;
}
.product-thumb-button {
  width: 48px;
  height: 48px;
  padding: 0;
  border: 0;
  background: transparent;
}
.image-preview {
  width: min(680px, calc(100vw - 52px));
  max-height: min(680px, calc(100vh - 160px));
  object-fit: contain;
  display: block;
  margin: 0 auto;
  background: #f3f5f1;
  border: 1px solid var(--line);
  border-radius: 8px;
}
.image-preview-wrap {
  position: relative;
  width: fit-content;
  max-width: 100%;
  margin: 0 auto;
}
.image-nav {
  position: absolute;
  top: 50%;
  transform: translateY(-50%);
  width: 42px;
  min-height: 42px;
  border-radius: 999px;
  background: color-mix(in srgb, var(--panel) 88%, transparent);
  box-shadow: 0 8px 22px rgba(0, 0, 0, .18);
  font-size: 28px;
  padding: 0;
}
.image-nav.prev { left: 10px; }
.image-nav.next { right: 10px; }
.pdf-match-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
  gap: 10px;
  margin-top: 10px;
}
.pdf-match-card {
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 10px;
  background: color-mix(in srgb, var(--panel) 94%, var(--accent) 6%);
}
.pdf-match-card img {
  width: 100%;
  max-height: 190px;
  object-fit: contain;
  border: 1px solid var(--line);
  border-radius: 6px;
  margin-top: 8px;
  background: var(--panel);
}
.pdf-match-detail-row td {
  background: color-mix(in srgb, var(--panel) 96%, var(--accent) 4%);
  border-left: 4px solid var(--accent);
  padding-top: 8px;
}
.pdf-match-details summary {
  cursor: pointer;
  list-style: none;
}
.pdf-match-details summary::-webkit-details-marker {
  display: none;
}
.pdf-match-summary {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: var(--muted);
  font-weight: 700;
}
.pdf-match-summary::before {
  content: "▸";
  color: var(--accent);
}
.pdf-match-details[open] .pdf-match-summary::before {
  content: "▾";
}
.pdf-match-strip {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: 8px;
  margin-top: 8px;
}
.pdf-match-strip.is-slider {
  display: flex;
  overflow-x: auto;
  overscroll-behavior-x: contain;
  scroll-snap-type: x proximity;
  padding-bottom: 8px;
}
.pdf-match-strip.is-slider .pdf-match-mini {
  flex: 0 0 min(260px, 82vw);
  scroll-snap-align: start;
}
.pdf-match-slider {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  gap: 8px;
  align-items: center;
}
.pdf-match-scroll {
  width: 38px;
  min-height: 38px;
  border-radius: 999px;
}
.pdf-match-mini {
  display: grid;
  grid-template-columns: 46px minmax(0, 1fr);
  gap: 8px;
  align-items: center;
  border: 1px solid var(--line);
  border-radius: 6px;
  padding: 6px;
  background: var(--panel);
}
.pdf-match-mini img {
  width: 46px;
  height: 46px;
  object-fit: contain;
  border: 1px solid var(--line);
  border-radius: 5px;
  background: #f3f5f1;
}
.pdf-match-title {
  display: -webkit-box;
  -webkit-line-clamp: 4;
  -webkit-box-orient: vertical;
  overflow: hidden;
  max-height: 5.8em;
}
.product-thumb-placeholder {
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--muted);
}
.product-thumb-placeholder svg {
  width: 20px;
  height: 20px;
}
.grid {
  display: grid;
  grid-template-columns: 1.4fr 1fr 1fr auto;
  gap: 10px;
  align-items: end;
}
.grid.market-grid { grid-template-columns: .7fr .7fr 1fr auto; }
.grid.product-grid { grid-template-columns: 1fr 1.5fr .9fr 1fr auto; }
.grid.filter-grid { grid-template-columns: 1fr .8fr 1.4fr auto auto; }
.category-filter-control {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  gap: 8px;
  align-items: end;
}
.category-filter-control:not(.has-multi) {
  grid-template-columns: minmax(0, 1fr);
}
.category-filter-control > .field { min-width: 0; }
.category-choice-list {
  display: grid;
  gap: 0;
  max-height: min(430px, 58vh);
  overflow: auto;
  border: 1px solid var(--line);
  border-radius: 8px;
}
.category-choice {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  min-height: 42px;
  padding: 8px 12px;
  border-bottom: 1px solid var(--line);
  cursor: pointer;
}
.category-choice:last-child {
  border-bottom: 0;
}
.category-choice:hover {
  background: color-mix(in srgb, var(--accent) 6%, transparent);
}
.category-choice input { width: auto; }
.category-choice-main {
  display: inline-flex;
  align-items: center;
  min-width: 0;
  gap: 8px;
}
.category-id-list {
  width: 100%;
  min-height: 78px;
  border-radius: 6px;
  border: 1px solid var(--line);
  background: var(--panel);
  color: var(--fg);
  padding: 10px;
  font: 13px/1.45 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}
.info-table td:first-child,
.info-table th:first-child {
  width: 46px;
}
.add-product-form {
  display: grid;
  gap: 14px;
}
.add-product-shared {
  display: grid;
  grid-template-columns: .85fr 1.55fr;
  gap: 10px;
  align-items: end;
}
.add-product-paths {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
}
.choice-panel {
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 14px;
  background: var(--panel);
  display: grid;
  gap: 10px;
  align-content: start;
}
.choice-panel h3 {
  margin: 0;
  font-size: 16px;
}
.button.is-active,
button.is-active {
  border-color: var(--accent);
  color: var(--accent);
  background: #fff1f3;
}
body[data-theme="dark"] .button.is-active,
body[data-theme="dark"] button.is-active {
  background: #35161c;
  border-color: #7f2c39;
  color: #ff9cab;
}
.button.icon-only.is-active,
button.icon-only.is-active {
  box-shadow: inset 0 2px 5px rgba(0, 0, 0, .16);
  transform: translateY(1px);
}
.choice-panel-actions {
  display: flex;
  justify-content: flex-end;
}
.optional-details summary {
  color: var(--muted);
  cursor: pointer;
  font-size: 12px;
  margin-bottom: 4px;
}
.optional-details .field {
  margin-top: 4px;
}
.shop-detect-status {
  border: 1px solid var(--line);
  border-radius: 6px;
  color: var(--muted);
  font-size: 13px;
  line-height: 1.35;
  padding: 8px 10px;
}
.shop-detect-status.is-good {
  background: #eef9f0;
  border-color: #b9dfc0;
  color: #1f7a35;
}
.shop-detect-status.is-warn {
  background: #fff6df;
  border-color: #ecd08b;
  color: #8a5a00;
}
.shop-detect-status.is-neutral {
  background: #f8f8f4;
}
.visual-price-map {
  max-height: min(70vh, 760px);
  overflow: auto;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: #fff;
  margin-top: 12px;
}
.visual-price-layer {
  position: relative;
  line-height: 0;
}
.visual-price-map img {
  width: 100%;
  height: auto;
  display: block;
}
.visual-price-marker {
  position: absolute;
  margin: 0;
  line-height: normal;
}
.visual-price-marker button {
  min-height: 0;
  padding: 3px 6px;
  border: 2px solid #1f8f3a;
  background: rgba(238, 249, 240, .94);
  color: #145c27;
  border-radius: 6px;
  font-size: 12px;
  font-weight: 750;
  box-shadow: 0 2px 10px rgba(0, 0, 0, .18);
  white-space: nowrap;
}
.visual-price-marker button:hover {
  transform: translateY(-1px);
}
body[data-theme="dark"] .shop-detect-status.is-good {
  background: #14291a;
  border-color: #315b3b;
  color: #8bd09a;
}
body[data-theme="dark"] .shop-detect-status.is-warn {
  background: #312711;
  border-color: #66501c;
  color: #e7bd56;
}
body[data-theme="dark"] .shop-detect-status.is-neutral {
  background: #20251f;
}
body[data-theme="dark"] .visual-price-map {
  background: #f5f5f2;
}
.table-actions {
  display: flex;
  justify-content: flex-end;
  margin-top: 12px;
}
.refresh-box {
  display: grid;
  justify-items: end;
  gap: 4px;
}
.market-list {
  display: grid;
  gap: 8px;
}
.code-preview {
  width: 100%;
  min-height: 180px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12px;
  line-height: 1.45;
  resize: vertical;
}
.market-row {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 10px;
  align-items: center;
  border-top: 1px solid var(--line);
  padding-top: 10px;
}
.sort-button {
  min-height: 0;
  padding: 0;
  border: 0;
  background: transparent;
  color: inherit;
  font: inherit;
  font-size: 12px;
  font-weight: 650;
}
.sort-button::after {
  content: " ⇅";
  color: var(--muted);
  margin-left: 4px;
  font-size: 11px;
}
.sort-button.sorted::after {
  color: var(--accent);
  font-weight: 800;
}
.sort-button.sorted[data-direction="asc"]::after {
  content: " ↑";
}
.sort-button.sorted[data-direction="desc"]::after {
  content: " ↓";
}
.notice {
  background: #f3f8ff;
  border: 1px solid #bfd5f2;
  color: #16406f;
  border-radius: 8px;
  padding: 12px;
  margin-bottom: 12px;
}
.busy-overlay {
  position: fixed;
  inset: 0;
  z-index: 1000;
  display: grid;
  place-items: center;
  padding: 24px;
  background: rgba(12, 16, 12, .48);
}
.busy-overlay[hidden] {
  display: none;
}
.busy-box {
  width: min(520px, 92vw);
  border-radius: 8px;
  border: 1px solid var(--line);
  background: var(--panel);
  color: var(--fg);
  box-shadow: 0 20px 80px rgba(0, 0, 0, .28);
  padding: 18px;
}
.busy-box strong {
  display: block;
  font-size: 18px;
  margin-bottom: 6px;
}
.status-dot {
  display: inline-block;
  width: 9px;
  height: 9px;
  border-radius: 999px;
  margin-right: 5px;
  background: var(--muted);
}
.status-dot.ok { background: var(--ok); }
.status-dot.warn { background: var(--warn); }
.status-dot.off { background: var(--muted); }
.app-footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin: 18px 0 4px;
  color: var(--muted);
  font-size: 12px;
}
.address-lines span { display: block; }
.dialog-backdrop {
  position: fixed;
  inset: 0;
  display: none;
  align-items: center;
  justify-content: center;
  background: rgba(23, 32, 24, .42);
  padding: 18px;
  z-index: 20;
}
.dialog-backdrop[open] { display: flex; }
.dialog-backdrop.is-open { display: flex; }
.dialog-backdrop:target { display: flex; }
.dialog {
  width: min(760px, 100%);
  max-height: min(760px, calc(100vh - 36px));
  overflow: auto;
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 16px;
  box-shadow: 0 18px 70px rgba(23, 32, 24, .22);
}
.dialog-head {
  display: flex;
  justify-content: space-between;
  align-items: start;
  gap: 16px;
  margin-bottom: 12px;
}
.dialog-close {
  width: 36px;
  min-height: 36px;
  padding: 0;
  font-size: 22px;
}
.dialog-product-url {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  margin-top: 5px;
}
.dialog-product-url code {
  min-width: 0;
  max-width: min(520px, 66vw);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--muted);
}
.dialog-product-url .icon-only {
  width: 30px;
  min-height: 30px;
}
.form-icon {
  width: 46px;
  min-height: 36px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 2px;
  border: 1px solid #efc2c8;
  border-radius: 6px;
  color: var(--accent);
  background: #fff6f7;
  text-decoration: none;
}
.form-icon svg { width: 16px; height: 16px; stroke: currentColor; }
.field label {
  display: block;
  color: var(--muted);
  font-size: 12px;
  margin-bottom: 4px;
}
.settings-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
  align-items: end;
}
.settings-grid.align-start {
  align-items: start;
}
.settings-card {
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 12px;
  background: color-mix(in srgb, var(--panel) 96%, var(--accent) 4%);
}
.soft-panel {
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 12px;
  background: color-mix(in srgb, var(--panel) 98%, var(--fg) 2%);
}
.soft-panel h4 {
  margin: 0 0 8px;
  font-size: 15px;
}
.soft-panel pre {
  overflow: auto;
  margin: 10px 0;
  padding: 10px;
  border-radius: 6px;
  border: 1px solid var(--line);
  background: color-mix(in srgb, var(--panel) 92%, var(--fg) 8%);
}
.settings-card .field:last-child {
  margin-bottom: 0;
}
.settings-card-full {
  grid-column: 1 / -1;
}
.inline-setting {
  display: grid;
  grid-template-columns: minmax(170px, auto) minmax(120px, 1fr);
  gap: 10px;
  align-items: end;
}
.file-upload-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 10px;
  align-items: end;
}
.toggle-line,
.field label.toggle-line {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 36px;
  margin: 0;
  color: var(--fg);
  line-height: 1.2;
  margin-bottom: 0;
}
.toggle-line input,
.field label.toggle-line input {
  width: auto;
  margin: 0;
  flex: 0 0 auto;
}
.settings-actions { margin-top: 14px; }
.settings-tabs {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 14px;
}
.settings-tab.is-active {
  background: var(--accent-button);
  color: white;
  border-color: var(--accent-button);
}
.panel-title-row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 10px;
}
.panel-title-row h2 { margin: 0; }
.quick-cats {
  display: inline-flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
}
.error {
  background: #fff3f3;
  border: 1px solid #e7b9b9;
  color: #8f1d1d;
  border-radius: 8px;
  padding: 12px;
  white-space: pre-wrap;
}
@media (max-width: 820px) {
  header { align-items: stretch; flex-direction: column; }
  .header-meta-row { grid-template-columns: 1fr; gap: 0; }
  .header-meta-row > span:first-child { min-width: 0; white-space: normal; }
  .summary { grid-template-columns: 1fr 1fr; }
  .grid, .settings-grid, .inline-setting, .file-upload-row, .add-product-shared, .add-product-paths { grid-template-columns: 1fr; }
  table, thead, tbody, tr, th, td { display: block; }
  thead { display: none; }
  tr { border-bottom: 1px solid var(--line); padding: 8px 0; }
  td { border-bottom: 0; padding: 6px 12px; }
  td::before { content: attr(data-label); display: block; color: var(--muted); font-size: 12px; }
  .product-cell { min-width: 0; }
}
"""


SCRIPT = """
let refreshWasRunning = false;
function showBusyOverlay(message) {
  const textMessage = message || 'Vorgang läuft...';
  const overlay = document.querySelector('[data-busy-overlay]');
  if (overlay) {
    overlay.hidden = false;
    const text = overlay.querySelector('[data-busy-overlay-text]');
    if (text) text.textContent = textMessage;
  }
  document.querySelectorAll('[data-upload-status-global], [data-backup-status], [data-restore-status]').forEach((status) => {
    status.hidden = false;
    status.textContent = textMessage;
  });
}
async function pollProgress() {
  try {
    const response = await fetch('/api/progress', {cache: 'no-store'});
    const progress = await response.json();
    const box = document.querySelector('[data-progress-box]');
    const bar = document.querySelector('[data-progress-bar]');
    const text = document.querySelector('[data-progress-text]');
    if (!box || !bar || !text) return;
    const total = progress.total || 0;
    const done = progress.done || 0;
    const pct = total ? Math.min(100, Math.round((done / total) * 100)) : 0;
    const current = total ? Math.min(done + 1, total) : 0;
    bar.style.setProperty('--pct', pct + '%');
    if (progress.running) {
      refreshWasRunning = true;
      box.hidden = false;
      text.textContent = total ? `Aktualisiere ${current}/${total}: ${progress.current_product_name || ''}` : 'Aktualisierung startet...';
      window.setTimeout(pollProgress, 900);
    } else if (refreshWasRunning && progress.finished_at) {
      text.textContent = `Fertig: ${done}/${total}`;
      if (!window.location.search.includes('done=1')) window.location = '/?done=1';
    }
  } catch (_) {
    window.setTimeout(pollProgress, 2000);
  }
}
pollProgress();
if (new URLSearchParams(window.location.search).has('refresh_started')) {
  window.scrollTo({top: 0, behavior: 'smooth'});
}

function bindSortButtons(root = document) {
  root.querySelectorAll('[data-sort]').forEach((button) => {
    if (button.dataset.sortBound === 'true') return;
    button.dataset.sortBound = 'true';
    button.addEventListener('click', () => {
      const table = button.closest('table');
      const tbody = table.querySelector('tbody');
      const index = Number(button.dataset.sort);
      const type = button.dataset.type || 'text';
      const nextDirection = button.dataset.direction === 'asc' ? 'desc' : 'asc';
      const direction = nextDirection === 'asc' ? 1 : -1;
      table.querySelectorAll('.sort-button').forEach((item) => {
        if (item !== button) {
          item.classList.remove('sorted');
          delete item.dataset.direction;
        }
      });
      button.classList.add('sorted');
      button.dataset.direction = nextDirection;
      const blocks = [];
      [...tbody.querySelectorAll('tr')].forEach((row) => {
        if (row.classList.contains('pdf-match-detail-row')) {
          if (blocks.length) blocks[blocks.length - 1].rows.push(row);
          return;
        }
        blocks.push({main: row, rows: [row]});
      });
      blocks
        .sort((a, b) => {
          const av = a.main.children[index]?.dataset.sortValue || a.main.children[index]?.innerText || '';
          const bv = b.main.children[index]?.dataset.sortValue || b.main.children[index]?.innerText || '';
          if (type === 'number') return (Number(av) - Number(bv)) * direction;
          return av.localeCompare(bv, 'de', {numeric: true, sensitivity: 'base'}) * direction;
        })
        .forEach((block) => block.rows.forEach((row) => tbody.appendChild(row)));
    });
  });
}
bindSortButtons();

document.addEventListener('click', (event) => {
  const opener = event.target.closest('[data-dialog-open]');
  if (opener) {
    event.preventDefault();
    const target = document.getElementById(opener.dataset.dialogOpen || '');
    if (target) {
      opener.closest('.dialog-backdrop')?.classList.remove('is-open');
      opener.closest('.dialog-backdrop')?.style.removeProperty('display');
      target.style.removeProperty('display');
      target.classList.add('is-open');
    }
    return;
  }
  const closer = event.target.closest('[data-dialog-close]');
  if (closer) {
    event.preventDefault();
    const dialog = closer.closest('.dialog-backdrop');
    dialog?.classList.remove('is-open');
    if (dialog) dialog.style.display = 'none';
    if (dialog?.id && location.hash === '#' + dialog.id) {
      history.replaceState(null, '', location.pathname + location.search);
    }
    return;
  }
  const toggle = event.target.closest('[data-id-toggle]');
  document.querySelectorAll('.id-reveal.is-open').forEach((item) => {
    if (!toggle || item !== toggle.closest('.id-reveal')) item.classList.remove('is-open');
  });
  if (toggle) {
    event.preventDefault();
    toggle.closest('.id-reveal')?.classList.toggle('is-open');
  }
});

document.querySelectorAll('[data-color-picker]').forEach((picker) => {
  const text = picker.parentElement?.querySelector('[data-color-text]');
  if (!text) return;
  picker.addEventListener('input', () => {
    text.value = picker.value || '';
  });
  text.addEventListener('input', () => {
    const value = text.value.trim();
    if (/^#?[0-9a-fA-F]{6}$/.test(value)) {
      picker.value = value.startsWith('#') ? value : '#' + value;
    }
  });
});

document.querySelectorAll('[data-add-product-form]').forEach((form) => {
  const urlInput = form.querySelector('[data-product-url]');
  const providerSelect = form.querySelector('[data-provider-select]');
  const shopStatus = form.querySelector('[data-shop-status]');
  const genericStatus = form.querySelector('[data-generic-status]');
  if (!urlInput || !providerSelect) return;
  const providerRules = [
    ['rewe.de', 'rewe::', 'REWE'],
    ['mueller.de', 'mueller::', 'Müller'],
    ['mediamarkt.de', 'mediamarkt::', 'MediaMarkt'],
    ['aldi-sued.de', 'aldi_sued::', 'ALDI Süd'],
    ['rossmann.de', 'rossmann::', 'Rossmann'],
  ];
  const setStatus = (element, mode, text) => {
    if (!element) return;
    element.classList.remove('is-good', 'is-warn', 'is-neutral');
    element.classList.add(mode);
    element.textContent = text;
  };
  const selectProvider = () => {
    const value = urlInput.value.toLowerCase();
    const rule = providerRules.find(([domain]) => value.includes(domain));
    if (!value.trim()) {
      setStatus(shopStatus, 'is-neutral', 'URL einfügen, dann wird ein passender Anbieter vorgeschlagen.');
      setStatus(genericStatus, 'is-neutral', 'Für nicht erkannte Shops oder Spezialseiten mit mehreren Preisen.');
      return;
    }
    if (!rule) {
      setStatus(shopStatus, 'is-neutral', 'Kein eingebauter Shop erkannt.');
      setStatus(genericStatus, 'is-warn', 'Kein Shop erkannt. Nutze am besten diese Box für beliebige Webseiten.');
      return;
    }
    const prefix = rule[1];
    const providerName = rule[2];
    const options = [...providerSelect.options].filter((item) => item.value.startsWith(prefix));
    if (options.length === 1) {
      providerSelect.value = options[0].value;
      setStatus(shopStatus, 'is-good', `Shop erkannt: ${providerName}. Der passende Eintrag wurde ausgewählt.`);
      setStatus(genericStatus, 'is-neutral', 'Die freie Webseiten-Erkennung ist hier nur nötig, wenn der Shop-Parser nicht passt.');
    } else if (options.length > 1) {
      providerSelect.value = options[0].value;
      setStatus(shopStatus, 'is-warn', `${providerName} erkannt. Bitte den passenden ${providerName} Eintrag auswählen.`);
      setStatus(genericStatus, 'is-neutral', 'Die freie Webseiten-Erkennung ist hier nur nötig, wenn der Shop-Parser nicht passt.');
    } else {
      setStatus(shopStatus, 'is-neutral', `${providerName} erkannt, aber kein passender Eintrag ist eingerichtet.`);
      setStatus(genericStatus, 'is-warn', 'Kein eingerichteter Shop-Eintrag gefunden. Nutze am besten diese Box oder lege zuerst den Anbieter an.');
    }
  };
  urlInput.addEventListener('input', selectProvider);
  urlInput.addEventListener('change', selectProvider);
  selectProvider();
});

document.querySelectorAll('form').forEach((form) => {
  form.addEventListener('submit', (event) => {
    if (form.dataset.ajaxForm !== undefined) {
      event.preventDefault();
      const status = document.querySelector(form.dataset.statusTarget || '');
      const button = event.submitter || form.querySelector('button[type="submit"], button:not([type])');
      const originalText = button?.textContent || '';
      if (status) {
        status.hidden = false;
        status.textContent = 'MQTT-Aktion wird gesendet...';
      }
      if (button) {
        button.disabled = true;
        button.textContent = 'Bitte warten...';
      }
      fetch(form.action, {method: form.method || 'POST'})
        .then((response) => response.json())
        .then((data) => {
          if (status) status.textContent = data.message || (data.ok ? 'MQTT-Aktion ausgeführt.' : 'MQTT-Aktion fehlgeschlagen.');
        })
        .catch((error) => {
          if (status) status.textContent = 'MQTT-Aktion fehlgeschlagen: ' + error;
        })
        .finally(() => {
          if (button) {
            button.disabled = false;
            button.textContent = originalText;
          }
        });
      return;
    }
    if (form.dataset.noScroll) {
      sessionStorage.setItem('preisermittlung.restoreScrollY', String(window.scrollY));
    }
    if (form.dataset.pdfProcessing) {
      document.querySelectorAll('[data-upload-status-global]').forEach((status) => {
        status.hidden = false;
        status.textContent = form.dataset.pdfProcessing;
      });
      if (!form.dataset.noScroll) window.scrollTo({top: 0, behavior: 'smooth'});
    }
    if (form.dataset.backupUploadForm !== undefined) {
      const message = 'Backup wird hochgeladen und geprüft. Bei großen ZIP-Dateien kann das einen Moment dauern...';
      showBusyOverlay(message);
      window.scrollTo({top: 0, behavior: 'smooth'});
      if (form.dataset.backupSubmitting !== 'true') {
        event.preventDefault();
        form.dataset.backupSubmitting = 'true';
        const button = event.submitter || form.querySelector('button[type="submit"], button:not([type])');
        if (button) {
          button.disabled = true;
          button.textContent = 'Bitte warten...';
        }
        requestAnimationFrame(() => {
          window.setTimeout(() => HTMLFormElement.prototype.submit.call(form), 120);
        });
        return;
      }
    }
    const submitter = event.submitter || document.activeElement;
    if (submitter && submitter.name && !form.querySelector(`input[type="hidden"][data-submit-proxy="${submitter.name}"]`)) {
      const proxy = document.createElement('input');
      proxy.type = 'hidden';
      proxy.name = submitter.name;
      proxy.value = submitter.value || '';
      proxy.dataset.submitProxy = submitter.name;
      form.appendChild(proxy);
    }
    const button = submitter?.matches?.('button[type="submit"], button:not([type])') ? submitter : form.querySelector('button[type="submit"], button:not([type])');
    if (!button || button.dataset.busy === 'true') return;
    button.dataset.busy = 'true';
    button.dataset.originalText = button.textContent;
    button.textContent = button.dataset.busyText || 'Bitte warten...';
    form.querySelectorAll('button[type="submit"], button:not([type])').forEach((item) => {
      item.disabled = true;
    });
  });
});

document.querySelectorAll('[data-auto-submit-file]').forEach((input) => {
  input.addEventListener('change', () => {
    if (input.files && input.files.length && input.form) {
      document.querySelectorAll('[data-upload-status], [data-upload-status-global]').forEach((status) => {
        status.hidden = false;
        status.textContent = 'PDF wird hochgeladen und vorhandene Suchwörter werden geprüft...';
      });
      const button = input.form.querySelector('button[type="submit"]');
      if (button) {
        button.dataset.busy = 'true';
        button.textContent = 'Bitte warten...';
        button.disabled = true;
      }
      window.scrollTo({top: 0, behavior: 'smooth'});
      window.setTimeout(() => input.form.submit(), 80);
    }
  });
});

document.addEventListener('click', (event) => {
  const button = event.target.closest('[data-scroll-strip]');
  if (!button) return;
  event.preventDefault();
  const strip = document.querySelector(button.dataset.scrollStrip || '');
  if (!strip) return;
  const direction = button.dataset.scrollDirection === 'prev' ? -1 : 1;
  strip.scrollBy({left: direction * Math.max(220, strip.clientWidth * 0.8), behavior: 'smooth'});
});

document.querySelectorAll('[data-pdf-category-select]').forEach((select) => {
  const syncPdfCategory = () => {
    document.querySelectorAll('[data-pdf-confirm-category]').forEach((hidden) => {
      hidden.value = select.value;
    });
  };
  select.addEventListener('change', syncPdfCategory);
  syncPdfCategory();
});

document.querySelectorAll('[data-live-search]').forEach((input) => {
  let timer = null;
  input.addEventListener('input', () => {
    window.clearTimeout(timer);
    timer = window.setTimeout(() => {
      const form = input.form;
      const params = new URLSearchParams(new FormData(form));
      const url = form.action + '?' + params.toString();
      fetch(url, {cache: 'no-store'})
        .then((response) => response.text())
        .then((html) => {
          const next = new DOMParser().parseFromString(html, 'text/html');
          const nextResults = next.querySelector('[data-results]');
          const currentResults = document.querySelector('[data-results]');
          const nextSummary = next.querySelector('[data-summary]');
          const currentSummary = document.querySelector('[data-summary]');
          if (nextResults && currentResults) {
            currentResults.innerHTML = nextResults.innerHTML;
            bindSortButtons(currentResults);
          }
          if (nextSummary && currentSummary) currentSummary.innerHTML = nextSummary.innerHTML;
          const query = params.toString();
          history.replaceState(null, '', query ? '?' + query : '/');
        });
    }, 250);
  });
});
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def format_cents(cents: Optional[int]) -> str:
    if cents is None:
        return "-"
    euros, remainder = divmod(int(cents), 100)
    return f"{euros},{remainder:02d} €"


def parse_price_cents(value: Any) -> Optional[int]:
    raw = str(value or "").strip()
    if not raw:
        return None
    cleaned = raw.replace("€", "").replace(" ", "").replace("\u00a0", "")
    if "," in cleaned and "." in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    else:
        cleaned = cleaned.replace(",", ".")
    try:
        cents = int(round(float(cleaned) * 100))
    except ValueError:
        return None
    return cents if cents >= 0 else None


def format_price_input(cents: Optional[int]) -> str:
    if cents is None:
        return ""
    return f"{int(cents) / 100:.2f}".replace(".", ",")


def product_target_price_cents(product: Dict[str, Any]) -> Optional[int]:
    value = product.get("target_price_cents")
    if value in (None, ""):
        return None
    try:
        cents = int(value)
    except (TypeError, ValueError):
        return parse_price_cents(value)
    return cents if cents >= 0 else None


def target_price_highlight_enabled(config: Dict[str, Any]) -> bool:
    raw = settings_value(config, "target_price_highlight_enabled", "false").strip().lower()
    return raw in {"1", "true", "yes", "on", "ja"}


def target_price_extra_matches_enabled(config: Dict[str, Any]) -> bool:
    raw = settings_value(config, "target_price_extra_matches_enabled", "false").strip().lower()
    return raw in {"1", "true", "yes", "on", "ja"}


def target_price_missed_display_mode(config: Dict[str, Any]) -> str:
    value = settings_value(config, "target_price_missed_display", "normal").strip().lower()
    return value if value in {"hide", "normal", "muted"} else "normal"


def product_below_target_price(product: Dict[str, Any]) -> bool:
    target_cents = product_target_price_cents(product)
    if target_cents is None:
        return False
    price_cents = (product.get("state") or {}).get("price_cents")
    try:
        return int(price_cents) <= target_cents
    except (TypeError, ValueError):
        return False


def target_price_badge_html(
    target_cents: Optional[int], reached: bool, missed_mode: str, compact: bool = False
) -> str:
    if target_cents is None:
        return ""
    if not reached and missed_mode == "hide":
        return ""
    label = "WP" if compact else "Wunschpreis"
    state_class = "is-hit" if reached else ("is-muted" if missed_mode == "muted" else "is-normal")
    return f'<span class="target-price-badge {state_class}">{escape(label)} {escape(format_cents(target_cents))}</span>'


def unit_price_html(item_state: Dict[str, Any]) -> str:
    package_size = item_state.get("package_size_text")
    unit_price = item_state.get("unit_price_text")
    if package_size and unit_price:
        return f'{escape(str(package_size))}<br><span class="small">{escape(str(unit_price))}</span>'
    if package_size:
        return escape(str(package_size))
    if unit_price:
        return f'<span class="small">{escape(str(unit_price))}</span>'
    return escape(str(item_state.get("unit_price") or "-"))


def format_datetime_de(value: Optional[str]) -> str:
    if not value:
        return "-"
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        parsed = parsed.astimezone()
        return parsed.strftime("%d.%m.%Y %H:%M")
    except ValueError:
        return str(value)


def current_process_memory_text() -> str:
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    bytes_value = rss if sys.platform == "darwin" else rss * 1024
    value = float(bytes_value)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} GB"


def add_seconds_iso(value: Optional[str], seconds: float) -> Optional[str]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return datetime.fromtimestamp(parsed.timestamp() + seconds, timezone.utc).isoformat()
    except ValueError:
        return None


def icon(name: str) -> str:
    paths = {
        "refresh": '<path d="M21 12a9 9 0 0 1-15.5 6.2"/><path d="M3 12a9 9 0 0 1 15.5-6.2"/><path d="M18 2v4h-4"/><path d="M6 22v-4h4"/>',
        "shop": '<path d="M6 2 3 6v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V6l-3-4Z"/><path d="M3 6h18"/><path d="M16 10a4 4 0 0 1-8 0"/>',
        "trash": '<path d="M3 6h18"/><path d="M8 6V4h8v2"/><path d="M19 6l-1 14H6L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/>',
        "plus": '<path d="M12 5v14"/><path d="M5 12h14"/>',
        "copy": '<rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>',
        "download": '<path d="M12 3v12"/><path d="m7 10 5 5 5-5"/><path d="M5 21h14"/>',
        "upload": '<path d="M12 21V9"/><path d="m7 14 5-5 5 5"/><path d="M5 3h14"/>',
        "search": '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
        "home": '<path d="m3 11 9-8 9 8"/><path d="M5 10v10h14V10"/><path d="M9 20v-6h6v6"/>',
        "list": '<path d="M8 6h13"/><path d="M8 12h13"/><path d="M8 18h13"/><path d="M3 6h.01"/><path d="M3 12h.01"/><path d="M3 18h.01"/>',
        "image": '<rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="M21 15l-5-5L5 21"/>',
        "pdf": '<path d="M6 2h8l4 4v16H6z"/><path d="M14 2v5h5"/><path d="M8 13h1.5a1.5 1.5 0 0 0 0-3H8v7"/><path d="M13 10v7h1.5a2.5 2.5 0 0 0 0-5H13"/><path d="M18 10h3"/><path d="M18 13h2"/>',
        "settings": '<path d="M12 15.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7Z"/><path d="M19.4 15a1.8 1.8 0 0 0 .36 1.98l.04.04a2 2 0 1 1-2.83 2.83l-.04-.04a1.8 1.8 0 0 0-1.98-.36 1.8 1.8 0 0 0-1.1 1.65V21a2 2 0 1 1-4 0v-.06a1.8 1.8 0 0 0-1.1-1.65 1.8 1.8 0 0 0-1.98.36l-.04.04a2 2 0 1 1-2.83-2.83l.04-.04A1.8 1.8 0 0 0 4.6 15a1.8 1.8 0 0 0-1.65-1.1H3a2 2 0 1 1 0-4h.06A1.8 1.8 0 0 0 4.7 8.8a1.8 1.8 0 0 0-.36-1.98l-.04-.04a2 2 0 1 1 2.83-2.83l.04.04A1.8 1.8 0 0 0 9 4.6a1.8 1.8 0 0 0 1.1-1.65V3a2 2 0 1 1 4 0v.06A1.8 1.8 0 0 0 15.2 4.7a1.8 1.8 0 0 0 1.98-.36l.04-.04a2 2 0 1 1 2.83 2.83l-.04.04a1.8 1.8 0 0 0-.36 1.98 1.8 1.8 0 0 0 1.65 1.1H21a2 2 0 1 1 0 4h-.06A1.8 1.8 0 0 0 19.4 15Z"/>',
        "sun": '<circle cx="12" cy="12" r="4"/><path d="M12 2v2"/><path d="M12 20v2"/><path d="m4.93 4.93 1.41 1.41"/><path d="m17.66 17.66 1.41 1.41"/><path d="M2 12h2"/><path d="M20 12h2"/><path d="m6.34 17.66-1.41 1.41"/><path d="m19.07 4.93-1.41 1.41"/>',
        "moon": '<path d="M12 3a6 6 0 0 0 9 7.7A9 9 0 1 1 12 3Z"/>',
    }
    return f'<svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">{paths[name]}</svg>'


def market_label(market: Dict[str, Any]) -> str:
    return " ".join(
        str(part)
        for part in [
            market.get("market_name") if not market.get("market_company") else None,
            market.get("market_company") or market.get("companyName"),
            market.get("market_street") or market.get("street"),
            market.get("postal_code") or market.get("zipCode"),
            market.get("market_city") or market.get("city"),
        ]
        if part
    )


def market_address_parts(market: Dict[str, Any]) -> List[str]:
    company = market.get("market_company") or market.get("companyName") or ""
    street = market.get("market_street") or market.get("street") or ""
    city_line = " ".join(
        str(part)
        for part in [
            market.get("postal_code") or market.get("zipCode"),
            market.get("market_city") or market.get("city"),
        ]
        if part
    )
    return [str(part) for part in [company, street, city_line] if part]


def market_address_html(market: Dict[str, Any]) -> str:
    parts = market_address_parts(market)
    if not parts:
        return "-"
    return '<span class="address-lines">' + "".join(f"<span>{escape(part)}</span>" for part in parts) + "</span>"


def product_market_html(provider: str, product: Dict[str, Any], market: Dict[str, Any]) -> str:
    if provider == "generic":
        parsed = urllib.parse.urlparse(str(product.get("product_url") or ""))
        domain = parsed.netloc.removeprefix("www.") or "Webseite"
        return f'<span class="address-lines"><span>{escape(domain)}</span></span>'
    if provider == "aez_pdf":
        return '<span class="address-lines"><span>Wochenblatt</span><span>Prospekt-PDF</span></span>'
    if str(market.get("market_id") or "") == "online":
        return '<span class="address-lines"><span>Online</span></span>'
    street = market.get("market_street") or market.get("street") or ""
    city_line = " ".join(
        str(part)
        for part in [
            market.get("postal_code") or market.get("zipCode"),
            market.get("market_city") or market.get("city"),
        ]
        if part
    )
    parts = [str(part) for part in [street, city_line] if part]
    if not parts:
        return market_address_html(market)
    return '<span class="address-lines compact-market">' + "".join(f"<span>{escape(part)}</span>" for part in parts) + "</span>"


def product_market_sort_value(provider: str, product: Dict[str, Any], market: Dict[str, Any]) -> str:
    if provider == "generic":
        parsed = urllib.parse.urlparse(str(product.get("product_url") or ""))
        return parsed.netloc.removeprefix("www.") or "Generic"
    if provider == "aez_pdf":
        return "AEZ Wochenblatt"
    return market_label(market) or ""


def market_by_id(config: Dict[str, Any], market_id: str, provider: Optional[str] = None) -> Optional[Dict[str, str]]:
    for market in markets_from_config(config):
        if str(market.get("market_id")) == str(market_id) and (
            provider is None or market_provider(market) == provider
        ):
            return market
    return None


def article_number_from_url(url: str) -> str:
    match = re.search(r"/(\d+)(?:[/?#].*)?$", url.strip())
    return match.group(1) if match else ""


def load_config() -> Dict[str, Any]:
    return parse_simple_yaml(CONFIG_PATH)


def save_config(config: Dict[str, Any]) -> None:
    write_simple_yaml(config, CONFIG_PATH)


def slug_from_name(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return slug or DEFAULT_CATEGORY_ID


def categories_from_config(config: Dict[str, Any]) -> List[Dict[str, str]]:
    categories = [dict(category) for category in config.get("categories") or [] if category.get("id")]
    if not any(category.get("id") == DEFAULT_CATEGORY_ID for category in categories):
        categories.insert(0, {"id": DEFAULT_CATEGORY_ID, "name": DEFAULT_CATEGORY_NAME})
    return sorted(categories, key=lambda category: (category.get("name") or category["id"]).casefold())


def category_name(config: Dict[str, Any], category_id: Optional[str]) -> str:
    lookup = {category["id"]: category.get("name") or category["id"] for category in categories_from_config(config)}
    return lookup.get(category_id or DEFAULT_CATEGORY_ID, DEFAULT_CATEGORY_NAME)


def normalize_hex_color(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    if not value.startswith("#"):
        value = f"#{value}"
    return value.lower() if re.fullmatch(r"#[0-9a-fA-F]{6}", value) else ""


def category_color(category: Dict[str, Any]) -> str:
    return normalize_hex_color(str(category.get("color") or ""))


def category_lookup(config: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {category["id"]: category for category in categories_from_config(config)}


def category_chip_html(category: Dict[str, Any], href: str = "") -> str:
    label = escape(category.get("name") or category["id"])
    color = category_color(category)
    style = f' style="--category-color: {escape(color)}"' if color else ""
    color_class = " has-color" if color else ""
    tag = "a" if href else "span"
    href_attr = f' href="{escape(href)}"' if href else ""
    return f'<{tag} class="category-chip{color_class}"{style}{href_attr}>{label}</{tag}>'


def category_color_from_form() -> str:
    return normalize_hex_color(request.form.get("color_text", ""))


def category_quick_enabled(category: Dict[str, Any]) -> bool:
    raw = str(category.get("quick_cat", "false")).strip().lower()
    return raw in {"1", "true", "yes", "on", "ja"}


def category_admin_row_html(category: Dict[str, Any], product_count: int) -> str:
    color = category_color(category)
    swatch = f'<span class="category-swatch" style="--category-color: {escape(color)}"></span>' if color else ""
    quick_label = '<br><span class="small">Quick Cat</span>' if category_quick_enabled(category) else ""
    delete_button = (
        f'<a class="button danger" href="/?categories_dialog=1&delete_category={escape(category["id"])}">Löschen</a>'
        if category["id"] != DEFAULT_CATEGORY_ID
        else ""
    )
    return (
        '<div class="market-row">'
        f'<div><strong>{swatch}{escape(category.get("name") or category["id"])}</strong><br>'
        f'<span class="small">{product_count} Artikel</span>{quick_label}</div>'
        '<div class="row-actions">'
        f'<a class="button" href="/?edit_category={escape(category["id"])}">Bearbeiten</a>'
        f'{delete_button}</div></div>'
    )


def product_category_id(product: Dict[str, Any]) -> str:
    return product.get("category_id") or DEFAULT_CATEGORY_ID


def unique_category_id(categories: List[Dict[str, str]], name: str) -> str:
    existing = {category.get("id") for category in categories}
    base = slug_from_name(name)
    if base not in existing:
        return base
    index = 2
    while f"{base}_{index}" in existing:
        index += 1
    return f"{base}_{index}"


def default_state() -> Dict[str, Any]:
    return {"products": {}, "last_refresh_started_at": None, "last_refresh_finished_at": None}


def load_state() -> Dict[str, Any]:
    if not STATE_PATH.exists():
        return default_state()
    try:
        raw_state = STATE_PATH.read_text(encoding="utf-8")
        if not raw_state.strip():
            return default_state()
        state = json.loads(raw_state)
    except (OSError, json.JSONDecodeError):
        return default_state()
    if not isinstance(state, dict):
        return default_state()
    state.setdefault("products", {})
    state.setdefault("last_refresh_started_at", None)
    state.setdefault("last_refresh_finished_at", None)
    return state


def save_state(state: Dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(state, ensure_ascii=False, indent=2) + "\n"
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=STATE_PATH.parent,
            prefix=".state.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(payload)
            temp_path = Path(handle.name)
        temp_path.replace(STATE_PATH)
    except PermissionError:
        STATE_PATH.write_text(payload, encoding="utf-8")


def atomic_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.NamedTemporaryFile(
            "wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(payload)
            temp_path = Path(handle.name)
        temp_path.replace(path)
    except PermissionError:
        path.write_bytes(payload)


def backup_manifest() -> Dict[str, Any]:
    return {
        "app": APP_NAME,
        "version": APP_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "contains": {
            "config": CONFIG_PATH.exists(),
            "state": STATE_PATH.exists(),
            "manual_pdfs": manual_pdf_reader.UPLOAD_DIR.exists(),
        },
    }


def create_backup_zip(include_config: bool, include_state: bool, include_pdfs: bool) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("metadata.json", json.dumps(backup_manifest(), ensure_ascii=False, indent=2) + "\n")
        if include_config and CONFIG_PATH.exists():
            archive.write(CONFIG_PATH, "config.yaml")
        if include_state and STATE_PATH.exists():
            archive.write(STATE_PATH, "state.json")
        if include_pdfs and manual_pdf_reader.UPLOAD_DIR.exists():
            for pdf_path in sorted(manual_pdf_reader.UPLOAD_DIR.glob("*.pdf"), key=lambda item: item.name.casefold()):
                archive.write(pdf_path, f"manual_pdfs/{pdf_path.name}")
    return buffer.getvalue()


def analyze_backup_file(path: Path) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "config": False,
        "state": False,
        "pdfs": [],
        "metadata": {},
    }
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        result["config"] = "config.yaml" in names
        result["state"] = "state.json" in names
        result["pdfs"] = sorted(
            Path(name).name
            for name in names
            if name.startswith("manual_pdfs/") and name.lower().endswith(".pdf") and Path(name).name
        )
        if "metadata.json" in names:
            try:
                result["metadata"] = json.loads(archive.read("metadata.json").decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                result["metadata"] = {}
    return result


def backup_has_components(info: Dict[str, Any]) -> bool:
    return bool(info.get("config") or info.get("state") or info.get("pdfs"))


def cleanup_old_backup_imports(max_age_seconds: int = 24 * 60 * 60) -> None:
    if not BACKUP_IMPORT_PATH.exists():
        return
    cutoff = time.time() - max_age_seconds
    for backup_path in BACKUP_IMPORT_PATH.glob("*.zip"):
        try:
            if backup_path.stat().st_mtime < cutoff:
                backup_path.unlink()
        except OSError:
            continue


def remove_pending_backup_import(state: Dict[str, Any]) -> None:
    backup_import = state.pop("backup_import", None) or {}
    token = backup_import.get("token")
    if token and re.fullmatch(r"[0-9a-f]{32}", str(token)):
        (BACKUP_IMPORT_PATH / f"{token}.zip").unlink(missing_ok=True)


def restore_backup_file(path: Path, restore_config: bool, restore_state: bool, restore_pdfs: bool) -> List[str]:
    restored: List[str] = []
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        if restore_config and "config.yaml" in names:
            atomic_write_bytes(CONFIG_PATH, archive.read("config.yaml"))
            restored.append("config.yaml")
        if restore_state and "state.json" in names:
            atomic_write_bytes(STATE_PATH, archive.read("state.json"))
            restored.append("state.json")
        if restore_pdfs:
            pdf_names = [
                name
                for name in names
                if name.startswith("manual_pdfs/") and name.lower().endswith(".pdf") and Path(name).name
            ]
            if pdf_names:
                manual_pdf_reader.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
                for existing_pdf in manual_pdf_reader.UPLOAD_DIR.glob("*.pdf"):
                    existing_pdf.unlink()
                manual_pdf_reader.GENERATED_DIR.mkdir(parents=True, exist_ok=True)
                for existing_image in manual_pdf_reader.GENERATED_DIR.glob("*.png"):
                    try:
                        existing_image.unlink()
                    except OSError:
                        pass
                for name in sorted(pdf_names):
                    atomic_write_bytes(manual_pdf_reader.UPLOAD_DIR / Path(name).name, archive.read(name))
                restored.append(f"{len(pdf_names)} PDF-Datei(en)")
    return restored


def set_notice(message: str) -> None:
    with state_lock:
        state = load_state()
        state["notice"] = message
        save_state(state)


def pop_notice(state: Dict[str, Any]) -> Optional[str]:
    notice = state.pop("notice", None)
    if notice is not None:
        save_state(state)
    return notice


def set_product_mqtt_notice(product_id: str, message: str) -> None:
    with state_lock:
        state = load_state()
        notices = state.setdefault("product_mqtt_notices", {})
        notices[product_id] = message
        save_state(state)


def pop_product_mqtt_notice(state: Dict[str, Any], product_id: str) -> Optional[str]:
    notices = state.get("product_mqtt_notices") or {}
    message = notices.pop(product_id, None)
    if message is not None:
        if notices:
            state["product_mqtt_notices"] = notices
        else:
            state.pop("product_mqtt_notices", None)
        save_state(state)
    return message


def set_pdf_analysis(data: Dict[str, Any]) -> None:
    with state_lock:
        state = load_state()
        state["pdf_analysis"] = data
        save_state(state)


def get_delay_seconds(config: Dict[str, Any]) -> float:
    raw = (config.get("settings") or {}).get("refresh_delay_seconds", "5")
    try:
        return max(0.0, float(str(raw).replace(",", ".")))
    except ValueError:
        return 5.0


def get_auto_refresh_enabled(config: Dict[str, Any]) -> bool:
    raw = str((config.get("settings") or {}).get("auto_refresh_enabled", "false")).lower()
    return raw in {"1", "true", "yes", "on"}


def get_auto_refresh_interval_seconds(config: Dict[str, Any]) -> float:
    raw = (config.get("settings") or {}).get("auto_refresh_interval_hours", "6")
    try:
        hours = float(str(raw).replace(",", "."))
        return max(0.1, hours) * 3600
    except ValueError:
        return 6 * 3600


def get_auto_refresh_manual_pdfs_enabled(config: Dict[str, Any]) -> bool:
    raw = str((config.get("settings") or {}).get("auto_refresh_manual_pdfs", "false")).lower()
    return raw in {"1", "true", "yes", "on"}


def get_api_enabled(config: Dict[str, Any]) -> bool:
    raw = str((config.get("settings") or {}).get("api_enabled", "true")).lower()
    return raw not in {"0", "false", "no", "off", "nein"}


def current_theme(config: Dict[str, Any]) -> str:
    theme = str((config.get("settings") or {}).get("theme", "light")).lower()
    return "dark" if theme == "dark" else "light"


def settings_value(config: Dict[str, Any], key: str, default: str) -> str:
    return str((config.get("settings") or {}).get(key, default))


def product_id_display_mode(config: Dict[str, Any]) -> str:
    value = settings_value(config, "product_id_display", "show").strip().lower()
    return value if value in {"show", "hide", "interactive"} else "show"


def pdf_extra_matches_display_mode(config: Dict[str, Any]) -> str:
    value = settings_value(config, "pdf_extra_matches_display", "wrap").strip().lower()
    return value if value in {"wrap", "slider", "off"} else "wrap"


def pdf_extra_matches_expanded(config: Dict[str, Any]) -> bool:
    raw = settings_value(config, "pdf_extra_matches_expanded", "true").strip().lower()
    return raw in {"1", "true", "yes", "on", "ja"}


def default_home_view(config: Dict[str, Any]) -> str:
    value = settings_value(config, "default_home_view", "all").strip().lower()
    return "grouped" if value == "grouped" else "all"


def multi_category_filter_enabled(config: Dict[str, Any]) -> bool:
    raw = settings_value(config, "multi_category_filter_enabled", "false").strip().lower()
    return raw in {"1", "true", "yes", "on", "ja"}


def selected_category_ids_from_args(valid_ids: set[str]) -> List[str]:
    values: List[str] = []
    for raw in request.args.getlist("categories"):
        values.extend(part.strip() for part in str(raw).split(","))
    selected = []
    for value in values:
        if value and value in valid_ids and value not in selected:
            selected.append(value)
    return selected


def product_enabled(product: Dict[str, Any]) -> bool:
    raw = str(product.get("enabled", "true")).strip().lower()
    return raw not in {"0", "false", "no", "off", "nein"}


def default_mqtt_client_id() -> str:
    host = re.sub(r"[^a-zA-Z0-9_-]+", "-", socket.gethostname()).strip("-").lower()
    return f"preisermittlung-{host or 'server'}"


def save_settings_from_form(config: Dict[str, Any]) -> Dict[str, Any]:
    settings = config.setdefault("settings", {})
    if "refresh_delay_seconds" in request.form:
        settings["refresh_delay_seconds"] = request.form.get("refresh_delay_seconds", "5").strip() or "5"
    if "auto_refresh_interval_hours" in request.form:
        settings["auto_refresh_interval_hours"] = request.form.get("auto_refresh_interval_hours", "6").strip() or "6"
    if "auto_refresh_enabled" in request.form:
        settings["auto_refresh_enabled"] = "true" if request.form.get("auto_refresh_enabled") == "true" else "false"
    elif "refresh_delay_seconds" in request.form:
        settings["auto_refresh_enabled"] = "false"
    if "auto_refresh_manual_pdfs_present" in request.form:
        settings["auto_refresh_manual_pdfs"] = "true" if request.form.get("auto_refresh_manual_pdfs") == "true" else "false"
    if "api_settings_present" in request.form:
        settings["api_enabled"] = "true" if request.form.get("api_enabled") == "true" else "false"
    if "product_id_display" in request.form:
        mode = request.form.get("product_id_display", "show").strip().lower()
        settings["product_id_display"] = mode if mode in {"show", "hide", "interactive"} else "show"
    if "default_home_view" in request.form:
        view = request.form.get("default_home_view", "all").strip().lower()
        settings["default_home_view"] = "grouped" if view == "grouped" else "all"
    if "home_settings_present" in request.form:
        settings["multi_category_filter_enabled"] = (
            "true" if request.form.get("multi_category_filter_enabled") == "true" else "false"
        )
        settings["target_price_highlight_enabled"] = (
            "true" if request.form.get("target_price_highlight_enabled") == "true" else "false"
        )
        settings["target_price_extra_matches_enabled"] = (
            "true" if request.form.get("target_price_extra_matches_enabled") == "true" else "false"
        )
        missed_mode = request.form.get("target_price_missed_display", "normal").strip().lower()
        settings["target_price_missed_display"] = missed_mode if missed_mode in {"hide", "normal", "muted"} else "normal"
    if "pdf_extra_matches_display" in request.form:
        mode = request.form.get("pdf_extra_matches_display", "wrap").strip().lower()
        settings["pdf_extra_matches_display"] = mode if mode in {"wrap", "slider", "off"} else "wrap"
    if "pdf_extra_matches_expanded_present" in request.form:
        settings["pdf_extra_matches_expanded"] = (
            "true" if request.form.get("pdf_extra_matches_expanded") == "true" else "false"
        )
    if "mqtt_client_id" in request.form:
        settings["mqtt_enabled"] = "true" if request.form.get("mqtt_enabled") == "true" else "false"
        settings["mqtt_client_id"] = request.form.get("mqtt_client_id", "").strip() or default_mqtt_client_id()
        settings["mqtt_broker_url"] = request.form.get("mqtt_broker_url", "").strip()
        settings["mqtt_port"] = request.form.get("mqtt_port", "").strip() or "1883"
        settings["mqtt_username"] = request.form.get("mqtt_username", "").strip()
        settings["mqtt_keepalive"] = request.form.get("mqtt_keepalive", "").strip() or "60"
        password = request.form.get("mqtt_password", "")
        if password:
            settings["mqtt_password"] = password
        elif "mqtt_password" in request.form:
            settings.pop("mqtt_password", None)
    if "user_agent" in request.form:
        user_agent = request.form.get("user_agent", "").strip()
        if user_agent:
            settings["user_agent"] = user_agent
        else:
            settings.pop("user_agent", None)
    return settings


def parse_mqtt_target(settings: Dict[str, Any]) -> Dict[str, Any]:
    raw_url = str(settings.get("mqtt_broker_url") or "").strip()
    if not raw_url:
        raise ValueError("Broker-URL fehlt.")
    if "://" not in raw_url:
        raw_url = f"mqtt://{raw_url}"
    parsed = urllib.parse.urlparse(raw_url)
    scheme = (parsed.scheme or "mqtt").lower()
    if scheme not in {"mqtt", "tcp", "mqtts", "ssl", "tls"}:
        raise ValueError("Erlaubte Protokolle: mqtt://, tcp://, mqtts://, ssl:// oder tls://.")
    host = parsed.hostname or parsed.path
    if not host:
        raise ValueError("Host in der Broker-URL fehlt.")
    port_raw = str(settings.get("mqtt_port") or "").strip()
    port = int(port_raw) if port_raw else (parsed.port or (8883 if scheme in {"mqtts", "ssl", "tls"} else 1883))
    keepalive = int(str(settings.get("mqtt_keepalive") or "60").strip())
    return {
        "host": host,
        "port": port,
        "tls": scheme in {"mqtts", "ssl", "tls"},
        "keepalive": max(5, keepalive),
    }


def mqtt_client_from_settings(settings: Dict[str, Any]) -> Any:
    try:
        import paho.mqtt.client as mqtt
    except ImportError as exc:
        raise RuntimeError("MQTT-Bibliothek fehlt. Bitte `pip install -r requirements.txt` ausführen.") from exc

    client_id = str(settings.get("mqtt_client_id") or "").strip() or default_mqtt_client_id()
    try:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
    except AttributeError:
        client = mqtt.Client(client_id=client_id)
    username = str(settings.get("mqtt_username") or "").strip()
    if username:
        client.username_pw_set(username, str(settings.get("mqtt_password") or ""))
    return client


def mqtt_settings_signature(settings: Dict[str, Any]) -> str:
    parts = [
        settings.get("mqtt_enabled"),
        settings.get("mqtt_client_id"),
        settings.get("mqtt_broker_url"),
        settings.get("mqtt_port"),
        settings.get("mqtt_username"),
        settings.get("mqtt_password"),
        settings.get("mqtt_keepalive"),
    ]
    return "|".join(str(part or "") for part in parts)


def set_mqtt_runtime(ok: bool, message: str, target: Optional[Dict[str, Any]] = None) -> None:
    with state_lock:
        state = load_state()
        state["mqtt_runtime"] = {
            "ok": ok,
            "message": message,
            "checked_at": now_iso(),
            "host": (target or {}).get("host"),
            "port": (target or {}).get("port"),
        }
        save_state(state)


def test_mqtt_connection(settings: Dict[str, Any]) -> str:
    target = parse_mqtt_target(settings)
    client = mqtt_client_from_settings(settings)
    if target["tls"]:
        client.tls_set()
    rc = client.connect(target["host"], target["port"], target["keepalive"])
    client.disconnect()
    if rc != 0:
        raise RuntimeError(f"Broker hat Verbindungscode {rc} zurückgegeben.")
    return f"MQTT-Test erfolgreich: {target['host']}:{target['port']}"


def mqtt_slug(value: Any) -> str:
    text = re.sub(r"[^a-zA-Z0-9_-]+", "_", str(value or "")).strip("_").lower()
    return text or "artikel"


def mqtt_topic_base(product: Dict[str, Any]) -> str:
    return f"preisermittlung/products/{mqtt_slug(product.get('id'))}"


def mqtt_discovery_topic(product: Dict[str, Any]) -> str:
    return f"homeassistant/sensor/preisermittlung/{mqtt_slug(product.get('id'))}/config"


def mqtt_state_topic(product: Dict[str, Any]) -> str:
    return f"{mqtt_topic_base(product)}/state"


def mqtt_availability_topic() -> str:
    return "preisermittlung/status"


def mqtt_pdf_matches(item_state: Dict[str, Any]) -> List[Dict[str, Any]]:
    matches = item_state.get("matches")
    if not isinstance(matches, list):
        return []
    result = []
    for match in matches:
        if not isinstance(match, dict):
            continue
        result.append(
            {
                "title": match.get("title"),
                "price": match.get("price"),
                "price_cents": match.get("price_cents"),
                "price_text": match.get("price_text"),
                "currency": match.get("currency") or "EUR",
                "unit_price": match.get("unit_price_text") or match.get("unit_price"),
                "package_size": match.get("package_size_text"),
                "url": match.get("url"),
                "image_url": match.get("image_url"),
                "pdf_page": match.get("pdf_page"),
                "pdf_file_name": match.get("pdf_file_name"),
                "provider_article_number": match.get("provider_article_number"),
            }
        )
    return result


def mqtt_pdf_extra_matches(item_state: Dict[str, Any]) -> List[Dict[str, Any]]:
    matches = mqtt_pdf_matches(item_state)
    return [
        match
        for match in matches
        if not (
            match.get("provider_article_number") == item_state.get("provider_article_number")
            or (
                match.get("pdf_page") == item_state.get("pdf_page")
                and match.get("price_cents") == item_state.get("price_cents")
                and match.get("title") == item_state.get("title")
            )
        )
    ]


def product_market_text(provider: str, product: Dict[str, Any], market: Dict[str, Any], item_state: Dict[str, Any]) -> str:
    if provider == "generic":
        parsed = urllib.parse.urlparse(str(product.get("product_url") or item_state.get("url") or ""))
        return parsed.netloc.removeprefix("www.") or "Webseite"
    if provider_kind(provider) == "prospect":
        return short_pdf_label(item_state.get("pdf_file_name") or product.get("market_id") or provider_label(provider))
    if str(market.get("market_id") or "") == "online":
        return "Online"
    return market_label(market) or str(product.get("market_id") or "")


def mqtt_state_payload(config: Dict[str, Any], product: Dict[str, Any]) -> Dict[str, Any]:
    markets = markets_from_config(config)
    item_state = product.get("state") or {}
    provider = product_provider(config, product)
    market = market_for_selection(provider, product.get("market_id", ""), markets) or {}
    category = category_lookup(config).get(product_category_id(product), {})
    market_text = product_market_text(provider, product, market, item_state)
    price_cents = item_state.get("price_cents")
    old_price_cents = item_state.get("old_price_cents")
    target_cents = product_target_price_cents(product)
    status = "disabled" if not product_enabled(product) else ("error" if item_state.get("last_error") else "ok")
    payload = {
        "id": product.get("id"),
        "name": product_display_name(product, item_state),
        "article_number": product.get("article_number"),
        "search_term": product.get("search_term") or item_state.get("pdf_search_term"),
        "provider": provider,
        "provider_name": provider_label(provider),
        "shop": provider_label(provider),
        "shop_detail": market_text,
        "source_type": "prospect" if provider_kind(provider) == "prospect" else "shop",
        "market_id": product.get("market_id"),
        "market": market_text,
        "category_id": product_category_id(product),
        "category": category.get("name") or DEFAULT_CATEGORY_NAME,
        "price": round(int(price_cents) / 100, 2) if price_cents is not None else None,
        "price_cents": price_cents,
        "price_text": format_cents(price_cents),
        "currency": item_state.get("currency") or "EUR",
        "old_price": round(int(old_price_cents) / 100, 2) if old_price_cents is not None else None,
        "old_price_cents": old_price_cents,
        "old_price_text": format_cents(old_price_cents) if old_price_cents else None,
        "target_price": round(int(target_cents) / 100, 2) if target_cents is not None else None,
        "target_price_cents": target_cents,
        "target_price_text": format_cents(target_cents) if target_cents is not None else None,
        "below_target_price": product_below_target_price(product),
        "package_size": item_state.get("package_size_text"),
        "unit_price": item_state.get("unit_price_text") or item_state.get("unit_price"),
        "available": status == "ok",
        "enabled": product_enabled(product),
        "status": status,
        "error": item_state.get("last_error"),
        "url": item_state.get("url") or product.get("product_url"),
        "image_url": item_state.get("image_url"),
        "last_checked": item_state.get("last_checked_at"),
        "last_changed": item_state.get("last_changed_at"),
        "pdf_page": item_state.get("pdf_page"),
        "pdf_file": item_state.get("pdf_file_name"),
        "match_count": item_state.get("match_count"),
    }
    if provider_kind(provider) == "prospect":
        payload["matches"] = mqtt_pdf_matches(item_state)
        payload["extra_matches"] = mqtt_pdf_extra_matches(item_state)
    return payload


def mqtt_discovery_payload(config: Dict[str, Any], product: Dict[str, Any]) -> Dict[str, Any]:
    item_state = product.get("state") or {}
    name = product_display_name(product, item_state)
    safe_id = mqtt_slug(product.get("id"))
    return {
        "name": name,
        "unique_id": f"preisermittlung_{safe_id}_price",
        "object_id": f"preisermittlung_{safe_id}_price",
        "state_topic": mqtt_state_topic(product),
        "value_template": "{{ value_json.price if value_json.price is not none else 'unknown' }}",
        "unit_of_measurement": "€",
        "device_class": "monetary",
        "state_class": "measurement",
        "icon": "mdi:cart",
        "json_attributes_topic": mqtt_state_topic(product),
        "availability_topic": mqtt_availability_topic(),
        "payload_available": "online",
        "payload_not_available": "offline",
        "device": {
            "identifiers": ["preisermittlung"],
            "name": APP_NAME,
            "manufacturer": APP_NAME,
            "sw_version": APP_VERSION,
        },
    }


def mqtt_preview_payloads(config: Dict[str, Any], product: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "discovery_topic": mqtt_discovery_topic(product),
        "discovery_payload": mqtt_discovery_payload(config, product),
        "state_topic": mqtt_state_topic(product),
        "state_payload": mqtt_state_payload(config, product),
        "delete_topic": mqtt_discovery_topic(product),
        "delete_payload": "",
        "availability_topic": mqtt_availability_topic(),
        "availability_payload": "online",
    }


def mqtt_publish(topic: str, payload: Any, settings: Dict[str, Any], retain: bool = True) -> None:
    target = parse_mqtt_target(settings)
    client = mqtt_client_from_settings(settings)
    if target["tls"]:
        client.tls_set()
    client.connect(target["host"], target["port"], target["keepalive"])
    client.loop_start()
    try:
        if isinstance(payload, (dict, list)):
            payload = json.dumps(payload, ensure_ascii=False)
        result = client.publish(topic, payload=payload, qos=0, retain=retain)
        result.wait_for_publish(timeout=10)
        if topic != mqtt_availability_topic():
            availability_result = client.publish(mqtt_availability_topic(), payload="online", qos=0, retain=True)
            availability_result.wait_for_publish(timeout=10)
    finally:
        client.loop_stop()
        client.disconnect()


def mqtt_publish_for_product(config: Dict[str, Any], product: Dict[str, Any], action: str) -> str:
    payloads = mqtt_preview_payloads(config, product)
    settings = config.get("settings") or {}
    if action == "discovery":
        mqtt_publish(payloads["discovery_topic"], payloads["discovery_payload"], settings, retain=True)
        return f"MQTT Discovery gesendet: {product.get('id')}"
    if action == "state":
        mqtt_publish(payloads["state_topic"], payloads["state_payload"], settings, retain=True)
        return f"MQTT Status gesendet: {product.get('id')}"
    if action == "delete":
        mqtt_publish(payloads["delete_topic"], "", settings, retain=True)
        return f"MQTT Discovery gelöscht: {product.get('id')}"
    raise ValueError("Unbekannte MQTT-Aktion.")


def mqtt_status(config: Dict[str, Any], state: Dict[str, Any]) -> Dict[str, str]:
    settings = config.get("settings") or {}
    enabled = str(settings.get("mqtt_enabled", "false")).lower() in {"1", "true", "yes", "on"}
    if not enabled:
        return {"class": "off", "text": "MQTT aus"}
    runtime = state.get("mqtt_runtime") or {}
    if runtime.get("ok"):
        return {"class": "ok", "text": "MQTT aktiv"}
    return {"class": "warn", "text": "MQTT aktiv"}


def product_id_from(article_number: str, name: str) -> str:
    source = name or article_number
    slug = re.sub(r"[^a-z0-9]+", "_", source.lower()).strip("_")
    return slug[:48] or f"artikel_{article_number}"


def display_name_from_search_term(value: str) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        return ""
    if text == text.lower():
        return text[:1].upper() + text[1:]
    return text


def display_name_for_list(value: Any) -> str:
    text = str(value or "")
    if text and text == text.lower():
        return text[:1].upper() + text[1:]
    return text


def short_pdf_label(value: Any) -> str:
    name = Path(str(value or "")).name
    if not name:
        return "Prospekt"
    lower = name.lower()
    known = {
        "aez": "AEZ",
        "rewe": "REWE",
        "edeka": "EDEKA",
        "aldi": "ALDI",
        "lidl": "Lidl",
        "kaufland": "Kaufland",
        "rossmann": "Rossmann",
        "dm": "dm",
        "mueller": "Müller",
        "müller": "Müller",
    }
    for token, label in known.items():
        if token in lower:
            kw_match = re.search(r"\bkw[\s_-]*(\d{1,2})\b", lower, flags=re.I)
            return f"{label} KW{kw_match.group(1)}" if kw_match else label
    stem = re.sub(r"\.pdf$", "", name, flags=re.I)
    stem = re.sub(r"[_-]+", " ", stem).strip()
    return stem[:26] + "..." if len(stem) > 29 else stem or "Prospekt"


def pdf_page_url(value: Any, page: Any) -> str:
    url = str(value or "").strip()
    if not url:
        return ""
    try:
        page_number = int(page or 0)
    except (TypeError, ValueError):
        page_number = 0
    return f"{url}#page={page_number}" if page_number > 0 else url


def product_display_name(product: Dict[str, Any], item_state: Dict[str, Any]) -> str:
    if product.get("pdf_auto_name") == "true":
        return str(item_state.get("title") or product.get("name") or product.get("search_term") or product.get("id") or "")
    return str(product.get("name") or item_state.get("title") or product.get("search_term") or product.get("id") or "")


def unique_product_id(
    products: List[Dict[str, Any]], base_id: str, article_number: str, market_id: str, provider: str
) -> str:
    existing = {product.get("id") for product in products}
    if base_id not in existing:
        return base_id
    candidate = f"{base_id}_{provider}_{market_id}"
    if candidate not in existing:
        return candidate
    index = 2
    while f"{candidate}_{index}" in existing:
        index += 1
    return f"{candidate}_{index}"


def product_state(product_id: str) -> Dict[str, Any]:
    state = load_state()
    return (state.get("products") or {}).get(product_id, {})


def products_with_state(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    state = load_state()
    product_states = state.get("products") or {}
    merged = []
    for product in config.get("products") or []:
        item = dict(product)
        item["state"] = product_states.get(product["id"], {})
        merged.append(item)
    return merged


def latest_change_at(products: List[Dict[str, Any]]) -> Optional[str]:
    values = [product["state"].get("last_changed_at") for product in products if product["state"].get("last_changed_at")]
    return max(values) if values else None


def update_state_for_product(product: Dict[str, Any], result: Optional[Dict[str, Any]], error: Optional[str]) -> None:
    with state_lock:
        state = load_state()
        state.setdefault("products", {})
        previous = state["products"].get(product["id"], {})
        entry = dict(previous)
        entry["last_checked_at"] = now_iso()
        entry["last_error"] = error

        if result:
            old_price = previous.get("price_cents")
            new_price = result.get("price_cents")
            entry.update(result)
            if old_price != new_price:
                entry["previous_price_cents"] = old_price
                entry["last_changed_at"] = now_iso()
            browser_memory = result.get("browser_memory")
            if isinstance(browser_memory, dict):
                state.setdefault("browser_runtime", {})
                state["browser_runtime"][browser_memory.get("provider") or "browser"] = {
                    **browser_memory,
                    "checked_at": entry["last_checked_at"],
                }
        elif error and product.get("provider") == "manual_pdf":
            for stale_key in [
                "price",
                "price_cents",
                "price_text",
                "unit_price",
                "unit_price_text",
                "package_size_text",
                "image_url",
                "matches",
                "match_count",
                "pdf_page",
                "pdf_file_name",
                "pdf_extracted_title",
                "provider_article_number",
            ]:
                entry.pop(stale_key, None)
        state["products"][product["id"]] = entry
        save_state(state)


def save_product_url_state(product: Dict[str, Any], url: str) -> None:
    with state_lock:
        state = load_state()
        state.setdefault("products", {})
        entry = dict(state["products"].get(product["id"], {}))
        entry.update(
            {
                "id": product["id"],
                "name": product.get("name"),
                "title": product.get("name"),
                "article_number": product.get("article_number"),
                "provider": product.get("provider") or "rewe",
                "market_id": product.get("market_id"),
                "url": url,
            }
        )
        state["products"][product["id"]] = entry
        save_state(state)


def refresh_worker(product_id: Optional[str] = None, refresh_kind: str = "manual") -> None:
    global progress
    try:
        config = load_config()
        configure_user_agent((config.get("settings") or {}).get("user_agent"))
        products = config.get("products") or []
        if product_id:
            products = [product for product in products if product["id"] == product_id]
        else:
            products = [product for product in products if product_enabled(product)]
            if refresh_kind == "auto" and not get_auto_refresh_manual_pdfs_enabled(config):
                products = [product for product in products if product_provider(config, product) != "manual_pdf"]
        delay = get_delay_seconds(config)

        with state_lock:
            state = load_state()
            state["last_refresh_started_at"] = now_iso()
            if refresh_kind == "auto":
                state["last_auto_refresh_started_at"] = state["last_refresh_started_at"]
            elif not product_id:
                state["last_manual_refresh_started_at"] = state["last_refresh_started_at"]
            save_state(state)
            progress.update(
                {
                    "running": True,
                    "current_product_id": None,
                    "current_product_name": None,
                    "done": 0,
                    "total": len(products),
                    "started_at": state["last_refresh_started_at"],
                    "finished_at": None,
                    "error": None,
                }
            )

        for index, product in enumerate(products):
            with state_lock:
                progress["current_product_id"] = product["id"]
                progress["current_product_name"] = product.get("name") or product["article_number"]

            try:
                product_store = market_for_product(config, product)
                provider = product_provider(config, product)
                market = resolve_market(provider, product_store)
                result = read_product(provider, product, market, product_store["postal_code"])
                update_state_for_product(product, result, None)
            except Exception as exc:
                update_state_for_product(product, None, str(exc))

            with state_lock:
                progress["done"] = index + 1

            if index + 1 < len(products) and delay > 0:
                time.sleep(delay)

        with state_lock:
            state = load_state()
            state["last_refresh_finished_at"] = now_iso()
            if refresh_kind == "auto":
                state["last_auto_refresh_finished_at"] = state["last_refresh_finished_at"]
                state["last_auto_refresh_at"] = state["last_refresh_finished_at"]
            elif not product_id:
                state["last_manual_refresh_finished_at"] = state["last_refresh_finished_at"]
            save_state(state)
            progress["running"] = False
            progress["finished_at"] = state["last_refresh_finished_at"]
            progress["current_product_id"] = None
            progress["current_product_name"] = None
    except Exception as exc:
        with state_lock:
            progress["running"] = False
            progress["error"] = str(exc)
            progress["finished_at"] = now_iso()


def start_refresh(product_id: Optional[str] = None, refresh_kind: str = "manual") -> bool:
    global refresh_thread
    with state_lock:
        if progress.get("running"):
            return False
        progress.update({"running": True, "done": 0, "total": 0, "error": None})
        refresh_thread = threading.Thread(target=refresh_worker, args=(product_id, refresh_kind), daemon=True)
        refresh_thread.start()
    return True


def refresh_provider_products(config: Dict[str, Any], provider_id: str) -> int:
    configure_user_agent((config.get("settings") or {}).get("user_agent"))
    count = 0
    for product in config.get("products") or []:
        if not product_enabled(product) or product_provider(config, product) != provider_id:
            continue
        try:
            product_store = market_for_product(config, product)
            market = resolve_market(provider_id, product_store)
            result = read_product(provider_id, product, market, product_store.get("postal_code", ""))
            update_state_for_product(product, result, None)
        except Exception as exc:
            update_state_for_product(product, None, str(exc))
        count += 1
    return count


def scheduler_worker() -> None:
    while True:
        try:
            config = load_config()
            if get_auto_refresh_enabled(config):
                interval = get_auto_refresh_interval_seconds(config)
                state = load_state()
                last_value = state.get("last_auto_refresh_at") or state.get("last_auto_refresh_finished_at")
                due = True
                if last_value:
                    try:
                        last = datetime.fromisoformat(str(last_value).replace("Z", "+00:00"))
                        due = (datetime.now(timezone.utc) - last.astimezone(timezone.utc)).total_seconds() >= interval
                    except ValueError:
                        due = True
                if due:
                    start_refresh(refresh_kind="auto")
        except Exception:
            pass
        time.sleep(30)


def ensure_scheduler_started() -> None:
    global scheduler_thread
    with scheduler_lock:
        if scheduler_thread and scheduler_thread.is_alive():
            return
        scheduler_thread = threading.Thread(target=scheduler_worker, daemon=True)
        scheduler_thread.start()


def mqtt_worker() -> None:
    active_signature = ""
    client = None
    while True:
        try:
            config = load_config()
            settings = config.get("settings") or {}
            enabled = str(settings.get("mqtt_enabled", "false")).lower() in {"1", "true", "yes", "on"}
            signature = mqtt_settings_signature(settings)
            if not enabled:
                if client:
                    client.loop_stop()
                    client.disconnect()
                    client = None
                active_signature = ""
                set_mqtt_runtime(False, "MQTT aus")
                time.sleep(15)
                continue

            if client and signature == active_signature:
                time.sleep(15)
                continue

            if client:
                client.loop_stop()
                client.disconnect()
                client = None

            target = parse_mqtt_target(settings)
            new_client = mqtt_client_from_settings(settings)

            def on_connect(client_obj: Any, userdata: Any, flags: Any, reason_code: Any, properties: Any = None) -> None:
                ok = int(reason_code) == 0 if str(reason_code).isdigit() else str(reason_code) in {"Success", "0"}
                if ok:
                    set_mqtt_runtime(True, f"Verbunden mit {target['host']}:{target['port']}", target)
                else:
                    set_mqtt_runtime(False, f"MQTT Verbindungscode {reason_code}", target)

            def on_disconnect(client_obj: Any, userdata: Any, flags: Any, reason_code: Any = None, properties: Any = None) -> None:
                if reason_code in (None, 0):
                    return
                set_mqtt_runtime(False, f"MQTT getrennt: {reason_code}", target)

            new_client.on_connect = on_connect
            new_client.on_disconnect = on_disconnect
            if target["tls"]:
                new_client.tls_set()
            new_client.connect(target["host"], target["port"], target["keepalive"])
            new_client.loop_start()
            client = new_client
            active_signature = signature
        except Exception as exc:
            set_mqtt_runtime(False, f"MQTT Fehler: {exc}")
            try:
                if client:
                    client.loop_stop()
                    client.disconnect()
            except Exception:
                pass
            client = None
            active_signature = ""
            time.sleep(30)


def ensure_mqtt_started() -> None:
    global mqtt_thread
    with mqtt_lock:
        if mqtt_thread and mqtt_thread.is_alive():
            return
        mqtt_thread = threading.Thread(target=mqtt_worker, daemon=True)
        mqtt_thread.start()


@app.before_request
def before_request() -> None:
    ensure_scheduler_started()
    ensure_mqtt_started()


def render_delete_market_dialog(
    market: Optional[Dict[str, Any]],
    products: List[Dict[str, Any]],
    reassignment_options: str,
) -> str:
    if not market:
        return ""
    provider = market_provider(market)
    product_list = "".join(f"<li>{escape(product.get('name') or product.get('id'))}</li>" for product in products)
    affected = (
        f"<div class=\"small\">Betroffene Artikel:<ul>{product_list}</ul></div>"
        if products
        else "<div class=\"small\">Diesem Markt sind aktuell keine Artikel zugeordnet.</div>"
    )
    reassign = (
        "<label><input type=\"radio\" name=\"delete_action\" value=\"reassign\" checked> Artikel einem anderen Markt zuordnen</label>"
        f"<select name=\"target_market_id\">{reassignment_options}</select>"
        if products and reassignment_options
        else ""
    )
    delete_products = (
        "<label><input type=\"radio\" name=\"delete_action\" value=\"delete_products\"> Zugeordnete Artikel mitlöschen</label>"
        if products
        else "<input type=\"hidden\" name=\"delete_action\" value=\"delete_products\">"
    )
    return (
        "<div class=\"error\" style=\"margin-top: 14px\">"
        f"<strong>Markt löschen?</strong><br>{market_address_html(market)}"
        f"{affected}"
        f"<form method=\"post\" action=\"/markets/{escape(str(market.get('market_id')))}/delete\">"
        f"<input type=\"hidden\" name=\"provider\" value=\"{escape(provider)}\">"
        f"{reassign}"
        f"{delete_products}"
        "<div class=\"actions\" style=\"margin-top: 10px\">"
        "<button class=\"danger\" type=\"submit\">Markt löschen</button>"
        "<a class=\"button\" href=\"/?markets_dialog=1\">Abbrechen</a>"
        "</div></form></div>"
    )


def render_product_table(rows: List[str]) -> str:
    return (
        "<table>"
        '<thead><tr><th><button class="sort-button" data-sort="0">Produkt</button></th>'
        '<th><button class="sort-button" data-sort="1">Markt</button></th>'
        '<th><button class="sort-button" data-sort="2" data-type="number">Preis</button></th>'
        '<th>Grundpreis</th>'
        '<th><button class="sort-button" data-sort="4">Geprüft</button></th>'
        '<th><button class="sort-button" data-sort="5">Geändert</button></th>'
        '<th><button class="sort-button" data-sort="6">Status</button></th>'
        "<th>Aktionen</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def render_page(config: Dict[str, Any], state: Dict[str, Any], error: Optional[str] = None) -> str:
    settings = config.get("settings") or {}
    markets = markets_from_config(config)
    all_products = products_with_state(config)
    categories = categories_from_config(config)
    providers = provider_choices()
    theme = current_theme(config)
    id_display_mode = product_id_display_mode(config)
    extra_matches_mode = pdf_extra_matches_display_mode(config)
    extra_matches_open = pdf_extra_matches_expanded(config)
    target_highlight = target_price_highlight_enabled(config)
    target_extra_matches = target_price_extra_matches_enabled(config)
    target_missed_mode = target_price_missed_display_mode(config)
    home_view = default_home_view(config)
    valid_category_ids = {category["id"] for category in categories}
    selected_multi_categories = selected_category_ids_from_args(valid_category_ids)
    selected_category = request.args.get("category") or "all"
    if selected_category != "all" and selected_category not in valid_category_ids:
        selected_category = "all"
    category_filter_ids = selected_multi_categories or ([] if selected_category == "all" else [selected_category])
    selected_shop = request.args.get("shop") or "all"
    search_text = request.args.get("q", "").strip()
    grouped_view = request.args.get("view") == "grouped" or (not request.args and default_home_view(config) == "grouped")
    products = [
        product
        for product in all_products
        if (not category_filter_ids or product_category_id(product) in category_filter_ids)
        and (
            selected_shop == "all"
            or (selected_shop == "all_shops" and provider_kind(product_provider(config, product)) != "prospect")
            or (selected_shop == "all_prospects" and provider_kind(product_provider(config, product)) == "prospect")
            or product_provider(config, product) == selected_shop
        )
        and (
            not search_text
            or search_text.lower()
            in " ".join(
                str(part or "")
                for part in [
                    product.get("name"),
                    product.get("id"),
                    product.get("article_number"),
                    (product.get("state") or {}).get("title"),
                ]
            ).lower()
        )
    ]
    total = sum(int(product["state"].get("price_cents") or 0) for product in products)
    changed_at = latest_change_at(products)
    next_run_value = None
    if get_auto_refresh_enabled(config):
        last_auto_base = state.get("last_auto_refresh_at") or state.get("last_auto_refresh_finished_at")
        next_run_value = add_seconds_iso(last_auto_base, get_auto_refresh_interval_seconds(config))
    mqtt = mqtt_status(config, state)
    interval_text = (
        f"Alle {settings.get('auto_refresh_interval_hours', '6')} Stunden"
        if get_auto_refresh_enabled(config)
        else "Auto-Refresh aus"
    )
    header_meta = (
        '<div class="header-meta">'
        '<div class="header-meta-row">'
        '<span>Letztes automatisches Aktualisieren:</span>'
        f'<strong>{escape(format_datetime_de(state.get("last_auto_refresh_finished_at") or state.get("last_auto_refresh_at")))}</strong>'
        f'<span><span class="status-dot {escape(mqtt["class"])}"></span>{escape(mqtt["text"])}</span>'
        '</div>'
        '<div class="header-meta-row">'
        '<span>Nächstes automatisches Aktualisieren:</span>'
        f'<strong>{escape(format_datetime_de(next_run_value) if next_run_value else "-")}</strong>'
        f'<span>{escape(interval_text)}</span>'
        '</div>'
        '</div>'
    )
    pct = 0
    if progress.get("total"):
        pct = min(100, int((progress.get("done", 0) / progress["total"]) * 100))

    search = state.get("market_search") or {}
    shop_choices = sorted(
        [choice for choice in providers if choice.get("kind") != "prospect" and choice["id"] != "generic"],
        key=lambda item: str(item["label"]).lower(),
    )
    prospect_choices = sorted(
        [choice for choice in providers if choice.get("kind") == "prospect"],
        key=lambda item: str(item["label"]).lower(),
    )
    market_options_groups = []
    for choice in shop_choices:
        if choice["id"] == "generic":
            continue
        provider_markets = [market for market in markets if market_provider(market) == choice["id"]]
        if not provider_uses_markets(choice["id"]):
            provider_markets = virtual_markets(choice["id"])
        if provider_markets:
            options = "".join(
                f'<option value="{escape(choice["id"])}::{escape(str(market.get("market_id")))}">'
                f'{escape(market_label(market))}</option>'
                for market in provider_markets
            )
            market_options_groups.append(f'<optgroup label="{escape(choice["label"])}">{options}</optgroup>')
    market_options = "".join(market_options_groups)
    pdf_provider_options = "".join(
        f'<option value="{escape(choice["id"])}">{escape(choice["label"])}</option>'
        for choice in prospect_choices
    )
    provider_select_options = "".join(
        f'<option value="{escape(choice["id"])}" '
        f'{"selected" if search.get("provider", "rewe") == choice["id"] else ""}>{escape(choice["label"])}</option>'
        for choice in shop_choices
        if choice.get("markets")
    )
    show_market_dialog = request.args.get("market_dialog") == "1"
    show_market_results = request.args.get("market_results") == "1"
    show_markets_dialog = request.args.get("markets_dialog") == "1"
    show_categories_dialog = request.args.get("categories_dialog") == "1"
    show_generic_dialog = request.args.get("generic_dialog") == "1"
    show_add_product_dialog = request.args.get("add_product") == "1"
    show_add_pdf_dialog = request.args.get("add_pdf") == "1"
    delete_market_id = request.args.get("delete_market")
    delete_market_provider = request.args.get("provider") or None
    edit_category_id = request.args.get("edit_category") or request.args.get("rename_category")
    delete_category_id = request.args.get("delete_category")
    delete_market_item = market_by_id(config, delete_market_id or "", delete_market_provider) if delete_market_id else None
    delete_market_products = [
        product
        for product in config.get("products", [])
        if delete_market_id
        and product.get("market_id") == delete_market_id
        and (not delete_market_provider or product_provider(config, product) == delete_market_provider)
    ]
    reassignment_options = "".join(
        f'<option value="{escape(market_provider(market))}::{escape(str(market.get("market_id")))}">'
        f'{escape(provider_label(market_provider(market)))} - {escape(market_label(market))}</option>'
        for market in markets
        if not delete_market_id
        or str(market.get("market_id")) != str(delete_market_id)
        or market_provider(market) != delete_market_provider
    )
    notice = pop_notice(state)
    category_filter_options = f'<option value="all" {"selected" if selected_multi_categories or selected_category == "all" else ""}>Alle Kategorien</option>' + "".join(
        f'<option value="{escape(category["id"])}" {"selected" if not selected_multi_categories and selected_category == category["id"] else ""}>'
        f'{escape(category.get("name") or category["id"])}</option>'
        for category in categories
    )
    shop_filter_options = (
        f'<option value="all" {"selected" if selected_shop == "all" else ""}>Alle Shops &amp; Prospekte</option>'
        f'<option value="all_shops" {"selected" if selected_shop == "all_shops" else ""}>Alle Shops</option>'
        + (
            '<optgroup label="Shops">'
            + "".join(
                f'<option value="{escape(choice["id"])}" {"selected" if selected_shop == choice["id"] else ""}>'
                f'{escape(choice["label"])}</option>'
                for choice in shop_choices
            )
            + "</optgroup>"
        )
        + f'<option value="all_prospects" {"selected" if selected_shop == "all_prospects" else ""}>Alle Prospekte</option>'
        + (
            '<optgroup label="Prospekte">'
            + "".join(
                f'<option value="{escape(choice["id"])}" {"selected" if selected_shop == choice["id"] else ""}>'
                f'{escape(choice["label"])}</option>'
                for choice in prospect_choices
            )
            + "</optgroup>"
        )
    )
    quick_category_links = "".join(
        category_chip_html(category, "/?category=" + urllib.parse.quote(category["id"]))
        for category in categories
        if category_quick_enabled(category)
    )
    quick_category_html = f'<div class="quick-cats">{quick_category_links}</div>' if quick_category_links else ""
    multi_category_enabled = multi_category_filter_enabled(config)
    multi_filter_link_params: Dict[str, Any] = {"categories_filter": "1"}
    if selected_multi_categories:
        multi_filter_link_params["categories"] = selected_multi_categories
    elif selected_category != "all":
        multi_filter_link_params["categories"] = [selected_category]
    if selected_shop != "all":
        multi_filter_link_params["shop"] = selected_shop
    if search_text:
        multi_filter_link_params["q"] = search_text
    if grouped_view:
        multi_filter_link_params["view"] = "grouped"
    multi_filter_href = "/?" + urllib.parse.urlencode(multi_filter_link_params, doseq=True)
    multi_button_active_class = " is-active" if selected_multi_categories else ""
    category_multi_button = (
        f'<a class="button icon-only{multi_button_active_class}" href="{escape(multi_filter_href)}" title="Mehrere Kategorien auswählen" '
        f'aria-label="Mehrere Kategorien auswählen">{icon("list")}</a>'
        if multi_category_enabled
        else ""
    )
    categories_checked_in_dialog = selected_multi_categories or category_filter_ids
    category_dialog_hidden_inputs = ""
    if selected_shop != "all":
        category_dialog_hidden_inputs += f'<input type="hidden" name="shop" value="{escape(selected_shop)}">'
    if search_text:
        category_dialog_hidden_inputs += f'<input type="hidden" name="q" value="{escape(search_text)}">'
    if grouped_view:
        category_dialog_hidden_inputs += '<input type="hidden" name="view" value="grouped">'
    category_choice_items = "".join(
        '<label class="category-choice">'
        f'<span class="category-choice-main">{category_chip_html(category)}</span>'
        f'<input type="checkbox" name="categories" value="{escape(category["id"])}" {"checked" if category["id"] in categories_checked_in_dialog else ""}>'
        '</label>'
        for category in categories
    )
    multi_category_dialog = (
        '<div class="dialog-backdrop" open><section class="dialog">'
        '<div class="dialog-head"><div><h2>Kategorien auswählen</h2>'
        '<div class="small">Mehrere Kategorien markieren und übernehmen. Eine normale Kategorieauswahl oder „Alle“ setzt diese Auswahl zurück.</div>'
        '</div><a class="button icon-only" href="/" aria-label="Schließen">×</a></div>'
        '<form method="get" action="/">'
        f'{category_dialog_hidden_inputs}'
        f'<div class="category-choice-list">{category_choice_items}</div>'
        '<div class="actions" style="margin-top: 14px">'
        '<button class="primary" type="submit">Übernehmen</button>'
        '<a class="button" href="/?view=all">Alle</a>'
        '<a class="button" href="/">Abbrechen</a>'
        '</div></form></section></div>'
        if multi_category_enabled and request.args.get("categories_filter") == "1"
        else ""
    )
    base_filter_params = {}
    if selected_multi_categories:
        for category_id in selected_multi_categories:
            base_filter_params.setdefault("categories", []).append(category_id)
    elif selected_category != "all":
        base_filter_params["category"] = selected_category
    if selected_shop != "all":
        base_filter_params["shop"] = selected_shop
    if search_text:
        base_filter_params["q"] = search_text
    group_active_class = " is-active" if grouped_view else ""
    all_active_class = " is-active" if not grouped_view and not selected_multi_categories and selected_category == "all" and selected_shop == "all" and not search_text else ""
    grouped_hidden = '<input type="hidden" name="view" value="grouped">' if grouped_view else ""
    group_toggle_control = (
        f'<button class="{group_active_class.strip()}" type="submit" name="view" value="grouped">{icon("list")} Gruppieren</button>'
    )
    product_category_options = "".join(
        f'<option value="{escape(category["id"])}">{escape(category.get("name") or category["id"])}</option>'
        for category in categories
    )
    category_rows = "".join(
        category_admin_row_html(
            category,
            sum(1 for product in all_products if product_category_id(product) == category["id"]),
        )
        for category in categories
    )
    edit_category = next((category for category in categories if category["id"] == edit_category_id), None)
    delete_category = next((category for category in categories if category["id"] == delete_category_id), None)
    delete_category_products = [
        product for product in all_products if delete_category and product_category_id(product) == delete_category["id"]
    ]
    delete_category_options = "".join(
        f'<option value="{escape(category["id"])}" {"selected" if category["id"] == DEFAULT_CATEGORY_ID else ""}>'
        f'{escape(category.get("name") or category["id"])}</option>'
        for category in categories
        if not delete_category or category["id"] != delete_category["id"]
    )
    edit_category_dialog = ""
    if edit_category:
        edit_color = category_color(edit_category) or "#d0001f"
        edit_color_text = category_color(edit_category)
        edit_category_dialog = (
            '<div class="dialog-backdrop" open><section class="dialog">'
            '<div class="dialog-head">'
            f'<div><h2>Kategorie bearbeiten</h2><div class="small">{escape(edit_category.get("name") or edit_category["id"])}</div></div>'
            '<a class="button dialog-close" href="/?categories_dialog=1" aria-label="Schließen">×</a>'
            '</div>'
            f'<form method="post" action="/categories/{escape(edit_category["id"])}/rename">'
            f'<div class="field"><label>Name</label><input name="name" value="{escape(edit_category.get("name") or "")}" required></div>'
            '<div class="field"><label>Farbe optional</label><div class="color-row">'
            f'<input type="color" data-color-picker value="{escape(edit_color)}">'
            f'<input name="color_text" data-color-text value="{escape(edit_color_text)}" placeholder="#d0001f" pattern="#?[0-9a-fA-F]{{6}}">'
            '</div></div>'
            '<label class="toggle-line"><input type="checkbox" name="clear_color" value="true"> Farbe auf Standard zurücksetzen</label>'
            f'<label class="toggle-line"><input type="checkbox" name="quick_cat" value="true" {"checked" if category_quick_enabled(edit_category) else ""}> Quick Cat</label>'
            '<div class="actions" style="margin-top: 12px"><button class="primary" type="submit">Speichern</button>'
            '<a class="button" href="/?categories_dialog=1">Abbrechen</a></div></form></section></div>'
        )
    delete_category_dialog = (
        '<div class="dialog-backdrop" open><section class="dialog">'
        '<div class="dialog-head">'
        f'<div><h2>Kategorie löschen?</h2><div class="small">{escape(delete_category.get("name") or delete_category["id"])} · {len(delete_category_products)} Artikel betroffen</div></div>'
        '<a class="button dialog-close" href="/?categories_dialog=1" aria-label="Schließen">×</a>'
        '</div>'
        f'<form method="post" action="/categories/{escape(delete_category["id"])}/delete">'
        '<label class="toggle-line"><input type="radio" name="delete_action" value="move" checked> Artikel in diese Kategorie verschieben</label>'
        f'<div class="field"><select name="target_category_id">{delete_category_options}</select></div>'
        '<label class="toggle-line"><input type="radio" name="delete_action" value="delete_products"> Artikel dieser Kategorie löschen</label>'
        '<div class="actions" style="margin-top: 12px"><button class="danger" type="submit">Kategorie löschen</button>'
        '<a class="button" href="/?categories_dialog=1">Abbrechen</a></div></form></section></div>'
        if delete_category
        else ""
    )
    generic_analysis = (state.get("generic_analysis") or {}) if show_generic_dialog else {}
    generic_candidates = generic_analysis.get("candidates") or []
    generic_mode = str(generic_analysis.get("requested_mode") or generic_analysis.get("source") or "http")
    generic_is_browser = generic_mode == "browser"
    generic_mode_label = "Erweitert (Playwright)" if generic_is_browser else "Einfach (HTTP)"
    generic_other_label = "Einfach testen" if generic_is_browser else "Erweitert testen"
    generic_other_value = "false" if generic_is_browser else "true"
    generic_mode_note = (
        "Erweitert nutzt Playwright/Chromium und braucht deutlich mehr Arbeitsspeicher. "
        "Nutze Einfach, wenn dort der richtige Preis gefunden wird."
        if generic_is_browser
        else "Einfach lädt die Seite per HTTP ohne Browser und ist sparsamer. "
        "Falls Preise fehlen oder JavaScript nötig ist, nutze Erweitert mit Playwright/Chromium."
    )
    generic_switch_form = (
        '<form method="post" action="/generic/analyze">'
        f'<input type="hidden" name="product_url" value="{escape(str(generic_analysis.get("url") or ""))}">'
        f'<input type="hidden" name="category_id" value="{escape(str(generic_analysis.get("category_id") or DEFAULT_CATEGORY_ID))}">'
        f'<input type="hidden" name="id" value="{escape(str(generic_analysis.get("requested_id") or ""))}">'
        f'<input type="hidden" name="prefer_browser" value="{generic_other_value}">'
        f'<button type="submit">{icon("refresh")} {generic_other_label}</button>'
        '</form>'
        if generic_analysis.get("url")
        else ""
    )
    generic_candidate_rows = []
    for candidate in generic_candidates:
        generic_candidate_rows.append(
            '<div class="market-row">'
            '<div>'
            f'<strong>{escape(str(candidate.get("price_text") or ""))}</strong><br>'
            f'<span class="small">{escape(str(candidate.get("raw_text") or ""))}</span><br>'
            f'<span class="small">{escape(str(candidate.get("context") or ""))}</span>'
            '</div>'
            '<form method="post" action="/generic/products">'
            f'<input type="hidden" name="candidate_index" value="{escape(str(candidate.get("index", 0)))}">'
            f'<input type="hidden" name="category_id" value="{escape(str(generic_analysis.get("category_id") or DEFAULT_CATEGORY_ID))}">'
            f'<input type="hidden" name="id" value="{escape(str(generic_analysis.get("requested_id") or ""))}">'
            f'<button type="submit">{icon("plus")} Überwachen</button>'
            '</form>'
            '</div>'
        )
    visual_selection_html = ""
    screenshot = generic_analysis.get("screenshot") if isinstance(generic_analysis, dict) else None
    visual_candidates = generic_analysis.get("visual_candidates") if isinstance(generic_analysis, dict) else None
    if generic_is_browser and isinstance(screenshot, dict) and screenshot.get("data_url"):
        screenshot_width = max(1, int(screenshot.get("width") or 1280))
        screenshot_height = max(1, int(screenshot.get("height") or 720))
        visual_marker_forms = []
        for item in visual_candidates or []:
            left = max(0, min(100, (float(item.get("x") or 0) / screenshot_width) * 100))
            top = max(0, min(100, (float(item.get("y") or 0) / screenshot_height) * 100))
            visual_marker_forms.append(
                f'<form class="visual-price-marker" style="left: {left:.3f}%; top: {top:.3f}%;" method="post" action="/generic/products">'
                f'<input type="hidden" name="candidate_index" value="{escape(str(item.get("candidate_index", 0)))}">'
                f'<input type="hidden" name="category_id" value="{escape(str(generic_analysis.get("category_id") or DEFAULT_CATEGORY_ID))}">'
                f'<input type="hidden" name="id" value="{escape(str(generic_analysis.get("requested_id") or ""))}">'
                f'<button type="submit" title="Diesen Preis überwachen">{escape(str(item.get("price_text") or item.get("raw_text") or "Preis"))}</button>'
                '</form>'
            )
        visual_note = (
            "Grafische Auswahl: klicke direkt auf den passenden markierten Preis."
            if visual_marker_forms
            else "Screenshot der geladenen Seite. Es wurden keine sichtbaren Preis-Markierungen gefunden."
        )
        visual_selection_html = (
            f'<div class="small" style="margin-top: 12px">{escape(visual_note)}</div>'
            '<div class="visual-price-map">'
            '<div class="visual-price-layer">'
            f'<img src="{escape(str(screenshot.get("data_url") or ""))}" alt="Screenshot der Webseite">'
            f'{"".join(visual_marker_forms)}'
            '</div></div>'
        )
    generic_dialog_html = (
        f'<div class="dialog-backdrop"{" open" if show_generic_dialog else ""}>'
        '<section class="dialog">'
        '<div class="dialog-head">'
        '<div><h2>Preis auswählen</h2>'
        f'<div class="small">{escape(str(generic_analysis.get("title") or generic_analysis.get("url") or "Beliebige Webseite"))}</div></div>'
        '<a class="button dialog-close" href="/" aria-label="Schließen">×</a>'
        '</div>'
        f'<div class="small">Methode: {escape(generic_mode_label)}. {escape(generic_mode_note)}</div>'
        f'<div class="actions" style="margin-top: 10px">{generic_switch_form}</div>'
        '<div class="small" style="margin-top: 10px">Wenn mehrere Preise vorkommen, wähle genau den Preis, den du überwachen willst.</div>'
        f'{visual_selection_html}'
        + (
            f'<div class="market-list" style="margin-top: 12px">{"".join(generic_candidate_rows)}</div>'
            if generic_candidate_rows
            else '<div class="error">Keine Preis-Kandidaten gefunden.</div>'
        )
        + '</section></div>'
    )
    pdf_analysis = (state.get("pdf_analysis") or {}) if show_add_pdf_dialog else {}
    pdf_result = pdf_analysis.get("result") if isinstance(pdf_analysis.get("result"), dict) else None
    pdf_preview_html = ""
    if pdf_analysis:
        pdf_confirm_category = str(pdf_analysis.get("category_id") or DEFAULT_CATEGORY_ID)
        if not any(category["id"] == pdf_confirm_category for category in categories):
            pdf_confirm_category = DEFAULT_CATEGORY_ID
        hidden = (
            f'<input type="hidden" name="provider" value="{escape(str(pdf_analysis.get("provider") or ""))}">'
            f'<input type="hidden" name="category_id" value="{escape(pdf_confirm_category)}" data-pdf-confirm-category>'
            f'<input type="hidden" name="search_term" value="{escape(str(pdf_analysis.get("search_term") or ""))}">'
            f'<input type="hidden" name="id" value="{escape(str(pdf_analysis.get("requested_id") or ""))}">'
        )
        if pdf_result:
            suggested_name = str(pdf_analysis.get("display_name") or pdf_result.get("title") or pdf_analysis.get("search_term") or "")
            pdf_matches = pdf_result.get("matches") if isinstance(pdf_result.get("matches"), list) else []
            pdf_match_count = int(pdf_result.get("match_count") or len(pdf_matches) or 1)
            image = (
                f'<img class="image-preview" src="{escape(str(pdf_result.get("image_url")))}" alt="" loading="lazy" referrerpolicy="no-referrer">'
                if pdf_result.get("image_url")
                else ""
            )
            match_cards = []
            for match in pdf_matches[:8]:
                match_image = (
                    f'<img src="{escape(str(match.get("image_url")))}" alt="" loading="lazy" referrerpolicy="no-referrer">'
                    if match.get("image_url")
                    else ""
                )
                match_cards.append(
                    '<div class="pdf-match-card">'
                    f'<strong>{escape(str(match.get("price_text") or ""))}</strong><br>'
                    f'<span class="small">Seite {escape(str(match.get("pdf_page") or "-"))}'
                    f' · {escape(display_name_for_list(str(match.get("title") or "")))}</span>'
                    f'{match_image}'
                    '</div>'
                )
            matches_note = ""
            if pdf_match_count > 1:
                hidden_count = max(0, pdf_match_count - len(match_cards))
                hidden_note = (
                    f'<div class="small">Weitere {hidden_count} Treffer werden beim Aktualisieren gespeichert.</div>'
                    if hidden_count
                    else ""
                )
                matches_note = (
                    '<div class="notice">Mehrere Treffer gefunden. Der erste Treffer wird als Hauptpreis angezeigt; '
                    'alle Treffer bleiben am Suchwort gespeichert. Wenn du nur einen bestimmten Artikel willst, erweitere das Suchwort.</div>'
                    f'<div class="pdf-match-grid">{"".join(match_cards)}</div>'
                    f'{hidden_note}'
                )
            quality_note = (
                '<div class="small warn">Hinweis: Der Treffer wurde über unscharfe PDF-Texterkennung gefunden. Bitte Bild und Preis prüfen.</div>'
                if pdf_result.get("pdf_match_quality") == "fuzzy"
                else ""
            )
            pdf_preview_html = (
                '<div class="choice-panel">'
                '<h3>Treffer prüfen</h3>'
                f'<div><strong>{escape(str(pdf_result.get("title") or pdf_analysis.get("search_term") or ""))}</strong></div>'
                f'<div class="price">{escape(str(pdf_result.get("price_text") or ""))}</div>'
                f'<div class="small">Seite {escape(str(pdf_result.get("pdf_page") or "-"))}'
                f' · Suchwort: {escape(str(pdf_analysis.get("search_term") or ""))}</div>'
                f'{quality_note}{image}{matches_note}'
                '<form method="post" action="/pdf-products/confirm">'
                f'{hidden}<input type="hidden" name="found" value="true">'
                f'<div class="field"><label>Artikelname optional</label><input name="display_name" placeholder="{escape(display_name_for_list(suggested_name))}"></div>'
                '<div class="small">Wenn leer, wird bei jeder Aktualisierung der erkannte Name aus dem Prospekt angezeigt. Wenn du etwas einträgst, bleibt dieser Artikelname fest gesetzt. Das Suchwort bleibt separat gespeichert und wird bei späteren Prospekten weiter gesucht.</div>'
                '<div class="actions" style="justify-content: flex-end; margin-top: 10px">'
                '<button class="primary" type="submit">Treffer übernehmen</button>'
                '</div></form></div>'
            )
        else:
            pdf_preview_html = (
                '<div class="error">Im aktuellen Prospekt wurde kein Treffer gefunden.</div>'
                '<form method="post" action="/pdf-products/confirm">'
                f'{hidden}<input type="hidden" name="found" value="false">'
                f'<div class="field"><label>Artikelname optional</label><input name="display_name" placeholder="{escape(display_name_for_list(str(pdf_analysis.get("search_term") or "")))}"></div>'
                '<div class="small">Wenn leer, wird zunächst das Suchwort als Name angezeigt. Sobald ein späterer Prospekt einen Treffer liefert, kann der erkannte Name angezeigt werden. Das Suchwort bleibt separat gespeichert.</div>'
                '<div class="actions" style="justify-content: flex-end; margin-top: 10px">'
                '<button type="submit">Suchwort trotzdem anlegen</button>'
                '</div></form>'
            )
    pdf_form_provider = str(pdf_analysis.get("provider") or (prospect_choices[0]["id"] if prospect_choices else ""))
    pdf_form_category = str(pdf_analysis.get("category_id") or DEFAULT_CATEGORY_ID)
    pdf_form_term = str(pdf_analysis.get("search_term") or "")
    pdf_form_id = str(pdf_analysis.get("requested_id") or "")
    pdf_provider_options_selected = "".join(
        f'<option value="{escape(choice["id"])}" {"selected" if pdf_form_provider == choice["id"] else ""}>{escape(choice["label"])}</option>'
        for choice in prospect_choices
    )
    pdf_category_options_selected = "".join(
        f'<option value="{escape(category["id"])}" {"selected" if pdf_form_category == category["id"] else ""}>'
        f'{escape(category.get("name") or category["id"])}</option>'
        for category in categories
    )

    rows = []
    rows_by_category: Dict[str, List[str]] = {category["id"]: [] for category in categories}
    categories_by_id = category_lookup(config)
    image_dialogs = []
    move_dialogs = []
    delete_product_dialogs = []
    for product in products:
        item_state = product["state"]
        last_error = item_state.get("last_error")
        is_enabled = product_enabled(product)
        status = (
            "Deaktiviert"
            if not is_enabled
            else ("Fehler" if last_error else ("OK" if item_state.get("last_checked_at") else "Noch nicht geladen"))
        )
        status_html = escape(status)
        if last_error:
            status_html += f'<br><span class="small">{escape(str(last_error))}</span>'
        provider = product_provider(config, product)
        product_market = market_for_selection(provider, product.get("market_id", ""), markets) or {}
        product_provider_label = provider_label(provider)
        product_market_display = product_market_html(provider, product, product_market)
        product_market_sort = product_market_sort_value(provider, product, product_market)
        category_id = product_category_id(product)
        category = categories_by_id.get(category_id, {"id": DEFAULT_CATEGORY_ID, "name": DEFAULT_CATEGORY_NAME})
        price_extra = ""
        if item_state.get("old_price_cents") and item_state.get("old_price_cents") != item_state.get("price_cents"):
            price_extra = f"<br><span class=\"small\">statt {escape(format_cents(item_state.get('old_price_cents')))}</span>"
        target_cents = product_target_price_cents(product)
        target_reached = product_below_target_price(product)
        target_badge_html = target_price_badge_html(target_cents, target_reached, target_missed_mode)
        target_badge = (
            f"<br>{target_badge_html}"
            if target_badge_html
            else ""
        )
        target_row_class = " class=\"is-target-price\"" if target_highlight and target_reached else ""
        match_count = int(item_state.get("match_count") or 0)
        image_url = item_state.get("image_url")
        image_dialog_id = f"image-{re.sub(r'[^a-zA-Z0-9_-]+', '-', product.get('id', 'produkt'))}"
        thumbnail = (
            f'<button class="product-thumb-button" type="button" data-dialog-open="{escape(image_dialog_id)}" title="Bild groß anzeigen" aria-label="Bild groß anzeigen">'
            f'<img class="product-thumb" src="{escape(str(image_url))}" alt="" loading="lazy" referrerpolicy="no-referrer"></button>'
            if image_url
            else f'<span class="product-thumb product-thumb-placeholder" aria-hidden="true">{icon("image")}</span>'
        )
        product_name = escape(display_name_for_list(product_display_name(product, item_state)))
        visible_extra_match_count = (
            max(
                0,
                len(
                    [
                        match
                        for match in (item_state.get("matches") or [])
                        if not (
                            match.get("provider_article_number") == item_state.get("provider_article_number")
                            or (
                                match.get("pdf_page") == item_state.get("pdf_page")
                                and match.get("price_cents") == item_state.get("price_cents")
                                and match.get("title") == item_state.get("title")
                            )
                        )
                    ]
                ),
            )
            if extra_matches_mode != "off"
            else 0
        )
        if image_url:
            last_extra_dialog_id = f"{image_dialog_id}-match-{visible_extra_match_count}"
            previous_image_button = (
                f'<button class="image-nav prev" type="button" data-dialog-open="{escape(last_extra_dialog_id)}" aria-label="Vorheriges Bild">‹</button>'
                if visible_extra_match_count
                else ""
            )
            next_image_button = (
                f'<button class="image-nav next" type="button" data-dialog-open="{escape(image_dialog_id)}-match-1" aria-label="Nächstes Bild">›</button>'
                if visible_extra_match_count
                else ""
            )
            image_dialogs.append(
                f'<div class="dialog-backdrop" id="{escape(image_dialog_id)}">'
                '<section class="dialog">'
                '<div class="dialog-head">'
                f'<div><h2>{product_name}</h2><div class="small">Produktbild</div></div>'
                '<button class="dialog-close" type="button" data-dialog-close aria-label="Schließen">×</button>'
                '</div>'
                '<div class="image-preview-wrap">'
                f'{previous_image_button}<img class="image-preview" src="{escape(str(image_url))}" alt="{product_name}" loading="lazy" referrerpolicy="no-referrer">{next_image_button}'
                '</div>'
                '</section></div>'
            )
        move_options = "".join(
            f'<option value="{escape(category["id"])}" {"selected" if category["id"] == category_id else ""}>'
            f'{escape(category.get("name") or category["id"])}</option>'
            for category in categories
        )
        move_dialog_id = f"move-{re.sub(r'[^a-zA-Z0-9_-]+', '-', product.get('id', 'produkt'))}"
        delete_product_dialog_id = f"delete-{re.sub(r'[^a-zA-Z0-9_-]+', '-', product.get('id', 'produkt'))}"
        product_mqtt_notice = pop_product_mqtt_notice(state, str(product.get("id", "")))
        product_mqtt_notice_html = f'<div class="notice" style="margin-top: 10px">{escape(product_mqtt_notice)}</div>' if product_mqtt_notice else ""
        target_price_value = format_price_input(target_cents)
        product_open_url = (
            pdf_page_url(item_state.get("url"), item_state.get("pdf_page"))
            if provider_kind(provider) == "prospect"
            else str(item_state.get("url") or product.get("product_url") or "")
        )
        product_url_html = (
            '<div class="dialog-product-url">'
            f'<code title="{escape(product_open_url)}">{escape(product_open_url)}</code>'
            f'<a class="button icon-only" href="{escape(product_open_url)}" target="_blank" rel="noopener noreferrer" title="Link öffnen" aria-label="Link öffnen">{icon("shop")}</a>'
            '</div>'
            if product_open_url
            else ""
        )
        move_dialogs.append(
            f'<div class="dialog-backdrop" id="{escape(move_dialog_id)}"><section class="dialog">'
            '<div class="dialog-head">'
            f'<div><h2>Artikel bearbeiten</h2><div class="small">{product_name}</div>{product_url_html}</div>'
            '<button class="dialog-close" type="button" data-dialog-close aria-label="Schließen">×</button>'
            '</div>'
            f'<form method="post" action="/products/{escape(product.get("id", ""))}/category">'
            f'<div class="field"><label>Kategorie</label><select name="category_id">{move_options}</select></div>'
            '<div class="field" style="margin-top: 10px">'
            '<label>Wunschpreis optional</label>'
            f'<input name="target_price" inputmode="decimal" value="{escape(target_price_value)}" placeholder="1,23">'
            '<div class="small">Leer lassen, um keinen Wunschpreis zu verwenden. 1,23 und 1.23 sind beide möglich.</div>'
            '</div>'
            f'<label class="toggle-line" style="margin-top: 10px"><input type="checkbox" name="enabled" value="true" {"checked" if is_enabled else ""}> Artikel aktiv</label>'
            '<div class="small">Inaktive Artikel werden bei „Alle aktualisieren“ und beim Auto-Refresh übersprungen.</div>'
            '<div class="actions" style="margin-top: 12px"><button class="primary" type="submit">Speichern</button>'
            '<button class="button" type="button" data-dialog-close>Abbrechen</button></div></form>'
            '<div class="settings-card" style="margin-top: 12px">'
            '<h3>MQTT / Home Assistant</h3>'
            '<div class="small">Manueller Test für diesen Artikel. Automatisch wird noch nichts beim Aktualisieren gesendet.</div>'
            f'{product_mqtt_notice_html}'
            '<div class="actions" style="margin-top: 10px">'
            f'<form method="post" action="/products/{escape(product.get("id", ""))}/mqtt/discovery?return=dialog"><button type="submit">{icon("settings")} Discovery senden</button></form>'
            f'<form method="post" action="/products/{escape(product.get("id", ""))}/mqtt/state?return=dialog"><button type="submit">{icon("refresh")} Status senden</button></form>'
            f'<form method="post" action="/products/{escape(product.get("id", ""))}/mqtt/delete?return=dialog"><button class="danger" type="submit">{icon("trash")} Aus HA löschen</button></form>'
            '</div></div></section></div>'
        )
        delete_product_dialogs.append(
            f'<div class="dialog-backdrop" id="{escape(delete_product_dialog_id)}"><section class="dialog">'
            '<div class="dialog-head">'
            f'<div><h2>Produkt löschen?</h2><div class="small">{product_name}</div></div>'
            '<button class="dialog-close" type="button" data-dialog-close aria-label="Schließen">×</button>'
            '</div>'
            '<div class="small">Der Artikel wird aus der Überwachung entfernt. Gespeicherte Zustandsdaten zu diesem Artikel werden ebenfalls gelöscht.</div>'
            f'<form method="post" action="/products/{escape(product.get("id", ""))}/delete">'
            '<div class="actions" style="margin-top: 12px"><button class="danger" type="submit">Produkt löschen</button>'
            '<button class="button" type="button" data-dialog-close>Abbrechen</button></div></form></section></div>'
        )
        enabled_badge = "" if is_enabled else '<br><span class="category-chip">Deaktiviert</span>'
        product_meta = f"Artikel {escape(product.get('article_number', ''))}"
        if (
            extra_matches_mode != "off"
            and product_provider(config, product)
            and provider_kind(product_provider(config, product)) == "prospect"
            and match_count > 1
        ):
            product_meta += f" · {match_count} Treffer"
        product_id = escape(product.get("id", ""))
        if id_display_mode == "show":
            product_meta += f" · Kennung {product_id}"
        elif id_display_mode == "interactive":
            product_meta += (
                ' · <span class="id-reveal">'
                f'<button class="id-label" type="button" data-id-toggle>Kennung</button><span class="id-tooltip">{product_id}</span></span>'
            )
        row_html = (
            f"<tr id=\"product-{escape(product.get('id', ''))}\"{target_row_class}>"
            f"<td data-label=\"Produkt\" data-sort-value=\"{escape(product.get('name') or product.get('id'))}\"><div class=\"product-cell\">{thumbnail}<div><strong>{product_name}</strong><br>"
            f"<div class=\"small\">{product_meta}</div>"
            f"{category_chip_html(category, '/?category=' + category_id)}{enabled_badge}</div></div></td>"
            f"<td data-label=\"Markt\" data-sort-value=\"{escape(product_provider_label + ' ' + product_market_sort)}\"><span class=\"small\"><strong>{escape(product_provider_label)}</strong>{product_market_display}</span></td>"
            f"<td data-label=\"Preis\" class=\"price\" data-sort-value=\"{int(item_state.get('price_cents') or -1)}\">{escape(format_cents(item_state.get('price_cents')))}{price_extra}{target_badge}</td>"
            f"<td data-label=\"Grundpreis\">{unit_price_html(item_state)}</td>"
            f"<td data-label=\"Geprüft\" data-sort-value=\"{escape(str(item_state.get('last_checked_at') or ''))}\"><span class=\"small\">{escape(format_datetime_de(item_state.get('last_checked_at')))}</span></td>"
            f"<td data-label=\"Geändert\" data-sort-value=\"{escape(str(item_state.get('last_changed_at') or ''))}\"><span class=\"small\">{escape(format_datetime_de(item_state.get('last_changed_at')))}</span></td>"
            f"<td data-label=\"Status\" data-sort-value=\"{escape(status + ' ' + str(last_error or ''))}\" class=\"{'warn' if last_error or not is_enabled else 'ok'}\">{status_html}</td>"
            "<td data-label=\"Aktionen\"><div class=\"row-actions action-grid\">"
            f"<form method=\"post\" action=\"/products/{escape(product.get('id', ''))}/refresh\"><button class=\"icon-only\" title=\"Einzeln aktualisieren\" aria-label=\"Einzeln aktualisieren\">{icon('refresh')}</button></form>"
            f"<button class=\"icon-only\" type=\"button\" data-dialog-open=\"{escape(move_dialog_id)}\" title=\"Artikel bearbeiten\" aria-label=\"Artikel bearbeiten\">{icon('settings')}</button>"
            + (
                f"<a class=\"button icon-only\" href=\"{escape(pdf_page_url(item_state.get('url'), item_state.get('pdf_page')) if provider_kind(provider) == 'prospect' else str(item_state.get('url')))}\" target=\"_blank\" rel=\"noopener noreferrer\" title=\"Öffnen\" aria-label=\"Öffnen\">{icon('shop')}</a>"
                if item_state.get("url")
                else ""
            )
            + f"<button class=\"danger icon-only\" type=\"button\" data-dialog-open=\"{escape(delete_product_dialog_id)}\" title=\"Löschen\" aria-label=\"Löschen\">{icon('trash')}</button>"
            + "</div>"
            + "</td>"
            "</tr>"
        )
        rows.append(row_html)
        rows_by_category.setdefault(category_id, []).append(row_html)
        if product_provider(config, product) and provider_kind(product_provider(config, product)) == "prospect" and match_count > 1:
            match_items = []
            extra_matches = [
                match
                for match in (item_state.get("matches") or [])
                if not (
                    match.get("provider_article_number") == item_state.get("provider_article_number")
                    or (
                        match.get("pdf_page") == item_state.get("pdf_page")
                        and match.get("price_cents") == item_state.get("price_cents")
                        and match.get("title") == item_state.get("title")
                    )
                )
            ]
            displayed_extra_matches = extra_matches
            for match_index, match in enumerate(displayed_extra_matches, start=1):
                match_dialog_id = f"image-{re.sub(r'[^a-zA-Z0-9_-]+', '-', product.get('id', 'produkt'))}-match-{match_index}"
                match_pdf_url = pdf_page_url(match.get("url") or item_state.get("url"), match.get("pdf_page"))
                match_pdf_label = short_pdf_label(match.get("pdf_file_name") or item_state.get("pdf_file_name"))
                match_pdf_link = (
                    f'<a class="button icon-small" href="{escape(match_pdf_url)}" target="_blank" rel="noopener noreferrer" title="PDF auf Seite {escape(str(match.get("pdf_page") or "-"))} öffnen" aria-label="PDF öffnen">{icon("pdf")}</a>'
                    if match_pdf_url
                    else ""
                )
                match_image = (
                    f'<button class="product-thumb-button" type="button" data-dialog-open="{escape(match_dialog_id)}" title="Bild groß anzeigen" aria-label="Bild groß anzeigen">'
                    f'<img src="{escape(str(match.get("image_url")))}" alt="" loading="lazy" referrerpolicy="no-referrer"></button>'
                    if match.get("image_url")
                    else '<span class="product-thumb product-thumb-placeholder" aria-hidden="true"></span>'
                )
                match_target_badge = ""
                if target_extra_matches and target_cents is not None:
                    try:
                        match_target_reached = int(match.get("price_cents")) <= target_cents
                    except (TypeError, ValueError):
                        match_target_reached = False
                    match_target_badge = target_price_badge_html(
                        target_cents, match_target_reached, target_missed_mode, compact=True
                    )
                    if match_target_badge:
                        match_target_badge = f"{match_target_badge}<br>"
                if match.get("image_url"):
                    previous_dialog_id = image_dialog_id if match_index == 1 else f"{image_dialog_id}-match-{match_index - 1}"
                    next_dialog_id = (
                        f"{image_dialog_id}-match-{match_index + 1}"
                        if match_index < len(displayed_extra_matches)
                        else image_dialog_id
                    )
                    image_dialogs.append(
                        f'<div class="dialog-backdrop" id="{escape(match_dialog_id)}">'
                        '<section class="dialog">'
                        '<div class="dialog-head">'
                        f'<div><h2>{escape(display_name_for_list(str(match.get("title") or product_name)))}</h2><div class="small">Prospekt-Treffer · Seite {escape(str(match.get("pdf_page") or "-"))}</div></div>'
                        '<button class="dialog-close" type="button" data-dialog-close aria-label="Schließen">×</button>'
                        '</div>'
                        '<div class="image-preview-wrap">'
                        f'<button class="image-nav prev" type="button" data-dialog-open="{escape(previous_dialog_id)}" aria-label="Vorheriges Bild">‹</button>'
                        f'<img class="image-preview" src="{escape(str(match.get("image_url")))}" alt="" loading="lazy" referrerpolicy="no-referrer">'
                        f'<button class="image-nav next" type="button" data-dialog-open="{escape(next_dialog_id)}" aria-label="Nächstes Bild">›</button>'
                        '</div>'
                        '</section></div>'
                    )
                match_items.append(
                    '<div class="pdf-match-mini">'
                    f'{match_image}'
                    '<div>'
                    f'<strong>{escape(str(match.get("price_text") or ""))}</strong><br>'
                    f'{match_target_badge}'
                    f'<span class="small">Seite {escape(str(match.get("pdf_page") or "-"))} · {escape(match_pdf_label)}<br>'
                    f'<span class="pdf-match-title">{escape(display_name_for_list(str(match.get("title") or "")))}</span></span>'
                    f'<div class="actions" style="margin-top: 6px">{match_pdf_link}</div>'
                    '</div></div>'
                )
            if match_items:
                extra_count = len(extra_matches)
                hidden_matches = max(0, extra_count - len(match_items))
                hidden_matches_text = (
                    f'<div class="small">Weitere {hidden_matches} Treffer sind im Zustand/API gespeichert.</div>'
                    if hidden_matches
                    else ""
                )
                search_term_label = escape(
                    display_name_for_list(
                        str(product.get("search_term") or item_state.get("pdf_search_term") or product.get("name") or "dieses Suchwort")
                    )
                )
                details_open = " open" if extra_matches_open else ""
                strip_id = f"matches-{re.sub(r'[^a-zA-Z0-9_-]+', '-', product.get('id', 'produkt'))}"
                strip_class = "pdf-match-strip is-slider" if extra_matches_mode == "slider" else "pdf-match-strip"
                strip_html = f'<div id="{escape(strip_id)}" class="{strip_class}">{"".join(match_items)}</div>'
                if extra_matches_mode == "slider":
                    strip_html = (
                        '<div class="pdf-match-slider">'
                        f'<button class="pdf-match-scroll" type="button" data-scroll-strip="#{escape(strip_id)}" data-scroll-direction="prev" aria-label="Zusatztreffer nach links">‹</button>'
                        f'{strip_html}'
                        f'<button class="pdf-match-scroll" type="button" data-scroll-strip="#{escape(strip_id)}" data-scroll-direction="next" aria-label="Zusatztreffer nach rechts">›</button>'
                        '</div>'
                    )
                match_row_html = (
                    '<tr class="pdf-match-detail-row">'
                    '<td colspan="8">'
                    f'<details class="pdf-match-details"{details_open}>'
                    f'<summary><span class="pdf-match-summary">{extra_count} zusätzliche Prospekt-Treffer für {search_term_label}</span></summary>'
                    f'{strip_html}'
                    f'{hidden_matches_text}'
                    '</details>'
                    '</td></tr>'
                )
                rows.append(match_row_html)
                rows_by_category.setdefault(category_id, []).append(match_row_html)

    table_html = ""
    if grouped_view:
        sections = []
        for category in categories:
            category_rows_for_table = rows_by_category.get(category["id"], [])
            if not category_rows_for_table:
                continue
            sections.append(
                '<section class="category-section">'
                f'<h2>{escape(category.get("name") or category["id"])}</h2>'
                f'{render_product_table(category_rows_for_table)}'
                '</section>'
            )
        table_html = "".join(sections) or '<div class="panel small">Keine Artikel gefunden.</div>'
    else:
        table_html = render_product_table(rows)

    market_rows = []
    for market in markets:
        provider = market_provider(market)
        market_detail = (
            '<span class="address-lines"><span>Online</span></span>'
            if str(market.get("market_id") or "") == "online"
            else market_address_html(market)
        )
        market_rows.append(
            '<div class="market-row">'
            f'<div><strong>{escape(provider_label(provider))} - {escape(market.get("market_name") or "Markt")}</strong><br>'
            f'<span class="small">{market_detail}<span>Markt {escape(str(market.get("market_id")))}</span></span></div>'
            f'<a class="button danger icon-only" href="/?markets_dialog=1&delete_market={escape(str(market.get("market_id")))}&provider={escape(provider)}" title="Markt löschen" aria-label="Markt löschen">{icon("trash")}</a>'
            '</div>'
        )

    search_rows = []
    for market in (search.get("results") or []) if show_market_dialog and show_market_results else []:
        provider = market_provider(market)
        hidden = "".join(
            f'<input type="hidden" name="{escape(key)}" value="{escape(str(value))}">'
            for key, value in market.items()
        )
        search_rows.append(
            '<div class="market-row">'
            f'<div><strong>{escape(provider_label(provider))} - {escape(market.get("market_name") or "Markt")}</strong><br>'
            f'<span class="small">{market_address_html(market)}<span>Markt {escape(str(market.get("market_id")))}</span></span></div>'
            f'<form method="post" action="/markets">{hidden}<button>{icon("plus")} Speichern</button></form>'
            '</div>'
        )

    error_html = f'<div class="error">{escape(error)}</div>' if error else ""
    notice_html = f'<div class="notice">{escape(notice)}</div>' if notice else ""
    active_products = [product for product in products if product_enabled(product)]
    progress_hidden = "" if progress.get("running") else " hidden"
    return f"""<!doctype html>
<html lang="de">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(APP_NAME)}</title>
  <style>{STYLE}</style>
</head>
<body data-theme="{escape(theme)}">
  <main>
    <header>
      <div>
        <h1>{escape(APP_NAME)}</h1>
        <div class="meta">{header_meta}</div>
      </div>
      <nav class="actions">
        <a class="button primary" href="/">{icon('home')} Home</a>
        <form method="post" action="/theme"><button title="Darstellung wechseln" aria-label="Darstellung wechseln">{icon('moon' if theme == 'light' else 'sun')} {'Dark' if theme == 'light' else 'Light'}</button></form>
        <a class="button" href="/settings">{icon('settings')} Settings</a>
      </nav>
    </header>
    {error_html}
    {notice_html}
    <section class="summary" data-summary>
      <div class="metric"><div class="metric-head"><div><span>Märkte</span><strong>{len(markets)}</strong></div><div class="row-actions"><a class="button icon-small" href="/?market_dialog=1" title="Markt hinzufügen" aria-label="Markt hinzufügen">{icon('plus')}</a><a class="button icon-small" href="/?markets_dialog=1" title="Märkte verwalten" aria-label="Märkte verwalten">{icon('settings')}</a></div></div></div>
      <div class="metric"><div class="metric-head"><div><span>Kategorien</span><strong>{len(categories)}</strong></div><div class="row-actions"><a class="button icon-small" href="/?categories_dialog=1" title="Kategorie hinzufügen" aria-label="Kategorie hinzufügen">{icon('plus')}</a><a class="button icon-small" href="/?categories_dialog=1" title="Kategorien verwalten" aria-label="Kategorien verwalten">{icon('settings')}</a></div></div></div>
      <div class="metric"><div class="metric-head"><div><span>Produkte</span><strong>{len(active_products)} / {len(all_products)}</strong></div><div class="row-actions"><a class="button icon-small" href="/?add_product=1" title="Produkt hinzufügen" aria-label="Produkt hinzufügen">{icon('plus')}</a><a class="button icon-small" href="/?add_pdf=1" title="PDF-Suchwort hinzufügen" aria-label="PDF-Suchwort hinzufügen">{icon('pdf')}</a></div></div></div>
      <div class="metric"><span>Summe</span><strong>{escape(format_cents(total))}</strong></div>
      <div class="metric"><span>Zuletzt aktualisiert</span><strong>{escape(format_datetime_de(state.get("last_refresh_finished_at")))}</strong></div>
    </section>
    <section class="panel" data-progress-box{progress_hidden}>
      <h2>Aktualisierung</h2>
      <div class="small" data-progress-text>{escape(str(progress.get("current_product_name") or "Wartet"))}</div>
      <div class="progress-line"><div data-progress-bar style="--pct: {pct}%"></div></div>
      <div class="small">Wartezeit zwischen einzelnen Shop-Abfragen: {escape(str(settings.get("refresh_delay_seconds", "5")))} Sekunden</div>
    </section>
    <section class="panel">
      <div class="panel-title-row"><h2>Filter</h2>{quick_category_html}</div>
      <form class="grid filter-grid" method="get" action="/">
        {grouped_hidden}
        <div class="category-filter-control{' has-multi' if multi_category_enabled else ''}">
          {category_multi_button}
          <div class="field"><label>Kategorieauswahl</label><select name="category" onchange="this.form.submit()">{category_filter_options}</select></div>
        </div>
        <div class="field"><label>Shop</label><select name="shop" onchange="this.form.submit()">{shop_filter_options}</select></div>
        <div class="field"><label>Suchwort</label><input data-live-search name="q" value="{escape(search_text)}" placeholder="Produkt, Artikelnummer oder Kennung"></div>
        <a class="button{all_active_class}" href="/?view=all">{icon('list')} Alle</a>
        {group_toggle_control}
      </form>
    </section>
    <section data-results>
      {table_html}
      <div class="table-actions">
        <form class="refresh-box" method="post" action="/refresh">
          <button class="primary">{icon('refresh')} Alle aktualisieren</button>
          <span class="small">Letzte manuelle Aktualisierung: {escape(format_datetime_de(state.get("last_manual_refresh_finished_at")))}</span>
        </form>
      </div>
    </section>
    <footer class="app-footer"><span>{escape(APP_NAME)}</span><span>v{escape(APP_VERSION)}</span></footer>
  </main>
  <div class="dialog-backdrop"{' open' if show_market_dialog else ''}>
    <section class="dialog">
      <div class="dialog-head">
        <div>
          <h2>Markt hinzufügen</h2>
          <div class="small">Nur für Anbieter, bei denen ein Standort oder Markt konfiguriert werden muss. Anbieter wie Müller, MediaMarkt, ALDI Süd oder Rossmann sind beim Artikel hinzufügen automatisch als Online-Anbieter verfügbar.</div>
        </div>
        <a class="button dialog-close" href="/" aria-label="Schließen">×</a>
      </div>
      <form class="grid market-grid" method="post" action="/markets/search">
        <div class="field"><label>Anbieter</label><select name="provider" required>{provider_select_options}</select></div>
        <div class="field"><label>PLZ</label><input name="postal_code" required inputmode="numeric" autocomplete="off" placeholder="10115" value=""></div>
        <div class="field"><label>Suchtext optional</label><input name="query" placeholder="Straße, Ort oder Marktname" value="{escape(str(search.get('query') or '')) if show_market_results else ''}"></div>
        <button type="submit">{icon('search')} Suchen</button>
      </form>
      {('<div class="market-list">' + ''.join(search_rows) + '</div>') if search_rows else '<div class="small">Noch keine Suche ausgeführt.</div>'}
    </section>
  </div>
  <div class="dialog-backdrop"{' open' if show_markets_dialog else ''}>
    <section class="dialog">
      <div class="dialog-head">
        <div>
          <h2>Gespeicherte Märkte</h2>
          <div class="small">Hier verwaltest du die Märkte, die beim Hinzufügen von Artikeln auswählbar sind.</div>
        </div>
        <a class="button dialog-close" href="/" aria-label="Schließen">×</a>
      </div>
      {('<div class="market-list">' + ''.join(market_rows) + '</div>') if market_rows else '<div class="small">Noch kein Markt gespeichert.</div>'}
      {render_delete_market_dialog(delete_market_item, delete_market_products, reassignment_options)}
    </section>
  </div>
  <div class="dialog-backdrop"{' open' if show_categories_dialog else ''}>
    <section class="dialog">
      <div class="dialog-head">
        <div>
          <h2>Kategorien</h2>
          <div class="small">Kategorien erstellen, umbenennen, löschen und Artikel später filtern.</div>
        </div>
        <a class="button dialog-close" href="/" aria-label="Schließen">×</a>
      </div>
      <form class="grid" method="post" action="/categories">
        <div class="field"><label>Neue Kategorie</label><input name="name" required placeholder="Drogerie"></div>
        <div class="field"><label>Farbe optional</label><div class="color-row">
          <input type="color" data-color-picker value="#d0001f">
          <input name="color_text" data-color-text placeholder="#d0001f" pattern="#?[0-9a-fA-F]{{6}}">
        </div></div>
        <label class="toggle-line"><input type="checkbox" name="quick_cat" value="true"> Quick Cat</label>
        <button class="primary" type="submit">{icon('plus')} Erstellen</button>
      </form>
      <div class="market-list" style="margin-top: 12px">{category_rows}</div>
    </section>
  </div>
  {multi_category_dialog}
  <div class="dialog-backdrop"{' open' if show_add_product_dialog else ''}>
    <section class="dialog">
      <div class="dialog-head">
        <div>
          <h2>Produkt hinzufügen</h2>
          <div class="small">Shop-URL einfügen, Kategorie wählen und danach entscheiden, ob ein eingebauter Anbieter oder die freie Webseiten-Erkennung genutzt werden soll.</div>
        </div>
        <a class="button dialog-close" href="/" aria-label="Schließen">×</a>
      </div>
      <form class="add-product-form" method="post" action="/products" data-add-product-form>
        <div class="add-product-shared">
          <div class="field"><label>Kategorie</label><select name="category_id">{product_category_options}</select></div>
          <div class="field"><label>Produkt URL</label><input name="product_url" data-product-url required placeholder="https://www.rossmann.de/de/..."></div>
        </div>
        <details class="optional-details">
          <summary>Technische Kennung optional</summary>
          <div class="field"><input name="id" placeholder="pepsi_zero_125"></div>
          <div class="small">Stabiler interner Name für API/Home Assistant. Wenn leer, wird er automatisch erzeugt.</div>
        </details>
        <div class="add-product-paths">
          <div class="choice-panel">
            <h3>Shop / Anbieter</h3>
            <div class="shop-detect-status is-neutral" data-shop-status>URL einfügen, dann wird ein passender Anbieter vorgeschlagen.</div>
            <div class="field"><label>Anbieter oder Markt</label><select name="market_id" data-provider-select required>{market_options}</select></div>
            <div class="small">Für REWE, Müller, MediaMarkt, ALDI Süd, Rossmann und weitere eingebaute Shop-Anbieter.</div>
            <div class="choice-panel-actions"><button class="primary" type="submit" formaction="/products">{icon('plus')} Hinzufügen</button></div>
          </div>
          <div class="choice-panel">
            <h3>Beliebige Webseite</h3>
            <div class="shop-detect-status is-neutral" data-generic-status>Für nicht erkannte Shops oder Spezialseiten mit mehreren Preisen.</div>
            <div class="field"><label>Methode</label><select name="prefer_browser"><option value="false">Einfach (HTTP)</option><option value="true">Erweitert (Playwright/Chromium)</option></select></div>
            <div class="small">Einfach ist sparsamer. Erweitert nutzt einen echten Browser, findet mehr JavaScript-Preise, braucht aber mehr Arbeitsspeicher.</div>
            <div class="choice-panel-actions"><button class="primary" type="submit" formaction="/generic/analyze">{icon('search')} Preise suchen</button></div>
          </div>
        </div>
      </form>
    </section>
  </div>
  <div class="dialog-backdrop"{' open' if show_add_pdf_dialog else ''}>
    <section class="dialog">
      <div class="dialog-head">
        <div>
          <h2>PDF-Suchwort hinzufügen</h2>
          <div class="small">Prospekt auswählen und ein Suchwort eintragen. Die App sucht im aktuellen PDF nach dem Begriff, ermittelt Preis und erzeugt einen Prospekt-Ausschnitt.</div>
        </div>
        <a class="button dialog-close" href="/" aria-label="Schließen">×</a>
      </div>
      <form class="add-product-form" method="post" action="/pdf-products/analyze">
        <div class="add-product-shared">
          <div class="field"><label>Kategorie</label><select name="category_id" data-pdf-category-select>{pdf_category_options_selected}</select></div>
          <div class="field"><label>Prospekt</label><select name="provider" required>{pdf_provider_options_selected}</select></div>
        </div>
        <div class="field"><label>Suchwort</label><input name="search_term" required placeholder="purina gourmet" value="{escape(pdf_form_term)}"></div>
        <details class="optional-details">
          <summary>Technische Kennung optional</summary>
          <div class="field"><input name="id" placeholder="aez_purina_gourmet" value="{escape(pdf_form_id)}"></div>
          <div class="small">Stabiler interner Name für API/Home Assistant. Wenn leer, wird er automatisch erzeugt.</div>
        </details>
        <div class="small">Tipp: Wenn ein Prospektartikel unter mehreren Begriffen gefunden werden soll, lege mehrere Suchwörter an. Gleiche Treffer werden beim Hinzufügen zusammengefasst.</div>
        <div class="actions" style="justify-content: flex-end"><button class="primary" type="submit">{icon('search')} Treffer prüfen</button></div>
      </form>
      {pdf_preview_html}
    </section>
  </div>
  {edit_category_dialog}
  {delete_category_dialog}
  {generic_dialog_html}
  {''.join(move_dialogs)}
  {''.join(delete_product_dialogs)}
  {''.join(image_dialogs)}
  <script>{SCRIPT}</script>
</body>
</html>"""


def render_install_info_card() -> str:
    app_dir = Path(__file__).resolve().parent
    local_items = [
        (".venv", app_dir / ".venv", "isolierte Python-Umgebung nur für diese App"),
        (".playwright-browsers", app_dir / ".playwright-browsers", "app-lokaler Chromium/Headless-Shell/FFmpeg-Download für Playwright"),
        (".browser-cache", app_dir / ".browser-cache", "Browserprofile und Cache-Daten für Playwright-Reader"),
        (".pdf-cache", app_dir / ".pdf-cache", "PDF-Zwischenspeicher für Prospekt-Auswertung"),
        ("generated", app_dir / "generated", "generierte Produkt- und Prospektbilder"),
        ("manual_pdfs", app_dir / "manual_pdfs", "hochgeladene Prospekte"),
        ("tmp", app_dir / "tmp", "temporäre App-Dateien"),
        ("config.yaml", app_dir / "config.yaml", "lokale Konfiguration und Artikelliste"),
        ("state.json", app_dir / "state.json", "Laufzeitstatus, letzte Preise und Fortschritt"),
    ]
    python_packages = [
        "Flask",
        "gunicorn",
        "paho-mqtt",
        "playwright",
        "pdfplumber",
        "Pillow",
        "Jinja2/Werkzeug",
        "pdfminer/pypdfium2",
    ]
    apt_packages = [
        "git",
        "nginx",
        "rsync",
        "python3",
        "python3-venv",
        "python3-pip",
        "python3-dev",
        "build-essential",
        "libjpeg-dev",
        "zlib1g-dev",
        "libopenjp2-7",
        "libtiff6",
        "poppler-utils",
    ]
    playwright_deps = [
        "libasound2",
        "libatk-bridge2.0-0",
        "libatk1.0-0",
        "libatspi2.0-0",
        "libcairo2",
        "libcups2",
        "libdbus-1-3",
        "libdrm2",
        "libgbm1",
        "libglib2.0-0",
        "libnspr4",
        "libnss3",
        "libpango-1.0-0",
        "libx11-6",
        "libxcb1",
        "libxcomposite1",
        "libxdamage1",
        "libxext6",
        "libxfixes3",
        "libxkbcommon0",
        "libxrandr2",
        "xvfb",
        "diverse Schriftpakete",
    ]
    system_files = [
        ("/etc/systemd/system/preisermittlung.service", "systemd-Service für diese App"),
        ("/etc/nginx/sites-available/preisermittlung.conf", "nginx-Site-Konfiguration"),
        ("/etc/nginx/sites-enabled/preisermittlung.conf", "nginx-Site-Aktivierung/Symlink"),
    ]

    local_rows = "".join(
        "<tr>"
        f"<td><code>{escape(name)}</code></td>"
        f"<td><code>{escape(str(path))}</code></td>"
        f"<td>{escape(description)}</td>"
        "</tr>"
        for name, path, description in local_items
    )
    system_rows = "".join(
        "<tr>"
        f"<td><code>{escape(path)}</code></td>"
        f"<td>{escape(description)}</td>"
        "</tr>"
        for path, description in system_files
    )

    return f"""
        <div class="settings-card settings-card-full">
          <h3>Installationsinfo</h3>
          <div class="small">Diese Übersicht zeigt, welche Dateien, Dienste und Pakete die Debian-Installation verwendet. App-lokale Dateien liegen gesammelt im Installationsverzeichnis; Systempakete können auch von anderen Programmen stammen oder dort weiter gebraucht werden.</div>
          <div class="table-wrap" style="margin-top: 12px">
            <table class="info-table">
              <thead><tr><th colspan="3">App-lokal unter <code>{escape(str(app_dir))}</code></th></tr></thead>
              <tbody>{local_rows}</tbody>
            </table>
          </div>
          <div class="table-wrap" style="margin-top: 12px">
            <table class="info-table">
              <thead><tr><th>Bereich</th><th>Installiert</th><th>Hinweis</th></tr></thead>
              <tbody>
                <tr>
                  <td>Python venv</td>
                  <td>{escape(", ".join(python_packages))}</td>
                  <td>Nur innerhalb von <code>{escape(str(app_dir / ".venv"))}</code> nutzbar.</td>
                </tr>
                <tr>
                  <td>apt Basispakete</td>
                  <td>{escape(", ".join(apt_packages))}</td>
                  <td>Diese Pakete sind systemweit verfügbar. Einige davon waren eventuell schon vorher installiert und können auch von anderen Anwendungen benötigt werden.</td>
                </tr>
                <tr>
                  <td>Playwright Systembibliotheken</td>
                  <td>{escape(", ".join(playwright_deps))}</td>
                  <td>Systemweite Linux-Bibliotheken für Chromium. Einige können schon vorher installiert gewesen sein und auch von Browsern, Desktop-Komponenten oder anderen Diensten benötigt werden. Deshalb nicht pauschal entfernen.</td>
                </tr>
              </tbody>
            </table>
          </div>
          <div class="table-wrap" style="margin-top: 12px">
            <table class="info-table">
              <thead><tr><th>Systemdatei oder Dienst</th><th>Funktion</th></tr></thead>
              <tbody>{system_rows}</tbody>
            </table>
          </div>
          <div class="soft-panel" style="margin-top: 12px">
            <h4>Deinstallation</h4>
            <div class="small">Das Script <code>scripts/uninstall_debian.sh</code> stoppt den Dienst, entfernt die systemd- und nginx-Konfiguration dieser App und fragt anschließend, ob das komplette App-Verzeichnis gelöscht werden soll.</div>
            <pre><code>cd {escape(str(app_dir))}
./scripts/uninstall_debian.sh</code></pre>
            <div class="small">Der Befehl muss als root laufen. Wenn du nicht per <code>su -</code> als root angemeldet bist, nutze stattdessen <code>sudo ./scripts/uninstall_debian.sh</code>.</div>
            <div class="small">Wenn das App-Verzeichnis gelöscht wird, verschwinden auch <code>.venv</code>, <code>.playwright-browsers</code>, Cache-Dateien, hochgeladene PDFs, generierte Bilder und die lokale Konfiguration. Systempakete werden dabei nicht automatisch entfernt, weil sie schon vorher installiert gewesen sein oder von anderen Anwendungen genutzt werden können. Nicht mehr benötigte apt-Pakete kann man danach bei Bedarf manuell mit <code>sudo apt autoremove</code> prüfen.</div>
          </div>
        </div>
    """


def render_settings_page(config: Dict[str, Any], state: Dict[str, Any], error: Optional[str] = None) -> str:
    cleanup_old_backup_imports()
    settings = config.get("settings") or {}
    theme = current_theme(config)
    auto_enabled = get_auto_refresh_enabled(config)
    mqtt_enabled = str(settings.get("mqtt_enabled", "false")).lower() in {"1", "true", "yes", "on"}
    api_enabled = get_api_enabled(config)
    id_display_mode = product_id_display_mode(config)
    extra_matches_mode = pdf_extra_matches_display_mode(config)
    extra_matches_open = pdf_extra_matches_expanded(config)
    home_view = default_home_view(config)
    error_html = f'<div class="error">{escape(error)}</div>' if error else ""
    notice = pop_notice(state)
    notice_html = f'<div class="notice">{escape(notice)}</div>' if notice else ""
    mqtt_test_notice = state.pop("mqtt_test_notice", None) or {}
    if mqtt_test_notice:
        save_state(state)
    mqtt_test_notice_html = (
        f'<div class="notice" style="margin-top: 10px">{escape(str(mqtt_test_notice.get("message") or ""))}</div>'
        if mqtt_test_notice.get("message")
        else ""
    )
    user_agent = settings_value(config, "user_agent", "")
    mqtt_client_id = settings_value(config, "mqtt_client_id", default_mqtt_client_id())
    browser_runtime = state.get("browser_runtime") or {}
    mqtt_products = products_with_state(config)
    selected_mqtt_product_id = request.args.get("mqtt_product") or (mqtt_products[0].get("id") if mqtt_products else "")
    selected_mqtt_product = next((product for product in mqtt_products if product.get("id") == selected_mqtt_product_id), None)
    mqtt_product_options = "".join(
        f'<option value="{escape(str(product.get("id")))}" {"selected" if product.get("id") == selected_mqtt_product_id else ""}>'
        f'{escape(product_display_name(product, product.get("state") or {}))} · {escape(provider_label(product_provider(config, product)))}</option>'
        for product in mqtt_products
    )
    mqtt_preview = mqtt_preview_payloads(config, selected_mqtt_product) if selected_mqtt_product else {}
    mqtt_preview_discovery = json.dumps(
        {"discovery_topic": mqtt_preview.get("discovery_topic"), "discovery_payload": mqtt_preview.get("discovery_payload")},
        ensure_ascii=False,
        indent=2,
    )
    mqtt_preview_state = json.dumps(
        {"state_topic": mqtt_preview.get("state_topic"), "state_payload": mqtt_preview.get("state_payload")},
        ensure_ascii=False,
        indent=2,
    )
    mqtt_preview_delete = json.dumps(
        {"delete_topic": mqtt_preview.get("delete_topic"), "delete_payload": mqtt_preview.get("delete_payload")},
        ensure_ascii=False,
        indent=2,
    )
    manual_pdf_rows = "".join(
        '<div class="market-row">'
        f'<div><strong>{escape(info["name"])}</strong><br>'
        f'<span class="small">{escape(info["size_text"])}</span></div>'
        f'<form method="post" action="/manual-pdfs/{escape(info["name"])}/delete" data-pdf-processing="PDF wird gelöscht und vorhandene Suchwörter werden neu geprüft..." onsubmit="document.querySelectorAll(\'[data-upload-status-global]\').forEach(s=>{{s.hidden=false;s.textContent=\'PDF wird gelöscht und vorhandene Suchwörter werden neu geprüft...\';}});window.scrollTo({{top:0,behavior:\'smooth\'}});">'
        f'<button class="danger" type="submit">{icon("trash")} Löschen</button></form>'
        '</div>'
        for info in manual_pdf_reader.pdf_infos()
    ) or '<div class="small">Noch keine manuellen PDFs hochgeladen.</div>'
    browser_cache_rows = "".join(
        (
            lambda runtime: (
        '<div class="market-row">'
        f'<div><strong>{escape(info["label"])}</strong><br>'
        f'<span class="small">Cache: {escape(info["size_text"])}<br>'
        f'Letzte Chromium-Spitze: {escape(runtime.get("peak_text") or "-")}'
        f'{(" am " + escape(format_datetime_de(runtime.get("checked_at")))) if runtime.get("checked_at") else ""}<br>'
        f'{escape(info["path"])}</span></div>'
        f'<form method="post" action="/settings/browser-cache/{escape(info["provider"])}/clear">'
        '<button type="submit">Cache leeren</button></form>'
        '</div>'
            )
        )(browser_runtime.get(info["provider"]) or {})
        for info in browser_cache_infos()
    )
    settings_categories = categories_from_config(config)
    products_for_info = config.get("products", []) or []
    category_info_rows = "".join(
        '<tr>'
        f'<td><input type="checkbox" data-category-id-select value="{escape(category["id"])}"></td>'
        f'<td>{category_chip_html(category)}</td>'
        f'<td><code>{escape(category["id"])}</code></td>'
        f'<td>{sum(1 for product in products_for_info if product_category_id(product) == category["id"])}</td>'
        '</tr>'
        for category in settings_categories
    )
    install_info_html = render_install_info_card()
    backup_import = state.get("backup_import") or {}
    backup_import_info = backup_import.get("info") or {}
    backup_pdf_count = len(backup_import_info.get("pdfs") or [])
    backup_import_html = ""
    if backup_import.get("token") and backup_has_components(backup_import_info):
        backup_parts = []
        if backup_import_info.get("config"):
            backup_parts.append("config.yaml")
        if backup_import_info.get("state"):
            backup_parts.append("state.json")
        if backup_pdf_count:
            backup_parts.append(f"{backup_pdf_count} hochgeladene PDF-Datei(en)")
        backup_import_html = f"""
          <div class="settings-card settings-card-full">
            <h3>Backup wiederherstellen</h3>
            <div class="warning">Achtung: Ausgewählte Bereiche werden mit dem Inhalt der ZIP-Datei überschrieben.</div>
            <div class="small" style="margin-top: 8px">Datei: <strong>{escape(str(backup_import.get("filename") or "Backup.zip"))}</strong><br>Erkannt: {escape(", ".join(backup_parts))}</div>
            <form method="post" action="/backup/import/confirm" style="margin-top: 12px" data-backup-restore-form>
              <input type="hidden" name="token" value="{escape(str(backup_import.get("token")))}">
              <div class="settings-grid">
                <div class="settings-card">
                  <label class="toggle-line"><input type="checkbox" name="restore_config" value="true" {'checked' if backup_import_info.get("config") else ''} {'disabled' if not backup_import_info.get("config") else ''}> Konfiguration wiederherstellen</label>
                  <div class="small">Überschreibt <code>config.yaml</code>, also Einstellungen, Märkte, Kategorien und Artikelliste.</div>
                </div>
                <div class="settings-card">
                  <label class="toggle-line"><input type="checkbox" name="restore_state" value="true" {'checked' if backup_import_info.get("state") else ''} {'disabled' if not backup_import_info.get("state") else ''}> Artikel- und Statusliste wiederherstellen</label>
                  <div class="small">Überschreibt <code>state.json</code>, also letzte Preise, Zeitpunkte, Status und Fortschrittsdaten.</div>
                </div>
                <div class="settings-card settings-card-full">
                  <label class="toggle-line"><input type="checkbox" name="restore_pdfs" value="true" {'checked' if backup_pdf_count else ''} {'disabled' if not backup_pdf_count else ''}> Hochgeladene PDFs wiederherstellen</label>
                  <div class="small">Ersetzt die vorhandenen PDF-Dateien im Ordner <code>manual_pdfs</code> durch die PDFs aus dem Backup.</div>
                </div>
              </div>
              <div class="notice" data-restore-status hidden style="margin-top: 10px">Backup wird wiederhergestellt. PDFs und Suchwörter werden verarbeitet...</div>
              <div class="actions settings-actions">
                <button class="danger" type="submit" onclick="showBusyOverlay('Backup wird wiederhergestellt. PDFs und Suchwörter werden verarbeitet...')">{icon('refresh')} Ausgewählte Bereiche wiederherstellen</button>
                <a class="button" href="/backup/import/cancel">Abbrechen</a>
              </div>
            </form>
          </div>
        """
    return f"""<!doctype html>
<html lang="de">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Settings · {escape(APP_NAME)}</title>
  <style>{STYLE}</style>
</head>
<body data-theme="{escape(theme)}">
  <main>
    <header>
      <div>
        <h1>Settings</h1>
        <div class="meta">Abfragen, Startseite, JSON-API und MQTT</div>
      </div>
      <nav class="actions">
        <a class="button primary" href="/">{icon('home')} Home</a>
        <form method="post" action="/theme"><button title="Darstellung wechseln" aria-label="Darstellung wechseln">{icon('moon' if theme == 'light' else 'sun')} {'Dark' if theme == 'light' else 'Light'}</button></form>
      </nav>
    </header>
    {error_html}
    {notice_html}
    <div class="notice" data-upload-status-global hidden>PDF wird hochgeladen und vorhandene Suchwörter werden geprüft...</div>
    <div class="busy-overlay" data-busy-overlay hidden>
      <div class="busy-box">
        <strong>Bitte warten</strong>
        <div class="small" data-busy-overlay-text>Vorgang läuft...</div>
      </div>
    </div>
    <nav class="settings-tabs" aria-label="Settings Bereiche">
      <button class="settings-tab" type="button" data-settings-tab="info">Info</button>
      <button class="settings-tab" type="button" data-settings-tab="home">Startseite</button>
      <button class="settings-tab" type="button" data-settings-tab="queries">Abfragen</button>
      <button class="settings-tab" type="button" data-settings-tab="pdfs">Manuelle PDFs</button>
      <button class="settings-tab" type="button" data-settings-tab="api">JSON-API</button>
      <button class="settings-tab" type="button" data-settings-tab="browser">Browser</button>
      <button class="settings-tab" type="button" data-settings-tab="mqtt">MQTT</button>
      <button class="settings-tab" type="button" data-settings-tab="backup">Backup</button>
      <button class="settings-tab" type="button" data-settings-tab="updates">Update</button>
    </nav>
    <section class="panel" data-settings-panel="info">
      <h2>Info</h2>
      <div class="settings-grid align-start">
        <div class="settings-card settings-card-full">
          <h3>Kategorien</h3>
          <div class="small">Hier stehen Name und ID aller Kategorien. Die IDs kannst du für Home Assistant oder spätere Kartenfilter verwenden.</div>
          <div class="table-wrap" style="margin-top: 12px">
            <table class="info-table">
              <thead><tr><th></th><th>Kategorie</th><th>ID</th><th>Artikel</th></tr></thead>
              <tbody>{category_info_rows}</tbody>
            </table>
          </div>
        </div>
        <div class="settings-card settings-card-full">
          <h3>Ausgewählte Kategorie-IDs</h3>
          <div class="small">Kategorien links markieren. Die Liste wird automatisch erzeugt und kann später direkt in HA genutzt werden.</div>
          <textarea class="category-id-list" data-category-id-output readonly placeholder="Noch keine Kategorie markiert"></textarea>
          <div class="actions" style="margin-top: 8px">
            <button type="button" data-copy-category-ids>{icon('copy')} IDs kopieren</button>
            <span class="small" data-copy-category-ids-status></span>
          </div>
        </div>
        {install_info_html}
      </div>
    </section>
    <section class="panel" data-settings-panel="queries">
      <h2>Abfragen</h2>
      <form method="post" action="/settings">
        <div class="settings-grid">
          <div class="settings-card">
          <div class="field">
            <label>Wartezeit zwischen einzelnen Shop-Abfragen in Sekunden</label>
            <input name="refresh_delay_seconds" inputmode="decimal" value="{escape(settings_value(config, 'refresh_delay_seconds', '5'))}">
            <div class="small">Gilt für „Alle aktualisieren“ und für automatische Läufe.</div>
          </div>
          </div>
          <div class="settings-card">
          <div class="inline-setting">
            <label class="toggle-line"><input type="checkbox" name="auto_refresh_enabled" value="true" {'checked' if auto_enabled else ''}> Auto-Refresh aktiv</label>
            <div class="field">
            <label>Intervall in Stunden</label>
            <input name="auto_refresh_interval_hours" inputmode="decimal" value="{escape(settings_value(config, 'auto_refresh_interval_hours', '6'))}">
            </div>
          </div>
          <div class="small">Läuft serverseitig, solange die App läuft.</div>
          </div>
        </div>
        <div class="settings-card settings-card-full" style="margin-top: 12px">
          <div class="field">
            <label>Browserkennung</label>
            <input id="user-agent-input" name="user_agent" value="{escape(user_agent)}" placeholder="Leer lassen für Standardkennung">
            <div class="small">Standardkennung: {escape(default_user_agent())}</div>
          </div>
          <div class="actions" style="margin-top: 8px">
            <button type="button" id="use-current-user-agent">Aktuelle Browserkennung verwenden</button>
            <button type="button" id="clear-user-agent">Standardkennung verwenden</button>
          </div>
        </div>
        <div class="actions settings-actions">
          <button class="primary" type="submit">Speichern</button>
          <a class="button" href="/">Abbrechen</a>
        </div>
      </form>
    </section>
    <section class="panel" data-settings-panel="pdfs">
      <h2>Manuelle PDFs</h2>
      <form method="post" action="/manual-pdfs" enctype="multipart/form-data" data-pdf-processing="PDF wird hochgeladen und vorhandene Suchwörter werden geprüft...">
        <div class="settings-card">
          <div class="file-upload-row">
          <div class="field">
            <label>PDF hochladen</label>
            <input type="file" name="pdf_file" accept="application/pdf,.pdf" required data-auto-submit-file onchange="if(this.files.length){{document.querySelectorAll('[data-upload-status],[data-upload-status-global]').forEach(s=>{{s.hidden=false;s.textContent='PDF wird hochgeladen und vorhandene Suchwörter werden geprüft...';}});const b=this.form.querySelector('button[type=submit]');if(b){{b.disabled=true;b.textContent='Bitte warten...';}}window.scrollTo({{top:0,behavior:'smooth'}});setTimeout(()=>HTMLFormElement.prototype.submit.call(this.form),80);}}">
          </div>
          <button class="primary" type="submit">{icon('plus')} PDF hochladen</button>
          </div>
          <div class="small">Die PDF wird direkt nach der Auswahl hochgeladen. Hochgeladene Prospekte erscheinen als Anbieter „Manuelle PDFs“.</div>
          <div class="notice" data-upload-status hidden style="margin-top: 10px">PDF wird hochgeladen und vorhandene Suchwörter werden geprüft...</div>
        </div>
      </form>
      <form method="post" action="/settings" style="margin-top: 12px">
        <input type="hidden" name="auto_refresh_manual_pdfs_present" value="1">
        <label class="toggle-line"><input type="checkbox" name="auto_refresh_manual_pdfs" value="true" {'checked' if get_auto_refresh_manual_pdfs_enabled(config) else ''}> Manuelle PDFs beim Auto-Refresh berücksichtigen</label>
        <div class="small">Normalerweise ist das nicht sinnvoll, weil hochgeladene PDFs sich nicht von selbst ändern. Aktivieren lohnt sich nur, wenn externe Scripts die PDFs im Ordner austauschen.</div>
        <div class="actions settings-actions">
          <button class="primary" type="submit">Speichern</button>
        </div>
      </form>
      <div class="market-list" style="margin-top: 12px">{manual_pdf_rows}</div>
    </section>
    <section class="panel" data-settings-panel="home">
      <h2>Startseite</h2>
      <form method="post" action="/settings">
        <input type="hidden" name="home_settings_present" value="1">
        <div class="settings-grid">
          <div class="settings-card">
          <div class="field">
            <label>Standardansicht</label>
            <select name="default_home_view">
              <option value="all" {'selected' if home_view == 'all' else ''}>Alle</option>
              <option value="grouped" {'selected' if home_view == 'grouped' else ''}>Gruppiert</option>
            </select>
            <div class="small">Gilt nur, wenn die Startseite direkt ohne Filter oder View-Parameter geöffnet wird.</div>
          </div>
          <div class="field" style="margin-top: 10px">
            <label class="toggle-line"><input type="checkbox" name="multi_category_filter_enabled" value="true" {'checked' if multi_category_filter_enabled(config) else ''}> Mehrfachauswahl für Kategorien aktivieren</label>
            <div class="small">Zeigt auf der Startseite links neben der Kategorieauswahl einen Button für mehrere Kategorien.</div>
          </div>
          </div>
          <div class="settings-card">
          <div class="field">
            <label>Kennung in der Produkttabelle</label>
            <select name="product_id_display">
              <option value="show" {'selected' if id_display_mode == 'show' else ''}>Kennung anzeigen</option>
              <option value="hide" {'selected' if id_display_mode == 'hide' else ''}>Kennung verbergen</option>
              <option value="interactive" {'selected' if id_display_mode == 'interactive' else ''}>Interaktiv</option>
            </select>
            <div class="small">Bei „Interaktiv“ erscheint die Kennung per Mouseover oder Klick auf das Wort Kennung.</div>
          </div>
          </div>
          <div class="settings-card">
          <h3>Wunschpreis</h3>
          <div class="field">
            <label class="toggle-line"><input type="checkbox" name="target_price_highlight_enabled" value="true" {'checked' if target_price_highlight_enabled(config) else ''}> Erreichte Wunschpreise farblich markieren</label>
            <div class="small">Wenn der aktuelle Preis den Wunschpreis erreicht oder unterschreitet, wird die Artikelzeile hervorgehoben.</div>
          </div>
          <div class="field" style="margin-top: 10px">
            <label>Nicht erreichten Wunschpreis</label>
            <select name="target_price_missed_display">
              <option value="hide" {'selected' if target_price_missed_display_mode(config) == 'hide' else ''}>nicht anzeigen</option>
              <option value="normal" {'selected' if target_price_missed_display_mode(config) == 'normal' else ''}>normal anzeigen</option>
              <option value="muted" {'selected' if target_price_missed_display_mode(config) == 'muted' else ''}>ausgegraut anzeigen</option>
            </select>
            <div class="small">Erreichte Wunschpreise bleiben immer grün sichtbar.</div>
          </div>
          <div class="field" style="margin-top: 10px">
            <label class="toggle-line"><input type="checkbox" name="target_price_extra_matches_enabled" value="true" {'checked' if target_price_extra_matches_enabled(config) else ''}> Wunschpreis auch bei Zusatzartikeln anzeigen</label>
            <div class="small">Bei Prospekt-Zusatztreffern kann die Zuordnung unsicher sein, wenn ein Suchwort mehrere unterschiedliche Angebote findet. Dort wird kompakt „WP“ angezeigt.</div>
          </div>
          </div>
          <div class="settings-card">
          <div class="field">
            <label>Zusatztreffer anzeigen</label>
            <select name="pdf_extra_matches_display">
              <option value="wrap" {'selected' if extra_matches_mode == 'wrap' else ''}>Umbruch</option>
              <option value="slider" {'selected' if extra_matches_mode == 'slider' else ''}>Slider</option>
              <option value="off" {'selected' if extra_matches_mode == 'off' else ''}>Aus</option>
            </select>
            <div class="small">Betrifft zusätzliche Treffer bei Prospekt-Suchwörtern. Im Umbruch-Modus wird die Anzahl pro Zeile automatisch an die verfügbare Breite angepasst.</div>
          </div>
          <div class="field" style="margin-top: 10px">
            <input type="hidden" name="pdf_extra_matches_expanded_present" value="1">
            <label class="toggle-line"><input type="checkbox" name="pdf_extra_matches_expanded" value="true" {'checked' if extra_matches_open else ''}> Zusatztreffer standardmäßig ausgeklappt</label>
            <div class="small">Wenn deaktiviert, bleibt nur die Trefferzeile sichtbar und die Zusatztreffer lassen sich aufklappen.</div>
          </div>
          </div>
        </div>
        <div class="actions settings-actions">
          <button class="primary" type="submit">Speichern</button>
        </div>
      </form>
    </section>
    <section class="panel" data-settings-panel="api">
      <h2>JSON-API</h2>
      <form method="post" action="/settings">
        <input type="hidden" name="api_settings_present" value="1">
        <label class="toggle-line"><input type="checkbox" name="api_enabled" value="true" {'checked' if api_enabled else ''}> JSON-API aktiv</label>
        <div class="small">Wenn deaktiviert, liefert die API nur den deaktiviert-Status als JSON zurück.</div>
        <div class="actions settings-actions">
          <button class="primary" type="submit">Speichern</button>
          <a class="button" href="/api/prices" target="_blank" rel="noopener noreferrer">{icon('list')} JSON öffnen</a>
        </div>
      </form>
    </section>
    <section class="panel" data-settings-panel="browser">
      <h2>Browser-Module</h2>
      <div class="small">Wird aktuell für Anbieter genutzt, die echte Browserausführung brauchen. App-Prozessspeicher aktuell: {escape(current_process_memory_text())}. Chromium-Arbeitsspeicher fällt nur während einer laufenden Abfrage an.</div>
      <div class="market-list" style="margin-top: 12px">{browser_cache_rows}</div>
    </section>
    <section class="panel" data-settings-panel="mqtt">
      <h2>MQTT</h2>
      <form method="post" action="/settings">
        <div class="settings-grid">
          <div class="settings-card">
          <label class="toggle-line"><input type="checkbox" name="mqtt_enabled" value="true" {'checked' if mqtt_enabled else ''}> MQTT aktiv</label>
          <div class="small">Der Client verbindet sich nur, wenn MQTT aktiv ist.</div>
          </div>
          <div class="settings-card">
          <div class="field">
            <label>Clientname</label>
            <input name="mqtt_client_id" value="{escape(mqtt_client_id)}">
          </div>
          </div>
          <div class="settings-card settings-card-full">
          <div class="settings-grid align-start">
          <div class="field">
            <label>Broker-URL</label>
            <input name="mqtt_broker_url" value="{escape(settings_value(config, 'mqtt_broker_url', ''))}" placeholder="mqtt://homeassistant.local">
            <div class="small">Erlaubt: mqtt:// oder tcp:// ohne TLS, mqtts://, ssl:// oder tls:// mit TLS.</div>
          </div>
          <div class="field">
            <label>Port</label>
            <input name="mqtt_port" inputmode="numeric" value="{escape(settings_value(config, 'mqtt_port', '1883'))}">
          </div>
          </div>
          </div>
          <div class="settings-card">
          <div class="field">
            <label>Username optional</label>
            <input name="mqtt_username" value="{escape(settings_value(config, 'mqtt_username', ''))}">
          </div>
          </div>
          <div class="settings-card">
          <div class="field">
            <label>Password optional</label>
            <input type="password" name="mqtt_password" value="{escape(settings_value(config, 'mqtt_password', ''))}">
          </div>
          </div>
          <div class="settings-card">
          <div class="field">
            <label>Keepalive in Sekunden</label>
            <input name="mqtt_keepalive" inputmode="numeric" value="{escape(settings_value(config, 'mqtt_keepalive', '60'))}">
          </div>
          </div>
        </div>
        <input type="hidden" name="refresh_delay_seconds" value="{escape(settings_value(config, 'refresh_delay_seconds', '5'))}">
        <input type="hidden" name="auto_refresh_interval_hours" value="{escape(settings_value(config, 'auto_refresh_interval_hours', '6'))}">
        <input type="hidden" name="auto_refresh_enabled" value="{'true' if auto_enabled else 'false'}">
        <input type="hidden" name="user_agent" value="{escape(user_agent)}">
        <div class="actions settings-actions">
          <button class="primary" type="submit">Speichern</button>
          <button type="submit" formaction="/settings/mqtt/test">Verbindung testen</button>
        </div>
      </form>
      <div class="settings-card settings-card-full" style="margin-top: 14px">
        <h3>Home Assistant Discovery Testarea</h3>
        <div class="small">Hier siehst du den MQTT-Aufbau für genau einen Artikel. Gesendet wird nur über die Buttons.</div>
        <div class="notice" style="margin-top: 10px" data-mqtt-test-status {'hidden' if not mqtt_test_notice_html else ''}>{escape(str((mqtt_test_notice or {}).get("message") or ""))}</div>
        <form class="settings-grid align-start" method="get" action="/settings#mqtt" style="margin-top: 10px" data-no-scroll="true">
          <div class="field">
            <label>Artikel suchen</label>
            <input type="search" data-option-filter="#mqtt-preview-product" placeholder="Name, Anbieter oder Kennung">
          </div>
          <div class="field">
            <label>Artikel</label>
            <select id="mqtt-preview-product" name="mqtt_product" data-auto-submit-select>{mqtt_product_options}</select>
          </div>
        </form>
        <div class="actions" style="margin-top: 10px">
          <form method="post" action="/products/{escape(str(selected_mqtt_product_id))}/mqtt/discovery?return=json" data-mqtt-action-form="discovery" data-ajax-form data-status-target="[data-mqtt-test-status]" data-no-scroll="true"><button type="submit">{icon('settings')} Discovery senden</button></form>
          <form method="post" action="/products/{escape(str(selected_mqtt_product_id))}/mqtt/state?return=json" data-mqtt-action-form="state" data-ajax-form data-status-target="[data-mqtt-test-status]" data-no-scroll="true"><button type="submit">{icon('refresh')} Status senden</button></form>
          <form method="post" action="/products/{escape(str(selected_mqtt_product_id))}/mqtt/delete?return=json" data-mqtt-action-form="delete" data-ajax-form data-status-target="[data-mqtt-test-status]" data-no-scroll="true"><button class="danger" type="submit">{icon('trash')} Aus HA löschen</button></form>
        </div>
        <div class="settings-grid align-start" style="margin-top: 12px">
          <div class="field">
            <label>Discovery Config</label>
            <textarea class="code-preview" data-mqtt-preview="discovery" readonly>{escape(mqtt_preview_discovery)}</textarea>
          </div>
          <div class="field">
            <label>Status Payload</label>
            <textarea class="code-preview" data-mqtt-preview="state" readonly>{escape(mqtt_preview_state)}</textarea>
          </div>
          <div class="field settings-card-full">
            <label>Discovery löschen</label>
            <textarea class="code-preview" data-mqtt-preview="delete" readonly>{escape(mqtt_preview_delete)}</textarea>
          </div>
        </div>
      </div>
    </section>
    <section class="panel" data-settings-panel="backup">
      <h2>Backup</h2>
      <div class="settings-grid align-start">
        <div class="settings-card">
          <h3>Export</h3>
          <div class="small">Erstellt eine ZIP-Datei mit den ausgewählten lokalen Daten. Caches und generierte Bilder werden nicht gesichert, weil sie neu erzeugt werden können.</div>
          <form method="get" action="/backup/export" style="margin-top: 12px">
            <label class="toggle-line"><input type="checkbox" name="config" value="true" checked> Konfigurationsdatei sichern</label>
            <div class="small">Enthält Einstellungen, Märkte, Kategorien und Artikelliste aus <code>config.yaml</code>.</div>
            <label class="toggle-line" style="margin-top: 10px"><input type="checkbox" name="state" value="true" checked> Artikel- und Statusliste sichern</label>
            <div class="small">Enthält letzte Preise, Zeitpunkte und Status aus <code>state.json</code>.</div>
            <label class="toggle-line" style="margin-top: 10px"><input type="checkbox" name="pdfs" value="true" checked> Hochgeladene PDFs sichern</label>
            <div class="small">Enthält die Dateien aus <code>manual_pdfs</code>.</div>
            <div class="actions settings-actions">
              <button class="primary" type="submit">{icon('download')} Backup als ZIP herunterladen</button>
            </div>
          </form>
        </div>
        <div class="settings-card">
          <h3>Import</h3>
          <div class="small">Lade eine Backup-ZIP hoch. Die App prüft zuerst, was enthalten ist, und fragt danach, welche Bereiche überschrieben werden sollen.</div>
          <div class="small">Eine geprüfte ZIP wird nur temporär gespeichert. „Abbrechen“ löscht sie wieder; nach einer Wiederherstellung wird sie ebenfalls entfernt.</div>
          <form method="post" action="/backup/import/analyze" enctype="multipart/form-data" style="margin-top: 12px" data-backup-upload-form>
            <div class="field">
              <label>Backup-ZIP</label>
              <input type="file" name="backup_file" accept="application/zip,.zip" required onchange="if(this.files.length) showBusyOverlay('Backup ausgewählt. Klicke auf Backup prüfen, um die Datei hochzuladen und zu prüfen.')">
            </div>
            <div class="notice" data-backup-status hidden style="margin-top: 10px">Backup wird hochgeladen und geprüft. Bei großen ZIP-Dateien kann das einen Moment dauern...</div>
            <div class="actions settings-actions">
              <button type="submit" onclick="showBusyOverlay('Backup wird hochgeladen und geprüft. Bei großen ZIP-Dateien kann das einen Moment dauern...')">{icon('upload')} Backup prüfen</button>
            </div>
          </form>
        </div>
        {backup_import_html}
      </div>
    </section>
    <section class="panel" data-settings-panel="updates">
      <h2>Update</h2>
      <div class="settings-grid align-start">
        <div class="settings-card">
          <h3>Installierte Version</h3>
          <div class="metric"><span>{escape(APP_NAME)}</span><strong>v{escape(APP_VERSION)}</strong></div>
          <div class="small">Diese Anzeige kommt aus der App selbst. Später kann hier gegen GitHub-Releases geprüft werden.</div>
        </div>
        <div class="settings-card">
          <h3>Geplante Update-Wege</h3>
          <div class="small">Release-Update: installiert eine veröffentlichte Version mit Versionsnummer.</div>
          <div class="small">Git-Pull-Update: zieht den neuesten Stand von main für Tests oder schnelle Korrekturen.</div>
        </div>
        <div class="settings-card settings-card-full">
          <h3>Config-Schutz</h3>
          <div class="small">Updates sollen keine lokale config.yaml, state.json, hochgeladenen PDFs, generierten Bilder oder Cache-Daten überschreiben. Diese Dateien gehören nicht ins öffentliche Repo.</div>
        </div>
      </div>
    </section>
    <footer class="app-footer"><span>{escape(APP_NAME)}</span><span>v{escape(APP_VERSION)}</span></footer>
  </main>
  <script>
    function showBusyOverlay(message) {{
      const textMessage = message || 'Vorgang läuft...';
      const overlay = document.querySelector('[data-busy-overlay]');
      if (overlay) {{
        overlay.hidden = false;
        const text = overlay.querySelector('[data-busy-overlay-text]');
        if (text) text.textContent = textMessage;
      }}
      document.querySelectorAll('[data-upload-status-global], [data-backup-status], [data-restore-status]').forEach((status) => {{
        status.hidden = false;
        status.textContent = textMessage;
      }});
    }}

    document.querySelectorAll('[data-backup-upload-form]').forEach((form) => {{
      form.addEventListener('submit', (event) => {{
        const message = 'Backup wird hochgeladen und geprüft. Bei großen ZIP-Dateien kann das einen Moment dauern...';
        showBusyOverlay(message);
        if (form.dataset.backupSubmitting === 'true') return;
        event.preventDefault();
        form.dataset.backupSubmitting = 'true';
        const button = event.submitter || form.querySelector('button[type="submit"], button:not([type])');
        if (button) {{
          button.disabled = true;
          button.dataset.originalText = button.textContent;
          button.textContent = 'Bitte warten...';
        }}
        requestAnimationFrame(() => {{
          window.setTimeout(() => HTMLFormElement.prototype.submit.call(form), 120);
        }});
      }});
    }});

    document.querySelectorAll('[data-backup-restore-form]').forEach((form) => {{
      form.addEventListener('submit', () => {{
        showBusyOverlay('Backup wird wiederhergestellt. PDFs und Suchwörter werden verarbeitet...');
      }});
    }});

    const userAgentInput = document.getElementById('user-agent-input');
    document.getElementById('use-current-user-agent')?.addEventListener('click', () => {{
      userAgentInput.value = navigator.userAgent || '';
    }});
    document.getElementById('clear-user-agent')?.addEventListener('click', () => {{
      userAgentInput.value = '';
    }});
    const updateMqttPreview = (productId) => {{
      if (!productId) return;
      const status = document.querySelector('[data-mqtt-test-status]');
      fetch(`/settings/mqtt/preview?mqtt_product=${{encodeURIComponent(productId)}}`, {{cache: 'no-store'}})
        .then((response) => response.json())
        .then((data) => {{
          if (!data.ok) throw new Error(data.error || 'Preview konnte nicht geladen werden.');
          const discovery = document.querySelector('[data-mqtt-preview="discovery"]');
          const state = document.querySelector('[data-mqtt-preview="state"]');
          const deletePreview = document.querySelector('[data-mqtt-preview="delete"]');
          if (discovery) discovery.value = data.discovery;
          if (state) state.value = data.state;
          if (deletePreview) deletePreview.value = data.delete;
          document.querySelectorAll('[data-mqtt-action-form]').forEach((form) => {{
            const action = form.dataset.mqttActionForm;
            form.action = `/products/${{encodeURIComponent(productId)}}/mqtt/${{action}}?return=json`;
          }});
          if (status && status.textContent) status.textContent = '';
        }})
        .catch((error) => {{
          if (status) {{
            status.hidden = false;
            status.textContent = 'MQTT-Vorschau fehlgeschlagen: ' + error.message;
          }}
        }});
    }};
    document.querySelectorAll('[data-option-filter]').forEach((input) => {{
      const select = document.querySelector(input.dataset.optionFilter || '');
      if (!select) return;
      const options = [...select.options].map((option) => ({{
        value: option.value,
        text: option.textContent,
        search: `${{option.textContent}} ${{option.value}}`.toLowerCase(),
      }}));
      const renderOptions = (items) => {{
        const previousValue = select.value;
        select.replaceChildren(...items.map((item) => {{
          const option = document.createElement('option');
          option.value = item.value;
          option.textContent = item.text;
          return option;
        }}));
        if (items.some((item) => item.value === previousValue)) {{
          select.value = previousValue;
        }} else if (items.length) {{
          select.selectedIndex = 0;
          updateMqttPreview(select.value);
        }}
      }};
      input.addEventListener('input', () => {{
        const query = input.value.trim().toLowerCase();
        const filtered = query ? options.filter((item) => item.search.includes(query)) : options;
        renderOptions(filtered);
      }});
    }});
    document.querySelectorAll('[data-auto-submit-select]').forEach((select) => {{
      select.addEventListener('change', () => {{
        updateMqttPreview(select.value);
      }});
    }});
    document.querySelectorAll('[data-ajax-form]').forEach((form) => {{
      form.addEventListener('submit', (event) => {{
        event.preventDefault();
        const status = document.querySelector(form.dataset.statusTarget || '');
        const button = event.submitter || form.querySelector('button[type="submit"], button:not([type])');
        const originalText = button?.textContent || '';
        if (status) {{
          status.hidden = false;
          status.textContent = 'MQTT-Aktion wird gesendet...';
        }}
        if (button) {{
          button.disabled = true;
          button.textContent = 'Bitte warten...';
        }}
        fetch(form.action, {{method: form.method || 'POST'}})
          .then((response) => response.json())
          .then((data) => {{
            if (status) status.textContent = data.message || (data.ok ? 'MQTT-Aktion ausgeführt.' : 'MQTT-Aktion fehlgeschlagen.');
          }})
          .catch((error) => {{
            if (status) status.textContent = 'MQTT-Aktion fehlgeschlagen: ' + error;
          }})
          .finally(() => {{
            if (button) {{
              button.disabled = false;
              button.textContent = originalText;
            }}
        }});
      }});
    }});
    const categoryIdOutput = document.querySelector('[data-category-id-output]');
    const categoryIdChecks = document.querySelectorAll('[data-category-id-select]');
    const updateCategoryIdOutput = () => {{
      if (!categoryIdOutput) return;
      categoryIdOutput.value = [...categoryIdChecks]
        .filter((item) => item.checked)
        .map((item) => item.value)
        .join(', ');
    }};
    categoryIdChecks.forEach((item) => item.addEventListener('change', updateCategoryIdOutput));
    updateCategoryIdOutput();
    const copyCategoryIdsButton = document.querySelector('[data-copy-category-ids]');
    const copyCategoryIdsStatus = document.querySelector('[data-copy-category-ids-status]');
    if (copyCategoryIdsButton && categoryIdOutput) {{
      copyCategoryIdsButton.addEventListener('click', async () => {{
        const value = categoryIdOutput.value.trim();
        if (!value) {{
          if (copyCategoryIdsStatus) copyCategoryIdsStatus.textContent = 'Keine IDs ausgewählt.';
          return;
        }}
        try {{
          if (navigator.clipboard && window.isSecureContext) {{
            await navigator.clipboard.writeText(value);
          }} else {{
            categoryIdOutput.focus();
            categoryIdOutput.select();
            document.execCommand('copy');
          }}
          if (copyCategoryIdsStatus) copyCategoryIdsStatus.textContent = 'Kopiert.';
        }} catch (error) {{
          if (copyCategoryIdsStatus) copyCategoryIdsStatus.textContent = 'Kopieren nicht möglich.';
        }}
        window.setTimeout(() => {{
          if (copyCategoryIdsStatus) copyCategoryIdsStatus.textContent = '';
        }}, 2200);
      }});
    }}
    const settingsTabs = document.querySelectorAll('[data-settings-tab]');
    const settingsPanels = document.querySelectorAll('[data-settings-panel]');
    const activateSettingsTab = (name) => {{
      if (![...settingsTabs].some((tab) => tab.dataset.settingsTab === name)) name = 'info';
      settingsTabs.forEach((tab) => tab.classList.toggle('is-active', tab.dataset.settingsTab === name));
      settingsPanels.forEach((panel) => {{
        panel.hidden = panel.dataset.settingsPanel !== name;
      }});
      if (location.hash !== '#' + name) history.replaceState(null, '', '#' + name);
    }};
    settingsTabs.forEach((tab) => {{
      tab.addEventListener('click', () => activateSettingsTab(tab.dataset.settingsTab));
    }});
    const initialSettingsTab = location.hash || (new URLSearchParams(location.search).has('mqtt_product') ? '#mqtt' : '#info');
    activateSettingsTab(initialSettingsTab.slice(1));
    const restoreScrollY = sessionStorage.getItem('preisermittlung.restoreScrollY');
    if (restoreScrollY !== null) {{
      sessionStorage.removeItem('preisermittlung.restoreScrollY');
      requestAnimationFrame(() => window.scrollTo({{top: Number(restoreScrollY) || 0, behavior: 'auto'}}));
    }}
  </script>
</body>
</html>"""


@app.get("/")
def index() -> Response:
    try:
        config = load_config()
        state = load_state()
        return Response(render_page(config, state), mimetype="text/html")
    except Exception as exc:
        return Response(render_page({"markets": [], "products": []}, load_state(), str(exc)), mimetype="text/html", status=500)


@app.get("/settings")
def settings_page() -> Response:
    try:
        config = load_config()
        state = load_state()
        return Response(render_settings_page(config, state), mimetype="text/html")
    except Exception as exc:
        return Response(render_settings_page({"markets": [], "products": [], "settings": {}}, load_state(), str(exc)), mimetype="text/html", status=500)


@app.post("/settings")
def save_settings() -> Response:
    config = load_config()
    save_settings_from_form(config)
    save_config(config)
    set_notice("Settings gespeichert.")
    anchor = "queries"
    if (
        "home_settings_present" in request.form
        or "product_id_display" in request.form
        or "pdf_extra_matches_display" in request.form
        or "default_home_view" in request.form
    ):
        anchor = "home"
    elif "api_settings_present" in request.form:
        anchor = "api"
    elif "auto_refresh_manual_pdfs_present" in request.form:
        anchor = "pdfs"
    elif "mqtt_client_id" in request.form:
        anchor = "mqtt"
    return redirect(url_for("settings_page", _anchor=anchor))


@app.get("/settings/mqtt/preview")
def mqtt_preview_api() -> Response:
    config = load_config()
    product_id = request.args.get("mqtt_product", "").strip()
    product = next((item for item in products_with_state(config) if item.get("id") == product_id), None)
    if not product:
        return jsonify({"ok": False, "error": "Artikel nicht gefunden."}), 404
    preview = mqtt_preview_payloads(config, product)
    return jsonify(
        {
            "ok": True,
            "product_id": product_id,
            "discovery": json.dumps(
                {"discovery_topic": preview.get("discovery_topic"), "discovery_payload": preview.get("discovery_payload")},
                ensure_ascii=False,
                indent=2,
            ),
            "state": json.dumps(
                {"state_topic": preview.get("state_topic"), "state_payload": preview.get("state_payload")},
                ensure_ascii=False,
                indent=2,
            ),
            "delete": json.dumps(
                {"delete_topic": preview.get("delete_topic"), "delete_payload": preview.get("delete_payload")},
                ensure_ascii=False,
                indent=2,
            ),
        }
    )


@app.post("/settings/mqtt/test")
def test_mqtt() -> Response:
    config = load_config()
    settings = save_settings_from_form(config)
    save_config(config)
    try:
        message = test_mqtt_connection(settings)
        with state_lock:
            state = load_state()
            state["mqtt_last_test"] = {"ok": True, "message": message, "checked_at": now_iso()}
            save_state(state)
        set_notice(message)
    except Exception as exc:
        message = f"MQTT-Test fehlgeschlagen: {exc}"
        with state_lock:
            state = load_state()
            state["mqtt_last_test"] = {"ok": False, "message": message, "checked_at": now_iso()}
            save_state(state)
        set_notice(message)
    return redirect(url_for("settings_page", _anchor="mqtt"))


@app.get("/backup/export")
def export_backup() -> Response:
    include_config = request.args.get("config") == "true"
    include_state = request.args.get("state") == "true"
    include_pdfs = request.args.get("pdfs") == "true"
    if not (include_config or include_state or include_pdfs):
        set_notice("Bitte mindestens einen Backup-Bereich auswählen.")
        return redirect(url_for("settings_page", _anchor="backup"))
    payload = create_backup_zip(include_config, include_state, include_pdfs)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    filename = f"preisermittlung_backup_{timestamp}.zip"
    return Response(
        payload,
        mimetype="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(len(payload)),
        },
    )


@app.post("/backup/import/analyze")
def analyze_backup_import() -> Response:
    try:
        cleanup_old_backup_imports()
        upload = request.files.get("backup_file")
        if not upload or not getattr(upload, "filename", ""):
            set_notice("Keine Backup-ZIP ausgewählt.")
            return redirect(url_for("settings_page", _anchor="backup"))
        if not str(upload.filename).lower().endswith(".zip"):
            set_notice("Bitte eine ZIP-Datei auswählen.")
            return redirect(url_for("settings_page", _anchor="backup"))
        BACKUP_IMPORT_PATH.mkdir(parents=True, exist_ok=True)
        token = uuid.uuid4().hex
        target = BACKUP_IMPORT_PATH / f"{token}.zip"
        upload.save(target)
        info = analyze_backup_file(target)
    except zipfile.BadZipFile:
        target.unlink(missing_ok=True)
        set_notice("Die Datei ist keine gültige ZIP-Datei.")
        return redirect(url_for("settings_page", _anchor="backup"))
    except Exception as exc:
        app.logger.exception("Backup import analysis failed")
        if "target" in locals():
            target.unlink(missing_ok=True)
        set_notice(f"Backup konnte nicht geprüft werden: {exc}")
        return redirect(url_for("settings_page", _anchor="backup"))
    if not backup_has_components(info):
        target.unlink(missing_ok=True)
        set_notice("In dieser ZIP-Datei wurden keine bekannten Backup-Daten gefunden.")
        return redirect(url_for("settings_page", _anchor="backup"))
    with state_lock:
        state = load_state()
        remove_pending_backup_import(state)
        state["backup_import"] = {
            "token": token,
            "filename": Path(str(upload.filename)).name,
            "info": info,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        save_state(state)
    set_notice("Backup geprüft. Bitte auswählen, was wiederhergestellt werden soll.")
    return redirect(url_for("settings_page", _anchor="backup"))


@app.get("/backup/import/cancel")
def cancel_backup_import() -> Response:
    with state_lock:
        state = load_state()
        remove_pending_backup_import(state)
        save_state(state)
    set_notice("Backup-Import abgebrochen.")
    return redirect(url_for("settings_page", _anchor="backup"))


@app.post("/backup/import/confirm")
def confirm_backup_import() -> Response:
    token = request.form.get("token", "").strip()
    if not token or not re.fullmatch(r"[0-9a-f]{32}", token):
        set_notice("Backup-Import nicht gefunden.")
        return redirect(url_for("settings_page", _anchor="backup"))
    backup_path = BACKUP_IMPORT_PATH / f"{token}.zip"
    if not backup_path.exists():
        set_notice("Die hochgeladene Backup-Datei ist nicht mehr vorhanden.")
        return redirect(url_for("settings_page", _anchor="backup"))
    restore_config = request.form.get("restore_config") == "true"
    restore_state = request.form.get("restore_state") == "true"
    restore_pdfs = request.form.get("restore_pdfs") == "true"
    if not (restore_config or restore_state or restore_pdfs):
        set_notice("Bitte mindestens einen Bereich für die Wiederherstellung auswählen.")
        return redirect(url_for("settings_page", _anchor="backup"))
    try:
        restored = restore_backup_file(backup_path, restore_config, restore_state, restore_pdfs)
        if restore_pdfs:
            refreshed = refresh_provider_products(load_config(), "manual_pdf")
            if refreshed:
                restored.append(f"{refreshed} PDF-Suchwörter neu geprüft")
    except Exception as exc:
        set_notice(f"Backup konnte nicht wiederhergestellt werden: {exc}")
        return redirect(url_for("settings_page", _anchor="backup"))
    backup_path.unlink(missing_ok=True)
    with state_lock:
        state = load_state()
        state.pop("backup_import", None)
        state["notice"] = "Backup wiederhergestellt: " + (", ".join(restored) if restored else "keine Daten geändert")
        save_state(state)
    return redirect(url_for("settings_page", _anchor="backup"))


def product_by_id_with_state(config: Dict[str, Any], product_id: str) -> Optional[Dict[str, Any]]:
    return next((product for product in products_with_state(config) if str(product.get("id")) == str(product_id)), None)


@app.post("/products/<product_id>/mqtt/<action>")
def product_mqtt_action(product_id: str, action: str) -> Response:
    config = load_config()
    product = product_by_id_with_state(config, product_id)
    return_target = request.args.get("return") or ""
    dialog_anchor = f"move-{re.sub(r'[^a-zA-Z0-9_-]+', '-', product_id)}"
    def mqtt_redirect() -> Response:
        if return_target == "dialog":
            return redirect(url_for("index", mqtt_product=product_id, _anchor=dialog_anchor))
        if return_target == "mqtt_settings":
            return redirect(url_for("settings_page", mqtt_product=product_id, _anchor="mqtt"))
        referrer = request.referrer or ""
        if "/settings" in referrer:
            return redirect(url_for("settings_page", mqtt_product=product_id, _anchor="mqtt"))
        return redirect(request.referrer or url_for("index"))

    if not product:
        message = f"Artikel nicht gefunden: {product_id}"
        if return_target == "json":
            return jsonify({"ok": False, "message": message}), 404
        if return_target == "dialog":
            set_product_mqtt_notice(product_id, message)
        elif return_target == "mqtt_settings":
            with state_lock:
                state = load_state()
                state["mqtt_test_notice"] = {"message": message, "checked_at": now_iso(), "product_id": product_id}
                save_state(state)
        else:
            set_notice(message)
        return mqtt_redirect()
    try:
        message = mqtt_publish_for_product(config, product, action)
        ok = True
    except Exception as exc:
        message = f"MQTT-Aktion fehlgeschlagen: {exc}"
        ok = False
    if return_target == "json":
        return jsonify({"ok": ok, "message": message})
    if return_target == "dialog":
        set_product_mqtt_notice(product_id, message)
    elif return_target == "mqtt_settings":
        with state_lock:
            state = load_state()
            state["mqtt_test_notice"] = {"message": message, "checked_at": now_iso(), "product_id": product_id}
            save_state(state)
    else:
        set_notice(message)
    return mqtt_redirect()


@app.post("/settings/browser-cache/<provider>/clear")
def clear_provider_browser_cache(provider: str) -> Response:
    try:
        clear_browser_cache(provider)
        set_notice("Browser-Cache geleert.")
    except Exception as exc:
        set_notice(f"Browser-Cache konnte nicht geleert werden: {exc}")
    return redirect(url_for("settings_page"))


@app.post("/manual-pdfs")
def upload_manual_pdf() -> Response:
    upload = request.files.get("pdf_file")
    if not upload or not getattr(upload, "filename", ""):
        set_notice("Keine PDF-Datei ausgewählt.")
        return redirect(url_for("settings_page", _anchor="pdfs"))
    if not str(upload.filename).lower().endswith(".pdf"):
        set_notice("Bitte eine PDF-Datei hochladen.")
        return redirect(url_for("settings_page", _anchor="pdfs"))
    try:
        name = manual_pdf_reader.save_upload(upload)
        refreshed = refresh_provider_products(load_config(), "manual_pdf")
        suffix = f" · {refreshed} Suchwörter neu geprüft" if refreshed else ""
        set_notice(f"PDF hochgeladen: {name}{suffix}")
    except Exception as exc:
        set_notice(f"PDF konnte nicht hochgeladen werden: {exc}")
    return redirect(url_for("settings_page", _anchor="pdfs"))


@app.post("/manual-pdfs/<path:name>/delete")
def delete_manual_pdf(name: str) -> Response:
    try:
        manual_pdf_reader.delete_pdf(name)
        refreshed = refresh_provider_products(load_config(), "manual_pdf")
        suffix = f" · {refreshed} Suchwörter neu geprüft" if refreshed else ""
        set_notice(f"PDF gelöscht: {Path(name).name}{suffix}")
    except Exception as exc:
        set_notice(f"PDF konnte nicht gelöscht werden: {exc}")
    return redirect(url_for("settings_page", _anchor="pdfs"))


@app.get("/manual-pdfs/file/<path:name>")
def manual_pdf_file(name: str) -> Response:
    return send_from_directory(manual_pdf_reader.UPLOAD_DIR, Path(name).name)


@app.post("/theme")
def toggle_theme() -> Response:
    config = load_config()
    settings = config.setdefault("settings", {})
    settings["theme"] = "light" if current_theme(config) == "dark" else "dark"
    save_config(config)
    return redirect(request.referrer or url_for("index"))


@app.get("/api/prices")
def api_prices() -> Response:
    try:
        config = load_config()
        if not get_api_enabled(config):
            return jsonify({"ok": False, "disabled": True, "error": "JSON-API ist deaktiviert."})
        state = load_state()
        products = products_with_state(config)
        return jsonify(
            {
                "ok": True,
                "markets": markets_from_config(config),
                "products": [{**{k: v for k, v in product.items() if k != "state"}, **product["state"]} for product in products],
                "last_refresh_started_at": state.get("last_refresh_started_at"),
                "last_refresh_finished_at": state.get("last_refresh_finished_at"),
                "progress": dict(progress),
            }
        )
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.get("/api/progress")
def api_progress() -> Response:
    with state_lock:
        return jsonify(dict(progress))


@app.get("/generated/<path:filename>")
def generated_file(filename: str) -> Response:
    return send_from_directory(GENERATED_PATH, filename)


@app.post("/refresh")
def refresh_all() -> Response:
    start_refresh()
    return redirect(url_for("index", refresh_started=1))


@app.post("/products/<product_id>/refresh")
def refresh_product(product_id: str) -> Response:
    start_refresh(product_id)
    return redirect(url_for("index"))


@app.post("/markets/search")
def search_markets() -> Response:
    config = load_config()
    provider = request.form.get("provider", "rewe").strip() or "rewe"
    postal_code = request.form.get("postal_code", "").strip()
    query = request.form.get("query", "").strip().lower()
    if postal_code:
        results = find_markets(provider, postal_code)
        if query:
            results = [market for market in results if query in market_label(market).lower()]
        with state_lock:
            state = load_state()
            state["market_search"] = {"provider": provider, "postal_code": postal_code, "query": query, "results": results}
            save_state(state)
    return redirect(url_for("index", market_dialog=1, market_results=1))


@app.post("/markets")
def add_market() -> Response:
    config = load_config()
    market = {
        key: request.form.get(key, "").strip()
        for key in (
            "provider",
            "market_id",
            "postal_code",
            "service",
            "market_name",
            "market_company",
            "market_street",
            "market_city",
            "market_match",
            "market_match_2",
        )
    }
    market["provider"] = market.get("provider") or "rewe"
    if market["market_id"]:
        markets = config.setdefault("markets", [])
        if any(item.get("market_id") == market["market_id"] and market_provider(item) == market["provider"] for item in markets):
            set_notice("Dieser Markt ist bereits gespeichert.")
            return redirect(url_for("index", markets_dialog=1))
        markets.append(market)
        save_config(config)
    return redirect(url_for("index"))


@app.post("/markets/<market_id>/delete")
def delete_market(market_id: str) -> Response:
    config = load_config()
    provider = request.form.get("provider", "rewe").strip() or "rewe"
    delete_action = request.form.get("delete_action", "delete_products")
    target_raw = request.form.get("target_market_id", "")
    target_provider, _, target_market_id = target_raw.partition("::")
    if not target_market_id:
        target_provider, target_market_id = provider, target_raw
    assigned_products = [
        product
        for product in config.get("products", [])
        if product.get("market_id") == market_id and product_provider(config, product) == provider
    ]

    if assigned_products and delete_action == "reassign":
        if not target_market_id or (target_market_id == market_id and target_provider == provider):
            set_notice("Bitte einen anderen Zielmarkt auswählen.")
            return redirect(url_for("index", markets_dialog=1, delete_market=market_id, provider=provider))
        for product in assigned_products:
            product["provider"] = target_provider
            product["market_id"] = target_market_id
    elif assigned_products:
        remove_ids = {product["id"] for product in assigned_products}
        config["products"] = [product for product in config.get("products", []) if product.get("id") not in remove_ids]
        with state_lock:
            state = load_state()
            for product_id in remove_ids:
                state.setdefault("products", {}).pop(product_id, None)
            save_state(state)

    config["markets"] = [
        market
        for market in config.get("markets", [])
        if not (market.get("market_id") == market_id and market_provider(market) == provider)
    ]
    save_config(config)
    return redirect(url_for("index"))


@app.post("/products")
def add_product() -> Response:
    config = load_config()
    categories = categories_from_config(config)
    configure_user_agent((config.get("settings") or {}).get("user_agent"))
    product_url = request.form.get("product_url", "").strip()
    market_raw = request.form.get("market_id", "").strip()
    provider, _, market_id = market_raw.partition("::")
    if not market_id:
        provider, market_id = "rewe", market_raw
    article_number = (
        request.form.get("article_number", "").strip()
        or provider_article_number_from_url(provider, product_url)
        or article_number_from_url(product_url)
    )
    if not article_number or not market_id:
        return redirect(url_for("index"))
    category_id = request.form.get("category_id", DEFAULT_CATEGORY_ID).strip() or DEFAULT_CATEGORY_ID
    if not any(category["id"] == category_id for category in categories):
        category_id = DEFAULT_CATEGORY_ID

    products = config.setdefault("products", [])
    if any(
        product.get("article_number") == article_number
        and product.get("market_id") == market_id
        and product_provider(config, product) == provider
        for product in products
    ):
        set_notice("Dieser Artikel ist für diesen Markt bereits vorhanden.")
        return redirect(url_for("index"))

    requested_id = request.form.get("id", "").strip()
    name = article_number
    result = None
    market_config = market_for_selection(provider, market_id, markets_from_config(config))
    if market_config:
        try:
            probe_product = {
                "id": requested_id or f"artikel_{article_number}",
                "article_number": article_number,
                "name": name,
                "provider": provider,
                "market_id": market_id,
                "product_url": normalize_product_url(provider, product_url, article_number),
            }
            result = read_product(provider, probe_product, resolve_market(provider, market_config), market_config.get("postal_code", ""))
            name = result.get("title") or result.get("name") or article_number
        except Exception:
            name = article_number

    resolved_article_number = str((result or {}).get("provider_article_number") or article_number)
    if resolved_article_number != article_number:
        if any(
            product.get("article_number") == resolved_article_number
            and product.get("market_id") == market_id
            and product_provider(config, product) == provider
            for product in products
        ):
            set_notice("Dieser Artikel ist für diesen Markt bereits vorhanden.")
            return redirect(url_for("index"))
        article_number = resolved_article_number

    product_id = requested_id or product_id_from(article_number, name)
    product_id = unique_product_id(products, product_id, article_number, market_id, provider)
    product = {
        "id": product_id,
        "article_number": article_number,
        "name": name,
        "category_id": category_id,
        "provider": provider,
        "market_id": market_id,
        "product_url": normalize_product_url(provider, product_url, article_number),
    }
    products.append(product)
    save_config(config)
    if result:
        result["id"] = product_id
        update_state_for_product(product, result, None)
    else:
        save_product_url_state(product, normalize_product_url(provider, product_url, article_number))
    return redirect(url_for("index"))


@app.post("/pdf-products/analyze")
def analyze_pdf_product() -> Response:
    config = load_config()
    categories = categories_from_config(config)
    configure_user_agent((config.get("settings") or {}).get("user_agent"))
    provider = request.form.get("provider", "").strip()
    search_term = request.form.get("search_term", "").strip()
    if not provider or not search_term or provider_kind(provider) != "prospect":
        return redirect(url_for("index", add_pdf=1))

    category_id = request.form.get("category_id", DEFAULT_CATEGORY_ID).strip() or DEFAULT_CATEGORY_ID
    if not any(category["id"] == category_id for category in categories):
        category_id = DEFAULT_CATEGORY_ID

    market_id = "weekly"
    product_url = normalize_product_url(provider, search_term, search_term)
    article_number = provider_article_number_from_url(provider, product_url) or product_id_from(search_term, search_term)
    requested_id = request.form.get("id", "").strip()
    probe_product = {
        "id": requested_id or f"pdf_{article_number}",
        "article_number": article_number,
        "name": search_term,
        "category_id": category_id,
        "provider": provider,
        "market_id": market_id,
        "product_url": product_url,
        "search_term": search_term,
    }

    try:
        market = virtual_markets(provider)[0]
        result = read_product(provider, probe_product, market, "")
    except Exception as exc:
        set_pdf_analysis(
            {
                "ok": False,
                "provider": provider,
                "category_id": category_id,
                "search_term": search_term,
                "display_name": search_term,
                "requested_id": requested_id,
                "error": str(exc),
            }
        )
        return redirect(url_for("index", add_pdf=1))

    set_pdf_analysis(
        {
            "ok": True,
            "provider": provider,
            "category_id": category_id,
            "search_term": search_term,
            "display_name": result.get("title") or search_term,
            "requested_id": requested_id,
            "result": result,
        }
    )
    return redirect(url_for("index", add_pdf=1))


@app.post("/pdf-products/confirm")
def confirm_pdf_product() -> Response:
    config = load_config()
    categories = categories_from_config(config)
    provider = request.form.get("provider", "").strip()
    search_term = request.form.get("search_term", "").strip()
    display_name = request.form.get("display_name", "").strip()
    requested_id = request.form.get("id", "").strip()
    found = request.form.get("found") == "true"
    if not provider or not search_term or provider_kind(provider) != "prospect":
        return redirect(url_for("index", add_pdf=1))

    category_id = request.form.get("category_id", DEFAULT_CATEGORY_ID).strip() or DEFAULT_CATEGORY_ID
    if not any(category["id"] == category_id for category in categories):
        category_id = DEFAULT_CATEGORY_ID

    with state_lock:
        state = load_state()
        analysis = state.get("pdf_analysis") or {}
        if (
            analysis.get("provider") != provider
            or analysis.get("search_term") != search_term
        ):
            analysis = {}

    result = analysis.get("result") if found and isinstance(analysis.get("result"), dict) else None

    market_id = "weekly"
    product_url = normalize_product_url(provider, search_term, search_term)
    article_number = provider_article_number_from_url(provider, product_url) or product_id_from(search_term, search_term)
    resolved_article_number = str((result or {}).get("provider_article_number") or article_number)
    products = config.setdefault("products", [])
    if any(
        product.get("article_number") == resolved_article_number
        and product.get("market_id") == market_id
        and product_provider(config, product) == provider
        for product in products
    ):
        with state_lock:
            state = load_state()
            state.pop("pdf_analysis", None)
            save_state(state)
        set_notice("Dieser Prospekt-Treffer ist bereits vorhanden.")
        return redirect(url_for("index"))

    auto_name = not bool(display_name)
    name = display_name or (result or {}).get("title") or search_term
    product_id = requested_id or product_id_from(resolved_article_number, name)
    product_id = unique_product_id(products, product_id, resolved_article_number, market_id, provider)
    product = {
        "id": product_id,
        "article_number": resolved_article_number,
        "name": name,
        "category_id": category_id,
        "provider": provider,
        "market_id": market_id,
        "product_url": product_url,
        "search_term": search_term,
    }
    if auto_name:
        product["pdf_auto_name"] = "true"
    products.append(product)
    save_config(config)
    if result:
        result["id"] = product_id
        update_state_for_product(product, result, None)
    else:
        save_product_url_state(product, product_url)
        with state_lock:
            state = load_state()
            state.setdefault("products", {}).setdefault(product_id, {})["last_error"] = "Im aktuellen Prospekt noch nicht gefunden."
            save_state(state)
    with state_lock:
        state = load_state()
        state.pop("pdf_analysis", None)
        save_state(state)
    set_notice(f"PDF-Suchwort angelegt: {search_term}")
    return redirect(url_for("index"))


@app.post("/generic/analyze")
def analyze_generic_product() -> Response:
    config = load_config()
    categories = categories_from_config(config)
    configure_user_agent((config.get("settings") or {}).get("user_agent"))
    product_url = request.form.get("product_url", "").strip()
    category_id = request.form.get("category_id", DEFAULT_CATEGORY_ID).strip() or DEFAULT_CATEGORY_ID
    if not any(category["id"] == category_id for category in categories):
        category_id = DEFAULT_CATEGORY_ID
    requested_id = request.form.get("id", "").strip()
    prefer_browser = request.form.get("prefer_browser") == "true"
    try:
        analysis = generic_reader.analyze_generic_url(product_url, prefer_browser=prefer_browser)
    except Exception as exc:
        set_notice(f"Generic-Analyse fehlgeschlagen: {exc}")
        return redirect(url_for("index"))

    analysis["category_id"] = category_id
    analysis["requested_id"] = requested_id
    analysis["requested_mode"] = "browser" if prefer_browser else "http"
    browser_memory = analysis.get("browser_memory")
    with state_lock:
        state = load_state()
        state["generic_analysis"] = analysis
        if isinstance(browser_memory, dict):
            state.setdefault("browser_runtime", {})
            state["browser_runtime"][browser_memory.get("provider") or "generic"] = {
                **browser_memory,
                "checked_at": now_iso(),
            }
        save_state(state)
    return redirect(url_for("index", generic_dialog=1))


@app.post("/generic/products")
def add_generic_product() -> Response:
    config = load_config()
    state = load_state()
    analysis = state.get("generic_analysis") or {}
    candidates = analysis.get("candidates") or []
    if not analysis.get("url") or not candidates:
        set_notice("Keine Generic-Analyse zum Speichern gefunden.")
        return redirect(url_for("index"))

    try:
        candidate_index = int(request.form.get("candidate_index", "0"))
    except ValueError:
        candidate_index = 0
    candidate = next((item for item in candidates if int(item.get("index") or 0) == candidate_index), candidates[0])
    category_id = request.form.get("category_id") or analysis.get("category_id") or DEFAULT_CATEGORY_ID
    requested_id = request.form.get("id", "").strip() or analysis.get("requested_id", "")
    article_number = generic_reader.article_number_from_url(str(analysis["url"]))
    title = str(analysis.get("title") or article_number)
    market_id = "online"
    provider = "generic"

    products = config.setdefault("products", [])
    if any(
        product.get("article_number") == article_number
        and product.get("market_id") == market_id
        and product_provider(config, product) == provider
        and int(product.get("generic_candidate_index") or (product.get("generic") or {}).get("candidate_index") or 0)
        == candidate_index
        for product in products
    ):
        set_notice("Dieser Generic-Artikel ist bereits vorhanden.")
        return redirect(url_for("index"))

    product_id = requested_id or product_id_from(article_number, title)
    product_id = unique_product_id(products, product_id, article_number, market_id, provider)
    product = {
        "id": product_id,
        "article_number": article_number,
        "name": title,
        "category_id": category_id,
        "provider": provider,
        "market_id": market_id,
        "product_url": str(analysis["url"]),
        "generic_candidate_index": str(candidate_index),
        "generic_source": str(analysis.get("source") or "http"),
        "generic_initial_context": str(candidate.get("context") or ""),
    }
    products.append(product)
    save_config(config)

    result = {
        "id": product_id,
        "name": title,
        "title": title,
        "article_number": article_number,
        "provider_article_number": article_number,
        "price": int(candidate["price_cents"]) / 100,
        "price_cents": int(candidate["price_cents"]),
        "price_text": generic_reader.euro_text_from_cents(int(candidate["price_cents"])),
        "currency": "EUR",
        "unit_price": None,
        "available_service": "ONLINE",
        "market_id": market_id,
        "seller": "Generic",
        "url": str(analysis["url"]),
        "generic_candidate": candidate,
    }
    update_state_for_product(product, result, None)
    with state_lock:
        state = load_state()
        state.pop("generic_analysis", None)
        save_state(state)
    set_notice(f"Artikel erstellt: {title}")
    return redirect(url_for("index"))


@app.post("/products/<product_id>/delete")
def delete_product(product_id: str) -> Response:
    config = load_config()
    config["products"] = [product for product in config.get("products", []) if product["id"] != product_id]
    save_config(config)
    with state_lock:
        state = load_state()
        state.setdefault("products", {}).pop(product_id, None)
        save_state(state)
    return redirect(url_for("index"))


@app.post("/products/<product_id>/category")
def change_product_category(product_id: str) -> Response:
    config = load_config()
    categories = categories_from_config(config)
    category_id = request.form.get("category_id", DEFAULT_CATEGORY_ID).strip() or DEFAULT_CATEGORY_ID
    if not any(category["id"] == category_id for category in categories):
        category_id = DEFAULT_CATEGORY_ID
    target_price_raw = request.form.get("target_price", "").strip()
    target_price_cents = parse_price_cents(target_price_raw) if target_price_raw else None
    invalid_target_price = bool(target_price_raw and target_price_cents is None)
    for product in config.get("products", []):
        if product.get("id") == product_id:
            product["category_id"] = category_id
            if target_price_raw:
                if target_price_cents is not None:
                    product["target_price_cents"] = target_price_cents
                else:
                    set_notice("Wunschpreis konnte nicht gelesen werden. Bitte z. B. 1,23 oder 1.23 verwenden.")
            else:
                product.pop("target_price_cents", None)
            if request.form.get("enabled") == "true":
                product.pop("enabled", None)
            else:
                product["enabled"] = "false"
            break
    save_config(config)
    if invalid_target_price:
        return redirect(url_for("index", category=category_id, _anchor=f"product-{product_id}"))
    return redirect(url_for("index", category=category_id, _anchor=f"product-{product_id}"))


@app.post("/categories")
def create_category() -> Response:
    config = load_config()
    categories = categories_from_config(config)
    config["categories"] = categories
    name = request.form.get("name", "").strip()
    if name:
        category = {"id": unique_category_id(categories, name), "name": name}
        color = category_color_from_form()
        if color:
            category["color"] = color
        if request.form.get("quick_cat") == "true":
            category["quick_cat"] = "true"
        categories.append(category)
        save_config(config)
    return redirect(url_for("index", categories_dialog=1))


@app.post("/categories/<category_id>/rename")
def rename_category(category_id: str) -> Response:
    config = load_config()
    name = request.form.get("name", "").strip()
    if name:
        categories = categories_from_config(config)
        config["categories"] = categories
        for category in categories:
            if category.get("id") == category_id:
                category["name"] = name
                if request.form.get("clear_color") == "true":
                    category.pop("color", None)
                else:
                    color = category_color_from_form()
                    if color:
                        category["color"] = color
                    else:
                        category.pop("color", None)
                if request.form.get("quick_cat") == "true":
                    category["quick_cat"] = "true"
                else:
                    category.pop("quick_cat", None)
                break
        save_config(config)
    return redirect(url_for("index", categories_dialog=1))


@app.post("/categories/<category_id>/delete")
def delete_category(category_id: str) -> Response:
    if category_id == DEFAULT_CATEGORY_ID:
        set_notice("Allgemein kann nicht gelöscht werden.")
        return redirect(url_for("index", categories_dialog=1))

    config = load_config()
    categories = categories_from_config(config)
    if not any(category["id"] == category_id for category in categories):
        return redirect(url_for("index", categories_dialog=1))

    delete_action = request.form.get("delete_action", "general")
    target_category_id = request.form.get("target_category_id", DEFAULT_CATEGORY_ID).strip() or DEFAULT_CATEGORY_ID
    if target_category_id == category_id or not any(category["id"] == target_category_id for category in categories):
        target_category_id = DEFAULT_CATEGORY_ID

    if delete_action == "delete_products":
        deleted_ids = {
            str(product.get("id"))
            for product in config.get("products", [])
            if product_category_id(product) == category_id
        }
        config["products"] = [
            product for product in config.get("products", []) if product_category_id(product) != category_id
        ]
        state = load_state()
        product_state = state.get("products") or {}
        for product_id in deleted_ids:
            product_state.pop(product_id, None)
        state["products"] = product_state
        save_state(state)
    else:
        for product in config.get("products", []):
            if product_category_id(product) == category_id:
                product["category_id"] = target_category_id

    config["categories"] = [category for category in categories if category["id"] != category_id]
    save_config(config)
    return redirect(url_for("index", categories_dialog=1))


@app.get("/health")
def health() -> Response:
    return jsonify({"ok": True})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5050, debug=True)
