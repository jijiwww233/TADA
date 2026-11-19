package dev.mousehole.collection;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.ContentUris;
import android.content.Intent;
import android.database.ContentObserver;
import android.database.Cursor;
import android.graphics.drawable.Icon;
import android.net.Uri;
import android.os.Bundle;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;
import android.provider.MediaStore;
import java.util.Locale;
import org.json.JSONException;
import org.json.JSONObject;

public final class CaptureService extends Service {
    private static final String LISTEN = "collection-listen";
    private static final String NEW_SHOT = "collection-screenshot";
    private static final Uri IMAGES = MediaStore.Images.Media.EXTERNAL_CONTENT_URI;
    static volatile boolean active;
    private final Handler handler = new Handler(Looper.getMainLooper());
    private final Runnable scan = this::scan;
    private long since;
    private long lastId = -1;
    private final ContentObserver observer = new ContentObserver(handler) {
        @Override public void onChange(boolean selfChange, Uri uri) {
            handler.removeCallbacks(scan);
            handler.postDelayed(scan, 500);
        }
    };

    @Override public void onCreate() {
        super.onCreate();
        NotificationManager manager = getSystemService(NotificationManager.class);
        manager.createNotificationChannel(new NotificationChannel(LISTEN, "截屏提示已开启", NotificationManager.IMPORTANCE_LOW));
        manager.createNotificationChannel(new NotificationChannel(NEW_SHOT, "截图收藏", NotificationManager.IMPORTANCE_HIGH));
        PendingIntent open = PendingIntent.getActivity(this, 1, new Intent(this, MainActivity.class),
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        startForeground(1, new Notification.Builder(this, LISTEN)
                .setSmallIcon(R.drawable.ic_mouse_check).setContentTitle("截图收藏")
                .setContentText("等待新的截图").setContentIntent(open).setOngoing(true).build());
        since = System.currentTimeMillis() / 1000;
        getContentResolver().registerContentObserver(IMAGES, true, observer);
        active = true;
    }
    @Override public int onStartCommand(Intent intent, int flags, int startId) { return START_NOT_STICKY; }
    @Override public IBinder onBind(Intent intent) { return null; }
    @Override public void onDestroy() {
        active = false;
        handler.removeCallbacks(scan);
        getContentResolver().unregisterContentObserver(observer);
        getSystemService(NotificationManager.class).cancel(2);
        super.onDestroy();
    }

    private void scan() {
        String id = MediaStore.Images.Media._ID;
        String name = MediaStore.Images.Media.DISPLAY_NAME;
        String path = MediaStore.Images.Media.RELATIVE_PATH;
        String added = MediaStore.Images.Media.DATE_ADDED;
        try (Cursor cursor = getContentResolver().query(IMAGES, new String[]{id, name, path},
                added + " >= ? AND " + MediaStore.Images.Media.IS_PENDING + " = 0",
                new String[]{Long.toString(Math.max(since, System.currentTimeMillis() / 1000 - 40))},
                added + " DESC, " + id + " DESC")) {
            while (cursor != null && cursor.moveToNext()) {
                long current = cursor.getLong(0);
                String filename = String.valueOf(cursor.getString(1)).toLowerCase(Locale.ROOT);
                String folder = String.valueOf(cursor.getString(2)).toLowerCase(Locale.ROOT);
                if (!filename.startsWith("screenshot") && !folder.contains("/screenshots/")
                        && !filename.startsWith("截屏") && !filename.startsWith("截图")) continue;
                if (current != lastId) {
                    notifyScreenshot(ContentUris.withAppendedId(IMAGES, current));
                    lastId = current;
                }
                break;
            }
        } catch (SecurityException denied) {
            stopSelf();
        }
    }

    private void notifyScreenshot(Uri uri) {
        String title = "保存这张截图";
        String content = "打开预览后传到素材库";
        Intent action = new Intent(this, MainActivity.class).setAction(MainActivity.OPEN_SCREENSHOT).setData(uri)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_GRANT_READ_URI_PERMISSION);
        PendingIntent open = PendingIntent.getActivity(this, 2, action,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        Notification.Builder notification = new Notification.Builder(this, NEW_SHOT)
                .setSmallIcon(R.drawable.ic_mouse_check).setContentTitle(title).setContentText(content)
                .setVisibility(Notification.VISIBILITY_PRIVATE).setAutoCancel(true).setTimeoutAfter(120_000)
                .setContentIntent(open)
                .addAction(new Notification.Action.Builder(Icon.createWithResource(this, R.drawable.ic_mouse_check),
                        "传到素材库", open).build());
        notification.addExtras(focusData(title, content));
        getSystemService(NotificationManager.class).notify(2, notification.build());
    }

    private Bundle focusData(String title, String content) {
        Bundle extras = new Bundle();
        String ref = "miui.focus.pic_collection";
        Bundle icons = new Bundle();
        icons.putParcelable(ref, Icon.createWithResource(this, R.drawable.ic_mouse_check));
        extras.putBundle("miui.focus.pics", icons);
        extras.putBundle("miui.focus.actions", new Bundle());
        try {
            JSONObject icon = new JSONObject().put("type", 1).put("pic", ref);
            JSONObject text = new JSONObject().put("title", title).put("content", content);
            JSONObject expanded = new JSONObject().put("imageTextInfoLeft",
                    new JSONObject().put("type", 1).put("picInfo", icon).put("textInfo", text));
            JSONObject island = new JSONObject().put("islandProperty", 1).put("islandPriority", 2)
                    .put("islandTimeout", 120).put("smallIslandArea", new JSONObject().put("picInfo", icon))
                    .put("bigIslandArea", expanded);
            JSONObject details = new JSONObject().put("protocol", 3).put("business", "mousehole_collection")
                    .put("ticker", title).put("enableFloat", false).put("islandFirstFloat", false)
                    .put("isShowNotification", true).put("param_island", island)
                    .put("iconTextInfo", new JSONObject().put("title", title).put("content", content)
                            .put("animIconInfo", new JSONObject().put("type", 0).put("src", ref)));
            extras.putString("miui.focus.param", new JSONObject().put("param_v2", details)
                    .put("isShowNotification", true).toString());
        } catch (JSONException ignored) { }
        return extras;
    }
}
