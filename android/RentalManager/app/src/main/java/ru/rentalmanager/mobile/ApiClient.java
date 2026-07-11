package ru.rentalmanager.mobile;

import android.content.Context;

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
    static final class ApiException extends Exception {
        final int statusCode;

        ApiException(int statusCode, String message) {
            super(message);
            this.statusCode = statusCode;
        }
    }

    private final Context context;
    private final SessionStore sessionStore;

    ApiClient(Context context) {
        this.context = context.getApplicationContext();
        this.sessionStore = new SessionStore(this.context);
    }

    String baseUrl() {
        return NotificationPrefs.baseUrl(context);
    }

    JSONObject getJson(String path) throws Exception {
        return parseJsonObject(requestText("GET", path, null));
    }

    JSONArray getArray(String path) throws Exception {
        return parseJsonArray(requestText("GET", path, null));
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
        sessionStore.clear();
    }

    String cookieHeader() {
        return sessionStore.read();
    }

    private JSONObject request(String method, String path, JSONObject body) throws Exception {
        String response = requestText(method, path, body);
        return parseJsonObject(response);
    }

    private JSONObject parseJsonObject(String response) throws ApiException {
        String text = response == null ? "" : response.trim();
        if (text.isEmpty()) return new JSONObject();
        if (looksLikeHtml(text)) throw htmlResponseException();
        try {
            return new JSONObject(text);
        } catch (Exception ex) {
            throw invalidJsonException();
        }
    }

    private JSONArray parseJsonArray(String response) throws ApiException {
        String text = response == null ? "" : response.trim();
        if (looksLikeHtml(text)) throw htmlResponseException();
        try {
            return new JSONArray(text);
        } catch (Exception ex) {
            throw invalidJsonException();
        }
    }

    private boolean looksLikeHtml(String text) {
        String lower = text.toLowerCase();
        return lower.startsWith("<!doctype html") || lower.startsWith("<html") || lower.contains("<body");
    }

    private ApiException htmlResponseException() {
        return new ApiException(0, "Сервер вернул HTML вместо API JSON. Проверьте, что в поле хоста указан адрес сервера без /api и без пути.");
    }

    private ApiException invalidJsonException() {
        return new ApiException(0, "Сервер ответил не JSON. Проверьте адрес хоста и доступность API.");
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
            if (!("GET".equals(method) || "HEAD".equals(method) || "OPTIONS".equals(method))) {
                String csrfToken = cookiePart(cookie, "rental_manager_csrf");
                if (!csrfToken.isEmpty()) connection.setRequestProperty("X-CSRF-Token", csrfToken);
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
        sessionStore.write(value.toString());
    }

    private String cookiePart(String header, String name) {
        if (header == null || header.isEmpty()) return "";
        for (String part : header.split(";")) {
            String trimmed = part.trim();
            int separator = trimmed.indexOf('=');
            if (separator > 0 && name.equals(trimmed.substring(0, separator))) {
                return trimmed.substring(separator + 1);
            }
        }
        return "";
    }
}
