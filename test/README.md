# Test Images

把待测试图片放到 [images](/opt/lab-safety-monitor/test/images)。

运行命令：

```bash
python3 test/run_ppe_person_test.py
```

如果本机 Python 缺少 `cv2`、`ultralytics` 等后端依赖，直接用项目 Docker 环境：

```bash
bash test/run_ppe_person_test_docker.sh
```

递归扫描子目录并输出 JSON：

```bash
python3 test/run_ppe_person_test.py --recursive --save-json
```

Docker 方式同样支持参数：

```bash
bash test/run_ppe_person_test_docker.sh --recursive --save-json
```

输出结果在 [output](/opt/lab-safety-monitor/test/output)。

当前脚本复用项目现有检测链路中的这些部分：

- 人员检测：`backend/app/ml/person_detector.py`，模型 `YOLOv8m`
- PPE 检测：`backend/app/ml/yolov11_detector.py`，模型路径来自 `settings.YOLOV11_MODEL_PATH`
- 人员与 PPE 归属：`backend/app/ml/hybrid_detector.py`
- 绘制：`backend/app/ml/mask_utils.py`

这个测试脚本只做两类标注：

- 人员框
- 已检测到的 PPE 框

不会标出未佩戴 PPE 的违规框。
