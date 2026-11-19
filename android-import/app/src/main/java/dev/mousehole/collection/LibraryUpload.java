package dev.mousehole.collection;

import android.content.Context;
import android.net.Uri;
import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.net.URL;
import java.net.Proxy;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.TimeUnit;
import okhttp3.MediaType;
import okhttp3.MultipartBody;
import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.RequestBody;
import okhttp3.Response;
import org.json.JSONObject;

final class LibraryUpload {
    static final String PREFS = "collection";
    static final int MAX_FILES = 8;
    private static final int MAX_FILE_BYTES = 20 * 1024 * 1024;
    private static final int MAX_BATCH_BYTES = 39 * 1024 * 1024;
    private static final OkHttpClient CLIENT = new OkHttpClient.Builder()
            .proxy(Proxy.NO_PROXY).followRedirects(false).followSslRedirects(false)
            .callTimeout(90, TimeUnit.SECONDS).build();

    static Uri pairing(String text) {
        Uri value = Uri.parse(text.trim());
        String scheme = value.getScheme();
        String host = value.getHost();
        String token = value.getFragment();
        if (host == null || value.getUserInfo() != null || token == null || !token.matches("[A-Za-z0-9_-]{24,128}")
                || value.getQuery() != null || !("".equals(value.getPath()) || "/".equals(value.getPath())))
            throw new IllegalArgumentException("请粘贴电脑生成的完整配对链接。");
        if (!"https".equals(scheme) && !("http".equals(scheme) && privateHost(host)))
            throw new IllegalArgumentException("请使用局域网、USB 调试连接或 HTTPS。");
        return value;
    }

    private static boolean privateHost(String host) {
        if ("localhost".equalsIgnoreCase(host)) return true;
        String[] cells = host.split("\\.", -1);
        if (cells.length != 4) return false;
        int[] parts = new int[4];
        for (int n = 0; n < parts.length; n++) {
            if (!cells[n].matches("[0-9]{1,3}")) return false;
            parts[n] = Integer.parseInt(cells[n]);
            if (parts[n] > 255) return false;
        }
        return parts[0] == 127 || parts[0] == 10 || parts[0] == 192 && parts[1] == 168
                || parts[0] == 172 && parts[1] >= 16 && parts[1] <= 31;
    }

    static String send(Context context, Uri pairing, List<Uri> images) throws Exception {
        ArrayList<File> cache = new ArrayList<>();
        MultipartBody.Builder request = new MultipartBody.Builder().setType(MultipartBody.FORM);
        long total = 0;
        try {
            for (int i = 0; i < images.size(); i++) {
                Uri uri = images.get(i);
                String type = context.getContentResolver().getType(uri);
                String suffix = extension(type);
                File item = File.createTempFile("upload-", "." + suffix, context.getCacheDir());
                cache.add(item);
                try (InputStream in = context.getContentResolver().openInputStream(uri);
                     FileOutputStream out = new FileOutputStream(item)) {
                    if (in == null) throw new IOException("无法读取图片，请重新选图。");
                    byte[] block = new byte[32768];
                    for (int read, length = 0; (read = in.read(block)) != -1; ) {
                        length += read;
                        total += read;
                        if (length > MAX_FILE_BYTES || total > MAX_BATCH_BYTES)
                            throw new IOException("图片太大，请缩小或分批上传。");
                        out.write(block, 0, read);
                    }
                }
                request.addFormDataPart("files", "screenshot-" + (i + 1) + "." + suffix,
                        RequestBody.create(item, MediaType.parse(type == null ? "application/octet-stream" : type)));
            }
            URL endpoint = new URL(pairing.getScheme(), pairing.getHost(), pairing.getPort(), "/api/images/import");
            Request call = new Request.Builder().url(endpoint).header("Authorization", "Bearer " + pairing.getFragment())
                    .post(request.build()).build();
            try (Response response = CLIENT.newCall(call).execute()) {
                if (response.code() == 401) throw new IOException("配对已失效，请粘贴新的链接。");
                if (response.code() == 413) throw new IOException("图片太大，请缩小或分批上传。");
                if (!response.isSuccessful() || response.body() == null) throw new IOException("电脑未能接收图片。");
                JSONObject result = new JSONObject(response.body().string());
                int added = result.getJSONArray("added").length();
                int duplicate = result.getJSONArray("duplicates").length();
                int failed = result.getJSONArray("failed").length();
                List<String> summary = new ArrayList<>();
                if (added > 0) summary.add("已收藏 " + added + " 张");
                if (duplicate > 0) summary.add(duplicate + " 张已在库中");
                if (failed > 0) summary.add(failed + " 张无法导入");
                if (summary.isEmpty()) throw new IOException("这次没有图片入库。");
                return String.join(" · ", summary);
            }
        } finally {
            for (File item : cache) item.delete();
        }
    }

    private static String extension(String type) {
        if (type == null) return "png";
        switch (type) {
            case "image/jpeg": return "jpg";
            case "image/webp": return "webp";
            case "image/gif": return "gif";
            case "image/bmp": return "bmp";
            case "image/heif": return "heif";
            case "image/heic": return "heic";
            case "image/tiff": return "tiff";
            default: return "png";
        }
    }
}
