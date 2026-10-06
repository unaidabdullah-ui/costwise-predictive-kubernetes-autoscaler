"""Sandbox stand-in for Kubernetes (used ONLY for validation in an environment without Docker/Minikube).

* each "pod" is a real marketplace process (python -m marketplace) placed in its own
  cgroup-v1 CPU group with cfs_quota 25000/100000 = 250m CPU - the same CFS mechanism
  Kubernetes uses for a 250m CPU limit;
* a pod becomes Ready when its /health answers AND a readiness delay has passed
  (emulates readinessProbe initialDelaySeconds);
* a round-robin TCP proxy (like a Service) forwards :PROXY_PORT to Ready pods only;
* Prometheus discovers all live pods through a file_sd JSON file.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, os.environ.get("PROJECT", str(Path(__file__).resolve().parents[2])))

from autoscaler.kubernetes_client import DeploymentStatus  # noqa: E402
from costwise.cluster import PodInfo  # noqa: E402

PROJECT = Path(os.environ.get("PROJECT", str(Path(__file__).resolve().parents[2])))
CGROUP_ROOT = Path("/sys/fs/cgroup/cpu")


@dataclass
class Pod:
    name: str
    port: int
    proc: subprocess.Popen
    started: float
    ready_at: float | None = None
    terminating_since: float | None = None
    cgroup: Path | None = None
    log: Path | None = field(default=None, repr=False)


class ProcessCluster:
    def __init__(self, workdir: Path, base_port: int = 18101, max_pods: int = 8, cpu_quota_us: int = 25000,
                 readiness_delay: float = 5.0, terminate_grace: float = 3.0, initial: int = 1) -> None:
        self.workdir = workdir
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.base_port = base_port
        self.max_pods = max_pods
        self.cpu_quota_us = cpu_quota_us
        self.readiness_delay = readiness_delay
        self.terminate_grace = terminate_grace
        self.pods: list[Pod] = []
        self.desired = 0
        self.lock = threading.RLock()
        self._names = (f"marketplace-sbx-{i:03d}" for i in itertools.count(1))
        self.sd_file = workdir / "marketplace-targets.json"
        self._stop = threading.Event()
        self.scale_calls: list[tuple[float, int]] = []
        self.scale(initial)
        threading.Thread(target=self._reconcile_loop, daemon=True).start()

    # -- pod lifecycle -------------------------------------------------------------------
    def _free_port(self) -> int:
        used = {p.port for p in self.pods}
        for port in range(self.base_port, self.base_port + 64):
            if port not in used:
                return port
        raise RuntimeError("no free port")

    def _start_pod(self) -> None:
        name = next(self._names)
        port = self._free_port()
        log = self.workdir / f"{name}.log"
        env = {**os.environ, "MARKETPLACE_PORT": str(port), "MARKETPLACE_HOST": "127.0.0.1", "POD_NAME": name,
               "PYTHONPATH": str(PROJECT)}
        proc = subprocess.Popen([sys.executable, "-m", "marketplace"], cwd=PROJECT, env=env,
                                stdout=log.open("w"), stderr=subprocess.STDOUT)
        cg = None
        try:
            cg = CGROUP_ROOT / f"costwise-{name}"
            cg.mkdir(exist_ok=True)
            (cg / "cpu.cfs_period_us").write_text("100000")
            (cg / "cpu.cfs_quota_us").write_text(str(self.cpu_quota_us))
            (cg / "cgroup.procs").write_text(str(proc.pid))
        except OSError as exc:
            print(f"WARNING: cgroup CPU limit not applied: {exc}", flush=True)
            cg = None
        self.pods.append(Pod(name, port, proc, time.time(), cgroup=cg, log=log))

    def _terminate(self, pod: Pod) -> None:
        pod.terminating_since = time.time()
        try:
            pod.proc.send_signal(signal.SIGTERM)
        except ProcessLookupError:
            pass

    def scale(self, replicas: int) -> None:
        with self.lock:
            self.desired = max(0, min(self.max_pods * 2, int(replicas)))
            self.scale_calls.append((time.time(), self.desired))
            self._reconcile()

    def _reconcile(self) -> None:
        with self.lock:
            live = [p for p in self.pods if p.terminating_since is None]
            while len(live) < self.desired:
                self._start_pod()
                live = [p for p in self.pods if p.terminating_since is None]
            if len(live) > self.desired:
                # like the ReplicaSet controller: remove not-ready / youngest first
                victims = sorted(live, key=lambda p: (p.ready_at is not None, -p.started))[: len(live) - self.desired]
                for p in victims:
                    self._terminate(p)
            now = time.time()
            for p in list(self.pods):
                if p.terminating_since is not None:
                    if p.proc.poll() is None and now - p.terminating_since > 10:
                        p.proc.kill()
                    if p.proc.poll() is not None and now - p.terminating_since >= self.terminate_grace:
                        self.pods.remove(p)
                        if p.cgroup:
                            try:
                                p.cgroup.rmdir()
                            except OSError:
                                pass
                    continue
                if p.ready_at is None and now - p.started >= self.readiness_delay and self._healthy(p):
                    p.ready_at = now
                if p.proc.poll() is not None:  # crashed: replace
                    p.terminating_since = now
            self._write_sd()

    @staticmethod
    def _healthy(p: Pod) -> bool:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{p.port}/health", timeout=1) as r:  # noqa: S310
                return r.status == 200
        except OSError:
            return False

    def _write_sd(self) -> None:
        alive = [p for p in self.pods if p.proc.poll() is None]
        groups = [{"targets": [f"127.0.0.1:{p.port}"], "labels": {"pod": p.name}} for p in alive]
        tmp = self.sd_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(groups))
        os.replace(tmp, self.sd_file)

    def _reconcile_loop(self) -> None:
        while not self._stop.is_set():
            self._reconcile()
            self._stop.wait(0.5)

    def ready_ports(self) -> list[int]:
        with self.lock:
            return [p.port for p in self.pods if p.ready_at is not None and p.terminating_since is None]

    def shutdown(self) -> None:
        self._stop.set()
        with self.lock:
            for p in self.pods:
                if p.proc.poll() is None:
                    p.proc.kill()
                if p.cgroup:
                    try:
                        p.proc.wait(2)
                        p.cgroup.rmdir()
                    except (OSError, subprocess.TimeoutExpired):
                        pass


class ProcessScaler:
    """ReplicaScaler protocol on top of ProcessCluster (backend clearly named 'sandbox')."""

    backend = "sandbox-processes"

    def __init__(self, cluster: ProcessCluster) -> None:
        self.cluster = cluster

    def get_status(self) -> DeploymentStatus:
        with self.cluster.lock:
            live = [p for p in self.cluster.pods if p.terminating_since is None]
            ready = sum(1 for p in live if p.ready_at is not None)
            return DeploymentStatus("marketplace", "sandbox", self.cluster.desired, ready, ready, len(live),
                                    len(live) - ready)

    def get_replicas(self) -> int:
        return self.cluster.desired

    def scale(self, replicas: int) -> None:
        self.cluster.scale(replicas)


class ProcessClusterView:
    backend = "sandbox-processes"

    def __init__(self, cluster: ProcessCluster) -> None:
        self.cluster = cluster

    def pods(self) -> list[PodInfo]:
        now = time.time()
        out = []
        with self.cluster.lock:
            for p in self.cluster.pods:
                if p.terminating_since is not None:
                    out.append(PodInfo(p.name, "terminating", "Stopping", False, 0, now - p.started))
                elif p.ready_at is not None:
                    out.append(PodInfo(p.name, "running", "Running", True, 0, now - p.started))
                else:
                    out.append(PodInfo(p.name, "starting", "Starting (not ready yet)", False, 0, now - p.started))
        order = {"running": 0, "starting": 1, "terminating": 2}
        return sorted(out, key=lambda p: (order[p.state], -(p.age_seconds or 0)))

    def recent_events(self, limit: int = 20) -> list[dict]:
        return [{"time": t, "type": "Normal", "reason": "ScalingReplicaSet", "object": "Deployment/marketplace",
                 "message": f"Scaled to {n} (sandbox)"} for t, n in self.cluster.scale_calls[-limit:]]


# ----------------------------------------------------------------------------- Service
class RoundRobinProxy:
    """TCP proxy forwarding each connection to the next Ready pod (Service emulation)."""

    def __init__(self, cluster: ProcessCluster, port: int) -> None:
        self.cluster = cluster
        self.port = port
        self._rr = itertools.count()
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)
        server = self.loop.run_until_complete(asyncio.start_server(self._handle, "127.0.0.1", self.port))
        self.server = server
        self.loop.run_forever()

    async def _pipe(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while data := await reader.read(65536):
                writer.write(data)
                await writer.drain()
        except (ConnectionError, OSError):
            pass
        finally:
            try:
                writer.close()
            except (ConnectionError, OSError):
                pass

    async def _handle(self, cr: asyncio.StreamReader, cw: asyncio.StreamWriter) -> None:
        ports = self.cluster.ready_ports()
        if not ports:
            cw.close()
            return
        port = ports[next(self._rr) % len(ports)]
        try:
            ur, uw = await asyncio.wait_for(asyncio.open_connection("127.0.0.1", port), 2)
        except (OSError, asyncio.TimeoutError):
            cw.close()
            return
        await asyncio.gather(self._pipe(cr, uw), self._pipe(ur, cw))
