# SAGS 算法流程

本文只描述当前 InsertAny3D 工作流中 SAGS 的算法数据流，不描述 Unity 操作、SSH、任务调度或模型服务安装。代码入口是 `run_sags_text.py`，完整流程由 `run_insert_pipeline.py` 负责准备视角和标注，再调用该入口。

## 1. 目标和术语

SAGS 的目标是：给定一个包含“锚点物体 + 新生成物体”的 3D Gaussian Splatting 场景，以及新物体在若干渲染视图中的 2D 标注，输出只包含新物体的 Gaussian PLY。

- **Gaussian**：组合场景中的一个 3D 高斯点，包含位置、透明度、颜色/球谐系数、尺度和旋转。
- **视角**：SAGS 3DGS model 中的一台训练相机。当前 ring6 名称为 `center`、`ring_060`、`ring_120`、`ring_180`、`ring_240`、`ring_300`。
- **源视角**：用于把 2D 正点击点映射成 3D seed 的视角，当前默认为 `center`。
- **标注 mask**：某一视角的 2D 二值目标图。白色表示新物体，黑色表示非目标。
- **Gaussian 标签**：对每个 Gaussian 在某个视角给出的 `1`（目标）、`0`（可见但非目标）或 `-1`（该视角中不可判定/不可见）。
- **几何先验**：把源视角的中心 mask 反投影到 3D 后，再投影到另一个视角得到的保守参考区域。它用于过滤明显不可能的独立标注，不直接替代独立标注。

## 2. 路线选择

完整编排器根据输入选择路线：

| 条件 | 实际路线 | 说明 |
| --- | --- | --- |
| provider 是 TRELLIS，`sags_view_mode=ring6`，且没有外部 `sags_points_json/sags_mask` | `ring6_independent` | 当前默认路线；六个视角各自生成标注并直接投票 |
| 提供外部 points/mask，或显式 `sags_view_mode=legacy` | `legacy`/`legacy_external_annotation` | 中心视角点提示传播到其他视角，再用 SAM 生成侧视角候选 |
| provider 直接返回单物体 Gaussian（如 SAM3D/Hunyuan） | `provider_output` | 直接复制 provider PLY，不再做组合场景 SAGS 提取 |

下面重点说明默认的 `ring6_independent`，并在相关步骤标出 legacy 差异。

## 3. 输入和输出接口

### 3.1 算法输入

| 输入 | 位置/格式 | 用途 |
| --- | --- | --- |
| 组合 Gaussian | `02_trellis/sample.ply`，随后转为 `03_sags_views/model/` | 待分割的全部 3D Gaussians |
| SAGS 相机和模型参数 | `03_sags_views/model/cfg_args`、`point_cloud/`、训练相机 | 投影、深度可见性和 SAM 特征 |
| 六视角 RGB | `03_sags_views/source/images/<view>.png` | 与视角对应的自动标注输入；SAGS 加载模型时也会按训练相机渲染 RGB |
| 每视角二值 mask | `06_sags/annotations/<view>/mask.png` | ring6 中该视角的独立 2D 目标证据 |
| 每视角点击 | `06_sags/annotations/<view>/points.json` | 源视角生成 3D seed；独立模式也用于 seed 回填 |
| 参数 | 命令行和任务 manifest | 控制投票阈值、可见性、先验门控、分解等 |

mask、points 和相机视角名必须一致。图片尺寸不一致时，代码会以最近邻缩放 mask 到相机尺寸；这不是相机校准，输入最好从一开始就使用同一分辨率。

### 3.2 算法输出

- `06_sags/inserted_object.ply`：最终只含被选 Gaussian 的前景资产，供 Unity 导入。
- `06_sags/inserted_object.json`：本次运行的参数、源视角、标注路径和投票计数。
- `06_sags/diagnostics/sags_diagnostics.json`：逐视角选择、可见 Gaussian 数量、先验门控和最终投票统计。
- `06_sags/diagnostics/<view>/selected.png`：SAGS 实际使用的 2D mask。
- `06_sags/diagnostics/<view>/candidate_*.png`：legacy 模式的 SAM 候选；独立模式通常只有 `candidate_0.png`，它是输入标注的副本。
- `06_sags/diagnostics/<view>/geometric_center_prior.png`：该视角的中心 Gaussian 几何先验。

