package com.gathin.tvhome;

import android.content.SharedPreferences;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.drawable.BitmapDrawable;
import android.graphics.drawable.Drawable;
import android.graphics.drawable.TransitionDrawable;
import android.os.Handler;
import android.os.Looper;
import android.service.dreams.DreamService;
import android.util.DisplayMetrics;
import android.view.View;
import android.widget.ImageView;
import android.widget.TextView;

import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.util.Random;

/**
 * Screensaver: the home screen's time-of-day picture (same Background setting, same hours), zooming very
 * slowly, with the clock, date and the weather from Open-Meteo in the lower left. Every minute the text
 * shifts a little and the picture follows the clock, so nothing stays still for long.
 */
public class ClockDream extends DreamService {

    private static final long MINUTE_MS = 60_000L;
    private static final long ZOOM_MS = 5 * 60_000L;
    /** How often the weather is fetched, and how long a fetched one is still shown when offline. */
    private static final long WEATHER_EVERY_MS = 30 * 60_000L, WEATHER_KEEP_MS = 3 * 60 * 60_000L;
    private static final String WEATHER_PREFS = "weather", WEATHER_TEXT = "text", WEATHER_TIME = "time";

    private final Handler handler = new Handler(Looper.getMainLooper());
    private final Random random = new Random();
    private ImageView picture;
    private int pictureRes;
    private View content;
    private TextView weather;
    private boolean zoomIn = true;
    private volatile boolean dreaming;

    private final Runnable minute = new Runnable() {
        @Override
        public void run() {
            showPicture();
            nudgeText();
            handler.postDelayed(this, MINUTE_MS);
        }
    };

    private final Runnable weatherTick = new Runnable() {
        @Override
        public void run() {
            fetchWeather();
            handler.postDelayed(this, WEATHER_EVERY_MS);
        }
    };

    @Override
    public void onAttachedToWindow() {
        super.onAttachedToWindow();
        setInteractive(false);   // any key wakes the TV back to where it was
        setFullscreen(true);
        setContentView(R.layout.dream_clock);
        picture = findViewById(R.id.dreamPicture);
        content = findViewById(R.id.dreamContent);
        weather = findViewById(R.id.dreamWeather);
        showPicture();
        showCachedWeather();
    }

    @Override
    public void onDreamingStarted() {
        super.onDreamingStarted();
        dreaming = true;
        content.setAlpha(0f);
        content.animate().alpha(1f).setDuration(1200).start();
        picture.post(this::zoom);
        handler.postDelayed(minute, MINUTE_MS);
        long age = System.currentTimeMillis() - weatherPrefs().getLong(WEATHER_TIME, 0);
        handler.postDelayed(weatherTick, age >= WEATHER_EVERY_MS ? 0 : WEATHER_EVERY_MS - age);
    }

    @Override
    public void onDreamingStopped() {
        stop();
        super.onDreamingStopped();
    }

    @Override
    public void onDetachedFromWindow() {
        stop();
        super.onDetachedFromWindow();
    }

    private void stop() {
        dreaming = false;
        handler.removeCallbacksAndMessages(null);
        if (picture != null) picture.animate().cancel();
    }

    // ---- picture ---------------------------------------------------------------------------------

    /** The picture the home screen would show now, cross-fading when the hour moves it on. */
    private void showPicture() {
        int res = MainActivity.skyPicture(this);
        if (res == pictureRes) return;
        Bitmap bmp;
        try {
            DisplayMetrics dm = getResources().getDisplayMetrics();
            BitmapFactory.Options opts = new BitmapFactory.Options();
            opts.inJustDecodeBounds = true;
            BitmapFactory.decodeResource(getResources(), res, opts);
            opts.inSampleSize = MainActivity.sampleSize(opts, dm);
            opts.inJustDecodeBounds = false;
            opts.inPreferredConfig = Bitmap.Config.RGB_565;
            bmp = BitmapFactory.decodeResource(getResources(), res, opts);
        } catch (Exception | OutOfMemoryError e) {
            return;   // black stays
        }
        if (bmp == null) return;
        Drawable next = new BitmapDrawable(getResources(), bmp);
        Drawable prev = picture.getDrawable();
        if (prev == null) {
            picture.setImageDrawable(next);
        } else {
            TransitionDrawable fade = new TransitionDrawable(new Drawable[]{prev, next});
            fade.setCrossFadeEnabled(true);
            picture.setImageDrawable(fade);
            fade.startTransition(3000);
            handler.postDelayed(() -> { if (picture.getDrawable() == fade) picture.setImageDrawable(next); }, 3200);
        }
        pictureRes = res;
    }

    /** Zooms in to 108% over five minutes towards a random spot, then back out, and so on. */
    private void zoom() {
        if (!dreaming) return;
        if (zoomIn) {
            picture.setPivotX(picture.getWidth() * (0.25f + 0.5f * random.nextFloat()));
            picture.setPivotY(picture.getHeight() * (0.25f + 0.5f * random.nextFloat()));
        }
        float to = zoomIn ? 1.08f : 1f;
        zoomIn = !zoomIn;
        picture.animate().scaleX(to).scaleY(to).setDuration(ZOOM_MS)
                .setInterpolator(new android.view.animation.LinearInterpolator())
                .withEndAction(this::zoom).start();
    }

