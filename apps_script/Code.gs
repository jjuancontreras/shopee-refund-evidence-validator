/**
 * Stage 0 intake trigger.
 *
 * Setup (one-time, inside the Google Sheet):
 *   1. Extensions -> Apps Script, paste this file in as Code.gs.
 *   2. Project Settings -> Script Properties -> add key "WEBHOOK_URL" with
 *      your FastAPI URL (e.g. an ngrok/cloudflared tunnel to
 *      http://localhost:8000/refund-requests).
 *   3. Triggers (clock icon) -> Add Trigger -> function "checkAndProcessRows",
 *      event source "From spreadsheet", event type "On edit" (or a
 *      time-driven trigger every few minutes if you prefer polling).
 *   4. Make sure the sheet has a "Processed" column (any name containing
 *      "processed") so a row is only POSTed once.
 */

var SHEET_NAME = "Training Data";
var STATUS_HINT = "status";
var READY_VALUE = "ready";
var PROCESSED_HINT = "processed";

var FIELD_HINTS = {
  order_id: ["order id"],
  shop_id: ["shop id"],
  item_id: ["item id"],
  return_reason: ["return reason"],
  proof: ["image/video link", "image / video link", "proof"]
};

function getWebhookUrl_() {
  var url = PropertiesService.getScriptProperties().getProperty("WEBHOOK_URL");
  if (!url) {
    throw new Error("Set the WEBHOOK_URL script property first (Project Settings > Script Properties).");
  }
  return url;
}

function findColumn_(headers, hints) {
  for (var i = 0; i < headers.length; i++) {
    var h = String(headers[i]).toLowerCase();
    for (var j = 0; j < hints.length; j++) {
      if (h.indexOf(hints[j]) !== -1) return i;
    }
  }
  return -1;
}

function splitList_(raw) {
  if (!raw) return [];
  return raw.toString().split(/[,;]/).map(function (s) { return s.trim(); }).filter(String);
}

function checkAndProcessRows() {
  // SpreadsheetApp.getActiveSheet() is unreliable when called from a
  // trigger (there's no real "active" UI tab during a background
  // execution, so it can silently return the wrong sheet) -- look up the
  // intake sheet by name instead so this always processes the right tab
  // regardless of which sheet was actually edited.
  var sheet = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(SHEET_NAME);
  if (!sheet) {
    throw new Error("Could not find a sheet named \"" + SHEET_NAME + "\" in this spreadsheet.");
  }
  var data = sheet.getDataRange().getValues();

  // The sheet has decorative banner/title rows above the real column
  // headers (a merged "Starter Data Pack" title, a blank row, a "Training
  // Data" label) -- scan down to find the actual header row instead of
  // assuming it's row 1 of the data range.
  var headerRowIndex = -1;
  var statusCol = -1, processedCol = -1, orderCol = -1, shopCol = -1, itemCol = -1, reasonCol = -1, proofCol = -1;
  for (var hr = 0; hr < data.length; hr++) {
    var candidate = data[hr];
    var sc = findColumn_(candidate, [STATUS_HINT]);
    var oc = findColumn_(candidate, FIELD_HINTS.order_id);
    if (sc !== -1 && oc !== -1) {
      headerRowIndex = hr;
      statusCol = sc;
      orderCol = oc;
      processedCol = findColumn_(candidate, [PROCESSED_HINT]);
      shopCol = findColumn_(candidate, FIELD_HINTS.shop_id);
      itemCol = findColumn_(candidate, FIELD_HINTS.item_id);
      reasonCol = findColumn_(candidate, FIELD_HINTS.return_reason);
      proofCol = findColumn_(candidate, FIELD_HINTS.proof);
      break;
    }
  }

  if (headerRowIndex === -1) {
    throw new Error("Could not find required columns (Status / Order ID) anywhere in the sheet.");
  }

  var webhookUrl = getWebhookUrl_();

  for (var r = headerRowIndex + 1; r < data.length; r++) {
    var row = data[r];
    var status = String(row[statusCol] || "").toLowerCase().trim();
    var alreadyProcessed = processedCol !== -1 && String(row[processedCol] || "").toLowerCase() === "yes";
    var orderIdValue = String(row[orderCol] || "").trim();

    // Guards against blank rows below the real data (e.g. a Status column
    // accidentally drag-filled with "Ready" past the end of the actual
    // table) being enqueued as meaningless empty-order_id jobs.
    if (status !== READY_VALUE || alreadyProcessed || !orderIdValue) continue;

    var itemIds = splitList_(row[itemCol]);
    var payload = {
      order_id: orderIdValue,
      shop_id: String(row[shopCol]),
      item_id: String(row[itemCol]),
      return_reason: String(row[reasonCol] || ""),
      proof_filenames: splitList_(row[proofCol]),
      listing_item_ids: itemIds.length ? itemIds : [String(row[itemCol])]
    };

    var response = UrlFetchApp.fetch(webhookUrl, {
      method: "post",
      contentType: "application/json",
      payload: JSON.stringify(payload),
      muteHttpExceptions: true
    });

    Logger.log("POSTed order %s -> %s: %s", payload.order_id, response.getResponseCode(), response.getContentText());

    if (response.getResponseCode() === 200 && processedCol !== -1) {
      sheet.getRange(r + 1, processedCol + 1).setValue("Yes");
    }
  }
}