## 4. 步骤一：生成 SAGS 六视角

`run_insert_pipeline.py` 先调用 `render_trellis_views.py`，把 `sample.ply` 放入 TRELLIS canonical 坐标系，按默认 yaw 偏移 `0,60,120,180,240,300` 渲染六张图；pitch、FOV、distance 来自统一流程配置。

输出至少包括：

```text
03_sags_views/
├── model/                         # SAGS/3DGS 可加载的模型目录
├── source/images/<view>.png       # 六视角 RGB
├── source/depths/absdepth/*.raw   # float32 深度（用于相机/诊断）
├── source/sparse/0/*.txt          # 相机内外参
└── views.json                     # 视角和渲染参数
```

SAGS 运行时加载 `model/`，读取其中的 Gaussian 和训练相机，并为每台训练相机渲染图像、提取 SAM image feature。外部 `mask.png` 必须对应这些训练相机的 `image_name`；如果名称不一致，算法不会得到正确的跨视角对应关系。

## 5. 步骤二：每个视角生成 2D 标注

对六张 RGB 独立调用 `auto_segment.py`：

```text
RGB + object prompt
        │
        ├─ LangSAM（可用时）
        └─ legacy：GroundingDINO 检测框 → SAM 按框分割
        │
        ├─ 多个检测 mask 取像素并集
        ├─ 从每个 mask 取若干个内部、分散的正点击点
        └─ 保存 mask / cutout / annotated / detections / points / manifest
```

当前默认 `engine=legacy`。GroundingDINO 的检测短语应是简短目标名词，而不是包含锚点的组合描述。`--prompt` 和 `--task-prompt` 的处理如下：

1. ASCII 英文显式 prompt 原样传给检测器。
2. 中文或其他非 ASCII 显式 prompt 先经过确定性别名归一化，例如 `一个蹲着的小孩` → `crouching child`；manifest 同时记录 `original_prompt`、`detector_prompt` 和 `rewrite_method`。
3. legacy 对每个检测框调用 SAM，多个框/多个 prompt 的 mask 取并集。因此如果检测器把锚点也框进来，`annotated.png` 和 `mask.png` 都会包含锚点；SAGS 后面不会重新理解这张 mask 的语义。
4. 每个 mask 默认生成 4 个内部正点击点。点的选择先做距离变换，再用最远点采样拉开间距，尽量覆盖人物的不同部位；这些点只表示正类，不等同于完整 3D 物体。

单视角输出示例：

```text
06_sags/annotations/ring_060/
├── mask.png          # 传给 SAGS 的二值标注
├── cutout.png        # RGB + mask alpha，主要用于检查
├── annotated.png     # 检测框/点击点可视化，不是 3D 结果
├── detections.json   # 检测器框、分数、mask 面积
├── points.json       # {x,y,label} 点击点
└── manifest.json     # prompt、engine、像素数、点数和归一化记录
```

## 6. 步骤三：源视角 2D 点映射到 3D

`run_sags_text.py` 从源视角（默认 `center`）的 `points.json` 读取正点击点。对每个 2D 点：

1. 用源相机的投影矩阵把全部 Gaussian 中心 `xyz` 投影到像素平面。
2. 在点击点周围约 `8×8` 像素邻域收集投影到同一像素的 Gaussian。
3. 只保留相机前方的点，按相机深度选择最近的 Gaussian。
4. 将选出的 3D 中心组成 `prompts_3D`。

这个过程只产生 3D seed，不直接决定最终前景。点击点落在错误物体上时，后续所有投票都可能被带偏，因此源视角标注质量是硬前提。

同时，源视角的完整 `mask.png` 通过 `mask_inverse` 投影回 Gaussian，形成 `center_constraint`。在当前 ring6 默认配置中，它不是最终硬交集（`centerMaskHard=false`），但仍用于生成几何先验；legacy 默认保留中心 mask 硬约束。

