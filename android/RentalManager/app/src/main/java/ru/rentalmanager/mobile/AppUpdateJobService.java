package ru.rentalmanager.mobile;

import android.app.job.JobParameters;
import android.app.job.JobService;

public final class AppUpdateJobService extends JobService {
    @Override public boolean onStartJob(JobParameters params) {
        AppUpdates.check(this, false, false, () -> jobFinished(params, false));
        return true;
    }

    @Override public boolean onStopJob(JobParameters params) {
        return true;
    }
}
