# OpenCV 数字识别说明

虚拟环境：

```bash
source /home/intyu/env/bin/activate
```

在 LCD 上直接显示识别画面：

```bash
cd /home/intyu/Desktop/opencv
./run_opencv_digits_lcd.sh
```

只通过 SSH 运行，不显示窗口：

```bash
./run_opencv_digits.sh --no-window
```

普通 X11 窗口模式：

```bash
./run_opencv_digits.sh
```

串口输出给 STM32：

```bash
./run_opencv_digits_serial.sh
```

串口协议：

```text
digit,dx,dy\n
```

没有可靠识别时：

```text
-1,0,0\n
```

建议实际使用时限制 ROI：

```bash
./run_opencv_digits_lcd.sh --roi x,y,w,h
```

调试阈值：

```bash
./run_opencv_digits_lcd.sh --debug --save-debug
```
