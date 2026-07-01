# 显示切换说明

本文件夹提供两个脚本：

- `切换到LCD显示.sh`
- `切换到大屏显示.sh`

## LCD 显示模式

运行：

```bash
cd /home/intyu/Desktop/显示切换
./切换到LCD显示.sh
```

效果：

- 使用 SPI LCD：`/dev/fb0`
- X11 桌面分辨率：`480x320`
- VNC 端口：`5900`
- VNC 画面就是 LCD 真实画面
- 支持触摸：`ADS7846 Touchscreen`

如果 LCD 停在终端：

```bash
sudo chvt 7
```

## 大屏显示模式

运行：

```bash
cd /home/intyu/Desktop/显示切换
./切换到大屏显示.sh
```

效果：

- 恢复 KMS/DRM
- 恢复 Raspberry Pi OS 默认 Wayland/labwc 桌面
- 恢复 WayVNC
- 适合 HDMI 或原来的大屏幕

## 重要区别

LCD 模式下 VNC 共享的是 480x320 的真实 LCD，因此 VNC 画面小是正常的。

大屏模式下 VNC 使用 WayVNC，适合正常远程桌面调试，但 SPI LCD 不再作为主桌面。
