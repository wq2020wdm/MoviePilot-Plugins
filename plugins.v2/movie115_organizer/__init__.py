from typing import List, Dict, Tuple, Any
from app.plugins import _PluginBase
from app.log import logger
from app.schemas import NotificationType

def get_p115_helper():
    """适配不同版本的 MP 路径"""
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
    plugin_icon = "" 
    plugin_version = "1.2.6"
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

    # --- V2 必须实现的抽象方法 (修复报错的关键) ---
    
    def get_state(self) -> bool:
        """返回插件运行状态"""
        return self._enabled

    def get_page(self) -> List[dict]:
        """返回自定义页面配置，V2 必须实现，暂不使用则返回空列表"""
        return []

    def get_api(self) -> List[dict]:
        """返回插件自定义 API，V2 必须实现，暂不使用则返回空列表"""
        return []

    # --- 核心逻辑 ---

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
        if not self._enabled: return
        HelperClass = get_p115_helper()
        if not HelperClass:
            logger.error("【115整理】核心模块加载失败")
            return
        p115 = HelperClass()
        m_id = self.get_id_by_path(p115, self._monitor_path)
        t_id = self.get_id_by_path(p115, self._target_path)
        if not m_id or not t_id:
            logger.error("【115整理】路径配置有误")
            return
        try:
            items = p115.get_file_list(m_id)
            for item in items:
                if not item.get('is_dir'): continue
                fid, fname = item.get('id'), item.get('name')
                sub_files = p115.get_file_list(fid)
                for sf in sub_files:
                    if not sf.get('is_dir'):
                        size_mb = sf.get('size', 0) / 1048576
                        if size_mb < self._threshold:
                            p115.delete_file(sf.get('id'))
                new_name = fname.split('@')[-1] if '@' in fname else fname
                if new_name != fname:
                    p115.rename_file(fid, new_name)
                p115.move_file(fid, t_id)
                logger.info("【115整理】归档成功: %s" % new_name)
                if self._notify:
                    self.post_message(NotificationType.SiteMessage, "115 整理完成", "已处理: %s" % new_name)
        except Exception as e:
            logger.error("【115整理】报错: %s" % str(e))

    def get_service(self) -> List[Dict[str, Any]]:
        if self._enabled and self._cron:
            from apscheduler.triggers.cron import CronTrigger
            return [{"id": "movie115_organizer", "name": "115整理服务", "trigger": CronTrigger.from_crontab(self._cron), "func": self.execute}]
        return []

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        return [{'component': 'VForm', 'content': [{'component': 'VRow', 'content': [{'component': 'VCol', 'props': {'cols': 12, 'md': 6}, 'content': [{'component': 'VSwitch', 'props': {'model': 'enabled', 'label': '启用'}}]}, {'component': 'VCol', 'props': {'cols': 12, 'md': 6}, 'content': [{'component': 'VSwitch', 'props': {'model': 'notify', 'label': '通知'}}]}]}, {'component': 'VRow', 'content': [{'component': 'VCol', 'props': {'cols': 12}, 'content': [{'component': 'VTextField', 'props': {'model': 'monitor_path', 'label': '监控路径'}}]}, {'component': 'VCol', 'props': {'cols': 12}, 'content': [{'component': 'VTextField', 'props': {'model': 'target_path', 'label': '目标路径'}}]}]}, {'component': 'VRow', 'content': [{'component': 'VCol', 'props': {'cols': 12, 'md': 6}, 'content': [{'component': 'VCronField', 'props': {'model': 'cron', 'label': '周期'}}]}, {'component': 'VCol', 'props': {'cols': 12, 'md': 6}, 'content': [{'component': 'VTextField', 'props': {'model': 'threshold', 'label': '阈值(MB)'}}]}]}]}], {"enabled": False, "notify": True, "threshold": 500, "cron": "*/30 * * * *"}

    def get_command(self) -> List[Dict[str, Any]]:
        return [{"command": "run_115", "data": "run_115", "description": "手动执行", "handler": self.execute}]

    def stop_service(self):
        pass
