# RK3576 黑屏救援实录：两处 DTB 字节错位导致 FIT 校验失败

记录日期：2026-09-28。平台：MLK-AFH03-AFC03，RK3576J，4 GiB 内存，eMMC 启动，Linux 6.1.99。本文是一次实机故障复盘，不是适用于所有 Rockchip 板卡的通用刷机教程。

配套仓库：[rk3576-boot-recovery-case-study](https://github.com/hewenbin825-png/rk3576-boot-recovery-case-study)。

**结果：保留原系统，仅恢复 64 MiB 的 boot 分区，写后整分区校验通过，正常软件重启后重新进入 Linux，桌面服务和 HDMI 输出恢复。** 原始日志与分区镜像留在本地；本仓库只公开脱敏摘录、哈希、分析方法和只读离线工具，不分发固件、系统镜像或厂商资料。

本文依据实机串口日志与二进制比对，由操作者结合 AI 辅助整理。结论以可核验的数据为准，不以工具生成的判断代替实测。

## 1. 故障现象与排查边界

板卡断电重启后，HDMI 显示器提示“无信号”，SSH 无法连接。故障前进行过 PCIe 相关配置操作，但仅凭操作回忆，不能直接认定某个人或某份脚本写坏了系统。

当时优先保留现场：不擦除 eMMC，不直接刷整套系统，不把文件名叫 `boot.img` 的镜像视为一定兼容。调查中使用了随板官方资料、U-Boot 串口、RKDevTool 3.36、Rockusb 驱动 5.13，以及后续临时启动的 Linux。

三个必须分开的判断是：

- HDMI 无信号，不等于显示器、HDMI 线或者显示驱动一定坏了。
- SSH 失败，不等于内核一定没有启动；网络地址和认证问题也会造成同样现象。
- `Bad Data Hash`，不等于内核载荷本体一定被破坏；读取位置错误同样会导致哈希不匹配。

## 2. 串口把故障定位在 Linux 启动之前

本板使用 RK 调试串口接口 J3，现场参数是 **1500000 baud、8N1、无流控**；J4 是 USB OTG，不能把 FPGA 的 JTAG/串口连接当作 RK 的启动日志口。接口标号和电气连接必须以自己的板卡版本与官方硬件手册为准。

抓取上电日志后，确认 DDR、SPL、U-Boot 已运行，失败发生在 FIT 内核校验阶段：

```text
Verifying Hash Integrity ... sha256 Bad hash: cc46e76cbc6e83fc59cae4ce6040af8e5b7713f263bf08a25c9eb575e3f0763c
Bad hash value for 'hash' hash node in 'kernel' image node
Bad Data Hash
ERROR: can't get kernel image!
```

这解释了为什么当时没有 Linux 桌面与 SSH 服务，但还没有回答“为什么校验失败”。下面继续对镜像本身取证。

> RESET 是复位，不是恢复出厂设置。RAM 中准备好的救援数据会在复位或断电后丢失；准备临时启动时，不要随意按键或断电。

## 3. 根因：不是一个开关，而是三层二进制约束

### 3.1 在故障分区里实际发现了什么

该 boot 镜像包含 FIT、第一份 DTB、内核，以及含第二份 DTB 的 resource。两份设备树的 `/pcie@2a210000/status` 均出现了同一种异常。

| 项目 | 原始内容 | 故障内容 |
| --- | --- | --- |
| 属性长度字段，大端整数 | 9 | 5 |
| 被替换的值字节 | `disabled\0`，9 字节 | `okay\0\0\0\0`，8 字节 |
| 对外围布局的影响 | 与原 FIT 一致 | 每处替换少了 1 字节 |
| FIT 原有位置与哈希 | 与载荷匹配 | 没有随变化正确更新 |

这里说的是**字节比对得到的变化效果**，不是声称已经找到实际执行的源代码。

Devicetree 规范要求结构块 token 按 4 字节边界排列，属性值后按需要补零；属性长度不包含为下一个 token 增加的对齐填充。不能只修改字符串和长度字段，再让原有结构自然“凑合着解析”。参见 [Devicetree 结构块与属性格式](https://devicetree-specification.readthedocs.io/en/stable/flattened-format.html#structure-block)。

本例原值 9 字节，后面原本有 3 字节填充，合计 12 字节。错误替换把 9 字节值换成 8 字节，却留下了原有 3 字节填充，实际占用变成 11 字节；与此同时，解析器按新长度 5 计算，下一个 token 应在 8 字节之后。两者不一致，第一份 DTB 在 boot 偏移 `0x1507c` 解析失败。

FIT 的外部数据位置、大小与 image hash 又是另一层约束。即使把设备树自身修到能解析，只要载荷变化后没有正确重建 FIT，仍可能在下一次启动时校验失败。参见 [FIT 规范](https://fitspec.osfw.foundation/)。

### 3.2 关键偏移：内核提前了 1 字节

以下均为本次镜像相对 boot 分区起点的偏移，不能套用到其他版本：

| 项目 | 原布局或 FIT 声明 | 故障镜像中的位置 |
| --- | --- | --- |
| 第一份 DTB 起点 | `0x800` | `0x800` |
| 第一处 status 值 | `0x15074` | `0x15074` |
| 内核起点 | `0x47800` | `0x477ff` |
| resource 起点 | `0x2637200` | `0x26371ff` |
| 第二处 status 值 | `0x264c274` | `0x264c273` |

两处替换之后，后续数据累计提前 2 字节。物理分区仍然是 64 MiB，不能用分区大小没有变化来否定内部移位。

```text
两处错误的 DTB 属性改写
  → DTB 结构失配，并导致后续载荷移位
  → U-Boot 仍按原 FIT 偏移读取内核
  → SHA256 不匹配，Linux 没有启动
  → HDMI 无桌面，SSH 服务不可用
```

### 3.3 为什么能确认内核本体没有损坏

先在 U-Boot 核实分区表和可用 RAM，把真实 boot 通过 MMC 读入 RAM。现场使用的起始地址是 `0x60000000`；下面是**该现场的只读校验记录**，不是通用地址建议：

```text
crypto_sum sha256 60047800 25efa00
# 按 FIT 原偏移读取：cc46e76c...f0763c，失败

crypto_sum sha256 600477ff 25efa00
# 按实际起点读取：dc871934...ad592，与原 FIT 的 kernel SHA256 完全一致
```

相同长度的内核，只把起点纠正 1 字节，就与原 FIT 中保存的哈希完全一致。因此本例的 `Bad Data Hash` 是偏移错误造成的，不是内核载荷被改坏的证据。

此外，撤销第一份 DTB 的错误改写后，其 290780 字节内容也与原 FIT 的 DTB 哈希完全一致。

### 3.4 更强的证据：整个 64 MiB 能精确重现

临时进入 Linux 后，找到了故障前保存的完整 boot 备份。对这份原始备份只模拟上述两处改写，并保留物理分区末尾原有的 2 个字节，结果与故障分区 **整个 64 MiB 逐字节相同**：

```text
exact_full_partition_match_after_two_bad_edits: true
```

这证明这两处变换能完整解释“原始备份 → 故障 boot”的差异，而不是只解释日志里的一个报错。

但这个结论仍有边界：它不等于已经识别具体操作者、实际执行脚本或执行时间；也不代表其他分区做过完整一致性检查。对交接材料的审核发现了不安全的 DTB/FIT 修改思路，但现存脚本与实测字节变化不完全相同，不能据此指认责任。

## 4. 排查中的第二个坑：导出成功不等于备份有效

RKDevTool 曾导出一个 64 MiB 文件，两次导出的 SHA256 也相同。最初据此把它当作完整备份；交叉核验后发现这个判断不成立，并立即将文件标注为**禁止回写**。

该文件只有前 16 MiB 是真实 boot 内容，后 48 MiB 全是 `0xCC`。串口 MMC 直接读取相同存储位置却有正常数据，因此不能把这段 `0xCC` 认定为 eMMC 内容损坏。

Rockchip 官方 U-Boot 源码中的该 rockusb 读取路径，在请求结束地址超过限制时，将缓冲区填成 `0xCC`，同时返回所请求的块数；限制常量为 `(32 * 2048)` 个 512 字节扇区，即绝对地址 32 MiB。本次 boot 起始于绝对地址 16 MiB，因此该导出流程越过边界后，余下 48 MiB 不是有效备份。参见固定源码版本 [rockusb.c](https://github.com/rockchip-linux/u-boot/blob/1c535d65b8509f388d09e49fb6961f49fda35a1d/cmd/rockusb.c) 与 [rockusb.h](https://github.com/rockchip-linux/u-boot/blob/1c535d65b8509f388d09e49fb6961f49fda35a1d/include/rockusb.h)。

**适用边界：这是本次 U-Boot rockusb 路径的实测结果，与引用源码逻辑一致。不是说所有 RKDevTool、Loader、Maskrom、U-Boot 或 Rockchip 设备都存在同一限制。跨边界读取的具体表现还取决于分块方式。**

备份至少应该交叉确认：

1. 分区起点、长度、扇区单位是否正确。
2. 文件长度是否符合预期。
3. 内部载荷能否解析、各段哈希是否匹配。
4. 是否出现长段异常填充值。
5. 与设备端直接读取的整分区哈希是否一致。

两次错误通道给出同一份填充值，也会得到完全相同的 SHA256。重复哈希只能证明两份导出相同，不能独立证明它们真实。

## 5. 实际救援路线：先启动，再备份，最后只恢复 boot

### 5.1 只在 RAM 中准备经过原哈希验证的启动数据

现场先核实 `bdinfo`、`sysmem_dump`、启动环境和分区布局，选择互不覆盖的 RAM 区域。通过非重叠复制与少量 RAM 字节恢复，重新得到原 DTB；内核从实际起点复制到原加载地址。

原始 boot 的 RAM 副本没有被覆盖。准备完成后分别计算 DTB 与内核 SHA256，只有两者都与原 FIT 的预期值一致，才允许临时启动。

本例最后执行的是：

```text
booti 40400000 - 48300000
```

这里的两个地址来自本次已经核实的内存布局；`-` 表示不传 initrd。启动参数、根分区标识和串口配置同样必须符合实际系统。命令含义参见 [U-Boot 官方 booti 文档](https://docs.u-boot.org/en/latest/usage/cmd/booti.html)。

没有执行 `saveenv`，也没有为了接受损坏数据而关闭校验或篡改预期哈希。这里使用的是已经通过原始哈希验证的原内核和原 DTB。

> RAM 中修改启动数据不等于整个过程“完全只读”。Linux 正常启动会写文件系统日志和系统日志。只是这一阶段没有回写 boot、没有擦除存储。

临时启动成功后，串口进入 root shell，桌面与 SSH 服务启动，现场确认 HDMI 出现画面。

### 5.2 在 Linux 中保存真正完整的故障备份

进入系统后，先用 `lsblk`、分区标签和 `/sys/class/block/.../start`、`size` 核实设备，不直接假定每块板子的 boot 都是同一个分区编号。

本例确认 boot 为 `/dev/mmcblk0p3`，大小 67108864 字节。通过 Linux 直接读取保存完整故障分区，再核对其 SHA256 与先前 U-Boot MMC 直读计算的值一致。

Wi-Fi 传输不稳定时，没有继续依赖可疑 USB 导出，而是把已验证文件压缩，通过带序号的串口数据帧传到电脑。接收端同时核对压缩数据哈希、解压长度和完整镜像 SHA256。传输完成才把它标为有效完整备份。

只读核查命令示例：

```bash
lsblk -o NAME,SIZE,FSTYPE,PARTLABEL,MOUNTPOINTS
sudo blockdev --getsize64 /dev/mmcblk0p3
sudo sha256sum /dev/mmcblk0p3
python3 tools/fit_audit.py /path/to/copied-boot-backup.bin
```

最后一条只检查电脑或工作目录里的普通文件，不会连接板卡或写分区；它是本案例的轻量审计工具，不代替完整 libfdt 检查、签名验证或厂商刷机工具。

工具仅支持本例使用的 FDT v17 头部和列出的 SHA 算法。`all_payload_hashes_verified` 为 true 才代表所有枚举到的 FIT 载荷都具有受支持且匹配的 hash；缺少 hash、算法不支持或不匹配时不会给出成功退出状态。

### 5.3 选择同一系统的原始备份，不盲刷不同版本 boot.img

在板上找到 9 月 24 日保存的原始 boot 备份后，确认了四件事：

- 完整文件大小为 64 MiB。
- DTB、kernel、resource 三项 FIT 哈希均通过。
- 其中内核、DTB 与临时启动成功的那一组原始载荷一致。
- 与故障分区之间的全部差异，能被两处错误修改精确解释。

电脑上也由真实故障备份逆向还原原镜像，其完整哈希与板上原始备份一致。永久恢复实际采用的是板上已有的原始备份。

随板另有一个名字同为 `boot.img` 的镜像，但内核哈希与当前系统不同，所以没有直接覆盖它。文件名相同不构成版本兼容证据。

### 5.4 经确认后，仅恢复 boot 并全量读回

获得操作者明确同意后，恢复程序再次核对：设备为块设备、分区名为 boot、起始扇区与长度正确、分区未挂载、原备份和故障备份哈希正确、当前分区仍与故障备份一致。

然后才把原始 64 MiB 写入该 boot 分区。写入后 `fsync`，刷新块设备缓存，重新读取全部 64 MiB 验证哈希；**读回一致之前不重启**。

没有格式化或重装 rootfs、userdata，也没有刷 FPGA、U-Boot、GPT 或 recovery。正常开机日志以及故障备份文件写入，不应与“重刷用户分区”混为一谈。

本仓库不提供可不经核实就运行的一键分区写入脚本。若没有匹配的原始备份，不能仅根据本文中的地址或哈希硬套恢复；应保留现场，获取板型、启动链和系统版本匹配的恢复材料。

### 5.5 正常软件重启验证

恢复后的 U-Boot 自动从 eMMC 读取 FIT，不再依赖上一轮临时准备的 RAM：

```text
Loading kernel from FIT Image ...
Verifying Hash Integrity ... sha256+ OK
Loading fdt from FIT Image ...
Verifying Hash Integrity ... sha256+ OK
Starting kernel ...
```

随后在新启动的 Linux 实例里，核实运行时间重新计数、串口可执行命令、系统状态为 `running`、failed units 为 0、SSH 与 LightDM 为 active、HDMI 为 connected/enabled。重启后再次计算 boot 整分区哈希，仍与原备份一致。

这证明了正常软件重启恢复。本文**不把它扩大为断电冷启动、所有用户文件完整性或 FPGA PCIe 业务的全面验收**。

## 6. 哈希证据与时间线

以下哈希用于复核这一具体案例，不是其他板卡的通用正确值。哈希一致证明内容对应；单独一个镜像内部哈希，并不能证明来源可信或通过安全启动签名验证。

```text
故障 boot，真实 64 MiB：
641383a1418518d6a9eeac4547deca37ce30df7879096c1a54571c54df923bc8

原始备份 / 还原镜像 / 写后读回 / 重启后 boot：
faebf16428deb61867f57f17f79b7d27ba0faf9503853284a697aa83e882abec

原 DTB：
a30ce31326022e507ba8a4c751a11afc70770a471a6f2d8009641b69815fa3dd

原内核：
dc87193455529b79f1487ad02790fd822f50c9cf5c3dcab7dcd98a1cf28ad592

原 resource：
65be9c66134d672d3d1de6f7630cf5b0e3bc98c17bb0a53a3c524e7d93a8a0c4

无效 USB 导出，禁止回写：
cfd5a0e72fb8579c5002e015b0af81cfcf63496d4824315df5e811b1260e67b5
```

2026-09-28，以下时间统一为 UTC+8；串口内核计时不能直接当作墙钟时间。

| 时间 | 实测进展 |
| --- | --- |
| 20:06 | 上电串口定位到 FIT kernel 的 Bad Data Hash |
| 20:49 | MMC 直读排除“后段全是 0xCC，因此 eMMC 数据损坏”的误判 |
| 20:51 | 内核读取起点减 1 字节后，与原 FIT SHA256 一致 |
| 20:54 | 两份缩短 DTB 哈希相同，取得真实整个 boot 的板上哈希 |
| 21:03 | 原 DTB 与内核在 RAM 中校验通过，临时启动成功 |
| 21:09—21:10 | 原始完整备份三项 FIT 校验通过；两处修改精确解释全分区差异 |
| 21:14 | 真正完整的故障备份传到电脑，完整哈希验证通过 |
| 21:15 | 获准仅恢复 boot，写后全量读回一致，执行正常重启 |
| 21:16—21:18 | 新系统实例、HDMI 和服务正常；网络连接问题单独记录 |

## 7. 为什么系统好了，SSH 仍可能暂时连不上

永久恢复后，SSH 服务本身为 active，但无线连接出现关联超时，最终为 disconnected，没有获得可用网络地址。有线接口也没有链路。

NetworkManager 日志出现 `association took too long` 与 `no-secrets`。日志里也能看到曾尝试使用隐藏的 PSK 值，因此不能仅凭 `no-secrets` 就断言密码一定错误。

这属于需要继续核查的网络问题，不能再次解释为“Linux 进不去”。可在桌面确认 Wi-Fi 连接，或接有线网络，再分别检查地址、路由、22 端口与 SSH 认证。此次没有为了恢复 boot 而更改网络密码或配置。

## 8. 防止复发：启用 PCIe 需要正确重建镜像

此次把 `/pcie@2a210000/status` 恢复为原始 `disabled`，目的是先恢复系统启动，**不是已经完成 PCIe 功能开通**。

以后修改应按实际 BSP 和构建流程进行：修改设备树源码或通过可靠的设备树接口生成合法 DTB，确认 FDT 结构、布局与依赖配置，再正确重建 FIT 的位置、长度与哈希；若平台启用了签名校验，还必须使用有授权的签名流程。

不要采用下面这些推断：

- “字符串写进去成功了，所以设备树正确。”
- “文件长度没变，所以启动镜像没问题。”
- “`dd` 或工具提示成功，所以重启一定正常。”
- “把 hash 改成当前坏数据的值，或者关闭校验，就算修好。”
- “重复下载文件哈希相同，所以备份一定有效。”

一个更稳妥的交付流程是：修改前保存有效完整备份 → 在副本上生成候选镜像 → 验证 DTB 与 FIT → 能临时启动时优先测试 → 受控写入单个目标分区 → 全量读回 → 正常重启及后续冷启动、PCIe 业务分别验收。

## 9. 仓库内容、工具与公开范围

- `README.md`：完整复盘。
- `evidence/serial-excerpts.txt`：从现场日志提取的关键行；明确保留来源文件名，不冒充连续完整日志。
- `evidence/verified-manifest.json`：载荷位置、长度和 SHA256，以及结论边界。
- `tools/fit_audit.py`：只读检查普通镜像文件的 FIT 载荷哈希和嵌入 DTB 状态。
- `tools/compare_case.py`：仅在内存模拟两处已知错误替换，比较本地原备份与故障副本；不生成可刷写文件。
- `tests/`：不依赖厂商固件的合成数据测试。

```bash
python3 -m unittest discover -s tests -v
python3 tools/fit_audit.py /path/to/original-boot.bin
python3 tools/compare_case.py /path/to/original-boot.bin /path/to/damaged-boot.bin
```

不上传原始完整串口日志、真实 IP/MAC/SSID、账户凭据、SSH 指纹、个人目录、固件镜像或厂商原始资料。公开源码仅用于离线检查，不连接串口或网络，不写设备。

## 10. 官方资料

- [Devicetree Specification：Flattened Devicetree Format](https://devicetree-specification.readthedocs.io/en/stable/flattened-format.html)
- [U-Boot FIT 格式入口](https://docs.u-boot.org/en/latest/usage/fit/source_file_format.html) 与其指向的 [FIT Specification](https://fitspec.osfw.foundation/)
- [U-Boot booti 命令](https://docs.u-boot.org/en/latest/usage/cmd/booti.html)
- [U-Boot cp 命令](https://docs.u-boot.org/en/latest/usage/cmd/cp.html)
- [Rockchip U-Boot：rockusb.c，固定源码提交](https://github.com/rockchip-linux/u-boot/blob/1c535d65b8509f388d09e49fb6961f49fda35a1d/cmd/rockusb.c)
- [Rockchip U-Boot：rockusb.h，固定源码提交](https://github.com/rockchip-linux/u-boot/blob/1c535d65b8509f388d09e49fb6961f49fda35a1d/include/rockusb.h)
- 现场另核对了随板 AFH03/AFC03 硬件资料与 RKDevTool 使用手册；本仓库不转载这些文件。接口、电平、分区和固件适配应以自己的合法资料与板卡版本为准。

源码参考用于解释已观测到的行为，不表示已经取得或逐行确认该板定制 U-Boot 的完整构建源码。
