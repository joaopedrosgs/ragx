"""Bound conversion subprocess lifetime and sampled process-tree memory.

This guard complements parser/allocation limits. It cannot prevent one native
allocation overshooting between samples; it terminates the whole build tree.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

import psutil


def worker_limit(requested: int, memory_mb: int, reserve_mb: int) -> int:
    if min(requested, memory_mb, reserve_mb) < 1:
        raise ValueError('workers and memory budgets must be positive')
    available = psutil.virtual_memory().available // 1024**2 - reserve_mb
    # One archive index plus geometry/scratch headroom, measured on LATAM assets.
    allowance = min(memory_mb, available)
    if allowance < 768:
        raise MemoryError('less than 768 MiB available after the desktop reserve')
    return min(requested, max(1, int(allowance // 768)), os.cpu_count() or 1)


def supervise(command: list[str], *, memory_mb: int, reserve_mb: int, timeout: float) -> int:
    env = dict(os.environ, RAGX_SUPERVISED='1')
    started = time.monotonic()
    child = subprocess.Popen(command, env=env)
    known = {}
    reason = None
    peak = 0
    try:
        while child.poll() is None:
            try:
                parent = psutil.Process(child.pid)
                processes = [parent, *parent.children(recursive=True)]
                private = 0
                for process in processes:
                    try:
                        known[process.pid] = process
                        info = process.memory_info()
                        private += info.private if os.name == 'nt' else process.memory_full_info().uss
                    except psutil.NoSuchProcess:
                        pass
                peak = max(peak, private)
                if private > memory_mb * 1024**2:
                    reason = 'memory budget exceeded'
                elif psutil.virtual_memory().available < reserve_mb * 1024**2:
                    reason = 'desktop memory reserve reached'
                elif time.monotonic() - started > timeout:
                    reason = 'conversion timeout'
                if reason:
                    break
            except psutil.NoSuchProcess:
                pass
            time.sleep(0.1)
    finally:
        if child.poll() is None or reason:
            for process in reversed(list(known.values())):
                try:
                    process.kill()
                except psutil.NoSuchProcess:
                    pass
            if child.poll() is None:
                child.kill()
            child.wait(timeout=10)
    print(f'budget: peak_private_mb={peak / 1024**2:.1f} status={reason or "finished"}', flush=True)
    return 1 if reason else child.returncode
