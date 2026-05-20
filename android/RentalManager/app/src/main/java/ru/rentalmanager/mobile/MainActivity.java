package ru.rentalmanager.mobile;

import android.Manifest;
import android.app.Activity;
import android.app.AlertDialog;
import android.app.DownloadManager;
import android.content.Context;
import android.content.DialogInterface;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Environment;
import android.provider.Settings;
import android.view.Gravity;
import android.view.View;
import android.webkit.CookieManager;
import android.webkit.DownloadListener;
import android.webkit.URLUtil;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.TextView;
import android.widget.Toast;

public class MainActivity extends Activity {
    private static final int REQUEST_FILES = 7101;
    private static final int REQUEST_NOTIFICATIONS = 7102;

    private WebView webView;
    private TextView subtitle;
    private ValueCallback<Uri[]> fileCallback;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        NotificationHelper.ensureChannels(this);
        requestNotificationPermission();
        buildUi();
        configureWebView();
        ReminderScheduler.schedule(this);
        if (!NotificationPrefs.hasCustomBaseUrl(this)) {
            showHostDialog(true);
        }
        loadPanel();
    }

    @Override
    protected void onResume() {
        super.onResume();
        if (subtitle != null) subtitle.setText(NotificationPrefs.baseUrl(this));
    }

    private void buildUi() {
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(Color.rgb(246, 247, 248));

        LinearLayout header = new LinearLayout(this);
        header.setOrientation(LinearLayout.HORIZONTAL);
        header.setGravity(Gravity.CENTER_VERTICAL);
        header.setPadding(dp(16), dp(16), dp(12), dp(10));

        LinearLayout titleBox = new LinearLayout(this);
        titleBox.setOrientation(LinearLayout.VERTICAL);
        TextView eyebrow = text("Rental Manager", 13, false);
        eyebrow.setTextColor(Color.rgb(101, 108, 117));
        TextView title = text("Пульт аренды", 29, true);
        subtitle = text(NotificationPrefs.baseUrl(this), 12, false);
        subtitle.setTextColor(Color.rgb(101, 108, 117));
        titleBox.addView(eyebrow);
        titleBox.addView(title);
        titleBox.addView(subtitle);
        header.addView(titleBox, new LinearLayout.LayoutParams(0, -2, 1));

        Button refresh = iconButton("↻");
        Button host = iconButton("⌁");
        Button bell = iconButton("●");
        header.addView(refresh);
        header.addView(host);
        header.addView(bell);
        root.addView(header);

        webView = new WebView(this);
        root.addView(webView, new LinearLayout.LayoutParams(-1, 0, 1));

        LinearLayout nav = new LinearLayout(this);
        nav.setOrientation(LinearLayout.HORIZONTAL);
        nav.setGravity(Gravity.CENTER);
        nav.setPadding(dp(8), dp(8), dp(8), dp(8));
        nav.setBackgroundColor(Color.argb(238, 250, 250, 250));
        addNav(nav, "Пульт", "dashboard");
        addNav(nav, "Аренда", "rent");
        addNav(nav, "Коммуналка", "utilities");
        addNav(nav, "Отчёты", "reports");
        addNativeNav(nav, "Пуши");
        root.addView(nav, new LinearLayout.LayoutParams(-1, dp(66)));

        refresh.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View view) {
                webView.reload();
                refreshDigest(false);
            }
        });
        host.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View view) {
                showHostDialog(false);
            }
        });
        bell.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View view) {
                startActivity(new Intent(MainActivity.this, NotificationSettingsActivity.class));
            }
        });

        setContentView(root);
    }

    private void configureWebView() {
        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setDatabaseEnabled(true);
        settings.setLoadsImagesAutomatically(true);
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_ALWAYS_ALLOW);
        settings.setUserAgentString(settings.getUserAgentString() + " RentalManagerAndroid/1.0");
        CookieManager.getInstance().setAcceptCookie(true);
        if (Build.VERSION.SDK_INT >= 21) {
            CookieManager.getInstance().setAcceptThirdPartyCookies(webView, true);
        }
        webView.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, String url) {
                if (url == null) return false;
                if (url.startsWith("tel:") || url.startsWith("mailto:") || url.startsWith("tg:") || url.startsWith("whatsapp:")) {
                    startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(url)));
                    return true;
                }
                return false;
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                CookieManager.getInstance().flush();
                subtitle.setText(NotificationPrefs.baseUrl(MainActivity.this));
                refreshDigest(false);
            }
        });
        webView.setWebChromeClient(new WebChromeClient() {
            @Override
            public boolean onShowFileChooser(WebView view, ValueCallback<Uri[]> callback, FileChooserParams params) {
                if (fileCallback != null) fileCallback.onReceiveValue(null);
                fileCallback = callback;
                Intent intent = params == null ? new Intent(Intent.ACTION_GET_CONTENT) : params.createIntent();
                intent.addCategory(Intent.CATEGORY_OPENABLE);
                try {
                    startActivityForResult(intent, REQUEST_FILES);
                } catch (Exception ex) {
                    fileCallback = null;
                    Toast.makeText(MainActivity.this, "Не удалось открыть выбор файла", Toast.LENGTH_SHORT).show();
                    return false;
                }
                return true;
            }
        });
        webView.setDownloadListener(new DownloadListener() {
            @Override
            public void onDownloadStart(String url, String userAgent, String contentDisposition, String mimeType, long contentLength) {
                download(url, userAgent, contentDisposition, mimeType);
            }
        });
    }

    private void loadPanel() {
        String baseUrl = NotificationPrefs.baseUrl(this);
        subtitle.setText(baseUrl);
        webView.loadUrl(baseUrl);
    }

    private void showHostDialog(final boolean firstLaunch) {
        final EditText input = new EditText(this);
        input.setSingleLine(true);
        input.setText(NotificationPrefs.baseUrl(this));
        input.setSelection(input.getText().length());
        input.setPadding(dp(12), 0, dp(12), 0);
        AlertDialog dialog = new AlertDialog.Builder(this)
            .setTitle("Хост панели")
            .setMessage("Укажи адрес FastAPI-панели. После переезда хоста меняется здесь, APK не надо пересобирать.")
            .setView(input)
            .setPositiveButton("Подключить", new DialogInterface.OnClickListener() {
                @Override
                public void onClick(DialogInterface dialogInterface, int which) {
                    NotificationPrefs.setBaseUrl(MainActivity.this, input.getText().toString());
                    loadPanel();
                    ReminderScheduler.schedule(MainActivity.this);
                }
            })
            .setNegativeButton(firstLaunch ? "Позже" : "Отмена", null)
            .create();
        dialog.show();
    }

    private void addNav(LinearLayout nav, String label, final String tab) {
        Button button = navButton(label);
        nav.addView(button, new LinearLayout.LayoutParams(0, -1, 1));
        button.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View view) {
                openTab(tab);
            }
        });
    }

    private void addNativeNav(LinearLayout nav, String label) {
        Button button = navButton(label);
        nav.addView(button, new LinearLayout.LayoutParams(0, -1, 1));
        button.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View view) {
                startActivity(new Intent(MainActivity.this, NotificationSettingsActivity.class));
            }
        });
    }

    private void openTab(String tab) {
        String script = "var t=document.querySelector('.tab[data-tab=\"" + tab + "\"]'); if(t){t.click(); true;} else {false;}";
        webView.evaluateJavascript(script, null);
    }

    private void download(String url, String userAgent, String contentDisposition, String mimeType) {
        try {
            String filename = URLUtil.guessFileName(url, contentDisposition, mimeType);
            DownloadManager.Request request = new DownloadManager.Request(Uri.parse(url));
            request.setTitle(filename);
            request.setDescription("Rental Manager");
            request.setMimeType(mimeType);
            request.addRequestHeader("User-Agent", userAgent);
            String cookie = CookieManager.getInstance().getCookie(url);
            if (cookie != null) request.addRequestHeader("Cookie", cookie);
            request.setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED);
            request.setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, filename);
            DownloadManager manager = (DownloadManager) getSystemService(Context.DOWNLOAD_SERVICE);
            if (manager != null) manager.enqueue(request);
            Toast.makeText(this, "Скачиваю " + filename, Toast.LENGTH_SHORT).show();
        } catch (Exception ex) {
            startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(url)));
        }
    }

    private void refreshDigest(final boolean manual) {
        new Thread(new Runnable() {
            @Override
            public void run() {
                DashboardDigest digest = NotificationRepository.fetchDigest(MainActivity.this);
                NotificationHelper.updateStickyDebt(MainActivity.this, digest);
                if (manual) NotificationHelper.notifyDigest(MainActivity.this, digest, true);
            }
        }, "rental-dashboard-check").start();
    }

    private void requestNotificationPermission() {
        if (Build.VERSION.SDK_INT >= 33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS}, REQUEST_NOTIFICATIONS);
        }
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode != REQUEST_FILES || fileCallback == null) return;
        Uri[] result = WebChromeClient.FileChooserParams.parseResult(resultCode, data);
        fileCallback.onReceiveValue(result);
        fileCallback = null;
    }

    @Override
    public void onBackPressed() {
        if (webView != null && webView.canGoBack()) {
            webView.goBack();
            return;
        }
        super.onBackPressed();
    }

    private Button navButton(String value) {
        Button button = new Button(this);
        button.setText(value);
        button.setAllCaps(false);
        button.setTextSize(12);
        button.setTextColor(Color.rgb(31, 33, 36));
        button.setBackgroundColor(Color.TRANSPARENT);
        return button;
    }

    private Button iconButton(String value) {
        Button button = new Button(this);
        button.setText(value);
        button.setTextSize(18);
        button.setAllCaps(false);
        button.setTextColor(Color.rgb(31, 33, 36));
        button.setBackgroundColor(Color.TRANSPARENT);
        button.setWidth(dp(44));
        button.setHeight(dp(44));
        return button;
    }

    private TextView text(String value, int sp, boolean bold) {
        TextView view = new TextView(this);
        view.setText(value);
        view.setTextSize(sp);
        view.setTextColor(Color.rgb(31, 33, 36));
        if (bold) view.setTypeface(android.graphics.Typeface.DEFAULT_BOLD);
        return view;
    }

    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }
}
