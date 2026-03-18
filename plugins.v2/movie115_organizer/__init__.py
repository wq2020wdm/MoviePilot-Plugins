from typing import List, Dict, Tuple, Any
from app.plugins import _PluginBase
from app.log import logger
from app.schemas import NotificationType

def get_p115_helper():
    """动态获取 115 助手类"""
    for path in ["app.helper.p115", "app.modules.p115"]:
        try:
            import importlib
            mod = importlib.import_module(path)
            return getattr(mod, "P115Helper")
        except (ImportError, AttributeError):
            continue
    return None

class movie115_organizer(_PluginBase):
    # --- V2 插件元数据 ---
    plugin_id = "movie115_organizer"
    plugin_name = "115 目录洗白整理"
    plugin_desc = "监控115路径，自动清理小文件、按@符号重命名并移动归档。"
    plugin_icon = "folder" 
    plugin_version = "1.2.7"
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

    # --- 修复点：注册手动命令与按钮 ---
    def get_command(self) -> List[Dict[str, Any]]:
        """
        在 UI 显示立即执行按钮，并注册 Telegram/Slack 命令
        """
        return [
            {
                "command": "run_115_organizer", # 命令 ID
                "data": "run_115_organizer",    # 传递给 handler 的数据
                "description": "立即运行115目录整理", # 按钮悬浮提示/Bot 命令说明
                "handler": self.execute,         # 指向执行函数
                "icon": "play_arrow"             # V2 按钮图标
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
            if not items: break
            for item in items:
                if item.get('is_dir') and item.get('name') == part:
                    current_id = item.get('id')
                    found = True
                    break
            if not found: return None
        return current_id

    def execute(self, **kwargs):
        """核心执行逻辑"""
        # 注意：V2 触发 handler 时可能会传入 kwargs，这里加上兼容
        if not self._enabled:
            logger.warn("【115整理】插件未启用，跳过执行")
            return

        HelperClass = get_p115_helper()
        if not HelperClass:
            logger.error("【115整理】未找到 P115Helper 模块")
            return

        p115 = HelperClass()
        m_id = self.get_id_by_path(p115, self._monitor_path)
        t_id = self.get_id_by_path(p115, self._target_path)

        if not m_id or not t_id:
            logger.error(f"【115整理】路径解析失败: 监控={self._monitor_path}, 目标={self._target_path}")
            return

        try:
            items = p115.get_file_list(m_id)
            if not items:
                logger.info("【115整理】监控目录为空，无需处理")
                return

            for item in items:
                if not item.get('is_dir'): continue
                
                fid, fname = item.get('id'), item.get('name')
                
                # 1. 清理小文件
                sub_files = p115.get_file_list(fid)
                for sf in sub_files:
                    if not sf.get('is_dir'):
                        size_mb = sf.get('size', 0) / 1048576
                        if size_mb < self._threshold:
                            p115.delete_file(sf.get('id'))

                # 2. 洗白重命名
                new_name = fname.split('@')[-1] if '@' in fname else fname
                if new_name != fname:
                    p115.rename_file(fid, new_name)

                # 3. 移动归档
                p115.move_file(fid, t_id)
                logger.info("【115整理】成功归档文件夹: %s" % new_name)

                if self._notify:
                    self.post_message(NotificationType.SiteMessage, "115 整理完成", f"已归档: {new_name}")
        except Exception as e:
            logger.error(f"【115整理】执行异常: {str(e)}")

    def get_service(self) -> List[Dict[str, Any]]:
        if self._enabled and self._cron:
            from apscheduler.triggers.cron import CronTrigger
            return [{
                "id": "movie115_organizer_task",
                "name": "115整理定时服务",
                "trigger": CronTrigger.from_crontab(self._cron),
                "func": self.execute
            }]
        return []

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        return [{'component': 'VForm', 'content': [{'component': 'VRow', 'content': [{'component': 'VCol', 'props': {'cols': 12, 'md': 6}, 'content': [{'component': 'VSwitch', 'props': {'model': 'enabled', 'label': '启用'}}]}, {'component': 'VCol', 'props': {'cols': 12, 'md': 6}, 'content': [{'component': 'VSwitch', 'props': {'model': 'notify', 'label': '通知'}}]}]}, {'component': 'VRow', 'content': [{'component': 'VCol', 'props': {'cols': 12}, 'content': [{'component': 'VTextField', 'props': {'model': 'monitor_path', 'label': '监控路径'}}]}, {'component': 'VCol', 'props': {'cols': 12}, 'content': [{'component': 'VTextField', 'props': {'model': 'target_path', 'label': '目标路径'}}]}]}, {'component': 'VRow', 'content': [{'component': 'VCol', 'props': {'cols': 12, 'md': 6}, 'content': [{'component': 'VCronField', 'props': {'model': 'cron', 'label': '周期'}}]}, {'component': 'VCol', 'props': {'cols': 12, 'md': 6}, 'content': [{'component': 'VTextField', 'props': {'model': 'threshold', 'label': '阈值(MB)'}}]}]}]}], {"enabled": False, "notify": True, "threshold": 500, "cron": "*/30 * * * *"}

    def stop_service(self):
        pass
