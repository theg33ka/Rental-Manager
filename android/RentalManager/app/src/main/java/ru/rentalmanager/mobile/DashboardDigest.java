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
    final List<String> eventTokens = new ArrayList<>();
    final List<Target> targets = new ArrayList<>();

    static final class Target {
        final String tab;
        final String action;
        final String label;

        Target(String tab, String action, String label) {
            this.tab = tab;
            this.action = action;
            this.label = label;
        }
    }

    void addTarget(String tab, String action, String label) {
        for (Target target : targets) if (target.tab.equals(tab) && target.action.equals(action)) return;
        targets.add(new Target(tab, action, label));
    }

    Target primaryTarget() {
        return targets.isEmpty() ? new Target("dashboard", "", "Открыть пульт") : targets.get(0);
    }

    String fingerprint() {
        return NotificationPolicy.fingerprint(eventTokens);
    }

    static DashboardDigest unauthorized() {
        DashboardDigest digest = new DashboardDigest();
        digest.authorized = false;
        digest.alertCount = 0;
        digest.lines.add("Откройте приложение и войдите по PIN. Данные не обновлены.");
        digest.addTarget("dashboard", "login", "Войти");
        return digest;
    }

    static DashboardDigest error(String message) {
        DashboardDigest digest = new DashboardDigest();
        digest.networkOk = false;
        digest.error = "Не удалось обновить данные. Проверьте подключение к серверу.";
        digest.lines.add(digest.error);
        digest.addTarget("dashboard", "connection", "Открыть пульт");
        return digest;
    }

    boolean hasAlerts() {
        return alertCount > 0 || debtorApartmentCount > 0;
    }

    String title() {
        if (!networkOk) return "Rental Manager: связи нет";
        if (!authorized) return "Rental Manager: нужен PIN";
        if (alertCount > 0) return "Требуют внимания: " + alertCount;
        return "Rental Manager";
    }

    String text() {
        if (!networkOk || !authorized) {
            return lines.isEmpty() ? "Проверка не выполнена" : lines.get(0);
        }
        if (!lines.isEmpty()) {
            return joinLimited(lines, 4);
        }
        return "Нет задач по выбранным категориям";
    }

    String bigText() {
        List<String> all = new ArrayList<>();
        for (String line : lines) all.add("• " + line);
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