## 7. 步骤四：逐视角得到 Gaussian 标签

### 7.1 ring6 独立标注（当前默认）

每个视角直接使用自己的 `mask.png`，不会把源视角的点重新投影后再调用一次 SAM。这样保留了环拍视角的独立证据。

对视角 `v` 的每个 Gaussian 执行：

1. 用该相机投影 Gaussian 中心，检查是否在图像范围内且深度为正。
2. 对每个像素做近似 z-buffer：同一像素只把最前方深度作为前表面。
3. 若 Gaussian 深度不超过该像素前表面深度加相对容差（默认 `0.02`），认为它在该视角可见；否则标为 unknown（`-1`）。
4. 对可见 Gaussian 读取该视角 mask 像素：白色为 `1`，黑色为 `0`。
5. 旧版兼容路径曾将正点击点以半径 `2` 回填到 mask；当前 independent ring6 路径不再修改上游 mask，`force_seed_radius` 仅保留兼容参数。mask 必须作为可审计的上游分割结果保持不变。

非源视角还会进行一次保守几何先验门控：

```text
coverage(v) = count(annotation_mask(v) ∩ geometric_center_prior(v))
              / count(annotation_mask(v))
```

默认要求 `coverage >= 0.25`。不满足时，该视角整体作为 unknown，不贡献正票或负票；这是为了处理目标被遮挡时检测器把锚点当成目标的情况。门控只拒绝视角，不修改其原始 mask。

### 7.2 legacy 中心点传播

legacy 只需要一个源视角 `mask.png + points.json`：

1. 源视角使用输入 mask。
2. 其他视角把 `prompts_3D` 投影为 2D 正点。
3. 对每个视角的 SAM image feature 执行多 mask 点提示分割。
4. 源视角实际直接使用输入的完整 mask（候选 IoU 只用于记录/对照）；其他视角按几何先验 IoU、点覆盖率、SAM 分数和候选面积排序。
5. 选中的候选再反投影为 Gaussian 标签。

legacy 的优点是输入少、兼容原始 SAGS UI；缺点是所有侧视角依赖源视角 seed，容易继承中心视角错误。直接调用 `run_sags_text.py` 默认仍是 legacy。ring6 independent 路径可先用 target/anchor 双 mask 做视角有效性和分离筛选，再固定可靠视角投票，并排除锚点 Gaussian、清理小型 3D 连通组件。当前生产默认不使用旧版单像素 z-buffer；`--use-zbuffer` 仅用于旧行为对照。投票时固定集合中的无效可见性不会被无条件忽略：默认要求至少 75% 的入选视角有有效结果。

## 8. 步骤五：可见性约束和多视角投票

六个视角的标签拼成矩阵：

```text
M ∈ {-1, 0, 1}^{N × V}

N：Gaussian 数量
V：参与的视角数量（ring6 通常为 6）
```

对第 `i` 个 Gaussian：

```text
valid_i    = count(M[i,v] >= 0)
positive_i = count(M[i,v] == 1)
ratio_i    = positive_i / max(valid_i, 1)
```

默认 `vote-mode=majority`，选中条件为：

```text
valid_i    >= min_votes
positive_i >= min_votes
ratio_i    >= threshold
```

当前完整 ring6 流程默认值：

| 参数 | 默认值 | 含义 |
| --- | ---: | --- |
| `min_votes` | 3 | 至少 3 个可见视角判为目标，即 3/6 多数票 |
| `threshold` | 0.5 | 可见视角中目标票比例至少 50% |
| `visibility_depth_tolerance` | 0.02 | z-buffer 相对深度容差 |
| `independent_min_prior_coverage` | 0.25 | 非源视角标注覆盖中心几何先验的最低比例 |
| `force_seed_radius` | 2 | 点击点回填半径 |
| `mask_id` | -1 | legacy 自动选 SAM 候选；固定 0/1/2 可用于对照 |
| `gd_interval` | -1 | 关闭 Gaussian Decomposition；当前默认不做边界参数分解 |

