# ini_builtin —— 内置规则快照（**不在仓库里**）

这个目录原本放两份**游戏原版规则文件**：

```
rulesmd.ini   (728 KB)
artmd.ini     (332 KB)
```

它们由 EA / Westwood 随游戏发布，**有版权，所以不放进开源仓库**（`.gitignore` 已忽略）。

## 工具没这两份文件会怎样

不会崩。`core/game_ini.py` 的查找顺序是：

1. `config.json` 里手动指定的路径
2. **存档所在目录**（及其上一级）的 `rulesmd.ini` / `artmd.ini`
3. `core/ini_builtin/` 里的内置快照

前两步找不到才会用第 3 步。缺了只是**型号中文名、建筑尺寸这类"查表信息"显示为空**，
存档读写与改档功能不受影响。

## 怎么补上（任选其一）

- **最省事**：把你的 `rulesmd.ini`、`artmd.ini`（在你游戏安装目录）复制到本目录，文件名保持原样。
- 或者写一个 `config.json` 放在项目根目录（打包后放在 exe 旁边）：

```json
{ "game_dir": "E:/YURI" }
```

> 用 exe 的人一般不用管：Release 包里的 exe 会优先读存档旁边的规则文件；
> 想让它有完整型号名，就把游戏目录填进 `config.json`。
