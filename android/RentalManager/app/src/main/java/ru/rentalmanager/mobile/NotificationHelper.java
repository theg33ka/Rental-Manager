package ru.rentalmanager.mobile;

import android.Manifest;
import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.os.Build;

final class NotificationHelper {
    static final String CHANNEL_REMINDERS = "rental_manager_reminders_sound_v2";
    static final String CHANNEL_REMINDERS_VIBRATE = "rental_manager_reminders_vibrate_v2";
    static final String CHANNEL_REMINDERS_SILENT = "rental_manager_reminders_silent_v2";
    static final String CHANNEL_STICKY = "rental_manager_status_panel_v3";
    static final int NOTIFICATION_DIGEST = 5101;
    static final int NOTIFICATION_STICKY_DEBT = 5102;
    static final String EXTRA_TAB = "notification_tab";
    static final String EXTRA_ACTION = "notification_action";

    private NotificationHelper() {
    }

    static void ensureChannels(Context context) {
        if (Build.VERSION.SDK_INT < 26) return;
        NotificationManager manager = (NotificationManager) context.getSystemService(Context.NOTIFICATION_SERVICE);
        if (manager == null) return;
        NotificationChannel reminders = new NotificationChannel(
            CHANNEL_REMINDERS,
            "Напоминания со звуком",
            NotificationManager.IMPORTANCE_HIGH
        );
        reminders.setDescription("Аренда, коммуналка, отчёты и проверки пульта.");
        reminders.enableVibration(true);
        reminders.setLockscreenVisibility(Notification.VISIBILITY_PRIVATE);
        reminders.setLightColor(Color.rgb(37, 109, 90));
        manager.createNotificationChannel(reminders);

        NotificationChannel vibrate = new NotificationChannel(
            CHANNEL_REMINDERS_VIBRATE,
            "Напоминания с вибрацией",
            NotificationManager.IMPORTANCE_HIGH
        );
        vibrate.setDescription("Без звука, но с вибрацией.");
        vibrate.setSound(null, null);
        vibrate.enableVibration(true);
        vibrate.setLockscreenVisibility(Notification.VISIBILITY_PRIVATE);
        manager.createNotificationChannel(vibrate);

        NotificationChannel silent = new NotificationChannel(
            CHANNEL_REMINDERS_SILENT,
            "Тихие напоминания",
            NotificationManager.IMPORTANCE_LOW
        );
        silent.setDescription("Проверки пульта без звука и вибрации.");
        silent.setSound(null, null);
        silent.enableVibration(false);
        silent.setLockscreenVisibility(Notification.VISIBILITY_PRIVATE);
        manager.createNotificationChannel(silent);

        NotificationChannel sticky = new NotificationChannel(
            CHANNEL_STICKY,
            "Статус Rental Manager",
            NotificationManager.IMPORTANCE_LOW
        );
        sticky.setDescription("Компактная статус-панель, пока есть квартиры-должники без отсрочки.");
        sticky.enableVibration(false);
        sticky.setSound(null, null);
        sticky.setLockscreenVisibility(Notification.VISIBILITY_PRIVATE);
        sticky.setLightColor(Color.rgb(255, 69, 58));
        manager.createNotificationChannel(sticky);
    }

    static synchronized void notifyDigest(Context context, DashboardDigest digest, boolean manual) {
        ensureChannels(context);
        updateStickyDebt(context, digest);
        boolean healthy = digest.networkOk && digest.authorized;
        int decision = NotificationPolicy.decision(NotificationPrefs.notificationsEnabled(context),
            canPostNotifications(context), manual, isQuietNow(context), healthy, digest.hasAlerts(),
            digest.fingerprint(), NotificationPrefs.lastDigest(context));
        if (decision == NotificationPolicy.CANCEL) {
            cancel(context, NOTIFICATION_DIGEST);
            if (healthy && !digest.hasAlerts()) NotificationPrefs.rememberDigest(context, "");
            return;
        }
        if (decision == NotificationPolicy.SKIP) return;
        NotificationManager manager = (NotificationManager) context.getSystemService(Context.NOTIFICATION_SERVICE);
        if (manager == null) return;
        boolean silent = decision == NotificationPolicy.POST_SILENT;
        String channel = silent ? CHANNEL_REMINDERS_SILENT : reminderChannel(context);
        Notification.Builder builder = baseBuilder(context, channel)
            .setSmallIcon(R.drawable.ic_stat_rental)
            .setContentTitle(digest.title())
            .setContentText(digest.text())
            .setStyle(new Notification.BigTextStyle().bigText(digest.bigText()))
            .setContentIntent(openAppIntent(context, digest.primaryTarget()))
            .setCategory(Notification.CATEGORY_REMINDER)
            .setVisibility(Notification.VISIBILITY_PRIVATE)
            .setPublicVersion(privatePreview(context, channel))
            .setAutoCancel(true)
            .setShowWhen(true);
        for (int i = 0; i < digest.targets.size() && i < 3; i++) {
            DashboardDigest.Target target = digest.targets.get(i);
            builder.addAction(0, target.label, openAppIntent(context, target));
        }
        if (silent) builder.setPriority(Notification.PRIORITY_LOW).setDefaults(0).setSound(null).setVibrate(null);
        else applyMode(context, builder);
        manager.notify(NOTIFICATION_DIGEST, builder.build());
        if (healthy) NotificationPrefs.rememberDigest(context, digest.fingerprint());
    }

