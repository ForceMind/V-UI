import psutil
import platform
import time
from fastapi import APIRouter

router = APIRouter()

def get_size(bytes, suffix="B"):
    """
    Scale bytes to its proper format
    e.g:
        1253656 => '1.20MB'
        1253656678 => '1.17GB'
    """
    factor = 1024
    for unit in ["", "K", "M", "G", "T", "P"]:
        if bytes < factor:
            return f"{bytes:.2f}{unit}{suffix}"
        bytes /= factor

@router.get("/status")
async def get_system_status():
    """
    Get real-time system status (CPU, RAM, Disk, Net)
    """
    # CPU
    cpu_percent = psutil.cpu_percent(interval=1)
    cpu_count = psutil.cpu_count(logical=True)
    
    # Memory
    svmem = psutil.virtual_memory()
    memory_total = get_size(svmem.total)
    memory_used = get_size(svmem.used)
    memory_percent = svmem.percent
    
    # Disk
    partitions = psutil.disk_partitions()
    disk_info = []
    for partition in partitions:
        try:
            partition_usage = psutil.disk_usage(partition.mountpoint)
            disk_info.append({
                "device": partition.device,
                "mountpoint": partition.mountpoint,
                "total": get_size(partition_usage.total),
                "used": get_size(partition_usage.used),
                "percent": partition_usage.percent
            })
        except PermissionError:
            continue

    # Network
    net_io = psutil.net_io_counters()
    
    # System Info
    uname = platform.uname()
    
    return {
        "os": f"{uname.system} {uname.release}",
        "uptime": int(time.time() - psutil.boot_time()),
        "cpu": {
            "percent": cpu_percent,
            "cores": cpu_count
        },
        "memory": {
            "total": memory_total,
            "used": memory_used,
            "percent": memory_percent
        },
        "disk": disk_info,
        "network": {
            "sent": get_size(net_io.bytes_sent),
            "recv": get_size(net_io.bytes_recv)
        }
    }
