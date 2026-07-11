package ru.rentalmanager.mobile;

import android.content.Context;
import android.content.SharedPreferences;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.util.Base64;

import java.nio.charset.StandardCharsets;
import java.security.KeyStore;

import javax.crypto.Cipher;
import javax.crypto.KeyGenerator;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;

final class SessionStore {
    private static final String KEY_ALIAS = "rental_manager_session_key";
    private static final String KEY_ENCRYPTED_SESSION = "panel_session_encrypted";
    private static final String KEY_LEGACY_SESSION = "panel_session_cookie";
    private static final String TRANSFORMATION = "AES/GCM/NoPadding";

    private final SharedPreferences preferences;

    SessionStore(Context context) {
        preferences = NotificationPrefs.prefs(context.getApplicationContext());
        migrateLegacySession();
    }

    String read() {
        String encoded = preferences.getString(KEY_ENCRYPTED_SESSION, "");
        if (encoded == null || encoded.isEmpty()) return "";
        try {
            byte[] payload = Base64.decode(encoded, Base64.NO_WRAP);
            if (payload.length <= 12) return "";
            byte[] iv = new byte[12];
            byte[] ciphertext = new byte[payload.length - iv.length];
            System.arraycopy(payload, 0, iv, 0, iv.length);
            System.arraycopy(payload, iv.length, ciphertext, 0, ciphertext.length);
            Cipher cipher = Cipher.getInstance(TRANSFORMATION);
            cipher.init(Cipher.DECRYPT_MODE, key(), new GCMParameterSpec(128, iv));
            return new String(cipher.doFinal(ciphertext), StandardCharsets.UTF_8);
        } catch (Exception exception) {
            clear();
            return "";
        }
    }

    void write(String value) {
        if (value == null || value.isEmpty()) {
            clear();
            return;
        }
        try {
            Cipher cipher = Cipher.getInstance(TRANSFORMATION);
            cipher.init(Cipher.ENCRYPT_MODE, key());
            byte[] ciphertext = cipher.doFinal(value.getBytes(StandardCharsets.UTF_8));
            byte[] payload = new byte[cipher.getIV().length + ciphertext.length];
            System.arraycopy(cipher.getIV(), 0, payload, 0, cipher.getIV().length);
            System.arraycopy(ciphertext, 0, payload, cipher.getIV().length, ciphertext.length);
            preferences.edit()
                .putString(KEY_ENCRYPTED_SESSION, Base64.encodeToString(payload, Base64.NO_WRAP))
                .remove(KEY_LEGACY_SESSION)
                .apply();
        } catch (Exception exception) {
            throw new IllegalStateException("Не удалось безопасно сохранить сессию", exception);
        }
    }

    void clear() {
        preferences.edit().remove(KEY_ENCRYPTED_SESSION).remove(KEY_LEGACY_SESSION).apply();
    }

    private void migrateLegacySession() {
        String legacy = preferences.getString(KEY_LEGACY_SESSION, "");
        if (legacy != null && !legacy.isEmpty() && preferences.getString(KEY_ENCRYPTED_SESSION, "").isEmpty()) {
            write(legacy);
        }
    }

    private SecretKey key() throws Exception {
        KeyStore keyStore = KeyStore.getInstance("AndroidKeyStore");
        keyStore.load(null);
        SecretKey existing = (SecretKey) keyStore.getKey(KEY_ALIAS, null);
        if (existing != null) return existing;
        KeyGenerator generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore");
        generator.init(new KeyGenParameterSpec.Builder(
            KEY_ALIAS,
            KeyProperties.PURPOSE_ENCRYPT | KeyProperties.PURPOSE_DECRYPT
        ).setBlockModes(KeyProperties.BLOCK_MODE_GCM)
            .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
            .build());
        return generator.generateKey();
    }
}
