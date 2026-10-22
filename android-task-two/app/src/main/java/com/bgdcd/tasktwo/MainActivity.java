package com.bgdcd.tasktwo;

import android.app.Activity;
import android.os.Bundle;
import android.os.Handler;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.util.DisplayMetrics;
import android.view.Window;
import android.view.WindowManager;
import android.webkit.WebChromeClient;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.ImageView;
import android.widget.FrameLayout;
import android.widget.TextView;
import android.view.Gravity;
import java.io.InputStream;

public class MainActivity extends Activity {
    private WebView webView;
    private final Handler handler = new Handler();

    @Override
    public void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        requestWindowFeature(Window.FEATURE_NO_TITLE);
        getWindow().setFlags(WindowManager.LayoutParams.FLAG_FULLSCREEN,
                WindowManager.LayoutParams.FLAG_FULLSCREEN);
        showCover();
    }

    // Decode the cover at screen size instead of loading a potentially huge bitmap into RAM.
    private Bitmap decodeCoverSafely() {
        BitmapFactory.Options bounds = new BitmapFactory.Options();
        bounds.inJustDecodeBounds = true;
        try (InputStream input = getAssets().open("chizongai.png")) {
            BitmapFactory.decodeStream(input, null, bounds);
        } catch (Exception e) {
            android.util.Log.e("TaskTwo", "Could not read opening cover", e);
            return null;
        }

        DisplayMetrics metrics = getResources().getDisplayMetrics();
        int targetWidth = Math.max(metrics.widthPixels, metrics.heightPixels);
        int targetHeight = Math.min(metrics.widthPixels, metrics.heightPixels);
        int sample = 1;
        while (bounds.outWidth / (sample * 2) >= targetWidth
                && bounds.outHeight / (sample * 2) >= targetHeight) {
            sample *= 2;
        }

        BitmapFactory.Options options = new BitmapFactory.Options();
        options.inSampleSize = sample;
        options.inPreferredConfig = Bitmap.Config.RGB_565;
        try (InputStream input = getAssets().open("chizongai.png")) {
            return BitmapFactory.decodeStream(input, null, options);
        } catch (Exception e) {
            android.util.Log.e("TaskTwo", "Could not decode opening cover", e);
            return null;
        }
    }

    private void showCover() {
        Bitmap bitmap = decodeCoverSafely();
        if (bitmap == null) {
            // A missing/unsupported cover must never crash the whole app.
            showTask();
            return;
        }

        ImageView cover = new ImageView(this);
        cover.setScaleType(ImageView.ScaleType.FIT_CENTER);
        cover.setBackgroundColor(android.graphics.Color.BLACK);
        cover.setImageBitmap(bitmap);

        FrameLayout root = new FrameLayout(this);
        root.setBackgroundColor(android.graphics.Color.BLACK);
        root.addView(cover, new FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.MATCH_PARENT));
        setContentView(root);
        handler.postDelayed(this::showTask, 4000);
    }

    private void showTask() {
        try {
            webView = new WebView(this);
            WebSettings settings = webView.getSettings();
            settings.setJavaScriptEnabled(true);
            settings.setDomStorageEnabled(true);
            settings.setAllowFileAccess(true);
            settings.setAllowContentAccess(true);
            settings.setLoadsImagesAutomatically(true);
            settings.setMixedContentMode(WebSettings.MIXED_CONTENT_ALWAYS_ALLOW);
            webView.setWebViewClient(new WebViewClient());
            webView.setWebChromeClient(new WebChromeClient());
            setContentView(webView);
            webView.loadUrl("file:///android_asset/task-two.html");
        } catch (RuntimeException e) {
            android.util.Log.e("TaskTwo", "WebView failed to start", e);
            TextView error = new TextView(this);
            error.setGravity(Gravity.CENTER);
            error.setPadding(32, 32, 32, 32);
            error.setTextSize(18);
            error.setText("页面组件启动失败。请检查平板的 Android System WebView 是否已启用并更新，然后重新打开应用。");
            setContentView(error);
        }
    }

    @Override
    public void onBackPressed() {
        if (webView != null && webView.canGoBack()) webView.goBack();
        else super.onBackPressed();
    }

    @Override
    protected void onDestroy() {
        handler.removeCallbacksAndMessages(null);
        if (webView != null) {
            webView.stopLoading();
            webView.loadUrl("about:blank");
            webView.destroy();
            webView = null;
        }
        super.onDestroy();
    }
}