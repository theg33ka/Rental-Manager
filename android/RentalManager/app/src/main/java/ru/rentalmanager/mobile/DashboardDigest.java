package ru.rentalmanager.mobile;

import java.util.ArrayList;
import java.util.List;

final class DashboardDigest {
    boolean authorized = true;
    boolean networkOk = true;
    String error = "";
    int alertCount = 0;
    int debtorApartmentCount = 0;
    final List<String> lines = new ArrayList<>();
    final List<String> debtorApartments = new ArrayList<>();

    static DashboardDigest unauthorized() {
        DashboardDigest digest = new DashboardDigest();
        digest.authorized = false;
        digest.alertCount = 0;
        digest.lines.add("Нужно войти по PIN в панели");
        return digest;
    }

    static DashboardDigest error(String message) {
        DashboardDigest digest = new DashboardDigest();
        digest.networkOk = false;
        digest.error = message == null ? "Не удалось проверить пульт" : message;
        digest.lines.add(digest.error);
        return digest;
    }

    boolean hasAlerts() {
        return alertCount > 0 || debtorApartmentCount > 0;
    }

    String title() {
        if (!networkOk) return "Rental Manager: связи нет";
        if (!authorized) return "Rental Manager: нужен PIN";
        if (debtorApartmentCount > 0) return "Есть квартиры-должники";
        if (alertCount > 0) return "Пульт просит внимания";
        return "Rental Manager: спокойно";
    }

    String text() {
        if (!networkOk || !authorized) {
            return lines.isEmpty() ? "Проверка не выполнена" : lines.get(0);
        }
        if (!debtorApartments.isEmpty()) {
            return joinLimited(debtorApartments, 4);
        }
        if (!lines.isEmpty()) {
            return joinLimited(lines, 4);
        }
        return "Критичных задач нет. Даже приложение удивилось, но молчит.";
    }

    String bigText() {
        List<String> all = new ArrayList<>();
        if (!debtorApartments.isEmpty()) {
            all.add("Должники без отсрочки:");
            for (String item : debtorApartments) all.add("• " + item);
        }
        if (!lines.isEmpty()) {
            if (!all.isEmpty()) all.add("");
            all.add("Остальное:");
            for (String line : lines) all.add("• " + line);
        }
        if (all.isEmpty()) all.add(text());
        return join(all, "\n");
    }

    private static String joinLimited(List<String> values, int limit) {
        List<String> result = new ArrayList<>();
        for (int i = 0; i < values.size() && i < limit; i++) {
            result.add(values.get(i));
        }
        if (values.size() > limit) {
            result.add("ещё " + (values.size() - limit));
        }
        return join(result, "; ");
    }

    private static String join(List<String> values, String delimiter) {
        StringBuilder builder = new StringBuilder();
        for (int i = 0; i < values.size(); i++) {
            if (i > 0) builder.append(delimiter);
            builder.append(values.get(i));
        }
        return builder.toString();
    }
}
