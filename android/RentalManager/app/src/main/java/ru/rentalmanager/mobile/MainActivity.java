package ru.rentalmanager.mobile;

import android.Manifest;
import android.app.Activity;
import android.app.AlertDialog;
import android.app.DownloadManager;
import android.content.Context;
import android.content.DialogInterface;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.graphics.RectF;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Environment;
import android.os.Handler;
import android.os.Looper;
import android.text.InputType;
import android.view.Gravity;
import android.view.MotionEvent;
import android.view.View;
import android.widget.ArrayAdapter;
import android.widget.Button;
import android.widget.CheckBox;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.Spinner;
import android.widget.TextView;
import android.widget.Toast;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.Calendar;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;

public class MainActivity extends Activity {
    private static final int REQUEST_NOTIFICATIONS = 7102;
    private static final long CACHE_TTL_MS = 60_000L;
    private static final long CONNECTION_CHECK_MIN_MS = 2_000L;
    private static final long RECONNECT_INTERVAL_MS = 10_000L;
    private static final String FULL_APP_STATE_SECTIONS = "bootstrap,registry,rent_charges,utility_bills,expenses,tariffs,utility_timeline,message_targets,suspicious_receipts";
    private static final String[] MONTH_NAMES_RU = {
        "январь", "февраль", "март", "апрель", "май", "июнь",
        "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь"
    };

    private final int bg = MobileUi.BG;
    private final int surface = MobileUi.SURFACE;
    private final int surface2 = MobileUi.SURFACE_ALT;
    private final int hairline = MobileUi.BORDER;
    private final int text = MobileUi.TEXT;
    private final int muted = MobileUi.MUTED;
    private final int blue = MobileUi.ACCENT;
    private final int green = MobileUi.SUCCESS;
    private final int orange = MobileUi.WARNING;
    private final int red = MobileUi.DANGER;
    private final int gray = MobileUi.MUTED;

    private ApiClient api;
    private FrameLayout appFrame;
    private LinearLayout root;
    private LinearLayout content;
    private LinearLayout bottomBar;
    private TextView screenTitle;
    private Button backButton;
    private ScrollView pageScroll;
    private String searchQuery = "";
    private boolean progressLoadFailed;
    private int selectedPaymentLeaseId;
    private String selectedPaymentKind = "rent";
    private TextView screenSubtitle;
    private TextView updateBanner;
    private final Runnable updateListener = () -> refreshUpdateBanner();
    private LinearLayout loadingOverlay;
    private LoadingRingView loadingRingView;
    private TextView loadingTitleView;
    private TextView loadingDetailView;
    private JSONObject bootstrap;
    private JSONArray rentCharges = new JSONArray();
    private JSONArray utilityBills = new JSONArray();
    private JSONArray utilityTimeline = new JSONArray();
    private JSONArray expenses = new JSONArray();
    private JSONArray tariffs = new JSONArray();
    private JSONArray messageTargets = new JSONArray();
    private JSONArray suspiciousReceipts = new JSONArray();
    private JSONArray progressRentCharges = new JSONArray();
    private JSONArray progressUtilityBills = new JSONArray();
    private JSONArray progressProviderReadings = new JSONArray();
    private JSONObject progressMonthSummary = new JSONObject();
    private String currentTab = "dashboard";
    private String servicesMode = "utilities";
    private String premiumPaymentFilter = "open";
    private String progressRentMonthKey = "";
    private long bootstrapLoadedAt = 0L;
    private long paymentsLoadedAt = 0L;
    private long servicesLoadedAt = 0L;
    private long moreLoadedAt = 0L;
    private long lastConnectionCheckAt = 0L;
    private boolean prefetchRunning = false;
    private boolean reconnectLoopRunning = false;
    private boolean lastConnectionOk = true;
    private boolean activityResumed = false;
    private boolean progressRentLoading = false;
    private Handler reconnectHandler;
    private Calendar selectedMonth = Calendar.getInstance();
    private float monthTouchStartX = 0f;
    private float monthTouchStartY = 0f;

    private final Runnable reconnectRunnable = new Runnable() {
        @Override
        public void run() {
            if (!reconnectLoopRunning || reconnectHandler == null) return;
            checkServerConnection(true);
            reconnectHandler.postDelayed(this, RECONNECT_INTERVAL_MS);
        }
    };

    interface Job {
        Object run() throws Exception;
    }