    /** Fades the text out and back a little way from where it was, within a small box in the lower left. */
    private void nudgeText() {
        float d = getResources().getDisplayMetrics().density;
        float x = (random.nextFloat() * 2 - 1) * 28 * d;
        float y = (random.nextFloat() * 2 - 1) * 18 * d;
        content.animate().alpha(0f).setDuration(900).withEndAction(() -> {
            content.setTranslationX(x);
            content.setTranslationY(y);
            content.animate().alpha(1f).setDuration(1200).start();
        }).start();
    }

    // ---- weather ---------------------------------------------------------------------------------

    private SharedPreferences weatherPrefs() {
        return getSharedPreferences(WEATHER_PREFS, MODE_PRIVATE);
    }

    private void showCachedWeather() {
        SharedPreferences p = weatherPrefs();
        String text = p.getString(WEATHER_TEXT, "");
        boolean fresh = System.currentTimeMillis() - p.getLong(WEATHER_TIME, 0) < WEATHER_KEEP_MS;
        weather.setText(text);
        weather.setVisibility(fresh && !text.isEmpty() ? View.VISIBLE : View.GONE);
    }

    /** Fetches the current weather on a background thread; on any failure the last one stays (for up to 3 hours). */
    private void fetchWeather() {
        String lat = getString(R.string.weather_lat).trim(), lon = getString(R.string.weather_lon).trim();
        if (lat.isEmpty() || lon.isEmpty()) return;
        String url = "https://api.open-meteo.com/v1/forecast?latitude=" + lat + "&longitude=" + lon
                + "&current=temperature_2m,weather_code,is_day&daily=temperature_2m_max,temperature_2m_min"
                + "&timezone=auto&forecast_days=1";
        new Thread(() -> {
            String text = null;
            try {
                JSONObject json = new JSONObject(get(url));
                JSONObject now = json.getJSONObject("current");
                JSONObject day = json.getJSONObject("daily");
                int code = now.getInt("weather_code");
                boolean isDay = now.optInt("is_day", 1) == 1;
                StringBuilder sb = new StringBuilder();
                sb.append(icon(code, isDay)).append("   ")
                        .append(Math.round(now.getDouble("temperature_2m"))).append("°   ")
                        .append(getString(label(code)));
                sb.append("   ·   ").append(getString(R.string.weather_high_low,
                        Math.round(day.getJSONArray("temperature_2m_max").getDouble(0)),
                        Math.round(day.getJSONArray("temperature_2m_min").getDouble(0))));
                String place = getString(R.string.weather_place).trim();
                if (!place.isEmpty()) sb.append("   ·   ").append(place);
                text = sb.toString();
            } catch (Exception ignored) {
                // offline or the service is down: keep what is on screen
            }
            if (text == null) {
                handler.post(() -> { if (dreaming) showCachedWeather(); });
                return;
            }
            weatherPrefs().edit().putString(WEATHER_TEXT, text).putLong(WEATHER_TIME, System.currentTimeMillis()).apply();
            handler.post(() -> { if (dreaming) showCachedWeather(); });
        }, "weather").start();
    }

    private static String get(String url) throws Exception {
        HttpURLConnection c = (HttpURLConnection) new URL(url).openConnection();
        c.setConnectTimeout(10_000);
        c.setReadTimeout(10_000);
        try (InputStream in = c.getInputStream()) {
            ByteArrayOutputStream out = new ByteArrayOutputStream();
            byte[] buf = new byte[4096];
            for (int n; (n = in.read(buf)) > 0; ) out.write(buf, 0, n);
            return out.toString("UTF-8");
        } finally {
            c.disconnect();
        }
    }

    /** WMO weather codes, as Open-Meteo sends them. */
    private static int label(int code) {
        if (code == 0) return R.string.weather_clear;
        if (code == 1) return R.string.weather_mostly_clear;
        if (code == 2) return R.string.weather_partly_cloudy;
        if (code == 3) return R.string.weather_cloudy;
        if (code == 45 || code == 48) return R.string.weather_fog;
        if (code >= 51 && code <= 57) return R.string.weather_drizzle;
        if (code >= 61 && code <= 67) return R.string.weather_rain;
        if (code >= 71 && code <= 77 || code == 85 || code == 86) return R.string.weather_snow;
        if (code >= 80 && code <= 82) return R.string.weather_showers;
        if (code >= 95) return R.string.weather_storm;
        return R.string.weather_cloudy;
    }

    private static String icon(int code, boolean isDay) {
        if (code <= 1) return isDay ? "☀️" : "🌙";        // sun / crescent moon
        if (code == 2) return isDay ? "⛅" : "☁️";               // sun behind cloud / cloud
        if (code == 3) return "☁️";
        if (code == 45 || code == 48) return "🌫️";            // fog
        if (code >= 71 && code <= 77 || code == 85 || code == 86) return "❄️";
        if (code >= 95) return "⛈️";                                 // thunder
        return "🌧️";                                           // rain
    }
}
