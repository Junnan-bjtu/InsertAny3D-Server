# InsertAny3D Server

这是 InsertAny3D 的服务器运行时仓库，负责执行 TRELLIS、GIM、姿态估计和 SAGS 阶段。
本仓库只保存源码、测试、版本记录和不含密钥的配置模板；模型权重、Python 虚拟环境、缓存、任务输入和运行结果均留在服务器本地。

## 运行环境

现有服务器 checkout 和环境路径保持不变：

```text
${INSERTANY3D_SERVER_ROOT:-/path/to/InsertAny3D-Server}
${INSERTANY3D_SERVER_ROOT:-/path/to/InsertAny3D-Server}/third_party/TRELLIS/.venv/bin/python
```

发布目录不会携带 `third_party/` 的实际 checkout。按 `.gitmodules` 固定第三方仓库后，使用 `tools/bootstrap_third_party.sh` 和
`tools/install_environments.sh` 准备环境。服务器机器配置模板位于
[`docs/server.env.example`](docs/server.env.example)。将它复制为运行目录中的
`.insertany3d/runtime.env`，再把缓存目录改成服务器上已存在的绝对路径；实际文件保持私有，不进入 Git。

## 入口

发布目录不包含第三方实际 checkout。下面的命令使用原服务器环境中的 Python，
但明确调用当前发布目录中的脚本。默认值对应当前服务器和旁路发布目录；迁移到
其他机器时可覆盖这两个变量：

```bash
export INSERTANY3D_PYTHON="${INSERTANY3D_PYTHON:-/path/to/TRELLIS/.venv/bin/python}"
export INSERTANY3D_SERVER_ROOT="${INSERTANY3D_SERVER_ROOT:-/path/to/InsertAny3D-Server}"
```

```bash
"$INSERTANY3D_PYTHON" "$INSERTANY3D_SERVER_ROOT/tools/run_insert_pipeline.py" --help
"$INSERTANY3D_PYTHON" "$INSERTANY3D_SERVER_ROOT/tools/run_insert_batch.py" --help
"$INSERTANY3D_PYTHON" "$INSERTANY3D_SERVER_ROOT/tools/stage_adapter.py" --help
```

低成本检查：

```bash
"$INSERTANY3D_PYTHON" -m py_compile "$INSERTANY3D_SERVER_ROOT"/tools/*.py "$INSERTANY3D_SERVER_ROOT"/tools/model_center/*.py
"$INSERTANY3D_PYTHON" "$INSERTANY3D_SERVER_ROOT/tools/test_insert_batch.py"
"$INSERTANY3D_PYTHON" "$INSERTANY3D_SERVER_ROOT/tools/test_estimate_similarity_pose.py"
PYTHONPATH="$INSERTANY3D_SERVER_ROOT/tools" "$INSERTANY3D_PYTHON" "$INSERTANY3D_SERVER_ROOT/tools/model_center/tests/test_model_center.py"
```

这些检查不启动 TRELLIS、GIM、SAGS 或付费 API。真实运行需要单独准备输入并按主仓库的任务契约执行。

## 发布边界

- `tools/` 是当前服务器 stage 入口和测试；`tools/remote_runtime.lock.json` 用于部署前逐文件校验。
- `metrics/` 只保留指标入口源码，不包含 HPSv2 checkout 或下载的权重。
- `code/environment/`、`code/third_party/` 保存环境版本、补丁和第三方固定版本记录。
- 任务数据、模型、缓存、日志、调试包、备份和验收资料不属于公开代码。
- `tools/app3.py`、`tools/trellis.py` 等未被正式流程调用的历史实验入口不在发布目录中。

本仓库的代码更新不会自动覆盖服务器运行目录；发布后须先在旁路目录完成验证，再由部署流程生成运行时快照。
