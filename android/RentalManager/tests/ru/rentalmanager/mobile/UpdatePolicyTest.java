package ru.rentalmanager.mobile;

public final class UpdatePolicyTest {
    private static int assertions;
    private static void expect(boolean value, String message) {
        assertions++;
        if (!value) throw new AssertionError(message);
    }
    public static void main(String[] args) {
        expect(UpdatePolicy.checkDue(100, 0, false), "first launch");
        expect(!UpdatePolicy.checkDue(200, 100, false), "resume throttled");
        expect(UpdatePolicy.checkDue(200, 100, true), "manual refresh");
        expect(UpdatePolicy.checkDue(100, 200, false), "clock moved back");
        expect(UpdatePolicy.checkDue(100 + UpdatePolicy.CHECK_INTERVAL_MS, 100, false), "interval elapsed");
        expect(UpdatePolicy.isNewer(9, 10), "new release");
        expect(!UpdatePolicy.isNewer(9, 9), "same version not downloaded");
        expect(!UpdatePolicy.isNewer(9, 8), "downgrade blocked");
        expect(!UpdatePolicy.isNewer(0, 10), "unknown installation blocked");
        expect(UpdatePolicy.validDigest("0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"), "valid checksum");
        expect(!UpdatePolicy.validDigest("abc"), "short checksum rejected");
        expect(!UpdatePolicy.validDigest(null), "missing checksum rejected");
        System.out.println("Update policy: " + assertions + " assertions passed");
    }
}
