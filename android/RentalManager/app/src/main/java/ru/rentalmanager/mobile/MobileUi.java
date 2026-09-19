package ru.rentalmanager.mobile;

import android.app.Activity;
import android.content.Context;
import android.content.res.ColorStateList;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.ColorFilter;
import android.graphics.Paint;
import android.graphics.Path;
import android.graphics.PixelFormat;
import android.graphics.Typeface;
import android.graphics.drawable.Drawable;
import android.graphics.drawable.GradientDrawable;
import android.graphics.drawable.RippleDrawable;
import android.graphics.drawable.StateListDrawable;
import android.os.Build;
import android.text.InputType;
import android.view.Gravity;
import android.view.View;
import android.view.Window;
import android.view.WindowInsets;
import android.view.WindowManager;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.TextView;

/** Shared native presentation; business values and actions stay in their API consumers. */
public final class MobileUi {
    public static final int BG = Color.rgb(9, 16, 24);
    public static final int SURFACE = Color.rgb(17, 28, 39);
    public static final int SURFACE_ALT = Color.rgb(25, 40, 53);
    public static final int BORDER = Color.rgb(43, 61, 76);
    public static final int TEXT = Color.rgb(238, 245, 249);
    public static final int MUTED = Color.rgb(156, 175, 190);
    public static final int ACCENT = Color.rgb(92, 224, 180);
    public static final int SUCCESS = Color.rgb(107, 224, 169);
    public static final int WARNING = Color.rgb(255, 202, 118);
    public static final int DANGER = Color.rgb(255, 143, 148);
    public static final int INFO = Color.rgb(130, 192, 255);

    private MobileUi() {}

    public static int dp(Context context, int value) {
        return Math.round(value * context.getResources().getDisplayMetrics().density);
    }

    public static TextView text(Context context, String value, int size, int color, boolean bold) {
        TextView view = new TextView(context);
        view.setText(value == null ? "" : value);
        view.setTextSize(size);
        view.setTextColor(color);
        view.setFontFeatureSettings("tnum");
        view.setTypeface(Typeface.create(bold ? "sans-serif-medium" : "sans-serif", Typeface.NORMAL));
        view.setIncludeFontPadding(false);
        view.setLineSpacing(dp(context, 3), 1f);
        view.setPadding(0, dp(context, 3), 0, dp(context, 3));
        return view;
    }

    public static LinearLayout column(Context context) {
        LinearLayout view = new LinearLayout(context);
        view.setOrientation(LinearLayout.VERTICAL);
        return view;
    }

    public static LinearLayout row(Context context) {
        LinearLayout view = new LinearLayout(context);
        view.setOrientation(LinearLayout.HORIZONTAL);
        view.setGravity(Gravity.CENTER_VERTICAL);
        return view;
    }

    public static LinearLayout card(Context context) {
        LinearLayout view = column(context);
        view.setPadding(dp(context, 18), dp(context, 17), dp(context, 18), dp(context, 17));
        view.setBackground(shape(context, SURFACE, 22, BORDER));
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(-1, -2);
        params.setMargins(0, 0, 0, dp(context, 12));
        view.setLayoutParams(params);
        return view;
    }

    public static void makeClickable(View view) {
        view.setClickable(true);
        view.setFocusable(true);
        view.setForeground(new RippleDrawable(ColorStateList.valueOf(alpha(ACCENT, 35)), null,
            shape(view.getContext(), Color.WHITE, 22, Color.TRANSPARENT)));
    }

    public static Button button(Context context, String title, boolean primary) {
        Button button = new Button(context);
        button.setText(title);
        button.setAllCaps(false);
        button.setTextSize(14);
        button.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
        button.setTextColor(new ColorStateList(new int[][]{new int[]{-android.R.attr.state_enabled}, new int[]{}},
            new int[]{MUTED, primary ? BG : TEXT}));
        button.setMinHeight(dp(context, 48));
        button.setMinimumHeight(dp(context, 48));
        button.setMinWidth(dp(context, 48));
        button.setMinimumWidth(dp(context, 48));
        button.setPadding(dp(context, 16), dp(context, 10), dp(context, 16), dp(context, 10));
        button.setGravity(Gravity.CENTER);
        button.setStateListAnimator(null);
        button.setBackground(ripple(context, primary ? ACCENT : SURFACE_ALT, 14,
            primary ? Color.TRANSPARENT : BORDER));
        return button;
    }

