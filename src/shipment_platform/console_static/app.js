const elements = {
  configurationBadge: document.querySelector("#configurationBadge"),
  apiUrl: document.querySelector("#apiUrl"),
  tokenHost: document.querySelector("#tokenHost"),
  tokenState: document.querySelector("#tokenState"),
  refreshToken: document.querySelector("#refreshToken"),
  newKey: document.querySelector("#newKey"),
  shipmentForm: document.querySelector("#shipmentForm"),
  idempotencyKey: document.querySelector("#idempotencyKey"),
  shipmentJson: document.querySelector("#shipmentJson"),
  lookupForm: document.querySelector("#lookupForm"),
  shipmentId: document.querySelector("#shipmentId"),
  resultTitle: document.querySelector("#resultTitle"),
  httpStatus: document.querySelector("#httpStatus"),
  resultBody: document.querySelector("#resultBody"),
};

function newIdempotencyKey() {
  const randomPart = crypto.randomUUID().replaceAll("-", "");
  elements.idempotencyKey.value = `console-${randomPart}`;
}

function setBusy(button, busy) {
  button.disabled = busy;
}

function showResult(title, status, body) {
  elements.resultTitle.textContent = title;
  elements.httpStatus.textContent = status ? `HTTP ${status}` : "—";
  elements.httpStatus.className = `status-code ${
    status >= 200 && status < 300 ? "success" : "failure"
  }`;
  elements.resultBody.textContent =
    typeof body === "string" ? body : JSON.stringify(body, null, 2);
}

async function request(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
  });
  const body = await response.json().catch(() => ({
    detail: "Console returned a non-JSON response",
  }));
  return { response, body };
}

async function loadConfiguration() {
  try {
    const { response, body } = await request("/api/config");
    if (!response.ok) {
      throw new Error(body.detail || "Configuration check failed");
    }

    elements.apiUrl.textContent = body.api_url || "Not configured";
    elements.tokenHost.textContent = body.token_host || "Not configured";
    elements.tokenState.textContent = body.has_usable_token
      ? `Valid until ${new Date(body.expires_at).toLocaleTimeString()}`
      : "Not requested";
    elements.configurationBadge.textContent = body.configured
      ? `Configured · client …${body.client_id_suffix}`
      : "Configuration required";
    elements.configurationBadge.className = `badge ${
      body.configured ? "good" : "bad"
    }`;
  } catch (error) {
    elements.configurationBadge.textContent = "Console unavailable";
    elements.configurationBadge.className = "badge bad";
    showResult("Configuration error", 0, error.message);
  }
}

elements.refreshToken.addEventListener("click", async () => {
  setBusy(elements.refreshToken, true);
  try {
    const { response, body } = await request("/api/token/refresh", {
      method: "POST",
      body: "{}",
    });
    showResult("Token refresh", response.status, body);
    await loadConfiguration();
  } catch (error) {
    showResult("Token refresh failed", 0, error.message);
  } finally {
    setBusy(elements.refreshToken, false);
  }
});

elements.newKey.addEventListener("click", newIdempotencyKey);

elements.shipmentForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = event.submitter;
  setBusy(button, true);
  try {
    let shipment;
    try {
      shipment = JSON.parse(elements.shipmentJson.value);
    } catch {
      showResult("Invalid JSON", 0, "Correct the shipment JSON and try again.");
      return;
    }

    const { response, body } = await request("/api/shipments", {
      method: "POST",
      body: JSON.stringify({
        idempotency_key: elements.idempotencyKey.value,
        shipment,
      }),
    });
    showResult("Create shipment", response.status, body);
    if (body.shipment_id) {
      elements.shipmentId.value = body.shipment_id;
    }
    await loadConfiguration();
  } catch (error) {
    showResult("Shipment request failed", 0, error.message);
  } finally {
    setBusy(button, false);
  }
});

elements.lookupForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = event.submitter;
  setBusy(button, true);
  try {
    const shipmentId = encodeURIComponent(elements.shipmentId.value.trim());
    const { response, body } = await request(`/api/shipments/${shipmentId}`);
    showResult("Shipment status", response.status, body);
    await loadConfiguration();
  } catch (error) {
    showResult("Status lookup failed", 0, error.message);
  } finally {
    setBusy(button, false);
  }
});

newIdempotencyKey();
loadConfiguration();
