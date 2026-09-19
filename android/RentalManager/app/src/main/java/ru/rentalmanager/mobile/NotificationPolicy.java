package ru.rentalmanager.mobile;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.Collection;
import java.util.HashSet;
import java.util.Set;
import java.util.TreeSet;

final class NotificationPolicy {
    static final int CANCEL = 0;
    static final int SKIP = 1;
    static final int POST_SILENT = 2;
    static final int POST_ALERT = 3;

    private NotificationPolicy() { }

    static String preferenceFor(String category) {
        if ("utility_partial".equals(category)) return "utility_overdue";
        if ("provider_reading_due".equals(category)) return "stale_readings";
        if ("rent_overdue".equals(category) || "rent_partial".equals(category)
                || "rent_today".equals(category) || "utility_overdue".equals(category)
                || "utility_issued".equals(category) || "provider_debts".equals(category)
                || "stale_readings".equals(category) || "suspicious_receipts".equals(category)
                || "monthly_reports".equals(category) || "manual_debts".equals(category)) return category;
        return "";
    }

    static boolean eventEnabled(String category, Set<String> enabledPreferences) {
        String preference = preferenceFor(category);
        return !preference.isEmpty() && enabledPreferences.contains(preference);
    }

    static boolean isQuiet(int now, int start, int end) {
        if (start == end) return false;
        return start < end ? now >= start && now < end : now >= start || now < end;
    }

    static int minutes(String value, int fallback) {
        if (value == null || !value.matches("[0-9]{1,2}:[0-9]{2}")) return fallback;
        String[] parts = value.split(":");
        int hour = Integer.parseInt(parts[0]);
        int minute = Integer.parseInt(parts[1]);
        return hour < 24 && minute < 60 ? hour * 60 + minute : fallback;
    }

    static String eventToken(String category, String identity, String state) {
        try {
            byte[] bytes = MessageDigest.getInstance("SHA-256").digest(
                (category + "\n" + identity + "\n" + state).getBytes(StandardCharsets.UTF_8));
            StringBuilder token = new StringBuilder();
            for (byte value : bytes) token.append(String.format(java.util.Locale.ROOT, "%02x", value & 255));
            return token.toString();
        } catch (java.security.NoSuchAlgorithmException impossible) {
            throw new IllegalStateException(impossible);
        }
    }

    static String fingerprint(Collection<String> eventTokens) {
        StringBuilder fingerprint = new StringBuilder();
        for (String token : new TreeSet<>(eventTokens)) {
            if (fingerprint.length() > 0) fingerprint.append('\n');
            fingerprint.append(token);
        }
        return fingerprint.toString();
    }

    static int decision(boolean enabled, boolean permitted, boolean manual, boolean quiet,
                        boolean healthy, boolean hasAlerts, String current, String previous) {
        if (!enabled || !permitted) return CANCEL;
        if (!healthy) return manual ? POST_SILENT : CANCEL;
        if (manual) return quiet ? POST_SILENT : POST_ALERT;
        if (!hasAlerts) return CANCEL;
        if (quiet || current.equals(previous)) return SKIP;
        Set<String> delivered = new HashSet<>();
        for (String token : previous.split("\n")) delivered.add(token);
        for (String token : current.split("\n")) if (!delivered.contains(token)) return POST_ALERT;
        return POST_SILENT;
    }

    static boolean showSticky(boolean enabled, boolean stickyEnabled, boolean permitted,
                              boolean healthy, int debtorApartments) {
        return enabled && stickyEnabled && permitted && healthy && debtorApartments > 0;
    }
}
