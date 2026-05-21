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
        manager.createNotificationChannel(vibrate);

        NotificationChannel silent = new NotificationChannel(
            CHANNEL_REMINDERS_SILENT,
            "Тихие напоминания",
            NotificationManager.IMPORTANCE_LOW
        );
        silent.setDescription("Проверки пульта без звука и вибрации.");
        silent.setSound(null, null);
        silent.enableVibration(false);
        manager.createNotificationChannel(silent);

        NotificationChannel sticky = new NotificationChannel(
            CHANNEL_STICKY,
            "Статус Rental Manager",
            NotificationManager.IMPORTANCE_LOW
        );
        sticky.setDescription("Компактная статус-панель, пока есть квартиры-должники без отсрочки.");
        sticky.enableVibration(false);
        sticky.setSound(null, null);
        sticky.setLightColor(Color.rgb(255, 69, 58));
        manager.createNotificationChannel(sticky);
    }

    static void notifyDigest(Context context, DashboardDigest digest, boolean manual) {
        ensureChannels(context);
        boolean statusPanelActive = updateStickyDebt(context, digest);
        if (statusPanelActive && !manual) {
            cancel(context, NOTIFICATION_DIGEST);
            return;
        }
        if (!NotificationPrefs.notificationsEnabled(context) && !manual) return;
        if (!manual && isQuietNow(context)) return;
        if (!manual && !digest.hasAlerts() && digest.networkOk && digest.authorized) {
            cancel(context, NOTIFICATION_DIGEST);
            return;
        }
        if (!canPostNotifications(context)) return;
        NotificationManager manager = (NotificationManager) context.getSystemService(Context.NOTIFICATION_SERVICE);
        if (manager == null) return;
        Notification.Builder builder = baseBuilder(context, reminderChannel(context))
            .setSmallIcon(R.drawable.ic_stat_rental)
            .setContentTitle(digest.title())
            .setContentText(digest.text())
            .setStyle(new Notification.BigTextStyle().bigText(digest.bigText()))
            .setContentIntent(openAppIntent(context))
            .setCategory(Notification.CATEGORY_REMINDER)
            .setVisibility(Notification.VISIBILITY_PUBLIC)
            .setAutoCancel(true)
            .setShowWhen(true);
        applyMode(context, builder);
        manager.notify(NOTIFICATION_DIGEST, builder.build());
    }

    static boolean updateStickyDebt(Context context, DashboardDigest digest) {
        if (!NotificationPrefs.stickyDebtEnabled(context) || digest.debtorApartmentCount <= 0 || !canPostNotifications(context)) {
            cancel(context, NOTIFICATION_STICKY_DEBT);
            context.stopService(new Intent(context, PersistentDebtService.class));
            return false;
        }
        Intent service = new Intent(context, PersistentDebtService.class);
        service.putExtra("title", "Квартиры с долгами: " + digest.debtorApartmentCount);
        service.putExtra("text", digest.text());
        try {
            if (Build.VERSION.SDK_INT >= 26) {
                context.startForegroundService(service);
            } else {
                context.startService(service);
            }
        } catch (Exception ignored) {
            NotificationManager manager = (NotificationManager) context.getSystemService(Context.NOTIFICATION_SERVICE);
            if (manager != null) manager.notify(NOTIFICATION_STICKY_DEBT, stickyNotification(context, digest.title(), digest.text()));
        }
        return true;
    }

    static Notification stickyNotification(Context context, String title, String text) {
        ensureChannels(context);
        return baseBuilder(context, CHANNEL_STICKY)
            .setSmallIcon(R.drawable.ic_stat_rental)
            .setContentTitle(title)
            .setContentText(text)
            .setSubText("статус")
            .setContentIntent(openAppIntent(context))
            .setCategory(Notification.CATEGORY_STATUS)
            .setVisibility(Notification.VISIBILITY_PUBLIC)
            .setPriority(Notification.PRIORITY_LOW)
            .setDefaults(0)
            .setOnlyAlertOnce(true)
            .setOngoing(true)
            .setAutoCancel(false)
            .setShowWhen(true)
            .build();
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

    private static PendingIntent openAppIntent(Context context) {
        Intent intent = new Intent(context, MainActivity.class);
        intent.setFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_CLEAR_TOP);
        int flags = PendingIntent.FLAG_UPDATE_CURRENT;
        if (Build.VERSION.SDK_INT >= 23) flags |= PendingIntent.FLAG_IMMUTABLE;
        return PendingIntent.getActivity(context, 0, intent, flags);
    }

    private static boolean canPostNotifications(Context context) {
        if (Build.VERSION.SDK_INT < 33) return true;
        return context.checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED;
    }

    private static boolean isQuietNow(Context context) {
        int start = minutes(NotificationPrefs.quietStart(context), 22 * 60);
        int end = minutes(NotificationPrefs.quietEnd(context), 8 * 60);
        java.util.Calendar calendar = java.util.Calendar.getInstance();
        int now = calendar.get(java.util.Calendar.HOUR_OF_DAY) * 60 + calendar.get(java.util.Calendar.MINUTE);
        if (start == end) return false;
        if (start < end) return now >= start && now < end;
        return now >= start || now < end;
    }

    private static int minutes(String value, int fallback) {
        try {
            String[] parts = value.split(":");
            return Math.max(0, Math.min(1439, Integer.parseInt(parts[0]) * 60 + Integer.parseInt(parts[1])));
        } catch (Exception ignored) {
            return fallback;
        }
    }
}
