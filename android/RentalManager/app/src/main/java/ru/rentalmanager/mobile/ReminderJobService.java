package ru.rentalmanager.mobile;

import android.app.job.JobParameters;
import android.app.job.JobService;

public class ReminderJobService extends JobService {
    private volatile int generation;
    private Thread worker;

    @Override
    public boolean onStartJob(final JobParameters params) {
        if (!NotificationPrefs.notificationsEnabled(this)) {
            NotificationHelper.refreshPreferences(this);
            return false;
        }
        final int run = ++generation;
        worker = new Thread(new Runnable() {
            @Override
            public void run() {
                DashboardDigest digest = NotificationRepository.fetchDigest(ReminderJobService.this);
                if (run != generation) return;
                NotificationHelper.notifyDigest(ReminderJobService.this, digest, false);
                jobFinished(params, false);
            }
        }, "rental-reminder-job");
        worker.start();
        return true;
    }

    @Override
    public boolean onStopJob(JobParameters params) {
        generation++;
        if (worker != null) worker.interrupt();
        return true;
    }
}