    public static EditText field(Context context, String hint) {
        EditText field = new EditText(context);
        field.setHint(hint);
        field.setHintTextColor(MUTED);
        field.setTextColor(TEXT);
        field.setTextSize(16);
        field.setSingleLine(true);
        field.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_FLAG_CAP_SENTENCES);
        field.setPadding(dp(context, 14), dp(context, 10), dp(context, 14), dp(context, 10));
        field.setMinHeight(dp(context, 52));
        field.setBackgroundTintList(null);
        StateListDrawable states = new StateListDrawable();
        states.addState(new int[]{android.R.attr.state_focused}, shape(context, SURFACE_ALT, 12, ACCENT));
        states.addState(new int[]{}, shape(context, SURFACE_ALT, 12, BORDER));
        field.setBackground(states);
        return field;
    }

    public static TextView chip(Context context, String title, int color) {
        TextView chip = text(context, title, 12, color, true);
        chip.setGravity(Gravity.CENTER);
        chip.setPadding(dp(context, 10), dp(context, 7), dp(context, 10), dp(context, 7));
        chip.setBackground(shape(context, alpha(color, 22), 9, Color.TRANSPARENT));
        return chip;
    }

    public static TextView section(Context context, String title) {
        TextView titleView = text(context, title, 18, TEXT, true);
        titleView.setPadding(dp(context, 2), dp(context, 16), 0, dp(context, 12));
        return titleView;
    }

    public static Button iconButton(Context context, String icon, String description) {
        Button view = button(context, "", false);
        Drawable drawable = icon(context, icon, TEXT, 22);
        view.setCompoundDrawables(null, null, null, null);
        view.setForeground(drawable);
        view.setForegroundGravity(Gravity.CENTER);
        view.setContentDescription(description);
        view.setPadding(dp(context, 13), dp(context, 13), dp(context, 13), dp(context, 13));
        view.setLayoutParams(new LinearLayout.LayoutParams(dp(context, 48), dp(context, 48)));
        return view;
    }

    public static LinearLayout navItem(Context context, String icon, String title, boolean selected) {
        LinearLayout item = column(context);
        item.setGravity(Gravity.CENTER);
        item.setPadding(dp(context, 2), dp(context, 4), dp(context, 2), dp(context, 4));
        item.setMinimumHeight(dp(context, 64));
        item.setBackground(ripple(context, selected ? alpha(ACCENT, 20) : Color.TRANSPARENT, 16,
            Color.TRANSPARENT));
        TextView symbol = text(context, "", 1, TEXT, false);
        Drawable drawable = icon(context, icon, selected ? ACCENT : MUTED, 23);
        symbol.setCompoundDrawables(null, drawable, null, null);
        symbol.setPadding(0, 0, 0, 0);
        item.addView(symbol, new LinearLayout.LayoutParams(dp(context, 25), dp(context, 25)));
        TextView label = text(context, title, 11, selected ? ACCENT : MUTED, selected);
        label.setGravity(Gravity.CENTER);
        item.addView(label, new LinearLayout.LayoutParams(-1, -2));
        item.setContentDescription(title);
        item.setSelected(selected);
        item.setFocusable(true);
        item.setClickable(true);
        return item;
    }

    public static GradientDrawable shape(Context context, int color, int radiusDp, int strokeColor) {
        GradientDrawable shape = new GradientDrawable();
        shape.setColor(color);
        shape.setCornerRadius(dp(context, radiusDp));
        if (strokeColor != Color.TRANSPARENT) shape.setStroke(dp(context, 1), strokeColor);
        return shape;
    }

    public static RippleDrawable ripple(Context context, int color, int radiusDp, int strokeColor) {
        return new RippleDrawable(ColorStateList.valueOf(alpha(color == ACCENT ? BG : ACCENT, 40)),
            shape(context, color, radiusDp, strokeColor), shape(context, Color.WHITE, radiusDp, Color.TRANSPARENT));
    }

    public static int alpha(int color, int alpha) {
        return (color & 0x00ffffff) | (Math.max(0, Math.min(255, alpha)) << 24);
    }

    public static void applyWindow(Activity activity, View root) {
        Window window = activity.getWindow();
        window.setStatusBarColor(BG);
        window.setNavigationBarColor(BG);
        window.setSoftInputMode(WindowManager.LayoutParams.SOFT_INPUT_ADJUST_RESIZE);
        if (Build.VERSION.SDK_INT >= 29) {
            window.setStatusBarContrastEnforced(false);
            window.setNavigationBarContrastEnforced(false);
        }
        if (Build.VERSION.SDK_INT >= 30) window.setDecorFitsSystemWindows(false);
        else window.getDecorView().setSystemUiVisibility(View.SYSTEM_UI_FLAG_LAYOUT_STABLE
            | View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN | View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION);
        final int left = root.getPaddingLeft();
        final int top = root.getPaddingTop();
        final int right = root.getPaddingRight();
        final int bottom = root.getPaddingBottom();
        root.setOnApplyWindowInsetsListener((view, insets) -> {
            if (Build.VERSION.SDK_INT >= 30) {
                android.graphics.Insets safe = insets.getInsets(WindowInsets.Type.systemBars()
                    | WindowInsets.Type.displayCutout() | WindowInsets.Type.ime());
                view.setPadding(left + safe.left, top + safe.top, right + safe.right, bottom + safe.bottom);
                return WindowInsets.CONSUMED;
            }
            view.setPadding(left + insets.getSystemWindowInsetLeft(), top + insets.getSystemWindowInsetTop(),
                right + insets.getSystemWindowInsetRight(), bottom + insets.getSystemWindowInsetBottom());
            return insets.consumeSystemWindowInsets();
        });
        root.requestApplyInsets();
    }

    public static Drawable icon(Context context, String name, int color, int sizeDp) {
        Drawable icon = new LineIcon(name, color, dp(context, sizeDp));
        icon.setBounds(0, 0, dp(context, sizeDp), dp(context, sizeDp));
        return icon;
    }

    private static final class LineIcon extends Drawable {
        private final String name;
        private final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);
        private final int size;

        LineIcon(String name, int color, int size) {
            this.name = name;
            this.size = size;
            paint.setColor(color);
            paint.setStyle(Paint.Style.STROKE);
            paint.setStrokeWidth(1.8f);
            paint.setStrokeCap(Paint.Cap.ROUND);
            paint.setStrokeJoin(Paint.Join.ROUND);
        }

        @Override public void draw(Canvas canvas) {
            canvas.save();
            float side = Math.min(getBounds().width(), getBounds().height());
            canvas.translate(getBounds().left + (getBounds().width() - side) / 2f,
                getBounds().top + (getBounds().height() - side) / 2f);
            canvas.scale(side / 24f, side / 24f);
            switch (name) {
                case "home":
                    line(canvas, 3, 10, 12, 3, 21, 10);
                    line(canvas, 5, 9, 5, 21, 10, 21, 10, 14, 14, 14, 14, 21, 19, 21, 19, 9);
                    break;
                case "tasks":
                    canvas.drawRoundRect(5, 4, 20, 21, 3, 3, paint);
                    line(canvas, 9, 2, 9, 6, 16, 6, 16, 2);
                    line(canvas, 9, 13, 12, 16, 17, 10);
                    break;
                case "payments": case "wallet":
                    canvas.drawRoundRect(3, 6, 21, 20, 3, 3, paint);
                    line(canvas, 4, 6, 17, 3, 18, 6);
                    canvas.drawRoundRect(15, 11, 22, 16, 1, 1, paint);
                    canvas.drawPoint(18, 13.5f, paint);
                    break;
                case "buildings": case "properties":
                    canvas.drawRoundRect(4, 3, 14, 21, 1, 1, paint);
                    line(canvas, 14, 9, 20, 9, 20, 21, 3, 21);
                    line(canvas, 8, 7, 10, 7); line(canvas, 8, 11, 10, 11);
                    line(canvas, 8, 15, 10, 15); line(canvas, 8, 21, 8, 18, 10, 18, 10, 21);
                    break;
                case "more":
                    canvas.drawCircle(5, 12, 1, paint); canvas.drawCircle(12, 12, 1, paint);
                    canvas.drawCircle(19, 12, 1, paint);
                    break;
                case "refresh":
                    canvas.drawArc(4, 4, 20, 20, 45, 285, false, paint);
                    line(canvas, 20, 4, 20, 9, 15, 9);
                    break;
                case "back":
                    line(canvas, 11, 5, 4, 12, 11, 19); line(canvas, 4, 12, 21, 12);
                    break;
                case "chevron":
                    line(canvas, 9, 5, 16, 12, 9, 19);
                    break;
                case "bell":
                    line(canvas, 5, 16, 7, 13, 7, 9);
                    canvas.drawArc(7, 4, 17, 14, 180, 180, false, paint);
                    line(canvas, 17, 9, 17, 13, 19, 16, 5, 16);
                    canvas.drawArc(9, 16, 15, 22, 20, 140, false, paint);
                    break;
                case "download":
                    line(canvas, 12, 3, 12, 15); line(canvas, 7, 10, 12, 15, 17, 10);
                    line(canvas, 4, 16, 4, 21, 20, 21, 20, 16);
                    break;
                case "settings":
                    line(canvas, 4, 7, 20, 7); line(canvas, 4, 17, 20, 17);
                    canvas.drawCircle(9, 7, 3, paint); canvas.drawCircle(16, 17, 3, paint);
                    break;
                case "plus":
                    line(canvas, 12, 5, 12, 19); line(canvas, 5, 12, 19, 12);
                    break;
                case "search":
                    canvas.drawCircle(10, 10, 6, paint); line(canvas, 15, 15, 21, 21);
                    break;
                case "shield":
                    line(canvas, 12, 2, 21, 6, 19, 16, 12, 22, 5, 16, 3, 6, 12, 2);
                    line(canvas, 8, 12, 11, 15, 16, 9);
                    break;
                case "calendar":
                    canvas.drawRoundRect(3, 5, 21, 21, 2, 2, paint);
                    line(canvas, 3, 10, 21, 10); line(canvas, 8, 2, 8, 7); line(canvas, 16, 2, 16, 7);
                    canvas.drawPoint(8, 15, paint); canvas.drawPoint(12, 15, paint); canvas.drawPoint(16, 15, paint);
                    break;
                case "trend":
                    line(canvas, 3, 17, 9, 11, 13, 15, 21, 6); line(canvas, 15, 6, 21, 6, 21, 12);
                    break;
                case "document":
                    line(canvas, 14, 3, 5, 3, 5, 21, 19, 21, 19, 8, 14, 3, 14, 8, 19, 8);
                    line(canvas, 9, 12, 15, 12); line(canvas, 9, 16, 15, 16);
                    break;
                case "close":
                    line(canvas, 6, 6, 18, 18); line(canvas, 18, 6, 6, 18);
                    break;
                case "check":
                    line(canvas, 4, 12, 9, 17, 20, 6);
                    break;
                default:
                    canvas.drawCircle(12, 12, 9, paint);
                    line(canvas, 12, 11, 12, 17); canvas.drawPoint(12, 7, paint);
            }
            canvas.restore();
        }

        private void line(Canvas canvas, float... points) {
            Path path = new Path();
            path.moveTo(points[0], points[1]);
            for (int i = 2; i < points.length; i += 2) path.lineTo(points[i], points[i + 1]);
            canvas.drawPath(path, paint);
        }

        @Override public void setAlpha(int alpha) { paint.setAlpha(alpha); invalidateSelf(); }
        @Override public void setColorFilter(ColorFilter filter) { paint.setColorFilter(filter); invalidateSelf(); }
        @Override public int getOpacity() { return PixelFormat.TRANSLUCENT; }
        @Override public int getIntrinsicWidth() { return size; }
        @Override public int getIntrinsicHeight() { return size; }
    }
}
