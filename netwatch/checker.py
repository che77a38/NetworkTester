"""联网检测核心。

采用 TCP 探测判断目标是否可达：相比 DNS 解析，TCP 连通性更能真实反映
"能不能上网"（DNS 可能有本地缓存，ICMP 可能被防火墙丢弃）。
"""

from __future__ import annotations

import socket
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

DEFAULT_PORT = 443
ALT_PORT = 80


@dataclass
class TargetResult:
    """单个目标的检测结果。"""

    target: str
    online: bool
    latency_ms: int = -1
    error: str = ""
    ok_port: int = 0

    @property
    def label(self) -> str:
        """展示用的简短描述。"""
        if self.error:
            return "%s (%s)" % (self.target, self.error)
        if self.latency_ms >= 0:
            return "%s — %d ms" % (self.target, self.latency_ms)
        return self.target


@dataclass
class CheckResult:
    """一次整体检测的结果。"""

    online: bool
    results: List[TargetResult] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)

    @property
    def ok_count(self) -> int:
        return sum(1 for r in self.results if r.online)

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def avg_latency_ms(self) -> int:
        """成功目标的平均延迟（毫秒），无成功目标时为 -1。"""
        latencies = [r.latency_ms for r in self.results if r.online and r.latency_ms >= 0]
        if not latencies:
            return -1
        return int(sum(latencies) / len(latencies))

    def summary(self) -> str:
        """一行摘要文本。"""
        if not self.results:
            return "无检测目标"
        return "%d/%d 可用" % (self.ok_count, self.total)


def parse_target(raw: str) -> Tuple[str, int]:
    """解析检测目标，返回 (host, port)。

    支持以下写法：
      - ``example.com``            -> 443
      - ``example.com:8443``       -> 8443
      - ``https://example.com``    -> 443
      - ``http://example.com/x``   -> 80
      - ``1.1.1.1``                -> 443
    """
    text = (raw or "").strip()
    if not text:
        return "", DEFAULT_PORT

    scheme = ""
    if "://" in text:
        scheme, text = text.split("://", 1)
        scheme = scheme.lower()

    # 去掉路径与查询串
    for sep in ("/", "?", "#"):
        idx = text.find(sep)
        if idx != -1:
            text = text[:idx]

    # 去掉用户信息
    if "@" in text:
        text = text.rsplit("@", 1)[1]

    port = 0
    if text.startswith("["):  # IPv6 字面量 [::1]:8080
        end = text.find("]")
        if end != -1:
            host = text[1:end]
            rest = text[end + 1:]
            if rest.startswith(":") and rest[1:].isdigit():
                port = int(rest[1:])
            text = host
        else:
            text = text.strip("[]")
    elif ":" in text:
        head, _, tail = text.rpartition(":")
        if tail.isdigit() and head:
            port = int(tail)
            text = head

    if not text:
        text = "localhost"

    if port <= 0:
        port = 80 if scheme == "http" else DEFAULT_PORT
    return text, port


def probe(host: str, port: int, timeout: float) -> Tuple[bool, int, str]:
    """TCP 探测。返回 (是否连通, 延迟毫秒, 错误信息)。"""
    if not host:
        return False, -1, "空目标"
    try:
        timeout = max(0.5, float(timeout))
    except (TypeError, ValueError):
        timeout = 5.0
    started = time.time()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            elapsed = int((time.time() - started) * 1000)
            return True, elapsed, ""
    except socket.timeout:
        return False, -1, "超时"
    except socket.gaierror as exc:
        return False, -1, "DNS 解析失败: %s" % (exc.strerror or exc,)
    except OSError as exc:
        return False, -1, exc.strerror or str(exc)
    except Exception as exc:  # pragma: no cover - 兜底，避免检测线程崩溃
        return False, -1, str(exc)


def check_target(raw: str, timeout: float) -> TargetResult:
    """检测单个目标。

    未显式指定端口时，先试默认端口（443），失败再试备用端口（80），
    避免某些只开放 80 端口的站点被误判为断网。
    """
    host, port = parse_target(raw)
    explicit_port = False
    text = (raw or "").strip()
    if "://" in text:
        _, text = text.split("://", 1)
    if ":" in text and not text.startswith("["):
        tail = text.rpartition(":")[2].split("/")[0]
        explicit_port = tail.isdigit()
    if text.startswith("["):
        explicit_port = True

    ok, latency, error = probe(host, port, timeout)
    if ok:
        return TargetResult(target=raw, online=True, latency_ms=latency, ok_port=port)

    if not explicit_port:
        other = ALT_PORT if port == DEFAULT_PORT else DEFAULT_PORT
        ok2, latency2, error2 = probe(host, other, timeout)
        if ok2:
            return TargetResult(
                target=raw, online=True, latency_ms=latency2, ok_port=other
            )
        # 两个端口都失败时保留更有信息量的错误
        if port == DEFAULT_PORT and error2:
            error = error or error2

    return TargetResult(target=raw, online=False, latency_ms=-1, error=error)


def check_connectivity(
    targets: List[str], timeout: float, mode: str = "any"
) -> CheckResult:
    """并发检测所有目标，按 mode 汇总整体联网状态。"""
    cleaned: List[str] = []
    for item in targets or []:
        if isinstance(item, str) and item.strip():
            cleaned.append(item.strip())

    if not cleaned:
        return CheckResult(online=False, results=[])

    workers = min(8, len(cleaned))
    results: List[TargetResult] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(check_target, item, timeout) for item in cleaned]
        for future in futures:
            try:
                results.append(future.result())
            except Exception as exc:  # pragma: no cover - 兜底
                results.append(
                    TargetResult(target="?", online=False, error=str(exc))
                )

    if mode == "all":
        online = bool(results) and all(r.online for r in results)
    else:
        online = any(r.online for r in results)
    return CheckResult(online=online, results=results, timestamp=time.time())


def quick_check(timeout: float = 5.0) -> bool:
    """快速判断当前网络是否可用（用于 --check 命令行）。"""
    return check_connectivity(["www.baidu.com", "1.1.1.1"], timeout, "any").online
