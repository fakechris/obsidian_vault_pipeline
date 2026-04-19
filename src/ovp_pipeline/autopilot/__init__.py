"""
AutoPilot - 全自动知识管理守护进程

Usage:
    ovp-autopilot --watch=inbox --parallel=2
    ovp-autopilot --watch=inbox,pinboard --interval=300
"""

from .daemon import AutoPilotDaemon
from .queue import TaskQueue

__all__ = ['AutoPilotDaemon', 'TaskQueue']
