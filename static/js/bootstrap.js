import * as apiClient from "./api.js";
import { createPaymentCalendar } from "./payment-calendar.js?v=2026-09-18-calendar-v2";

window.RentalApi = apiClient;
window.createPaymentCalendar = createPaymentCalendar;
const script = document.createElement("script");
script.src = "/static/app.js?v=2026-09-18-calendar-v2";
script.defer = true;
document.body.append(script);