    interface Done {
        void run(Object value);
    }

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        api = new ApiClient(this);
        reconnectHandler = new Handler(Looper.getMainLooper());
        NotificationHelper.ensureChannels(this);
        requestNotificationPermission();
        buildShell();
        ReminderScheduler.schedule(this);
        AppUpdates.schedule(this);
        AppUpdates.check(this, true, false, null);
        checkServerConnection(true);
        checkAuthAndLoad();
    }

    @Override
    protected void onResume() {
        super.onResume();
        activityResumed = true;
        AppUpdates.observe(updateListener);
        refreshUpdateBanner();
        AppUpdates.check(this, false, false, null);
        if (getIntent().getBooleanExtra("open_update", false)) {
            getIntent().removeExtra("open_update");
            AppUpdates.showDialog(this);
        }
        if (screenSubtitle != null) setConnectionStatus(lastConnectionOk, lastConnectionOk ? "На связи" : "Нет связи");
        checkServerConnection(false);
        if (!lastConnectionOk) startReconnectLoop();
    }

    @Override
    protected void onPause() {
        activityResumed = false;
        AppUpdates.unobserve(updateListener);
        stopReconnectLoop();
        super.onPause();
    }

    @Override protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        if (bootstrapLoadedAt > 0 && intent.hasExtra("notification_tab")) renderCurrentTab();
    }

    private void refreshUpdateBanner() {
        if (updateBanner == null) return;
        updateBanner.setVisibility(AppUpdates.ready(this) ? View.VISIBLE : View.GONE);
        updateBanner.setText(AppUpdates.status(this));
    }

    private void buildShell() {
        appFrame = new FrameLayout(this);
        appFrame.setBackgroundColor(bg);
        root = MobileUi.column(this);
        root.setBackgroundColor(bg);
        LinearLayout header = MobileUi.row(this);
        header.setPadding(dp(18), dp(12), dp(18), dp(12));
        backButton = MobileUi.iconButton(this, "back", "Назад");
        backButton.setOnClickListener(v -> navigate("receipts".equals(currentTab) ? "payments" : "more"));
        header.addView(backButton);
        LinearLayout titleBox = MobileUi.column(this);
        titleBox.setPadding(dp(8), 0, dp(8), 0);
        screenTitle = label("Пульт", 25, text, true);
        screenSubtitle = label("Подключение…", 12, muted, false);
        titleBox.addView(screenTitle);
        titleBox.addView(screenSubtitle);
        header.addView(titleBox, new LinearLayout.LayoutParams(0, -2, 1));
        Button refresh = MobileUi.iconButton(this, "refresh", "Обновить данные");
        refresh.setOnClickListener(v -> { progressRentMonthKey = ""; progressLoadFailed = false; loadCurrentTab(true); });
        header.addView(refresh);
        Button menu = MobileUi.iconButton(this, "settings", "Настройки приложения");
        LinearLayout.LayoutParams menuParams = new LinearLayout.LayoutParams(dp(48), dp(48));
        menuParams.setMarginStart(dp(8));
        header.addView(menu, menuParams);
        menu.setOnClickListener(v -> showAppMenuDialog());
        root.addView(header);
        updateBanner = label("", 14, green, true);
        updateBanner.setPadding(dp(18), dp(12), dp(18), dp(12));
        updateBanner.setMinHeight(dp(48));
        updateBanner.setOnClickListener(v -> AppUpdates.showDialog(this));
        root.addView(updateBanner);
        refreshUpdateBanner();
        pageScroll = new ScrollView(this);
        pageScroll.setClipToPadding(false);
        pageScroll.setFillViewport(true);
        content = MobileUi.column(this);
        content.setPadding(dp(18), dp(4), dp(18), dp(24));
        pageScroll.addView(content);
        root.addView(pageScroll, new LinearLayout.LayoutParams(-1, 0, 1));
        bottomBar = MobileUi.row(this);
        bottomBar.setPadding(dp(8), dp(6), dp(8), dp(6));
        bottomBar.setBackgroundColor(surface);
        root.addView(bottomBar, new LinearLayout.LayoutParams(-1, -2));
        appFrame.addView(root, new FrameLayout.LayoutParams(-1, -1));
        buildLoadingOverlay();
        setContentView(appFrame);
        MobileUi.applyWindow(this, appFrame);
        buildBottomNav();
    }

    private boolean isGuest() {
        return "guest".equals(obj(bootstrap, "auth").optString("role"));
    }

    private void navigate(String tab) {
        currentTab = tab;
        searchQuery = "";
        buildBottomNav();
        if (pageScroll != null) pageScroll.scrollTo(0, 0);
        loadCurrentTab(false);
    }

    @Override public void onBackPressed() {
        if (!isOperationalTab(currentTab)) navigate("receipts".equals(currentTab) ? "payments" : "more");
        else if (!"dashboard".equals(currentTab)) navigate("dashboard");
        else super.onBackPressed();
    }

    private void consumeNotificationRoute() {
        String action = getIntent().getStringExtra("notification_action");
        String tab = getIntent().getStringExtra("notification_tab");
        if (tab == null) return;
        getIntent().removeExtra("notification_tab");
        getIntent().removeExtra("notification_action");
        if ("receipts".equals(action)) currentTab = "receipts";
        else if ("reports".equals(action)) currentTab = "reports";
        else if ("readings".equals(action) || "utilities".equals(action)) {
            currentTab = "services";
            servicesMode = "readings".equals(action) ? "meters" : "utilities";
        } else if (isOperationalTab(tab)) currentTab = tab;
        if (isGuest()) currentTab = "reports".equals(currentTab) ? "reports" : "dashboard";
    }

    private void buildBottomNav() {
        bottomBar.removeAllViews();
        if (backButton != null) backButton.setVisibility(isOperationalTab(currentTab) ? View.GONE : View.VISIBLE);
        addNav("Пульт", "dashboard");
        if (!isGuest()) {
            addNav("Дела", "tasks");
            addNav("Оплаты", "payments");
            addNav("Объекты", "properties");
        }
        addNav("Ещё", "more");
    }

    private boolean isOperationalTab(String tab) {
        return "dashboard".equals(tab) || "properties".equals(tab) || "payments".equals(tab) || "tasks".equals(tab) || "more".equals(tab);
    }

    private void addNav(String title, String tab) {
        String active = isOperationalTab(currentTab) ? currentTab : "receipts".equals(currentTab) ? "payments" : "more";
        LinearLayout item = MobileUi.navItem(this, "dashboard".equals(tab) ? "home" : tab, title, tab.equals(active));
        item.setOnClickListener(v -> navigate(tab));
        bottomBar.addView(item, new LinearLayout.LayoutParams(0, -2, 1));
    }

    private String navTitle(String title, String tab) {
        if ("dashboard".equals(tab)) return "⌂\n" + title;
        if ("properties".equals(tab)) return "◩\n" + title;
        if ("tasks".equals(tab)) return "✓\n" + title;
        if ("payments".equals(tab)) return "₽\n" + title;
        return "⋯\n" + title;
    }

    private void checkAuthAndLoad() {
        runApi("Проверяю вход", () -> api.getJson("/api/auth/status"), value -> {
            JSONObject status = (JSONObject) value;
            if (!status.optBoolean("authenticated")) {
                showLogin();
            } else {
                loadCurrentTab(true);
            }
        });
    }

    private void checkServerConnection(boolean force) {
        long now = System.currentTimeMillis();
        if (!force && now - lastConnectionCheckAt < CONNECTION_CHECK_MIN_MS) return;
        lastConnectionCheckAt = now;
        new Thread(() -> {
            try {
                api.getJson("/healthz");
                runOnUiThread(() -> setConnectionStatus(true, "сервер отвечает"));
            } catch (ApiClient.ApiException ex) {
                if (ex.statusCode == 401 || ex.statusCode == 403) {
                    runOnUiThread(() -> setConnectionStatus(false, "нужен PIN"));
                } else if (ex.statusCode <= 0) {
                    runOnUiThread(() -> setConnectionStatus(false, "не API"));
                } else {
                    runOnUiThread(() -> setConnectionStatus(false, "ошибка " + ex.statusCode));
                }
            } catch (Exception ex) {
                runOnUiThread(() -> setConnectionStatus(false, "связи нет"));
            }
        }, "rental-health").start();
    }

    private void setConnectionStatus(boolean ok, String status) {
        if (screenSubtitle == null) return;
        lastConnectionOk = ok;
        screenSubtitle.setText((ok ? "●  " : "○  ") + status);
        screenSubtitle.setTextColor(ok ? green : red);
        if (ok) {
            stopReconnectLoop();
        } else if (activityResumed) {
            startReconnectLoop();
        }
    }

    private void startReconnectLoop() {
        if (reconnectLoopRunning || reconnectHandler == null) return;
        reconnectLoopRunning = true;
        reconnectHandler.postDelayed(reconnectRunnable, RECONNECT_INTERVAL_MS);
    }

    private void stopReconnectLoop() {
        reconnectLoopRunning = false;
        if (reconnectHandler != null) reconnectHandler.removeCallbacks(reconnectRunnable);
    }

    private void showLogin() {
        hideLoadingCard();
        screenTitle.setText("Rental Manager");
        screenSubtitle.setText("Управление арендой");
        bottomBar.setVisibility(View.GONE);
        backButton.setVisibility(View.GONE);
        content.removeAllViews();
        addHero("Всё по делу.\nДаже в пути.", "Оплаты, квартиры и задачи — в одном месте. Введите PIN, чтобы продолжить.");
        LinearLayout card = card();
        EditText pin = input("PIN-код", true);
        pin.setInputType(InputType.TYPE_CLASS_NUMBER | InputType.TYPE_NUMBER_VARIATION_PASSWORD);
        CheckBox remember = checkbox("Запомнить устройство");
        remember.setChecked(true);
        Button login = primaryButton("Войти");
        Button host = secondaryButton("Адрес сервера");
        card.addView(field("PIN", pin));
        card.addView(remember);
        card.addView(login, new LinearLayout.LayoutParams(-1, dp(50)));
        card.addView(host, new LinearLayout.LayoutParams(-1, -2));
        content.addView(card);
        login.setOnClickListener(v -> {
            if (pin.getText().toString().trim().isEmpty()) { pin.setError("Введите PIN"); return; }
            login.setEnabled(false);
            runApi("Вхожу", () -> { try { return api.login(pin.getText().toString(), remember.isChecked()); }
                finally { runOnUiThread(() -> login.setEnabled(true)); } }, value -> loadCurrentTab(true));
        });
        host.setOnClickListener(v -> showHostDialog());
    }

    private void loadCurrentTab(boolean force) {
        if (!force && currentTabReady()) {
            renderCurrentTab();
            if (currentTabStale()) {
                refreshCurrentTab(false);
            }
            return;
        }
        refreshCurrentTab(true);
    }

    private boolean currentTabReady() {
        return bootstrapLoadedAt > 0;
    }

    private boolean currentTabStale() {
        return bootstrapLoadedAt <= 0 || System.currentTimeMillis() - bootstrapLoadedAt > CACHE_TTL_MS;
    }

    private void renderCurrentTab() {
        try {
            renderOperationalCurrentTab();
        } finally {
            hideLoadingCard();
        }
    }

    private void renderOperationalCurrentTab() {
        consumeNotificationRoute();
        bottomBar.setVisibility(View.VISIBLE);
        buildBottomNav();
        if ("properties".equals(currentTab)) renderPremiumProperties();
        else if ("payments".equals(currentTab)) renderPremiumPayments();
        else if ("tasks".equals(currentTab)) renderPremiumTasks();
        else if ("more".equals(currentTab)) renderMore();
        else if ("services".equals(currentTab)) renderServices();
        else if ("tenants".equals(currentTab)) renderTenants();
        else if ("receipts".equals(currentTab)) renderReceipts();
        else if ("reports".equals(currentTab)) renderReports();
        else if ("settings".equals(currentTab)) renderSettings();
        else renderPremiumDashboard();
    }

    private void refreshCurrentTab(boolean visible) {
        if (visible) showLoadingCard();
        runApi("Обновляю данные", visible, () -> {
            loadAppState();
            return null;
        }, value -> renderCurrentTab());
    }

    private void renderLoadedSnapshot(String status) {
        runOnUiThread(() -> {
            setConnectionStatus(true, status);
            if (bootstrapLoadedAt > 0) renderCurrentTab();
        });
    }

    private void loadAppState() throws Exception {
        updateLoadingCard("Собираю дашборд", "Проверяю PIN, настройки и основные карточки.");
        applyAppState(api.getJson("/api/app-state?sections=bootstrap"));
        bootstrapLoadedAt = System.currentTimeMillis();
        renderLoadedSnapshot("дашборд загружен");

        updateLoadingCard("Готовлю вкладки", "Объекты, жильцы, счётчики и услуги.");
        applyAppState(api.getJson("/api/app-state?sections=registry"));
        bootstrapLoadedAt = System.currentTimeMillis();
        renderLoadedSnapshot("справочник догружен");

        updateLoadingCard("Подтягиваю расчёты", "Аренда, коммуналка, расходы и тарифы.");
        applyAppState(api.getJson("/api/app-state?sections=rent_charges,utility_bills,expenses,tariffs"));
        long financialLoadedAt = System.currentTimeMillis();
        paymentsLoadedAt = financialLoadedAt;
        servicesLoadedAt = financialLoadedAt;
        renderLoadedSnapshot("расчёты догружены");

        updateLoadingCard("Загружаю дополнительные данные", "Таймлайн, рассылки и подозрительные чеки.");
        try {
            applyAppState(api.getJson("/api/app-state?sections=utility_timeline,message_targets,suspicious_receipts"));
            moreLoadedAt = System.currentTimeMillis();
            renderLoadedSnapshot("данные загружены");
        } catch (Exception ex) {
            runOnUiThread(() -> toast("Часть данных не загрузилась: " + (ex.getMessage() == null ? "ошибка" : ex.getMessage())));
        }
    }

    private void syncAfterMutation() {
        runApi("Синхронизирую", false, () -> {
            applyAppState(api.getJson("/api/app-state?sections=" + FULL_APP_STATE_SECTIONS));
            long now = System.currentTimeMillis();
            bootstrapLoadedAt = now;
            paymentsLoadedAt = now;
            servicesLoadedAt = now;
            moreLoadedAt = now;
            return null;
        }, value -> renderCurrentTab());
    }

    private void applyAppState(JSONObject payload) {
        if (payload == null) return;
        if (payload.has("bootstrap")) {
            bootstrap = payload.optJSONObject("bootstrap");
            if (bootstrap == null) bootstrap = new JSONObject();
        }
        if (payload.has("registry")) {
            JSONObject registry = payload.optJSONObject("registry");
            if (registry != null) {
                putBootstrapArray("objects", registry.optJSONArray("objects"));
                putBootstrapArray("leases", registry.optJSONArray("leases"));
                putBootstrapArray("meters", registry.optJSONArray("meters"));
                putBootstrapArray("services", registry.optJSONArray("services"));
            }
        }
        if (payload.has("rent_charges")) {
            rentCharges = payload.optJSONArray("rent_charges");
            if (rentCharges == null) rentCharges = new JSONArray();
        }
        if (payload.has("utility_bills")) {
            utilityBills = payload.optJSONArray("utility_bills");
            if (utilityBills == null) utilityBills = new JSONArray();
        }
        if (payload.has("utility_timeline")) {
            utilityTimeline = payload.optJSONArray("utility_timeline");
            if (utilityTimeline == null) utilityTimeline = new JSONArray();
        }
        if (payload.has("expenses")) {
            expenses = payload.optJSONArray("expenses");
            if (expenses == null) expenses = new JSONArray();
        }
        if (payload.has("tariffs")) {
            tariffs = payload.optJSONArray("tariffs");
            if (tariffs == null) tariffs = new JSONArray();
        }
        if (payload.has("message_targets")) {
            messageTargets = payload.optJSONArray("message_targets");
            if (messageTargets == null) messageTargets = new JSONArray();
        }
        if (payload.has("suspicious_receipts")) {
            suspiciousReceipts = payload.optJSONArray("suspicious_receipts");
            if (suspiciousReceipts == null) suspiciousReceipts = new JSONArray();
        }
    }

    private JSONObject ensureBootstrapObject() {
        if (bootstrap == null) bootstrap = new JSONObject();
        return bootstrap;
    }

    private void putBootstrapArray(String key, JSONArray value) {
        try {
            ensureBootstrapObject().put(key, value == null ? new JSONArray() : value);
        } catch (Exception ignored) {
            // Локальное слияние payload не должно валить экран.
        }
    }

    private void loadBootstrap() throws Exception {
        bootstrap = api.getJson("/api/bootstrap");
        bootstrapLoadedAt = System.currentTimeMillis();
    }

    private void loadPayments() throws Exception {
        rentCharges = api.getArray("/api/rent-charges");
        suspiciousReceipts = api.getArray("/api/payment-receipts/suspicious");
        paymentsLoadedAt = System.currentTimeMillis();
    }

    private void loadServices() throws Exception {
        utilityBills = api.getArray("/api/utility-bills");
        utilityTimeline = api.getArray("/api/utilities/timeline");
        expenses = api.getArray("/api/expenses");
        tariffs = api.getArray("/api/tariffs");
        servicesLoadedAt = System.currentTimeMillis();
    }

    private void loadMoreData() throws Exception {
        messageTargets = api.getArray("/api/messages/targets");
        moreLoadedAt = System.currentTimeMillis();
    }

    private void prefetchSecondaryData() {
        if (prefetchRunning) return;
        prefetchRunning = true;
        new Thread(() -> {
            try {
                if (currentTabStale()) loadAppState();
            } catch (Exception ignored) {
                // Фоновая предзагрузка не должна ломать открытый экран.
            } finally {
                prefetchRunning = false;
                runOnUiThread(() -> {
                    if ("dashboard".equals(currentTab) && bootstrapLoadedAt > 0) renderCurrentTab();
                });
            }
        }, "rental-prefetch").start();
    }

    private void invalidateAllCaches() {
        bootstrapLoadedAt = 0L;
        paymentsLoadedAt = 0L;
        servicesLoadedAt = 0L;
        moreLoadedAt = 0L;
    }

    private void buildLoadingOverlay() {
        loadingOverlay = new LinearLayout(this);
        loadingOverlay.setOrientation(LinearLayout.VERTICAL);
        loadingOverlay.setGravity(Gravity.CENTER);
        loadingOverlay.setPadding(dp(28), dp(28), dp(28), dp(28));
        loadingOverlay.setClickable(true);
        GradientDrawable bgDrawable = new GradientDrawable(
            GradientDrawable.Orientation.TL_BR,
            new int[] { Color.rgb(5, 10, 10), Color.rgb(14, 23, 24), Color.rgb(5, 8, 9) }
        );
        loadingOverlay.setBackground(bgDrawable);

        LinearLayout panel = new LinearLayout(this);
        panel.setOrientation(LinearLayout.VERTICAL);
        panel.setGravity(Gravity.CENTER);
        panel.setPadding(dp(24), dp(24), dp(24), dp(24));
        loadingRingView = new LoadingRingView(this);
        panel.addView(loadingRingView, new LinearLayout.LayoutParams(dp(120), dp(120)));

        TextView word = label("LOADING\nSCREEN", 38, Color.rgb(237, 243, 244), true);
        word.setGravity(Gravity.CENTER);
        word.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
        word.setLineSpacing(0, 0.92f);
        panel.addView(word, new LinearLayout.LayoutParams(-1, -2));

        LinearLayout loadingBottom = new LinearLayout(this);
        loadingBottom.setOrientation(LinearLayout.HORIZONTAL);
        loadingBottom.setGravity(Gravity.CENTER);
        TextView dots = label("• • •", 28, Color.rgb(237, 243, 244), true);
        dots.setGravity(Gravity.CENTER);
        LinearLayout.LayoutParams dotsParams = new LinearLayout.LayoutParams(-2, -2);
        dotsParams.setMargins(0, 0, dp(20), 0);
        loadingBottom.addView(dots, dotsParams);
        LinearLayout barTrack = new LinearLayout(this);
        barTrack.setPadding(dp(4), dp(4), dp(4), dp(4));
        GradientDrawable trackBg = round(Color.rgb(126, 232, 238), 22, dp(5), Color.argb(220, 126, 232, 238));
        barTrack.setBackground(trackBg);
        TextView barFill = new TextView(this);
        GradientDrawable fillBg = round(Color.rgb(126, 232, 238), 210, dp(2), Color.TRANSPARENT);
        barFill.setBackground(fillBg);
        barTrack.addView(barFill, new LinearLayout.LayoutParams(dp(54), -1));
        loadingBottom.addView(barTrack, new LinearLayout.LayoutParams(dp(112), dp(22)));
        LinearLayout.LayoutParams bottomParams = new LinearLayout.LayoutParams(-1, -2);
        bottomParams.setMargins(0, dp(4), 0, dp(16));
        panel.addView(loadingBottom, bottomParams);

        loadingTitleView = label("Собираю данные", 16, Color.rgb(237, 243, 244), true);
        loadingTitleView.setGravity(Gravity.CENTER);
        loadingDetailView = label("Проверяю сервер.", 13, Color.rgb(150, 166, 168), false);
        loadingDetailView.setGravity(Gravity.CENTER);
        panel.addView(loadingTitleView, new LinearLayout.LayoutParams(-1, -2));
        panel.addView(loadingDetailView, new LinearLayout.LayoutParams(-1, -2));

        loadingOverlay.addView(panel, new LinearLayout.LayoutParams(Math.min(dp(330), getResources().getDisplayMetrics().widthPixels - dp(32)), -2));
        loadingOverlay.setVisibility(View.GONE);
        appFrame.addView(loadingOverlay, new FrameLayout.LayoutParams(-1, -1));
    }

    private void showLoadingCard() {
        if (loadingOverlay != null) {
            loadingOverlay.setVisibility(View.VISIBLE);
            loadingOverlay.bringToFront();
        }
        if (loadingRingView != null) loadingRingView.start();
    }

    private void hideLoadingCard() {
        if (loadingRingView != null) loadingRingView.stop();
        if (loadingOverlay != null) loadingOverlay.setVisibility(View.GONE);
    }

    private void updateLoadingCard(String title, String detail) {
        Runnable update = () -> {
            if (loadingTitleView != null) loadingTitleView.setText(title);
            if (loadingDetailView != null) loadingDetailView.setText(detail);
        };
        if (Looper.myLooper() == Looper.getMainLooper()) {
            update.run();
        } else {
            runOnUiThread(update);
        }
    }

    private void showAppMenuDialog() {
        String[] entries = {"Обновления приложения", "Адрес сервера", "Уведомления"};
        new AlertDialog.Builder(this).setTitle("Приложение").setItems(entries, (dialog, which) -> {
            if (which == 0) AppUpdates.showDialog(this);
            else if (which == 1) showHostDialog();
            else startActivity(new Intent(this, NotificationSettingsActivity.class));
        }).setNegativeButton("Закрыть", null).show();
    }

    private void renderPremiumDashboard() {
        screenTitle.setText("Пульт");
        content.removeAllViews();
        JSONObject dashboard = obj(bootstrap, "dashboard");
        if (!isGuest()) ensureProgressRentData();
        LinearLayout month = row();
        month.addView(monthArrow("‹", -1), new LinearLayout.LayoutParams(dp(48), dp(48)));
        TextView heading = label(monthTitle(selectedMonth), 17, text, true);
        heading.setGravity(Gravity.CENTER);
        month.addView(heading, new LinearLayout.LayoutParams(0, -2, 1));
        month.addView(monthArrow("›", 1), new LinearLayout.LayoutParams(dp(48), dp(48)));
        content.addView(month);
        addPremiumFinancialCard(dashboard);
        if (isGuest()) {
            addDestination("document", "Отчёты", "Скачать сводку за период", () -> navigate("reports"));
            return;
        }
        content.addView(section("Быстрые действия"));
        LinearLayout quick = row();
        addAction(quick, "Принять оплату", () -> showManualPaymentDialog(), true);
        addAction(quick, "Показания", () -> showReadingDialog(), false);
        content.addView(quick);
        content.addView(section("Требует внимания"));
        addPremiumAttentionCards(dashboard, 3);
        content.addView(secondaryButton("Все дела", v -> navigate("tasks")));
        content.addView(section("Ближайшие платежи"));
        addPremiumUpcomingPayments(3);
    }

    private void addPremiumFinancialCard(JSONObject dashboard) {
        JSONObject summary = premiumMonthSummary(dashboard);
        LinearLayout card = premiumCard();
        card.setBackground(MobileUi.shape(this, Color.rgb(18, 42, 43), 24, Color.rgb(38, 82, 76)));
        card.addView(label("ДЕНЕЖНЫЙ ПОТОК МЕСЯЦА", 11, blue, true));
        if (summary.length() == 0) {
            card.addView(label(progressLoadFailed ? "Сводка недоступна" : "Сводка загружается", 23, text, true));
            card.addView(label("Суммы появятся после получения данных за выбранный месяц.", 14, muted, false));
            if (progressLoadFailed) card.addView(secondaryButton("Повторить", v -> {
                progressRentMonthKey = ""; progressLoadFailed = false; ensureProgressRentData(); renderCurrentTab();
            }));
            content.addView(card);
            return;
        }
        card.addView(label("Зарплата · получено", 14, muted, false));
        card.addView(label(money(summary.optDouble("salary_paid")), 34, text, true));
        card.addView(label("из " + money(summary.optDouble("salary_due")) + " за месяц", 14, muted, false));
        android.widget.ProgressBar progress = new android.widget.ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal);
        progress.setMax(1000);
        double due = summary.optDouble("salary_due");
        progress.setProgress(due > 0 ? (int) Math.min(1000, 1000 * summary.optDouble("salary_paid") / due) : 0);
        progress.setProgressTintList(android.content.res.ColorStateList.valueOf(blue));
        progress.setProgressBackgroundTintList(android.content.res.ColorStateList.valueOf(hairline));
        LinearLayout.LayoutParams bar = new LinearLayout.LayoutParams(-1, dp(6));
        bar.setMargins(0, dp(16), 0, dp(18));
        card.addView(progress, bar);
        card.addView(premiumValueLine("Коммунальные счета", moneyPair(summary.optDouble("bill_payment_paid"), summary.optDouble("bill_payment_due"))));
        card.addView(premiumValueLine("Авансы коммуналки", moneyPair(summary.optDouble("advance_paid"), summary.optDouble("advance_due"))));
        card.addView(label("Получено / начислено; авансы — получено / план", 11, muted, false));
        LinearLayout utility = row();
        utility.setPadding(0, dp(12), 0, dp(4));
        addStatus(utility, summary.optBoolean("utility_bills_issued") ? "Счета выставлены" : "Не все счета выставлены", summary.optBoolean("utility_bills_issued") ? green : orange);
        addStatus(utility, summary.optBoolean("utility_provider_paid") ? "Поставщик оплачен" : "Поставщик не оплачен", summary.optBoolean("utility_provider_paid") ? green : orange);
        card.addView(utility);
        card.addView(premiumValueLine("Занято квартир", summary.optInt("occupied") + " из " + summary.optInt("total_apartments")));
        LinearLayout counts = row();
        counts.setPadding(0, dp(10), 0, 0);
        addCountTile(counts, "Оплачено", summary.optInt("paid_count"), green);
        addCountTile(counts, "Ожидается", summary.optInt("pending_count"), orange);
        addCountTile(counts, "Просрочено", summary.optInt("overdue_count"), red);
        card.addView(counts);
        content.addView(card);
    }

    private void addCountTile(LinearLayout parent, String title, int count, int color) {
        LinearLayout tile = MobileUi.column(this);
        tile.addView(label(Integer.toString(count), 23, color, true));
        tile.addView(label(title, 11, muted, false));
        parent.addView(tile, new LinearLayout.LayoutParams(0, -2, 1));
    }

    private void addStatus(LinearLayout parent, String title, int color) {
        TextView chip = premiumChip(title, color);
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(0, -2, 1);
        params.setMargins(0, 0, dp(4), 0);
        parent.addView(chip, params);
    }

    private JSONObject premiumMonthSummary(JSONObject dashboard) {
        if (selectedMonthKey().equals(progressRentMonthKey) && progressMonthSummary.length() > 0) {
            return progressMonthSummary;
        }
        JSONObject summary = obj(dashboard, "month_summary");
        if (summary.optInt("year") == selectedMonth.get(Calendar.YEAR)
            && summary.optInt("month") == selectedMonth.get(Calendar.MONTH) + 1) {
            return summary;
        }
        return new JSONObject();
    }





    private LinearLayout premiumValueLine(String title, String value) {
        LinearLayout line = MobileUi.column(this);
        line.setPadding(0, dp(7), 0, dp(7));
        line.addView(label(title, 13, muted, false));
        line.addView(label(value, 17, text, true));
        return line;
    }

    private LinearLayout premiumUtilityStatusLine(String title, boolean issued, boolean paid) {
        LinearLayout line = row();
        line.setPadding(0, dp(8), 0, dp(4));
        line.addView(label(title, 16, text, true), new LinearLayout.LayoutParams(0, -2, 1));
        line.addView(label("Выстав. " + (issued ? "✓" : "×"), 15, issued ? green : red, true));
        line.addView(space(10), new LinearLayout.LayoutParams(dp(10), 1));
        line.addView(label("Оплач. " + (paid ? "✓" : "×"), 15, paid ? green : red, true));
        return line;
    }

    private void addPremiumAttentionCards(JSONObject dashboard, int limit) {
        int[] count = {0};
        count[0] += addPremiumAttentionSource(dashboard, "utility_overdue", "Просрочена коммуналка", red, "payments", limit - count[0]);
        count[0] += addPremiumAttentionSource(dashboard, "rent_overdue", "Просрочена аренда", red, "payments", limit - count[0]);
        count[0] += addPremiumAttentionSource(dashboard, "utility_partial", "Частичная коммуналка", orange, "payments", limit - count[0]);
        count[0] += addPremiumAttentionSource(dashboard, "rent_partial", "Частичная аренда", orange, "payments", limit - count[0]);
        count[0] += addPremiumAttentionSource(dashboard, "manual_debts", "Ручной долг", orange, "payments", limit - count[0]);
        count[0] += addPremiumAttentionSource(dashboard, "suspicious_receipts", "Проверить чек", red, "payments", limit - count[0]);
        count[0] += addPremiumAttentionSource(dashboard, "provider_reading_due", "Показания поставщику", orange, "tasks", limit - count[0]);
        if (count[0] == 0) {
            LinearLayout ok = premiumCard();
            ok.addView(label("Всё закрыто", 18, text, true));
            ok.addView(label("Просрочек и срочных действий сейчас нет.", 13, muted, false));
            content.addView(ok);
        }
    }

    private int addPremiumAttentionSource(JSONObject dashboard, String key, String title, int color, String targetTab, int limit) {
        if (limit <= 0) return 0;
        JSONArray items = arr(dashboard, key);
        int added = 0;
        for (int i = 0; i < items.length() && added < limit; i++) {
            JSONObject item = items.optJSONObject(i);
            if (item == null) continue;
            LinearLayout card = premiumCardWithAccent(color);
            card.addView(label(title, 13, color, true));
            card.addView(label(attentionGroupTitle(item, title), 18, text, true));
            card.addView(label(attentionLine(key, title, item), 13, muted, false));
            card.setOnClickListener(v -> {
                openAttention(key, item);
            });
            content.addView(card);
            added++;
        }
        return added;
    }

    private void openAttention(String key, JSONObject item) {
        if ("suspicious_receipts".equals(key)) { navigate("receipts"); return; }
        if (key.contains("reading")) { openService("meters"); return; }
        if (key.startsWith("utility") || "provider_debts".equals(key)) { openService("utilities"); return; }
        if (key.startsWith("rent")) {
            for (int i = 0; i < rentCharges.length(); i++) {
                JSONObject charge = rentCharges.optJSONObject(i);
                if (charge != null && (charge.optInt("id", -1) == item.optInt("id", -2)
                    || charge.optInt("id", -1) == item.optInt("rent_charge_id", -2))) { showRentActions(charge); return; }
            }
        }
        navigate("payments");
    }

    private void addPremiumUpcomingPayments(int limit) {
        int added = 0;
        for (int i = 0; i < rentCharges.length() && added < limit; i++) {
            JSONObject charge = rentCharges.optJSONObject(i);
            if (charge == null || isDoneStatus(charge.optString("status")) || !isFutureDate(charge.optString("due_date"))) continue;
            LinearLayout card = premiumCard();
            card.addView(label(joinNonEmpty(charge.optString("object"), charge.optString("apartment")), 17, text, true));
            card.addView(label(charge.optString("tenant"), 13, muted, false));
            card.addView(label("Аренда " + money(charge.optDouble("total_due")) + " до " + compactDate(charge.optString("due_date")), 14, orange, true));
            content.addView(card);
            added++;
        }
        for (int i = 0; i < utilityBills.length() && added < limit; i++) {
            JSONObject bill = utilityBills.optJSONObject(i);
            if (bill == null) continue;
            JSONArray lines = arr(bill, "lines");
            for (int j = 0; j < lines.length() && added < limit; j++) {
                JSONObject line = lines.optJSONObject(j);
                if (line == null || isDoneStatus(line.optString("status")) || !isFutureDate(line.optString("due_date"))) continue;
                LinearLayout card = premiumCard();
                card.addView(label(joinNonEmpty(line.optString("object"), line.optString("apartment")), 17, text, true));
                card.addView(label(joinNonEmpty(line.optString("tenant"), line.optString("service")), 13, muted, false));
                card.addView(label("Коммуналка " + money(line.optDouble("total_amount")) + " до " + compactDate(line.optString("due_date")), 14, orange, true));
                content.addView(card);
                added++;
            }
        }
        if (added == 0) {
            LinearLayout card = premiumCard();
            card.addView(label("Ближайших платежей нет", 17, text, true));
            card.addView(label("Следующее плановое действие появится здесь после начислений.", 13, muted, false));
            content.addView(card);
        }
    }

    private void renderPremiumProperties() {
        screenTitle.setText("Объекты");
        content.removeAllViews();
        LinearLayout actions = row();
        addAction(actions, "Заселить", () -> showOnboardDialog(null), true);
        addAction(actions, "Договоры", () -> navigate("tenants"), false);
        content.addView(actions);
        LinearLayout list = MobileUi.column(this);
        addSearch("Квартира, объект или жилец", () -> fillProperties(list));
        content.addView(list);
        fillProperties(list);
    }

    private void fillProperties(LinearLayout list) {
        list.removeAllViews();
        forEach(arr(bootstrap, "objects"), object -> {
            LinearLayout group = MobileUi.column(this);
            forEach(arr(object, "apartments"), apartment -> {
                if (!apartment.optBoolean("active", true)) return;
                JSONObject lease = findLease(apartment.optInt("active_lease_id"));
                if (!matchesSearch(object.optString("name"), apartment.optString("name"), lease == null ? "Свободно" : lease.optString("tenant"))) return;
                JSONObject charge = lease == null ? null : currentChargeForLease(lease.optInt("id"));
                LinearLayout card = premiumCard();
                LinearLayout heading = row();
                heading.addView(label(apartment.optString("name"), 21, text, true), new LinearLayout.LayoutParams(0, -2, 1));
                heading.addView(premiumChip(lease == null ? "Свободна" : "Заселена", lease == null ? blue : muted));
                card.addView(heading);
                card.addView(label(lease == null ? "Готова к заселению" : lease.optString("tenant"), 16, text, true));
                if (charge != null) {
                    card.addView(label(statusLabel(charge.optString("status")) + " · до " + compactDate(charge.optString("due_date")), 13, statusColor(charge.optString("status")), false));
                    if (charge.optDouble("debt") > 0) card.addView(label("Осталось " + money(charge.optDouble("debt")), 18, text, true));
                } else card.addView(label(lease == null ? "Открыть и заселить  ›" : "Открыть договор  ›", 13, muted, false));
                MobileUi.makeClickable(card);
                card.setOnClickListener(v -> showPremiumPropertyDetails(apartment, lease, charge));
                group.addView(card);
            });
            if (group.getChildCount() > 0) { list.addView(section(object.optString("name"))); list.addView(group); }
        });
        if (list.getChildCount() == 0) addEmpty(list, "Квартиры не найдены", searchQuery.isEmpty() ? "Активные квартиры появятся после добавления объекта." : "Попробуйте другое имя или номер квартиры.");
    }

    private void showPremiumPropertyDetails(JSONObject apartment, JSONObject lease, JSONObject charge) {
        LinearLayout form = dialogForm();
        LinearLayout hero = premiumCard();
        hero.addView(label(joinNonEmpty(apartment.optString("object_name"), apartment.optString("name")), 22, text, true));
        hero.addView(label(lease == null ? "Свободно" : lease.optString("tenant"), 14, muted, false));
        hero.addView(label(lease == null ? "Нет активного договора" : "Аренда " + money(lease.optDouble("ip_amount") + lease.optDouble("personal_amount")), 16, text, true));
        if (charge != null) {
            hero.addView(label("Следующая оплата " + compactDate(charge.optString("due_date")) + " · " + statusLabel(charge.optString("status")), 13, statusColor(charge.optString("status")), true));
        }
        hero.addView(label(lease == null ? "Договор: пусто" : "Договор: активен с " + lease.optString("start_date"), 13, muted, false));
        form.addView(hero);
        if (lease != null) {
            form.addView(primaryButton("Записать оплату", v -> showManualPaymentFor(lease.optInt("id"))), new LinearLayout.LayoutParams(-1, dp(52)));
            form.addView(secondaryButton("Отправить напоминание", v -> {
                JSONObject target = new JSONObject();
                try {
                    target.put("lease_id", lease.optInt("id"));
                } catch (Exception ignored) {
                }
                String template = charge != null && "overdue".equals(charge.optString("status")) ? "message_rent_overdue" : "message_rent_due";
                previewMessage(target, template);
            }), new LinearLayout.LayoutParams(-1, -2));
            form.addView(secondaryButton("История", v -> download("/api/reports/history.xlsx?apartment_id=" + apartment.optInt("id"), "history.xlsx")), new LinearLayout.LayoutParams(-1, -2));
        }
        if (lease != null) {
            form.addView(section("Договор"));
            form.addView(secondaryButton("Изменить условия", v -> showOnboardDialog(lease)));
            if (charge != null) form.addView(secondaryButton("Отсрочка оплаты", v -> deferRent(charge)));
            form.addView(secondaryButton(lease.optBoolean("ignored") ? "Вернуть из архива" : "В архив", v -> confirm("Изменить архивный статус договора? Долги и история сохранятся.", () -> toggleLeaseIgnore(lease))));
            form.addView(secondaryButton("Оформить выезд", v -> moveOut(lease)));
        } else {
            form.addView(primaryButton("Заселить жильца", v -> showOnboardDialog(null)));
        }
        new AlertDialog.Builder(this)
            .setTitle("Квартира")
            .setView(wrapDialog(form))
            .setPositiveButton("Закрыть", null)
            .show();
    }

    private void renderPremiumPayments() {
        screenTitle.setText("Оплаты");
        content.removeAllViews();
        LinearLayout actions = row();
        addAction(actions, "Принять оплату", () -> showManualPaymentDialog(), true);
        addAction(actions, "Чеки · " + suspiciousReceipts.length(), () -> navigate("receipts"), false);
        content.addView(actions);
        android.widget.HorizontalScrollView filters = new android.widget.HorizontalScrollView(this);
        filters.setHorizontalScrollBarEnabled(false);
        LinearLayout options = row();
        String[] titles = {"К оплате", "Просрочено", "Оплачено", "Все"};
        String[] keys = {"open", "overdue", "paid", "all"};
        for (int i = 0; i < keys.length; i++) {
            Button filter = paymentFilterButton(titles[i], keys[i]);
            LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(-2, -2);
            params.setMargins(0, dp(4), dp(8), dp(8));
            options.addView(filter, params);
        }
        filters.addView(options);
        content.addView(filters);
        LinearLayout list = MobileUi.column(this);
        addSearch("Жилец, квартира или услуга", () -> fillPayments(list));
        content.addView(list);
        fillPayments(list);
    }

    private void fillPayments(LinearLayout list) {
        list.removeAllViews();
        forEach(rentCharges, charge -> {
            if (!premiumPaymentMatches(charge.optString("status"), charge.optString("due_date")) || !matchesSearch(charge.optString("tenant"), charge.optString("object"), charge.optString("apartment"), "Аренда")) return;
            LinearLayout card = paymentCard("Аренда", charge, charge.optDouble("total_due"));
            card.addView(secondaryButton("Оплата и отсрочка", v -> showRentActions(charge)));
            list.addView(card);
        });
        forEach(utilityBills, bill -> {
            if ("draft".equals(bill.optString("status"))) return;
            forEach(arr(bill, "lines"), line -> {
                if (!premiumPaymentMatches(line.optString("status"), line.optString("due_date")) || !matchesSearch(line.optString("tenant"), line.optString("object"), line.optString("apartment"), line.optString("service"), "Коммуналка")) return;
                LinearLayout card = paymentCard(firstNonEmpty(line.optString("service"), bill.optString("service"), "Коммуналка"), line, line.optDouble("total_amount"));
                card.addView(secondaryButton("Записать оплату", v -> {
                    selectedPaymentKind = "utility";
                    showManualPaymentFor(line.optInt("lease_id"));
                }));
                list.addView(card);
            });
        });
        if (list.getChildCount() == 0) addEmpty(list, "Платежи не найдены", "Измените поиск или выберите другой статус.");
    }

    private LinearLayout paymentCard(String type, JSONObject item, double amount) {
        LinearLayout card = premiumCard();
        LinearLayout top = row();
        top.addView(label(type, 13, muted, false), new LinearLayout.LayoutParams(0, -2, 1));
        top.addView(premiumChip(statusLabel(item.optString("status")), statusColor(item.optString("status"))));
        card.addView(top);
        card.addView(label(item.optString("tenant"), 18, text, true));
        card.addView(label(joinNonEmpty(item.optString("object"), item.optString("apartment")), 13, muted, false));
        card.addView(label(money(item.has("debt") && !isDoneStatus(item.optString("status")) ? item.optDouble("debt") : amount), 26, text, true));
        card.addView(label((isDoneStatus(item.optString("status")) || !item.has("debt") ? "Начислено" : "Осталось") + " · срок " + compactDate(item.optString("due_date")), 13, muted, false));
        return card;
    }

    private void showRentActions(JSONObject charge) {
        LinearLayout form = dialogForm();
        form.addView(label(charge.optString("tenant"), 22, text, true));
        form.addView(label(joinNonEmpty(charge.optString("object"), charge.optString("apartment")), 14, muted, false));
        form.addView(premiumValueLine("ИП · оплачено / начислено", moneyPair(charge.optDouble("ip_paid"), charge.optDouble("ip_due"))));
        form.addView(premiumValueLine("Перевод · оплачено / начислено", moneyPair(charge.optDouble("personal_paid"), charge.optDouble("personal_due"))));
        form.addView(primaryButton("Принять на ИП", v -> payRent(charge, "ip")));
        form.addView(secondaryButton("Принять перевод", v -> payRent(charge, "personal")));
        form.addView(secondaryButton("Дать отсрочку", v -> deferRent(charge)));
        new AlertDialog.Builder(this).setTitle("Аренда · " + compactDate(charge.optString("due_date"))).setView(wrapDialog(form)).setNegativeButton("Закрыть", null).show();
    }

    private void showManualPaymentFor(int leaseId) {
        selectedPaymentLeaseId = leaseId;
        showManualPaymentDialog();
    }

    private void renderPremiumTasks() {
        screenTitle.setText("Дела");
        content.removeAllViews();
        content.addView(label("Здесь собраны действия, которым нужно ваше внимание.", 14, muted, false));
        List<MonthTask> tasks = buildPremiumTasks(obj(bootstrap, "dashboard"));
        addPremiumTaskGroup(tasks, "Сегодня и просрочено", 0);
        addPremiumTaskGroup(tasks, "На неделе", 1);
        addPremiumTaskGroup(tasks, "Позже", 2);
        if (tasks.isEmpty()) addEmpty(content, "На сегодня всё", "Новые платежи, показания и чеки появятся здесь.");
        content.addView(section("Напоминания жильцам"));
        content.addView(secondaryButton("Запустить напоминания", v -> confirm("Отправить жильцам положенные напоминания сейчас?", () -> runApi("Напоминания", () -> api.postJson("/api/reminders/run", new JSONObject()), value -> syncAfterMutation()))));
    }

    private List<MonthTask> buildPremiumTasks(JSONObject dashboard) {
        List<MonthTask> tasks = new ArrayList<>();
        collectPremiumTasks(tasks, dashboard, "utility_overdue", "Проверить коммуналку", "Счёт просрочен", red);
        collectPremiumTasks(tasks, dashboard, "rent_overdue", "Напомнить об аренде", "Оплата просрочена", red);
        collectPremiumTasks(tasks, dashboard, "rent_today", "Подтвердить аренду", "Срок сегодня", orange);
        collectPremiumTasks(tasks, dashboard, "manual_debts", "Закрыть ручной долг", "Нужна ручная проверка", orange);
        collectPremiumTasks(tasks, dashboard, "suspicious_receipts", "Проверить чек", "Нужно решение", red);
        collectPremiumTasks(tasks, dashboard, "provider_reading_due", "Передать показания", "Срок поставщику близко", orange);
        forEach(arr(dashboard, "monthly_reports"), report -> tasks.add(new MonthTask(1, "Проверить месячный отчёт", report.optString("title"), orange, () -> navigate("reports"))));
        forEach(rentCharges, charge -> {
            if (isDoneStatus(charge.optString("status")) || !isFutureDate(charge.optString("due_date"))) return;
            int group = daysFromToday(charge.optString("due_date")) <= 7 ? 1 : 2;
            tasks.add(new MonthTask(group, "Будущая аренда: " + compactApartment(charge), charge.optString("tenant") + " · " + compactDate(charge.optString("due_date")), gray, () -> showRentActions(charge)));
        });
        return tasks;
    }

    private void collectPremiumTasks(List<MonthTask> tasks, JSONObject dashboard, String key, String title, String step, int color) {
        forEach(arr(dashboard, key), item -> {
            String due = firstNonEmpty(item.optString("due_date"), item.optString("created_at"), today());
            int days = daysFromToday(due);
            int group = days <= 0 ? 0 : days <= 7 ? 1 : 2;
            tasks.add(new MonthTask(group, title + ": " + attentionGroupTitle(item, title), joinNonEmpty(step, compactDate(due), item.optString("tenant")), color, () -> openAttention(key, item)));
        });
    }

    private void addPremiumTaskGroup(List<MonthTask> tasks, String title, int group) {
        boolean hasItems = false;
        for (MonthTask task : tasks) {
            if (task.group != group) continue;
            if (!hasItems) {
                content.addView(section(title));
                hasItems = true;
            }
            LinearLayout card = premiumCardWithAccent(task.color);
            card.addView(label(task.title, 17, text, true));
            card.addView(label(task.detail, 13, muted, false));
            card.addView(label("Открыть дело  ›", 13, blue, true));
            MobileUi.makeClickable(card);
            card.setOnClickListener(v -> { if (task.action != null) task.action.run(); else navigate("payments"); });
            content.addView(card);
        }
    }

    private Button paymentFilterButton(String title, String filter) {
        Button button = pillButton(title, filter.equals(premiumPaymentFilter));
        button.setOnClickListener(v -> {
            premiumPaymentFilter = filter;
            renderPremiumPayments();
        });
        return button;
    }

    private boolean premiumPaymentMatches(String status, String dueDate) {
        if ("all".equals(premiumPaymentFilter)) return true;
        if ("open".equals(premiumPaymentFilter)) return !isDoneStatus(status);
        if ("paid".equals(premiumPaymentFilter)) return isDoneStatus(status);
        if ("overdue".equals(premiumPaymentFilter)) return isCriticalStatus(status) && !isFutureDate(dueDate);
        return !isDoneStatus(status) && !isCriticalStatus(status);
    }

    private JSONObject findLease(int leaseId) {
        if (leaseId <= 0) return null;
        JSONArray leases = arr(bootstrap, "leases");
        for (int i = 0; i < leases.length(); i++) {
            JSONObject lease = leases.optJSONObject(i);
            if (lease != null && lease.optInt("id") == leaseId) return lease;
        }
        return null;
    }

    private JSONObject currentChargeForLease(int leaseId) {
        JSONObject fallback = null;
        for (int i = 0; i < rentCharges.length(); i++) {
            JSONObject charge = rentCharges.optJSONObject(i);
            if (charge == null || charge.optInt("lease_id") != leaseId) continue;
            if (monthKey(charge.optString("due_date")).equals(selectedMonthKey())) return charge;
            if (fallback == null) fallback = charge;
        }
        return fallback;
    }

    private LinearLayout premiumCard() {
        return card();
    }

    private LinearLayout premiumCardWithAccent(int accent) {
        LinearLayout card = card();
        card.setBackground(MobileUi.shape(this, surface, 22, MobileUi.alpha(accent, 100)));
        return card;
    }

    private LinearLayout premiumStatLine(String title, double value) {
        LinearLayout line = row();
        line.setPadding(0, dp(2), 0, dp(2));
        line.addView(label(title, 13, muted, false), new LinearLayout.LayoutParams(0, -2, 1));
        TextView amount = label(money(value), 14, text, true);
        amount.setGravity(Gravity.RIGHT);
        line.addView(amount, new LinearLayout.LayoutParams(0, -2, 1));
        return line;
    }

    private TextView premiumChip(String title, int color) {
        return MobileUi.chip(this, title, color);
    }

    private int daysFromToday(String value) {
        String iso = isoDate(value);
        if (iso.length() < 10) return 999;
        try {
            java.text.SimpleDateFormat format = new java.text.SimpleDateFormat("yyyy-MM-dd", Locale.US);
            long target = format.parse(iso).getTime();
            long now = format.parse(today()).getTime();
            return Math.round((target - now) / 86400000f);
        } catch (Exception ignored) {
            return 999;
        }
    }

    private void renderDashboard() {
        screenTitle.setText("Пульт");
        content.removeAllViews();
        JSONObject dashboard = obj(bootstrap, "dashboard");
        if (showSection("dashboard_hero")) {
            addHero("Дела по аренде", "Сводка по платежам, отчётам, счётчикам и коммунальным задачам.");
        }

        if (showSection("dashboard_progress")) {
            addMonthProgressCard(dashboard);
        }

        if (showSection("dashboard_metrics")) {
            LinearLayout grid = new LinearLayout(this);
            grid.setOrientation(LinearLayout.VERTICAL);
            content.addView(grid);
            addMetricRow(grid, "Просрочка аренды", len(dashboard, "rent_overdue"), "Коммуналка", len(dashboard, "utility_overdue"));
            addMetricRow(grid, "Сегодня оплата", len(dashboard, "rent_today"), "Отчёты", len(dashboard, "monthly_reports"));
            addMetricRow(grid, "Чеки проверить", len(dashboard, "suspicious_receipts"), "Счётчики", len(dashboard, "provider_reading_due") + len(dashboard, "stale_readings"));
        }

        JSONArray reports = arr(dashboard, "monthly_reports");
        if (showSection("dashboard_reports") && reports.length() > 0) {
            content.addView(section("Месячные отчёты"));
            forEach(reports, item -> {
                LinearLayout card = card();
                card.addView(label(item.optString("title", "Отчёт"), 18, text, true));
                card.addView(label(item.optString("issue_count", "0") + " проблем · " + severityLabel(item.optString("severity")), 13, muted, false));
                Button download = secondaryButton("Скачать");
                download.setOnClickListener(v -> download("/api/reports/monthly.xlsx?year=" + item.optInt("year") + "&month=" + item.optInt("month") + "&kind=" + item.optString("kind", "full"), "monthly.xlsx"));
                card.addView(download);
                content.addView(card);
            });
        }

        if (showSection("dashboard_attention")) {
            content.addView(section("Требует внимания"));
            addGroupedAttentionCards(dashboard);
        }
        if (content.getChildCount() < 5) {
            LinearLayout ok = card();
            ok.addView(label("Критичных задач нет", 19, text, true));
            ok.addView(label("Все обязательные действия на текущий момент закрыты.", 14, muted, false));
            content.addView(ok);
        }
    }

    private void addMonthProgressCard(JSONObject dashboard) {
        ensureProgressRentData();
        MonthScore score = buildMonthScore(dashboard);
        LinearLayout card = card();
        card.setPadding(dp(14), dp(12), dp(14), dp(12));

        TextView month = label(monthTitle(selectedMonth), 21, text, true);
        month.setGravity(Gravity.CENTER);
        card.addView(month);
        TextView hint = label("Свайп влево или вправо меняет месяц", 12, muted, false);
        hint.setGravity(Gravity.CENTER);
        card.addView(hint);

        LinearLayout chartRow = new LinearLayout(this);
        chartRow.setOrientation(LinearLayout.HORIZONTAL);
        chartRow.setGravity(Gravity.CENTER);
        Button prev = monthArrow("<", -1);
        Button next = monthArrow(">", 1);
        MonthProgressView chart = new MonthProgressView(this);
        chart.setScore(score);
        chartRow.addView(prev, new LinearLayout.LayoutParams(dp(42), dp(132)));
        chartRow.addView(chart, new LinearLayout.LayoutParams(0, dp(138), 1));
        chartRow.addView(next, new LinearLayout.LayoutParams(dp(42), dp(132)));
        card.addView(chartRow);

        LinearLayout legend = row();
        legend.addView(legendItem("Выполнено", green), new LinearLayout.LayoutParams(0, -2, 1));
        legend.addView(legendItem("В работе", orange), new LinearLayout.LayoutParams(0, -2, 1));
        legend.addView(legendItem("Долги", red), new LinearLayout.LayoutParams(0, -2, 1));
        legend.addView(legendItem("Будущие", gray), new LinearLayout.LayoutParams(0, -2, 1));
        card.addView(legend);

        View.OnTouchListener monthSwipe = (view, event) -> {
            if (event.getAction() == MotionEvent.ACTION_DOWN) {
                monthTouchStartX = event.getX();
                monthTouchStartY = event.getY();
                return true;
            }
            if (event.getAction() == MotionEvent.ACTION_UP) {
                float dx = event.getX() - monthTouchStartX;
                float dy = event.getY() - monthTouchStartY;
                if (Math.abs(dx) > dp(48)) {
                    shiftSelectedMonth(dx < 0 ? 1 : -1);
                    renderCurrentTab();
                } else if (Math.abs(dx) < dp(18) && Math.abs(dy) < dp(18)) {
                    showMonthTasksDialog(dashboard);
                }
                return true;
            }
            return true;
        };
        attachMonthSwipe(card, monthSwipe);
        content.addView(card);
    }

    private Button monthArrow(String title, int delta) {
        Button button = secondaryButton(title);
        button.setTextSize(20);
        button.setPadding(0, 0, 0, 0);
        button.setOnClickListener(v -> {
            shiftSelectedMonth(delta);
            renderCurrentTab();
        });
        return button;
    }

    private void attachMonthSwipe(View view, View.OnTouchListener listener) {
        if (!(view instanceof Button)) {
            view.setOnTouchListener(listener);
        }
        if (view instanceof LinearLayout) {
            LinearLayout layout = (LinearLayout) view;
            for (int i = 0; i < layout.getChildCount(); i++) {
                attachMonthSwipe(layout.getChildAt(i), listener);
            }
        }
    }

    private TextView legendItem(String title, int color) {
        TextView view = label("● " + title, 11, color, true);
        view.setGravity(Gravity.CENTER);
        return view;
    }

    private void shiftSelectedMonth(int delta) {
        selectedMonth.add(Calendar.MONTH, delta);
    }

    private MonthScore buildMonthScore(JSONObject dashboard) {
        MonthScore score = new MonthScore();
        JSONArray scoreRentCharges = rentChargesForSelectedMonth();
        JSONArray scoreUtilityBills = utilityBillsForSelectedMonth();
        JSONArray scoreProviderReadings = providerReadingsForSelectedMonth();
        boolean detailedData = scoreRentCharges.length() > 0 || scoreUtilityBills.length() > 0 || scoreProviderReadings.length() > 0;

        forEach(scoreRentCharges, charge -> {
            if (!sameSelectedMonth(scoreDateForItem("rent", charge))) return;
            addTaskByStatus(score, charge.optString("status"), charge.optString("due_date"), true);
        });

        forEach(scoreUtilityBills, bill -> {
            if (!sameSelectedMonth(scoreDateForItem("utility", bill))) return;
            addTaskByStatus(score, bill.optBoolean("provider_paid") ? "paid" : "issued", bill.optString("due_date"), false);
            JSONArray lines = arr(bill, "lines");
            forEach(lines, line -> addTaskByStatus(score, line.optString("status"), line.optString("due_date"), true));
        });
        addMissingUtilityBillScore(score, scoreUtilityBills);
        forEach(scoreProviderReadings, item -> addTaskByStatus(score, item.optString("status"), item.optString("due_date"), false));

        forEach(arr(dashboard, "monthly_reports"), report -> {
            if (report.optInt("year") == selectedMonth.get(Calendar.YEAR)
                && report.optInt("month") == selectedMonth.get(Calendar.MONTH) + 1) {
                addTaskByStatus(score, report.optString("severity", "open"), null, false);
            }
        });

        if (sameSelectedMonth(today())) {
            forEach(arr(dashboard, "provider_reading_due"), item -> score.warning++);
            if (scoreProviderReadings.length() == 0) {
                forEach(arr(dashboard, "stale_readings"), item -> score.warning++);
            }
            if (!detailedData) {
                forEach(arr(dashboard, "provider_debts"), item -> addTaskByStatus(score, "issued", item.optString("due_date"), false));
            }
        }

        if (!detailedData) {
            collectScoreFromDashboard(score, dashboard, "rent_overdue", true);
            collectScoreFromDashboard(score, dashboard, "rent_partial", true);
            collectScoreFromDashboard(score, dashboard, "rent_today", false);
            collectScoreFromDashboard(score, dashboard, "rent_deferred", false);
            collectScoreFromDashboard(score, dashboard, "utility_overdue", true);
            collectScoreFromDashboard(score, dashboard, "utility_partial", true);
            collectScoreFromDashboard(score, dashboard, "utility_issued", false);
            collectScoreFromDashboard(score, dashboard, "manual_debts", true);
            collectScoreFromDashboard(score, dashboard, "provider_reading_due", false);
            collectScoreFromDashboard(score, dashboard, "suspicious_receipts", true);
        }

        if (score.total() == 0) {
            if (isSelectedMonthFuture()) {
                score.upcoming = 1;
            } else {
                score.done = 1;
            }
        }

        return score;
    }

    private void collectScoreFromDashboard(MonthScore score, JSONObject dashboard, String key, boolean critical) {
        forEach(arr(dashboard, key), item -> {
            String date = scoreDateForItem(key, item);
            if (!sameSelectedMonth(date)) return;
            addTaskByStatus(score, critical ? "overdue" : item.optString("status", "issued"), item.optString("due_date"), critical);
        });
    }

    private void addMissingUtilityBillScore(MonthScore score, JSONArray monthBills) {
        Set<Integer> billedServices = new LinkedHashSet<>();
        forEach(monthBills, bill -> billedServices.add(bill.optInt("service_id")));
        forEach(arr(bootstrap, "services"), service -> {
            if (!service.optBoolean("active", true)) return;
            if (billedServices.contains(service.optInt("id"))) return;
            addTaskByStatus(score, "issued", selectedMonthEnd(), false);
        });
    }

    private JSONArray rentChargesForSelectedMonth() {
        if (selectedMonthKey().equals(progressRentMonthKey)) {
            return progressRentCharges;
        }
        JSONArray result = new JSONArray();
        for (int i = 0; i < rentCharges.length(); i++) {
            JSONObject item = rentCharges.optJSONObject(i);
            if (item != null && sameSelectedMonth(scoreDateForItem("rent", item))) {
                result.put(item);
            }
        }
        return result;
    }

    private JSONArray utilityBillsForSelectedMonth() {
        if (selectedMonthKey().equals(progressRentMonthKey) && progressUtilityBills.length() > 0) {
            return progressUtilityBills;
        }
        JSONArray result = new JSONArray();
        for (int i = 0; i < utilityBills.length(); i++) {
            JSONObject item = utilityBills.optJSONObject(i);
            if (item != null && sameSelectedMonth(scoreDateForItem("utility", item))) {
                result.put(item);
            }
        }
        return result;
    }

    private JSONArray providerReadingsForSelectedMonth() {
        if (selectedMonthKey().equals(progressRentMonthKey)) {
            return progressProviderReadings;
        }
        return new JSONArray();
    }

    private void ensureProgressRentData() {
        String key = selectedMonthKey();
        if (key.equals(progressRentMonthKey) || progressRentLoading) return;
        progressRentLoading = true;
        progressLoadFailed = false;
        int year = selectedMonth.get(Calendar.YEAR);
        int month = selectedMonth.get(Calendar.MONTH) + 1;
        new Thread(() -> {
            try {
                JSONObject loaded = api.getJson("/api/month-progress?year=" + year + "&month=" + month);
                runOnUiThread(() -> {
                    if (isFinishing() || isDestroyed()) return;
                    progressRentCharges = arr(loaded, "rent_charges");
                    progressUtilityBills = arr(loaded, "utility_bills");
                    progressProviderReadings = arr(loaded, "provider_readings");
                    progressMonthSummary = obj(loaded, "summary");
                    progressRentMonthKey = key;
                    progressRentLoading = false;
                    if ("dashboard".equals(currentTab)) renderCurrentTab();
                });
            } catch (Exception error) {
                runOnUiThread(() -> {
                    if (isFinishing() || isDestroyed()) return;
                    progressRentLoading = false;
                    progressLoadFailed = true;
                    progressRentMonthKey = key;
                    progressMonthSummary = new JSONObject();
                    if ("dashboard".equals(currentTab)) renderCurrentTab();
                });
            }
        }, "rental-month-summary").start();
    }

    private String scoreDateForItem(String key, JSONObject item) {
        if (key != null && key.startsWith("rent")) {
            return firstNonEmpty(item.optString("due_date"), item.optString("period_start"), item.optString("period_end"));
        }
        if (key != null && key.startsWith("utility")) {
            return firstNonEmpty(item.optString("bill_period_start"), item.optString("period_start"), item.optString("bill_period_end"), item.optString("period_end"), item.optString("due_date"));
        }
        if ("manual_debts".equals(key)) {
            return firstNonEmpty(item.optString("period_start"), item.optString("period_end"), item.optString("due_date"));
        }
        if ("provider_debts".equals(key)) {
            return firstNonEmpty(item.optString("period_start"), item.optString("period_end"), item.optString("due_date"));
        }
        if ("provider_reading_due".equals(key) || "provider_readings".equals(key)) {
            return firstNonEmpty(item.optString("period_start"), item.optString("due_date"));
        }
        return firstNonEmpty(item.optString("due_date"), item.optString("period_start"), item.optString("period_end"), item.optString("created_at"));
    }

    private void addTaskByStatus(MonthScore score, String status, String dueDate, boolean debtCanBeCritical) {
        if (isFutureDate(dueDate)) {
            score.upcoming++;
        } else if (isDoneStatus(status)) {
            score.done++;
        } else if (debtCanBeCritical && isCriticalStatus(status)) {
            score.critical++;
        } else {
            score.warning++;
        }
    }

    private void showMonthTasksDialog(JSONObject dashboard) {
        LinearLayout form = dialogForm();
        List<MonthTask> tasks = buildMonthTasks(dashboard);
        if (progressRentLoading) {
            form.addView(monthTaskCard("Аренда за месяц догружается", "Откройте список ещё раз через пару секунд.", gray));
        }
        addTaskSection(form, tasks, "Долги", 0, red);
        addTaskSection(form, tasks, "В работе и предстоит", 1, orange);
        addTaskSection(form, tasks, "Выполнено", 2, green);
        if (form.getChildCount() == 0) {
            form.addView(monthTaskCard("Нет данных по выбранному месяцу", "Для будущего месяца задачи появятся после генерации начислений.", gray));
        }
        new AlertDialog.Builder(this)
            .setTitle("Дела: " + monthTitle(selectedMonth))
            .setView(wrapDialog(form))
            .setPositiveButton("Закрыть", null)
            .show();
    }

    private List<MonthTask> buildMonthTasks(JSONObject dashboard) {
        List<MonthTask> tasks = new ArrayList<>();
        forEach(rentChargesForSelectedMonth(), charge -> addRentMonthTasks(tasks, charge));
        JSONArray monthBills = utilityBillsForSelectedMonth();
        forEach(monthBills, bill -> addUtilityMonthTasks(tasks, bill));
        addMissingUtilityBillTasks(tasks, monthBills);
        forEach(providerReadingsForSelectedMonth(), item -> addProviderReadingTask(tasks, item));
        forEach(arr(dashboard, "monthly_reports"), report -> {
            if (report.optInt("year") == selectedMonth.get(Calendar.YEAR)
                && report.optInt("month") == selectedMonth.get(Calendar.MONTH) + 1) {
                tasks.add(new MonthTask(1, "Месячный отчёт", joinNonEmpty(severityLabel(report.optString("severity")), report.optString("issue_count") + " проблем"), orange));
            }
        });
        if (sameSelectedMonth(today()) && providerReadingsForSelectedMonth().length() == 0) {
            forEach(arr(dashboard, "provider_reading_due"), item ->
                tasks.add(new MonthTask(1, shortObjectName(item.optString("object")) + " " + item.optString("service"), "показания поставщику до " + compactDate(item.optString("due_date")), orange))
            );
        }
        return tasks;
    }

    private void addMissingUtilityBillTasks(List<MonthTask> tasks, JSONArray monthBills) {
        Set<Integer> billedServices = new LinkedHashSet<>();
        forEach(monthBills, bill -> billedServices.add(bill.optInt("service_id")));
        forEach(arr(bootstrap, "services"), service -> {
            if (!service.optBoolean("active", true)) return;
            if (billedServices.contains(service.optInt("id"))) return;
            boolean future = isFutureDate(selectedMonthEnd());
            String object = shortObjectName(service.optString("object"));
            String title = object + " " + service.optString("name") + " счета арендаторам";
            tasks.add(new MonthTask(1, title, future ? "предстоит" : "не выставлены за месяц", future ? gray : orange));
        });
    }

    private void addProviderReadingTask(List<MonthTask> tasks, JSONObject item) {
        String title = shortObjectName(item.optString("object")) + " " + item.optString("service") + " показания";
        String status = item.optString("status");
        String due = compactDate(item.optString("due_date"));
        String readingDate = compactDate(item.optString("reading_date"));
        if (isDoneStatus(status)) {
            tasks.add(new MonthTask(2, title, "✅ " + fallbackDate(readingDate, due), green));
        } else if (isFutureDate(item.optString("due_date"))) {
            tasks.add(new MonthTask(1, title + " до " + due, "предстоит", gray));
        } else {
            tasks.add(new MonthTask(1, title + " до " + due, "не переданы поставщику", orange));
        }
    }

    private void addRentMonthTasks(List<MonthTask> tasks, JSONObject charge) {
        String apartment = compactApartment(charge);
        double ipDue = charge.optDouble("ip_due");
        double ipPaid = charge.optDouble("ip_paid");
        double personalDue = charge.optDouble("personal_due");
        double personalPaid = charge.optDouble("personal_paid");
        String due = compactDate(charge.optString("due_date"));
        List<String> doneParts = new ArrayList<>();

        if (personalDue > 0.009) {
            String paidAt = paymentDate(arr(charge, "payments"), "personal");
            if (personalPaid + 0.009 >= personalDue) {
                doneParts.add("по номеру ✅ " + fallbackDate(paidAt, due));
            } else {
                int group = isFutureDate(charge.optString("due_date")) ? 1 : 0;
                String suffix = group == 0 ? "Долги" : "предстоит";
                tasks.add(new MonthTask(group, apartment + " платёж по номеру " + due, suffix, group == 0 ? red : gray));
            }
        }

        if (ipDue > 0.009) {
            String paidAt = firstNonEmpty(paymentDate(arr(charge, "payments"), "ip"), paymentDate(arr(charge, "payments"), "expense_fund"));
            if (ipPaid + 0.009 >= ipDue) {
                doneParts.add("ИП ✅ " + fallbackDate(paidAt, due));
            } else {
                int group = isFutureDate(charge.optString("due_date")) ? 1 : 0;
                String suffix = group == 0 ? "Долги" : "предстоит";
                tasks.add(new MonthTask(group, apartment + " платёж на ИП " + due, suffix, group == 0 ? red : gray));
            }
        }

        if (!doneParts.isEmpty()) {
            tasks.add(new MonthTask(2, apartment + " аренда", join(" | ", doneParts), green));
        }
        if ("deferred".equals(charge.optString("status"))) {
            tasks.add(new MonthTask(1, apartment + " отсрочка", compactDate(charge.optString("deferral_until")), orange));
        }
    }

    private void addUtilityMonthTasks(List<MonthTask> tasks, JSONObject bill) {
        if (!sameSelectedMonth(scoreDateForItem("utility", bill))) return;
        String service = bill.optString("service", "коммуналка");
        String object = shortObjectName(bill.optString("object"));
        String due = compactDate(bill.optString("due_date"));
        if (bill.optBoolean("provider_paid")) {
            tasks.add(new MonthTask(2, object + " поставщик " + service, "✅ " + compactDate(bill.optString("provider_paid_at")), green));
        } else {
            boolean future = isFutureDate(bill.optString("due_date"));
            tasks.add(new MonthTask(future ? 1 : 1, object + " поставщик " + service + " " + due, future ? "предстоит" : "в работе", future ? gray : orange));
        }
        forEach(arr(bill, "lines"), line -> {
            String apartment = compactApartment(line);
            String lineDue = compactDate(line.optString("due_date"));
            String paidAt = paymentDate(arr(line, "payments"), "utilities");
            if (isDoneStatus(line.optString("status"))) {
                tasks.add(new MonthTask(2, apartment + " коммуналка " + service, "✅ " + fallbackDate(paidAt, lineDue), green));
            } else if (isFutureDate(line.optString("due_date"))) {
                tasks.add(new MonthTask(1, apartment + " коммуналка " + service + " " + lineDue, "предстоит", gray));
            } else if (isCriticalStatus(line.optString("status"))) {
                tasks.add(new MonthTask(0, apartment + " коммуналка " + service + " " + lineDue, "Долги", red));
            } else {
                tasks.add(new MonthTask(1, apartment + " коммуналка " + service + " " + lineDue, statusLabel(line.optString("status")), orange));
            }
        });
    }

    private void addTaskSection(LinearLayout form, List<MonthTask> tasks, String title, int group, int color) {
        boolean hasItems = false;
        for (MonthTask task : tasks) {
            if (task.group == group) {
                if (!hasItems) {
                    form.addView(section(title));
                    hasItems = true;
                }
                form.addView(monthTaskCard(task.title, task.detail, task.color == 0 ? color : task.color));
            }
        }
    }

    private LinearLayout monthTaskCard(String title, String detail, int color) {
        LinearLayout card = cardWithAccent(color);
        card.setPadding(dp(12), dp(8), dp(12), dp(8));
        card.addView(label(title, 15, text, true));
        if (detail != null && !detail.trim().isEmpty()) {
            card.addView(label(detail, 12, color == gray ? muted : color, true));
        }
        return card;
    }

    private void addGroupedAttentionCards(JSONObject dashboard) {
        Map<String, IssueGroup> groups = new LinkedHashMap<>();
        collectAttention(groups, dashboard, "rent_overdue", "Просрочена аренда", red, "payments");
        collectAttention(groups, dashboard, "rent_partial", "Частичная аренда", orange, "payments");
        collectAttention(groups, dashboard, "rent_today", "Срок оплаты сегодня", orange, "payments");
        collectAttention(groups, dashboard, "rent_deferred", "Отсрочка", orange, "payments");
        collectAttention(groups, dashboard, "utility_overdue", "Долг по коммуналке", red, "services");
        collectAttention(groups, dashboard, "utility_partial", "Частичная коммуналка", orange, "services");
        collectAttention(groups, dashboard, "utility_issued", "Коммуналка к оплате", orange, "services");
        collectAttention(groups, dashboard, "manual_debts", "Ручной долг", orange, "payments");
        collectAttention(groups, dashboard, "provider_reading_due", "Показания поставщику", orange, "services");
        collectAttention(groups, dashboard, "stale_readings", "Нет показаний", orange, "services");
        collectAttention(groups, dashboard, "provider_debts", "Поставщик не оплачен", orange, "services");
        collectAttention(groups, dashboard, "suspicious_receipts", "Чек на проверку", red, "payments");

        for (IssueGroup group : groups.values()) {
            LinearLayout card = cardWithAccent(group.color);
            card.setPadding(dp(14), dp(10), dp(14), dp(10));
            card.addView(label(group.title, 17, text, true));
            if (!group.tenant.isEmpty()) {
                card.addView(label(group.tenant, 12, muted, false));
            }
            for (String line : group.lines) {
                card.addView(label(line, 13, muted, false));
            }
            Button open = secondaryButton("Открыть");
            open.setOnClickListener(v -> {
                currentTab = group.targetTab;
                buildBottomNav();
                loadCurrentTab(false);
            });
            card.addView(open, new LinearLayout.LayoutParams(-1, -2));
            content.addView(card);
        }
    }

    private void collectAttention(Map<String, IssueGroup> groups, JSONObject dashboard, String key, String title, int color, String targetTab) {
        forEach(arr(dashboard, key), item -> {
            int effectiveColor = isFutureDate(item.optString("due_date")) ? gray : color;
            String groupKey = attentionGroupKey(item, key);
            IssueGroup group = groups.get(groupKey);
            if (group == null) {
                group = new IssueGroup();
                group.title = attentionGroupTitle(item, title);
                group.tenant = item.optString("tenant", "");
                group.targetTab = targetTab;
                group.color = effectiveColor;
                groups.put(groupKey, group);
            }
            if (severityRank(effectiveColor) > severityRank(group.color)) group.color = effectiveColor;
            if ("payments".equals(targetTab)) group.targetTab = "payments";
            String issueKey = attentionIssueKey(key, item);
            if (group.seen.add(issueKey)) {
                group.lines.add(attentionLine(key, title, item));
            }
        });
    }

    private String attentionIssueKey(String key, JSONObject item) {
        int id = item.optInt("id", item.hashCode());
        if (key.startsWith("utility")) return "utility:" + id;
        if (key.startsWith("rent")) return "rent:" + id;
        return key + ":" + id;
    }

    private String attentionGroupKey(JSONObject item, String fallback) {
        String object = item.optString("object", "").trim();
        String apartment = item.optString("apartment", "").trim();
        if (!apartment.isEmpty()) return object + "|" + apartment;
        String service = item.optString("service", "").trim();
        if (!service.isEmpty()) return object + "|" + service;
        return fallback + "|" + item.optInt("id", item.hashCode());
    }

    private String attentionGroupTitle(JSONObject item, String fallback) {
        String where = joinNonEmpty(item.optString("object"), item.optString("apartment"));
        if (!where.isEmpty()) return where;
        String service = joinNonEmpty(item.optString("object"), item.optString("service"));
        return service.isEmpty() ? fallback : service;
    }

    private String attentionLine(String key, String title, JSONObject item) {
        String period = periodMonth(item);
        String amount = item.has("debt") ? money(item.optDouble("debt")) : item.has("total_cost") ? money(item.optDouble("total_cost")) : "";
        String due = item.optString("due_date", "");
        if (key.startsWith("rent")) {
            return joinNonEmpty(title + (period.isEmpty() ? "" : " за " + period), amount.isEmpty() ? "" : "долг " + amount, due.isEmpty() ? "" : "срок " + due);
        }
        if (key.startsWith("utility")) {
            return joinNonEmpty(title + (item.optString("service").isEmpty() ? "" : " · " + item.optString("service")) + (period.isEmpty() ? "" : " за " + period), amount.isEmpty() ? "" : "долг " + amount, due.isEmpty() ? "" : "срок " + due);
        }
        if ("manual_debts".equals(key)) {
            return joinNonEmpty(item.optString("title", title) + (period.isEmpty() ? "" : " за " + period), amount.isEmpty() ? "" : "долг " + amount, due.isEmpty() ? "" : "срок " + due);
        }
        if ("stale_readings".equals(key)) {
            String last = item.optString("last_date", "");
            String days = item.has("days") && !item.isNull("days") ? item.optInt("days") + " дн." : "";
            return joinNonEmpty(title + " · " + item.optString("service"), last.isEmpty() ? "нет последнего показания" : "последнее " + last, days);
        }
        if ("provider_reading_due".equals(key)) {
            return joinNonEmpty(title + " · " + item.optString("service"), "срок " + compactDate(item.optString("due_date")), statusLabel(item.optString("status")));
        }
        if ("provider_debts".equals(key)) {
            return joinNonEmpty(title + " · " + item.optString("service") + (period.isEmpty() ? "" : " за " + period), item.has("total_cost") ? money(item.optDouble("total_cost")) : "", due.isEmpty() ? "" : "срок " + due);
        }
        return joinNonEmpty(title, item.optString("tenant"), amount);
    }

    private void renderTenants() {
        screenTitle.setText("Жильцы");
        content.removeAllViews();
        if (showSection("tenants_hero")) {
            addHero("Заселение и квартиры", "Активные договоры, квартиры и основные действия по жильцам.");
        }
        if (showSection("tenants_onboard")) {
            Button add = primaryButton("Заселить жильца");
            add.setOnClickListener(v -> showOnboardDialog(null));
            content.addView(add, new LinearLayout.LayoutParams(-1, dp(52)));
        }

        if (showSection("tenants_active_leases")) {
            content.addView(section("Активные договоры"));
            JSONArray leases = arr(bootstrap, "leases");
            forEach(leases, lease -> {
                if (!lease.optBoolean("active", true)) return;
                LinearLayout card = card();
                card.addView(label(lease.optString("tenant", "Жилец"), 19, text, true));
                card.addView(label(joinNonEmpty(lease.optString("object"), lease.optString("apartment")) + " · с " + lease.optString("start_date"), 14, muted, false));
                card.addView(label("ИП " + money(lease.optDouble("ip_amount")) + " · перевод " + money(lease.optDouble("personal_amount")), 14, muted, false));
                LinearLayout actions = row();
                actions.addView(smallButton("Изменить", v -> showOnboardDialog(lease)), new LinearLayout.LayoutParams(0, -2, 1));
                actions.addView(smallButton(lease.optBoolean("ignored") ? "Из архива" : "В архив", v -> toggleLeaseIgnore(lease)), new LinearLayout.LayoutParams(0, -2, 1));
                actions.addView(smallButton("Выезд", v -> moveOut(lease)), new LinearLayout.LayoutParams(0, -2, 1));
                card.addView(actions);
                content.addView(card);
            });
        }

        if (showSection("tenants_apartments")) {
            content.addView(section("Квартиры"));
            JSONArray objects = arr(bootstrap, "objects");
            forEach(objects, object -> {
                JSONArray apartments = arr(object, "apartments");
                forEach(apartments, apartment -> {
                    LinearLayout card = card();
                    card.addView(label(object.optString("name") + " · " + apartment.optString("name"), 17, text, true));
                    card.addView(label(apartment.optBoolean("active", true) ? "активна" : "неактивна", 13, apartment.optBoolean("active", true) ? green : muted, false));
                    Button toggle = secondaryButton(apartment.optBoolean("active", true) ? "Отключить" : "Включить");
                    toggle.setOnClickListener(v -> toggleApartment(apartment));
                    card.addView(toggle);
                    content.addView(card);
                });
            });
        }
    }

    private void showOnboardDialog(JSONObject existing) {
        boolean editing = existing != null;
        LinearLayout form = dialogForm();
        Spinner apartment = spinner(apartmentLabels(), apartmentIds());
        EditText name = input("ФИО", false);
        EditText phone = input("+7...", false);
        EditText telegram = input("@username", false);
        EditText whatsapp = input("+7...", false);
        EditText start = input("2026-05-21", false);
        EditText end = input("", false);
        EditText day = input("день оплаты", false);
        EditText ip = input("ИП", false);
        EditText personal = input("перевод", false);
        EditText deposit = input("залог", false);
        EditText notes = input("комментарий", false);
        if (editing) {
            selectSpinnerValue(apartment, String.valueOf(existing.optInt("apartment_id")));
            name.setText(existing.optString("tenant"));
            phone.setText(existing.optString("phone"));
            telegram.setText(existing.optString("telegram"));
            whatsapp.setText(existing.optString("whatsapp"));
            start.setText(existing.optString("start_date"));
            end.setText(existing.optString("end_date"));
            day.setText(String.valueOf(existing.optInt("payment_day")));
            ip.setText(String.valueOf(existing.optDouble("ip_amount")));
            personal.setText(String.valueOf(existing.optDouble("personal_amount")));
            deposit.setText(String.valueOf(existing.optDouble("deposit_amount")));
            notes.setText(existing.optString("notes"));
        } else {
            start.setText(today());
        }
        form.addView(field("Квартира", apartment));
        form.addView(field("ФИО", name));
        form.addView(field("Телефон", phone));
        form.addView(field("Telegram", telegram));
        form.addView(field("WhatsApp", whatsapp));
        form.addView(field("Дата заезда", start));
        form.addView(field("Дата выезда", end));
        form.addView(field("День оплаты", day));
        form.addView(field("Платёж на ИП", ip));
        form.addView(field("Личный перевод", personal));
        form.addView(field("Залог", deposit));
        form.addView(field("Комментарий", notes));
        new AlertDialog.Builder(this)
            .setTitle(editing ? "Изменить жильца" : "Заселить жильца")
            .setView(wrapDialog(form))
            .setPositiveButton("Сохранить", (dialog, which) -> {
                try {
                    JSONObject body = new JSONObject();
                    body.put("apartment_id", Integer.parseInt(spinnerValue(apartment)));
                    body.put("full_name", name.getText().toString());
                    body.put("phone", phone.getText().toString());
                    body.put("telegram", telegram.getText().toString());
                    body.put("whatsapp", whatsapp.getText().toString());
                    body.put("start_date", start.getText().toString());
                    body.put("end_date", end.getText().toString());
                    body.put("payment_day", emptyToNull(day));
                    body.put("ip_amount", number(ip));
                    body.put("personal_amount", number(personal));
                    body.put("deposit_amount", number(deposit));
                    body.put("notes", notes.getText().toString());
                    if (editing) {
                        int id = existing.optInt("id");
                        runApi("Сохраняю", () -> api.patchJson("/api/leases/" + id, body), value -> syncAfterMutation());
                    } else {
                        runApi("Заселяю", () -> api.postJson("/api/leases/onboard", body), value -> syncAfterMutation());
                    }
                } catch (Exception ex) {
                    toast(ex.getMessage());
                }
            })
            .setNegativeButton("Отмена", null)
            .show();
    }

    private void renderPayments() {
        screenTitle.setText("Оплаты");
        content.removeAllViews();
        if (showSection("payments_hero")) {
            addHero("Аренда и платежи", "Начисления, отсрочки, ручные оплаты и проверка чеков.");
        }
        if (showSection("payments_actions")) {
            LinearLayout actions = row();
            actions.addView(smallButton("Сгенерировать", v -> runApi("Генерирую", () -> api.postJson("/api/rent-charges/generate", new JSONObject()), value -> syncAfterMutation())), new LinearLayout.LayoutParams(0, -2, 1));
            actions.addView(smallButton("Ручная оплата", v -> showManualPaymentDialog()), new LinearLayout.LayoutParams(0, -2, 1));
            content.addView(actions);
        }

        if (showSection("payments_rent")) {
            content.addView(section("Аренда"));
            forEach(rentCharges, charge -> {
                int color = isFutureDate(charge.optString("due_date")) && !isDoneStatus(charge.optString("status")) ? gray : statusColor(charge.optString("status"));
                LinearLayout card = cardWithAccent(color);
                card.addView(label(joinNonEmpty(charge.optString("object"), charge.optString("apartment")), 18, text, true));
                card.addView(label(charge.optString("tenant") + " · срок " + charge.optString("due_date"), 14, muted, false));
                card.addView(label("К оплате " + money(charge.optDouble("total_due")) + " · долг " + money(charge.optDouble("debt")), 14, muted, false));
                card.addView(label(statusLabel(charge.optString("status")), 13, color, true));
                LinearLayout row1 = row();
                row1.addView(smallButton("ИП", v -> payRent(charge, "ip")), new LinearLayout.LayoutParams(0, -2, 1));
                row1.addView(smallButton("Перевод", v -> payRent(charge, "personal")), new LinearLayout.LayoutParams(0, -2, 1));
                row1.addView(smallButton("Отсрочка", v -> deferRent(charge)), new LinearLayout.LayoutParams(0, -2, 1));
                card.addView(row1);
                content.addView(card);
            });
        }

        if (showSection("payments_suspicious") && suspiciousReceipts.length() > 0) {
            content.addView(section("Чеки на проверку"));
            forEach(suspiciousReceipts, receipt -> {
                LinearLayout card = cardWithAccent(red);
                card.addView(label(money(receipt.optDouble("amount")), 18, text, true));
                card.addView(label(joinNonEmpty(receipt.optString("apartment"), receipt.optString("tenant"), receipt.optString("recipient_name")), 14, muted, false));
                LinearLayout actions2 = row();
                actions2.addView(smallButton("Принять", v -> moderateReceipt(receipt, "accept")), new LinearLayout.LayoutParams(0, -2, 1));
                actions2.addView(smallButton("Скрыть", v -> ignoreReceipt(receipt)), new LinearLayout.LayoutParams(0, -2, 1));
                card.addView(actions2);
                content.addView(card);
            });
        }
    }

    private void showManualPaymentDialog() {
        LinearLayout form = dialogForm();
        Spinner lease = spinner(leaseLabels(), leaseIds());
        if (selectedPaymentLeaseId > 0) selectSpinnerValue(lease, Integer.toString(selectedPaymentLeaseId));
        selectedPaymentLeaseId = 0;
        Spinner kind = spinner(new String[]{"Аренда", "Коммуналка", "Аванс коммуналки"}, new String[]{"rent", "utility", "utility_advance"});
        selectSpinnerValue(kind, selectedPaymentKind);
        selectedPaymentKind = "rent";
        Spinner channel = spinner(new String[]{"Перевод", "ИП", "На расходы"}, new String[]{"personal", "ip", "expense_fund"});
        EditText amount = input("0", false);
        EditText paidAt = input("2026-05-21T12:00", false);
        EditText notes = input("комментарий", false);
        paidAt.setText(today() + "T12:00");
        form.addView(field("Жилец", lease));
        form.addView(field("Тип", kind));
        form.addView(field("Канал", channel));
        form.addView(field("Сумма", amount));
        form.addView(field("Дата и время", paidAt));
        form.addView(field("Комментарий", notes));
        new AlertDialog.Builder(this)
            .setTitle("Ручная оплата")
            .setView(wrapDialog(form))
            .setPositiveButton("Добавить", (dialog, which) -> {
                try {
                    JSONObject body = new JSONObject();
                    body.put("lease_id", Integer.parseInt(spinnerValue(lease)));
                    body.put("kind", spinnerValue(kind));
                    body.put("channel", spinnerValue(channel));
                    body.put("source", "manual");
                    body.put("amount", number(amount));
                    body.put("paid_at", paidAt.getText().toString());
                    body.put("notes", notes.getText().toString());
                    runApi("Добавляю оплату", () -> api.postJson("/api/payment-receipts/manual", body), value -> syncAfterMutation());
                } catch (Exception ex) {
                    toast(ex.getMessage());
                }
            })
            .setNegativeButton("Отмена", null)
            .show();
    }

    private void renderServices() {
        content.removeAllViews();
        String[] titles = {"Коммуналка", "Счётчики", "Тарифы", "Расходы"};
        String[] modes = {"utilities", "meters", "tariffs", "expenses"};
        android.widget.HorizontalScrollView scroll = new android.widget.HorizontalScrollView(this);
        scroll.setHorizontalScrollBarEnabled(false);
        LinearLayout tabs = row();
        for (int i = 0; i < modes.length; i++) {
            if (modes[i].equals(servicesMode)) screenTitle.setText(titles[i]);
            LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(-2, -2);
            params.setMargins(0, dp(4), dp(8), dp(12));
            tabs.addView(modeButton(titles[i], modes[i]), params);
        }
        scroll.addView(tabs);
        content.addView(scroll);
        if ("meters".equals(servicesMode)) renderMeters();
        else if ("tariffs".equals(servicesMode)) renderTariffs();
        else if ("expenses".equals(servicesMode)) renderExpenses();
        else renderUtilities();
        if (content.getChildCount() <= 3) addEmpty(content, "Пока нет записей", "Добавьте первую запись кнопкой выше.");
    }

    private void renderUtilities() {
        if (showSection("services_utility_calculate")) {
            Button calculate = primaryButton("Рассчитать коммуналку");
            calculate.setOnClickListener(v -> showUtilityCalcDialog());
            content.addView(calculate, new LinearLayout.LayoutParams(-1, dp(52)));
        }
        if (!showSection("services_utility_bills")) return;
        content.addView(section("Счета"));
        forEach(utilityBills, bill -> {
            int color = bill.optBoolean("provider_paid") ? green : isFutureDate(bill.optString("due_date")) ? gray : orange;
            LinearLayout card = cardWithAccent(color);
            card.addView(label(bill.optString("object") + " · " + bill.optString("service"), 18, text, true));
            card.addView(label(bill.optString("period_start") + " — " + bill.optString("period_end"), 13, muted, false));
            card.addView(label("Итого " + money(bill.optDouble("total_cost")), 15, text, true));
            LinearLayout row = row();
            row.addView(smallButton("Выставить", v -> issueBill(bill)), new LinearLayout.LayoutParams(0, -2, 1));
            row.addView(smallButton("Поставщик оплачен", v -> providerPaid(bill)), new LinearLayout.LayoutParams(0, -2, 1));
            row.addView(smallButton("Удалить", v -> deleteBill(bill)), new LinearLayout.LayoutParams(0, -2, 1));
            card.addView(row);
            content.addView(card);
        });
    }

    private void renderMeters() {
        if (showSection("services_meter_add")) {
            Button add = primaryButton("Добавить показание");
            add.setOnClickListener(v -> showReadingDialog());
            content.addView(add, new LinearLayout.LayoutParams(-1, dp(52)));
        }
        if (!showSection("services_meter_list")) return;
        content.addView(section("Счётчики"));
        forEach(arr(bootstrap, "meters"), meter -> {
            LinearLayout card = card();
            card.addView(label(meter.optString("object") + " · " + meter.optString("name"), 17, text, true));
            card.addView(label(meter.optString("scope") + " · последнее " + meter.optString("last_reading_date", "нет"), 13, muted, false));
            content.addView(card);
        });
    }

    private void renderTariffs() {
        if (showSection("services_tariff_add")) {
            Button add = primaryButton("Добавить тариф");
            add.setOnClickListener(v -> showTariffDialog());
            content.addView(add, new LinearLayout.LayoutParams(-1, dp(52)));
        }
        if (!showSection("services_tariff_list")) return;
        content.addView(section("Тарифы"));
        forEach(tariffs, tariff -> {
            LinearLayout card = card();
            card.addView(label(tariff.optString("service") + " · " + tariff.optString("name"), 17, text, true));
            card.addView(label("с " + tariff.optString("starts_on") + " · " + tariff.optString("tiers"), 13, muted, false));
            content.addView(card);
        });
    }

    private void renderExpenses() {
        if (showSection("services_expense_add")) {
            Button add = primaryButton("Добавить расход");
            add.setOnClickListener(v -> showExpenseDialog());
            content.addView(add, new LinearLayout.LayoutParams(-1, dp(52)));
        }
        if (!showSection("services_expense_list")) return;
        content.addView(section("Расходы"));
        forEach(expenses, expense -> {
            LinearLayout card = cardWithAccent("pending".equals(expense.optString("compensation_status")) ? orange : green);
            card.addView(label(expense.optString("category", "Расход") + " · " + money(expense.optDouble("amount")), 18, text, true));
            card.addView(label(joinNonEmpty(expense.optString("object"), expense.optString("apartment"), expense.optString("expense_date")), 13, muted, false));
            card.addView(label(expense.optString("description"), 13, muted, false));
            if ("pending".equals(expense.optString("compensation_status"))) {
                card.addView(smallButton("Компенсировано", v -> compensateExpense(expense)));
            }
            content.addView(card);
        });
    }

    private void renderMore() {
        screenTitle.setText("Ещё");
        content.removeAllViews();
        if (!isGuest()) {
            content.addView(section("Работа с объектами"));
            addDestination("wallet", "Коммуналка", "Расчёт, счета и оплата поставщику", () -> openService("utilities"));
            addDestination("trend", "Счётчики", "Внести показания", () -> openService("meters"));
            addDestination("document", "Расходы", "Затраты и компенсации", () -> openService("expenses"));
            addDestination("settings", "Тарифы", "Ставки и периоды действия", () -> openService("tariffs"));
            addDestination("buildings", "Договоры и квартиры", "Заселение, изменения и выезд", () -> navigate("tenants"));
            content.addView(section("Связь и контроль"));
            addDestination("tasks", "Чеки на проверку", suspiciousReceipts.length() + " ожидают решения", () -> navigate("receipts"));
            addDestination("bell", "Сообщения жильцам", "Получатели и предпросмотр отправки", () -> showMessageTargets());
            addDestination("shield", "Hermes", "Дела, предложения и обязательства", () -> startActivity(new Intent(this, HermesActivity.class)));
        }
        content.addView(section("Приложение"));
        addDestination("document", "Отчёты", "Скачать Excel за нужный период", () -> navigate("reports"));
        if (!isGuest()) addDestination("bell", "Уведомления", "События, тихие часы и частота", () -> startActivity(new Intent(this, NotificationSettingsActivity.class)));
        addDestination("settings", "Настройки", "Подключение, обновления и вход", () -> navigate("settings"));
    }

    private void addDestination(String icon, String title, String subtitle, Runnable action) {
        LinearLayout item = MobileUi.row(this);
        item.setPadding(dp(16), dp(14), dp(14), dp(14));
        item.setMinimumHeight(dp(76));
        item.setBackground(MobileUi.ripple(this, surface, 18, hairline));
        TextView symbol = label("", 1, blue, false);
        symbol.setCompoundDrawables(MobileUi.icon(this, icon, blue, 24), null, null, null);
        item.addView(symbol, new LinearLayout.LayoutParams(dp(38), dp(28)));
        LinearLayout words = MobileUi.column(this);
        words.addView(label(title, 16, text, true));
        if (!subtitle.isEmpty()) words.addView(label(subtitle, 12, muted, false));
        item.addView(words, new LinearLayout.LayoutParams(0, -2, 1));
        TextView arrow = label("›", 25, muted, false);
        arrow.setPadding(dp(10), 0, 0, 0);
        item.addView(arrow);
        item.setOnClickListener(v -> action.run());
        item.setFocusable(true);
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(-1, -2);
        params.setMargins(0, 0, 0, dp(8));
        content.addView(item, params);
    }

    private void addAction(LinearLayout parent, String title, Runnable action, boolean primary) {
        Button button = primary ? primaryButton(title) : secondaryButton(title);
        button.setOnClickListener(v -> action.run());
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(0, -2, 1);
        params.setMargins(0, dp(4), dp(6), dp(4));
        parent.addView(button, params);
    }

    private void openService(String mode) { servicesMode = mode; navigate("services"); }

    private void renderSettings() {
        screenTitle.setText("Настройки");
        content.removeAllViews();
        content.addView(section("Подключение"));
        addDestination("settings", "Адрес сервера", api.baseUrl(), () -> showHostDialog());
        if (!isGuest()) {
            addDestination("settings", "Сервер и Telegram", "Публичный адрес и настройки бота", () -> showServerSettingsDialog());
            addDestination("download", "Экспорт базы", "Скачать JSON", () -> download("/api/admin/database-export", "rental-manager-db.json"));
        }
        content.addView(section("Версия " + AppUpdates.installedName(this)));
        addDestination("download", "Обновления приложения", AppUpdates.status(this), () -> AppUpdates.showDialog(this));
        addDestination("shield", "Выйти", "Завершить PIN-сессию на этом устройстве", () -> confirm("Выйти из приложения?", () -> logout()));
    }

    private void renderReports() {
        screenTitle.setText("Отчёты");
        content.removeAllViews();
        LinearLayout period = card();
        period.addView(label("Период отчёта", 18, text, true));
        EditText start = dateInput(firstDay());
        EditText end = dateInput(today());
        period.addView(field("Начало", start));
        period.addView(field("Конец", end));
        content.addView(period);
        String[] names = {"Аренда", "Коммуналка", "Расходы", "Долги", "История", "Для хозяина"};
        String[] paths = {"rent", "utilities", "expenses", "debts", "history", "owner"};
        for (int i = 0; i < names.length; i++) {
            final String path = paths[i], name = names[i];
            addDestination("download", name, "Долги".equals(name) || "История".equals(name) ? "Все сохранённые данные · Excel" : "За выбранный период · Excel", () -> {
                if (start.getText().toString().compareTo(end.getText().toString()) > 0) { start.setError("Начало позже конца"); return; }
                String query = "debts".equals(path) || "history".equals(path) ? "" : "?start=" + start.getText() + "&end=" + end.getText();
                download("/api/reports/" + path + ".xlsx" + query, name + ".xlsx");
            });
        }
    }

    private EditText dateInput(String initial) {
        EditText field = input("ГГГГ-ММ-ДД", false);
        field.setText(initial);
        field.setFocusable(false);
        field.setOnClickListener(v -> {
            Calendar date = Calendar.getInstance();
            try {
                String[] parts = field.getText().toString().split("-");
                date.set(Integer.parseInt(parts[0]), Integer.parseInt(parts[1]) - 1, Integer.parseInt(parts[2]));
            } catch (Exception ignored) { }
            new android.app.DatePickerDialog(this, (picker, year, month, day) -> field.setText(String.format(Locale.US, "%04d-%02d-%02d", year, month + 1, day)),
                date.get(Calendar.YEAR), date.get(Calendar.MONTH), date.get(Calendar.DAY_OF_MONTH)).show();
        });
        return field;
    }

    private void renderReceipts() {
        screenTitle.setText("Проверка чеков");
        content.removeAllViews();
        content.addView(label("Сверьте платёж перед зачислением.", 14, muted, false));
        if (suspiciousReceipts.length() == 0) { addEmpty(content, "Все чеки разобраны", "Новые чеки, которым нужна проверка, появятся здесь."); return; }
        forEach(suspiciousReceipts, receipt -> {
            LinearLayout card = premiumCard();
            card.addView(premiumChip("Нужна проверка", orange));
            card.addView(label(money(receipt.optDouble("amount")), 26, text, true));
            card.addView(label(joinNonEmpty(receipt.optString("tenant"), receipt.optString("object"), receipt.optString("apartment")), 16, text, true));
            card.addView(label(joinNonEmpty(receipt.optString("recipient_name"), receipt.optString("paid_at"), receipt.optString("suspicious_reason")), 13, muted, false));
            LinearLayout actions = row();
            addAction(actions, "Принять", () -> confirm("Зачесть этот платёж?", () -> moderateReceipt(receipt, "accept")), true);
            addAction(actions, "Скрыть", () -> confirm("Убрать чек из проверки без зачисления?", () -> ignoreReceipt(receipt)), false);
            card.addView(actions);
            content.addView(card);
        });
    }

    private void addEmpty(LinearLayout parent, String title, String detail) {
        LinearLayout card = premiumCard();
        card.addView(label(title, 19, text, true));
        card.addView(label(detail, 14, muted, false));
        parent.addView(card);
    }

    private void addSearch(String hint, Runnable changed) {
        EditText search = input(hint, false);
        search.setInputType(InputType.TYPE_CLASS_TEXT);
        search.setCompoundDrawables(MobileUi.icon(this, "search", muted, 20), null, null, null);
        search.setCompoundDrawablePadding(dp(10));
        search.setText(searchQuery);
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(-1, -2);
        params.setMargins(0, dp(6), 0, dp(14));
        content.addView(search, params);
        search.addTextChangedListener(new android.text.TextWatcher() {
            public void beforeTextChanged(CharSequence value, int start, int count, int after) {}
            public void onTextChanged(CharSequence value, int start, int before, int count) { searchQuery = value.toString(); changed.run(); }
            public void afterTextChanged(android.text.Editable value) {}
        });
    }

    private boolean matchesSearch(String... values) {
        return searchQuery.trim().isEmpty() || joinNonEmpty(values).toLowerCase(Locale.forLanguageTag("ru")).contains(searchQuery.trim().toLowerCase(Locale.forLanguageTag("ru")));
    }

    private void showMessageTargets() {
        LinearLayout list = dialogForm();
        forEach(messageTargets, target -> {
            LinearLayout card = card();
            card.addView(label(target.optString("tenant"), 17, text, true));
            card.addView(label(joinNonEmpty(target.optString("object"), target.optString("apartment"), target.optString("telegram")), 13, muted, false));
            card.addView(smallButton("Все долги", v -> previewMessage(target, "message_all_debts")));
            list.addView(card);
        });
        new AlertDialog.Builder(this).setTitle("Получатели").setView(wrapDialog(list)).setPositiveButton("Закрыть", null).show();
    }

    private boolean showSection(String key) {
        return true;
    }

    private void showPageSectionsDialog() {
        LinearLayout form = dialogForm();
        List<CheckBox> boxes = new ArrayList<>();
        addSectionGroup(form, boxes, "Дом", new String[][]{
            {"dashboard_hero", "Верхняя карточка"},
            {"dashboard_progress", "Прогресс месяца"},
            {"dashboard_metrics", "Счётчики состояния"},
            {"dashboard_reports", "Месячные отчёты"},
            {"dashboard_attention", "Требует внимания"}
        });
        addSectionGroup(form, boxes, "Жильцы", new String[][]{
            {"tenants_hero", "Верхняя карточка"},
            {"tenants_onboard", "Кнопка заселения"},
            {"tenants_active_leases", "Активные договоры"},
            {"tenants_apartments", "Квартиры"}
        });
        addSectionGroup(form, boxes, "Оплаты", new String[][]{
            {"payments_hero", "Верхняя карточка"},
            {"payments_actions", "Быстрые действия"},
            {"payments_rent", "Арендные начисления"},
            {"payments_suspicious", "Чеки на проверку"}
        });
        addSectionGroup(form, boxes, "Учёт", new String[][]{
            {"services_hero", "Верхняя карточка"},
            {"services_modes", "Переключатель подразделов"},
            {"services_utility_calculate", "Кнопка расчёта коммуналки"},
            {"services_utility_bills", "Счета коммуналки"},
            {"services_meter_add", "Кнопка показания"},
            {"services_meter_list", "Список счётчиков"},
            {"services_tariff_add", "Кнопка тарифа"},
            {"services_tariff_list", "Список тарифов"},
            {"services_expense_add", "Кнопка расхода"},
            {"services_expense_list", "Список расходов"}
        });
        addSectionGroup(form, boxes, "Ещё", new String[][]{
            {"more_hero", "Верхняя карточка"},
            {"more_notifications", "Пуш-уведомления"},
            {"more_reports", "Excel-отчёты"},
            {"more_messages", "Сообщения"},
            {"more_server_settings", "Настройки сервера"}
        });
        new AlertDialog.Builder(this)
            .setTitle("Что показывать")
            .setView(wrapDialog(form))
            .setPositiveButton("Сохранить", (dialog, which) -> {
                SharedPreferences.Editor editor = NotificationPrefs.prefs(this).edit();
                for (CheckBox box : boxes) {
                    editor.putBoolean("ui_" + box.getTag().toString(), box.isChecked());
                }
                editor.apply();
                renderCurrentTab();
            })
            .setNegativeButton("Отмена", null)
            .show();
    }

    private void addSectionGroup(LinearLayout form, List<CheckBox> boxes, String title, String[][] items) {
        form.addView(section(title));
        for (String[] item : items) {
            CheckBox box = checkbox(item[1]);
            box.setTag(item[0]);
            box.setChecked(showSection(item[0]));
            boxes.add(box);
            form.addView(box);
        }
    }

    private void showServerSettingsDialog() {
        JSONObject settings = obj(bootstrap, "settings");
        LinearLayout form = dialogForm();
        EditText appUrl = input("https://...", false);
        EditText ownerChat = input("telegram id", false);
        EditText cutoff = input("2026-05-01", false);
        CheckBox enabled = checkbox("Автонапоминания жильцам включены");
        appUrl.setText(settings.optString("app_base_url"));
        ownerChat.setText(settings.optString("telegram_owner_chat_id"));
        cutoff.setText(settings.optString("notification_cutoff_date"));
        enabled.setChecked(settings.optBoolean("notifications_enabled"));
        form.addView(label("Это публичный URL для ссылок в Telegram. Хост приложения меняется в пункте «Сменить хост приложения».", 12, muted, false));
        form.addView(field("Публичный URL", appUrl));
        form.addView(field("ID чата владельца", ownerChat));
        form.addView(field("Игнорировать долги до", cutoff));
        form.addView(enabled);
        new AlertDialog.Builder(this)
            .setTitle("Настройки сервера")
            .setView(wrapDialog(form))
            .setPositiveButton("Сохранить", (dialog, which) -> {
                try {
                    JSONObject body = new JSONObject();
                    body.put("app_base_url", appUrl.getText().toString());
                    body.put("telegram_owner_chat_id", ownerChat.getText().toString());
                    body.put("notification_cutoff_date", cutoff.getText().toString());
                    body.put("notifications_enabled", enabled.isChecked());
                    runApi("Сохраняю настройки", () -> api.postJson("/api/settings", body), value -> syncAfterMutation());
                } catch (Exception ex) {
                    toast(ex.getMessage());
                }
            })
            .setNegativeButton("Отмена", null)
            .show();
    }

    private void previewMessage(JSONObject target, String key) {
        runApi("Готовлю сообщение", () -> {
            JSONObject body = new JSONObject();
            body.put("lease_id", target.optInt("lease_id"));
            body.put("template_key", key);
            return api.postJson("/api/messages/preview", body);
        }, value -> {
            JSONObject preview = (JSONObject) value;
            new AlertDialog.Builder(this)
                .setTitle("Сообщение")
                .setMessage(preview.optString("text", preview.toString()))
                .setPositiveButton("Отправить", (dialog, which) -> runApi("Отправляю", () -> api.postJson("/api/messages/send", obj(preview, "payload")), done -> toast("Отправлено")))
                .setNegativeButton("Отмена", null)
                .show();
        });
    }

    private void showUtilityCalcDialog() {
        LinearLayout form = dialogForm();
        Spinner service = spinner(serviceLabels(), serviceIds());
        EditText start = input("2026-05-01", false);
        EditText end = input("2026-05-31", false);
        CheckBox estimate = checkbox("Разрешить прогноз");
        start.setText(firstDay());
        end.setText(today());
        form.addView(field("Услуга", service));
        form.addView(field("Начало", start));
        form.addView(field("Конец", end));
        form.addView(estimate);
        new AlertDialog.Builder(this)
            .setTitle("Расчёт коммуналки")
            .setView(wrapDialog(form))
            .setPositiveButton("Рассчитать", (dialog, which) -> {
                try {
                    JSONObject body = new JSONObject();
                    body.put("service_id", Integer.parseInt(spinnerValue(service)));
                    body.put("period_start", start.getText().toString());
                    body.put("period_end", end.getText().toString());
                    body.put("allow_estimate", estimate.isChecked());
                    runApi("Считаю", () -> api.postJson("/api/utility-bills/calculate", body), value -> syncAfterMutation());
                } catch (Exception ex) {
                    toast(ex.getMessage());
                }
            })
            .setNegativeButton("Отмена", null)
            .show();
    }

    private void showReadingDialog() {
        LinearLayout form = dialogForm();
        Spinner meter = spinner(meterLabels(), meterIds());
        EditText date = input("2026-05-21", false);
        EditText value = input("0", false);
        EditText note = input("комментарий", false);
        date.setText(today());
        form.addView(field("Счётчик", meter));
        form.addView(field("Дата", date));
        form.addView(field("Показание", value));
        form.addView(field("Комментарий", note));
        new AlertDialog.Builder(this)
            .setTitle("Показание")
            .setView(wrapDialog(form))
            .setPositiveButton("Сохранить", (dialog, which) -> {
                try {
                    JSONObject body = new JSONObject();
                    body.put("meter_id", Integer.parseInt(spinnerValue(meter)));
                    body.put("reading_date", date.getText().toString());
                    body.put("value", number(value));
                    body.put("note", note.getText().toString());
                    runApi("Сохраняю", () -> api.postJson("/api/meter-readings", body), done -> syncAfterMutation());
                } catch (Exception ex) {
                    toast(ex.getMessage());
                }
            })
            .setNegativeButton("Отмена", null)
            .show();
    }

    private void showTariffDialog() {
        LinearLayout form = dialogForm();
        Spinner service = spinner(serviceLabels(), serviceIds());
        EditText starts = input("2026-05-21", false);
        EditText name = input("Тариф", false);
        EditText tiers = input("1000:4.2; *:7", false);
        starts.setText(today());
        form.addView(field("Услуга", service));
        form.addView(field("Действует с", starts));
        form.addView(field("Название", name));
        form.addView(field("Ступени", tiers));
        new AlertDialog.Builder(this)
            .setTitle("Новый тариф")
            .setView(wrapDialog(form))
            .setPositiveButton("Добавить", (dialog, which) -> {
                try {
                    JSONObject body = new JSONObject();
                    body.put("service_id", Integer.parseInt(spinnerValue(service)));
                    body.put("starts_on", starts.getText().toString());
                    body.put("name", name.getText().toString());
                    body.put("tiers", tiers.getText().toString());
                    runApi("Добавляю", () -> api.postJson("/api/tariffs", body), done -> syncAfterMutation());
                } catch (Exception ex) {
                    toast(ex.getMessage());
                }
            })
            .setNegativeButton("Отмена", null)
            .show();
    }

    private void showExpenseDialog() {
        LinearLayout form = dialogForm();
        Spinner apartment = spinner(apartmentLabelsWithEmpty(), apartmentIdsWithEmpty());
        EditText date = input("2026-05-21", false);
        EditText category = input("ремонт", false);
        EditText amount = input("0", false);
        Spinner source = spinner(new String[]{"Личные", "Арендный бюджет", "Другое"}, new String[]{"personal", "rental_budget", "other"});
        EditText method = input("карта", false);
        EditText description = input("описание", false);
        date.setText(today());
        form.addView(field("Квартира", apartment));
        form.addView(field("Дата", date));
        form.addView(field("Категория", category));
        form.addView(field("Сумма", amount));
        form.addView(field("Источник", source));
        form.addView(field("Способ оплаты", method));
        form.addView(field("Описание", description));
        new AlertDialog.Builder(this)
            .setTitle("Расход")
            .setView(wrapDialog(form))
            .setPositiveButton("Добавить", (dialog, which) -> {
                try {
                    JSONObject body = new JSONObject();
                    String aptId = spinnerValue(apartment);
                    if (!aptId.isEmpty()) body.put("apartment_id", Integer.parseInt(aptId));
                    body.put("expense_date", date.getText().toString());
                    body.put("category", category.getText().toString());
                    body.put("amount", number(amount));
                    body.put("source_funds", spinnerValue(source));
                    body.put("payment_method", method.getText().toString());
                    body.put("description", description.getText().toString());
                    runApi("Добавляю расход", () -> api.postJson("/api/expenses", body), done -> syncAfterMutation());
                } catch (Exception ex) {
                    toast(ex.getMessage());
                }
            })
            .setNegativeButton("Отмена", null)
            .show();
    }

    private void payRent(JSONObject charge, String channel) {
        double suggested = "ip".equals(channel) ? Math.max(0, charge.optDouble("ip_due") - charge.optDouble("ip_paid")) : Math.max(0, charge.optDouble("personal_due") - charge.optDouble("personal_paid"));
        promptNumber("Оплата · " + ("ip".equals(channel) ? "ИП" : "Перевод"), suggested, value -> {
            JSONObject body = new JSONObject();
            body.put("amount", value);
            body.put("channel", channel);
            body.put("source", "manual");
            return body;
        }, body -> runApi("Отмечаю оплату", () -> api.postJson("/api/rent-charges/" + charge.optInt("id") + "/payments", (JSONObject) body), done -> syncAfterMutation()));
    }

    private void deferRent(JSONObject charge) {
        promptNumber("Отсрочка, дней", 3, value -> {
            JSONObject body = new JSONObject();
            body.put("deferral_days", (int) value);
            body.put("deferral_note", "из Android");
            return body;
        }, body -> runApi("Сохраняю отсрочку", () -> api.postJson("/api/rent-charges/" + charge.optInt("id") + "/defer", (JSONObject) body), done -> syncAfterMutation()));
    }

    private void issueBill(JSONObject bill) {
        confirm("Выставить счёт жильцам?", () -> runApi("Выставляю", () -> api.postJson("/api/utility-bills/" + bill.optInt("id") + "/issue", new JSONObject()), done -> syncAfterMutation()));
    }

    private void providerPaid(JSONObject bill) {
        runApi("Отмечаю оплату поставщику", () -> api.postJson("/api/utility-bills/" + bill.optInt("id") + "/provider-paid", new JSONObject()), done -> syncAfterMutation());
    }

    private void deleteBill(JSONObject bill) {
        confirm("Удалить черновик коммуналки?", () -> runApi("Удаляю", () -> api.deleteJson("/api/utility-bills/" + bill.optInt("id")), done -> syncAfterMutation()));
    }

    private void compensateExpense(JSONObject expense) {
        runApi("Компенсирую", () -> api.postJson("/api/expenses/" + expense.optInt("id") + "/compensate", new JSONObject()), done -> syncAfterMutation());
    }

    private void toggleLeaseIgnore(JSONObject lease) {
        JSONObject body = new JSONObject();
        try {
            body.put("ignored", !lease.optBoolean("ignored"));
        } catch (Exception ignored) {
        }
        runApi("Сохраняю", () -> api.patchJson("/api/leases/" + lease.optInt("id") + "/ignore", body), done -> syncAfterMutation());
    }

    private void moveOut(JSONObject lease) {
        promptText("Дата выезда", today(), value -> {
            try {
                JSONObject body = new JSONObject();
                body.put("end_date", value);
                runApi("Оформляю выезд", () -> api.postJson("/api/leases/" + lease.optInt("id") + "/move-out", body), done -> syncAfterMutation());
            } catch (Exception ex) {
                toast(ex.getMessage());
            }
        });
    }

    private void toggleApartment(JSONObject apartment) {
        JSONObject body = new JSONObject();
        try {
            body.put("active", !apartment.optBoolean("active", true));
        } catch (Exception ignored) {
        }
        runApi("Сохраняю квартиру", () -> api.patchJson("/api/apartments/" + apartment.optInt("id"), body), done -> syncAfterMutation());
    }

    private void moderateReceipt(JSONObject receipt, String action) {
        JSONObject body = new JSONObject();
        try {
            body.put("action", action);
        } catch (Exception ignored) {
        }
        runApi("Проверяю чек", () -> api.postJson("/api/payment-receipts/" + receipt.optInt("id") + "/moderate", body), done -> syncAfterMutation());
    }

    private void ignoreReceipt(JSONObject receipt) {
        runApi("Скрываю чек", () -> api.postJson("/api/payment-receipts/" + receipt.optInt("id") + "/ignore", new JSONObject()), done -> syncAfterMutation());
    }

    private void logout() {
        runApi("Выхожу", () -> {
            api.logout();
            return new JSONObject();
        }, done -> showLogin());
    }

    private void showHostDialog() {
        EditText input = input("https://rental.example.ru", false);
        input.setText(api.baseUrl());
        new AlertDialog.Builder(this)
            .setTitle("Адрес сервера")
            .setMessage("Это адрес API для приложения. Например: https://menedzer-arendy-g33ka.waw0.amvera.tech. Путь /api дописывать не нужно.")
            .setView(input)
            .setPositiveButton("Сохранить", (dialog, which) -> {
                String oldUrl = api.baseUrl();
                String newUrl = NotificationPrefs.normalizeBaseUrl(input.getText().toString());
                NotificationPrefs.setBaseUrl(this, newUrl);
                if (!oldUrl.equals(newUrl)) {
                    new SessionStore(this).clear();
                }
                invalidateAllCaches();
                screenSubtitle.setText(api.baseUrl());
                checkServerConnection(true);
                checkAuthAndLoad();
            })
            .setNegativeButton("Отмена", null)
            .show();
    }

    private void download(String path, String fallbackName) {
        try {
            String url = api.baseUrl() + path;
            DownloadManager.Request request = new DownloadManager.Request(Uri.parse(url));
            request.setTitle(fallbackName);
            request.setDescription("Rental Manager");
            request.addRequestHeader("Cookie", api.cookieHeader());
            request.setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED);
            request.setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, fallbackName);
            DownloadManager manager = (DownloadManager) getSystemService(Context.DOWNLOAD_SERVICE);
            if (manager != null) manager.enqueue(request);
            toast("Скачиваю " + fallbackName);
        } catch (Exception ex) {
            toast(ex.getMessage());
        }
    }

    private void runApi(String loading, Job job, Done done) {
        runApi(loading, true, job, done);
    }

    private void runApi(String loading, boolean notify, Job job, Done done) {
        if (notify) toast(loading + "...");
        new Thread(() -> {
            try {
                Object result = job.run();
                runOnUiThread(() -> {
                    setConnectionStatus(true, "данные загружены");
                    done.run(result);
                });
            } catch (ApiClient.ApiException ex) {
                runOnUiThread(() -> {
                    hideLoadingCard();
                    if (ex.statusCode == 401 || ex.statusCode == 403) {
                        setConnectionStatus(false, "нужен PIN");
                        showLogin();
                    } else if (ex.statusCode <= 0) {
                        setConnectionStatus(false, "не API");
                    } else {
                        setConnectionStatus(false, "ошибка " + ex.statusCode);
                    }
                    toast(ex.getMessage());
                });
            } catch (Exception ex) {
                runOnUiThread(() -> {
                    hideLoadingCard();
                    setConnectionStatus(false, "связи нет");
                    toast(ex.getMessage() == null ? "Ошибка" : ex.getMessage());
                });
            }
        }, "rental-api").start();
    }

    private void addHero(String title, String subtitle) {
        LinearLayout hero = card();
        hero.addView(label(title, 25, text, true));
        hero.addView(label(subtitle, 14, muted, false));
        content.addView(hero);
    }

    private void addMetricRow(LinearLayout parent, String a, int av, String b, int bv) {
        LinearLayout row = row();
        row.addView(metric(a, av), new LinearLayout.LayoutParams(0, dp(78), 1));
        row.addView(space(10), new LinearLayout.LayoutParams(dp(10), 1));
        row.addView(metric(b, bv), new LinearLayout.LayoutParams(0, dp(78), 1));
        parent.addView(row);
    }

    private LinearLayout metric(String title, int value) {
        LinearLayout card = card();
        card.setPadding(dp(12), dp(8), dp(12), dp(8));
        card.addView(label(String.valueOf(value), 23, text, true));
        card.addView(label(title, 12, muted, false));
        return card;
    }

    private Button modeButton(String title, String mode) {
        Button button = mode.equals(servicesMode) ? primaryButton(title) : secondaryButton(title);
        button.setOnClickListener(v -> {
            servicesMode = mode;
            renderServices();
        });
        return button;
    }

    private Button primaryButton(String title) {
        return styledButton(title, blue, Color.WHITE);
    }

    private Button primaryButton(String title, View.OnClickListener listener) {
        Button button = primaryButton(title);
        button.setOnClickListener(listener);
        return button;
    }

    private Button secondaryButton(String title) {
        return styledButton(title, surface2, text);
    }

    private Button secondaryButton(String title, View.OnClickListener listener) {
        Button button = secondaryButton(title);
        button.setOnClickListener(listener);
        return button;
    }

    private Button smallButton(String title, View.OnClickListener listener) {
        Button button = semanticSmallButton(title);
        button.setTextSize(13);
        button.setOnClickListener(listener);
        return button;
    }

    private Button smallButton(String title) {
        Button button = semanticSmallButton(title);
        button.setTextSize(13);
        return button;
    }

    private Button semanticSmallButton(String title) {
        String lower = title == null ? "" : title.toLowerCase(Locale.forLanguageTag("ru-RU"));
        if (lower.contains("выезд") || lower.contains("удал") || lower.contains("скры") || lower.contains("отключ")) {
            return accentButton(title, red);
        }
        if (lower.contains("прин") || lower.contains("компенс") || lower.contains("оплачен") || lower.contains("включ")) {
            return accentButton(title, green);
        }
        if (lower.contains("отср") || lower.contains("выстав") || lower.contains("напом")) {
            return accentButton(title, orange);
        }
        if ("ип".equals(lower) || lower.contains("перевод") || lower.contains("оплата")) {
            return accentButton(title, blue);
        }
        return secondaryButton(title);
    }

    private Button accentButton(String title, int accent) {
        Button button = styledButton(title, surface2, accent);
        button.setBackground(round(surface2, 255, dp(14), accent));
        return button;
    }

    private Button pillButton(String title, boolean active) {
        return styledButton(title, active ? blue : surface2, text);
    }

    private Button navButton(String title, boolean active) {
        Button button = styledButton(title, active ? Color.rgb(31, 38, 51) : Color.TRANSPARENT, active ? text : muted);
        button.setTextSize(11);
        button.setGravity(Gravity.CENTER);
        button.setLines(2);
        return button;
    }

    private Button styledButton(String title, int bgColor, int textColor) {
        Button button = MobileUi.button(this, title, bgColor == blue);
        button.setTextColor(bgColor == blue ? bg : textColor);
        button.setBackground(MobileUi.ripple(this, bgColor, 14, bgColor == blue || bgColor == Color.TRANSPARENT ? Color.TRANSPARENT : hairline));
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(-1, -2);
        params.setMargins(0, dp(4), 0, dp(4));
        button.setLayoutParams(params);
        return button;
    }

    private int activeAlpha(int color) {
        return color == Color.TRANSPARENT ? 0 : 255;
    }

    private LinearLayout card() {
        return MobileUi.card(this);
    }

    private LinearLayout cardWithAccent(int accent) {
        LinearLayout card = card();
        card.setBackground(round(surface, 255, dp(18), accent));
        return card;
    }

    private TextView section(String title) {
        return MobileUi.section(this, title);
    }

    private LinearLayout row() {
        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setGravity(Gravity.CENTER);
        row.setPadding(0, 0, 0, dp(8));
        return row;
    }

    private TextView label(String value, int sp, int color, boolean bold) {
        return MobileUi.text(this, value, sp, color, bold);
    }

    private EditText input(String hint, boolean password) {
        EditText input = MobileUi.field(this, hint);
        if (password) input.setInputType(InputType.TYPE_CLASS_NUMBER | InputType.TYPE_NUMBER_VARIATION_PASSWORD);
        return input;
    }

    private CheckBox checkbox(String label) {
        CheckBox box = new CheckBox(this);
        box.setText(label);
        box.setTextColor(text);
        box.setTextSize(14);
        return box;
    }

    private LinearLayout field(String label, View field) {
        LinearLayout wrap = new LinearLayout(this);
        wrap.setOrientation(LinearLayout.VERTICAL);
        wrap.setPadding(0, dp(6), 0, dp(8));
        wrap.addView(label(label, 12, muted, false));
        wrap.addView(field, new LinearLayout.LayoutParams(-1, -2));
        return wrap;
    }

    private LinearLayout dialogForm() {
        LinearLayout form = new LinearLayout(this);
        form.setOrientation(LinearLayout.VERTICAL);
        form.setPadding(dp(8), dp(4), dp(8), dp(4));
        return form;
    }

    private ScrollView wrapDialog(LinearLayout form) {
        ScrollView scroll = new ScrollView(this);
        scroll.addView(form);
        return scroll;
    }

    private Spinner spinner(List<String> labels, List<String> values) {
        Spinner spinner = new Spinner(this);
        ArrayAdapter<String> adapter = new ArrayAdapter<String>(this, android.R.layout.simple_spinner_item, labels);
        adapter.setDropDownViewResource(android.R.layout.simple_spinner_dropdown_item);
        spinner.setAdapter(adapter);
        spinner.setTag(values);
        return spinner;
    }

    private Spinner spinner(String[] labels, String[] values) {
        List<String> labelList = new ArrayList<>();
        List<String> valueList = new ArrayList<>();
        for (String label : labels) labelList.add(label);
        for (String value : values) valueList.add(value);
        return spinner(labelList, valueList);
    }

    private String spinnerValue(Spinner spinner) {
        List<String> values = (List<String>) spinner.getTag();
        int position = spinner.getSelectedItemPosition();
        if (position < 0 || position >= values.size()) return "";
        return values.get(position);
    }

    private void selectSpinnerValue(Spinner spinner, String value) {
        List<String> values = (List<String>) spinner.getTag();
        int index = values.indexOf(value);
        if (index >= 0) spinner.setSelection(index);
    }

    private GradientDrawable round(int color, int alpha, int radius, int strokeColor) {
        GradientDrawable drawable = new GradientDrawable();
        drawable.setColor(color);
        drawable.setAlpha(alpha);
        drawable.setCornerRadius(radius);
        if (strokeColor != Color.TRANSPARENT) drawable.setStroke(1, strokeColor);
        return drawable;
    }

    private View space(int dpValue) {
        View view = new View(this);
        view.setMinimumWidth(dp(dpValue));
        view.setMinimumHeight(dp(dpValue));
        return view;
    }

    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }

    private void promptText(String title, String value, TextDone done) {
        EditText input = input(title, false);
        input.setText(value);
        new AlertDialog.Builder(this)
            .setTitle(title)
            .setView(input)
            .setPositiveButton("OK", (dialog, which) -> done.run(input.getText().toString()))
            .setNegativeButton("Отмена", null)
            .show();
    }

    private interface TextDone {
        void run(String value);
    }

    private interface JsonFactory {
        JSONObject make(double value) throws Exception;
    }

    private void promptNumber(String title, double suggested, JsonFactory factory, Done done) {
        EditText input = input(title, false);
        input.setInputType(InputType.TYPE_CLASS_NUMBER | InputType.TYPE_NUMBER_FLAG_DECIMAL);
        input.setText(String.valueOf(suggested));
        new AlertDialog.Builder(this)
            .setTitle(title)
            .setView(input)
            .setPositiveButton("OK", (dialog, which) -> {
                try {
                    done.run(factory.make(Double.parseDouble(input.getText().toString().replace(",", "."))));
                } catch (Exception ex) {
                    toast(ex.getMessage());
                }
            })
            .setNegativeButton("Отмена", null)
            .show();
    }

    private void confirm(String message, Runnable ok) {
        new AlertDialog.Builder(this)
            .setTitle("Подтверждение")
            .setMessage(message)
            .setPositiveButton("Да", (dialog, which) -> ok.run())
            .setNegativeButton("Отмена", null)
            .show();
    }

    private List<String> apartmentLabels() {
        List<String> labels = new ArrayList<>();
        forEach(arr(bootstrap, "objects"), object -> forEach(arr(object, "apartments"), apartment -> labels.add(object.optString("name") + " · " + apartment.optString("name"))));
        return labels;
    }

    private List<String> apartmentIds() {
        List<String> ids = new ArrayList<>();
        forEach(arr(bootstrap, "objects"), object -> forEach(arr(object, "apartments"), apartment -> ids.add(String.valueOf(apartment.optInt("id")))));
        return ids;
    }

    private List<String> apartmentLabelsWithEmpty() {
        List<String> labels = new ArrayList<>();
        labels.add("Не выбрано");
        labels.addAll(apartmentLabels());
        return labels;
    }

    private List<String> apartmentIdsWithEmpty() {
        List<String> ids = new ArrayList<>();
        ids.add("");
        ids.addAll(apartmentIds());
        return ids;
    }

    private List<String> leaseLabels() {
        List<String> labels = new ArrayList<>();
        forEach(arr(bootstrap, "leases"), lease -> labels.add(lease.optString("object") + " · " + lease.optString("apartment") + " · " + lease.optString("tenant")));
        return labels;
    }

    private List<String> leaseIds() {
        List<String> ids = new ArrayList<>();
        forEach(arr(bootstrap, "leases"), lease -> ids.add(String.valueOf(lease.optInt("id"))));
        return ids;
    }

    private List<String> serviceLabels() {
        List<String> labels = new ArrayList<>();
        forEach(arr(bootstrap, "services"), service -> labels.add(service.optString("object") + " · " + service.optString("name")));
        return labels;
    }

    private List<String> serviceIds() {
        List<String> ids = new ArrayList<>();
        forEach(arr(bootstrap, "services"), service -> ids.add(String.valueOf(service.optInt("id"))));
        return ids;
    }

    private List<String> meterLabels() {
        List<String> labels = new ArrayList<>();
        forEach(arr(bootstrap, "meters"), meter -> labels.add(meter.optString("object") + " · " + meter.optString("name")));
        return labels;
    }

    private List<String> meterIds() {
        List<String> ids = new ArrayList<>();
        forEach(arr(bootstrap, "meters"), meter -> ids.add(String.valueOf(meter.optInt("id"))));
        return ids;
    }

    private JSONObject obj(JSONObject source, String key) {
        JSONObject value = source == null ? null : source.optJSONObject(key);
        return value == null ? new JSONObject() : value;
    }

    private JSONArray arr(JSONObject source, String key) {
        JSONArray value = source == null ? null : source.optJSONArray(key);
        return value == null ? new JSONArray() : value;
    }

    private int len(JSONObject source, String key) {
        return arr(source, key).length();
    }

    private void forEach(JSONArray array, JsonItem item) {
        for (int i = 0; i < array.length(); i++) {
            JSONObject object = array.optJSONObject(i);
            if (object != null) item.run(object);
        }
    }

    private interface JsonItem {
        void run(JSONObject item);
    }

    private String joinNonEmpty(String... values) {
        StringBuilder builder = new StringBuilder();
        for (String value : values) {
            if (value == null || value.trim().isEmpty() || "null".equals(value)) continue;
            if (builder.length() > 0) builder.append(" · ");
            builder.append(value.trim());
        }
        return builder.toString();
    }

    private String money(double value) {
        return String.format(Locale.forLanguageTag("ru-RU"), "%,.0f ₽", value);
    }

    private String amountNoCurrency(double value) {
        return String.format(Locale.forLanguageTag("ru-RU"), "%,.0f", value);
    }

    private String moneyPair(double paid, double due) {
        return amountNoCurrency(paid) + " / " + amountNoCurrency(due) + " ₽";
    }

    private double number(EditText input) {
        String value = input.getText().toString().trim().replace(",", ".");
        if (value.isEmpty()) return 0;
        return Double.parseDouble(value);
    }

    private Object emptyToNull(EditText input) {
        String value = input.getText().toString().trim();
        if (value.isEmpty()) return JSONObject.NULL;
        return value;
    }

    private String firstNonEmpty(String... values) {
        for (String value : values) {
            if (value != null && !value.trim().isEmpty() && !"null".equals(value)) return value.trim();
        }
        return "";
    }

    private String join(String delimiter, List<String> values) {
        StringBuilder builder = new StringBuilder();
        for (String value : values) {
            if (value == null || value.trim().isEmpty()) continue;
            if (builder.length() > 0) builder.append(delimiter);
            builder.append(value.trim());
        }
        return builder.toString();
    }

    private String compactApartment(JSONObject item) {
        String object = shortObjectName(item.optString("object"));
        String apartment = item.optString("apartment", "").trim();
        if (!apartment.isEmpty()) return apartment.replace(" ", "");
        return object;
    }

    private String shortObjectName(String value) {
        String raw = value == null ? "" : value.trim();
        String lower = raw.toLowerCase(Locale.forLanguageTag("ru-RU")).replace("ё", "е");
        if (lower.contains("бел") || lower.contains("бд")) return "БД";
        if (lower.contains("чер") || lower.contains("чд")) return "ЧД";
        if (lower.contains("бан")) return "Баня";
        return raw;
    }

    private String paymentDate(JSONArray payments, String channel) {
        String result = "";
        for (int i = 0; i < payments.length(); i++) {
            JSONObject payment = payments.optJSONObject(i);
            if (payment == null || !channel.equals(payment.optString("channel"))) continue;
            result = compactDate(payment.optString("paid_at"));
        }
        return result;
    }

    private String fallbackDate(String value, String fallback) {
        return value == null || value.trim().isEmpty() ? fallback : value;
    }

    private String compactDate(String value) {
        String iso = isoDate(value);
        if (iso.length() < 10) return "";
        return iso.substring(8, 10) + "." + iso.substring(5, 7);
    }

    private boolean sameSelectedMonth(String value) {
        String key = monthKey(value);
        return !key.isEmpty() && key.equals(selectedMonthKey());
    }

    private String selectedMonthKey() {
        return String.format(Locale.US, "%04d-%02d", selectedMonth.get(Calendar.YEAR), selectedMonth.get(Calendar.MONTH) + 1);
    }

    private String monthKey(String value) {
        String iso = isoDate(value);
        return iso.length() >= 7 ? iso.substring(0, 7) : "";
    }

    private String isoDate(String value) {
        if (value == null) return "";
        String trimmed = value.trim();
        if (trimmed.length() >= 10 && trimmed.charAt(4) == '-' && trimmed.charAt(7) == '-') {
            return trimmed.substring(0, 10);
        }
        if (trimmed.length() >= 10 && trimmed.charAt(2) == '.' && trimmed.charAt(5) == '.') {
            return trimmed.substring(6, 10) + "-" + trimmed.substring(3, 5) + "-" + trimmed.substring(0, 2);
        }
        return "";
    }

    private boolean isFutureDate(String value) {
        String iso = isoDate(value);
        return !iso.isEmpty() && iso.compareTo(today()) > 0;
    }

    private boolean isSelectedMonthFuture() {
        return selectedMonthKey().compareTo(monthKey(today())) > 0;
    }

    private String selectedMonthStart() {
        Calendar calendar = (Calendar) selectedMonth.clone();
        calendar.set(Calendar.DAY_OF_MONTH, 1);
        return formatIsoDate(calendar);
    }

    private String selectedMonthEnd() {
        Calendar calendar = (Calendar) selectedMonth.clone();
        calendar.set(Calendar.DAY_OF_MONTH, calendar.getActualMaximum(Calendar.DAY_OF_MONTH));
        return formatIsoDate(calendar);
    }

    private String formatIsoDate(Calendar calendar) {
        java.text.SimpleDateFormat format = new java.text.SimpleDateFormat("yyyy-MM-dd", Locale.US);
        return format.format(calendar.getTime());
    }

    private String periodMonth(JSONObject item) {
        String date = firstNonEmpty(
            item.optString("period_start"),
            item.optString("bill_period_end"),
            item.optString("period_end"),
            item.optString("due_date")
        );
        String month = formatMonth(date);
        if (!month.isEmpty()) return month;
        return item.optString("period_label", "");
    }

    private String formatMonth(String value) {
        String key = monthKey(value);
        if (key.length() != 7) return "";
        try {
            int year = Integer.parseInt(key.substring(0, 4));
            int month = Integer.parseInt(key.substring(5, 7));
            if (month < 1 || month > 12) return "";
            return MONTH_NAMES_RU[month - 1] + " " + year;
        } catch (Exception ignored) {
            return "";
        }
    }

    private String monthTitle(Calendar month) {
        String value = MONTH_NAMES_RU[month.get(Calendar.MONTH)] + " " + month.get(Calendar.YEAR);
        return value.substring(0, 1).toUpperCase(Locale.forLanguageTag("ru-RU")) + value.substring(1);
    }

    private boolean isDoneStatus(String status) {
        return "paid".equals(status)
            || "paid_ahead".equals(status)
            || "accepted".equals(status)
            || "compensated".equals(status)
            || "not_required".equals(status)
            || "ok".equals(status)
            || "closed".equals(status);
    }

    private boolean isCriticalStatus(String status) {
        return "overdue".equals(status)
            || "partial".equals(status)
            || "suspicious".equals(status)
            || "rejected".equals(status);
    }

    private int severityRank(int color) {
        if (color == red) return 4;
        if (color == orange) return 3;
        if (color == blue) return 2;
        if (color == gray) return 1;
        return 0;
    }

    private String statusLabel(String status) {
        if ("pending".equals(status)) return "ожидается";
        if ("upcoming".equals(status)) return "предстоит";
        if ("overdue".equals(status)) return "просрочено";
        if ("partial".equals(status)) return "частично оплачено";
        if ("paid".equals(status)) return "оплачено";
        if ("paid_ahead".equals(status)) return "переплата";
        if ("deferred".equals(status)) return "отсрочка";
        if ("draft".equals(status)) return "черновик";
        if ("issued".equals(status)) return "выставлено";
        if ("compensated".equals(status)) return "компенсировано";
        if ("not_required".equals(status)) return "не требуется";
        if ("suspicious".equals(status)) return "требует проверки";
        if ("accepted".equals(status)) return "принято";
        if ("moderated".equals(status)) return "проверено";
        if ("rejected".equals(status)) return "отклонено";
        if ("ignored".equals(status)) return "скрыто";
        if ("open".equals(status)) return "открыто";
        if ("critical".equals(status)) return "критично";
        if ("warning".equals(status)) return "важно";
        if ("ok".equals(status)) return "в порядке";
        return status == null || status.isEmpty() ? "неизвестно" : status;
    }

    private String severityLabel(String severity) {
        return statusLabel(severity);
    }

    private int statusColor(String status) {
        if ("overdue".equals(status) || "suspicious".equals(status)) return red;
        if ("partial".equals(status) || "deferred".equals(status) || "issued".equals(status)) return orange;
        if ("paid".equals(status) || "paid_ahead".equals(status) || "accepted".equals(status)) return green;
        return muted;
    }

    private String today() {
        java.text.SimpleDateFormat format = new java.text.SimpleDateFormat("yyyy-MM-dd", Locale.US);
        return format.format(new java.util.Date());
    }

    private String firstDay() {
        java.text.SimpleDateFormat format = new java.text.SimpleDateFormat("yyyy-MM-01", Locale.US);
        return format.format(new java.util.Date());
    }

    private void toast(String message) {
        Toast.makeText(this, message == null ? "" : message, Toast.LENGTH_SHORT).show();
    }

    private void requestNotificationPermission() {
        if (Build.VERSION.SDK_INT >= 33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS}, REQUEST_NOTIFICATIONS);
        }
    }

    private static final class MonthScore {
        int done;
        int warning;
        int critical;
        int upcoming;

        int total() {
            return done + warning + critical + upcoming;
        }

        int percent() {
            int total = total();
            if (total <= 0) return 100;
            return Math.round(done * 100f / total);
        }
    }

    private static final class IssueGroup {
        String title = "";
        String tenant = "";
        String targetTab = "dashboard";
        int color = 0;
        final Set<String> seen = new LinkedHashSet<>();
        final List<String> lines = new ArrayList<>();
    }

    private static final class MonthTask {
        final int group;
        final String title;
        final String detail;
        final int color;
        final Runnable action;

        MonthTask(int group, String title, String detail, int color) { this(group, title, detail, color, null); }

        MonthTask(int group, String title, String detail, int color, Runnable action) {
            this.action = action;
            this.group = group;
            this.title = title;
            this.detail = detail;
            this.color = color;
        }
    }

    private class LoadingRingView extends View {
        private final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);
        private final RectF rect = new RectF();
        private float rotation = -90f;
        private boolean running = false;
        private final Runnable tick = new Runnable() {
            @Override
            public void run() {
                if (!running) return;
                rotation = (rotation + 8f) % 360f;
                invalidate();
                postDelayed(this, 16);
            }
        };

        LoadingRingView(Context context) {
            super(context);
        }

        void start() {
            if (running) return;
            running = true;
            removeCallbacks(tick);
            tick.run();
        }

        void stop() {
            running = false;
            removeCallbacks(tick);
        }

        @Override
        protected void onDraw(Canvas canvas) {
            super.onDraw(canvas);
            int size = Math.min(getWidth(), getHeight()) - dp(20);
            float left = (getWidth() - size) / 2f;
            float top = (getHeight() - size) / 2f;
            rect.set(left, top, left + size, top + size);

            paint.setStyle(Paint.Style.STROKE);
            paint.setStrokeWidth(dp(15));
            paint.setStrokeCap(Paint.Cap.BUTT);

            int[] colors = {
                Color.argb(235, 126, 232, 238),
                Color.argb(120, 237, 243, 244),
                Color.argb(55, 126, 232, 238),
                Color.argb(90, 237, 243, 244),
                Color.argb(42, 126, 232, 238),
                Color.argb(170, 126, 232, 238),
            };
            for (int i = 0; i < colors.length; i++) {
                paint.setColor(colors[i]);
                canvas.drawArc(rect, rotation + i * 58f, 34f, false, paint);
            }
        }
    }

    private class MonthProgressView extends View {
        private final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);
        private final RectF rect = new RectF();
        private MonthScore score = new MonthScore();

        MonthProgressView(Context context) {
            super(context);
        }

        void setScore(MonthScore score) {
            this.score = score == null ? new MonthScore() : score;
            invalidate();
        }

        @Override
        protected void onDraw(Canvas canvas) {
            super.onDraw(canvas);
            int width = getWidth();
            int height = getHeight();
            int size = Math.min(width, height) - dp(20);
            float left = (width - size) / 2f;
            float top = (height - size) / 2f;
            rect.set(left, top, left + size, top + size);

            paint.setStyle(Paint.Style.STROKE);
            paint.setStrokeWidth(dp(14));
            paint.setStrokeCap(Paint.Cap.BUTT);
            paint.setColor(hairline);
            canvas.drawArc(rect, -90, 360, false, paint);

            int total = score.total();
            if (total > 0) {
                float start = -90f;
                start = drawSegment(canvas, start, score.done, total, green);
                start = drawSegment(canvas, start, score.warning, total, orange);
                start = drawSegment(canvas, start, score.critical, total, red);
                drawSegment(canvas, start, score.upcoming, total, gray);
            } else {
                paint.setColor(green);
                canvas.drawArc(rect, -90, 360, false, paint);
            }

            paint.setStyle(Paint.Style.FILL);
            paint.setStrokeWidth(1);
            paint.setTextAlign(Paint.Align.CENTER);
            paint.setTypeface(Typeface.DEFAULT_BOLD);
            paint.setTextSize(dp(25));
            paint.setColor(text);
            Paint.FontMetrics metrics = paint.getFontMetrics();
            float cx = width / 2f;
            float cy = height / 2f - (metrics.ascent + metrics.descent) / 2f;
            canvas.drawText(score.percent() + "%", cx, cy, paint);
        }

        private float drawSegment(Canvas canvas, float start, int value, int total, int color) {
            if (value <= 0 || total <= 0) return start;
            float sweep = 360f * value / total;
            paint.setColor(color);
            canvas.drawArc(rect, start, sweep, false, paint);
            return start + sweep;
        }
    }
}
