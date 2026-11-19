package dev.mousehole.collection;

import android.Manifest;
import android.app.Activity;
import android.content.ClipData;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.provider.MediaStore;
import android.text.InputType;
import android.view.ViewGroup;
import android.widget.Button;
import android.widget.EditText;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import java.io.IOException;
import java.util.ArrayList;

public final class MainActivity extends Activity {
    static final String OPEN_SCREENSHOT = "dev.mousehole.collection.OPEN_SCREENSHOT";
    private static final int PICK = 100, WATCH_PERMISSION = 101;
    private final ArrayList<Uri> selection = new ArrayList<>();
    private EditText pair;
    private TextView status;
    private TextView chosen;
    private ImageView preview;
    private Button send;
    private Button watch;
    private boolean uploading;

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        ScrollView scroll = new ScrollView(this);
        LinearLayout page = new LinearLayout(this);
        page.setOrientation(LinearLayout.VERTICAL);
        page.setPadding(dp(24), dp(48), dp(24), dp(30));
        scroll.addView(page);
        setContentView(scroll);

        TextView title = text(page, "截图收藏");
        title.setTextSize(28);
        text(page, "在电脑素材库点「导入 +」，生成手机配对链接。");
        pair = new EditText(this);
        pair.setSingleLine();
        pair.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);
        pair.setHint("手机配对链接");
        pair.setContentDescription("手机配对链接");
        pair.setText(getSharedPreferences(LibraryUpload.PREFS, MODE_PRIVATE).getString("pair", ""));
        page.addView(pair);
        button(page, "保存连接", () -> {
            try {
                Uri value = LibraryUpload.pairing(pair.getText().toString());
                getSharedPreferences(LibraryUpload.PREFS, MODE_PRIVATE).edit().putString("pair", value.toString()).apply();
                status.setText("已保存配对链接。");
            } catch (IllegalArgumentException e) { status.setText(e.getMessage()); }
        });
        button(page, "从相册选图", this::pick);
        chosen = text(page, "还未选图");
        preview = new ImageView(this);
        preview.setScaleType(ImageView.ScaleType.FIT_CENTER);
        preview.setContentDescription("选中图片预览");
        page.addView(preview, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(144)));
        preview.setVisibility(ImageView.GONE);
        send = button(page, "传到素材库", this::send);
        watch = button(page, "开启截屏提示", this::toggleWatch);
        text(page, "截屏提示只读取新截图的相册记录，点「传到素材库」才上传。");
        status = text(page, "");
        status.setAccessibilityLiveRegion(TextView.ACCESSIBILITY_LIVE_REGION_POLITE);
        if (state != null) {
            ArrayList<Uri> saved = state.getParcelableArrayList("selection");
            if (saved != null) selection.addAll(saved);
        } else receive(getIntent());
        refresh();
    }

    @Override protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        receive(intent);
        refresh();
    }
    @Override protected void onResume() { super.onResume(); if (watch != null) refresh(); }
    @Override protected void onSaveInstanceState(Bundle state) {
        state.putParcelableArrayList("selection", new ArrayList<>(selection));
        super.onSaveInstanceState(state);
    }

    private void pick() {
        Intent intent = new Intent(Intent.ACTION_OPEN_DOCUMENT)
                .setType("image/*").addCategory(Intent.CATEGORY_OPENABLE)
                .putExtra(Intent.EXTRA_ALLOW_MULTIPLE, true);
        startActivityForResult(intent, PICK);
    }

    @Override protected void onActivityResult(int code, int result, Intent data) {
        super.onActivityResult(code, result, data);
        if (code == PICK && result == RESULT_OK && data != null) {
            setImages(data, false);
            refresh();
        }
    }

    private void receive(Intent i) {
        if (i == null) return;
        if (OPEN_SCREENSHOT.equals(i.getAction()) || Intent.ACTION_SEND.equals(i.getAction())
                || Intent.ACTION_SEND_MULTIPLE.equals(i.getAction())) setImages(i, true);
    }

    private void setImages(Intent i, boolean shared) {
        selection.clear();
        add(i.getData());
        ClipData clip = i.getClipData();
        if (clip != null) for (int n = 0; n < clip.getItemCount(); n++) add(clip.getItemAt(n).getUri());
        if (shared) {
            if (Intent.ACTION_SEND_MULTIPLE.equals(i.getAction())) {
                ArrayList<Uri> uris = Build.VERSION.SDK_INT >= 33
                        ? i.getParcelableArrayListExtra(Intent.EXTRA_STREAM, Uri.class)
                        : i.getParcelableArrayListExtra(Intent.EXTRA_STREAM);
                if (uris != null) for (Uri uri : uris) add(uri);
            } else if (Intent.ACTION_SEND.equals(i.getAction())) {
                add(Build.VERSION.SDK_INT >= 33 ? i.getParcelableExtra(Intent.EXTRA_STREAM, Uri.class)
                        : i.getParcelableExtra(Intent.EXTRA_STREAM));
            }
        }
        if (selection.size() == LibraryUpload.MAX_FILES) status.setText("每次可传最多 8 张。");
    }

    private void add(Uri uri) {
        if (uri != null && "content".equals(uri.getScheme()) && !selection.contains(uri)
                && selection.size() < LibraryUpload.MAX_FILES) selection.add(uri);
    }

    private void refresh() {
        chosen.setText(selection.isEmpty() ? "还未选图" : "已选 " + selection.size() + " 张");
        preview.setVisibility(selection.isEmpty() ? ImageView.GONE : ImageView.VISIBLE);
        preview.setImageURI(selection.isEmpty() ? null : selection.get(0));
        send.setEnabled(!uploading && !selection.isEmpty());
        watch.setText(CaptureService.active ? "关闭截屏提示" : "开启截屏提示");
    }

    private void send() {
        final Uri address;
        try { address = LibraryUpload.pairing(pair.getText().toString()); }
        catch (IllegalArgumentException e) { status.setText(e.getMessage()); return; }
        ArrayList<Uri> images = new ArrayList<>(selection);
        if (images.isEmpty() || uploading) return;
        uploading = true;
        send.setEnabled(false);
        status.setText("正在上传…");
        new Thread(() -> {
            try {
                String result = LibraryUpload.send(getApplicationContext(), address, images);
                runOnUiThread(() -> { uploading = false; status.setText(result); send.setEnabled(!uploading && !selection.isEmpty()); });
            } catch (Exception e) {
                String reason = e instanceof java.io.FileNotFoundException ? "????????????????"
                        : e instanceof IOException && e.getMessage() != null && e.getMessage().matches(".*[一-龥].*")
                        ? e.getMessage() : "上传失败，请检查手机和电脑之间的连接后重试。";
                runOnUiThread(() -> { uploading = false; status.setText(reason); send.setEnabled(!uploading && !selection.isEmpty()); });
            }
        }, "collection-upload").start();
    }

    private void toggleWatch() {
        if (CaptureService.active) {
            stopService(new Intent(this, CaptureService.class));
            watch.setText("开启截屏提示");
            return;
        }
        ArrayList<String> needed = new ArrayList<>();
        String media = Build.VERSION.SDK_INT >= 33 ? Manifest.permission.READ_MEDIA_IMAGES : Manifest.permission.READ_EXTERNAL_STORAGE;
        if (checkSelfPermission(media) != PackageManager.PERMISSION_GRANTED) needed.add(media);
        if (Build.VERSION.SDK_INT >= 33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED)
            needed.add(Manifest.permission.POST_NOTIFICATIONS);
        if (!needed.isEmpty()) requestPermissions(needed.toArray(new String[0]), WATCH_PERMISSION);
        else enableWatch();
    }
    @Override public void onRequestPermissionsResult(int code, String[] permissions, int[] results) {
        super.onRequestPermissionsResult(code, permissions, results);
        if (code != WATCH_PERMISSION || results.length == 0) return;
        for (int result : results) if (result != PackageManager.PERMISSION_GRANTED) {
            status.setText("截屏提示需要图片和通知权限；相册选图仍可使用。");
            return;
        }
        enableWatch();
    }
    private void enableWatch() {
        startForegroundService(new Intent(this, CaptureService.class));
        watch.setText("关闭截屏提示");
        status.setText("截屏提示已开启，等待下一张截图。");
    }
    private TextView text(LinearLayout parent, String content) {
        TextView view = new TextView(this);
        view.setText(content);
        view.setPadding(0, dp(8), 0, dp(8));
        parent.addView(view);
        return view;
    }
    private Button button(LinearLayout parent, String title, Runnable action) {
        Button button = new Button(this);
        button.setText(title);
        button.setOnClickListener(view -> action.run());
        parent.addView(button);
        return button;
    }
    private int dp(int amount) { return Math.round(amount * getResources().getDisplayMetrics().density); }
}
