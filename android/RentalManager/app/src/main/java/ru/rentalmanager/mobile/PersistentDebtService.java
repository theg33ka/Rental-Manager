package ru.rentalmanager.mobile;

import android.app.Service;
import android.content.Intent;
import android.os.IBinder;

public class PersistentDebtService extends Service {
    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        // Старый сервис завершается после обновления; сводку показывает NotificationManager.
        stopForeground(true);
        stopSelf(startId);
        return START_NOT_STICKY;
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }
}
