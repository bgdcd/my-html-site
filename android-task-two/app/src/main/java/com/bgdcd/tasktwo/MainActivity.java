package com.bgdcd.tasktwo;

import android.app.Activity;
import android.os.Bundle;
import android.os.Handler;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.Color;
import android.view.Window;
import android.view.WindowManager;
import android.webkit.WebChromeClient;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.ImageView;
import android.widget.FrameLayout;
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

    // Display the exact word.png downloaded from the repository as the app's opening cover.
    private void showCover() {
        FrameLayout root = new FrameLayout(this);
        root.setBackgroundColor(Color.BLACK);

        ImageView cover = new ImageView(this);
        cover.setBackgroundColor(Color.BLACK);
        cover.setScaleType(ImageView.ScaleType.FIT_CENTER);

        Bitmap bitmap = null;
        try (InputStream input = getAssets().open("word.png")) {
            bitmap = BitmapFactory.decodeStream(input);
        } catch (Exception e) {
            android.util.Log.e("TaskTwo", "Unable to load required cover image word.png", e);
        }

        if (bitmap != null) {
            cover.setImageBitmap(bitmap);
        } else {
            android.util.Log.e("TaskTwo", "word.png was not decoded; check APK assets");
        }

        root.addView(cover, new FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.MATCH_PARENT));
        setContentView(root);

        // Keep the cover visible long enough to be clearly seen before opening the lesson.
        handler.postDelayed(this::showTask, 2500);
    }

    private void showTask() {
        webView = new WebView(this);
        webView.setBackgroundColor(Color.WHITE);
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
    }

    @Override
    public void onBackPressed() {
        if (webView != null && webView.canGoBack()) webView.goBack();
        else super.onBackPressed();
    }

    @Override
    protected void onDestroy() {
        handler.removeCallbacksAndMessages(null);
        if (webView != null) webView.destroy();
        super.onDestroy();
    }
}