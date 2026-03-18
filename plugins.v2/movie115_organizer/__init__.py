from typing import List, Dict, Tuple, Any
from app.plugins import _PluginBase
from app.log import logger
from app.schemas import NotificationType

def get_p115_helper():
    """
    全量适配 V2 各种版本的 P115Helper 路径
    """
    import importlib
    # 按照优先级尝试所有可能的导入路径
    test_paths = [
        "app.helper.p115",
        "app.modules.p115",
        "app.modules.index.p115",
        "app.helper.index.p115"
    ]
    for path in test_paths:
        try:
            mod = importlib.import_module(path)
            helper = getattr(mod, "P115Helper", None)
            if helper:
                return helper
        except ImportError:
            continue
    return None

class movie115_organizer(_PluginBase):
    # --- V2 插件元数据 ---
    plugin_id = "movie115_organizer"
    plugin_name = "115 目录洗白整理"
    plugin_desc = "监控115路径，自动清理小文件、重命名并归档。"
    plugin_icon = "https://raw.githubusercontent.com/wq2020wdm/MoviePilot-Plugins/main/icons/98tang.png" 
    plugin_version = "1.2.8"
    plugin_author = "wq2020wdm"
    plugin_order = 10
    auth_level = 1

    # 配置变量初始化
    _enabled = False
    _cron = None
    _monitor_path = None
    _target_path = None
    _threshold = 500
    _notify = True

    def init_plugin(self, config: dict = None):
        if config:
            self._enabled = config.get("enabled")
            self._cron = config.get("cron")
            self._monitor_path = config.get("monitor_path")
            self._target_path = config.get("target_path")
            try:
                self._threshold = float(config.get("threshold") or 500)
            except:
                self._threshold = 500
            self._notify = config.get("notify")

    def get_state(self) -> bool:
        return self._enabled

    def get_page(self) -> List[dict]:
        return []

    def get_api(self) -> List[dict]:
        return []

    # --- Bot 命令与立即执行按钮注册 ---
    def get_command(self) -> List[Dict[str, Any]]:
        return [
            {
                "command": "run_115_organizer",
                "data": "run_115_organizer",
                "description": "立即运行115整理",
                "handler": self.execute,
                "icon": "PlayArrow" # V2 标准图标名
            }
        ]

    def get_id_by_path(self, p115, path: str):
        if not path: return None
        if path.isdigit(): return path
        if not path.startswith('/'): return path
        parts = [p for p in path.split('/') if p]
        current_id = '0'
        for part in parts:
            found = False
            items = p115.get_file_list(current_id)
            if items:
                for item in items:
                    if item.get('is_dir') and item.get('name') == part:
                        current_id = item.get('id')
                        found = True
                        break
            if not found: return None
        return current_id

    def execute(self, **kwargs):
        """核心执行逻辑"""
        # Bot 触发或手动触发都会进到这里
        HelperClass = get_p115_helper()
        if not HelperClass:
            logger.error("【115整理】加载失败：未找到 P115Helper 核心模块，请联系开发者适配路径")
            return

        p115 = HelperClass()
        m_id = self.get_id_by_path(p115, self._monitor_path)
        t_id = self.get_id_by_path(p115, self._target_path)

        if not m_id or not t_id:
            logger.error("【115整理】路径解析失败，请确认配置路径是否存在")
            return

        try:
            items = p115.get_file_list(m_id)
            if not items: return

            for item in items:
                if not item.get('is_dir'): continue
                fid, fname = item.get('id'), item.get('name')
                
                # 1. 清理
                sub_files = p115.get_file_list(fid)
                for sf in sub_files:
                    if not sf.get('is_dir'):
                        if (sf.get('size', 0) / 1048576) < self._threshold:
                            p115.delete_file(sf.get('id'))

                # 2. 重命名
                new_name = fname.split('@')[-1] if '@' in fname else fname
                if new_name != fname:
                    p115.rename_file(fid, new_name)

                # 3. 归档
                p115.move_file(fid, t_id)
                logger.info("【115整理】归档成功: %s" % new_name)

            if self._notify:
                self.post_message(NotificationType.SiteMessage, "115 整理完成", "监控目录已清理并归档。")
        except Exception as e:
            logger.error("【115整理】执行报错: %s" % str(e))

    def get_service(self) -> List[Dict[str, Any]]:
        if self._enabled and self._cron:
            from apscheduler.triggers.cron import CronTrigger
            return [{
                "id": "movie115_organizer_task",
                "name": "115整理服务",
                "trigger": CronTrigger.from_crontab(self._cron),
                "func": self.execute
            }]
        return []

    def stop_service(self):
        pass
