# 青岚复电余振复核交接单

`README.md` 描述目前可用的脉冲复检。从指定修订发复电件，供夜班在关站后用实际差值核定余振。两种复检分别保留自己的定稿和读取入口。

## 站端发行与中心实收

新增 `exchange.issue.issue_restart(station, issued, revision_id, base, directions)`。前三个参数沿用 `issue_packet`，返回同样提供 `.wire` 和 `.credential` 的发行件。`base` 是初态向量，`directions` 是零个或多个同维向量。接受列表或元组，系数采用 README 的有理数口径，向量长度等于本版 `A` 的行数，空状态使用空向量。允许初态是基点加方向的任意有理线性组合。复电第 `k` 拍的输出是 `C A^k x`，第零拍为 `C x`。`B`、`D` 继续用于旧脉冲复检。发行时校验 `A` 的全部特征根，仅允许 0、1/2、1、2，含重根。

同一个确切修订可同时拥有 V1 件和一份 V2 件，两件的发行身份必须可区分。重发按有理值逐项比较基点和原方向表，保留方向次序。原许可返原字节和原凭据，改许可需另冻结修订。勘误沿用 `revise` 的父版约定。

`receiving.intake.accept(receipts, wire, credential)` 接纳两版，仍返回实收号。V2 沿用 README 所述的规范 UTF-8 JSON 外层字段及摘要计算，`version` 为 `2`，摘要覆盖正文。中心验明版本、正文及独立原发行凭据后保存原来件，原样复投领首纳号。同一发行身份的冲突来件拒收。正文只能携带从许可和模型得到的公开响应证据，原初态坐标、私有状态名、原模型矩阵及向量、拓扑和原测均留站。交付时说明 V2 正文如何解读，使复核人能在对应实收件上检查报告中的见证。正文的响应表示由实现选择。

## 申请与复领

在 `review.request` 提供 `RestartRequest(batch, primary_receipt, reference_receipt, observations)` 和 `compare_restart(receipts, reports, request)`。`observations` 是非空列表或元组，每项为 `(拍号, 差值)`，按主件减对照件记。拍号为互异的非负整数，可乱序，差值沿用有理数输入约定。两号均来自申请批次且须为 V2，可指定同一实收号。每路可独立选一个本件允许的初态，各自用该初态解释整表。旧 `compare` 仍只办理 V1。

`compare_restart` 返回具有 `report_id`、`batch`、`observations`、`status`、`finite_witness`、`divergent_witness`、`sources` 的报告对象。`observations` 是按拍号升序的 `(int, Fraction)` 对元组。`sources` 采用原报告的两项 `ReportSource` 形状，按主件、对照件排序。`status` 取下列值。

| 值 | 意义 |
| --- | --- |
| `inconsistent` | 整份观测无相容解释，仍签此结论 |
| `all_finite` | 有相容解释，全部差分平方可和 |
| `all_divergent` | 有相容解释，全部差分平方和发散 |
| `mixed` | 同一申请下，有限与发散的解释都存在 |

`mixed` 的两个见证各为 `(主件前四拍, 对照件前四拍)`，每路按拍号 0、1、2、3 排列为四元 `Fraction` 元组，零值照记。每对须可在所选实收件中解释整份观测，且其完整差分具有对应的尾部资格。合规见证任选。其余状态的两项见证均为 `None`。

报告编号由对应的读取入口解释。`reports.history.read_restart_report(reports, report_id)` 只读报告库中已签存的值，返回与签发时相等的对象。签发原子保存观测、判定、见证及两份出处，提交失败可以重办。非法参数、冲突、坏件和错版报 `ValueError`，未知修订、实收号或复电报告号报 `KeyError`，存储失败保留 SQLite 异常。拒办或写入失败无对应新增记录。

## 交班样例与运行

`samples/restart.json` 中 `revisions` 按登记顺序列出源修订，`parent` 如有则指前一条登记的样例键。所有源模型使用零 `B`、零 `D` 和 V1 默认零初态。`base` 与 `directions` 仅用于复电件。`reviews` 中 `pair` 按主件、对照件列修订键，`status` 是复电定稿预期。见证示例中的字符串是精确有理数。

`linked` 的两种模式共用一个幅度，首拍 2 迫使增长项存在。`decaying` 的同一首拍只对应衰减。把 `linked` 的两幅度改成独立许可后用勘误件 `independent`，同一读数下有限、发散均可。`growth-main` 和 `growth-ref` 的首拍零差值迫使整尾相消，加上一拍差值 1 就无解。`late-growing` 和 `late-pulse` 各自的前四拍都是 0、0、0、1，第五拍分别是 8 和 0。四拍零差仍可与发散相容，只有零根链的延迟脉冲本身是有限的。

`python3 -B restart_fixture.py` 可重跑这批输入的正常 V1 对照。沿用 `.pointwise-runtime.json` 中的 `verify` 与 `demo` 作为运行入口。完成时 `verify` 应继续跑原回归并核验复电结论，`demo` 应展示关站签发和关闭实收库后重开报告库复领。