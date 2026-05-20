package ru.rentalmanager.mobile;

import android.content.Context;
import android.webkit.CookieManager;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.LinkedHashSet;
import java.util.Set;

final class NotificationRepository {
    private NotificationRepository() {
    }

    static DashboardDigest fetchDigest(Context context) {
        String baseUrl = NotificationPrefs.baseUrl(context);
        HttpURLConnection connection = null;
        try {
            URL url = new URL(baseUrl + "/api/bootstrap");
            connection = (HttpURLConnection) url.openConnection();
            connection.setConnectTimeout(9000);
            connection.setReadTimeout(12000);
            connection.setRequestProperty("Accept", "application/json");
            connection.setRequestProperty("User-Agent", "RentalManagerAndroid/1.0");
            String cookies = CookieManager.getInstance().getCookie(baseUrl);
            if (cookies != null && !cookies.trim().isEmpty()) {
                connection.setRequestProperty("Cookie", cookies);
            }
            int status = connection.getResponseCode();
            if (status == 401 || status == 403) {
                return DashboardDigest.unauthorized();
            }
            if (status < 200 || status >= 300) {
                return DashboardDigest.error("Сервер ответил " + status);
            }
            String body = readAll(connection.getInputStream());
            return parseDigest(context, new JSONObject(body));
        } catch (Exception ex) {
            return DashboardDigest.error(ex.getMessage());
        } finally {
            if (connection != null) connection.disconnect();
        }
    }

    private static DashboardDigest parseDigest(Context context, JSONObject payload) {
        DashboardDigest digest = new DashboardDigest();
        JSONObject dashboard = payload.optJSONObject("dashboard");
        if (dashboard == null) {
            digest.lines.add("Дашборд не найден в ответе сервера");
            return digest;
        }

        addCount(context, digest, dashboard, "rent_overdue", NotificationPrefs.KEY_RENT_OVERDUE, "просрочка аренды");
        addCount(context, digest, dashboard, "rent_partial", NotificationPrefs.KEY_RENT_PARTIAL, "частичная аренда");
        addCount(context, digest, dashboard, "rent_today", NotificationPrefs.KEY_RENT_TODAY, "сегодня срок аренды");
        addCount(context, digest, dashboard, "utility_overdue", NotificationPrefs.KEY_UTILITY_OVERDUE, "просрочена коммуналка");
        addCount(context, digest, dashboard, "utility_partial", NotificationPrefs.KEY_UTILITY_OVERDUE, "частичная коммуналка");
        addCount(context, digest, dashboard, "utility_issued", NotificationPrefs.KEY_UTILITY_ISSUED, "коммуналка выставлена");
        addCount(context, digest, dashboard, "provider_debts", NotificationPrefs.KEY_PROVIDER_DEBTS, "поставщик не закрыт");
        addCount(context, digest, dashboard, "stale_readings", NotificationPrefs.KEY_STALE_READINGS, "счётчики давно молчат");
        addCount(context, digest, dashboard, "suspicious_receipts", NotificationPrefs.KEY_SUSPICIOUS_RECEIPTS, "подозрительные чеки");
        addCount(context, digest, dashboard, "monthly_reports", NotificationPrefs.KEY_MONTHLY_REPORTS, "открытые месячные отчёты");
        addCount(context, digest, dashboard, "manual_debts", NotificationPrefs.KEY_MANUAL_DEBTS, "ручные долги");

        Set<String> debtors = new LinkedHashSet<>();
        collectDebtors(debtors, dashboard.optJSONArray("rent_overdue"));
        collectDebtors(debtors, dashboard.optJSONArray("rent_partial"));
        collectDebtors(debtors, dashboard.optJSONArray("utility_overdue"));
        collectDebtors(debtors, dashboard.optJSONArray("utility_partial"));
        collectDebtors(debtors, dashboard.optJSONArray("manual_debts"));
        digest.debtorApartments.addAll(debtors);
        digest.debtorApartmentCount = debtors.size();

        return digest;
    }

    private static void addCount(Context context, DashboardDigest digest, JSONObject dashboard, String arrayKey, String prefKey, String label) {
        if (!NotificationPrefs.eventEnabled(context, prefKey)) return;
        int count = dashboard.optJSONArray(arrayKey) == null ? 0 : dashboard.optJSONArray(arrayKey).length();
        if (count <= 0) return;
        digest.alertCount += count;
        digest.lines.add(label + ": " + count);
    }

    private static void collectDebtors(Set<String> result, JSONArray items) {
        if (items == null) return;
        for (int i = 0; i < items.length(); i++) {
            JSONObject item = items.optJSONObject(i);
            if (item == null) continue;
            String object = item.optString("object", "").trim();
            String apartment = item.optString("apartment", "").trim();
            String tenant = item.optString("tenant", "").trim();
            String title = (object + " " + apartment).trim();
            if (title.isEmpty()) title = "квартира";
            if (!tenant.isEmpty()) title = title + " — " + tenant;
            result.add(title);
        }
    }

    private static String readAll(InputStream stream) throws Exception {
        BufferedReader reader = new BufferedReader(new InputStreamReader(stream, StandardCharsets.UTF_8));
        StringBuilder builder = new StringBuilder();
        String line;
        while ((line = reader.readLine()) != null) {
            builder.append(line);
        }
        return builder.toString();
    }
}
