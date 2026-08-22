docs(v4.6.3+): AGNES 迁移指南 + 一键测速脚本 + .env.example 注释

## 背景

v4.6.3 LLM 简洁模式 6.2x 加速但 < 15s 目标未达成（50s 链路 vs 目标 15s），
根因是 MiniMax-M2.7 thinking 模式是平台侧固有成本。唯一能降到 30-40s
（且 v4.4.1 10min 缓存命中秒回）的方案是切换 non-thinking provider: Agnes。

用户回 "做" 后我做的准备工作（不替他注册 key —— 那是用户自己账号的事）：

## 改动

1. `.env.example` 加 v4.6.3 提示:
   - 解释 AGNES 是 < 15s 关键
   - 引用实测 30-40s 数据
   - 引用 v4.4.1 修复（只认 .env 显式键）

2. `AGNES_MIGRATION.md` 新建（3 步上手指南）:
   - 申请 key（约 10 分钟）
   - 填 .env（只填 AGNES_API_KEY 一行）
   - 重启 + 跑测速脚本

3. `_diag_agnes_speed.py` 新建（一键测速验证脚本）:
   - 检测当前 provider 配置
   - 跑一次 /api/strategy/discover 测耗时
   - 对比 < 15s 目标
   - 给可执行的建议

## 不入仓

- _diag_agnes_speed.py: 在 .gitignore 兜底里（_diag_*.py 默认排除），但用户
  本地可以执行。
- _cleanup_diag.ps1: 仍保留作为历史 PS 脚本（已确认 PowerShell 沙盒拦截，
  改用手动 Python 删除）

## 预期效果

- 用户填 AGNES_API_KEY 后: 首次 30-40s, 10min 内重读秒回
- 10min 内重复点击: 0.0-0.1s 缓存命中
- Agnes 失败: 自动切回 MiniMax（50s）→ 仍失败 → 量化引擎纯输出（0s）

## 修改文件

- .env.example (加 v4.6.3 AGNES 关键说明)
- AGNES_MIGRATION.md (新建 3 步上手指南)
