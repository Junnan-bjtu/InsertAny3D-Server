# Server-side InsertAny3D tools

这些脚本已同步到服务器项目 `${INSERTANY3D_SERVER_ROOT:-/path/to/InsertAny3D-Server}/tools`。

| 脚本 | 用途 |
| --- | --- |
| `gemini_edit.py` | APIYi Gemini 图片编辑，输入图片和 prompt，输出图片 |
| `auto_segment.py` | LangSAM/legacy 文本分割并生成 SAGS `points.json` |
| `segment_image.py` | GroundingDINO + SAM legacy 分割，支持同类多框 |
| `generate_trellis_asset.py` | 编辑图生成 TRELLIS Gaussian PLY/可选 GLB |
| `render_trellis_3dgs.py` | PLY 生成 RGB、深度、COLMAP 和 3DGS model |
| `render_trellis_views.py` | 先按 canonical 视角渲染，或把 Unity 外参转换到生成坐标后精确渲染 |
| `run_gim_match.py` | 带 ROI/mask/生成前景过滤的 GIM 匹配及 `matches.json` |
| `estimate_similarity_pose.py` | 独立拟合、交叉验证、联合门禁和双深度反投影，输出 Unity pose JSON |
| `run_sags_text.py` | 用完整 mask、逐视角 SAM 候选和可见性投票无 UI 运行 SAGS |
| `SAGS_ALGORITHM.md` | SAGS 算法流程、参数、输入输出和诊断字段 |
| `third_party/SAGS/app3.py` | 只读浏览当前 `runs/` 中已完成 SAGS 任务的前后 3DGS、多视角标注和诊断图 |
| `select_sags_views.py` | 根据 target/anchor 双 mask 的有效性、可见面积和质量排序 ring 视角；重叠只作诊断，不作硬拒绝 |
| `run_insert_pipeline.py` | 单任务串联组合 TRELLIS、三视图、GIM、pose、SAGS，并写任务 manifest/`05_pose`/`06_sags` |
| `run_insert_batch.py` | 从 JSON job 文件串行启动多个独立单任务编排器，写 batch manifest |
| `../tools/run_local_edit_remote_pipeline.py` | 本机真实图片编辑、来源校验、上传并启动远端串行任务 |
| `generate_insert_task_prompts_vlm.py` | 本机用 APIYi VLM 根据任务描述和原图生成结构化任务提示字段 |
| `vlm_prompt_gradio/app.py` | 最小 Gradio 页面；上传原图和任务描述后查看 VLM 结构化结果 |
| `test_gemini_edit.py` | 不联网的 Gemini API 请求/响应契约测试 |
| `test_estimate_similarity_pose.py` | 双视角 pose 和编排器的合成真值测试 |
| `test_insert_batch.py` | 无第三方依赖的 job/prompt 串行命令构建测试 |

每个任务使用独立的
`--run-root <server-root>/<scene-id> --task-id Task_001` 调用；脚本只拥有一个
任务目录，适合由外部串行调度器逐任务启动。`--output-dir` 保留兼容。

Unity“任务管理”中的“VLM 自动填写提示词”会调用本机
`generate_insert_task_prompts_vlm.py`。它使用 APIYi 原生 Gemini
`/v1beta/models/<model>:generateContent` 路由，默认模型为
`gemini-3.1-pro-preview`，把 PNG 作为 `inlineData` 发送，并要求返回 JSON
结构。密钥只从当前 WSL shell 的 `GEMINI_API_KEY` 或
`~/.config/insertany3d/apiyi_key` 读取；原图、任务描述、原始响应和结果保存在
Unity `Library/InsertWorkflow/VlmPromptGeneration/` 下，不写入密钥。
生成结果包含 `editPrompt`、`objectPrompt`、`anchorPrompt`、
`anchorMaskPrompt`、`trellisMaskPrompts`、置信度和警告；通用的默认插入约束仍由
`InsertTask` 维护。网络失败、429/5xx 重试、非 JSON 响应和英文检测词校验都会在
脚本层拒绝，Unity 只会把通过校验的字段写回任务。

