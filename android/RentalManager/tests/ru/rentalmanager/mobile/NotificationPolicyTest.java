package ru.rentalmanager.mobile;

import java.util.Arrays;
import java.util.HashSet;

public final class NotificationPolicyTest {
    private static int count;
    private static void check(boolean value, String name) {
        count++;
        if (!value) throw new AssertionError(name);
    }
    public static void main(String[] args) {
        check(NotificationPolicy.isQuiet(23 * 60, 22 * 60, 8 * 60), "overnight evening");
        check(NotificationPolicy.isQuiet(7 * 60, 22 * 60, 8 * 60), "overnight morning");
        check(!NotificationPolicy.isQuiet(8 * 60, 22 * 60, 8 * 60), "quiet end exclusive");
        check(!NotificationPolicy.isQuiet(600, 0, 0), "equal quiet boundaries disabled");
        check(NotificationPolicy.minutes("25:00", 480) == 480, "invalid hour");
        check(NotificationPolicy.minutes("12:61", 480) == 480, "invalid minute");
        check(NotificationPolicy.decision(false, true, true, false, true, true, "a", "") == NotificationPolicy.CANCEL, "master off includes manual");
        check(NotificationPolicy.decision(true, false, false, false, true, true, "a", "") == NotificationPolicy.CANCEL, "permission denied");
        check(NotificationPolicy.decision(true, true, false, false, false, true, "a", "") == NotificationPolicy.CANCEL, "no recurring network noise");
        check(NotificationPolicy.decision(true, true, false, false, true, true, "a", "a") == NotificationPolicy.SKIP, "unchanged dedup");
        check(NotificationPolicy.decision(true, true, false, true, true, true, "b", "a") == NotificationPolicy.SKIP, "quiet defers new event");
        check(NotificationPolicy.decision(true, true, false, false, true, true, "b", "a") == NotificationPolicy.POST_ALERT, "same count new identity alerts");
        check(NotificationPolicy.decision(true, true, false, false, true, true, "a", "a\nb") == NotificationPolicy.POST_SILENT, "resolved events silent");
        check(NotificationPolicy.decision(true, true, true, true, true, true, "a", "") == NotificationPolicy.POST_SILENT, "manual quiet");
        check(!NotificationPolicy.showSticky(false, true, true, true, 2), "master disables sticky");
        check(!NotificationPolicy.showSticky(true, true, true, false, 2), "stale sticky hidden");
        check(NotificationPolicy.eventEnabled("utility_partial", new HashSet<>(Arrays.asList("utility_overdue"))), "partial utility toggle");
        check(!NotificationPolicy.eventEnabled("rent_overdue", new HashSet<>()), "category off");
        check(NotificationPolicy.fingerprint(Arrays.asList("b", "a", "a")).equals("a\nb"), "stable order and duplicate removal");
        check(!NotificationPolicy.eventToken("rent", "1", "pending").equals(NotificationPolicy.eventToken("rent", "2", "pending")), "identity matters");
        System.out.println("Notification policy: " + count + " assertions passed");
    }
}
