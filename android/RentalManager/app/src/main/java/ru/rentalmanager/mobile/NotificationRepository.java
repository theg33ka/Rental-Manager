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
import java.util.HashSet;
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
            connection.setInstanceFollowRedirects(false);
            connection.setRequestProperty("Accept", "application/json");
            connection.setRequestProperty("User-Agent", "RentalManagerAndroid/1.0");
            String cookies = new SessionStore(context).read();
            if (cookies == null || cookies.trim().isEmpty()) return DashboardDigest.unauthorized();
            connection.setRequestProperty("Cookie", cookies);
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
        if (dashboard == null) return DashboardDigest.error("missing_dashboard");
        Set<String> enabled = new HashSet<>();
        for (String category : CATEGORIES) {
            String preference = NotificationPolicy.preferenceFor(category);
            if (NotificationPrefs.eventEnabled(context, preference)) enabled.add(preference);
        }
        Set<String> seen = new HashSet<>();
        Set<String> debtors = new LinkedHashSet<>();
        addCategory(digest, dashboard, enabled, seen, debtors, "suspicious_receipts", "Чеки на проверку", "payments", "receipts", false);
        addCategory(digest, dashboard, enabled, seen, debtors, "rent_overdue", "Просроченная аренда", "payments", "rent", true);
        addCategory(digest, dashboard, enabled, seen, debtors, "utility_overdue", "Просроченная коммуналка", "payments", "utilities", true);
        addCategory(digest, dashboard, enabled, seen, debtors, "rent_partial", "Частично оплачена аренда", "payments", "rent", true);
        addCategory(digest, dashboard, enabled, seen, debtors, "utility_partial", "Частично оплачена коммуналка", "payments", "utilities", true);
        addCategory(digest, dashboard, enabled, seen, debtors, "manual_debts", "Ручные долги", "payments", "rent", true);
        addCategory(digest, dashboard, enabled, seen, debtors, "rent_today", "Аренда к оплате сегодня", "payments", "rent", false);
        addCategory(digest, dashboard, enabled, seen, debtors, "utility_issued", "Выставленная коммуналка", "payments", "utilities", false);
        addCategory(digest, dashboard, enabled, seen, debtors, "provider_debts", "Счета поставщиков", "tasks", "utilities", false);
        String readings = dashboard.optJSONArray("provider_reading_due") == null ? "stale_readings" : "provider_reading_due";
        addCategory(digest, dashboard, enabled, seen, debtors, readings, "Нужно передать показания", "tasks", "readings", false);
        addCategory(digest, dashboard, enabled, seen, debtors, "monthly_reports", "Отчёты на проверку", "more", "reports", false);
        digest.debtorApartmentCount = debtors.size();
        return digest;
    }

    private static final String[] CATEGORIES = {
        "rent_overdue", "rent_partial", "rent_today", "utility_overdue", "utility_partial", "utility_issued",
        "manual_debts", "provider_debts", "provider_reading_due", "stale_readings", "suspicious_receipts", "monthly_reports"
    };

    private static void addCategory(DashboardDigest digest, JSONObject dashboard, Set<String> enabled,
                                    Set<String> seen, Set<String> debtors, String category, String label,
                                    String tab, String action, boolean debtCategory) {
        if (!NotificationPolicy.eventEnabled(category, enabled)) return;
        JSONArray items = dashboard.optJSONArray(category);
        if (items == null) return;
        int count = 0;
        for (int i = 0; i < items.length(); i++) {
            JSONObject item = items.optJSONObject(i);
            if (item == null) continue;
            // Серверный utility_issued также содержит просрочку; категории не обходят настройки друг друга.
            if ("utility_issued".equals(category) && !"issued".equals(item.optString("status"))) continue;
            String identity = identity(item);
            String family = category.startsWith("rent_") ? "rent" : category.startsWith("utility_") ? "utility" : category;
            if (!seen.add(family + ":" + identity)) continue;
            count++;
            digest.eventTokens.add(NotificationPolicy.eventToken(category, identity, stableFields(item,
                "status", "debt", "due_date", "period_start", "period_end", "reading_date", "last_date", "kind")));
            if (debtCategory) debtors.add(stableFields(item, "object", "apartment"));
        }
        if (count == 0) return;
        digest.alertCount += count;
        digest.lines.add(label + ": " + count);
        String targetLabel = "receipts".equals(action) ? "Проверить чеки" : "readings".equals(action) ? "Показания"
            : "reports".equals(action) ? "Отчёты" : "utilities".equals(action) ? "Коммуналка" : "Аренда";
        digest.addTarget(tab, action, targetLabel);
    }

    private static String identity(JSONObject item) {
        if (item.has("key")) return "key:" + item.optString("key");
        if (item.has("id")) return "id:" + item.optString("id");
        if (item.has("service_id")) return "service:" + item.optString("service_id");
        return stableFields(item, "object", "apartment", "service", "lease_id", "period_start", "period_end", "due_date");
    }

    private static String stableFields(JSONObject item, String... fields) {
        StringBuilder state = new StringBuilder();
        for (String field : fields) {
            String value = item.isNull(field) ? "" : item.optString(field, "");
            state.append(field).append('=').append(value.length()).append(':').append(value).append(';');
        }
        return state.toString();
    }

    private static String readAll(InputStream stream) throws Exception {
        try (BufferedReader reader = new BufferedReader(new InputStreamReader(stream, StandardCharsets.UTF_8))) {
            StringBuilder builder = new StringBuilder();
            String line;
            while ((line = reader.readLine()) != null) builder.append(line);
            return builder.toString();
        }
    }
}
