package ru.rentalmanager.mobile;

import android.content.Context;
import android.content.SharedPreferences;

import org.json.JSONObject;
import org.json.JSONArray;

import java.io.BufferedReader;
import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.Map;

final class ApiClient {
    static final String KEY_SESSION_COOKIE = "panel_session_cookie";

    static final class ApiException extends Exception {
        final int statusCode;

        ApiException(int statusCode, String message) {
            super(message);
            this.statusCode = statusCode;
        }
    }

    private final Context context;

    ApiClient(Context context) {
        this.context = context.getApplicationContext();
    }

    String baseUrl() {
        return NotificationPrefs.baseUrl(context);
    }

    JSONObject getJson(String path) throws Exception {
        return request("GET", path, null);
    }

    JSONArray getArray(String path) throws Exception {
        return new JSONArray(requestText("GET", path, null));
    }

    JSONObject postJson(String path, JSONObject body) throws Exception {
        return request("POST", path, body == null ? new JSONObject() : body);
    }

    JSONObject patchJson(String path, JSONObject body) throws Exception {
        return request("PATCH", path, body == null ? new JSONObject() : body);
    }

    JSONObject deleteJson(String path) throws Exception {
        return request("DELETE", path, null);
    }

    JSONObject login(String pin, boolean remember) throws Exception {
        JSONObject body = new JSONObject();
        body.put("pin_code", pin);
        body.put("remember_device", remember);
        return postJson("/api/auth/pin", body);
    }

    void logout() throws Exception {
        postJson("/api/auth/logout", new JSONObject());
        prefs().edit().remove(KEY_SESSION_COOKIE).apply();
    }

    String cookieHeader() {
        return prefs().getString(KEY_SESSION_COOKIE, "");
    }

    private JSONObject request(String method, String path, JSONObject body) throws Exception {
        String response = requestText(method, path, body);
        if (response == null || response.trim().isEmpty()) {
            return new JSONObject();
        }
        return new JSONObject(response);
    }

    private String requestText(String method, String path, JSONObject body) throws Exception {
        HttpURLConnection connection = null;
        try {
            URL url = new URL(baseUrl() + path);
            connection = (HttpURLConnection) url.openConnection();
            connection.setRequestMethod(method);
            connection.setConnectTimeout(10000);
            connection.setReadTimeout(20000);
            connection.setRequestProperty("Accept", "application/json");
            connection.setRequestProperty("User-Agent", "RentalManagerAndroidNative/0.1.0");
            String cookie = cookieHeader();
            if (!cookie.isEmpty()) {
                connection.setRequestProperty("Cookie", cookie);
            }
            if (body != null) {
                byte[] raw = body.toString().getBytes(StandardCharsets.UTF_8);
                connection.setDoOutput(true);
                connection.setRequestProperty("Content-Type", "application/json; charset=utf-8");
                connection.setFixedLengthStreamingMode(raw.length);
                OutputStream stream = connection.getOutputStream();
                stream.write(raw);
                stream.close();
            }

            int status = connection.getResponseCode();
            saveCookies(connection.getHeaderFields());
            String response = readResponse(connection, status);
            if (status < 200 || status >= 300) {
                String message = response;
                try {
                    JSONObject error = new JSONObject(response);
                    message = error.optString("detail", response);
                } catch (Exception ignored) {
                }
                throw new ApiException(status, message == null || message.isEmpty() ? "Ошибка запроса" : message);
            }
            return response == null ? "" : response;
        } finally {
            if (connection != null) connection.disconnect();
        }
    }

    private String readResponse(HttpURLConnection connection, int status) throws Exception {
        InputStream stream = status >= 400 ? connection.getErrorStream() : connection.getInputStream();
        if (stream == null) return "";
        BufferedReader reader = new BufferedReader(new InputStreamReader(stream, StandardCharsets.UTF_8));
        StringBuilder builder = new StringBuilder();
        String line;
        while ((line = reader.readLine()) != null) {
            builder.append(line);
        }
        return builder.toString();
    }

    byte[] getBytes(String path) throws Exception {
        HttpURLConnection connection = null;
        try {
            URL url = new URL(baseUrl() + path);
            connection = (HttpURLConnection) url.openConnection();
            connection.setRequestMethod("GET");
            connection.setConnectTimeout(10000);
            connection.setReadTimeout(30000);
            String cookie = cookieHeader();
            if (!cookie.isEmpty()) connection.setRequestProperty("Cookie", cookie);
            int status = connection.getResponseCode();
            saveCookies(connection.getHeaderFields());
            if (status < 200 || status >= 300) {
                throw new ApiException(status, readResponse(connection, status));
            }
            InputStream stream = connection.getInputStream();
            ByteArrayOutputStream output = new ByteArrayOutputStream();
            byte[] buffer = new byte[8192];
            int read;
            while ((read = stream.read(buffer)) >= 0) {
                output.write(buffer, 0, read);
            }
            return output.toByteArray();
        } finally {
            if (connection != null) connection.disconnect();
        }
    }

    private void saveCookies(Map<String, List<String>> headers) {
        if (headers == null) return;
        List<String> cookies = headers.get("Set-Cookie");
        if (cookies == null) cookies = headers.get("set-cookie");
        if (cookies == null || cookies.isEmpty()) return;
        StringBuilder value = new StringBuilder();
        for (String cookie : cookies) {
            String first = cookie.split(";", 2)[0];
            if (value.length() > 0) value.append("; ");
            value.append(first);
        }
        prefs().edit().putString(KEY_SESSION_COOKIE, value.toString()).apply();
    }

    private SharedPreferences prefs() {
        return NotificationPrefs.prefs(context);
    }
}
