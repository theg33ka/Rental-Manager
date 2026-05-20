package ru.rentalmanager.mobile;

import android.app.job.JobParameters;
import android.app.job.JobService;

public class ReminderJobService extends JobService {
    @Override
    public boolean onStartJob(final JobParameters params) {
        new Thread(new Runnable() {
            @Override
            public void run() {
                DashboardDigest digest = NotificationRepository.fetchDigest(ReminderJobService.this);
                NotificationHelper.notifyDigest(ReminderJobService.this, digest, false);
                jobFinished(params, false);
            }
        }, "rental-reminder-job").start();
        return true;
    }

    @Override
    public boolean onStopJob(JobParameters params) {
        return true;
    }
}
