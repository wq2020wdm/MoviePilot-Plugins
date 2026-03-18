import time
from typing import List, Dict, Tuple, Any
from app.plugins import _PluginBase
from app.log import logger
from app.schemas import NotificationType

def get_p115_helper():
    """动态获取 115 助手类，适配不同版本的 MP 路径"""
    try:
        from app.helper.p115 import P115Helper
        return P115Helper
    except ImportError:
        try:
            from app.modules.p115 import P115Helper
            return P115Helper
        except ImportError:
            return None

class Movie115Organizer(_PluginBase):
    # --- V2 插件元数据 ---
    plugin_id = "movie115_organizer"
    plugin_name = "115 目录洗白整理"
    plugin_desc = "监控115路径，自动清理小文件、按@符号重命名并移动归档。"
    plugin_icon = ""
    plugin_version = "1.2.4"
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
            except (ValueError, TypeError):
                self._threshold = 500
            self._notify = config.get("notify")

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

    def execute(self):
        if not self._enabled:
            return

        HelperClass = get_p115_helper()
        if not HelperClass:
            logger.error("【115整理】核心模块加载失败：未找到 P115Helper")
            return

        p115 = HelperClass()
        m_id = self.get_id_by_path(p115, self._monitor_path)
        t_id = self.get_id_by_path(p115, self._target_path)

        if not m_id or not t_id:
            logger.error("【115整理】配置错误：无法识别监控或目标路径")
            return

        try:
            items = p115.get_file_list(m_id)
            for item in items:
                if not item.get('is_dir'): continue
                
                fid, fname = item.get('id'), item.get('name')
                
                # 1. 清理小文件
                sub_files = p115.get_file_list(fid)
                for sf in sub_files:
                    if not sf.get('is_dir'):
                        size_mb = sf.get('size', 0) / (1024 * 1024)
                        if size_mb < self._threshold:
                            p115.delete_file(sf.get('id'))
                            logger.info("【115整理】删除广告小文件: %s" % sf.get('name'))

                # 2. 洗白重命名
                new_name = fname.split('@')[-1] if '@' in fname else fname
                if new_name != fname:
                    p115.rename_file(fid, new_name)
                    logger.info("【115整理】重命名成功: %s -> %s" % (fname, new_name))

                # 3. 移动归档
                p115.move_file(fid, t_id)
                logger.info("【115整理】归档成功: %s" % new_name)

                if self._notify:
                    self.post_message(
                        mtype=NotificationType.SiteMessage,
                        title="115 整理完成",
                        text="已处理文件夹: %s" % new_name
                    )
        except Exception as e:
            logger.error("【115整理】运行报错: %s" % str(e))

    def get_service(self) -> List[Dict[str, Any]]:
        if self._enabled and self._cron:
            from apscheduler.triggers.cron import CronTrigger
            return [{
                "id": "Movie115Organizer",
                "name": "115 整理服务",
                "trigger": CronTrigger.from_crontab(self._cron),
                "func": self.execute
            }]
        return []

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        return [
            {
                'component': 'VForm',
                'content': [
                    {
                        'component': 'VRow',
                        'content': [
                            {'component': 'VCol', 'props': {'cols': 12, 'md': 6}, 'content': [{'component': 'VSwitch', 'props': {'model': 'enabled', 'label': '启用插件'}}]},
                            {'component': 'VCol', 'props': {'cols': 12, 'md': 6}, 'content': [{'component': 'VSwitch', 'props': {'model': 'notify', 'label': '发送通知'}}]}
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {'component': 'VCol', 'props': {'cols': 12}, 'content': [{'component': 'VTextField', 'props': {'model': 'monitor_path', 'label': '监控路径 (如 /Temp)'}}]},
                            {'component': 'VCol', 'props': {'cols': 12}, 'content': [{'component': 'VTextField', 'props': {'model': 'target_path', 'label': '目标路径 (如 /Media)'}}]}
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {'component': 'VCol', 'props': {'cols': 12, 'md': 6}, 'content': [{'component': 'VCronField', 'props': {'model': 'cron', 'label': '执行周期'}}]},
                            {'component': 'VCol', 'props': {'cols': 12, 'md': 6}, 'content': [{'component': 'VTextField', 'props': {'model': 'threshold', 'label': '清理阈值 (MB)'}}]}
                        ]
                    }
                ]
            }
        ], {"enabled": False, "notify": True, "threshold": 500, "cron": "*/30 * * * *"}

    def get_command(self) -> List[Dict[str, Any]]:
        return [{"command": "run_115_clean", "data": "run_115_clean", "description": "立即整理 115", "handler": self.execute}]

    def stop_service(self):
        pass
