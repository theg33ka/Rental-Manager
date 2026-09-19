package ru.rentalmanager.mobile;

final class UpdatePolicy {
    static final long CHECK_INTERVAL_MS = 60L * 60L * 1000L;
    static final long BACKGROUND_INTERVAL_MS = 6L * CHECK_INTERVAL_MS;

    private UpdatePolicy() {}

    static boolean checkDue(long now, long lastCheck, boolean force) {
        return force || lastCheck <= 0 || now < lastCheck || now - lastCheck >= CHECK_INTERVAL_MS;
    }

    static boolean isNewer(long installed, long offered) {
        return installed > 0 && offered > installed;
    }

    static boolean validDigest(String digest) {
        return digest != null && digest.matches("[a-fA-F0-9]{64}");
    }
}
