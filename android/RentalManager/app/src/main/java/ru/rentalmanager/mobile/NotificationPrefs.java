package ru.rentalmanager.mobile;

import android.content.Context;
import android.content.SharedPreferences;

final class NotificationPrefs {
    static final String PREFS = "rental_manager_mobile";
    static final String KEY_BASE_URL = "base_url";
    static final String DEFAULT_BASE_URL = "https://menedzer-arendy-g33ka.waw0.amvera.tech";
    static final String KEY_NOTIFICATIONS_ENABLED = "notifications_enabled";
    static final String KEY_INTERVAL_MINUTES = "interval_minutes";
    static final String KEY_QUIET_START = "quiet_start";
    static final String KEY_QUIET_END = "quiet_end";
    static final String KEY_MODE = "mode";
    static final String KEY_STICKY_DEBT = "sticky_debt";
    static final String KEY_RENT_OVERDUE = "rent_overdue";
    static final String KEY_RENT_TODAY = "rent_today";
    static final String KEY_RENT_PARTIAL = "rent_partial";
    static final String KEY_UTILITY_OVERDUE = "utility_overdue";
    static final String KEY_UTILITY_ISSUED = "utility_issued";
    static final String KEY_PROVIDER_DEBTS = "provider_debts";
    static final String KEY_STALE_READINGS = "stale_readings";
    static final String KEY_SUSPICIOUS_RECEIPTS = "suspicious_receipts";
    static final String KEY_MONTHLY_REPORTS = "monthly_reports";
    static final String KEY_MANUAL_DEBTS = "manual_debts";

    static final String MODE_LOUD = "loud";
    static final String MODE_VIBRATE = "vibrate";
    static final String MODE_SILENT = "silent";

    private NotificationPrefs() {
    }

    static SharedPreferences prefs(Context context) {
        return context.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    static String baseUrl(Context context) {
        String fallback = context.getString(R.string.default_base_url);
        String normalized = normalizeBaseUrl(prefs(context).getString(KEY_BASE_URL, fallback));
        if (isLoopbackBaseUrl(normalized)) {
            String deployed = normalizeBaseUrl(fallback);
            if (isLoopbackBaseUrl(deployed)) deployed = DEFAULT_BASE_URL;
            prefs(context).edit().putString(KEY_BASE_URL, deployed).apply();
            return deployed;
        }
        return normalized;
    }

    static void setBaseUrl(Context context, String value) {
        prefs(context).edit().putString(KEY_BASE_URL, normalizeBaseUrl(value)).apply();
    }

    static String normalizeBaseUrl(String value) {
        String url = value == null ? "" : value.trim();
        while (url.endsWith("/")) {
            url = url.substring(0, url.length() - 1);
        }
        if (url.isEmpty()) {
            return DEFAULT_BASE_URL;
        }
        if (!url.startsWith("http://") && !url.startsWith("https://")) {
            url = "https://" + url;
        }
        return url;
    }

    static boolean isLoopbackBaseUrl(String value) {
        String url = value == null ? "" : value.trim().toLowerCase();
        return url.startsWith("http://127.0.0.1")
            || url.startsWith("https://127.0.0.1")
            || url.startsWith("http://localhost")
            || url.startsWith("https://localhost");
    }

    static boolean notificationsEnabled(Context context) {
        return prefs(context).getBoolean(KEY_NOTIFICATIONS_ENABLED, true);
    }

    static int intervalMinutes(Context context) {
        return Math.max(15, prefs(context).getInt(KEY_INTERVAL_MINUTES, 60));
    }

    static boolean stickyDebtEnabled(Context context) {
        return prefs(context).getBoolean(KEY_STICKY_DEBT, true);
    }

    static String mode(Context context) {
        return prefs(context).getString(KEY_MODE, MODE_LOUD);
    }

    static boolean eventEnabled(Context context, String key) {
        return prefs(context).getBoolean(key, true);
    }

    static String quietStart(Context context) {
        return prefs(context).getString(KEY_QUIET_START, "22:00");
    }

    static String quietEnd(Context context) {
        return prefs(context).getString(KEY_QUIET_END, "08:00");
    }

}