    static boolean updateStickyDebt(Context context, DashboardDigest digest) {
        context.stopService(new Intent(context, PersistentDebtService.class));
        if (!NotificationPolicy.showSticky(NotificationPrefs.notificationsEnabled(context),
                NotificationPrefs.stickyDebtEnabled(context), canPostNotifications(context),
                digest.networkOk && digest.authorized, digest.debtorApartmentCount)) {
            cancel(context, NOTIFICATION_STICKY_DEBT);
            return false;
        }
        NotificationManager manager = (NotificationManager) context.getSystemService(Context.NOTIFICATION_SERVICE);
        if (manager == null) return false;
        manager.notify(NOTIFICATION_STICKY_DEBT, stickyNotification(context,
            "Квартиры с долгами: " + digest.debtorApartmentCount, "Откройте оплаты, чтобы проверить задолженность."));
        return true;
    }

    static Notification stickyNotification(Context context, String title, String text) {
        ensureChannels(context);
        return baseBuilder(context, CHANNEL_STICKY)
            .setSmallIcon(R.drawable.ic_stat_rental)
            .setContentTitle(title)
            .setContentText(text)
            .setSubText("Долги по выбранным категориям")
            .setContentIntent(openAppIntent(context, new DashboardDigest.Target("payments", "", "Оплаты")))
            .setCategory(Notification.CATEGORY_STATUS)
            .setVisibility(Notification.VISIBILITY_PRIVATE)
            .setPublicVersion(privatePreview(context, CHANNEL_STICKY))
            .setPriority(Notification.PRIORITY_LOW)
            .setDefaults(0)
            .setOnlyAlertOnce(true)
            .setOngoing(true)
            .setAutoCancel(false)
            .setShowWhen(false)
            .build();
    }

    static synchronized void refreshPreferences(Context context) {
        cancel(context, NOTIFICATION_DIGEST);
        cancel(context, NOTIFICATION_STICKY_DEBT);
        context.stopService(new Intent(context, PersistentDebtService.class));
    }

    private static Notification privatePreview(Context context, String channel) {
        return baseBuilder(context, channel).setSmallIcon(R.drawable.ic_stat_rental)
            .setContentTitle("Rental Manager").setContentText("Откройте приложение для просмотра")
            .setVisibility(Notification.VISIBILITY_PUBLIC).build();
    }

    static void cancel(Context context, int id) {
        NotificationManager manager = (NotificationManager) context.getSystemService(Context.NOTIFICATION_SERVICE);
        if (manager != null) manager.cancel(id);
    }

    private static Notification.Builder baseBuilder(Context context, String channelId) {
        if (Build.VERSION.SDK_INT >= 26) {
            return new Notification.Builder(context, channelId);
        }
        return new Notification.Builder(context);
    }

    private static String reminderChannel(Context context) {
        String mode = NotificationPrefs.mode(context);
        if (NotificationPrefs.MODE_SILENT.equals(mode)) return CHANNEL_REMINDERS_SILENT;
        if (NotificationPrefs.MODE_VIBRATE.equals(mode)) return CHANNEL_REMINDERS_VIBRATE;
        return CHANNEL_REMINDERS;
    }

    private static void applyMode(Context context, Notification.Builder builder) {
        String mode = NotificationPrefs.mode(context);
        if (NotificationPrefs.MODE_SILENT.equals(mode)) {
            builder.setPriority(Notification.PRIORITY_LOW).setDefaults(0).setSound(null).setVibrate(null);
            return;
        }
        if (NotificationPrefs.MODE_VIBRATE.equals(mode)) {
            builder.setPriority(Notification.PRIORITY_HIGH).setDefaults(Notification.DEFAULT_VIBRATE);
            return;
        }
        builder.setPriority(Notification.PRIORITY_HIGH).setDefaults(Notification.DEFAULT_ALL);
    }

    private static PendingIntent openAppIntent(Context context, DashboardDigest.Target target) {
        Intent intent = new Intent(context, MainActivity.class);
        intent.setFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_CLEAR_TOP | Intent.FLAG_ACTIVITY_SINGLE_TOP);
        intent.putExtra(EXTRA_TAB, target.tab);
        intent.putExtra(EXTRA_ACTION, target.action);
        int flags = PendingIntent.FLAG_UPDATE_CURRENT;
        if (Build.VERSION.SDK_INT >= 23) flags |= PendingIntent.FLAG_IMMUTABLE;
        return PendingIntent.getActivity(context, (target.tab + ":" + target.action).hashCode(), intent, flags);
    }

    private static boolean canPostNotifications(Context context) {
        NotificationManager manager = (NotificationManager) context.getSystemService(Context.NOTIFICATION_SERVICE);
        if (manager == null || Build.VERSION.SDK_INT >= 24 && !manager.areNotificationsEnabled()) return false;
        return Build.VERSION.SDK_INT < 33
            || context.checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED;
    }

    private static boolean isQuietNow(Context context) {
        int start = NotificationPolicy.minutes(NotificationPrefs.quietStart(context), 22 * 60);
        int end = NotificationPolicy.minutes(NotificationPrefs.quietEnd(context), 8 * 60);
        java.util.Calendar calendar = java.util.Calendar.getInstance();
        int now = calendar.get(java.util.Calendar.HOUR_OF_DAY) * 60 + calendar.get(java.util.Calendar.MINUTE);
        return NotificationPolicy.isQuiet(now, start, end);
    }
}
