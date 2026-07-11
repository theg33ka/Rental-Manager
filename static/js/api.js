let unauthorizedHandler = () => {};

export function configureApiClient({ onUnauthorized } = {}) {
  unauthorizedHandler = typeof onUnauthorized === "function" ? onUnauthorized : () => {};
}

function cookieValue(name) {
  const prefix = `${encodeURIComponent(name)}=`;
  const item = document.cookie.split(";").map((value) => value.trim()).find((value) => value.startsWith(prefix));
  return item ? decodeURIComponent(item.slice(prefix.length)) : "";
}

function requestHeaders(options) {
  const headers = { ...(options.headers || {}) };
  const isFormData = typeof FormData !== "undefined" && options.body instanceof FormData;
  if (!isFormData && options.body != null && !headers["Content-Type"]) {
    headers["Content-Type"] = "application/json";
  }
  const method = String(options.method || "GET").toUpperCase();
  if (!["GET", "HEAD", "OPTIONS"].includes(method)) {
    const csrfToken = cookieValue("rental_manager_csrf");
    if (csrfToken) headers["X-CSRF-Token"] = csrfToken;
  }
  return headers;
}

export async function api(path, options = {}) {
  let response;
  try {
    response = await fetch(path, {
      credentials: "same-origin",
      ...options,
      headers: requestHeaders(options),
    });
  } catch {
    throw new Error("Сервер не ответил. Проверьте соединение и повторите запрос.");
  }
  const rawText = await response.text();
  let data = null;
  if (rawText) {
    try {
      data = JSON.parse(rawText);
    } catch {
      data = null;
    }
  }
  if (!response.ok) {
    if (response.status === 401 && !path.startsWith("/api/auth/")) unauthorizedHandler();
    throw new Error(data?.detail || rawText || "Ошибка запроса");
  }
  return data ?? rawText;
}

export async function downloadFile(path, fallbackFilename = "download") {
  const response = await fetch(path, { credentials: "same-origin" });
  if (!response.ok) throw new Error((await response.text()) || "Не удалось скачать файл");
  const blob = await response.blob();
  const contentDisposition = response.headers.get("Content-Disposition") || "";
  const match = contentDisposition.match(/filename="?([^";]+)"?/i);
  saveBlob(blob, (match && match[1]) || fallbackFilename);
}

export function triggerNativeDownload(path, fallbackFilename = "download") {
  const link = document.createElement("a");
  link.href = path;
  link.download = fallbackFilename;
  document.body.append(link);
  link.click();
  link.remove();
}

function saveBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.append(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}
