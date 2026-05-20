package ru.rentalmanager.mobile;

import android.app.job.JobInfo;
import android.app.job.JobScheduler;
import android.content.ComponentName;
import android.content.Context;

final class ReminderScheduler {
    static final int JOB_ID = 6201;
    private static final long MIN_PERIOD_MS = 15L * 60L * 1000L;

    private ReminderScheduler() {
    }

    static void schedule(Context context) {
        JobScheduler scheduler = (JobScheduler) context.getSystemService(Context.JOB_SCHEDULER_SERVICE);
        if (scheduler == null) return;
        if (!NotificationPrefs.notificationsEnabled(context)) {
            scheduler.cancel(JOB_ID);
            return;
        }
        long period = Math.max(MIN_PERIOD_MS, NotificationPrefs.intervalMinutes(context) * 60L * 1000L);
        JobInfo info = new JobInfo.Builder(JOB_ID, new ComponentName(context, ReminderJobService.class))
            .setRequiredNetworkType(JobInfo.NETWORK_TYPE_ANY)
            .setPeriodic(period)
            .setPersisted(true)
            .build();
        scheduler.schedule(info);
    }

    static void cancel(Context context) {
        JobScheduler scheduler = (JobScheduler) context.getSystemService(Context.JOB_SCHEDULER_SERVICE);
        if (scheduler != null) scheduler.cancel(JOB_ID);
        NotificationHelper.cancel(context, NotificationHelper.NOTIFICATION_DIGEST);
    }
}
