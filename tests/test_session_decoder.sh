#!/usr/bin/env bash
# 校验 tools/decode-session.mjs：会话日志是**拼接 zstd 帧**，必须把每一帧都解出来（票 06）。
#
# 核心反面自检（覆盖元断言）：Node 的 `zstdDecompressSync` 一次性只解**第一帧**。
# 本测试同时断言这条天真路径确实只拿到前缀——否则夹具退化成单帧时，本测试会空转通过。
set -euo pipefail
cd "$(dirname "$0")/.."

DECODER="tools/decode-session.mjs"
fail=0

# 合成反例探针纪律（票 05）：探针目录只在 $TMPDIR；跑完证明没动过仓库树。
# shellcheck source=tests/support/probe.sh
source tests/support/probe.sh

# node 是工具的运行时：缺失即判红（与 run_all.sh 对 Python 测试运行器缺失的态度一致，不回退、不静默通过）。
if ! command -v node >/dev/null 2>&1; then
  echo "FAIL: $DECODER 需要 node 运行时，PATH 里找不到 node"
  exit 1
fi

PROBE="$(probe_mktemp)" || exit 1
trap 'rm -rf "$PROBE"' EXIT
REPO_TREE_BEFORE="$(repo_tree_snapshot)"

# 夹具：3 帧拼接（帧内带 checksum，覆盖扫描器的 checksum 分支）+ 明文真值。
cat > "$PROBE/fixture.cjs" <<'JS'
const fs = require('fs'), z = require('zlib'), { pathToFileURL } = require('url')
const PARTS = ['{"event":1}\n{"event":2}\n', '{"event":3}\n', '{"event":4}\n{"event":5}\n']
const OPTIONS = { params: { [z.constants.ZSTD_c_checksumFlag]: 1 } }
const compress = (parts) => Buffer.concat(parts.map((part) => z.zstdCompressSync(part, OPTIONS)))
const [mode, ...rest] = process.argv.slice(2)

if (mode === 'build') {
  const [out, truth] = rest
  fs.writeFileSync(out, compress(PARTS))
  fs.writeFileSync(truth, PARTS.join(''))
} else if (mode === 'torn') {
  // 末帧砍掉 N 字节：扫描器应只认出前两帧，并把第三帧起点报成 tornStart。
  const [out, truth, bytes] = rest
  const whole = compress(PARTS)
  fs.writeFileSync(out, whole.subarray(0, whole.length - Number(bytes)))
  fs.writeFileSync(truth, PARTS.slice(0, -1).join(''))
} else if (mode === 'naive') {
  // 天真路径：一次 zstdDecompressSync 只解第一帧。
  fs.writeFileSync(rest[1], z.zstdDecompressSync(fs.readFileSync(rest[0])))
} else if (mode === 'scan') {
  // 导出面 scanZstdFrames：帧边界必须连续、恰好 3 帧、无残帧。
  import(pathToFileURL(rest[0]).href).then((mod) => {
    if (typeof mod.scanZstdFrames !== 'function') throw new Error('scanZstdFrames 未导出')
    const { frames, tornStart } = mod.scanZstdFrames(fs.readFileSync(rest[1]))
    if (frames.length !== 3) throw new Error(`应扫出 3 帧，实得 ${frames.length}`)
    if (tornStart !== undefined) throw new Error(`完整夹具不应有残帧，实得 tornStart=${tornStart}`)
    const contiguous = frames.every((f, i) => f.start === (i === 0 ? 0 : frames[i - 1].end))
    if (!contiguous) throw new Error('帧边界不连续')
    console.log(`frames=${frames.length} contiguous`)
    process.exit(0)
  }).catch((error) => {
    console.error(error.message)
    process.exit(1)
  })
}
JS

node "$PROBE/fixture.cjs" build "$PROBE/session.v3.jsonl.zstd" "$PROBE/truth.jsonl"

# 1. 主路径：--in/--out 解出全部帧。
if node "$DECODER" --in "$PROBE/session.v3.jsonl.zstd" --out "$PROBE/decoded.jsonl" >"$PROBE/decoder.log" 2>&1; then
  if cmp -s "$PROBE/truth.jsonl" "$PROBE/decoded.jsonl"; then
    echo "PASS: 三帧拼接日志逐字节解出全部明文"
  else
    echo "FAIL: 解码结果与明文真值不一致（可能只解了第一帧）"
    fail=1
  fi
  frames="$(grep -oE 'frames=[0-9]+' "$PROBE/decoder.log" | head -1 || true)"
  lines="$(grep -oE 'lines=[0-9]+' "$PROBE/decoder.log" | head -1 || true)"
  if [ "$frames" = "frames=3" ] && [ "$lines" = "lines=5" ]; then
    echo "PASS: 摘要帧数/行数与日志一致（frames=3 lines=5）"
  else
    echo "FAIL: 摘要不符——期望 frames=3 lines=5，实得 ${frames:-无} ${lines:-无}"
    fail=1
  fi