`vote-mode=union` 是诊断/实验选项，只要任一可见视角投正就保留，不是当前生产默认。`center-mask-hard` 打开后，最终结果还要与源视角反投影约束相交；ring6 默认关闭，以免中心视角看不到的背面永远无法恢复。

注意：`run_insert_pipeline.py` 会显式把完整流程默认 `min_votes=3` 传给适配器；直接运行 `run_sags_text.py` 的兼容默认仍是 `2`。查看 `sags_diagnostics.json` 中的 `minVotes`，不要只根据命令名猜测实际参数。

## 9. 步骤六：边界分解和 PLY 写出

投票得到 `final_indices` 后，调用 SAGS 原始 `seg_gaussian`：

1. 从原始 Gaussian 场景重新加载一份 `curr_gaussian`。
2. 若 `gd_interval != -1`，按相机间隔调用 `gaussian_decomp`，用该视角 2D mask、投票后的 3D 前景索引计算 Gaussian 与 mask 边界的比例，并更新边缘 Gaussian 参数。该步骤不新增 Gaussian。
3. `save_gs` 按 `final_indices` 写出位置、法线占位、球谐颜色、透明度、尺度和旋转字段。
4. 复制为用户指定的 `inserted_object.ply`，并写出同名 JSON manifest。

当前默认 `gd_interval=-1`，所以主要操作是从组合 Gaussian 中筛选子集，不做边界分解。

## 10. 诊断文件如何解释

`sags_diagnostics.json` 的关键字段：

- `annotationMode`：`independent` 或 `legacy`。
- `sourceView`、`viewAnnotations`：源视角和各视角输入文件。
- `views[*].annotationSource`：`provided_annotation`、`provided_annotation_rejected_geometry_prior`、`center_mask` 或 `projected_points`。
- `views[*].selectedPixels`：SAGS 实际使用的 mask 面积；不是检测器框面积。
- `views[*].visibleGaussians`：经过投影和 z-buffer 后可参与该视角投票的点数。
- `views[*].positiveGaussians`：该视角投为正的可见点数。
- `vote.gaussianCount`：组合场景总 Gaussian 数。
- `vote.visibleInMinVotes`：至少在 `minVotes` 个视角可见的点数。
- `vote.positiveBeforeCenterConstraint`：满足票数和比例、尚未应用中心硬约束的点数。
- `vote.votedPositive`：最终写入 PLY 的点数。

`annotated.png` 只是在 2D 检测 mask 上画框和点击点；它不是 SAGS 的 3D 分割结果。若该图已经包含锚点，首先应修正 prompt 或输入 mask；不能通过调高 `threshold` 期待 SAGS 自动理解语义。

## 11. 伪代码

```text
sample.ply
  -> canonical ring6 render + 3DGS model
  -> for each view: detector/SAM -> mask_v, points_v
  -> source points_center -> project to 3D prompts
  -> source mask -> center Gaussian constraint
  -> for each view:
       project Gaussian centers
       z-buffer visibility -> {-1, visible}
       read independent mask (or legacy SAM candidate)
       optional prior gate and seed backfill
       produce label column M[:, v]
  -> vote M with min_votes + threshold (+ optional center hard mask)
  -> optional Gaussian boundary decomposition
  -> save selected Gaussian subset as inserted_object.ply
```

## 12. 当前实现边界

SAGS 不负责判断“这张 mask 语义上是不是新物体”。它只把 2D 标注转换成 3D Gaussian 标签并融合。因此算法质量依赖顺序是：

1. 每视角 RGB 与相机对应正确；
2. detector/SAM 的 mask 没有把锚点并入；
3. 源视角正点击点落在新物体上；
4. 六视角有足够的可见重叠；
5. 投票和可见性参数适合当前 Gaussian 密度。

发生错误时，按 `annotations/*/annotated.png` → `annotations/*/mask.png` → `diagnostics/*/selected.png` → `sags_diagnostics.json` → `inserted_object.ply` 的顺序检查，能区分是 2D 标注、3D 投影、投票还是输出阶段的问题。
