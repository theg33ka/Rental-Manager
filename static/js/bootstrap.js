import * as apiClient from "./api.js";

window.RentalApi = apiClient;
const script = document.createElement("script");
script.src = "/static/app.js?v=2026-07-11-security";
script.defer = true;
document.body.append(script);
