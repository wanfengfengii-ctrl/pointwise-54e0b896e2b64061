# 青岚仪表校验室双链复检台账

校验室对同一次标准单位脉冲下的两条采集链做离线复检。白班从试验台接收批次、通道、精确离散模型和测行出处。交班员交换固定件。夜班签发差分能量，质控班据定稿安排次班补测。

## 运行

需要 Python 3.11，使用标准库和 SQLite。在仓库根目录运行完整正常回归和工作日演示。

```text
python3 -B check_project.py
python3 -B demo.py
```

演示读取 `samples/workday.json`，在独立临时目录建立三本台账。演示中的 `POINTWISE_SCENARIO` 行包含核对过的输入、实际结果和补测结果。

## 一个工作日

1. 白班为同批的主通道和对照通道分别登记零初态模型，取得两个修订号。
2. 交班员按这两个修订号发件，原状态编号、拓扑标注和测行留在站库。
3. 中心分别核对固定件和原发行凭据，保存实收副本。同件有效复投沿用原实收号。
4. 夜班关闭站库后，以两个指定实收号和门槛 1 提交原版复检。两件从第一拍起的共同尾部为 1、2、4 等，尾部全部相消。
5. 定稿保存第零拍差值产生的精确能量 1。等号放行，报告可按号领取。
6. 白班以原对照修订为父版登记勘误，发行并实收尾部为 2、4、8 等的新件。
7. 夜班把原主件与新对照件配对，签出发散和补测。重开后两份报告分别保留各自的原出处。
8. 质控把次班和两份报告号交给补测清单入口，只领取勘误版补测项。

## Python 接口

- `StationRepository(path, station_id)` 打开站库。`station_id` 是台账中唯一分配给该站库的名称，重开时沿用。`revise(station, batch, channel, model, source, parent_id=None)` 返回修订号。`read_revision(station, revision_id)` 用于站内回查。`StationSource` 可记录 `state_ids`、`topology` 和文本 `raw_rows`。
- `StationModel(A, B, C, D, initial_state=None)` 接受 0 到 4 个状态。A 为方阵，B 和 C 是同长度向量，D 必填。省略初态表示全零，显式初态也必须全零。系数和门槛接受整数、`Fraction` 和十进制整数字符串或分子/正分母字符串。布尔值、浮点数、空值及非法维度被拒绝。
- `IssuedCatalog(station.connection)` 使用本站连接。`issue_packet(station, issued, revision_id)` 返回带有 `.wire` 和 `.credential` 的发行件。`.credential` 是从可信发行目录交付的凭据，包含发行身份、来源和摘要。交班员独立取得它，再调用 `accept(receipts, wire, credential)`。
- `ReceiptCatalog(path)` 管理实收库。`accept` 返回实收号。`receipts.lookup(receipt_id)` 交回原始字节和已核对出处。冲突复投须拒收。
- `ReportRepository(path)` 管理报告库。`compare(receipts, reports, ReviewRequest(batch, primary_receipt, reference_receipt, threshold))` 用指定顺序计算并返回 `SignedReport`。两件都须归属请求批次。`sources` 顺序对应主件和对照件，每项含实收号、发行号、摘要、站名及修订出处。
- `read_report(reports, report_id)` 返回原签稿。`followup(reports, shift, report_ids)` 接受 `day` 或 `night` 及报告号集合，按报告号升序返回补测项。相同报告号只列一次。任一报告缺失会使整次请求失败。

库对象提供 `close()`。初始化只建立缺失结构，重开保留记录。修订采用追加方式，勘误不更新父版。通过公开接口收到的校验错误为 `ValueError`，未知修订、实收和报告为 `KeyError`，数据库操作失败保留 SQLite 异常。复检失败时无新定稿或补测项。

## 固定件与计算口径

V1 为规范 UTF-8 JSON 字节。外层含 `version`、`issue_id`、`origin`、`body`、`digest`。键按字典序排列，分隔符无空格。摘要为去掉 `digest` 的规范字节之 SHA-256。可信原发行凭据与来件的摘要、发行号和来源须一致。

`body` 中 `direct` 为第零拍，`numerator` 和 `denominator` 由低到高按 q 的次数存储尾序列有理式，常数项对应第一拍。分母常数为 1，零尾为 `["0"]/["1"]`。有理数用约化后的整数字符串或分子/分母字符串。第 k≥1 拍为 C A^(k−1) B。中心同拍相减并约分，按剩余动态判定全拍平方和。平方和按归一响应幅值的平方计，门槛采用相同口径。

有限能量是 `Fraction`，发散在报告中使用 `energy=None` 和 `energy_status='divergent'`。有限值小于或等于门槛放行。复检签稿保全原门槛和顺序，领取和补测清单只读取已签事实。回归还核对折半尾能量 4/3、有限零、重复单位模部分相消、延迟尾、同件复投、混批、提交回滚和重开。

## 复电自由响应（V2）

同一修订可另发一份 V2 复电件，供关站后按自由响应复核；V1 脉冲入口、原件、定稿和补测保持不变。

- `exchange.issue.issue_restart(station, issued, revision_id, base, directions)` 冻结该修订的有理初态基点和方向表后签发 V2 件，返回带 `.wire`/`.credential` 的发行件，并记下 `base`/`directions`。允许初态为 `base + Σ u_i·direction_i`；`B`、`D` 不参与，响应自第零拍按 `C A^k x` 计。发行时校验 `A` 的特征根只落于 0、1/2、1、2，重根照计。
- 同一修订的 V1 与 V2 发行号不同（V2 以 `:v2` 结尾），可并存；`receiving.intake.accept` 接纳两版。原许可重发返回原字节与原凭据，改基点或方向须按 `revise` 的父版约定另冻修订，冲突拒收。
- V2 外层仍为规范 UTF-8 JSON（`version=2`、`issue_id`、`origin`、`body`、`digest`），摘要覆盖 `body`。`body` 只携带由许可和模型导出的公开响应证据：`parameters`、`base` 与 `directions`，每项与 V1 同构（`direct` 为第零拍，`numerator`/`denominator` 自第一拍起）。初态坐标、私有状态名、模型矩阵与向量、拓扑和原测均留在站库。
- `review.request.RestartRequest(batch, primary_receipt, reference_receipt, observations)` 与 `compare_restart(receipts, reports, request)` 办理复电复核。`observations` 为非空的 `(拍号, 差值)` 列表或元组，按主件减对照件记；拍号互异非负、可乱序，差值沿用有理数约定。两号须同批且为 V2，可同一号。
- 每一路各选一个本件允许的初态解释整表，按整份差分的平方可和性判定。`compare_restart` 返回 `report_id`、`batch`、升序 `observations`、`status`、`finite_witness`、`divergent_witness`、`sources`；`status` 为 `inconsistent`、`all_finite`、`all_divergent` 或 `mixed`。`mixed` 的两见证各为 `(主件前四拍, 对照件前四拍)` 四元 `Fraction` 元组，零照记；其余状态两项为 `None`。
- `reports.history.read_restart_report(reports, report_id)` 只读已签存值，提交失败可重办，拒办或写入失败无新增。未知号报 `KeyError`，非法或冲突来件报 `ValueError`。

`samples/restart.json` 的 `reviews` 给出七组预期状态，`restart_fixture.py` 另跑这批输入的 V2 对照；`demo.py` 演示关站签发、关闭实收库后重开报告库复领。
