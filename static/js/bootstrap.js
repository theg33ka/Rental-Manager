import * as apiClient from "./api.js";

window.RentalApi = apiClient;
const script = document.createElement("script");
script.src = "/static/app.js?v=2026-07-21-tenant-debts-v6";
script.defer = true;
document.body.append(script);