需要脱离 Unity 手工检查 VLM 结果时，可启动
`tools/vlm_prompt_gradio/app.py`；完整的端点、请求体、鉴权、响应和错误处理说明见
`tools/vlm_prompt_gradio/README.md`。
当前默认是 `--trellis-input composite`、`--render-mode anchor`、1024 分辨率；
组合任务可用重复的 `--trellis-mask-prompt` 先生成 `00_trellis_input` 并取 mask
并集，避免 TRELLIS 的自动去背景误裁。当前输入正面对应 `render_yaw_degrees=180`。
组合流程在生成最终三视图后，会额外在 `03_sags_views/` 生成默认六视角环拍图，并对每张图独立执行自动标注；每视角的 `mask.png`、`points.json` 和检测清单位于
`06_sags/annotations/<view>/`，SAGS 使用 `--annotation-mode independent` 融合这些标注，产出
`06_sags/inserted_object.ply`。GIM 和 pose 仍只使用 `03_rendered_3dgs/` 的三台 Unity 对齐相机。
整理调试包时，六视角 RGB、深度和相机会复制到 `04_sags/ring6_views/`，逐视角标注、投票诊断和输出 PLY 位于 `04_sags/results/`；原始完整目录仍在 `99_raw_pipeline/`。
独立标注默认还要覆盖中心 Gaussian 几何先验至少 25%；明显被遮挡而误标到锚点的视角会作为 unknown 跳过投票，可用 `sags_independent_min_prior_coverage` 设为 0 关闭。
使用 `--sags-view-mode legacy` 可回退到旧的中心点投影流程；使用 `--sags-yaw-offsets`、`--sags-view-names` 可调整环拍配置。完整参数、Unity 文件协议和输出树见
`codex_ops/WORKFLOW.md`。

提供 Unity 三组图片、深度、相机和 `--unity-manifest` 后，主流程会先渲染
canonical 三视图，用中心图求粗位姿，再把三台 Unity 相机的完整外参变换到
TRELLIS 坐标系重渲染。最终 pose 默认联合 `left,center,right`，任一视角无支持、
留一验证失败或总拟合退化时写 `status=rejected`；这不是脚本崩溃，Unity 端只会
应用明确写有 `status=ready` 的结果。

下游分割默认从每个 mask 生成 4 个分散的内部正点击点，而不是只取最厚位置。
这能避免人物任务只点中帽子、躯干等单一部件；可用
`sags_points_per_mask` 或 `--sags-points-per-mask` 调整，人工
`sags_points_json` 仍具有最高优先级。

GIM 的稠密匹配抽样与 OpenCV RANSAC 使用任务 `seed`（默认 1）固定随机状态，
并把 seed 写入 `matches.json`，保证相同输入的 pose 续跑可复现。
GIM 默认使用 Unity 锚点投影的圆形 ROI、生成图非黑前景、深度有效性/深度跳变
过滤和网格均匀采样；若任务提供 `scene_masks` 或 `generated_masks`，还会在原图
坐标中叠加完整二值 mask。

SAGS ring6 默认先对每个视角分别生成 target 和 anchor mask，再用 `select_sags_views.py` 做有效性和 target-anchor 分离筛选；面积只用于排除空/异常 mask，分离判断使用 mask IoU、边界间距和连通域，必要时可追加 VLM 语义检查。入选视角固定后才参与 Gaussian 投票，未入选视角不进入投票集合。生产默认关闭旧版单像素 z-buffer：只要 Gaussian 中心投影在图像内就参与 mask 投票，避免同一整数像素后方的 Gaussian 全部变成 unknown；需要复现旧行为时使用 `--sags-use-zbuffer`。入选集合内默认至少 75% 的视角必须有有效可见性结果，才允许进行正票判断，避免退化成“只统计恰好可见的两张图”。锚点 mask 会反投影为 Gaussian 排除集合，结果随后做 3D 连通组件清理。`force_seed_radius` 保留为兼容参数但新版不再修改输入 mask。默认至少 3 个票且比例不低于 0.5；`--sags-view-mode legacy` 或直接调用 `run_sags_text.py` 的旧参数时，仍使用中心 mask、中心点投影和侧视角 SAM 候选。逐视角输入、筛选记录、overlay、锚点排除和投票数量写入 `06_sags/`。

