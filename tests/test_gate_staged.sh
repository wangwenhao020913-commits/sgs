#!/usr/bin/env bash
# 门禁分级器：docs 提交走快速文档检查，src/tests 走全量 run_all.sh。
# 用临时 git 仓库与 stub 命令验证三种分类，不跑真实门禁。
set -euo pipefail
# 门禁 hook 会导出 GIT_DIR / GIT_WORK_TREE / GIT_INDEX_FILE（linked worktree）。本文件用
# `git -C "$tmp"` 操作临时仓库；GIT_INDEX_FILE 是**相对当前 cwd** 解析的，不隔离就会把
# `add` / `reset` 打到本仓库的暂存区（真踩过：hook 跑门禁时暂存区被 reset）。先清掉。
unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_PREFIX GIT_OBJECT_DIRECTORY
unset GIT_ALTERNATE_OBJECT_DIRECTORIES GIT_COMMON_DIR GIT_NAMESPACE
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

mkdir -p "$tmp/tools" "$tmp/tests" "$tmp/src" "$tmp/开发文档" "$tmp/.venv/bin"
cp "$ROOT/tools/gate-staged.sh" "$tmp/tools/gate-staged.sh"

cat > "$tmp/tests/run_all.sh" <<'STUB'
echo full >> "$MARKER"
STUB
cat > "$tmp/tests/test_dev_docs.sh" <<'STUB'
echo devdocs >> "$MARKER"
STUB
cat > "$tmp/tests/test_agent_skills.sh" <<'STUB'
echo skills >> "$MARKER"
STUB
cat > "$tmp/tests/test_agents_md.sh" <<'STUB'
echo agents >> "$MARKER"
STUB
py_cmd="py""test"
export py_cmd
cat > "$tmp/.venv/bin/$py_cmd" <<'STUB'
#!/usr/bin/env bash
echo "$py_cmd" >> "$MARKER"
STUB
chmod +x "$tmp/.venv/bin/$py_cmd"

git -C "$tmp" init -q
git -C "$tmp" config user.email test@example.com
git -C "$tmp" config user.name test

fail=0
marker="$tmp/marker"
run_in_tmp() {
  ( cd "$tmp" && MARKER="$marker" bash tools/gate-staged.sh "$@" )
}

# 1) 分类：docs / full / skip
touch "$tmp/开发文档/04-AI与交互设计.md"
git -C "$tmp" add "开发文档/04-AI与交互设计.md"
[ "$(run_in_tmp --classify)" = "docs" ] || { echo "FAIL: docs 未分到 docs"; fail=1; }
dry="$(run_in_tmp --dry-run)"
grep -q 'test_writeback_landing.py' <<<"$dry" || { echo "FAIL: docs dry-run 缺账本检查"; fail=1; }
grep -q 'run_all.sh' <<<"$dry" && { echo "FAIL: docs dry-run 不该跑全量"; fail=1; }

# docs 级的每个触发路径都要真的触发（不能只测 开发文档/）
for path in GLOSSARY.md AGENTS.md CODING_STANDARDS.md docs/agents/x.md; do
  mkdir -p "$tmp/$(dirname "$path")"
  touch "$tmp/$path"
  git -C "$tmp" reset -q
  git -C "$tmp" add "$path"
  [ "$(run_in_tmp --classify)" = "docs" ] || { echo "FAIL: $path 未分到 docs"; fail=1; }
done

git -C "$tmp" reset -q
touch "$tmp/src/example.py"
git -C "$tmp" add src/example.py
[ "$(run_in_tmp --classify)" = "full" ] || { echo "FAIL: src 未分到 full"; fail=1; }
dry="$(run_in_tmp --dry-run)"
grep -q 'run_all.sh' <<<"$dry" || { echo "FAIL: full dry-run 缺 run_all"; fail=1; }

git -C "$tmp" reset -q
touch "$tmp/tests/example.py"
git -C "$tmp" add tests/example.py
[ "$(run_in_tmp --classify)" = "full" ] || { echo "FAIL: tests 未分到 full"; fail=1; }

git -C "$tmp" reset -q
touch "$tmp/README.md"
git -C "$tmp" add README.md
[ "$(run_in_tmp --classify)" = "skip" ] || { echo "FAIL: 无关改动未分到 skip"; fail=1; }
[ "$(run_in_tmp --dry-run)" = "skip" ] || { echo "FAIL: skip dry-run 不是 skip"; fail=1; }

# 2) 执行：docs 只跑四个快速检查，src 只跑全量
: > "$marker"
git -C "$tmp" reset -q
git -C "$tmp" add "开发文档/04-AI与交互设计.md"
run_in_tmp --run "门禁 hook" >/dev/null
for expected in devdocs skills agents "$py_cmd"; do
  grep -qx "$expected" "$marker" || { echo "FAIL: docs 执行缺 $expected"; fail=1; }
done
grep -qx full "$marker" && { echo "FAIL: docs 执行跑了全量"; fail=1; }

: > "$marker"
git -C "$tmp" reset -q
git -C "$tmp" add src/example.py
run_in_tmp --run "门禁 hook" >/dev/null
grep -qx full "$marker" || { echo "FAIL: src 执行没跑全量"; fail=1; }
for unexpected in devdocs skills agents "$py_cmd"; do
  grep -qx "$unexpected" "$marker" && { echo "FAIL: src 执行跑了 $unexpected"; fail=1; }
done

: > "$marker"
git -C "$tmp" reset -q
git -C "$tmp" add README.md
out="$(run_in_tmp --run "门禁 hook")"
grep -q '跳过' <<<"$out" || { echo "FAIL: skip 执行没有跳过文案"; fail=1; }
[ ! -s "$marker" ] || { echo "FAIL: skip 执行碰了命令"; fail=1; }

if [ "$fail" -eq 0 ]; then
  echo "PASS: gate-staged 分级与执行命令符合预期"
fi
exit "$fail"