else
  echo "FAIL: node $DECODER --in ... --out ... 退出非零"
  sed 's/^/      /' "$PROBE/decoder.log"
  fail=1
fi

# 1b. 内联形式 --in=… --out=… 与空格形式等价（参数解析的另一分支）。
if node "$DECODER" --in="$PROBE/session.v3.jsonl.zstd" --out="$PROBE/inline.jsonl" >/dev/null 2>&1 \
  && cmp -s "$PROBE/truth.jsonl" "$PROBE/inline.jsonl"; then
  echo "PASS: --in=/--out= 内联形式等价解出全部明文"
else
  echo "FAIL: --in=/--out= 内联形式结果不符"
  fail=1
fi

# 2. 覆盖元断言的反面：天真单帧解码必须只拿到第一帧前缀，否则这个夹具证明不了"多帧"。
if node "$PROBE/fixture.cjs" naive "$PROBE/session.v3.jsonl.zstd" "$PROBE/naive.jsonl"; then
  naive_bytes="$(wc -c < "$PROBE/naive.jsonl")"
  truth_bytes="$(wc -c < "$PROBE/truth.jsonl")"
  if [ "$naive_bytes" -lt "$truth_bytes" ] \
    && cmp -s "$PROBE/naive.jsonl" <(head -c "$naive_bytes" "$PROBE/truth.jsonl"); then
    echo "PASS: 天真单帧路径只拿到第一帧前缀（$naive_bytes < $truth_bytes 字节）"
  else
    echo "FAIL: 反面自检失效——天真单帧路径拿到了全部明文（夹具可能只有一帧）"
    fail=1
  fi
else
  echo "FAIL: 反面自检夹具无法运行"
  fail=1
fi

# 3. 导出面：scanZstdFrames 可被 import，且帧边界连续。
if node "$PROBE/fixture.cjs" scan "$DECODER" "$PROBE/session.v3.jsonl.zstd" >"$PROBE/scan.log" 2>&1; then
  echo "PASS: scanZstdFrames 扫出连续 3 帧（$(cat "$PROBE/scan.log")）"
else
  echo "FAIL: scanZstdFrames 导出面/帧扫描不符合预期"
  sed 's/^/      /' "$PROBE/scan.log"
  fail=1
fi

# 3b. 末帧截断：完整帧照解、残帧只警告，退出码 3（部分解出）——防"静默丢掉残帧后的事件"。
if node "$PROBE/fixture.cjs" torn "$PROBE/torn.zstd" "$PROBE/torn-truth.jsonl" 5 \
  && node "$DECODER" --in "$PROBE/torn.zstd" --out "$PROBE/torn-out.jsonl" >"$PROBE/torn.log" 2>&1; then
  echo "FAIL: 残帧输入仍报成功（退出码 0），与完整日志无法区分"
  fail=1
else
  code=$?
  if [ "$code" -eq 3 ] && cmp -s "$PROBE/torn-truth.jsonl" "$PROBE/torn-out.jsonl" \
    && grep -q '末帧不完整' "$PROBE/torn.log"; then
    echo "PASS: 残帧只解完整帧、警告并退出 3（部分解出）"
  else
    echo "FAIL: 残帧处理不符（期望退出 3 且只含完整帧，实得退出 $code）"
    sed 's/^/      /' "$PROBE/torn.log"
    fail=1
  fi
fi

# 4. 腐坏输入必须判红：坏 magic 不得静默产出。
printf 'not-a-zstd-log' > "$PROBE/garbage.bin"
if node "$DECODER" --in "$PROBE/garbage.bin" --out "$PROBE/nope.jsonl" >/dev/null 2>&1; then
  echo "FAIL: 坏 magic 的输入未被判红"
  fail=1
else
  echo "PASS: 坏 magic 的输入被判红（退出非零）"
fi

# 5. 用法错误必须判红：缺 --in/--out。
if node "$DECODER" --in "$PROBE/session.v3.jsonl.zstd" >/dev/null 2>&1; then
  echo "FAIL: 缺 --out 时未判红"
  fail=1
else
  echo "PASS: 缺 --out 时判红（退出非零）"
fi

# 探针纪律：本测试只应写 $TMPDIR，不碰仓库工作树；断言本身也要有反面自检。
if ! probe_discipline_check "$REPO_TREE_BEFORE"; then fail=1; fi
if ! probe_discipline_selftest; then fail=1; fi

if [ "$fail" -eq 0 ]; then
  echo "PASS: 会话日志解码工具校验全部通过"
fi
exit "$fail"
