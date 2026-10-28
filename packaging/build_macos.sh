#!/usr/bin/env bash
# ===========================================================================
#  NetWatch —— macOS 构建脚本
#
#  用法：
#      ./packaging/build_macos.sh
#
#  产物：
#      dist/netwatch.app               应用包（macOS 12+）
#      dist/netwatch-macos-<arch>.zip  发布用压缩包
#
#  前置条件：已安装 Python 3.8+ 与 PyInstaller
#      python3 -m pip install -r requirements-dev.txt
#
#  说明：PyInstaller 在 macOS 上不支持 "onefile + .app" 组合，因此默认生成
#  标准 .app 应用包（内部为 onedir 结构）。如需强制单文件输出，可设置
#  环境变量 NETWATCH_ONEFILE=1，此时将得到 dist/netwatch 而没有 .app。
# ===========================================================================
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
cd "${PROJECT_ROOT}"

APP_NAME="netwatch"
PYTHON_BIN="${PYTHON_BIN:-python3}"
DIST_DIR="${PROJECT_ROOT}/dist"
BUILD_DIR="${PROJECT_ROOT}/build/packaging"
ARCH="$(uname -m)"
STAGE_NAME="NetWatch-macos-${ARCH}"
STAGE_DIR="${DIST_DIR}/${STAGE_NAME}"
ZIP_PATH="${DIST_DIR}/netwatch-macos-${ARCH}.zip"

log()  { printf '\033[1;34m[build]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[build]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[build] 错误：\033[0m %s\n' "$*" >&2; exit 1; }

# --- 环境检查 -------------------------------------------------------------
command -v "${PYTHON_BIN}" >/dev/null 2>&1 \
    || die "未找到 ${PYTHON_BIN}，请先安装 Python 3.8+"

"${PYTHON_BIN}" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' \
    || die "需要 Python 3.8 或更高版本"

if ! "${PYTHON_BIN}" -m PyInstaller --version >/dev/null 2>&1; then
    die "未安装 PyInstaller，请先执行：${PYTHON_BIN} -m pip install -r requirements-dev.txt"
fi
log "PyInstaller 版本：$("${PYTHON_BIN}" -m PyInstaller --version)"

# --- 清理暂存目录 ---------------------------------------------------------
rm -rf "${STAGE_DIR}"
mkdir -p "${STAGE_DIR}"

# --- 构建 -----------------------------------------------------------------
log "开始构建 macOS 应用包（${ARCH}）..."
"${PYTHON_BIN}" -m PyInstaller "${PROJECT_ROOT}/packaging/netwatch.spec" \
    --noconfirm --clean \
    --distpath "${DIST_DIR}" \
    --workpath "${BUILD_DIR}"

if [ -d "${DIST_DIR}/${APP_NAME}.app" ]; then
    cp -R "${DIST_DIR}/${APP_NAME}.app" "${STAGE_DIR}/"
    log "已生成应用包：${DIST_DIR}/${APP_NAME}.app"
elif [ -f "${DIST_DIR}/${APP_NAME}" ]; then
    cp "${DIST_DIR}/${APP_NAME}" "${STAGE_DIR}/"
    warn "未生成 .app（可能设置了 NETWATCH_ONEFILE=1），改为打包单文件可执行程序。"
else
    die "构建失败：未找到 ${DIST_DIR}/${APP_NAME}.app 或 ${DIST_DIR}/${APP_NAME}"
fi

# --- 附带说明文件 ---------------------------------------------------------
for extra in README.md LICENSE CHANGELOG.md; do
    if [ -f "${PROJECT_ROOT}/${extra}" ]; then
        cp "${PROJECT_ROOT}/${extra}" "${STAGE_DIR}/"
    fi
done

# --- 打包 ZIP（ditto 保留符号链接与扩展属性）-------------------------------
log "生成 ZIP 压缩包：${ZIP_PATH}"
rm -f "${ZIP_PATH}"
if command -v ditto >/dev/null 2>&1; then
    ditto -c -k --sequesterRsrc --keepParent "${STAGE_DIR}" "${ZIP_PATH}"
else
    ( cd "${DIST_DIR}" && zip -r -q "$(basename "${ZIP_PATH}")" "${STAGE_NAME}" )
fi

log "构建完成！"
log "  应用包：  ${DIST_DIR}/${APP_NAME}.app"
log "  发布压缩包：${ZIP_PATH}"
