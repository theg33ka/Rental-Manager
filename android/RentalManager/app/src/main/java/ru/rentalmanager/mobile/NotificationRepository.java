package ru.rentalmanager.mobile;

import android.content.Context;
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
            String cookies = NotificationPrefs.prefs(context).getString(ApiClient.KEY_SESSION_COOKIE, "");
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

        Set<String> debtors = new LinkedHashSet<>();
        collectDebtors(debtors, dashboard.optJSONArray("rent_overdue"));
        collectDebtors(debtors, dashboard.optJSONArray("rent_partial"));
        collectDebtors(debtors, dashboard.optJSONArray("utility_overdue"));
        collectDebtors(debtors, dashboard.optJSONArray("utility_partial"));
        collectDebtors(debtors, dashboard.optJSONArray("manual_debts"));
        digest.debtorApartmentCount = debtors.size();
        if (digest.debtorApartmentCount > 0) {
            digest.alertCount += digest.debtorApartmentCount;
            digest.lines.add("Квартиры с долгами: " + digest.debtorApartmentCount);
        }

        addCount(context, digest, dashboard, "rent_today", NotificationPrefs.KEY_RENT_TODAY, "Срок аренды сегодня");
        addCount(context, digest, dashboard, "rent_deferred", NotificationPrefs.KEY_RENT_OVERDUE, "Отсрочки");
        addCount(context, digest, dashboard, "utility_issued", NotificationPrefs.KEY_UTILITY_ISSUED, "Коммуналка выставлена");
        addCount(context, digest, dashboard, "provider_debts", NotificationPrefs.KEY_PROVIDER_DEBTS, "Поставщик не оплачен");
        addStaleReadings(context, digest, dashboard);
        addCount(context, digest, dashboard, "suspicious_receipts", NotificationPrefs.KEY_SUSPICIOUS_RECEIPTS, "Чеки на проверку");
        addCount(context, digest, dashboard, "monthly_reports", NotificationPrefs.KEY_MONTHLY_REPORTS, "Месячные отчёты");

        return digest;
    }

    private static void addCount(Context context, DashboardDigest digest, JSONObject dashboard, String arrayKey, String prefKey, String label) {
        if (!NotificationPrefs.eventEnabled(context, prefKey)) return;
        int count = dashboard.optJSONArray(arrayKey) == null ? 0 : dashboard.optJSONArray(arrayKey).length();
        if (count <= 0) return;
        digest.alertCount += count;
        digest.lines.add(label + ": " + count);
    }

    private static void addStaleReadings(Context context, DashboardDigest digest, JSONObject dashboard) {
        if (!NotificationPrefs.eventEnabled(context, NotificationPrefs.KEY_STALE_READINGS)) return;
        JSONArray items = dashboard.optJSONArray("stale_readings");
        if (items == null || items.length() == 0) return;
        Set<String> objects = new LinkedHashSet<>();
        for (int i = 0; i < items.length(); i++) {
            JSONObject item = items.optJSONObject(i);
            if (item != null) objects.add(shortObjectName(item.optString("object", "")));
        }
        digest.alertCount += items.length();
        digest.lines.add("Не переданы показания счётчиков: " + join(objects, "/"));
    }

    private static void collectDebtors(Set<String> result, JSONArray items) {
        if (items == null) return;
        for (int i = 0; i < items.length(); i++) {
            JSONObject item = items.optJSONObject(i);
            if (item == null) continue;
            String object = item.optString("object", "").trim();
            String apartment = item.optString("apartment", "").trim();
            String title = (object + " " + apartment).trim();
            if (title.isEmpty()) title = "квартира";
            result.add(title);
        }
    }

    private static String shortObjectName(String value) {
        String lower = value.toLowerCase();
        if (lower.contains("бел") || lower.contains("бд")) return "БД";
        if (lower.contains("чер") || lower.contains("чёр") || lower.contains("чд")) return "ЧД";
        if (lower.contains("бан")) return "Баня";
        return value == null || value.trim().isEmpty() ? "объект" : value.trim();
    }

    private static String join(Set<String> values, String delimiter) {
        StringBuilder builder = new StringBuilder();
        for (String value : values) {
            if (builder.length() > 0) builder.append(delimiter);
            builder.append(value);
        }
        return builder.toString();
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
