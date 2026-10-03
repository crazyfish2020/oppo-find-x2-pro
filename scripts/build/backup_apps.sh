#!/bin/bash
# 备份第三方应用的 APK（含 split APK），用于刷机后快速重装。
# 不备份应用数据（用户要求「其他不用」）。
set -u

ADB="$HOME/Documents/oppo/platform-tools/adb"
OUT="$HOME/Documents/oppo/2026-安卓16/06-应用备份"
mkdir -p "$OUT/apk"

echo "=== 1) 收集包名与版本 ==="
"$ADB" shell 'pm list packages -3' | sed 's/package://' | tr -d '\r' | sort > "$OUT/包名清单.txt"
N=$(wc -l < "$OUT/包名清单.txt" | tr -d ' ')
echo "  第三方应用: $N 个"

echo "=== 2) 收集版本号 ==="
: > "$OUT/版本清单.txt"
while read -r pkg; do
  [ -z "$pkg" ] && continue
  ver=$("$ADB" shell "dumpsys package $pkg | grep -m1 versionName" </dev/null 2>/dev/null | tr -d '\r' | sed 's/.*versionName=//')
  printf '%s\t%s\n' "$pkg" "${ver:-未知}" >> "$OUT/版本清单.txt"
done < "$OUT/包名清单.txt"
echo "  完成 $(wc -l < "$OUT/版本清单.txt" | tr -d ' ') 条"

echo "=== 3) 拉取 APK ==="
ok=0; skip=0; fail=0
: > "$OUT/拉取日志.txt"
while read -r pkg; do
  [ -z "$pkg" ] && continue
  paths=$("$ADB" shell "pm path $pkg" </dev/null 2>/dev/null | tr -d '\r' | sed 's/^package://')
  if [ -z "$paths" ]; then
    echo "  ✗ $pkg 无 APK 路径" | tee -a "$OUT/拉取日志.txt"
    fail=$((fail+1)); continue
  fi
  mkdir -p "$OUT/apk/$pkg"
  i=0
  for p in $paths; do
    i=$((i+1))
    name=$(basename "$p")
    [ "$i" -gt 1 ] && name="split_${i}_${name}"
    if "$ADB" pull "$p" "$OUT/apk/$pkg/$name" </dev/null >/dev/null 2>&1; then
      :
    else
      echo "  ✗ $pkg / $p 拉取失败" | tee -a "$OUT/拉取日志.txt"
      fail=$((fail+1))
    fi
  done
  ok=$((ok+1))
  printf "  ✓ %-42s %d 个 apk\n" "$pkg" "$i"
done < "$OUT/包名清单.txt"

echo
echo "=== 汇总 ==="
echo "  成功: $ok   失败: $fail"
echo "  备份目录: $OUT"
du -sh "$OUT" 2>/dev/null
