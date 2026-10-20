#!/usr/bin/env python3
"""把 plugins.v3 的插件源码同步到 plugins.v2，并校验两代索引/源码一致。

MoviePilot 插件市场按代际读取 `package.{v}.json` 与 `plugins.{v}/<pid.lower()>/`，
两代内容若有漂移，旧宿主安装到的就是过期代码（v2.7.4 曾因此留下别名行导致
`NameError: name 'movie115_organizer' is not defined`）。改动插件后运行本脚本。

用法：
    python scripts/sync_plugins.py          # 同步
    python scripts/sync_plugins.py --check  # 只校验，不一致时退出码 1
"""

from __future__ import annotations

import filecmp
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GENERATIONS = ("v2", "v3")


def plugin_ids() -> list[str]:
    index = ROOT / "package.v3.json"
    return sorted(json.loads(index.read_text(encoding="utf-8")))


def sync(check_only: bool = False) -> int:
    problems: list[str] = []
    for pid in plugin_ids():
        src = ROOT / "plugins.v3" / pid.lower()
        dst = ROOT / "plugins.v2" / pid.lower()
        if not src.is_dir():
            problems.append(f"缺少源码目录：{src.relative_to(ROOT)}")
            continue
        if check_only:
            for f in sorted(src.iterdir()):
                target = dst / f.name
                if not target.exists():
                    problems.append(f"v2 缺少 {target.relative_to(ROOT)}")
                elif not filecmp.cmp(f, target, shallow=False):
                    problems.append(f"内容不一致：{target.relative_to(ROOT)}")
            continue
        dst.mkdir(parents=True, exist_ok=True)
        for f in src.iterdir():
            if f.is_file():
                shutil.copy2(f, dst / f.name)

        # 索引版本号必须与插件类声明一致
        version = json.loads((ROOT / "package.v3.json").read_text(encoding="utf-8"))[pid]["version"]
        init = (src / "__init__.py").read_text(encoding="utf-8")
        if f'plugin_version = "{version}"' not in init:
            problems.append(f"{pid}: package 版本 {version} 与 __init__.py 的 plugin_version 不一致")
        for gen in GENERATIONS:
            pkg = ROOT / f"package.{gen}.json"
            if not pkg.exists():
                problems.append(f"缺少索引：{pkg.name}")
                continue
            if json.loads(pkg.read_text(encoding="utf-8")).get(pid, {}).get("version") != version:
                problems.append(f"{pkg.name}: {pid} 版本号未同步为 {version}")

    if problems:
        print("\n".join(f"✗ {p}" for p in problems), file=sys.stderr)
        return 1
    print("✓ 两代插件源码与索引一致")
    return 0


if __name__ == "__main__":
    raise SystemExit(sync(check_only="--check" in sys.argv[1:]))