底层诊断脚本仍支持场景级批量，但 Unity 完整工作流不会调用该模式；日常运行必须在
Unity 面板中一次选择一个任务。需要单独调试底层批处理时可使用：

```bash
third_party/TRELLIS/.venv/bin/python tools/run_insert_batch.py \
  --jobs <scene>/insert_jobs.json --skip-ready
```

推荐从工作区根目录使用本地入口。它默认只执行图片编辑，确认图片后再分别上传和
启动远端任务；也可用 `--stage all` 一次完成。API key 仅从
`GEMINI_API_KEY` 或 `~/.config/insertany3d/apiyi_key` 读取，不写入 job、日志或
远端目录：

```bash
python3 tools/run_local_edit_remote_pipeline.py \
  --local-run-root <local-run-root>/Farm_001 \
  --stage edit
```

每个 `edited/` 会保存 `center.png`、`prompt.txt`、`response.json` 和
`edit_manifest.json`。后两阶段会强制核对原图、完整 prompt、编辑图三个哈希，
拒绝旧 fallback 或来源不明图片。

跨任务默认编辑提示词采用“只插入”约束，优先级为：先保持原图和锚点状态，再满足
新增物体的交互动作。已有接地物体必须留在原位置，关闭的盖板、门和引擎盖必须保持
关闭；发生冲突时，只调整新增人物或物体的姿势。调度器会把历史内置默认词和 `null`
字段自动升级到该约束，但不会覆盖用户自定义默认词，也不会把明确的空字符串改回默认值。
任务自己的 `editPrompt` 应只描述新增内容，`anchorPrompt` 应尽量明确指出被锁定的原物体。
测试候选可保存在任务的 `edited_trials/<版本>/`，确认后再让 `edited/` 进入上传阶段。
已有正确 `02_trellis/sample.ply` 时，本机入口可用 `--reuse-remote-trellis` 配合
`--stage upload --allow-existing-remote` 写入续跑 jobs，再用 `--stage run` 只重做
渲染、SAGS、GIM 和 pose。

如果只是调整 SAGS 点击点，不应重新估计 pose，可在续跑 job 的单项 `options` 中
加入 `"skip_gim": true, "skip_pose": true`；这样会保留已有
`05_pose/pose.json`，只更新渲染、分割和 `06_sags`。

job 的 `prompts` 支持 `edit_default/edit_user`、`object_default/object_user`、
`anchor_default/anchor_user`；调度器会把每一对合并并写入任务目录的
`prompts.json`。首批建议 `seg_engine` 固定为 `legacy`，人工修正 mask 后再继续。
若已经得到人工 `points.json`，在任务的 `options` 中加入
`sags_points_json` 和 `skip_segmentation: true` 即可跳过自动分割。
任务也可提供 `unity_manifest`，自动读取 Unity 输出的默认/用户 prompt；job 中
显式 prompt 始终优先。

远端入口为 `ssh -p 25367 root@<server-host>`。需要人工修正 legacy 标注时，使用：

```bash
cd ${INSERTANY3D_SERVER_ROOT:-/path/to/InsertAny3D-Server}/third_party/SAGS
GRADIO_ANALYTICS_ENABLED=False CUDA_VISIBLE_DEVICES=0 .venv/bin/python app_text.py
```

Gradio UI 已完成模块导入和 `Blocks` 构建测试；CLI 批处理仍使用
`run_sags_text.py`，不会启动 Web 服务。
