#!/usr/bin/env bash
# ===========================================================================
#  NetWatch —— Linux 构建脚本
#
#  用法：
#      ./packaging/build_linux.sh
#
#  产物：
#      dist/netwatch                       单文件可执行程序
#      dist/netwatch-linux-<arch>.tar.gz   发布用压缩包
#
#  前置条件：已安装 Python 3.8+ 与 PyInstaller
#      python3 -m pip install -r requirements-dev.txt
#
#  说明：桌面通知依赖 notify-send / zenity，属于运行期依赖，无需打进产物。
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
STAGE_NAME="NetWatch-linux-${ARCH}"
STAGE_DIR="${DIST_DIR}/${STAGE_NAME}"
TARBALL="${DIST_DIR}/netwatch-linux-${ARCH}.tar.gz"

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
log "开始构建 Linux 可执行程序（${ARCH}）..."
"${PYTHON_BIN}" -m PyInstaller "${PROJECT_ROOT}/packaging/netwatch.spec" \
    --noconfirm --clean \
    --distpath "${DIST_DIR}" \
    --workpath "${BUILD_DIR}"

if [ ! -f "${DIST_DIR}/${APP_NAME}" ]; then
    die "构建失败：未找到 ${DIST_DIR}/${APP_NAME}"
fi

cp "${DIST_DIR}/${APP_NAME}" "${STAGE_DIR}/"
chmod +x "${STAGE_DIR}/${APP_NAME}"

# --- 附带说明文件 ---------------------------------------------------------
for extra in README.md LICENSE CHANGELOG.md; do
    if [ -f "${PROJECT_ROOT}/${extra}" ]; then
        cp "${PROJECT_ROOT}/${extra}" "${STAGE_DIR}/"
    fi
done

# --- 打包 tar.gz ----------------------------------------------------------
log "生成压缩包：${TARBALL}"
rm -f "${TARBALL}"
tar -czf "${TARBALL}" -C "${DIST_DIR}" "${STAGE_NAME}"

log "构建完成！"
log "  可执行程序：${DIST_DIR}/${APP_NAME}"
log "  发布压缩包：${TARBALL}"
