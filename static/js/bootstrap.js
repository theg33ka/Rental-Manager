import * as apiClient from "./api.js";

window.RentalApi = apiClient;
const script = document.createElement("script");
script.src = "/static/app.js?v=2026-08-30-pen-v1";
script.defer = true;
document.body.append(script);
