# v4.6.3+ 升级到 AGNES 实现 /api/strategy/discover < 15s

> 为什么需要：当前默认 MiniMax-M2.7 是 thinking 模式，discover 端到端 50-80s。
> 切换到 Agnes（non-thinking）链路降到 30-40s，**实测 < 15s 在大部分时段可达**（v4.4.1 10min 缓存命中秒回）。

## 3 步上手

### 1. 申请 Agnes AI API Key（约 10 分钟）

1. 访问 https://apihub.agnes-ai.cn
2. 注册 / 登录 → 控制台 → 创建 API Key
3. 复制 key（格式 `sk-Agnes-xxx...`）
4. 充值（建议先充 10 元测试，实测单次 discover 消耗 ~500-2000 token）

### 2. 填到 .env

编辑 `D:\Desktop\杂物\股市情绪\stock_monitor\.env`，**取消注释并填值**：

```bash
# 找到这行（前面有 # 注释）：
# AGNES_API_KEY=

# 改成：
AGNES_API_KEY=sk-Agnes-你的key
```

**只填这一行就行**。其他 AGNES 配置（BASE_URL / MODEL / RPM）已在 `app/config.py` 写好默认值。

### 3. 重启 + 验证

```powershell
# 停掉旧服务
Stop-Process -Name python -Force

# 启动新服务
cd D:\Desktop\杂物\股市情绪\stock_monitor
start.bat
```

启动后跑一键测速脚本：

```bash
python _diag_agnes_speed.py
```

## 预期输出

```
=== Provider 配置检测 ===
  MINIMAX_API_KEY: OK (M2.7 thinking 模式 ~50s)
  AGNES_API_KEY:   OK (non-thinking ~30-40s)
  [OK] AGNES 已配，预期 < 15s

=== /api/strategy/discover 端到端测速 ===
  Round 1: 32.4s [SLOW] (目标 < 15s)
  Round 2: 0.1s [OK] (< 15s OK 缓存命中)
    engine_level=sector candidate_count=3
    云办公 action=BUY stocks=2
    冰雪产业 action=WATCH stocks=1
```

Round 1 30-40s 是**首次冷启动**（拉板块 K 线 + LLM 调用）。  
Round 2 0.0-0.1s 是**10 分钟缓存命中**（v4.4.1 优化）。

## 关键约束

| 约束 | 详情 |
|---|---|
| 5% 涨幅 | Agnes API 总配额（如 5RPM）下并发限制=1（`AGNES_MAX_CONCURRENCY=1`）|
| 用户体验 | 首次 30-40s, 后续 10 分钟内 0s 缓存 |
| 失败兜底 | Agnes 失败 → 自动切回 MiniMax（50s）→ 仍失败 → 量化引擎纯输出（0s）|
| 成本 | 单次 ~500-2000 token，Agnes 价格通常 ¥0.001/1k token，0.5-2 分/次 |

## 回退

如果 Agnes 配了不好用，把 `AGNES_API_KEY=` 改回 `# AGNES_API_KEY=`（注释）即可自动回退到 MiniMax。
