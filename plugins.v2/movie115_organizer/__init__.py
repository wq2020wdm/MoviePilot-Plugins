import time
from typing import List, Dict, Tuple, Any, Optional
from app.plugins import _PluginBase
from app.log import logger
from app.schemas import NotificationType

# 封装动态获取 115 助手的逻辑，避免在文件头 import 导致加载失败
def get_p115_helper():
    try:
        # MoviePilot V2 标准路径
        from app.helper.p115 import P115Helper
        return P115Helper
    except ImportError:
        try:
            # 尝试兼容部分 V2 过渡版本路径
            from app.modules.p115 import P115Helper
            return P115Helper
        except ImportError:
            return None

class Movie115Organizer(_PluginBase):
    # --- V2 核心元数据 ---
    plugin_id = "movie115_organizer"
    plugin_name = "115 目录洗白整理"
    plugin_desc = "监控115路径，自动清理小文件、按@符号重命名并移动归档。"
    plugin_icon = "folder.png"
    plugin_version = "1.2.2"
    plugin_author = "YourName"
    plugin_order = 10
    auth_level = 1

    # 配置变量
    _enabled = False
    _cron = None
    _monitor_path = None
    _target_path = None
    _threshold = 500
    _notify = True

    def init_plugin(self, config: dict = None):
        """配置初始化与热重载"""
        if config:
            self._enabled = config.get("enabled")
            self._cron = config.get("cron")
            self._monitor_path = config.get("monitor_path")
            self._target_path = config.get("target_path")
            try:
                self._threshold = float(config.get("threshold") or 500)
            except (ValueError, TypeError):
                self._threshold = 500
            self._notify = config.get("notify")

    def get_id_by_path(self, p115, path: str):
        """将 115 路径字符串转换为文件夹 ID"""
        if not path: return None
        if path.isdigit(): return path
        if not path.startswith('/'): return path
        
        parts = [p for p in path.split('/') if p]
        current_id = '0'
        for part in parts:
            found = False
            items = p115.get_file_list(current_id)
            if not items: break
            for item in items:
                if item.get('is_dir') and item.get('name') == part:
                    current_id = item.get('id')
                    found = True
                    break
            if not found: return None
        return current_id

    def execute(self):
        """核心执行逻辑"""
        if not self._enabled:
            return

        # 运行时动态获取助手类
        P115HelperClass = get_p115_helper()
        if not P115HelperClass:
            logger.error("【115整理】加载失败：未能在系统中找到 P115Helper 模块。")
            return

        p115 = P115HelperClass()
        m_id = self.get_id_by_path(p115, self._monitor_path)
        t_id = self.get_id_by_path(p115, self._target_path)

        if not m_id or not t_id:
            logger.error(f"【115整理】无法
